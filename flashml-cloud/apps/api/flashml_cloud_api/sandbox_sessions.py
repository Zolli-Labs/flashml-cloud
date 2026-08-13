"""The durable ledger for one Alibaba FC Agent Sandbox lifecycle.

A sandbox is created, prepared, deep-hibernated, woken, made to evaluate, and
killed. The interesting part of that sequence is the part where **nothing of
ours is running**: the sandbox sits hibernated — its instance destroyed, its
filesystem snapshotted — for as long as training takes somewhere else, which
is minutes now and hours later. If the only record of "there is a live sandbox
`i-abc` in ap-southeast-1 that must be woken and then killed" is a Python
object in one API process, a deploy during that wait leaks a sandbox that
bills by the second and loses the run with it.

So this module is the answer to one sentence in the design spec (D7): *the API
must be restartable mid-run and resume from ``external_sandbox_id``*. Hard
rule 3 in the same words — nodes are disposable, state is not.

**Three properties, and each one is a bug this module exists to make
impossible.**

*Transitions are compare-and-set.* :func:`transition` moves a session only from
a state the caller says it believes the session is in, in one ``UPDATE ...
RETURNING``, so two controllers cannot both move one session
``HIBERNATED -> RESUMING`` and wake a sandbox twice. Losing that race is a
normal outcome and returns ``False``; it is not an error and does not raise.
Naming an *illegal* pair is a different thing entirely — that is a bug in the
caller, and it raises :class:`IllegalTransition` rather than quietly reporting
"somebody else won", which is what a returned ``False`` would tell a retry loop
until it gave up.

*Events are observed facts.* :func:`append_event` takes an :class:`Observation`
— what was SEEN, when, by whom, and how long it took — and there is
deliberately no way to record an intention. After a call that may have
succeeded remotely and failed in transit, the recovery is to inspect the
provider and append what came back, never to write down what was asked for.
Sequence numbers are allocated here, under the session row's lock, so they are
gapless per session and a client cannot choose one.

*Nothing that looks like a credential is stored.* ``error_message`` and every
string inside an event's ``data`` go through :func:`redact` first. That is not
hygiene theatre: the strings this ledger records come from a provider SDK and
from OSS, and a presigned URL — which is a bearer credential with a signature
in its query string — is exactly the kind of thing an exception message
carries. The table has no token column at all, for the reason
``flashml-cloud/AGENTS.md`` gives about the GitHub App: a table whose leak
grants nobody anything is worth more than one you promise to be careful with.

``evaluation_spec`` (migration 0017) is jsonb and is **not** an exception to
that. It holds a job specification — what to evaluate and against what — of
exactly the kind ``jobs.spec`` already holds, and nothing that grants access
to any of it: the sandbox authenticates as an ordinary machine and reaches
OSS through presigned URLs minted per object at the moment of use. It sits
outside the redaction path on purpose, because a key-name matcher tuned for
an SDK exception would quietly replace a spec field called ``api_key_name``
with ``[redacted]`` and nobody would find out until the wake. Every event
payload is still scrubbed exactly as before; this column simply is not one.

**Policy is pure and lives at the top of this file**, the same split
``contributions.py`` and ``storage.py`` make: which states are terminal, which
transitions are legal, and what counts as a secret are all *policy* — readable,
arguable, and testable with no database anywhere — while the rows are facts. A
state machine expressed as a trigger or a check constraint cannot be read or
argued with, and the whole point of writing it down is that somebody can.
"""
from __future__ import annotations

import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Json

# ---------------------------------------------------------------------------
# Policy. Pure functions and constants: no database, no I/O, no clock.
# ---------------------------------------------------------------------------

#: Every state a session may be in, matching the check constraint in
#: migration 0014. Spelled out here as well as there on purpose — the database
#: refuses an invalid value even if this module has a bug, and this module
#: refuses one without a round trip.
STATES: tuple[str, ...] = (
    "REQUESTED",
    "ACTIVE",
    "PREPARED",
    "HIBERNATED",
    "RESUMING",
    "EVALUATING",
    "SUCCEEDED",
    "FAILED",
    "TERMINATED",
)

#: Where every session begins: we have asked for a sandbox and do not yet know
#: whether we have one. That window is the whole reason this state exists — a
#: `create` that succeeds remotely and fails in transit leaves a real sandbox
#: with no id on our side, and REQUESTED is what says "go and look".
INITIAL_STATE = "REQUESTED"

DEFAULT_PROVIDER = "alibaba-fc-sandbox"

