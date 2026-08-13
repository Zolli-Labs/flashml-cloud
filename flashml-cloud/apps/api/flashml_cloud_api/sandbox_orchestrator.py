"""One FC Sandbox evaluation session, driven end to end.

Everything below this module already exists and is tested: the gateway that
reports what it saw (``alibaba_sandbox``), the ledger that records only what
was observed (``sandbox_sessions``), the one-session credential
(``sandbox_identity``), the sequence that turns a bare Debian box into an
enrolled FlashNode (``sandbox_bootstrap``), and the presigned-URL mint
(``alibaba_oss`` / ``artifact_mirror``). This is the thing that decides *when*
each of them runs, and it is the last module before the loop runs unattended.

**A reducer over observed facts.** The six inputs it accepts are, in order:
a sandbox observed active; a worker observed prepared; a sandbox observed
hibernated; a training artifact observed present in OSS; a sandbox observed
active again after a reconnect; an evaluation result accepted; a sandbox
observed terminated. Every one of those is a *sighting* — a
:class:`~flashml_cloud_api.alibaba_sandbox.SandboxObservation`, an
``OSSArtifacts.head`` that returned metadata, a
:class:`~flashml_cloud_api.sandbox_bootstrap.WorkerHealth`. There is no input
spelled "we asked for a pause", and that asymmetry is the whole design. The
single most expensive mistake available here is to record HIBERNATED because
``pause()`` returned; the second is to record TERMINATED because ``kill()``
did not raise.

**What was measured, and what this module is therefore not allowed to
assume** (all 2026-08-11, ``ap-southeast-1``, n=5 unless noted):

1. *Region is an allowlist of one.* ``pause()`` answers ``403
   PauseSessionForbidden`` anywhere else, forever. :func:`start_session`
   refuses before it creates anything, because a sandbox created for a
   session that can never hibernate is a sandbox billing for nothing.
2. *The worker survives hibernation* — 5/5, and a running ``flashnode work``
   carried its lease claims straight across a pause, 13 → 19. So the wake
   path calls :func:`~flashml_cloud_api.sandbox_bootstrap.verify_worker` and
   never :func:`~flashml_cloud_api.sandbox_bootstrap.bootstrap_worker`. A
   re-bootstrap would install a second agent under one node id and tell the
   coordinator to forget the leases the surviving one is already serving.
3. *``connect()`` must always carry an explicit timeout.* A bare connect
   re-arms the sandbox to exactly 300 s — confirmed on the wire, ``end_at``
   set to ``now + 300.0``. The gateway does that for us
   (``E2BSandboxGateway.connect`` re-sends ``default_timeout_ms``); this
   module must therefore never reach past the gateway to the SDK.
4. *Pausing is slow* — p50 2635 ms, p95 3075 ms, against a wake of p50 1085
   / p95 1598 ms. It is never on a latency-sensitive step: the only pause is
   at the end of :func:`start_session`, after everything a user is waiting
   for has already happened.
5. *``run()`` and ``write_file()`` implicitly WAKE a paused sandbox* (~1 s,
   back to billed time), because they need a connection. So nothing in
   :func:`reconcile` touches a hibernated sandbox with anything except
   ``inspect()``, which does not. This is the foot-gun that turns a
   bookkeeping sweep into a bill.
6. *There are two TTL clocks and only one has been measured.* The E2B
   sandbox TTL appeared to stop while paused (n=1, 150 s); Alibaba doc
   3028695 says the FC Session TTL keeps accruing from creation.
   **Nothing here requires the clock to stop.** :func:`start_session`
   asserts the remaining budget exceeds the whole expected wait *before* it
   pauses, and expiry is a real terminal path in :func:`reconcile`, not an
   edge case.
7. *``unfinished_sessions`` deliberately returns SUCCEEDED and FAILED rows.*
   A session whose evaluation succeeded still owns a live, billing sandbox
   until something kills it. Only TERMINATED is terminal for cleanup, and
   :func:`reconcile` is what makes that true rather than aspirational.
8. *A presigned URL minted before a hibernation is expired on wake.* So the
   URLs are minted on the wake path, as the first action after the health
   check, and delivered with ``write_file`` — never interpolated into a
   command, which would put a bearer credential into
   ``CommandEvidence.command`` and from there into the event ledger.
9. *The woken worker is already claiming.* It survived the pause and it asks
   for work every two seconds, so the evaluation task is submitted **last** —
   after the artifacts are on disk and confirmed there. An earlier draft
   submitted first so the wake latency would read as time-to-productive; that
   is a presentation preference, and it opened a window in which the worker
   could claim a task and find no ``artifacts.json``. Submitting last closes
   it and yields two honest numbers where there was one blended one: the wake
   (``sandbox.woken``) and the controller's own time to make the sandbox
   productive (``ready_ms`` on ``evaluation.submitted``).

**Four rules that are not negotiable, and where each one lives.**

*Never infer state from a call you made.* Every mutating call goes through a
helper that, on a :class:`SandboxTransportError` with ``may_have_applied``
set, calls ``inspect()`` and records what is actually there —
:func:`_settle_after`. A timeout abandons the await, not the thread: the SDK
call is still running in the pool after we have given up on it, so "it
failed" is not a fact about the sandbox.

*Every transition is compare-and-set, and losing is normal.*
``sandbox_sessions.transition`` returns False when another controller got
there first. Every call site here treats that as "somebody else is driving,
stop" and returns quietly. Two controllers must never both wake one sandbox;
after a restart during a long hibernation, two controllers is the ordinary
shape of this system.

*Restartable from the persisted row plus ``external_sandbox_id``.* Assume the
process dies at every step. What makes each step resumable is written on the
step: the node id and the marker nonce are **derived from the session id**
rather than randomly generated, so a repeated bootstrap reproduces the same
identity and the same marker hash; the evaluation job id is appended to the
ledger the instant the coordinator hands it back, so a resumed
:func:`on_model_ready` finds it instead of submitting a second one; and
:func:`reconcile` closes the cases where nothing local survived at all.

*Bounded everywhere.* Two attempts at a pause, two at a wake, two at a kill,
and a deadline on the evaluation result. There is no unbounded wake loop and
no retry of ``create`` — a retried create that actually succeeded is a
sandbox nobody has the id of.

**What this module does not do.** It adds no routes and holds no HTTP client.
Submitting the evaluation task and reading its verdict are the coordinator's
business, and the coordinator client lives in ``app.py`` behind the operator
token; so an :class:`EvaluationDriver` is injected, exactly as
``artifact_mirror`` takes an ``ArtifactSource`` rather than importing ``app``
and closing a cycle.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shlex
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol, runtime_checkable

import psycopg

from . import db as dbmod
from . import sandbox_identity, sandbox_sessions as ss
from .alibaba_oss import DEFAULT_TTL_S, OSSArtifacts, OSSUnavailable
from .alibaba_sandbox import (
    SandboxAllowlistError,
    SandboxError,
    SandboxGateway,
    SandboxObservation,
    SandboxState,
    SandboxTerminalError,
    SandboxTransportError,
)
from .artifact_mirror import manifest_key, presign_job_artifacts
from .sandbox_bootstrap import (
    DEFAULT_PATHS,
    BootstrapResult,
    SandboxPaths,
    WorkerHealth,
    bootstrap_worker,
    verify_worker,
)
from .observability import Chain, chain_of, log_event, new_correlation_id
from .sandbox_sessions import Observation
from .settings import Settings

log = logging.getLogger(__name__)

__all__ = [
    "ARTIFACTS_MODE",
    "ARTIFACT_MANIFEST_SCHEMA",
    "DEFAULT_EVALUATION_TIMEOUT_S",
    "DEFAULT_EXPECTED_WAIT_S",
    "EVALUATION_SUBMITTED",
    "EvaluationDriver",
    "EvaluationOutcome",
    "EvaluationRequest",
    "EvaluationUnavailable",
    "OrchestratorError",
    "RESUME_RESERVE_S",
    "SandboxUnconfigured",
    "SessionFailed",
    "SessionNotFound",
    "TrainingJobNotAuthorised",
    "artifacts_document",
    "cleanup_session",
    "evaluation_artifacts_path",
    "marker_nonce_for",
    "node_id_for",
    "on_model_ready",
    "reconcile",
    "start_session",
]


# ---------------------------------------------------------------------------
# Policy. Constants and pure functions: no database, no gateway, no clock.
# ---------------------------------------------------------------------------

#: How long the caller expects to wait between hibernating and the model
#: appearing in OSS. Used for one thing only: the assertion, made **before**
#: the pause, that the sandbox's own budget outlasts the wait. Thirty minutes
#: is a training run, not a guess about hibernation — see
#: :data:`RESUME_RESERVE_S` for the other half.
DEFAULT_EXPECTED_WAIT_S = 1800.0

#: Budget held back for everything that happens *after* the wake: the wake
#: itself (p95 1598 ms), the health probe, the presign round trip, and the
#: evaluation. Five minutes, which is generous for the first three and the
#: floor for the fourth. The pause is not in here because the assertion
#: happens before it — a sandbox with less than ``expected_wait +
#: RESUME_RESERVE_S`` left is one that would expire mid-hibernation and take
#: the prepared environment with it, and there is no point paying 2.6 s to
#: find that out.
RESUME_RESERVE_S = 300.0

#: The ceiling on awaiting an accepted evaluation result. Bounded because the
#: alternative is a background task that holds a sandbox open for ever on a
#: coordinator that has quietly stopped answering.
DEFAULT_EVALUATION_TIMEOUT_S = 900.0

#: Between polls of the evaluation job.
EVALUATION_POLL_INTERVAL_S = 2.0

#: Attempts at each mutating lifecycle call. Two, not one, because a
#: transport failure is genuinely retryable once the ``inspect()`` in
#: :func:`_settle_after` has established that the first attempt did not
#: apply; and two, not more, because every one of these is a call whose
#: repeated failure means something structural.
#:
#: ``create`` is deliberately absent: a retried create that actually
#: succeeded is a second sandbox whose id nothing holds, billing until its
#: TTL runs out. One attempt, and a transport failure is reconciled by id or
#: reported loudly.
PAUSE_ATTEMPTS = 2
WAKE_ATTEMPTS = 2
KILL_ATTEMPTS = 2

#: A session in a transient state whose row has not moved in this long is
#: abandoned — the controller that owned it is gone. Sized past the default
#: sandbox lifetime (``fc_sandbox_timeout_ms``, 1 h) so a sweep never
#: interrupts a session that is merely slow; by the time this fires the
#: sandbox has usually expired on its own and the only thing left to do is
#: say so and stop the ledger claiming work is in progress.
DEFAULT_STALE_AFTER_S = 3600.0

#: States a session can sit in while a controller is actively driving it.
#: Everything here is subject to :data:`DEFAULT_STALE_AFTER_S`.
_TRANSIENT_STATES = frozenset({"REQUESTED", "ACTIVE", "PREPARED", "RESUMING",
                               "EVALUATING"})

#: Where the woken worker reads its presigned URLs. Under the control
#: directory ``sandbox_bootstrap`` already creates, and deliberately not under
#: the agent's own state directory — ``flashnode env purge`` promises nothing
#: about files it did not write.
ARTIFACTS_NAME = "artifacts.json"

#: The mode ``artifacts.json`` is written at, and the mode
#: :func:`_assert_artifacts_present` confirms it came back as. Restrictive
#: because every value in that file is a bearer credential — the same
#: reasoning ``sandbox_bootstrap`` applies to the machine token, and the same
#: reason the check is a real round trip rather than an assumption:
#: ``write_file`` sets the mode with a *second* call that can fail on its own.
ARTIFACTS_MODE = 0o600

#: Versioned from the first byte, for ``artifact_mirror.MANIFEST_SCHEMA``'s
#: reason: a consumer reading a document it does not understand must be able
#: to say so rather than guess.
ARTIFACT_MANIFEST_SCHEMA = "flashml.sandbox-artifacts/v1"

#: The ledger event that carries the evaluation job id. Read back by a
#: resumed :func:`on_model_ready` to answer "did the controller that died
#: already submit this?", which is the one question a bare state column
#: cannot answer.
EVALUATION_SUBMITTED = "evaluation.submitted"


def node_id_for(session_id: str) -> str:
    """The node id this session's worker registers under.

    **Derived, not random.** A restarted controller that has lost the
    credential it minted needs to be able to recognise its own machine row,
    and ``machines.node_id`` is globally unique precisely so one machine
    cannot adopt another's identity — so a second provision under the same id
    raises ``NodeAlreadyEnrolled`` instead of quietly minting a second
    claimant for one session. Matches ``app.NODE_ID_RE``
    (``[A-Za-z0-9][A-Za-z0-9._:-]{0,127}``), because this value later becomes
    an HTTP header on the delegation path.
    """
    return f"fc-{session_id}"


def marker_nonce_for(session_id: str) -> str:
    """The nonce that makes this session's marker document unique.

    Derived from the session id for the same reason as :func:`node_id_for`:
    a bootstrap that is retried after a crash must produce *the same bytes*,
    so the marker hash a wake compares against is stable no matter how many
    times the preparation was attempted. A random nonce would make a resumed
    bootstrap look, to :func:`~flashml_cloud_api.sandbox_bootstrap.verify_worker`,
    exactly like a sandbox that had been swapped underneath us.

    Not a secret and not a capability — it is a uniqueness token whose only
    job is to keep two sessions' markers from colliding.
    """
    return hashlib.sha256(f"flashml-marker:{session_id}".encode()).hexdigest()[:32]


def evaluation_artifacts_path(paths: SandboxPaths = DEFAULT_PATHS) -> str:
    return f"{paths.control_dir.rstrip('/')}/{ARTIFACTS_NAME}"


def artifacts_document(
    *,
    session_id: str,
    training_job_id: str,
    urls: Mapping[str, str],
    ttl_s: int,
    issued_at: datetime | None = None,
) -> bytes:
    """The bytes handed to the sandbox so it can read the trained model.

    Every value in ``objects`` is a bearer credential with its signature in
    the query string. That is why this function returns *bytes for a file*
    and there is no variant that returns a command line: ``write_file`` puts
    them in the sandbox and nowhere else, while a ``run()`` would put them in
    ``CommandEvidence.command`` and from there into the event ledger, which
    the whole redaction layer of ``sandbox_sessions`` exists to keep clean.

    Sorted keys and a stable separator so identical inputs serialise
    identically — a re-mint that produced different bytes for the same set of
    objects would make a file comparison meaningless.
    """
    stamp = (issued_at or _now()).astimezone(timezone.utc)
    payload = {
        "schema": ARTIFACT_MANIFEST_SCHEMA,
        "session_id": session_id,
        "job_id": training_job_id,
        "issued_at": stamp.isoformat().replace("+00:00", "Z"),
        "ttl_s": int(ttl_s),
        "objects": dict(sorted(urls.items())),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class OrchestratorError(Exception):
    """Base class. Nothing raised from this module carries a credential —
    every message is built here from ids and states, and the two modules that
    could supply one (``alibaba_sandbox``, ``sandbox_bootstrap``) have already
    redacted theirs."""


class SessionNotFound(OrchestratorError, LookupError):
    """No such session. Raised rather than returned so a caller cannot carry
    on with a ``None`` row and act on a session that is not there."""


class TrainingJobNotAuthorised(OrchestratorError):
    """The training job does not exist, or belongs to somebody else.

    One exception for both, deliberately — the repo's 404-not-403 doctrine
    (``fetch_job_for_owner``, ``fetch_pool_for_member``). Splitting them would
    let a caller with a guessed id learn that it names a real job.
    """


class SandboxUnconfigured(OrchestratorError):
    """This deployment cannot run a sandbox session at all.

    Missing FC configuration, no isolation pool, or a region where
    ``pause()`` is not enabled. Raised **before** anything is created: a
    sandbox that can never hibernate is a sandbox billing for nothing, and
    finding that out after the create costs a real 2.6 s round trip to learn
    something ``settings`` already knew.
    """


class EvaluationUnavailable(OrchestratorError):
    """:func:`on_model_ready` was called with no :class:`EvaluationDriver`.

    A caller bug rather than a runtime condition: without a driver there is
    nothing to give the woken worker, and waking a sandbox to hand it no work
    is the one outcome the wake ordering exists to prevent.
    """


class SessionFailed(OrchestratorError):
    """The session ended FAILED. The ledger already says why; this carries
    the same words to the caller.

    ``session_id`` is on the exception because the caller of
    :func:`start_session` has no other way to reach a session that failed
    before the id was returned — and that session's row, events and share
    link all exist and are the point.
    """

    def __init__(self, session_id: str, code: str, detail: str = ""):
        super().__init__(f"sandbox session {session_id} failed: {code}"
                         + (f" ({detail})" if detail else ""))
        self.session_id = session_id
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# The evaluation, injected
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationRequest:
    """Everything a submitter needs to place one evaluation task.

    ``pool_id`` is the isolation pool and it is not advisory: the
    coordinator's seventh placement gate refuses a pool-scoped task to any
    node whose stamp does not list that pool, and this session's pool contains
    exactly one machine. A submission that dropped it would place the
    evaluation on any volunteer laptop that happened to be online.

    ``session_id`` is carried so the submitter can make submission idempotent
    on it — see :meth:`EvaluationDriver.submit`.
    """

    session_id: str
    owner_id: str
    pool_id: str
    training_job_id: str
    node_id: str
    spec: Mapping[str, Any]


@dataclass(frozen=True)
class EvaluationOutcome:
    """A **settled** evaluation. Never "still running" — that is ``None``.

    ``accepted`` distinguishes attempted work from accepted work, which repo
    hard rule 4 requires everywhere a result is believed. A job that ran to
    completion and produced a refusal is ``accepted=False`` and moves the
    session to FAILED; it is not an exception, because "the evaluation says
    no" is a result somebody asked for.
    """

    evaluation_job_id: str
    accepted: bool
    detail: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class EvaluationDriver(Protocol):
    """Submitting the evaluation and reading its verdict.

    A protocol rather than a direct dependency on ``app.CoordinatorClient``,
    for ``artifact_mirror.ArtifactSource``'s two reasons: ``app`` imports this
    module, so importing it back would close a cycle; and every ordering rule
    below is then testable with a dictionary instead of an HTTP server.
    """

    async def submit(self, request: EvaluationRequest) -> str:
        """Place the task and return the coordinator's job id.

        **Must be idempotent on ``request.session_id``.** The coordinator
        mints the job id, so this module cannot choose one in advance; it
        records the id the instant it is handed back, and a resumed
        :func:`on_model_ready` reads it out of the ledger rather than
        submitting again. The irreducible window is between the coordinator
        accepting the job and that append landing, and an implementation that
        returns the existing id for a session it has already seen is what
        closes it.
        """
        ...

    async def poll(self, evaluation_job_id: str) -> EvaluationOutcome | None:
        """The settled outcome, or ``None`` while the job is still running.

        ``None`` means *not yet*, never *failed*: a job that ended badly is an
        :class:`EvaluationOutcome` with ``accepted=False``. Collapsing those
        two would have the orchestrator wait out its whole deadline on a job
        that finished in the first second.
        """
        ...


# ---------------------------------------------------------------------------
# Ledger plumbing
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """A tz-aware UTC datetime, treating a naive one as already UTC.

    The SDK hands back ``end_at`` and Postgres hands back ``updated_at``, and
    only one of them is reliably aware. ``astimezone`` on a naive value
    assumes *local* time, which on a Render box in UTC is invisible and on a
    laptop in Singapore is an eight-hour error in a budget assertion.
    """
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None \
        else value.astimezone(timezone.utc)


def _jsonable(value: Any) -> Any:
    """``value`` in a shape ``psycopg``'s ``Json`` can serialise.

    Datetimes are the whole reason this exists: ``SandboxObservation`` carries
    ``observed_at`` and ``expires_at`` as ``datetime``, ``json.dumps`` refuses
    one, and the resulting ``TypeError`` would land on a ledger write during a
    cleanup path — the worst possible place for one.
    """
    if isinstance(value, datetime):
        return _as_utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, SandboxState):
        return str(value)
    return value


class _Ledger:
    """One session's event stream, written best effort.

    Recording is best effort and the sandbox is not — the argument
    ``sandbox_bootstrap._Recorder`` makes. A ledger write that fails during
    cleanup must not stop the kill, and it must not replace the exception the
    caller was already handling with a psycopg error about a different
    subject. So every append here is wrapped, logged and dropped.

    ``note`` is synchronous because it is what ``bootstrap_worker``'s
    ``on_observation`` sink needs — that hook is called from inside an async
    function and cannot await. The insert is small and the alternative
    (buffering until the end) loses exactly the observations a failed
    bootstrap most needs to have left behind.
    """

    def __init__(self, db: psycopg.Connection, session_id: str) -> None:
        self._db = db
        self.session_id = str(session_id)

    def note(
        self,
        type_: str,
        *,
        source: str = "controller",
        latency_ms: float | None = None,
        **data: Any,
    ) -> None:
        self.append(
            Observation(
                type=type_,
                source=source,
                latency_ms=None if latency_ms is None else max(0.0, float(latency_ms)),
                data={k: _jsonable(v) for k, v in data.items() if v is not None},
            )
        )

    def append(self, observation: Observation) -> None:
        try:
            ss.append_event(self._db, self.session_id, observation)
        except Exception:  # noqa: BLE001 - a ledger hiccup is not a lifecycle failure
            log.warning(
                "sandbox orchestrator: could not record %s on session %s",
                observation.type, self.session_id, exc_info=True,
            )

    async def record(
        self,
        type_: str,
        *,
        source: str = "controller",
        latency_ms: float | None = None,
        **data: Any,
    ) -> None:
        await asyncio.to_thread(
            self.note, type_, source=source, latency_ms=latency_ms, **data
        )

    def sink(self) -> Callable[[Observation], None]:
        return self.append

    def observed(self, type_: str, observation: SandboxObservation) -> None:
        """Record one gateway sighting, attributed to the provider.

        ``source="fc"`` rather than ``"controller"``: a latency the provider
        reports and one we timed around the provider are different claims, and
        the evidence view has to be able to say which it is showing.
        """
        self.note(
            type_,
            source="fc",
            latency_ms=float(observation.latency_ms),
            sandbox_state=str(observation.state),
            template=observation.template,
            expires_at=observation.expires_at,
        )


async def _transition(
    db: psycopg.Connection,
    session_id: str,
    from_state: str,
    to_state: str,
    **kwargs: Any,
) -> bool:
    """``sandbox_sessions.transition`` off the event loop.

    Returns False when another controller got there first. **That is not an
    error** and no call site here treats it as one — it means somebody else is
    driving this session, and the correct response is to stop rather than to
    race them for the sandbox.
    """
    return await asyncio.to_thread(
        ss.transition, db, session_id, from_state, to_state, **kwargs
    )


async def _fetch(db: psycopg.Connection, session_id: str) -> dict[str, Any]:
    row = await asyncio.to_thread(ss.fetch_session, db, session_id)
    if row is None:
        raise SessionNotFound(f"no sandbox session {session_id}")
    return row


# ---------------------------------------------------------------------------
# Gateway plumbing: nothing below infers a state from a call it made.
# ---------------------------------------------------------------------------


async def _settle_after(
    gateway: SandboxGateway,
    sandbox_id: str,
    exc: SandboxError,
    *,
    ledger: _Ledger,
    operation: str,
) -> SandboxObservation | None:
    """After an ambiguous failure, go and look.

    ``SandboxTransportError.may_have_applied`` is the field that matters:
    ``asyncio.wait_for`` abandons the await and not the thread, so the SDK
    call is still running in the pool after we have given up on it, and the
    pause may have happened, the sandbox may have been created. Retrying
    blindly is how a create that succeeded becomes two sandboxes.

    Returns what the API actually reports, or ``None`` when there is nothing
    to ask about (no id yet) or when the reconciling ``inspect`` itself
    failed. ``None`` is honest; a guess would not be.
    """
    if not isinstance(exc, SandboxTransportError) or not exc.may_have_applied:
        return None
    target = sandbox_id or getattr(exc, "sandbox_id", "") or ""
    if not target:
        # A create whose response never arrived, with no id anywhere. There
        # is no `list` on the gateway Protocol, so there is nothing to ask.
        # Say so loudly: this is the one leak this module cannot close.
        #
        # TODO(alibaba_sandbox): `SandboxGateway.list()` is being added by the
        # agent that owns that module. Once it exists, this branch searches by
        # the `flashml_session_id` metadata `_prepare_and_hibernate` already
        # stamps on every create, and the leak closes. Do not add the method
        # from here — one owner per module.
        await ledger.record(
            f"{operation}.unreconcilable",
            detail="the call may have applied and no sandbox id was returned",
        )
        log.error(
            "FC sandbox: %s may have applied with no sandbox id on session %s",
            operation, ledger.session_id,
        )
        return None
    try:
        observed = await gateway.inspect(target)
    except SandboxError as inspect_exc:
        await ledger.record(
            f"{operation}.unreconciled",
            detail=f"{type(inspect_exc).__name__} while inspecting after {operation}",
        )
        return None
    await asyncio.to_thread(ledger.observed, f"{operation}.reconciled", observed)
    return observed


async def _pause_sandbox(
    gateway: SandboxGateway, sandbox_id: str, *, ledger: _Ledger
) -> SandboxObservation:
    """Hibernate, bounded, and report only what was seen.

    ``SandboxAllowlistError`` is re-raised immediately and never retried: it
    is an account-and-region fact, permanent until Alibaba changes it, and a
    retry loop around it burns budget on a call that cannot ever succeed.
    """
    last: SandboxObservation | None = None
    for attempt in range(1, PAUSE_ATTEMPTS + 1):
        try:
            observed = await gateway.pause(sandbox_id)
        except SandboxAllowlistError:
            raise
        except SandboxError as exc:
            settled = await _settle_after(
                gateway, sandbox_id, exc, ledger=ledger, operation="pause"
            )
            if settled is not None and settled.state is SandboxState.PAUSED:
                return settled
            last = settled
            # A terminal error will fail identically next time; retrying one
            # is how a configuration mistake becomes a rate-limit ban.
            if attempt == PAUSE_ATTEMPTS or not isinstance(
                exc, SandboxTransportError
            ):
                raise
            continue
        await asyncio.to_thread(ledger.observed, "sandbox.pause_observed", observed)
        if observed.state is SandboxState.PAUSED:
            return observed
        last = observed
        if attempt == PAUSE_ATTEMPTS:
            break
    raise SandboxTerminalError(
        "the sandbox did not report itself paused after "
        f"{PAUSE_ATTEMPTS} attempts (last state "
        f"{str(last.state) if last else 'unknown'})",
        operation="pause", sandbox_id=sandbox_id,
    )


async def _wake_sandbox(
    gateway: SandboxGateway, sandbox_id: str, *, ledger: _Ledger
) -> SandboxObservation:
    """Resume, bounded. There is no ``resume()`` — ``connect`` is the wake.

    The gateway re-sends the configured lifetime on every connect, which is
    not an optimisation: a bare connect sets ``end_at`` to ``now + 300``, so
    a one-hour sandbox silently becomes a five-minute one. Nothing here may
    reach past the gateway to the SDK for that reason alone.

    Bounded at :data:`WAKE_ATTEMPTS`. An unbounded wake loop against a
    sandbox that will not come back is a hot loop against somebody's rate
    limiter.
    """
    for attempt in range(1, WAKE_ATTEMPTS + 1):
        try:
            observed = await gateway.connect(sandbox_id)
        except SandboxError as exc:
            settled = await _settle_after(
                gateway, sandbox_id, exc, ledger=ledger, operation="connect"
            )
            if settled is not None and settled.state is SandboxState.RUNNING:
                return settled
            if attempt == WAKE_ATTEMPTS or not isinstance(
                exc, SandboxTransportError
            ):
                raise
            continue
        await asyncio.to_thread(ledger.observed, "sandbox.woken", observed)
        if observed.state is SandboxState.RUNNING:
            return observed
        if attempt == WAKE_ATTEMPTS:
            raise SandboxTerminalError(
                f"the sandbox reported {observed.state} after a connect that "
                "should have woken it",
                operation="connect", sandbox_id=sandbox_id,
            )
    raise AssertionError("unreachable")  # pragma: no cover


async def _kill_sandbox(
    gateway: SandboxGateway, sandbox_id: str, *, ledger: _Ledger
) -> bool:
    """Destroy the sandbox. True only when the API confirmed it is gone.

    ``kill`` returns TERMINATED both for a delete the API confirmed and for a
    404, and both are the API telling us the sandbox no longer exists — so
    both are a genuine observation and both return True. Anything else
    returns False and leaves the session for :func:`reconcile`, because
    recording TERMINATED on a sandbox that might still be running is exactly
    the inference this module refuses to make.
    """
    for attempt in range(1, KILL_ATTEMPTS + 1):
        try:
            observed = await gateway.kill(sandbox_id)
        except SandboxError as exc:
            settled = await _settle_after(
                gateway, sandbox_id, exc, ledger=ledger, operation="kill"
            )
            if settled is not None and settled.state is SandboxState.TERMINATED:
                return True
            if attempt == KILL_ATTEMPTS or not isinstance(
                exc, SandboxTransportError
            ):
                await ledger.record(
                    "sandbox.kill_failed",
                    detail=f"{type(exc).__name__} after {attempt} attempt(s)",
                )
                return False
            continue
        await asyncio.to_thread(ledger.observed, "sandbox.killed", observed)
        return observed.state is SandboxState.TERMINATED
    return False  # pragma: no cover


# ---------------------------------------------------------------------------
# start_session
# ---------------------------------------------------------------------------


BootstrapFn = Callable[..., Any]


async def start_session(
    db: psycopg.Connection,
    gateway: SandboxGateway,
    settings: Settings,
    *,
    owner_id: str,
    training_job_id: str,
    evaluation_spec: Mapping[str, Any],
    pool_id: str | None = None,
    correlation_id: str | None = None,
    coordinator_url: str | None = None,
    expected_wait_s: float = DEFAULT_EXPECTED_WAIT_S,
    resume_reserve_s: float = RESUME_RESERVE_S,
    paths: SandboxPaths = DEFAULT_PATHS,
    bootstrap: BootstrapFn = bootstrap_worker,
    **bootstrap_kwargs: Any,
) -> str:
    """Create, prepare and hibernate one evaluation sandbox. Returns its id.

    The sequence, and every step of it is a fact that was observed rather
    than a call that returned:

    1. **Authorise the training job for this owner.** ``fetch_job_for_owner``
       folds the owner into the query; a job that is not this person's is
       simply not there. Nothing is created before this, so an unauthorised
       call costs nothing and leaves nothing.
    2. **Refuse an impossible deployment early** — no FC configuration, no
       isolation pool, or a region where ``pause()`` is not enabled. A
       sandbox created for a session that can never hibernate bills for
       nothing.
    3. **Open the session row**, so that from here on every step has somewhere
       durable to be recorded and a restart has something to find.
    4. **The isolation pool must be empty**, then
       ``provision_sandbox_machine`` mints one credential into it and
       ``assert_pool_isolated`` re-checks the boundary that credential is only
       safe inside. Both directions: nothing else in the pool, nothing else
       for the machine.
    5. **Create the sandbox** with an explicit ``timeout_ms``, and record the
       id in the same compare-and-set that moves REQUESTED → ACTIVE. That
       ordering is the point of REQUESTED existing at all.
    6. **Bootstrap the worker**, streaming its observations into the ledger as
       they happen, and observe PREPARED with the marker hash a wake will be
       checked against.
    7. **Assert the remaining budget exceeds the expected wait**, before
       pausing and never after. Two clocks may be running and only one has
       been measured; nothing here requires either to stop.
    8. **Pause**, and observe HIBERNATED only once the API says PAUSED.

    Any failure from step 3 onwards records FAILED, runs
    :func:`cleanup_session` — which kills the sandbox and revokes the
    credential independently — and raises :class:`SessionFailed` carrying the
    session id, because the row, its events and its share link all exist and
    are the evidence of what went wrong.

    ``evaluation_spec`` is not submitted here — the evaluation is placed on
    the wake path, once the model exists and the sandbox can actually do it.
    It is stored on the session row (``sandbox_sessions.evaluation_spec``,
    migration 0017) rather than in an event, because every string in an event
    goes through a key-name matcher that would silently rewrite a spec field
    called ``api_key_name`` to ``[redacted]`` and would only be found out
    hours later, on the one path that runs after a hibernation. It is a job
    specification and must never carry a credential; the column comment says
    so where the next person will read it.

    ``correlation_id`` — **this is the one function in this module allowed to
    mint one**, and the rule it follows is worth stating precisely because the
    rule next door is the opposite one.

    Three cases, in order:

    1. **The caller supplied one.** Use it. A route that has already minted at
       the request is the outermost edge and this session joins its thread.
    2. **The training job carries one.** Inherit it. ``fetch_job_for_owner``
       below is already run for authorisation, so the row is in hand — and a
       session opened against a threaded job is the same piece of work, not a
       new one.
    3. **Neither.** Mint. D-1 names a sandbox session as one of the three
       things a user initiates, so when nothing upstream has a thread, this
       session *is* the outermost thing and minting here relates nothing that
       was not already related.

    What is NOT done, in any of the three: the id is never written back onto
    the training job. A thread that flowed backwards would put every later
    attempt on that job onto an id minted by an unrelated evaluation.

    ``ss.create_session`` mints nothing at all (D-2). The asymmetry is the
    design: the edge decides, the writer records.
    """
    pool_id = str(pool_id or settings.fc_sandbox_pool_id or "")
    coordinator_url = coordinator_url or settings.coordinator_url

    if not settings.fc_sandbox_configured:
        raise SandboxUnconfigured(
            "the FC Agent Sandbox is not configured on this deployment"
        )
    if not pool_id:
        raise SandboxUnconfigured(
            "no isolation pool is configured for the FC sandbox; a session "
            "without one would put the evaluation task on any online node"
        )
    if not settings.fc_sandbox_hibernation_region:
        raise SandboxUnconfigured(
            f"deep hibernation is not enabled in {settings.fc_sandbox_region!r}; "
            "pause() answers 403 PauseSessionForbidden outside ap-southeast-1"
        )

    job = await asyncio.to_thread(
        dbmod.fetch_job_for_owner, db, training_job_id, owner_id
    )
    if job is None:
        raise TrainingJobNotAuthorised(
            f"training job {training_job_id} is not available to this account"
        )

    # Supplied, else inherited from the job, else minted. See the docstring:
    # `new_correlation_id` appears here and nowhere else in this module.
    inherited = job.get("correlation_id")
    correlation_id = (
        correlation_id
        or (str(inherited) if inherited is not None else None)
        or new_correlation_id()
    )

    row = await asyncio.to_thread(
        ss.create_session,
        db,
        owner_id=str(owner_id),
        pool_id=pool_id,
        training_job_id=str(training_job_id),
        region=settings.fc_sandbox_region,
        template=settings.fc_sandbox_template,
        evaluation_spec=dict(evaluation_spec or {}),
        correlation_id=correlation_id,
    )
    session_id = str(row["id"])
    log_event(
        log, "sandbox.session_opened",
        chain=Chain(correlation_id=correlation_id, session_id=session_id,
                    job_id=str(training_job_id)),
        state=ss.INITIAL_STATE,
        # `region` and `template` are deployment settings and would pass a
        # provenance check, but they have no slot here and do not need one:
        # the ids are what makes the line findable, and everything else about
        # this session is one query away from them.
        reason="inherited" if inherited is not None else "minted",
    )
    ledger = _Ledger(db, session_id)
    await ledger.record(
        "session.opened",
        # The spec itself is a COLUMN (migration 0017), not an event payload:
        # every string in an event is scrubbed by a key-name matcher that
        # would silently replace a spec field called `api_key_name` with
        # `[redacted]`, and nobody would find out until the wake. What is
        # recorded here is the shape of it, which is safe to scrub because
        # nothing reads it back.
        spec_keys=sorted(str(k) for k in (evaluation_spec or {})),
        expected_wait_s=float(expected_wait_s),
    )

    try:
        await _prepare_and_hibernate(
            db, gateway, settings,
            session_id=session_id,
            owner_id=str(owner_id),
            pool_id=pool_id,
            coordinator_url=coordinator_url,
            created_at=row["created_at"],
            expected_wait_s=float(expected_wait_s),
            resume_reserve_s=float(resume_reserve_s),
            paths=paths,
            bootstrap=bootstrap,
            ledger=ledger,
            bootstrap_kwargs=bootstrap_kwargs,
        )
    except SessionFailed:
        raise
    except BaseException as exc:  # noqa: BLE001 - recorded, cleaned up, re-raised
        code = type(exc).__name__
        await _cleanup_even_if_cancelled(
            _fail(db, gateway, settings, session_id=session_id,
                  code=code, detail=str(exc), ledger=ledger)
        )
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
            raise
        raise SessionFailed(session_id, code, str(exc)) from exc
    return session_id


async def _prepare_and_hibernate(
    db: psycopg.Connection,
    gateway: SandboxGateway,
    settings: Settings,
    *,
    session_id: str,
    owner_id: str,
    pool_id: str,
    coordinator_url: str,
    created_at: datetime,
    expected_wait_s: float,
    resume_reserve_s: float,
    paths: SandboxPaths,
    bootstrap: BootstrapFn,
    ledger: _Ledger,
    bootstrap_kwargs: Mapping[str, Any],
) -> None:
    """Steps 4 to 8. Split out so :func:`start_session` reads as a sequence
    and every exception in here meets exactly one handler."""

    # -- 4. one machine, one pool, one credential ---------------------------
    already = await asyncio.to_thread(dbmod.machine_ids_bound_to_pool, db, pool_id)
    if already:
        raise sandbox_identity.PoolNotIsolated(
            f"pool {pool_id} already holds {len(already)} machine(s); a "
            "session cannot mint into a pool that is not empty — an earlier "
            "session's cleanup did not finish"
        )

    credential = await asyncio.to_thread(
        sandbox_identity.provision_sandbox_machine,
        db,
        owner_id=owner_id,
        pool_id=pool_id,
        node_id=node_id_for(session_id),
        label=f"fc sandbox session {session_id[:8]}",
    )
    await asyncio.to_thread(
        sandbox_identity.assert_pool_isolated,
        db, pool_id=pool_id, machine_id=credential.machine_id,
    )
    await ledger.record(
        "machine.provisioned",
        machine_id=credential.machine_id,
        node_id=credential.node_id,
        pool_id=pool_id,
    )

    # -- 5. create, and record the id in the move out of REQUESTED ----------
    try:
        observed = await gateway.create(
            template=settings.fc_sandbox_template,
            timeout_ms=int(settings.fc_sandbox_timeout_ms),
            metadata={
                "flashml_session_id": session_id,
                "flashml_pool_id": pool_id,
            },
        )
    except SandboxError as exc:
        settled = await _settle_after(
            gateway, "", exc, ledger=ledger, operation="create"
        )
        if settled is None or not settled.sandbox_id:
            raise
        observed = settled

    sandbox_id = observed.sandbox_id
    if not sandbox_id:
        raise SandboxTerminalError(
            "the sandbox was created with no id, so it can never be killed",
            operation="create",
        )
    moved = await _transition(
        db, session_id, "REQUESTED", "ACTIVE",
        external_sandbox_id=sandbox_id,
        machine_id=credential.machine_id,
        observation=Observation(
            type="sandbox.created", source="fc",
            latency_ms=float(observed.latency_ms),
            data=_jsonable({
                "sandbox_id": sandbox_id,
                "sandbox_state": str(observed.state),
                "template": observed.template,
                "expires_at": observed.expires_at,
                "region": settings.fc_sandbox_region,
            }),
        ),
    )
    if not moved:
        # Nobody else can be driving a session this call has only just
        # created, so this is a bug rather than a race — but the sandbox is
        # real and the id is now unrecorded, which is the expensive half.
        raise SandboxTerminalError(
            f"could not record sandbox {sandbox_id} against session "
            f"{session_id}: the session is no longer REQUESTED",
            operation="create", sandbox_id=sandbox_id,
        )
    if observed.state is not SandboxState.RUNNING:
        raise SandboxTerminalError(
            f"a freshly created sandbox reported {observed.state}",
            operation="create", sandbox_id=sandbox_id,
        )

    # -- 6. bootstrap the worker -------------------------------------------
    result: BootstrapResult = await bootstrap(
        gateway,
        sandbox_id,
        credential=credential,
        coordinator_url=coordinator_url,
        pool_id=pool_id,
        marker_nonce=marker_nonce_for(session_id),
        paths=paths,
        on_observation=ledger.sink(),
        **dict(bootstrap_kwargs),
    )
    prepared = await _transition(
        db, session_id, "ACTIVE", "PREPARED",
        marker_sha256=result.marker_sha256,
        observation=Observation(
            type="worker.prepared", source="controller",
            latency_ms=float(result.register_ms),
            data=_jsonable({
                "node_id": result.node_id,
                "worker_pid": result.worker_pid,
                "flashnode_version": result.flashnode_version,
                "install_ms": round(float(result.install_ms), 3),
                "reused": bool(result.reused),
            }),
        ),
    )
    if not prepared:
        raise SandboxTerminalError(
            f"session {session_id} left ACTIVE while its worker was being "
            "prepared; another controller is driving it",
            operation="bootstrap", sandbox_id=sandbox_id,
        )

    # -- 7. the budget assertion, before the pause and never after ---------
    await _assert_budget(
        gateway, sandbox_id, settings,
        session_id=session_id, created_at=created_at,
        needed_s=expected_wait_s + resume_reserve_s, ledger=ledger,
    )

    # -- 8. pause, and believe only the read that followed it --------------
    paused = await _pause_sandbox(gateway, sandbox_id, ledger=ledger)
    hibernated = await _transition(
        db, session_id, "PREPARED", "HIBERNATED",
        observation=Observation(
            type="sandbox.hibernated", source="fc",
            latency_ms=float(paused.latency_ms),
            data=_jsonable({
                "sandbox_state": str(paused.state),
                "expires_at": paused.expires_at,
            }),
        ),
    )
    if not hibernated:
        raise SandboxTerminalError(
            f"session {session_id} left PREPARED during the pause",
            operation="pause", sandbox_id=sandbox_id,
        )


async def _assert_budget(
    gateway: SandboxGateway,
    sandbox_id: str,
    settings: Settings,
    *,
    session_id: str,
    created_at: datetime,
    needed_s: float,
    ledger: _Ledger,
) -> float:
    """Refuse to hibernate a sandbox that will not outlast the wait.

    Two clocks may be running here and only one has been measured. The E2B
    sandbox TTL *appeared* to stop while paused (n=1, a 150 s pause); Alibaba
    doc 3028695 says the FC Session TTL keeps accruing from creation
    regardless. So this assertion assumes the pessimistic reading — that the
    clock keeps running — which is correct under either, and is the only
    assumption that is.

    The provider's own ``end_at`` is preferred when it reports one. When it
    does not, the fallback is the configured lifetime measured from the
    session's creation, which is a strictly conservative substitute: it can
    only ever under-estimate what is left.

    A sandbox that expires mid-hibernation is gone and takes the prepared
    environment with it, so failing here — before the 2.6 s pause, with the
    worker still running and killable — is the cheap outcome.
    """
    observed = await gateway.inspect(sandbox_id)
    now = _now()
    if observed.expires_at is not None:
        deadline = _as_utc(observed.expires_at)
        source = "provider"
    else:
        deadline = _as_utc(created_at) + timedelta(
            milliseconds=max(0, int(settings.fc_sandbox_timeout_ms))
        )
        source = "configured"
    remaining_s = (deadline - now).total_seconds()

    await ledger.record(
        "sandbox.budget_checked",
        remaining_s=round(remaining_s, 3),
        required_s=round(float(needed_s), 3),
        deadline=deadline,
        budget_source=source,
        sandbox_state=str(observed.state),
    )
    if remaining_s < needed_s:
        raise SandboxTerminalError(
            f"the sandbox has {remaining_s:.0f}s of budget left and the "
            f"session needs {needed_s:.0f}s; hibernating it would lose the "
            "prepared environment to an expiry nobody is watching",
            operation="budget", sandbox_id=sandbox_id,
        )
    return remaining_s


# ---------------------------------------------------------------------------
# on_model_ready
# ---------------------------------------------------------------------------


async def on_model_ready(
    db: psycopg.Connection,
    gateway: SandboxGateway,
    settings: Settings,
    *,
    session_id: str,
    driver: EvaluationDriver | None = None,
    oss: OSSArtifacts | None = None,
    model_key: str | None = None,
    presign_ttl_s: int = DEFAULT_TTL_S,
    evaluation_timeout_s: float = DEFAULT_EVALUATION_TIMEOUT_S,
    poll_interval_s: float = EVALUATION_POLL_INTERVAL_S,
    paths: SandboxPaths = DEFAULT_PATHS,
    verify: Callable[..., Any] = verify_worker,
) -> None:
    """Wake the sandbox, evaluate, and clean up. Safe to call repeatedly.

    The trigger is an *observation*: the training artifact is in OSS. That is
    checked with ``head``, where ``None`` means **absent**, which is a fact
    and not a failure — the evaluator waits precisely because the model does
    not exist yet — so this returns without transitioning and the next poll
    asks again. Only a transport or auth failure raises.

    The ordering after that is the part worth reading:

    1. **Compare-and-set HIBERNATED → RESUMING first.** Winning that is what
       makes this call the one controller driving the session; a loser returns
       immediately, having submitted nothing and woken nothing. Two
       controllers both waking one sandbox is the ordinary shape of a restart
       during a long hibernation, and this is the only thing that prevents it.
    2. **``connect()``**, which is the wake — there is no ``resume``. The
       gateway re-sends the configured lifetime; a bare connect would cut a
       one-hour sandbox to five minutes. Its latency is recorded on its own,
       as the wake and nothing else.
    3. **``verify_worker``**: marker hash, pid in ``/proc``, and evidence of
       claiming. It repairs nothing and it never re-bootstraps — the worker
       survives hibernation, measured 5/5, and a fresh install would be a
       second agent under one node id.
    4. **Re-mint the presigned URLs**, first thing after the health check. A
       URL signed before the hibernation is expired on the other side of it.
       They are written into the sandbox with ``write_file`` and appear in no
       event, no log line and no command string.
    5. **Submit the evaluation task, last, and only once the artifacts are
       confirmed on disk.** The woken worker is already claiming every two
       seconds, so a task queued any earlier can be picked up by a process
       that finds no ``artifacts.json``.
       :func:`_assert_artifacts_present` is what makes that ordering
       enforceable rather than merely documented.
    6. **EVALUATING**, then a bounded wait for an *accepted* result, then
       SUCCEEDED, then :func:`cleanup_session` — because a session whose
       evaluation succeeded still owns a live, billing sandbox.

    Calling this twice produces one evaluation job and one resume. A second
    call while the first is still working finds the session in RESUMING or
    EVALUATING, adopts the recorded evaluation job id, and waits on the same
    result rather than submitting another.

    **This is the far side of the hibernation, and it is what the correlation
    id is for.** Everything this function has is a session id and a
    connection — which is exactly what a redeployed API has, because deep
    hibernation destroys the instance and the process that opened the session
    is long gone. The thread survives because it is a column: the row read on
    the first line below carries the same id the submission started with, and
    the line logged from it is the join a person would otherwise do by hand
    across five queries.
    """
    row = await _fetch(db, session_id)
    state = str(row["state"])
    # Before the settled-state return, so that a call arriving after the
    # session finished is still a line somebody can find on the thread. `state`
    # is our own enum and `sandbox_id` is the provider's id for infrastructure
    # we created; nothing on this line was authored by a submitter.
    log_event(
        log, "sandbox.wake_requested", chain=chain_of(row), state=state,
    )
    if state in ss.SETTLED_STATES:
        return
    if driver is None:
        raise EvaluationUnavailable(
            "on_model_ready needs an EvaluationDriver: waking a sandbox to "
            "hand it no work is the outcome the wake ordering exists to "
            "prevent"
        )

    ledger = _Ledger(db, session_id)
    sandbox_id = row["external_sandbox_id"]
    if not sandbox_id:
        raise SessionFailed(
            session_id, "NoSandbox",
            "the session has no external_sandbox_id, so there is nothing to wake",
        )

    # Already evaluating: the sandbox is awake, the task is placed, and the
    # only thing left is the verdict. Nothing is re-submitted and nothing is
    # re-woken — this call simply joins the wait, which is what makes a
    # second trigger (or a restarted poller) harmless.
    if state == "EVALUATING":
        await _await_and_settle(
            db, gateway, settings, session_id=session_id, ledger=ledger,
            driver=driver,
            evaluation_job_id=str(row["evaluation_job_id"] or ""),
            evaluation_timeout_s=evaluation_timeout_s,
            poll_interval_s=poll_interval_s,
        )
        return
    # Which of the three resumable shapes is this?
    #
    # HIBERNATED is the ordinary trigger. RESUMING is a controller that died
    # part-way through a wake. ACTIVE is one that died between the health
    # check and the presign — and it is distinguished from the ACTIVE that
    # `start_session` passes through on its way to PREPARED by the marker
    # hash, which is written when the worker is first prepared and never
    # un-written. Waking a sandbox `start_session` is still building would be
    # two controllers on one session.
    if state == "ACTIVE" and not row["marker_sha256"]:
        return
    if state not in ("HIBERNATED", "RESUMING", "ACTIVE"):
        return
    resuming = state != "ACTIVE"

    oss = oss or _oss_from(settings)
    key = model_key or manifest_key(str(row["training_job_id"]))

    if state == "HIBERNATED":
        present = await asyncio.to_thread(oss.head, key)
        if present is None:
            # Absent is a FACT, not an error. Nothing moves; the next poll
            # asks again. Deliberately not recorded as an event — a training
            # run that takes an hour would otherwise write a thousand rows
            # saying the same nothing.
            return
        await ledger.record(
            "artifact.observed",
            key=key,
            size_bytes=getattr(present, "size_bytes", None),
            last_modified=getattr(present, "last_modified", None),
        )
        if not await _transition(
            db, session_id, "HIBERNATED", "RESUMING",
            observation=Observation(
                type="session.resuming", source="controller",
                data={"trigger": "artifact-observed"},
            ),
        ):
            # Another controller won. It is driving; we are not. This is an
            # ordinary outcome and there is nothing to retry.
            return

    try:
        await _resume_and_evaluate(
            db, gateway, settings,
            session_id=session_id, sandbox_id=sandbox_id,
            owner_id=str(row["owner_id"]), pool_id=str(row["pool_id"]),
            training_job_id=str(row["training_job_id"]),
            marker_sha256=str(row["marker_sha256"] or ""),
            ledger=ledger, driver=driver, oss=oss,
            presign_ttl_s=int(presign_ttl_s),
            evaluation_timeout_s=float(evaluation_timeout_s),
            poll_interval_s=float(poll_interval_s),
            evaluation_spec=dict(row["evaluation_spec"] or {}),
            paths=paths, verify=verify, resuming=resuming,
        )
    except SessionFailed:
        raise
    except BaseException as exc:  # noqa: BLE001 - recorded, cleaned up, re-raised
        code = type(exc).__name__
        await _cleanup_even_if_cancelled(
            _fail(db, gateway, settings, session_id=session_id,
                  code=code, detail=str(exc), ledger=ledger)
        )
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
            raise
        raise SessionFailed(session_id, code, str(exc)) from exc


async def _resume_and_evaluate(
    db: psycopg.Connection,
    gateway: SandboxGateway,
    settings: Settings,
    *,
    session_id: str,
    sandbox_id: str,
    owner_id: str,
    pool_id: str,
    training_job_id: str,
    marker_sha256: str,
    ledger: _Ledger,
    driver: EvaluationDriver,
    oss: OSSArtifacts,
    presign_ttl_s: int,
    evaluation_timeout_s: float,
    poll_interval_s: float,
    evaluation_spec: Mapping[str, Any],
    paths: SandboxPaths,
    verify: Callable[..., Any],
    resuming: bool,
) -> None:
    """The wake path proper, from a won RESUMING to a settled session.

    **Wake, verify, deliver, THEN submit.** An earlier draft submitted the
    task first, so that the measured wake latency would read as
    time-to-productive — but the sandbox comes back with a running worker
    that claims every two seconds, so a task queued before the wake can be
    claimed in the window between ``connect()`` returning and
    ``artifacts.json`` being written, and that worker finds no artifacts at
    all. Presentation does not outrank correctness. Submitting last removes
    the window entirely, and :func:`_assert_artifacts_present` makes the
    ordering a property of the code rather than of this paragraph.

    It also produces **two honest numbers instead of one blended one**:
    ``sandbox.woken`` carries the provider's wake latency and nothing else,
    and ``evaluation.submitted`` carries how long the controller took to make
    the woken sandbox productive. A single figure that folded the health
    probe and the presign into "the wake" would overstate the wake and
    understate everything it was hiding.

    ``resuming`` is False on exactly one path: a restart that finds the
    session already ACTIVE, i.e. one whose wake and health check landed before
    the controller died. The state machine has no ACTIVE → ACTIVE edge and
    would raise ``IllegalTransition`` for one, which is correct — re-observing
    a state is an event, not a move — so that step is skipped rather than
    retried.
    """
    # -- 3. the wake. Measured alone, because that is what it is. -----------
    woken_at = time.monotonic()
    woken = await _wake_sandbox(gateway, sandbox_id, ledger=ledger)

    # -- 4. what came back, checked rather than assumed --------------------
    worker_pid = await asyncio.to_thread(_recorded_worker_pid, db, session_id)
    health: WorkerHealth = await verify(
        gateway, sandbox_id,
        expected_marker_sha256=marker_sha256,
        expected_pid=worker_pid,
        paths=paths,
        on_observation=ledger.sink(),
    )
    if not health.healthy:
        raise SandboxTerminalError(
            f"the worker did not survive the hibernation: {health.detail}",
            operation="verify", sandbox_id=sandbox_id,
        )

    if resuming:
        resumed = await _transition(
            db, session_id, "RESUMING", "ACTIVE",
            marker_sha256=health.marker_sha256 or None,
            observation=Observation(
                type="worker.resumed", source="controller",
                # The health check's own cost. The wake's is on
                # `sandbox.woken`, where it belongs — folding the two would
                # make the wake look slower than it measured.
                latency_ms=round(float(health.checked_ms), 3),
                data=_jsonable({
                    "marker_matches": health.marker_matches,
                    "pid_alive": health.pid_alive,
                    "claiming": health.claiming,
                    "credential_absent": health.credential_absent,
                    "wake_ms": float(woken.latency_ms),
                }),
            ),
        )
        if not resumed:
            # Somebody else moved the session out of RESUMING. Stop: the
            # sandbox is awake and healthy, and whoever won is driving it.
            return

    # -- 5. re-mint, because a URL signed before the pause is dead ---------
    await _deliver_artifacts(
        gateway, sandbox_id, oss,
        session_id=session_id, training_job_id=training_job_id,
        ttl_s=presign_ttl_s, paths=paths, ledger=ledger,
    )

    # -- 6. only now is there anything worth claiming ----------------------
    await _assert_artifacts_present(gateway, sandbox_id, paths=paths)
    evaluation_job_id = await _ensure_evaluation_submitted(
        db, session_id=session_id, ledger=ledger, driver=driver,
        ready_ms=(time.monotonic() - woken_at) * 1000,
        request=EvaluationRequest(
            session_id=session_id,
            owner_id=owner_id,
            pool_id=pool_id,
            training_job_id=training_job_id,
            node_id=node_id_for(session_id),
            spec=evaluation_spec,
        ),
    )

    # -- 7. evaluating, then a bounded wait for an accepted result ---------
    if not await _transition(
        db, session_id, "ACTIVE", "EVALUATING",
        evaluation_job_id=evaluation_job_id,
        observation=Observation(
            type="evaluation.started", source="controller",
            data={"evaluation_job_id": evaluation_job_id},
        ),
    ):
        return

    await _await_and_settle(
        db, gateway, settings, session_id=session_id, ledger=ledger,
        driver=driver, evaluation_job_id=evaluation_job_id,
        evaluation_timeout_s=evaluation_timeout_s,
        poll_interval_s=poll_interval_s,
    )


async def _assert_artifacts_present(
    gateway: SandboxGateway, sandbox_id: str, *, paths: SandboxPaths
) -> None:
    """Refuse to submit until the woken worker has something to read.

    This is the ordering rule made enforceable. The worker in there is
    already running and claims every two seconds, so a task placed before
    ``artifacts.json`` exists can be claimed by a process that then finds
    nothing — a failure that would present as "the evaluation script could
    not open the model", hours in, and would reproduce roughly never.

    ``stat -c %a`` rather than ``test -e``, following
    ``sandbox_bootstrap._write_credential``: one round trip answers both
    "is it there" (stat exits non-zero if not) and "is it 0600", and the mode
    is worth confirming because ``write_file`` sets it with a *second* call
    that can fail on its own. Cheap: the sandbox is awake by now, so this
    wakes nothing.
    """
    path = evaluation_artifacts_path(paths)
    evidence = await gateway.run(
        sandbox_id, f"stat -c %a {shlex.quote(path)}", timeout_s=30
    )
    mode = (evidence.stdout or "").strip()
    if not evidence.ok or mode != f"{ARTIFACTS_MODE:o}":
        raise SandboxTerminalError(
            f"{path} is not present at {ARTIFACTS_MODE:o} after the re-mint "
            f"(exit {evidence.exit_code}, mode {mode or 'unknown'}); refusing "
            "to queue work the woken worker cannot do",
            operation="artifacts", sandbox_id=sandbox_id,
        )


async def _ensure_evaluation_submitted(
    db: psycopg.Connection,
    *,
    session_id: str,
    ledger: _Ledger,
    driver: EvaluationDriver,
    request: EvaluationRequest,
    ready_ms: float | None = None,
) -> str:
    """One evaluation job per session, across restarts.

    The row's ``evaluation_job_id`` is written by the RESUMING → ACTIVE
    transition, which is *after* the submission — so between the coordinator
    accepting the job and that transition landing there is a window in which
    the column is still null and a restarted controller would submit again.
    The :data:`EVALUATION_SUBMITTED` event closes almost all of it: it is
    appended immediately, in its own statement, so recovery reads the id from
    the history rather than from a column that has not been written yet.

    What is left is the width of one append, and it is closed on the other
    side by :meth:`EvaluationDriver.submit` being required to be idempotent on
    ``session_id``. Both halves are needed; neither alone is enough.
    """
    row = await asyncio.to_thread(ss.fetch_session, db, session_id)
    if row is not None and row["evaluation_job_id"]:
        return str(row["evaluation_job_id"])

    recorded = await asyncio.to_thread(_evaluation_id_from_events, db, session_id)
    if recorded:
        return recorded

    started = time.monotonic()
    evaluation_job_id = str(await driver.submit(request))
    if not evaluation_job_id:
        raise SandboxTerminalError(
            "the evaluation was accepted with no job id to own it by",
            operation="evaluation.submit",
        )
    await ledger.record(
        EVALUATION_SUBMITTED,
        # The submission's own round trip. `ready_ms` beside it is the
        # controller's contribution: wall time from the sandbox being
        # observed awake to it having work queued and the artifacts to do it
        # with. Two separate numbers because they answer two questions, and
        # neither of them is the wake — that is on `sandbox.woken`.
        latency_ms=(time.monotonic() - started) * 1000,
        ready_ms=None if ready_ms is None else round(float(ready_ms), 3),
        evaluation_job_id=evaluation_job_id,
        pool_id=request.pool_id,
        node_id=request.node_id,
    )
    return evaluation_job_id


def _evaluation_id_from_events(
    db: psycopg.Connection, session_id: str
) -> str | None:
    for event in ss.events_for_session(db, session_id):
        if event["type"] != EVALUATION_SUBMITTED:
            continue
        data = event.get("data") or {}
        found = data.get("evaluation_job_id")
        if found:
            return str(found)
    return None


def _recorded_worker_pid(db: psycopg.Connection, session_id: str) -> int:
    """The pid ``bootstrap_worker`` reported, out of the ledger.

    Read back rather than carried, because the process that recorded it is
    routinely not the process that needs it — that is the entire premise of
    hibernating across a deploy. ``0`` when no bootstrap event survives, which
    ``verify_worker`` reports as a dead pid; that is the right answer, because
    a session whose preparation left no trace is not one whose worker we can
    claim to recognise.
    """
    try:
        events = ss.events_for_session(db, session_id)
    except Exception:  # noqa: BLE001 - a read failure is not a live pid
        return 0
    for event in reversed(events):
        data = event.get("data") or {}
        pid = data.get("worker_pid")
        if isinstance(pid, int) and pid > 0:
            return pid
    return 0


async def _deliver_artifacts(
    gateway: SandboxGateway,
    sandbox_id: str,
    oss: OSSArtifacts,
    *,
    session_id: str,
    training_job_id: str,
    ttl_s: int,
    paths: SandboxPaths,
    ledger: _Ledger,
) -> None:
    """Mint the presigned URLs and put them in the sandbox.

    First action after the health check, because a URL signed before the
    hibernation is expired on the other side of it —
    ``alibaba_oss.DEFAULT_TTL_S`` is short precisely so carrying one across a
    lifecycle boundary fails loudly rather than intermittently.

    The URLs go in with ``write_file``. Not a command, ever: a ``run()``
    carrying them would put a signature-bearing bearer credential into
    ``CommandEvidence.command`` and from there into the event ledger. What is
    recorded here is the object *keys* and the count — enough to prove the
    re-mint happened, and nothing that grants anybody anything.
    """
    started = time.monotonic()
    urls = await presign_job_artifacts(training_job_id, oss, ttl_s=ttl_s)
    if not urls:
        raise SandboxTerminalError(
            f"job {training_job_id} presigned to no objects at all",
            operation="presign", sandbox_id=sandbox_id,
        )
    document = artifacts_document(
        session_id=session_id, training_job_id=training_job_id,
        urls=urls, ttl_s=ttl_s,
    )
    await gateway.write_file(
        sandbox_id, evaluation_artifacts_path(paths), document,
        mode=ARTIFACTS_MODE,
    )
    await ledger.record(
        "artifacts.presigned",
        latency_ms=(time.monotonic() - started) * 1000,
        object_count=len(urls),
        keys=sorted(urls),
        ttl_s=int(ttl_s),
        path=evaluation_artifacts_path(paths),
    )


async def _await_and_settle(
    db: psycopg.Connection,
    gateway: SandboxGateway,
    settings: Settings,
    *,
    session_id: str,
    ledger: _Ledger,
    driver: EvaluationDriver,
    evaluation_job_id: str,
    evaluation_timeout_s: float,
    poll_interval_s: float,
) -> None:
    """Wait for an accepted result, bounded, then end the session.

    Every exit from here runs :func:`cleanup_session`, including the refusals
    and the timeout, because ``TERMINAL_STATES`` is ``{"TERMINATED"}`` alone:
    a session that succeeded still owns a sandbox billing by the second, and
    the reconciler would otherwise be the only thing that ever stopped it.
    """
    if not evaluation_job_id:
        await _fail(db, gateway, settings, session_id=session_id,
                    code="NoEvaluationJob",
                    detail="the session is EVALUATING with no job id recorded",
                    ledger=ledger)
        raise SessionFailed(session_id, "NoEvaluationJob")

    outcome = await _poll_for_outcome(
        driver, evaluation_job_id,
        timeout_s=evaluation_timeout_s, interval_s=poll_interval_s,
        ledger=ledger,
    )
    if outcome is None:
        await _fail(db, gateway, settings, session_id=session_id,
                    code="EvaluationTimeout",
                    detail=(f"no settled result for {evaluation_job_id} within "
                            f"{evaluation_timeout_s:.0f}s"),
                    ledger=ledger)
        raise SessionFailed(session_id, "EvaluationTimeout")

    if not outcome.accepted:
        await _fail(db, gateway, settings, session_id=session_id,
                    code="EvaluationRejected", detail=outcome.detail,
                    ledger=ledger, data=dict(outcome.data))
        raise SessionFailed(session_id, "EvaluationRejected", outcome.detail)

    await _transition(
        db, session_id, "EVALUATING", "SUCCEEDED",
        evaluation_job_id=evaluation_job_id,
        observation=Observation(
            type="evaluation.accepted", source="runtime",
            data=_jsonable({
                "evaluation_job_id": evaluation_job_id,
                "detail": outcome.detail or None,
                # Nested rather than merged: a driver's own payload must not
                # be able to overwrite the two fields above by choosing a
                # colliding key.
                "result": dict(outcome.data),
            }),
        ),
    )
    # SUCCEEDED is not the end. The sandbox is still there and still billing.
    await cleanup_session(db, gateway, settings, session_id=session_id)


async def _poll_for_outcome(
    driver: EvaluationDriver,
    evaluation_job_id: str,
    *,
    timeout_s: float,
    interval_s: float,
    ledger: _Ledger,
) -> EvaluationOutcome | None:
    """Poll until settled or the deadline passes. Never longer.

    A driver that raises is not retried into oblivion either — the exception
    propagates, is recorded by the caller, and the session fails. An
    unbounded poll against a coordinator that has stopped answering is a
    sandbox held open for ever.
    """
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        outcome = await driver.poll(evaluation_job_id)
        if outcome is not None:
            return outcome
        if time.monotonic() >= deadline:
            await ledger.record(
                "evaluation.timeout",
                evaluation_job_id=evaluation_job_id,
                waited_s=round(float(timeout_s), 3),
            )
            return None
        await asyncio.sleep(max(0.0, float(interval_s)))


def _oss_from(settings: Settings) -> OSSArtifacts:
    if not settings.oss_configured:
        raise OSSUnavailable(
            "OSS is not configured, so there is no artifact to observe and no "
            "URL to mint"
        )
    return OSSArtifacts.from_settings(settings)


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


async def cleanup_session(
    db: psycopg.Connection,
    gateway: SandboxGateway,
    settings: Settings,
    *,
    session_id: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Kill the sandbox and revoke the credential. Both, independently.

    **Both** because they fail for unrelated reasons and each one left undone
    is its own liability: a surviving sandbox bills by the second, and a
    surviving machine token is a live credential for a machine nobody is
    watching. **Independently** because the obvious implementation — kill,
    then revoke — silently stops revoking the moment Alibaba has a bad
    minute. So the revocation runs in a ``finally``, and it runs whether the
    kill succeeded, failed, or was never attempted because there was no
    sandbox to attempt it on.

    Nothing in ``revoke_sandbox_machine`` touches Alibaba, which is what makes
    that possible: a sandbox that has already expired, been killed, or never
    existed revokes exactly as cleanly as a live one.

    **Idempotent.** A session already TERMINATED returns immediately and makes
    no calls at all. A second call after a partial cleanup re-runs both halves
    — ``kill`` on a dead sandbox is a no-op that reports TERMINATED, and
    ``revoke_sandbox_machine`` unbinds on every call including ones where the
    row is already revoked, which is exactly the state a crashed cleanup
    leaves behind.

    **TERMINATED is recorded only once the API confirms the sandbox is gone.**
    If the kill could not be completed the session is left where it is, and
    ``unfinished_sessions`` keeps handing it to :func:`reconcile` until
    somebody succeeds. This function does not raise for that: it is called
    from ``finally`` blocks, and an exception here would mask the failure that
    sent us in.
    """
    row = await asyncio.to_thread(ss.fetch_session, db, session_id)
    if row is None or ss.is_terminal(str(row["state"])):
        return

    ledger = _Ledger(db, session_id)
    sandbox_id = row["external_sandbox_id"]
    owner_id = str(row["owner_id"])
    machine_id = row["machine_id"] or await _machine_by_derived_node_id(
        db, session_id, owner_id
    )
    killed = False

    try:
        if sandbox_id:
            killed = await _kill_sandbox(gateway, str(sandbox_id), ledger=ledger)
        else:
            # Nothing was ever created, so nothing is billing. That is an
            # observation about the world and not an assumption about a call.
            killed = True
            await ledger.record("sandbox.absent",
                                detail="no sandbox was ever recorded")
    finally:
        if machine_id:
            try:
                revoked = await asyncio.to_thread(
                    sandbox_identity.revoke_sandbox_machine,
                    db, machine_id=str(machine_id), owner_id=owner_id,
                )
                await ledger.record("machine.revoked",
                                    machine_id=str(machine_id),
                                    changed=bool(revoked))
            except Exception as exc:  # noqa: BLE001 - recorded, never masking
                log.error(
                    "sandbox orchestrator: could not revoke machine %s for "
                    "session %s: %s", machine_id, session_id,
                    type(exc).__name__, exc_info=True,
                )
                await ledger.record("machine.revoke_failed",
                                    machine_id=str(machine_id),
                                    detail=type(exc).__name__)

    if not killed:
        # Deliberately no transition. The sandbox may still be running, and
        # a TERMINATED row is a promise that it is not.
        return

    current = await asyncio.to_thread(ss.fetch_session, db, session_id)
    if current is None or ss.is_terminal(str(current["state"])):
        return
    await _transition(
        db, session_id, str(current["state"]), "TERMINATED",
        error_code=error_code, error_message=error_message,
        observation=Observation(
            type="session.terminated", source="controller",
            data={"sandbox_killed": bool(sandbox_id), "reason": error_code or "cleanup"},
        ),
    )


