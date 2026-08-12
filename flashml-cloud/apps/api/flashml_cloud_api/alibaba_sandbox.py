"""The Alibaba FC Agent Sandbox, behind a gateway that reports what it saw.

This is the hibernatable evaluation worker. A sandbox is created, prepared,
put to sleep for however long the training half of the demo takes, woken,
and asked for a verdict. `settings.fc_sandbox_*` configures it; `render.yaml`
does not, deliberately — an unconfigured deploy must still boot.

**Why a gateway at all, rather than calling the SDK from a route.** Three
reasons, in the order they bit:

1. **The SDK is synchronous.** `e2b_code_interpreter.Sandbox` is blocking
   httpx underneath. Called from a coroutine it stops the event loop for the
   whole round trip — 1.8 s for a create, 2.7 s for a pause, and unbounded
   when the far end hangs. Every call here goes through `asyncio.to_thread`
   with an explicit ceiling.
2. **The API key is a bearer credential with no scope.** It creates
   sandboxes, kills them, and reads their metadata. It must not reach a log
   line, a traceback, an HTTP error body or a `repr`. `redact()` runs on
   every exception path in this module, and `test_alibaba_sandbox.py` asserts
   it.
3. **A failed call is not a known state.** The single most expensive mistake
   available here is to return "paused" because we asked for a pause. A
   request that times out may have been applied server-side; a sandbox we
   believe is running may have hit its TTL. So every method returns a
   `SandboxObservation` built from what the API *reported*, and a transport
   failure carries `may_have_applied` and points the caller at `inspect()`.

**Region is not a preference.** Deep hibernation is enabled only in
`ap-southeast-1`. Measured 2026-08-11: `us-west-1` answers `pause()` with
`403 PauseSessionForbidden — "pauseSession is not enabled for this
function"`, while Singapore pauses and wakes fine. That 403 gets its own
exception class (`SandboxAllowlistError`) because it is an account-and-region
fact, permanent until Alibaba changes it, and a caller that retries it burns
its budget on a call that cannot ever succeed. See
`settings.fc_sandbox_hibernation_region`, which is the one allowlist.

**What survives hibernation, stated precisely.** Re-measured 2026-08-11,
n=5, `ap-southeast-1`:

- The **filesystem** survives. A marker file's hash matched across the wake.
- **Processes survive, 5/5.** A background process is still there after the
  resume, and not merely resident: a running `flashnode work` kept serving
  lease claims straight across the hibernation, 13 → 19. It resumed
  *working*.
- The **guest clock is frozen** for the duration. `/proc/uptime` advanced
  ~2.7 s across 29.1 s of wall time, which is the mechanism behind the other
  two: the instance is suspended and restored, not torn down and rebuilt.

An earlier revision of this file claimed the opposite — that a background
process did not survive, "verified: PID gone after resume". **That was a
measurement artifact and it is retracted.** The probe was
`ps -p $PID >/dev/null && echo ALIVE || echo GONE`, and the template has no
`procps`: `ps` itself exited 127, so the probe took the GONE branch
unconditionally and never tested anything. The re-measurement uses
`[ -d /proc/$PID ]`. The lesson generalises past this one fact — see the
environment note on `run()` before writing any probe that runs in there.

**Pausing stops the TTL clock.** A sandbox created with a 90 s lifetime,
paused, and reconnected 64 s *past* its own expiry came back intact, and the
`end_at` read while paused was byte-identical to the one read at create.
Note that this contradicts Alibaba doc 3028695 and the comment on
`settings.fc_sandbox_timeout_ms`, which both say the TTL accrues from the
original creation time regardless. The measurement is ours and it is
specific; the doc may describe a different product generation. Sizing the
TTL to cover the whole span anyway — what that settings comment tells you to
do — stays correct either way, so nothing needs to change on the strength of
this. Do not build a design that *requires* the clock to stop until somebody
has re-confirmed it.

**There is no resume call.** `Sandbox.connect(sandbox_id)` auto-resumes a
paused sandbox, which is why the Protocol has no `resume()` — adding one
would be inventing an operation the platform does not have. It also means
`run()` and `write_file()` silently wake a paused sandbox, because they need
a connection to do anything. That is a ~1085 ms cost and a return to billed
running time; it is called out on those methods.

**Measured latencies**, 2026-08-11, `ap-southeast-1`, n=5, p50 / p95:
create 901 / 1851 ms · pause 2635 / 3075 ms · wake 1085 / 1598 ms. Size any
timeout off the p95 column; the p50s are what a caller feels.

Nothing here persists an SDK object. `SandboxInfo` and friends carry fields
we have no story for and, on the connect path, envd access tokens — so they
are converted to the small frozen values below at the boundary and the SDK
object is dropped.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import shlex
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .settings import Settings

log = logging.getLogger(__name__)

T = TypeVar("T")

#: The only region where `pause()` works, as of 2026-08-11. Mirrors
#: `settings.fc_sandbox_hibernation_region`; duplicated as a constant here so
#: a caller holding a gateway and no Settings can still ask.
HIBERNATION_REGIONS = ("ap-southeast-1",)

#: What one control-plane call is allowed to take before we stop waiting.
#: Well above the measured worst case (pause, p95 3075 ms) and well below any
#: request timeout a route sits behind. The point is not to be tight — it is
#: that a hung SDK call must not hold a thread forever.
DEFAULT_OP_TIMEOUT_S = 30.0

#: The ceiling on a single `run()`. Distinct from the above because a command
#: is user work, not a control-plane call, and the two have nothing to do
#: with each other.
DEFAULT_COMMAND_TIMEOUT_S = 300

#: stdout/stderr captured per command. The sandbox runs submitted code, and
#: this evidence lands in the event ledger — an unbounded capture is a way to
#: put a gigabyte of someone's training log into Postgres via a `print` in a
#: loop. Truncation is marked, never silent.
MAX_OUTPUT_CHARS = 64 * 1024

#: The default file mode for `write_file`. Restrictive on purpose: files we
#: place are configuration and inputs for one process, not shared state.
DEFAULT_FILE_MODE = 0o600

#: How many pages `list()` will drain before giving up. A cursor that never
#: clears `x-next-token` would otherwise spin forever inside one worker
#: thread — and the population this call exists to search is one managed
#: sandbox pool, so needing more than this many pages means something is
#: wrong, not that the ceiling is too low.
MAX_LIST_PAGES = 20


class SandboxState(str, Enum):
    """What the API said about a sandbox, at one instant.

    A `str` subclass so it serialises to a plain value in an event payload
    and compares equal to one in a test.

    The platform itself only distinguishes RUNNING from PAUSED. The other two
    are ours and both mean "do not act on this as if it were live":

    - TERMINATED — the API answered 404, or told us the delete succeeded.
      Absence is a real observation, not a failure to observe.
    - UNKNOWN — the operation happened but the state read after it did not.
      Emitted rather than guessed. A caller seeing UNKNOWN still has the
      `sandbox_id`, which is the thing that must never be lost: an id we
      dropped is a sandbox that bills until its TTL expires.
    """

    RUNNING = "running"
    PAUSED = "paused"
    TERMINATED = "terminated"
    UNKNOWN = "unknown"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


@dataclass(frozen=True)
class SandboxObservation:
    """One sighting of a sandbox. Not a claim about what it will do next.

    `state` always comes from a response the API gave us — a `get_info`, or a
    404, or a delete it confirmed. It is never inferred from the fact that we
    asked for something, which is the whole reason this type exists instead
    of methods returning `None`.

    `latency_ms` is the wall time of the operation the caller asked for,
    measured with `time.monotonic()`. Not the state read that followed it:
    when a caller compares a pause against the measured baseline (p50 2635
    ms), the number they need is the pause's, and folding a bookkeeping round
    trip into it would quietly inflate every reading.

    `expires_at` is the sandbox's own deadline, straight from the API's
    `end_at`. Measured 2026-08-11, a pause **stops** this clock: a 90 s
    sandbox reconnected 64 s past its nominal expiry was intact, and `end_at`
    read while paused was byte-identical to `end_at` at create. The module
    docstring records why that is worth carrying anyway and why it should not
    yet be designed around — Alibaba doc 3028695 says the opposite.
    """

    sandbox_id: str
    state: SandboxState
    observed_at: datetime
    latency_ms: int
    template: str | None = None
    expires_at: datetime | None = None

    @property
    def is_live(self) -> bool:
        """Running or paused — i.e. something that still costs money and can
        still be killed. Deliberately not "== RUNNING": a cleanup path that
        skips paused sandboxes leaks them."""
        return self.state in (SandboxState.RUNNING, SandboxState.PAUSED)


@dataclass(frozen=True)
class CommandEvidence:
    """What a command did, in a form that can be stored and argued with.

    `exit_code` is `None` for a background command and only for that — the
    process was started, nothing has exited, and reporting 0 would be a lie
    that reads as success. `ok` is `False` in that case for the same reason.

    A **non-zero exit is not an error here.** The SDK raises
    `CommandExitException` for one; this module catches it and returns
    evidence, because "the command ran and failed" and "we could not reach
    the sandbox" are different facts and a caller that cannot tell them apart
    will retry a deterministic failure forever.
    """

    sandbox_id: str
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    observed_at: datetime
    background_pid: int | None = None
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SandboxError(Exception):
    """Base class. Every message that reaches here has been through
    `redact()` — see `_classify`. Nothing raised from this module should ever
    need a caller to think about whether it is safe to log."""

    def __init__(self, message: str, *, operation: str = "", sandbox_id: str = ""):
        super().__init__(message)
        self.operation = operation
        self.sandbox_id = sandbox_id


class SandboxUnavailable(SandboxError):
    """The sandbox is not configured on this deployment.

    Raised at construction rather than returning a gateway that fails on
    first use, and raised rather than returning None, for `OSSUnavailable`'s
    reason: "no sandbox" and "a sandbox that reported nothing" must not be
    the same value. Every existing pool keeps working without one — this is
    an absent capability, not a broken one.
    """


class SandboxAllowlistError(SandboxError):
    """`403 PauseSessionForbidden` — this account cannot pause in this region.

    Its own class because it is the one failure that is neither transient nor
    the caller's fault nor fixable by retrying: `us-west-1` returns
    "pauseSession is not enabled for this function" every time, forever, and
    only `ap-southeast-1` is enabled (measured 2026-08-11). A caller that
    treats this as a generic failure will retry a call that cannot succeed
    and, worse, will report "hibernation failed" when the truth is
    "hibernation was never available here" — which is a deployment decision
    somebody has to make, not an incident to page on.

    The sandbox is still RUNNING when this is raised. Nothing was consumed
    except a round trip.
    """


class SandboxTransportError(SandboxError):
    """We could not complete the call. Retryable — with a caveat.

    `may_have_applied` is the caveat and it is the important field. A request
    that timed out, or whose connection died after the bytes went out, may
    have been applied server-side: the pause may have happened, the sandbox
    may have been created, and our timeout tells us nothing either way.
    `asyncio.wait_for` makes this concrete — it abandons the *await*, not the
    thread, so the SDK call keeps running in the pool after we have given up
    on it.

    When this is True the correct response is `inspect()`, not a blind retry.
    Retrying a create that actually succeeded leaks a sandbox that bills for
    an hour; retrying a kill is harmless. The caller knows which it did.
    """

    def __init__(
        self,
        message: str,
        *,
        operation: str = "",
        sandbox_id: str = "",
        may_have_applied: bool = False,
    ):
        super().__init__(message, operation=operation, sandbox_id=sandbox_id)
        self.may_have_applied = may_have_applied


class SandboxTerminalError(SandboxError):
    """The call failed in a way that will fail again. Do not retry.

    Bad credentials, a template that does not exist, a malformed request, a
    sandbox that will not wake. Distinguished from `SandboxTransportError`
    because a retry loop around this one is a way to turn a configuration
    mistake into a rate-limit ban.
    """


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

#: Shapes that are credentials wherever they appear. Belt and braces around
#: the explicit secret list: an SDK message can echo a header we never held a
#: copy of, and an error body can carry the key of a *different* account than
#: the one this gateway was built with.
_SECRET_SHAPES: tuple[re.Pattern[str], ...] = (
    # E2B-issued keys.
    re.compile(r"e2b_[A-Za-z0-9]{8,}"),
    # Alibaba RAM AccessKey ids and the STS variety.
    re.compile(r"\bLTAI[A-Za-z0-9]{8,}\b"),
    re.compile(r"\bSTS\.[A-Za-z0-9]{8,}\b"),
)

#: `X-API-KEY: abc`, `api_key='abc'`, `authorization: Bearer abc`. The value
#: is whatever follows the label, minus an auth *scheme* word if one is in
#: the way — replacing "Bearer" and leaving the token behind is the mistake
#: this group exists to avoid, and it is the shape `httpx` prints.
_LABELLED_SECRET_RE = re.compile(
    r"(?i)\b(x-api-key|api[-_]?key|access[-_]?key[-_]?(?:id|secret)?|"
    r"authorization|bearer|token|secret|password)\b"
    r"(\s*[:=]\s*|\s+)"
    r"((?:bearer|basic|token)\s+)?"
    r"['\"]?([^\s'\",;}\)\]]{4,})"
)

REDACTED = "[redacted]"


def redact(text: object, *secrets: str) -> str:
    """Return `text` as a string with every known credential removed.

    Used on every exception path in this module, including the ones that
    "cannot" contain a key — the 401 body that echoes the header it rejected
    is exactly the message somebody pastes into an issue.

    Redaction of the literal secret happens **first and unconditionally**,
    longest first so a key that contains another configured value cannot be
    partially replaced into a still-guessable fragment. A one-character key
    would mangle the message into uselessness; that is the correct direction
    to round, and a one-character key is a configuration error anyway.
    """
    out = str(text)
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        out = out.replace(secret, REDACTED)
    for pattern in _SECRET_SHAPES:
        out = pattern.sub(REDACTED, out)
    return _LABELLED_SECRET_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3) or ''}{REDACTED}", out
    )


# ---------------------------------------------------------------------------
# The Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class SandboxGateway(Protocol):
    """The eight things the control plane does to a sandbox.

    `runtime_checkable` so a test can assert the fake has not drifted from
    the interface. It only checks that the methods exist, which is exactly
    the drift worth catching — a fake missing a method that production calls
    is a suite that stays green while the route 500s.

    Deliberately not a superset of the SDK. No `resume` (connect auto-resumes
    — see the module docstring), no snapshots, no metrics, no network
    reconfiguration. Each of those is a capability we would then have to keep
    working in the fake and reason about in a security review, bought for a
    caller that does not exist.

    `list` is the eighth and it earned its place by a caller that *did*
    exist: without it, a `create` whose response was lost left a live,
    billing sandbox whose id nobody ever learned, and the orchestrator could
    do nothing but record `create.unreconcilable` and log it. See its
    docstring for the recovery it makes possible.

    Every method returns an observation or evidence and raises one of the
    four `SandboxError` subclasses. None returns `None` on failure.
    """

    async def create(
        self, *, template: str, timeout_ms: int, metadata: dict[str, str]
    ) -> SandboxObservation:
        ...

    async def connect(self, sandbox_id: str) -> SandboxObservation:
        ...

    async def run(
        self,
        sandbox_id: str,
        command: str,
        *,
        timeout_s: int,
        background: bool = False,
    ) -> CommandEvidence:
        ...

    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        data: bytes,
        *,
        mode: int = DEFAULT_FILE_MODE,
    ) -> None:
        ...

    async def pause(self, sandbox_id: str) -> SandboxObservation:
        ...

    async def inspect(self, sandbox_id: str) -> SandboxObservation:
        ...

    async def list(
        self, *, metadata: Mapping[str, str] | None = None
    ) -> list[SandboxObservation]:
        ...

    async def kill(self, sandbox_id: str) -> SandboxObservation:
        ...


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _elapsed_ms(started: float) -> int:
    """Monotonic, always. Wall clock goes backwards over an NTP correction
    and this number ends up in a latency budget somebody makes a decision
    with — a negative pause duration is worse than no measurement."""
    return max(0, int((time.monotonic() - started) * 1000))


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text, False
    kept = text[:MAX_OUTPUT_CHARS]
    return kept + f"\n... [truncated at {MAX_OUTPUT_CHARS} chars]", True


#: Matches the status at the start of the message, or after the `operation: `
#: prefix `_classify` adds — so the same helper works on a raw SDK message and
#: on one of ours that has already been wrapped.
_STATUS_RE = re.compile(r"(?:\A|:\s)(\d{3})\s*:")


def _status_from_message(text: str) -> int | None:
    """Recover the HTTP status the SDK folded into a message string.

    `e2b.api.handle_api_exception` formats every non-401/429 failure as
    `f"{status}: {body}"` and raises a bare `SandboxException`, so the status
    code is not available as an attribute anywhere. Parsing the prefix is the
    only way to tell a retryable 503 from a permanent 400, and it is the only
    way to recognise the pause allowlist 403.

    This is a real coupling to an SDK message format and it is the most
    likely thing in this file to break on an `e2b` upgrade. It fails in the
    safe direction — an unparsed status classifies as terminal, so a caller
    stops rather than hammering.
    """
    match = _STATUS_RE.search(text)
    return int(match.group(1)) if match else None


def _looks_like_pause_allowlist(text: str) -> bool:
    lowered = text.lower()
    return "pausesession" in lowered or (
        "pause" in lowered and "not enabled" in lowered
    )


# ---------------------------------------------------------------------------
# The real one
# ---------------------------------------------------------------------------


class E2BSandboxGateway:
    """`SandboxGateway` over `e2b-code-interpreter==2.8.1` / `e2b==2.31.0`.

    The SDK is imported lazily, exactly as `alibaba_oss` imports `oss2`: a
    deploy without the sandbox configured must not need the dependency
    installed, and the API's own test suite must import this module without
    it. `sandbox_cls` exists so a test can inject a stub without reaching for
    `unittest.mock.patch` on an import that has not happened yet.

    Not thread-safe and not meant to be shared across event loops. One
    instance per process is the intended shape; the handle cache below is why.
    """

    def __init__(
        self,
        *,
        api_key: str,
        api_url: str,
        domain: str,
        region: str = HIBERNATION_REGIONS[0],
        default_template: str = "code-interpreter-v1",
        default_timeout_ms: int = 3_600_000,
        op_timeout_s: float = DEFAULT_OP_TIMEOUT_S,
        sandbox_cls: Any = None,
        query_cls: Any = None,
    ) -> None:
        if not (api_key and api_url and domain):
            raise SandboxUnavailable("the FC Agent Sandbox is not fully configured")
        self._api_key = api_key
        self._api_url = api_url
        self._domain = domain
        self.region = region
        self.default_template = default_template
        self.default_timeout_ms = default_timeout_ms
        self._op_timeout_s = op_timeout_s
        self._sandbox_cls = sandbox_cls
        self._query_cls = query_cls
        #: sandbox_id -> a connected SDK handle.
        #:
        #: `run` and `write_file` need a connection, and establishing one is a
        #: measured 1085 ms (p50). Without this cache a twenty-command
        #: evaluation pays nineteen extra wakes — and each `connect` re-sets the
        #: sandbox TTL, so the cache is not only a speed-up. Every failure
        #: drops the entry, because a handle that just failed is the one thing
        #: we know is not usable.
        self._handles: dict[str, Any] = {}

    @classmethod
    def from_settings(cls, settings: "Settings") -> "E2BSandboxGateway":
        if not settings.fc_sandbox_configured:
            raise SandboxUnavailable("the FC Agent Sandbox is not configured")
        return cls(
            api_key=settings.fc_sandbox_api_key,
            api_url=settings.fc_sandbox_api_url,
            domain=settings.fc_sandbox_domain,
            region=settings.fc_sandbox_region,
            default_template=settings.fc_sandbox_template,
            default_timeout_ms=settings.fc_sandbox_timeout_ms,
        )

    def __repr__(self) -> str:
        """Explicit, because the default would print `self.__dict__` and the
        first thing in it is the API key. `Settings` marks the field
        `repr=False` for the same reason; a gateway that undoes that at the
        first `f"{gateway}"` in a log line would make that pointless."""
        return (
            f"<E2BSandboxGateway region={self.region!r} "
            f"api_url={self._api_url!r} template={self.default_template!r}>"
        )

    __str__ = __repr__

    @property
    def can_hibernate(self) -> bool:
        """Whether `pause()` can work at all here.

        Check this before offering hibernation in a UI. It is not a
        substitute for handling `SandboxAllowlistError` — the allowlist lives
        on Alibaba's side and this is our copy of it — but it turns a wasted
        round trip into a decision made before anything was created.
        """
        return self.region in HIBERNATION_REGIONS

    # -- SDK plumbing -------------------------------------------------------

    def _cls(self) -> Any:
        if self._sandbox_cls is None:
            try:
                from e2b_code_interpreter import Sandbox
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise SandboxUnavailable(
                    "e2b-code-interpreter is not installed"
                ) from exc
            self._sandbox_cls = Sandbox
        return self._sandbox_cls

    def _query(self) -> Any:
        """`SandboxQuery`, lazily, on the same seam as `_cls`.

        A separate injection point rather than a duck-typed stand-in of our
        own: the paginator only reads `.metadata` and `.state` off this
        object today, so a local shim would work right up until the SDK type
        checks it. `SandboxQuery` is exported from `e2b_code_interpreter`,
        so using the real one costs nothing.
        """
        if self._query_cls is None:
            try:
                from e2b_code_interpreter import SandboxQuery
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise SandboxUnavailable(
                    "e2b-code-interpreter is not installed"
                ) from exc
            self._query_cls = SandboxQuery
        return self._query_cls

    def _params(self) -> dict[str, Any]:
        """The connection kwargs every SDK entry point takes.

        Rebuilt per call rather than stored as an attribute so the key lives
        in exactly one field, which is the field `__repr__` knows to omit.

        `validate_api_key=False` is required, not tidiness. The SDK asserts
        keys match `e2b_` + hex (`e2b.api.validate_api_key`) and raises
        `AuthenticationException` locally before any request — but Alibaba
        issues the key for its own FC deployment and it does not carry that
        prefix. Leaving the default on means every call fails at the client
        with a message telling you to visit the e2b.dev dashboard, which is
        not where this key came from. The SDK documents this switch for
        precisely this case.
        """
        return {
            "api_key": self._api_key,
            "api_url": self._api_url,
            "domain": self._domain,
            "validate_api_key": False,
        }

    def _classify(
        self, exc: BaseException, *, operation: str, sandbox_id: str, mutating: bool
    ) -> SandboxError:
        """Map an SDK exception onto our four. Redacts on the way through.

        `mutating` decides `may_have_applied` for the ambiguous failures. A
        failed `inspect` changed nothing by definition, so saying it might
        have would send callers reconciling against a call that cannot have
        had an effect.
        """
        message = redact(exc, self._api_key)
        name = type(exc).__name__

        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return SandboxTransportError(
                f"{operation} timed out after {self._op_timeout_s:.0f}s",
                operation=operation,
                sandbox_id=sandbox_id,
                may_have_applied=mutating,
            )

        # The SDK's own timeout, raised for both sandbox and request timeouts.
        if name == "TimeoutException":
            return SandboxTransportError(
                f"{operation}: {message}",
                operation=operation,
                sandbox_id=sandbox_id,
                may_have_applied=mutating,
            )

        if name == "AuthenticationException":
            # Never retried, and never surfaced with the body attached: a 401
            # body echoes the header that was rejected.
            log.error("FC sandbox refused this deployment's credentials on %s", operation)
            return SandboxTerminalError(
                "the FC Agent Sandbox refused this deployment's credentials",
                operation=operation,
                sandbox_id=sandbox_id,
            )

        if name == "RateLimitException":
            return SandboxTransportError(
                f"{operation}: rate limited",
                operation=operation,
                sandbox_id=sandbox_id,
                may_have_applied=False,
            )

        status = _status_from_message(message)

        if status == 403 and _looks_like_pause_allowlist(message):
            return SandboxAllowlistError(
                f"{operation} is not enabled for this account in "
                f"{self.region!r} — deep hibernation is available only in "
                f"{', '.join(HIBERNATION_REGIONS)}",
                operation=operation,
                sandbox_id=sandbox_id,
            )

        if status is not None and status >= 500:
            return SandboxTransportError(
                f"{operation}: {message}",
                operation=operation,
                sandbox_id=sandbox_id,
                may_have_applied=mutating,
            )

        if status is not None:
            return SandboxTerminalError(
                f"{operation}: {message}",
                operation=operation,
                sandbox_id=sandbox_id,
            )

        # No status to read: a connection reset, a DNS failure, a proxy
        # closing the socket. Retryable, and ambiguous for a mutating call.
        if isinstance(exc, (OSError, ConnectionError)):
            return SandboxTransportError(
                f"{operation}: {name}: {message}",
                operation=operation,
                sandbox_id=sandbox_id,
                may_have_applied=mutating,
            )

        # Unrecognised. Terminal, so a retry loop stops rather than
        # hammering something we do not understand.
        return SandboxTerminalError(
            f"{operation}: {name}: {message}",
            operation=operation,
            sandbox_id=sandbox_id,
        )

    async def _call(
        self,
        operation: str,
        fn: Callable[[], T],
        *,
        sandbox_id: str = "",
        mutating: bool,
        timeout_s: float | None = None,
    ) -> tuple[T, int]:
        """Run one blocking SDK call off the loop, with a ceiling.

        Returns the result and the monotonic milliseconds it took, so callers
        never re-measure and never disagree about what was being timed.

        The ceiling is `asyncio.wait_for`, which cancels the await and not the
        thread — the SDK call keeps going in the default executor after we
        stop waiting. That is a leaked thread until the socket gives up, and
        it is the honest reason `may_have_applied` is True on a timeout for
        anything that changes state.
        """
        limit = self._op_timeout_s if timeout_s is None else timeout_s
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(asyncio.to_thread(fn), timeout=limit)
        except BaseException as exc:  # noqa: BLE001 - classified, then re-raised
            self._handles.pop(sandbox_id, None)
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
                raise
            if isinstance(exc, SandboxError):
                # Already classified inside the callable — `list` raises its
                # own page-ceiling error. Re-wrapping would demote a
                # deliberate `SandboxTransportError` to terminal, since
                # `_classify` has no status to read off it.
                raise
            raise self._classify(
                exc, operation=operation, sandbox_id=sandbox_id, mutating=mutating
            ) from None
        return result, _elapsed_ms(started)

    def _observation(
        self, info: Any, *, latency_ms: int, fallback_id: str = ""
    ) -> SandboxObservation:
        """Convert one `SandboxInfo` into our value and drop the SDK object.

        `SandboxInfo` also carries `metadata`, `network`, `volume_mounts` and
        an `envd_version`; the connect response next door carries envd and
        traffic access tokens. None of it is persisted here — hard rule 3 is
        about durable state we can reason about, and an SDK struct that gains
        a credential field in a minor release is not that.
        """
        raw_state = getattr(info, "state", None)
        value = getattr(raw_state, "value", raw_state)
        try:
            state = SandboxState(str(value))
        except ValueError:
            # A state this SDK version knows and we do not. UNKNOWN is the
            # honest answer and keeps the id, which is what cleanup needs.
            log.warning("FC sandbox reported an unrecognised state %r", value)
            state = SandboxState.UNKNOWN
        return SandboxObservation(
            sandbox_id=str(getattr(info, "sandbox_id", "") or fallback_id),
            state=state,
            observed_at=_now(),
            latency_ms=latency_ms,
            template=getattr(info, "template_id", None),
            expires_at=getattr(info, "end_at", None),
        )

    async def _observe_after(
        self, sandbox_id: str, *, latency_ms: int, operation: str
    ) -> SandboxObservation:
        """Read the state that followed a mutation, attributing the
        mutation's own latency to it.

        Falls back to UNKNOWN instead of raising, on purpose. The mutation
        already happened; failing here would throw away a `sandbox_id` the
        caller now owns and has no other copy of, and a leaked sandbox bills
        until its TTL runs out. The state is unknown — say so, keep the id.
        """
        try:
            observed = await self.inspect(sandbox_id)
        except SandboxError as exc:
            log.warning(
                "FC sandbox %s: %s succeeded but its state could not be read (%s)",
                sandbox_id,
                operation,
                type(exc).__name__,
            )
            return SandboxObservation(
                sandbox_id=sandbox_id,
                state=SandboxState.UNKNOWN,
                observed_at=_now(),
                latency_ms=latency_ms,
            )
        return SandboxObservation(
            sandbox_id=observed.sandbox_id or sandbox_id,
            state=observed.state,
            observed_at=observed.observed_at,
            latency_ms=latency_ms,
            template=observed.template,
            expires_at=observed.expires_at,
        )

    # -- lifecycle ----------------------------------------------------------

    async def create(
        self, *, template: str, timeout_ms: int, metadata: dict[str, str]
    ) -> SandboxObservation:
        """Create a sandbox and report the state the API gives back.

        Measured 2026-08-11 in `ap-southeast-1`, n=5: p50 901 ms, p95 1851 ms.

        `timeout_ms` is converted to whole seconds because that is the SDK's
        unit (`Sandbox.create(timeout=...)`, seconds, defaulting to 300 when
        omitted). The settings field is milliseconds; getting this wrong by
        1000x means a one-hour sandbox that dies in 3.6 seconds. Rounded up,
        never down — a truncating conversion can only ever shorten a lifetime
        somebody chose.

        The state is read back rather than assumed. `create` returns a
        handle, not a state, and "it returned so it is running" is exactly
        the inference this module exists to refuse: a sandbox can be created
        and be dead by the time anyone looks. If that read fails the
        observation is UNKNOWN and still carries the id, so the caller can
        kill what it just made.
        """
        sandbox_cls = self._cls()
        timeout_s = max(1, math.ceil(timeout_ms / 1000))

        def _create() -> Any:
            return sandbox_cls.create(
                template=template,
                timeout=timeout_s,
                metadata=dict(metadata),
                **self._params(),
            )

        handle, latency_ms = await self._call("create", _create, mutating=True)
        sandbox_id = str(getattr(handle, "sandbox_id", "") or "")
        if sandbox_id:
            self._handles[sandbox_id] = handle
        return await self._observe_after(
            sandbox_id, latency_ms=latency_ms, operation="create"
        )

    async def connect(self, sandbox_id: str) -> SandboxObservation:
        """Attach to an existing sandbox, **waking it if it is paused**.

        This is the resume path. There is no separate resume call — measured
        2026-08-11, n=5, a wake through `connect` takes p50 1085 ms, p95 1598
        ms — and the returned observation is how a caller learns whether the
        wake worked, rather than assuming it did because no exception came
        back.

        The TTL is re-sent explicitly. `Sandbox.connect` puts whatever
        `timeout` it is given into the request body and defaults it to the
        SDK's `default_sandbox_timeout` of **300 seconds** when omitted, so
        connecting without one silently cuts a one-hour sandbox to five
        minutes. That is confirmed on the wire, not just read off the source:
        a bare `connect()` set `end_at` to exactly `now + 300.0`, while
        `connect(timeout=1800)` set `now + 1800`. Passing the configured
        lifetime is not an optimisation.

        Everything comes back with it: the filesystem, and the running
        processes — including one that carried on serving work across the
        pause. See the module docstring.
        """
        sandbox_cls = self._cls()
        timeout_s = max(1, math.ceil(self.default_timeout_ms / 1000))

        def _connect() -> Any:
            return sandbox_cls.connect(sandbox_id, timeout=timeout_s, **self._params())

        handle, latency_ms = await self._call(
            "connect", _connect, sandbox_id=sandbox_id, mutating=True
        )
        self._handles[sandbox_id] = handle
        return await self._observe_after(
            sandbox_id, latency_ms=latency_ms, operation="connect"
        )

    async def pause(self, sandbox_id: str) -> SandboxObservation:
        """Hibernate. Only works in `ap-southeast-1`.

        Measured 2026-08-11, n=5: p50 2635 ms, p95 3075 ms in Singapore;
        `403 PauseSessionForbidden — "pauseSession is not enabled for this
        function"` in `us-west-1`, which arrives as `SandboxAllowlistError`.

        The SDK returns `False` for a 409 (already paused) rather than
        raising, which is correct and is not treated as failure — but it is
        also not what the returned state is built from. The state comes from
        the read that follows, because "the pause call returned" and "the
        sandbox is paused" are different statements and only the second one
        is worth storing.

        The clock stops for the duration — a 90 s sandbox survived being
        reconnected 64 s past its nominal expiry, and `end_at` did not move
        while paused. Alibaba doc 3028695 and the `settings` comment say the
        opposite; the module docstring holds that argument. Size the TTL to
        cover the whole span regardless, which is correct under either
        reading.

        `keep_memory` is left at the SDK's default of True and not exposed.
        A single trial of `keep_memory=False` behaved identically, which
        *suggests* FC always takes a memory snapshot — but that is n=1 per
        variant and it is not a claim, so the flag stays unused rather than
        being plumbed through on the strength of it.
        """
        sandbox_cls = self._cls()

        def _pause() -> Any:
            return sandbox_cls.pause(sandbox_id, **self._params())

        _, latency_ms = await self._call(
            "pause", _pause, sandbox_id=sandbox_id, mutating=True
        )
        # A paused sandbox cannot serve the cached connection.
        self._handles.pop(sandbox_id, None)
        return await self._observe_after(
            sandbox_id, latency_ms=latency_ms, operation="pause"
        )

    async def inspect(self, sandbox_id: str) -> SandboxObservation:
        """What the API says about this sandbox right now.

        The reconciliation path. After any `SandboxTransportError` with
        `may_have_applied` set, this is the call that turns "we do not know"
        back into a fact.

        A 404 returns TERMINATED rather than raising. Absence is an
        observation: the sandbox is not there, which is precisely what a
        cleanup path or a post-timeout reconcile needs to hear. Raising would
        force every caller to catch an exception to learn a normal outcome.
        """
        sandbox_cls = self._cls()

        def _info() -> Any:
            return sandbox_cls.get_info(sandbox_id, **self._params())

        try:
            info, latency_ms = await self._call(
                "inspect", _info, sandbox_id=sandbox_id, mutating=False
            )
        except SandboxError as exc:
            if _is_not_found(exc):
                return SandboxObservation(
                    sandbox_id=sandbox_id,
                    state=SandboxState.TERMINATED,
                    observed_at=_now(),
                    latency_ms=0,
                )
            raise
        return self._observation(info, latency_ms=latency_ms, fallback_id=sandbox_id)

    async def list(
        self, *, metadata: Mapping[str, str] | None = None
    ) -> list[SandboxObservation]:
        """Every sandbox this key can see, optionally filtered by metadata.

        **This is how a lost `create` is recovered, and that is why it
        exists.** If `create` times out or its response is lost, the sandbox
        may well be alive and billing while its id was never learned — the
        one failure in this module with an ongoing cost. `inspect` cannot
        help: it needs the id that went missing. So a caller stamps something
        it already knows into the create metadata — `flashml_session_id` is
        the agreed key — and finds the sandbox again by listing on it:

            observed = await gateway.list(
                metadata={"flashml_session_id": session_id}
            )
            for orphan in observed:
                await gateway.kill(orphan.sandbox_id)

        Do that rather than guessing at ids or writing off the sandbox. A
        caller that stamps nothing has no recovery available, which is the
        argument for stamping every create.

        **Paused sandboxes are included.** With no state filter the API
        returns `running` and `paused` both, and that is precisely the
        property that makes this usable for orphan-hunting: a hibernated
        sandbox is the easiest kind to forget and it still holds its
        resources. The live-validation agent asserts zero orphans after each
        run on exactly this basis.

        **Filtering is server-side.** The metadata pairs go on the request
        (`GET /v2/sandboxes?metadata=...`) rather than being filtered out of
        a full listing here, so a busy account does not have to be paged
        through to find one sandbox. All pairs must match.

        **The key is region-scoped.** It is authenticated against the region
        in `api_url`/`domain` and 401s elsewhere — measured against
        `us-west-1`. So this cannot orphan-hunt across regions: a sandbox
        created under a region this deployment no longer configures is
        invisible here and has to be found from that region's credentials.
        Changing `FC_SANDBOX_REGION` therefore orphans anything still live in
        the old one, which is a migration step, not an afterthought.

        Every observation carries the latency of the whole listing call, not
        a per-sandbox figure — there is one round trip behind all of them.
        """
        sandbox_cls = self._cls()
        query = self._query()(metadata=dict(metadata)) if metadata else None

        def _drain() -> Any:
            paginator = sandbox_cls.list(query=query, **self._params())
            found = []
            pages = 0
            while paginator.has_next:
                if pages >= MAX_LIST_PAGES:
                    # Pages still pending past the ceiling. Truncated results
                    # are reported, never silently returned as complete: a
                    # reconciler that believes it has seen everything will
                    # conclude there are no orphans and stop looking.
                    raise SandboxTransportError(
                        f"listing did not terminate within {MAX_LIST_PAGES} "
                        f"pages",
                        operation="list",
                        may_have_applied=False,
                    )
                found.extend(paginator.next_items())
                pages += 1
            return found

        infos, latency_ms = await self._call("list", _drain, mutating=False)
        return [
            self._observation(info, latency_ms=latency_ms) for info in infos
        ]

    async def kill(self, sandbox_id: str) -> SandboxObservation:
        """Destroy the sandbox. Killing a dead one is a no-op, not an error.

        Cleanup paths depend on this and there is no acceptable alternative:
        a `finally:` block that raises because the sandbox already went away
        will mask whatever the `try:` was actually failing on, and the
        commonest reason a sandbox is gone is that it hit its TTL — which is
        not a fault anybody needs to hear about at cleanup time.

        This is the one method whose return value *is* the observation
        without a second read. The SDK's `kill` returns True when the API
        confirmed the delete and False on a 404; both are the API telling us
        the sandbox no longer exists, so both are TERMINATED, and asking
        again would only add a round trip to reach the same answer.
        """
        sandbox_cls = self._cls()

        def _kill() -> Any:
            return sandbox_cls.kill(sandbox_id, **self._params())

        try:
            _, latency_ms = await self._call(
                "kill", _kill, sandbox_id=sandbox_id, mutating=True
            )
        except SandboxError as exc:
            if _is_not_found(exc):
                return SandboxObservation(
                    sandbox_id=sandbox_id,
                    state=SandboxState.TERMINATED,
                    observed_at=_now(),
                    latency_ms=0,
                )
            raise
        finally:
            self._handles.pop(sandbox_id, None)
        return SandboxObservation(
            sandbox_id=sandbox_id,
            state=SandboxState.TERMINATED,
            observed_at=_now(),
            latency_ms=latency_ms,
        )

    # -- work ---------------------------------------------------------------

    async def _handle(self, sandbox_id: str) -> Any:
        """A connected handle, cached. Connecting wakes a paused sandbox."""
        handle = self._handles.get(sandbox_id)
        if handle is not None:
            return handle
        await self.connect(sandbox_id)
        handle = self._handles.get(sandbox_id)
        if handle is None:  # pragma: no cover - connect populates or raises
            raise SandboxTerminalError(
                "could not obtain a connection", operation="connect",
                sandbox_id=sandbox_id,
            )
        return handle

    async def run(
        self,
        sandbox_id: str,
        command: str,
        *,
        timeout_s: int = DEFAULT_COMMAND_TIMEOUT_S,
        background: bool = False,
    ) -> CommandEvidence:
        """Run one shell command and return evidence of what it did.

        **A non-zero exit is a result, not an exception.** The SDK raises
        `CommandExitException` — which subclasses both `SandboxException` and
        `CommandResult`, so it carries the output — and this catches it and
        returns `CommandEvidence` with the real exit code. A caller can then
        distinguish "the evaluation script failed" from "the sandbox is
        unreachable", which are the two things a retry loop must never
        confuse.

        `background=True` returns as soon as the process is started, with
        `exit_code=None` and the PID. **That process survives a
        hibernation** — re-measured 2026-08-11, n=5, alive 5/5 after the
        wake, and a `flashnode work` running that way kept serving lease
        claims straight through the pause (13 → 19). No checkpoint-and-
        restart dance is needed around a pause; the guest is suspended and
        restored with its processes intact.

        (An earlier revision of this docstring said the reverse. It was wrong
        for the reason in the environment note below, and the module
        docstring records the retraction.)

        **The sandbox environment, because it decides what you can run.**
        Debian 13, Python 3.13.13, 2 vCPU, 2 GB RAM, 10 GB disk, non-root
        user `user` with sudo, no Docker, and **no `ps`** — the template
        ships no `procps`. That last one is not trivia: a liveness probe
        written as `ps -p $PID && echo ALIVE || echo GONE` does not fail
        loudly, it exits 127 on `ps` and takes the GONE branch every time,
        which is precisely how this module came to assert the opposite of the
        truth for a day. Probe with `[ -d /proc/$PID ]`, and check exit codes
        on anything you assume is installed.

        Calling this on a paused sandbox wakes it (~1085 ms) as a side effect
        of needing a connection, and puts it back on billed running time.
        """
        handle = await self._handle(sandbox_id)

        def _run() -> Any:
            return handle.commands.run(
                command,
                background=background,
                timeout=float(timeout_s),
                request_timeout=float(timeout_s),
            )

        # The command's own ceiling, not the control-plane one: user work
        # legitimately takes minutes and has nothing to do with how long a
        # create is allowed to take. A margin over the SDK's own timeout so
        # the SDK reports the timeout first, with its context, instead of us
        # abandoning the thread underneath it.
        result, duration_ms = await self._call(
            "run",
            _exit_code_is_not_an_error(_run),
            sandbox_id=sandbox_id,
            mutating=True,
            timeout_s=timeout_s + self._op_timeout_s,
        )

        if background:
            return CommandEvidence(
                sandbox_id=sandbox_id,
                command=command,
                exit_code=None,
                stdout="",
                stderr="",
                duration_ms=duration_ms,
                observed_at=_now(),
                background_pid=_as_int(getattr(result, "pid", None)),
            )

        return _evidence_from_result(
            sandbox_id, command, result, duration_ms, self._api_key
        )

    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        data: bytes,
        *,
        mode: int = DEFAULT_FILE_MODE,
    ) -> None:
        """Place bytes in the sandbox at `path`, then set the mode.

        **Two calls, because the SDK has no mode parameter.**
        `sandbox.files.write(path, data)` writes at the agent's umask and
        offers nothing else, so the mode is applied by a following `chmod`.
        The file therefore exists at the default mode for the width of one
        round trip. That window is why the artifact design hands the sandbox
        *presigned URLs* rather than credentials in a file (`alibaba_oss`) —
        the mode here protects configuration and inputs from other processes
        in the sandbox, and is not load-bearing for anything that would be a
        disaster to expose for 50 ms.

        The path is quoted for the chmod. It is ours today, but a command
        built by concatenation is where an injection arrives the day
        something else starts supplying the value — the same argument as
        `github_app.install_url`.
        """
        if "\x00" in path or "\n" in path:
            raise SandboxTerminalError(
                "refusing a sandbox path containing a NUL or a newline",
                operation="write_file",
                sandbox_id=sandbox_id,
            )

        handle = await self._handle(sandbox_id)

        def _write() -> Any:
            return handle.files.write(path, data)

        await self._call("write_file", _write, sandbox_id=sandbox_id, mutating=True)

        chmod = f"chmod {mode:o} {shlex.quote(path)}"
        evidence = await self.run(sandbox_id, chmod, timeout_s=30)
        if not evidence.ok:
            raise SandboxTerminalError(
                f"wrote {path!r} but could not set mode {mode:o} "
                f"(exit {evidence.exit_code})",
                operation="write_file",
                sandbox_id=sandbox_id,
            )


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_not_found(exc: SandboxError) -> bool:
    """Whether a classified error was really a 404.

    `SandboxNotFoundException` arrives from the SDK for a deleted or
    never-existent sandbox and lands in `SandboxTerminalError` by way of
    `_classify`; `kill` and `inspect` both need to recognise it to keep their
    "absence is an observation" contract.
    """
    text = str(exc).lower()
    return "not found" in text or _status_from_message(str(exc)) == 404


def _evidence_from_result(
    sandbox_id: str,
    command: str,
    result: Any,
    duration_ms: int,
    api_key: str,
) -> CommandEvidence:
    stdout, truncated_out = _truncate(redact(getattr(result, "stdout", "") or "", api_key))
    stderr, truncated_err = _truncate(redact(getattr(result, "stderr", "") or "", api_key))
    return CommandEvidence(
        sandbox_id=sandbox_id,
        command=command,
        exit_code=_as_int(getattr(result, "exit_code", None)),
        stdout=stdout,
        stderr=stderr,
        duration_ms=duration_ms,
        observed_at=_now(),
        truncated=truncated_out or truncated_err,
    )


def _exit_code_is_not_an_error(fn: Callable[[], Any]) -> Callable[[], Any]:
    """Turn the SDK's `CommandExitException` back into a result.

    `commands.run` raises on any non-zero exit. That exception subclasses
    both `SandboxException` and `CommandResult`, so it already *is* the
    result — it carries `stdout`, `stderr`, `exit_code` and `error`. Letting
    it out of the worker thread would send a failed evaluation script through
    `_classify` and out as a `SandboxTerminalError`, which reads to a caller
    as "the sandbox broke" when the truth is "the command exited 1". Those
    two get retried very differently.

    Duck-typed on `exit_code` rather than importing the exception class,
    because importing it would drag the SDK into module import and undo the
    lazy import the rest of this file is built on.
    """

    def _wrapped() -> Any:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - re-raised unless it is an exit
            if getattr(exc, "exit_code", None) is not None:
                return exc
            raise

    return _wrapped


# ---------------------------------------------------------------------------
# The fake
# ---------------------------------------------------------------------------


@dataclass
class _Fault:
    """One injected failure, armed for a single call."""

    kind: str
    applied: bool = False
    message: str = ""


@dataclass
class _FakeSandbox:
    sandbox_id: str
    template: str
    state: SandboxState
    expires_at: datetime | None
    metadata: dict[str, str] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    modes: dict[str, int] = field(default_factory=dict)
    #: PIDs started with `background=True`. **Kept across a pause**, because
    #: that is what the platform does — re-measured 2026-08-11, alive 5/5
    #: after the wake. This list used to be cleared on pause, on the strength
    #: of a probe that turned out to be testing nothing (see `run`); a fake
    #: that clears it teaches every test written against it a fact that is
    #: false.
    background_pids: list[int] = field(default_factory=list)
    wake_fails: bool = False


class FakeSandboxGateway:
    """A `SandboxGateway` with no network. **Tests only.**

    Not a mock: it keeps real state, so a test can pause, wake, and assert
    that both the file it wrote and the background PID it started are still
    there. The behaviours it reproduces are the ones that were measured or
    that cost somebody an afternoon:

    - `region="us-west-1"` (anything outside `HIBERNATION_REGIONS`) makes
      `pause()` raise `SandboxAllowlistError`, exactly as the account does.
    - `fail_next(op, applied=True)` raises `SandboxTransportError` **after**
      performing the mutation — the transport failure whose call actually
      succeeded server-side. That is the case a caller can only get right by
      reconciling with `inspect()`, and it cannot be exercised against a
      gateway that fails cleanly.
    - Killing an already-dead sandbox returns TERMINATED and does not raise.
    - `break_wake(sandbox_id)` makes `connect()` fail terminally, for the
      sandbox that goes to sleep and does not come back.
    - `list(metadata=...)` finds a sandbox created by a `create` that then
      failed in transit, paused ones included — so the orphan recovery a
      caller depends on can be tested end to end offline. Combining it with
      `fail_next("create", applied=True)` reproduces the whole hole: a live
      sandbox whose id the caller never received.

    Latency is synthetic (`simulated_latency_ms`, defaulting to the p50s
    measured in `ap-southeast-1` on 2026-08-11) and is *added* to a real
    monotonic measurement of the fake's own work. Nothing sleeps. It is there
    so a caller with a latency budget can be tested against production-shaped
    numbers offline; it is not an observation of anything.
    """

    #: Measured 2026-08-11, ap-southeast-1, n=5, p50. The p95s — create 1851,
    #: pause 3075, wake 1598 — are what a timeout should be sized against;
    #: these are what a caller feels. Override the dict to test a tail.
    DEFAULT_LATENCY_MS = {
        "create": 901,
        "pause": 2635,
        "connect": 1085,
        "inspect": 120,
        "kill": 210,
        "run": 90,
        "write_file": 60,
        # Not in the measured set — a plausible single-page listing, kept
        # small so nothing reads it as evidence.
        "list": 150,
    }

    def __init__(
        self,
        *,
        region: str = HIBERNATION_REGIONS[0],
        default_template: str = "code-interpreter-v1",
        api_key: str = "fake-not-a-real-key",
        simulated_latency_ms: dict[str, int] | None = None,
    ) -> None:
        self.region = region
        self.default_template = default_template
        self._api_key = api_key
        self.simulated_latency_ms = dict(
            simulated_latency_ms or self.DEFAULT_LATENCY_MS
        )
        self.sandboxes: dict[str, _FakeSandbox] = {}
        #: Every call made, in order, as (operation, sandbox_id). Lets a test
        #: prove that a cleanup path did not make a second kill round trip,
        #: or that a reconcile actually reconciled.
        self.calls: list[tuple[str, str]] = []
        self._faults: dict[str, _Fault] = {}
        self._next_id = 0
        self._next_pid = 1000
        #: Commands answered with a canned result. Anything not listed exits 0.
        self.command_results: dict[str, tuple[int, str, str]] = {}

    def __repr__(self) -> str:
        return (
            f"<FakeSandboxGateway region={self.region!r} "
            f"sandboxes={len(self.sandboxes)}>"
        )

    __str__ = __repr__

    @property
    def can_hibernate(self) -> bool:
        return self.region in HIBERNATION_REGIONS

    # -- fault injection ----------------------------------------------------

    def fail_next(
        self,
        operation: str,
        *,
        kind: str = "transport",
        applied: bool = False,
        message: str = "",
    ) -> None:
        """Arm one failure for the next call to `operation`.

        `kind` is one of `transport`, `terminal`, `allowlist`, `unavailable`.

        `applied=True` is the interesting one and only means anything for
        `transport`: the mutation is performed and *then* the error is
        raised, which is what a timeout on a request the server honoured
        looks like from here. A caller that treats that as "it did not
        happen" leaks a sandbox.
        """
        self._faults[operation] = _Fault(kind=kind, applied=applied, message=message)

    def break_wake(self, sandbox_id: str) -> None:
        """Make this sandbox refuse to come back from a pause."""
        self.sandboxes[sandbox_id].wake_fails = True

    def set_expiry(self, sandbox_id: str, expires_at: datetime | None) -> None:
        self.sandboxes[sandbox_id].expires_at = expires_at

    def _take_fault(self, operation: str) -> _Fault | None:
        return self._faults.pop(operation, None)

    def _raise(self, fault: _Fault, operation: str, sandbox_id: str) -> None:
        message = fault.message or f"injected {fault.kind} failure on {operation}"
        if fault.kind == "transport":
            raise SandboxTransportError(
                message,
                operation=operation,
                sandbox_id=sandbox_id,
                may_have_applied=fault.applied,
            )
        if fault.kind == "allowlist":
            raise SandboxAllowlistError(
                message, operation=operation, sandbox_id=sandbox_id
            )
        if fault.kind == "unavailable":
            raise SandboxUnavailable(
                message, operation=operation, sandbox_id=sandbox_id
            )
        raise SandboxTerminalError(
            message, operation=operation, sandbox_id=sandbox_id
        )

    def _latency(self, operation: str, started: float) -> int:
        return _elapsed_ms(started) + self.simulated_latency_ms.get(operation, 0)

    def _require(self, sandbox_id: str, operation: str) -> _FakeSandbox:
        sandbox = self.sandboxes.get(sandbox_id)
        if sandbox is None or sandbox.state is SandboxState.TERMINATED:
            raise SandboxTerminalError(
                f"sandbox {sandbox_id} not found",
                operation=operation,
                sandbox_id=sandbox_id,
            )
        return sandbox

    # -- SandboxGateway -----------------------------------------------------

    async def create(
        self, *, template: str, timeout_ms: int, metadata: dict[str, str]
    ) -> SandboxObservation:
        started = time.monotonic()
        self.calls.append(("create", ""))
        fault = self._take_fault("create")

        sandbox_id = ""
        if fault is None or fault.applied:
            self._next_id += 1
            sandbox_id = f"sbx_{self._next_id:04d}"
            self.sandboxes[sandbox_id] = _FakeSandbox(
                sandbox_id=sandbox_id,
                template=template,
                state=SandboxState.RUNNING,
                expires_at=None,
                metadata=dict(metadata),
            )

        if fault is not None:
            self._raise(fault, "create", sandbox_id)

        sandbox = self.sandboxes[sandbox_id]
        return SandboxObservation(
            sandbox_id=sandbox_id,
            state=sandbox.state,
            observed_at=_now(),
            latency_ms=self._latency("create", started),
            template=sandbox.template,
            expires_at=sandbox.expires_at,
        )

    async def connect(self, sandbox_id: str) -> SandboxObservation:
        started = time.monotonic()
        self.calls.append(("connect", sandbox_id))
        fault = self._take_fault("connect")

        sandbox = self._require(sandbox_id, "connect")
        if sandbox.wake_fails:
            raise SandboxTerminalError(
                f"sandbox {sandbox_id} did not wake",
                operation="connect",
                sandbox_id=sandbox_id,
            )

        if fault is None or fault.applied:
            sandbox.state = SandboxState.RUNNING
        if fault is not None:
            self._raise(fault, "connect", sandbox_id)

        return SandboxObservation(
            sandbox_id=sandbox_id,
            state=sandbox.state,
            observed_at=_now(),
            latency_ms=self._latency("connect", started),
            template=sandbox.template,
            expires_at=sandbox.expires_at,
        )

    async def pause(self, sandbox_id: str) -> SandboxObservation:
        started = time.monotonic()
        self.calls.append(("pause", sandbox_id))
        fault = self._take_fault("pause")

        sandbox = self._require(sandbox_id, "pause")

        if not self.can_hibernate:
            # The real 403, in the shape the real one arrives in.
            raise SandboxAllowlistError(
                f"pause is not enabled for this account in {self.region!r} — "
                f"deep hibernation is available only in "
                f"{', '.join(HIBERNATION_REGIONS)}",
                operation="pause",
                sandbox_id=sandbox_id,
            )

        if fault is None or fault.applied:
            sandbox.state = SandboxState.PAUSED
            # Hibernation keeps the disk AND the processes: the guest is
            # suspended and restored, not rebuilt. Nothing to clear.
        if fault is not None:
            self._raise(fault, "pause", sandbox_id)

        return SandboxObservation(
            sandbox_id=sandbox_id,
            state=sandbox.state,
            observed_at=_now(),
            latency_ms=self._latency("pause", started),
            template=sandbox.template,
            expires_at=sandbox.expires_at,
        )

    async def inspect(self, sandbox_id: str) -> SandboxObservation:
        started = time.monotonic()
        self.calls.append(("inspect", sandbox_id))
        fault = self._take_fault("inspect")
        if fault is not None:
            self._raise(fault, "inspect", sandbox_id)

        sandbox = self.sandboxes.get(sandbox_id)
        if sandbox is None:
            return SandboxObservation(
                sandbox_id=sandbox_id,
                state=SandboxState.TERMINATED,
                observed_at=_now(),
                latency_ms=self._latency("inspect", started),
            )
        return SandboxObservation(
            sandbox_id=sandbox_id,
            state=sandbox.state,
            observed_at=_now(),
            latency_ms=self._latency("inspect", started),
            template=sandbox.template,
            expires_at=sandbox.expires_at,
        )

    async def list(
        self, *, metadata: Mapping[str, str] | None = None
    ) -> list[SandboxObservation]:
        """Everything still alive, paused included, filtered by metadata.

        The filter is a subset match — every pair given must be present —
        which is what the API does with `SandboxQuery(metadata=...)`. Paused
        sandboxes are in the result for the same reason they are in the real
        one: they are the orphans most easily forgotten.

        A killed sandbox is gone from `self.sandboxes` entirely, so it cannot
        appear here. That matches the API: `list` returns `running` and
        `paused`, and terminated is neither.
        """
        started = time.monotonic()
        self.calls.append(("list", ""))
        fault = self._take_fault("list")
        if fault is not None:
            self._raise(fault, "list", "")

        wanted = dict(metadata or {})
        latency_ms = self._latency("list", started)
        return [
            SandboxObservation(
                sandbox_id=sandbox.sandbox_id,
                state=sandbox.state,
                observed_at=_now(),
                latency_ms=latency_ms,
                template=sandbox.template,
                expires_at=sandbox.expires_at,
            )
            for sandbox in self.sandboxes.values()
            if sandbox.state is not SandboxState.TERMINATED
            and all(sandbox.metadata.get(k) == v for k, v in wanted.items())
        ]

    async def kill(self, sandbox_id: str) -> SandboxObservation:
        started = time.monotonic()
        self.calls.append(("kill", sandbox_id))
        fault = self._take_fault("kill")

        if fault is None or fault.applied:
            self.sandboxes.pop(sandbox_id, None)
        if fault is not None:
            self._raise(fault, "kill", sandbox_id)

        # Unconditionally terminated, including for a sandbox that was never
        # here. Cleanup depends on it.
        return SandboxObservation(
            sandbox_id=sandbox_id,
            state=SandboxState.TERMINATED,
            observed_at=_now(),
            latency_ms=self._latency("kill", started),
        )

    async def run(
        self,
        sandbox_id: str,
        command: str,
        *,
        timeout_s: int = DEFAULT_COMMAND_TIMEOUT_S,
        background: bool = False,
    ) -> CommandEvidence:
        started = time.monotonic()
        self.calls.append(("run", sandbox_id))
        fault = self._take_fault("run")
        if fault is not None and not fault.applied:
            self._raise(fault, "run", sandbox_id)

        sandbox = self._require(sandbox_id, "run")
        # Connecting to run wakes a paused sandbox, as it does for real.
        if sandbox.state is SandboxState.PAUSED:
            if sandbox.wake_fails:
                raise SandboxTerminalError(
                    f"sandbox {sandbox_id} did not wake",
                    operation="run",
                    sandbox_id=sandbox_id,
                )
            sandbox.state = SandboxState.RUNNING

        if background:
            self._next_pid += 1
            sandbox.background_pids.append(self._next_pid)
            if fault is not None:
                self._raise(fault, "run", sandbox_id)
            return CommandEvidence(
                sandbox_id=sandbox_id,
                command=command,
                exit_code=None,
                stdout="",
                stderr="",
                duration_ms=self._latency("run", started),
                observed_at=_now(),
                background_pid=self._next_pid,
            )

        exit_code, stdout, stderr = self.command_results.get(command, (0, "", ""))
        if fault is not None:
            self._raise(fault, "run", sandbox_id)

        stdout_text, truncated_out = _truncate(redact(stdout, self._api_key))
        stderr_text, truncated_err = _truncate(redact(stderr, self._api_key))
        return CommandEvidence(
            sandbox_id=sandbox_id,
            command=command,
            exit_code=exit_code,
            stdout=stdout_text,
            stderr=stderr_text,
            duration_ms=self._latency("run", started),
            observed_at=_now(),
            truncated=truncated_out or truncated_err,
        )

    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        data: bytes,
        *,
        mode: int = DEFAULT_FILE_MODE,
    ) -> None:
        self.calls.append(("write_file", sandbox_id))
        fault = self._take_fault("write_file")

        sandbox = self._require(sandbox_id, "write_file")
        if sandbox.state is SandboxState.PAUSED:
            sandbox.state = SandboxState.RUNNING

        if fault is None or fault.applied:
            sandbox.files[path] = bytes(data)
            sandbox.modes[path] = mode
        if fault is not None:
            self._raise(fault, "write_file", sandbox_id)