#: Nothing further can happen, and — the operative part — **no sandbox is
#: still running**. Exactly one state qualifies.
#:
#: SUCCEEDED and FAILED are deliberately NOT in here, and getting that wrong
#: is the expensive mistake this constant exists to prevent. A session whose
#: evaluation succeeded still owns a live FC sandbox until something kills it;
#: treating "the run is over" as "the money has stopped" is how a voucher is
#: drained by a sandbox nobody is looking at. :func:`unfinished_sessions`
#: sweeps on *this* set, so it keeps returning a succeeded session until
#: cleanup has actually been observed.
TERMINAL_STATES = frozenset({"TERMINATED"})

#: The run's outcome is decided and no further work will be attempted. Useful
#: for the console ("is this finished?"); useless for the reconciler, which
#: must care about the sandbox rather than the verdict. See above.
SETTLED_STATES = frozenset({"SUCCEEDED", "FAILED", "TERMINATED"})

#: Who observed an event. 'fc' is the provider's own answer, 'runtime' is the
#: coordinator or the agent, 'controller' is this API watching its own calls.
#: A latency the provider reports and one we timed around the provider are
#: different claims, and the evidence view has to be able to say which it is
#: showing.
EVENT_SOURCES = frozenset({"controller", "fc", "runtime"})

#: The lifecycle proper, as drawn in the design spec §3.4. Cleanup and failure
#: edges are NOT listed here — they are added by
#: :func:`legal_transitions_from`, because "you may always give up" and "you
#: may always clean up" are properties of every state rather than nine
#: separate entries that a tenth state would silently not get.
_FORWARD: Mapping[str, frozenset[str]] = {
    # create() came back with a sandbox id.
    "REQUESTED": frozenset({"ACTIVE"}),
    # Two ways out of a running sandbox: it finished being set up (the marker
    # and credential are written and the agent has registered), or — on the
    # wake path, where the environment is already prepared — the relaunched
    # worker picked up the evaluation task.
    "ACTIVE": frozenset({"PREPARED", "EVALUATING"}),
    # Only a PREPARED sandbox may hibernate. Pausing before the environment is
    # built snapshots an empty machine, which is the one thing hibernation was
    # supposed to buy us.
    "PREPARED": frozenset({"HIBERNATED"}),
    "HIBERNATED": frozenset({"RESUMING"}),
    # RESUMING -> ACTIVE, never straight to EVALUATING: the marker hash is
    # verified in between, and a session that skipped that step would be
    # claiming environment continuity it never checked (spec D2).
    "RESUMING": frozenset({"ACTIVE"}),
    "EVALUATING": frozenset({"SUCCEEDED"}),
    "SUCCEEDED": frozenset(),
    "FAILED": frozenset(),
    "TERMINATED": frozenset(),
}


class IllegalTransition(ValueError):
    """A transition this state machine does not have an edge for.

    Distinct from losing a compare-and-set, which is ordinary and returns
    ``False``. This is a caller bug — a swapped argument pair, or a state
    somebody assumed existed — and it is raised so it surfaces in the run that
    contains it rather than as a retry loop that never terminates.
    """


class UnknownSession(LookupError):
    """An event was appended against a session id that is not in the table.

    Raised rather than left to the foreign key so the message names the id.
    The bare constraint violation arrives after a sequence has already been
    computed and says only that some column of some row failed, which is a
    poor thing to read at 3am during a demo.
    """


def is_terminal(state: str) -> bool:
    """True when nothing more will happen AND no sandbox is still billing."""
    _require_state(state)
    return state in TERMINAL_STATES


def is_settled(state: str) -> bool:
    """True when the run's outcome is decided — which is not the same as
    finished. See :data:`TERMINAL_STATES`."""
    _require_state(state)
    return state in SETTLED_STATES


def legal_transitions_from(state: str) -> frozenset[str]:
    """Every state ``state`` may move to.

    Two edges are added to every non-terminal state rather than written out
    per state:

    * ``-> FAILED``, because a controller exception can land anywhere and the
      alternative is a session stuck in ``RESUMING`` for ever, which reads to
      a reconciler as work in progress.
    * ``-> TERMINATED``, because cleanup runs in a ``finally`` (spec D11) and
      must be able to fire from whatever state the exception interrupted. A
      cleanup path that could not terminate from ``ACTIVE`` would leak the
      sandbox it was written to kill.
    """
    _require_state(state)
    if state in TERMINAL_STATES:
        return frozenset()
    onward = set(_FORWARD[state])
    onward.add("TERMINATED")
    if state != "FAILED":
        onward.add("FAILED")
    return frozenset(onward)


def is_legal_transition(from_state: str, to_state: str) -> bool:
    """Whether the machine has this edge. Self-transitions are not edges: a
    re-observation of the state a session is already in is an *event*, and
    recording it as a transition would make the ledger claim something moved
    when nothing did."""
    _require_state(from_state)
    _require_state(to_state)
    return to_state in legal_transitions_from(from_state)