async def _cleanup_even_if_cancelled(coro: Any) -> None:
    """Run a cleanup so that a cancellation cannot abandon it half-done.

    A plain ``await`` inside an ``except`` block is worthless under
    cancellation: the first suspension point re-raises ``CancelledError`` and
    the kill never happens. Shielding a real task means the caller's
    cancellation is still honoured immediately while the cleanup carries on to
    completion behind it.

    This is not a promise that cleanup always finishes — a process that is
    killed outright takes the task with it. That case is what
    :func:`reconcile` is for, and it is the only backstop there can be.
    """
    task = asyncio.ensure_future(coro)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        log.warning(
            "sandbox orchestrator: cancelled while cleaning up; the cleanup "
            "continues in the background and reconcile is the backstop"
        )
        raise


async def _machine_by_derived_node_id(
    db: psycopg.Connection, session_id: str, owner_id: str
) -> str | None:
    """The session's machine, found by the node id rather than the column.

    ``machine_id`` is written by the REQUESTED → ACTIVE transition, which is
    the same statement that records ``external_sandbox_id`` — so a session
    that provisioned a credential and then failed to create a sandbox has a
    real, live machine and a null column pointing at it. Without this lookup
    that credential is never revoked and its binding never dropped, which
    leaves the isolation pool permanently non-empty and the next session
    unable to start.

    :func:`node_id_for` is what makes it recoverable: the id is derived from
    the session, ``machines.node_id`` is globally unique, and the owner is
    checked here as well as inside ``revoke_sandbox_machine`` so a collision
    could never reach somebody else's row.
    """
    try:
        machine = await asyncio.to_thread(
            dbmod.fetch_machine_by_node_id, db, node_id_for(session_id)
        )
    except Exception:  # noqa: BLE001 - a lookup failure is not a live sandbox
        log.warning("sandbox orchestrator: could not look up the machine for "
                    "session %s", session_id, exc_info=True)
        return None
    if machine is None or str(machine["owner_id"]) != owner_id:
        return None
    return str(machine["id"])