def _require_state(state: str) -> None:
    if state not in STATES:
        raise ValueError(
            f"{state!r} is not a sandbox session state; expected one of "
            f"{', '.join(STATES)}"
        )


# ---------------------------------------------------------------------------
# Redaction.
#
# Applied to `error_message`, `error_code` and every string inside an event's
# `data` before any of them is bound to a statement. The threat is not a
# malicious caller — it is an SDK exception that echoes the request that
# caused it, and an OSS presigned URL, which is a bearer credential whose
# signature travels in a query string.
#
# Every pattern below is anchored on something a credential DECLARES about
# itself: a scheme, a vendor prefix, or a parameter name. Deliberately no
# "long high-entropy string" rule — a sha256 marker hash is 64 hex characters
# and is the evidence this whole feature rests on, so a heuristic that ate it
# would redact the one field the demo is about.
# ---------------------------------------------------------------------------

_PLACEHOLDER = "[redacted]"

#: Cap for a stored message. Applied AFTER redaction, never before: truncating
#: first can cut a token in half, and half a token no longer matches the
#: pattern that would have removed it while remaining every bit as leaked.
MAX_ERROR_CHARS = 2000

#: Parameter and field names that introduce a secret. Used both inside text
#: (``Signature=...``, ``"api_key": "..."``) and against jsonb keys, where the
#: value alone carries no marker at all — ``{"token": "a1b2c3"}`` is
#: indistinguishable from a hostname until you read the key.
#:
#: These are matched as SUBSTRINGS, which over-matches on purpose in one
#: direction and is worth knowing about: a field called ``tokens_processed``
#: is redacted along with a field called ``token``. That is the correct way
#: round — a lost metric is visible in the evidence view and someone fixes the
#: name, while a leaked credential is invisible and permanent — but do not
#: name a counter after a credential and expect to read it back.
#:
#: A bare ``sig`` is deliberately NOT in this list. It matches inside
#: ``assign``, ``design`` and ``consigned``, and ``signature`` already covers
#: every parameter this system actually meets: OSS signs with ``Signature=``
#: and AWS with ``X-Amz-Signature=``.
_SECRET_NAME = (
    r"(?:signature|secret|password|passwd|token|credential|authorization"
    r"|access[_-]?key|api[_-]?key|private[_-]?key)"
)

_SENSITIVE_KEY = re.compile(_SECRET_NAME, re.I)

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # A PEM block first: it spans newlines, and letting a later pattern chew
    # through its base64 body produces confetti that is still a key.
    (
        re.compile(
            r"-----BEGIN[^\n-]*PRIVATE KEY-----.*?-----END[^\n-]*PRIVATE KEY-----",
            re.S,
        ),
        _PLACEHOLDER,
    ),
    # `name=value` / `"name": "value"` where the NAME says it is a secret.
    # This is the one that catches presigned URLs — `Signature=`,
    # `OSSAccessKeyId=`, `X-Amz-Credential=` — and any SDK that prints its own
    # request parameters. The optional scheme group keeps
    # `Authorization: Bearer x` readable as `Authorization: Bearer
    # [redacted]`, which tells the next reader what kind of thing was removed.
    (
        re.compile(
            r"([A-Za-z0-9_.\[\]-]*" + _SECRET_NAME + r"[A-Za-z0-9_.\[\]-]*)"
            r"(\"?\s*[=:]\s*\"?)"
            r"((?:bearer|basic)\s+)?"
            r"([^\s\"'&,;)}\]]+)",
            re.I,
        ),
        r"\1\2\3" + _PLACEHOLDER,
    ),
    # A bare `Bearer <token>` with no parameter name in front of it.
    (
        re.compile(r"\b(bearer|basic)\s+([A-Za-z0-9._~+/=-]{8,})", re.I),
        r"\1 " + _PLACEHOLDER,
    ),
    # Anything shaped like a JWT. Supabase access tokens, GitHub App
    # assertions, and whatever a future provider hands us all land here.
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*"),
        _PLACEHOLDER,
    ),
    # Vendor prefixes, which are the whole reason token prefixes exist.
    # `fmk_` machine tokens and `fmu_` CLI tokens are ours (0001, 0012);
    # `LTAI` is an Alibaba access key id; the rest are the neighbours a
    # workspace `.env` tends to have lying around.
    (re.compile(r"\bfm[ku]_[A-Za-z0-9_-]{4,}"), _PLACEHOLDER),
    (re.compile(r"\bLTAI[A-Za-z0-9]{8,}"), _PLACEHOLDER),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"), _PLACEHOLDER),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), _PLACEHOLDER),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), _PLACEHOLDER),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), _PLACEHOLDER),
)


def scrub(text: str) -> str:
    """Every known credential shape in ``text``, replaced. Length untouched.

    Separate from :func:`redact` because ``data`` values want the scrubbing
    without the "blank becomes None" policy: an empty string inside a payload
    is a value somebody chose, while an empty error message is the absence of
    an error.
    """
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    # Postgres text cannot hold a NUL, and psycopg raises on one. A provider
    # returning a byte string decoded with errors="replace" is the realistic
    # source, and the resulting failure would be a lost ledger write during a
    # cleanup path — the worst possible place for one.
    return text.replace("\x00", "")


def redact(message: Any) -> str | None:
    """A message safe to store, or None.

    None for None and for anything blank once stripped: an empty error message
    is not an error, and storing ``''`` would make every reader distinguish
    two kinds of nothing.

    Non-strings are rendered with ``str()`` first — callers pass exceptions —
    and then scrubbed like anything else, because an exception's ``str()`` is
    exactly where a provider echoes the request it refused.
    """
    if message is None:
        return None
    text = message if isinstance(message, str) else str(message)
    text = scrub(text).strip()
    if not text:
        return None
    if len(text) > MAX_ERROR_CHARS:
        text = text[: MAX_ERROR_CHARS - 1].rstrip() + "…"
    return text


def redact_data(value: Any) -> Any:
    """``value`` with credentials removed, structure preserved.

    Two rules, because a payload leaks two ways. A *value* that declares
    itself (a presigned URL, an `fmk_` token) is caught by :func:`scrub`; a
    value that declares nothing but sits under a *key* that does — the
    ``{"token": "a1b2c3"}`` case — is replaced wholesale on the strength of
    the key alone. The second rule is the one that matters, because it is the
    shape a well-behaved SDK produces.
    """
    if isinstance(value, Mapping):
        return {
            str(key): (
                _PLACEHOLDER
                if _SENSITIVE_KEY.search(str(key))
                else redact_data(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_data(item) for item in value]
    if isinstance(value, str):
        scrubbed = scrub(value)
        return (
            scrubbed
            if len(scrubbed) <= MAX_ERROR_CHARS
            else scrubbed[: MAX_ERROR_CHARS - 1] + "…"
        )
    return value


def new_share_token() -> str:
    """An unguessable capability for the public, no-auth evidence view.

    256 bits from ``secrets``, because this is the only surface in the system
    that answers without a JWT and the token IS the authorization. Generated
    here rather than by a SQL default on purpose (migration 0014 says so
    too): a uuid default would make the one unauthenticated route in the
    product guessable from a clock on any deployment whose uuid generation is
    not what we assume it is.

    The ``shr_`` prefix follows ``fmk_``/``fmu_``: it makes the value
    recognisable in a log line somebody is about to paste somewhere, and it
    gives secret scanners something to match on.
    """
    return "shr_" + secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """Something that was SEEN, with the evidence for it.

    There is no counterpart for an intention, and that asymmetry is the design
    (spec D7). A controller that has just called ``pause()`` has not observed
    a hibernated sandbox; it has observed a response. When the response never
    arrives, the recovery is to ``inspect()`` and append whatever came back —
    which is a different fact, and one this type can carry while "I meant to
    pause it" is not.

    ``latency_ms`` is measured, not derived, and is None when nothing held a
    stopwatch. Zero would be a claim.
    """

    type: str
    source: str
    observed_at: datetime | None = None
    latency_ms: float | None = None
    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.type, str) or not self.type.strip():
            raise ValueError("an observation needs a non-empty type")
        if self.source not in EVENT_SOURCES:
            raise ValueError(
                f"{self.source!r} is not an event source; expected one of "
                f"{', '.join(sorted(EVENT_SOURCES))}"
            )
        if self.latency_ms is not None:
            if isinstance(self.latency_ms, bool) or not isinstance(
                self.latency_ms, (int, float)
            ):
                raise ValueError("latency_ms must be a number or None")
            if self.latency_ms < 0:
                raise ValueError("latency_ms cannot be negative")


def state_observation(state: str, *, source: str = "controller") -> Observation:
    """The observation a bare :func:`transition` records when the caller
    supplies none. Every state change lands in the ledger whether or not the
    caller remembered to say so, which is what keeps the history from
    disagreeing with the ``state`` column."""
    _require_state(state)
    return Observation(type=f"state.{state.lower()}", source=source)


# ---------------------------------------------------------------------------
# Repository
#
# `db` is a live psycopg connection in autocommit mode, opened by
# `db.connect()`, exactly as in `db.py`. Every read scoped to a person folds
# the owner into the query itself rather than filtering afterwards in Python:
# leaving it out has to be a missing argument, not a missing `if`.
# ---------------------------------------------------------------------------

#: Everything a session row carries. Spelled out — same discipline as
#: ``MACHINE_PUBLIC_COLUMNS`` and ``POOL_PUBLIC_COLUMNS`` in ``db`` — so a
#: column added to the table later does not start leaving this module by
#: default.
SESSION_COLUMNS: tuple[str, ...] = (
    "id",
    "owner_id",
    "pool_id",
    "machine_id",
    "training_job_id",
    "evaluation_job_id",
    "provider",
    "region",
    "template",
    "external_sandbox_id",
    "state",
    "marker_sha256",
    "evaluation_spec",
    "share_token",
    "created_at",
    "updated_at",
    "terminated_at",
    "error_code",
    "error_message",
)

#: What the PUBLIC share view may read. Five columns are missing and each one
#: is missing for its own reason: ``owner_id`` and ``pool_id`` identify people
#: and teams, ``machine_id`` and ``external_sandbox_id`` name live
#: infrastructure to somebody holding only a link, ``evaluation_spec`` names
#: the datasets, repositories and thresholds somebody was measuring, and
#: ``share_token`` is the capability itself — a page that echoes its own
#: bearer token hands it to every proxy, referrer header and screenshot in
#: the chain.
#:
#: ``id`` stays because the route needs it to read the session's events; the
#: route renders a suffix, never the whole value.
SESSION_SHARE_COLUMNS: tuple[str, ...] = (
    "id",
    "training_job_id",
    "evaluation_job_id",
    "provider",
    "region",
    "template",
    "state",
    "marker_sha256",
    "created_at",
    "updated_at",
    "terminated_at",
    "error_code",
    "error_message",
)

EVENT_COLUMNS: tuple[str, ...] = (
    "id",
    "session_id",
    "sequence",
    "type",
    "source",
    "observed_at",
    "latency_ms",
    "data",
)

_SESSION_SELECT = ", ".join(SESSION_COLUMNS)
_SESSION_SHARE_SELECT = ", ".join(f"s.{c}" for c in SESSION_SHARE_COLUMNS)
_EVENT_SELECT = ", ".join(EVENT_COLUMNS)


def create_session(
    db: psycopg.Connection,
    *,
    owner_id: str,
    pool_id: str,
    training_job_id: str,
    region: str,
    template: str,
    provider: str = DEFAULT_PROVIDER,
    machine_id: str | None = None,
    external_sandbox_id: str | None = None,
    evaluation_spec: Mapping[str, Any] | None = None,
    share_token: str | None = None,
    observation: Observation | None = None,
) -> dict[str, Any]:
    """Open a session at :data:`INITIAL_STATE` and record its first event.

    ``owner_id`` comes from the verified JWT ``sub``, never from a body, and
    the browser chooses none of ``pool_id``, ``region``, ``template`` or
    ``external_sandbox_id`` — those are deployment settings (spec §3.5). This
    function takes them as arguments rather than reading settings itself so
    the policy about who may pick what stays at the route, where it can be
    seen next to the authentication that justifies it.

    ``evaluation_spec`` is written **here and only here** (migration 0017).
    The evaluation is submitted on the far side of a hibernation designed to
    outlive the process that opened the session, so the specification has to
    be durable — and it is stored as a column rather than as an event payload
    because :func:`redact_data` scrubs every string in an event by matching
    KEY NAMES, which is exactly right for an SDK exception and silent data
    loss for a specification with a field called ``api_key_name``. It is a
    job specification and **never a credential**: the sandbox authenticates
    as an ordinary machine and reaches OSS through per-object presigned URLs,
    so nothing that grants access to anything belongs in it. :func:`transition`
    deliberately cannot touch it — a spec that changed half-way through a
    session would make the evaluation that actually ran unexplainable from
    the row describing it.

    A ``share_token`` is minted when the caller supplies none, so every
    session has a working evidence page from the moment it exists. The column
    stays nullable so one can be withdrawn later by setting it to NULL — a
    revocation that needs no migration.

    The row and its first event are written in ONE transaction. A session
    whose ledger begins at its second event would show a lifecycle that
    appears to start already running.
    """
    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                f"""
                insert into public.sandbox_sessions
                    (owner_id, pool_id, machine_id, training_job_id, provider,
                     region, template, external_sandbox_id, state,
                     evaluation_spec, share_token)
                values (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s,
                        %s, %s)
                returning {_SESSION_SELECT}
                """,
                (
                    owner_id,
                    pool_id,
                    machine_id,
                    training_job_id,
                    provider,
                    region,
                    template,
                    external_sandbox_id,
                    INITIAL_STATE,
                    Json(dict(evaluation_spec))
                    if evaluation_spec is not None
                    else None,
                    share_token if share_token is not None else new_share_token(),
                ),
            )
            row = cur.fetchone()
            assert row is not None
            _append_locked(
                cur,
                str(row["id"]),
                observation
                or Observation(
                    type="session.requested",
                    source="controller",
                    data={"provider": provider, "region": region},
                ),
                sequence=None,
            )
    return row