async def _fail(
    db: psycopg.Connection,
    gateway: SandboxGateway,
    settings: Settings,
    *,
    session_id: str,
    code: str,
    detail: str,
    ledger: _Ledger,
    data: Mapping[str, Any] | None = None,
) -> None:
    """Record FAILED, then clean up. In that order, and both unconditionally.

    FAILED first so the reason survives even if the cleanup itself falls over
    — ``transition``'s column arguments are fill-in-only, so the later move to
    TERMINATED cannot erase the code and message written here.
    """
    row = await asyncio.to_thread(ss.fetch_session, db, session_id)
    if row is not None and not ss.is_terminal(str(row["state"])):
        state = str(row["state"])
        if state != "FAILED":
            await _transition(
                db, session_id, state, "FAILED",
                error_code=code, error_message=detail,
                observation=Observation(
                    type="session.failed", source="controller",
                    data=_jsonable({
                        "code": code, "result": dict(data) if data else {},
                    }),
                ),
            )
    await cleanup_session(
        db, gateway, settings, session_id=session_id,
        error_code=code, error_message=detail,
    )


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


async def reconcile(
    db: psycopg.Connection,
    gateway: SandboxGateway,
    settings: Settings,
    *,
    stale_after_s: float = DEFAULT_STALE_AFTER_S,
) -> list[str]:
    """Sweep every session that still owns a sandbox. Returns what it changed.

    **This is what makes "the API can die mid-hibernation and recover" a true
    sentence**, and what stops a forgotten sandbox billing indefinitely.
    ``unfinished_sessions`` returns every row with an ``external_sandbox_id``
    that is not TERMINATED — which deliberately includes SUCCEEDED and FAILED,
    because a run being over is not the same as the money having stopped.

    For each row the real sandbox is ``inspect``ed and the ledger is
    reconciled to what the API reports. ``inspect`` and nothing else:
    ``run()`` and ``write_file()`` implicitly wake a paused sandbox at ~1 s
    and put it back on billed time, so a bookkeeping sweep that reached for
    either would be a sweep that costs money every time it runs.

    Four outcomes, in the order they are checked:

    * *The sandbox is gone.* Kill (a no-op that reports TERMINATED) and
      revoke, then record TERMINATED. The ledger stops claiming a live
      sandbox that is not there.
    * *The session is settled but the sandbox is alive.* This is the leak the
      terminal set exists to catch: kill it.
    * *The sandbox has outlived its own deadline*, or the session has sat in a
      transient state past ``stale_after_s`` — the controller that owned it is
      gone. Fail it and clean up.
    * *Everything agrees.* A HIBERNATED session with a PAUSED sandbox is left
      strictly alone. A PREPARED session with a PAUSED sandbox is a pause that
      applied while the response was lost, so the ledger catches up.

    Failures are per row: one unreachable sandbox does not stop the sweep, and
    the id is simply not in the returned list.
    """
    rows = await asyncio.to_thread(ss.unfinished_sessions, db)
    touched: list[str] = []
    for row in rows:
        try:
            if await _reconcile_one(
                db, gateway, settings, row, stale_after_s=float(stale_after_s)
            ):
                touched.append(str(row["id"]))
        except Exception:  # noqa: BLE001 - one bad row must not stop the sweep
            log.warning(
                "sandbox orchestrator: could not reconcile session %s",
                row["id"], exc_info=True,
            )
    return touched