def transition(
    db: psycopg.Connection,
    session_id: str,
    from_state: str,
    to_state: str,
    *,
    observation: Observation | None = None,
    machine_id: str | None = None,
    external_sandbox_id: str | None = None,
    evaluation_job_id: str | None = None,
    marker_sha256: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> bool:
    """Move a session, but only if it is still where the caller left it.

    Returns True if this call performed the transition and False if it did
    not — because the session is in some other state already, or does not
    exist at all. **Losing is not an error.** Two controllers racing to wake
    one hibernated sandbox is the ordinary shape of this system under a
    restart, and exactly one of them must proceed; the other has nothing to
    apologise for and nothing to retry.

    False also covers "no such session", deliberately, and the route above
    answers 404 to both. Splitting them would let a caller with a guessed id
    learn that it is real, which is the same reason ``fetch_pool_for_member``
    folds "not yours" into "not found".

    One statement does the check and the move together. A ``select`` followed
    by an ``update`` has a window in it, and this is precisely the window that
    wakes a sandbox twice — the same argument ``claim_attempt_credit`` makes
    about crediting a lease twice.

    Naming a pair the machine has no edge for raises
    :class:`IllegalTransition` instead of returning False: a swapped argument
    pair reported as a lost race is a retry loop that never ends.

    The optional column arguments are **fill-in-only**: None leaves the
    existing value alone rather than clearing it, so a later transition that
    does not happen to mention the marker hash cannot erase the evidence an
    earlier one recorded. Un-learning a fact is not something this ledger
    offers.

    On success the transition and its event are one transaction. There is no
    state in this table that the history does not explain.
    """
    _require_state(from_state)
    _require_state(to_state)
    if not is_legal_transition(from_state, to_state):
        raise IllegalTransition(
            f"{from_state} -> {to_state} is not a legal sandbox session "
            f"transition; from {from_state} the machine allows "
            f"{', '.join(sorted(legal_transitions_from(from_state))) or 'nothing'}"
        )

    params = {
        "session_id": str(session_id),
        "from_state": from_state,
        "to_state": to_state,
        "machine_id": str(machine_id) if machine_id is not None else None,
        "external_sandbox_id": external_sandbox_id,
        "evaluation_job_id": evaluation_job_id,
        "marker_sha256": marker_sha256,
        # Redacted here, at the boundary, rather than trusted to every caller.
        # A sanitiser somebody has to remember to call is a sanitiser that
        # gets skipped on the one path that had an exception in hand.
        "error_code": redact(error_code),
        "error_message": redact(error_message),
    }

    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                f"""
                update public.sandbox_sessions
                   set state = %(to_state)s,
                       updated_at = now(),
                       machine_id = coalesce(%(machine_id)s::uuid, machine_id),
                       external_sandbox_id = coalesce(
                           %(external_sandbox_id)s::text, external_sandbox_id),
                       evaluation_job_id = coalesce(
                           %(evaluation_job_id)s::text, evaluation_job_id),
                       marker_sha256 = coalesce(
                           %(marker_sha256)s::text, marker_sha256),
                       error_code = coalesce(%(error_code)s::text, error_code),
                       error_message = coalesce(
                           %(error_message)s::text, error_message),
                       terminated_at = case
                           when %(to_state)s::text = 'TERMINATED'
                               then coalesce(terminated_at, now())
                           else terminated_at
                       end
                 where id = %(session_id)s::uuid
                   and state = %(from_state)s::text
                returning {_SESSION_SELECT}
                """,
                params,
            )
            row = cur.fetchone()
            if row is None:
                return False
            _append_locked(
                cur,
                str(row["id"]),
                observation or state_observation(to_state),
                sequence=None,
            )
    return True


def append_event(
    db: psycopg.Connection,
    session_id: str,
    observation: Observation,
    *,
    sequence: int | None = None,
) -> dict[str, Any] | None:
    """Record an observation against a session. Append only.

    Returns the row written, or **None if this observation was already
    recorded** — which happens only when the caller supplies an explicit
    ``sequence``, and is the point of allowing it. A controller that appended
    an event and then lost the connection before it learned so re-sends the
    same ``(session, sequence)`` pair; the unique constraint from migration
    0014 turns that into one row rather than two, and this returns None to
    say which happened. Idempotency is a property of the table, not of the
    caller's discipline — the argument 0003 makes about the credit ledger.

    With no explicit ``sequence`` the number is allocated here, under the
    session row's lock, so it is gapless from 1 and no client can pick one. A
    client-chosen sequence would let a retry claim a fresh number and write
    the same observation twice; a timestamp ordering would tie on the
    sub-millisecond pairs this lifecycle actually produces.

    Raises :class:`UnknownSession` for a session id that is not in the table.
    """
    with db.transaction():
        with db.cursor() as cur:
            return _append_locked(cur, str(session_id), observation, sequence)


def _append_locked(
    cur: psycopg.Cursor,
    session_id: str,
    observation: Observation,
    sequence: int | None,
) -> dict[str, Any] | None:
    """The insert, assuming a transaction is open.

    Takes the session row's lock BEFORE reading ``max(sequence)``. Without it
    two appends against one session read the same maximum, and the loser gets
    a unique violation that aborts whatever transaction it was part of —
    which, on the transition path, would be a state change already made.
    Serialising on the session row costs nothing (events for one session are
    written by one controller in the ordinary case) and makes the gapless
    guarantee true rather than probable.
    """
    if not isinstance(observation, Observation):
        raise TypeError(
            "append an Observation, not a dict: this ledger records what was "
            "seen, and the type is what keeps an intention out of it"
        )

    cur.execute(
        "select id from public.sandbox_sessions where id = %s::uuid for update",
        (session_id,),
    )
    if cur.fetchone() is None:
        raise UnknownSession(f"no sandbox session {session_id}")

    if sequence is None:
        cur.execute(
            "select coalesce(max(sequence), 0) + 1 as next_sequence"
            "  from public.sandbox_events where session_id = %s::uuid",
            (session_id,),
        )
        sequence = int(cur.fetchone()["next_sequence"])
    elif sequence < 1:
        raise ValueError("event sequences start at 1")

    cur.execute(
        f"""
        insert into public.sandbox_events
            (session_id, sequence, type, source, observed_at, latency_ms, data)
        values (%s::uuid, %s, %s, %s, coalesce(%s::timestamptz, now()), %s, %s)
        on conflict (session_id, sequence) do nothing
        returning {_EVENT_SELECT}
        """,
        (
            session_id,
            sequence,
            observation.type,
            observation.source,
            observation.observed_at,
            (
                float(observation.latency_ms)
                if observation.latency_ms is not None
                else None
            ),
            Json(redact_data(dict(observation.data))),
        ),
    )
    return cur.fetchone()