async def _reconcile_one(
    db: psycopg.Connection,
    gateway: SandboxGateway,
    settings: Settings,
    row: Mapping[str, Any],
    *,
    stale_after_s: float,
) -> bool:
    session_id = str(row["id"])
    sandbox_id = str(row["external_sandbox_id"])
    state = str(row["state"])
    ledger = _Ledger(db, session_id)

    try:
        observed = await gateway.inspect(sandbox_id)
    except SandboxError as exc:
        await ledger.record(
            "reconcile.unreadable", detail=type(exc).__name__,
            sandbox_id=sandbox_id,
        )
        return False

    await asyncio.to_thread(ledger.observed, "reconcile.observed", observed)

    # 1. Absent. Whatever the ledger believed, there is no sandbox.
    if observed.state is SandboxState.TERMINATED:
        if state in ss.SETTLED_STATES:
            # The verdict is already recorded; only the bookkeeping is late.
            await cleanup_session(db, gateway, settings, session_id=session_id)
        else:
            # A session that thought work was in progress and has no sandbox
            # to do it in. That is a failure, and saying so is the difference
            # between a run that ended and a run that vanished.
            await _fail(
                db, gateway, settings, session_id=session_id,
                code="SandboxGone",
                detail="the provider reports no such sandbox",
                ledger=ledger,
            )
        return True

    # 2. The run is over and the sandbox is not. The expensive case.
    if state in ss.SETTLED_STATES:
        await cleanup_session(db, gateway, settings, session_id=session_id)
        return True

    # 3. Out of time — its own deadline, or ours.
    #
    # The configured lifetime is the fallback deadline, and it is not
    # decoration: a HIBERNATED session is deliberately exempt from the
    # staleness rule (a hibernation is *meant* to last), so without a deadline
    # of some kind a provider that reports no `end_at` would leave a forgotten
    # sandbox billing for ever — the exact outcome this function exists to
    # prevent. A sandbox cannot outlive the TTL it was created with, whichever
    # of the two clocks is running.
    now = _now()
    created_at = row.get("created_at")
    if observed.expires_at is not None:
        deadline = _as_utc(observed.expires_at)
    elif isinstance(created_at, datetime):
        deadline = _as_utc(created_at) + timedelta(
            milliseconds=max(0, int(settings.fc_sandbox_timeout_ms))
        )
    else:  # pragma: no cover - the column is NOT NULL
        deadline = None
    expired = deadline is not None and deadline <= now
    updated_at = row.get("updated_at")
    stale = (
        state in _TRANSIENT_STATES
        and isinstance(updated_at, datetime)
        and (now - _as_utc(updated_at)).total_seconds() >= stale_after_s
    )
    if expired or stale:
        await _fail(
            db, gateway, settings, session_id=session_id,
            code="SandboxExpired" if expired else "SessionAbandoned",
            detail=(
                "the sandbox is past its own deadline"
                if expired else
                f"no controller has moved this session in {stale_after_s:.0f}s"
            ),
            ledger=ledger,
        )
        return True

    # 4. A pause whose response was lost. The state machine has the edge;
    #    the ledger simply did not hear about it.
    if state == "PREPARED" and observed.state is SandboxState.PAUSED:
        return await _transition(
            db, session_id, "PREPARED", "HIBERNATED",
            observation=Observation(
                type="sandbox.hibernated", source="fc",
                latency_ms=float(observed.latency_ms),
                data=_jsonable({
                    "sandbox_state": str(observed.state),
                    "expires_at": observed.expires_at,
                    "reconciled": True,
                }),
            ),
        )

    # Everything else agrees, or is a live session somebody is driving. A
    # HIBERNATED session with a PAUSED sandbox lands here and is left alone —
    # touching it with anything but `inspect` would wake it.
    return False