def fetch_session(
    db: psycopg.Connection, session_id: str
) -> dict[str, Any] | None:
    """Unscoped read, for the controller and the reconciler.

    Deliberately not owner-scoped, and deliberately not reachable from a
    route. A background sweep acts on behalf of the deployment rather than a
    person; every path a browser can reach uses
    :func:`fetch_session_for_owner`.
    """
    return db.execute(
        f"select {_SESSION_SELECT} from public.sandbox_sessions"
        " where id = %s::uuid",
        (str(session_id),),
    ).fetchone()


def fetch_session_for_owner(
    db: psycopg.Connection, session_id: str, owner_id: str
) -> dict[str, Any] | None:
    """Owner-scoped read: None when the session does not exist *or* belongs to
    somebody else.

    Callers must not tell those two apart in a response — same 404 doctrine as
    ``fetch_pool_for_member`` and ``fetch_machine_for_owner``. A 403 for
    "exists but not yours" confirms to a guesser that the id is real, and
    these ids sit in shareable URLs.
    """
    return db.execute(
        f"select {_SESSION_SELECT} from public.sandbox_sessions"
        " where id = %s::uuid and owner_id = %s::uuid",
        (str(session_id), str(owner_id)),
    ).fetchone()


def fetch_session_by_share_token(
    db: psycopg.Connection, share_token: str
) -> dict[str, Any] | None:
    """The public evidence view's read: no auth, narrowed columns.

    The token IS the authorization, so this is the one read in the module with
    no owner in it — which is exactly why it returns
    :data:`SESSION_SHARE_COLUMNS` rather than the full row. The narrowing is
    here, in the query, and not in a serializer the next person to add a field
    would have to remember.

    An empty or missing token matches nothing: without the guard, a NULL
    ``share_token`` on a session whose page was withdrawn would be handed to
    any caller who sent no token at all.
    """
    if not share_token:
        return None
    return db.execute(
        f"select {_SESSION_SHARE_SELECT} from public.sandbox_sessions s"
        " where s.share_token = %s",
        (share_token,),
    ).fetchone()


def list_sessions_for_owner(
    db: psycopg.Connection,
    owner_id: str,
    *,
    training_job_id: str | None = None,
) -> list[dict[str, Any]]:
    """This owner's sessions, newest first, optionally for one training job."""
    return list(
        db.execute(
            f"""
            select {_SESSION_SELECT} from public.sandbox_sessions
             where owner_id = %s::uuid
               and (%s::text is null or training_job_id = %s::text)
             order by created_at desc, id
            """,
            (str(owner_id), training_job_id, training_job_id),
        ).fetchall()
    )


def events_for_session(
    db: psycopg.Connection,
    session_id: str,
    *,
    after_sequence: int = 0,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """One session's history in order, for the controller and the share view.

    ``after_sequence`` is what makes the job page's poll cheap: the console
    holds the last sequence it drew and asks for what came after, so a page
    open for the length of a hibernation does not re-fetch the whole ledger
    every two seconds.
    """
    sql = (
        f"select {_EVENT_SELECT} from public.sandbox_events"
        " where session_id = %s::uuid and sequence > %s"
        " order by sequence"
    )
    params: tuple[Any, ...] = (str(session_id), int(after_sequence))
    if limit is not None:
        sql += " limit %s"
        params = params + (int(limit),)
    return list(db.execute(sql, params).fetchall())


def events_for_owner(
    db: psycopg.Connection,
    session_id: str,
    owner_id: str,
    *,
    after_sequence: int = 0,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """The same history, owner-scoped in SQL rather than by a prior check.

    A non-owner gets ``[]``, and so does a caller naming a session that does
    not exist. The join is the point: an events route that checked ownership
    in a separate statement and then read events would be one refactor away
    from checking nothing at all.
    """
    sql = (
        f"select {', '.join('e.' + c for c in EVENT_COLUMNS)}"
        "  from public.sandbox_events e"
        "  join public.sandbox_sessions s"
        "    on s.id = e.session_id and s.owner_id = %s::uuid"
        " where e.session_id = %s::uuid and e.sequence > %s"
        " order by e.sequence"
    )
    params: tuple[Any, ...] = (
        str(owner_id),
        str(session_id),
        int(after_sequence),
    )
    if limit is not None:
        sql += " limit %s"
        params = params + (int(limit),)
    return list(db.execute(sql, params).fetchall())


def unfinished_sessions(db: psycopg.Connection) -> list[dict[str, Any]]:
    """Every session holding a sandbox that is still alive somewhere.

    **This is the function that makes "the API can die mid-hibernation and
    recover" a true sentence.** A cleanup reconciler sweeps it on a timer and
    an API restart reads it at boot; both need the same answer, which is "what
    did some earlier process start that nobody has finished".

    Two predicates, and both are load-bearing:

    * ``external_sandbox_id is not null`` — a session with no provider id owns
      nothing to reconcile. It may still be a stuck REQUESTED row, but that is
      a bookkeeping problem, not money.
    * ``not in`` :data:`TERMINAL_STATES` — which is ``TERMINATED`` alone, so
      SUCCEEDED and FAILED sessions keep coming back until cleanup has
      actually been observed. That is the whole point: a run whose evaluation
      succeeded still owns a sandbox billing by the second, and a reconciler
      that skipped it would leak exactly the sessions that went well.

    Oldest first — the sandbox that has been running longest is the one
    costing the most, and a sweep that runs out of time should have dealt with
    it. Deliberately NOT owner-scoped: it acts for the deployment, not a
    person, which is why no route reaches it.
    """
    return list(
        db.execute(
            f"""
            select {_SESSION_SELECT} from public.sandbox_sessions
             where external_sandbox_id is not null
               and state <> all (%s::text[])
             order by created_at, id
            """,
            (sorted(TERMINAL_STATES),),
        ).fetchall()
    )


def live_session_in_pool(
    db: psycopg.Connection, pool_id: str
) -> dict[str, Any] | None:
    """The oldest session still holding this pool, or ``None``.

    Asked by :func:`sandbox_identity.provision_rented_machine` before it mints
    into a pool, and the reason it is asked *there* is that nothing else asks
    it in time. An evaluation session's pool is isolated on purpose — exactly
    one machine, so no other node is an eligible claimant for the code the
    submitter wrote — and the session re-checks that with
    ``assert_pool_isolated`` at its own mint. A rental arriving afterwards
    breaks the invariant with nothing watching until the session's *next*
    re-check, which may be after tasks have been placed.

    :data:`TERMINAL_STATES`, the same set :func:`unfinished_sessions` sweeps
    on, and for the same reason: it is ``TERMINATED`` alone, so a SUCCEEDED or
    FAILED session still counts. Its sandbox is alive until cleanup has been
    observed, and a machine that can claim its tasks is a problem for exactly
    as long.

    Unlike :func:`unfinished_sessions` this does NOT require an
    ``external_sandbox_id``: a REQUESTED session that has not yet heard back
    from the provider owns the pool just as much, and may be seconds away from
    minting into it.
    """
    return db.execute(
        f"""
        select {_SESSION_SELECT} from public.sandbox_sessions
         where pool_id = %s::uuid
           and state <> all (%s::text[])
         order by created_at, id
         limit 1
        """,
        (str(pool_id), sorted(TERMINAL_STATES)),
    ).fetchone()
