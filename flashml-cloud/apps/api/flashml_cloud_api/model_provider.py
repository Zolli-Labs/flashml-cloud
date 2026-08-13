"""The agent's LLM, behind a gateway that reports what it saw.

This mirrors `alibaba_sandbox.py` — the house pattern for a gated external
gateway — because the same three reasons apply:

1. **The SDK is synchronous.** `dashscope.Generation.call` is a blocking
   HTTP round trip. Called from a coroutine it stops the event loop for the
   whole request. Every call here goes through `asyncio.to_thread` with an
   explicit ceiling, exactly like the sandbox's `_call`.
2. **The API key is a bearer credential with no scope.** It must not reach a
   log line, a traceback, an HTTP error body, or a `repr`. `redact()` runs on
   every exception path in this module, and `test_model_provider.py` asserts
   it.
3. **A failed call is not a known state.** DashScope's own convention (see
   the Qwen text-generation docs, verified 2026-08-13) is to answer an
   authentication or quota failure as a NORMAL response with a non-200
   `status_code`, `code` and `message` fields — not as a raised exception.
   Only transport-level failures (timeout, DNS, connection reset) come back
   as Python exceptions. Both paths are classified into the same three
   `ModelError` subclasses below, so a caller never has to know which shape
   the failure arrived in.

Settings: `settings.qwen_*`. `render.yaml` does not set them, deliberately —
an unconfigured deploy must still boot, and every route that wants a model
falls back to `FakeModelProvider` until a key exists. See
`settings.qwen_configured`, which is gated on the key alone: `qwen_model`,
`qwen_base_url` and `qwen_region` all carry usable defaults.

**Verified against the DashScope SDK, 2026-08-13** (PyPI `dashscope`
1.26.7, requires Python >=3.8): `Generation.call(api_key=, model=,
messages=, result_format="message", max_tokens=, temperature=, ...)`
returns a response object exposing `status_code`, `request_id`, `code`,
`message`, `output.choices[0].{finish_reason,message.content}`, and
`usage.{input_tokens,output_tokens,total_tokens}`. The `base_url` is NOT a
per-call keyword argument — the SDK reads it off the module-level
`dashscope.base_http_api_url`, set once before the first call. That call
shape, and the "errors come back as data, not exceptions" convention, are
documented; nothing here has been exercised against a live account, so the
real path is deliberately thin — see `_reply_from_response` and `_classify`
— and every behaviour claim is proven instead against the stub classes in
`test_model_provider.py`, the same seam `test_alibaba_sandbox.py` uses for
`e2b_code_interpreter`.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .settings import Settings

log = logging.getLogger(__name__)

T = TypeVar("T")

#: What one call is allowed to take before we stop waiting. Mirrors
#: `alibaba_sandbox.DEFAULT_OP_TIMEOUT_S` — the point is not to be tight, it
#: is that a hung SDK call must not hold a thread forever.
DEFAULT_OP_TIMEOUT_S = 30.0

#: The two DashScope endpoints. Mainland is the default; the international
#: one exists for accounts that were provisioned there. See
#: `settings.qwen_region` / `settings.qwen_base_url`.
DASHSCOPE_BASE_URL_CN = "https://dashscope.aliyuncs.com/api/v1"
DASHSCOPE_BASE_URL_INTL = "https://dashscope-intl.aliyuncs.com/api/v1"


class ModelState(str, Enum):
    """Reserved for a future health/connectivity read, mirroring the shape
    of `alibaba_sandbox.SandboxState`. Not yet wired into `ModelReply` or
    any method here — there is no `inspect()`-equivalent call for a model
    provider today, so nothing produces this value. Declared now rather
    than invented later, on the same premise as `SandboxState.UNKNOWN`:
    when a caller needs to say "asked, and got nothing interpretable back",
    the honest value should already exist rather than being bolted on under
    time pressure.
    """

    OK = "ok"
    UNKNOWN = "unknown"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


@dataclass(frozen=True)
class ModelReply:
    """One completion, in a form that can be stored and argued with.

    `prompt_tokens`/`completion_tokens` are `None`, never `0`, when the
    provider's response did not carry usage — the house honesty rule: an
    absent figure is not an observed zero. `latency_ms` is the wall time of
    the `complete()` call itself, measured with `time.monotonic()`, the same
    convention as `alibaba_sandbox.SandboxObservation.latency_ms`.
    """

    text: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    observed_at: datetime
    latency_ms: int
    finish_reason: str | None = None
    truncated: bool = False


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ModelError(Exception):
    """Base class. Every message that reaches here has been through
    `redact()` — see `_classify` and `_reply_from_response`. Nothing raised
    from this module should ever need a caller to think about whether it is
    safe to log."""

    def __init__(self, message: str, *, operation: str = "", model: str = "") -> None:
        super().__init__(message)
        self.operation = operation
        self.model = model


class ModelUnavailable(ModelError):
    """Not configured — no key, or (at construction) an empty one.

    Raised rather than returning a provider that fails on first use, and
    rather than returning `None`, for the reason `SandboxUnavailable`
    documents: "no provider" and "a provider that reported nothing" must
    not be the same value. A route that wants a model falls back to
    `FakeModelProvider` on this, not on a generic exception.
    """


class ModelTransportError(ModelError):
    """We could not complete the call, or the provider reported a transient
    failure (a 5xx, a rate limit). Retryable.

    Unlike `alibaba_sandbox.SandboxTransportError`, this carries no
    `may_have_applied` flag: a chat completion is not a mutation with a
    server-side side effect a caller needs to reconcile against — worst case
    a retry re-asks the same question and pays for two completions instead
    of one, which is a cost, not a correctness hazard.
    """


class ModelTerminalError(ModelError):
    """The call failed in a way that will fail again: bad credentials, an
    unknown model id, a request the provider refused outright, a malformed
    request. Distinguished from `ModelTransportError` because a retry loop
    around this one turns a configuration mistake into a rate-limit ban."""


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

#: DashScope-issued keys, wherever they appear. Belt and braces around the
#: explicit secret passed to `redact()` on every call: a provider error body
#: can echo a header we do not hold a copy of.
_SECRET_SHAPES: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
)

#: `X-API-KEY: abc`, `api_key='abc'`, `authorization: Bearer abc`. Copied
#: from `alibaba_sandbox._LABELLED_SECRET_RE` — same shape, same reasoning:
#: the value is whatever follows the label, minus an auth *scheme* word if
#: one is in the way.
_LABELLED_SECRET_RE = re.compile(
    r"(?i)\b(x-api-key|api[-_]?key|authorization|bearer|token|secret|password)\b"
    r"(\s*[:=]\s*|\s+)"
    r"((?:bearer|basic|token)\s+)?"
    r"['\"]?([^\s'\",;}\)\]]{4,})"
)

REDACTED = "[redacted]"


def redact(text: object, *secrets: str) -> str:
    """Return `text` as a string with every known credential removed.

    Used on every exception path in this module, including the response
    path where the provider's own `message` field echoed the rejected key —
    that is exactly the string somebody pastes into an issue. Literal
    secrets are stripped first and longest-first, then the shape patterns
    catch anything not explicitly passed in.
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
class ModelProvider(Protocol):
    """The one thing the control plane does to a model: ask it something.

    `runtime_checkable` so a test can assert the fake has not drifted from
    the interface — a fake missing this method is a suite that stays green
    while the caller 500s.
    """

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        timeout_s: int = DEFAULT_OP_TIMEOUT_S,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ModelReply: ...


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _elapsed_ms(started: float) -> int:
    """Monotonic, always — see `alibaba_sandbox._elapsed_ms` for why."""
    return max(0, int((time.monotonic() - started) * 1000))


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get(obj: Any, key: str) -> Any:
    """Read `key` off `obj` whether it is a mapping or an attribute-bearing
    object. DashScope's response type supports both; the stubs in the test
    file use plain dicts, so this is what lets the same code run against
    either."""
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


# ---------------------------------------------------------------------------
# The real one
# ---------------------------------------------------------------------------


class QwenModelProvider:
    """`ModelProvider` over the `dashscope` SDK's `Generation.call`.

    The SDK is imported lazily, exactly as `alibaba_sandbox` imports
    `e2b_code_interpreter`: a deploy without Qwen configured must not need
    the wheel installed, and this module must import cleanly without it.
    `generation_cls` exists so a test can inject a stub without reaching for
    `unittest.mock.patch` on an import that has not happened yet.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "",
        model: str = "qwen-plus",
        region: str = "cn-beijing",
        op_timeout_s: float = DEFAULT_OP_TIMEOUT_S,
        generation_cls: Any = None,
    ) -> None:
        if not api_key:
            raise ModelUnavailable("Qwen is not configured: no API key")
        self._api_key = api_key
        self._base_url = base_url
        self.default_model = model
        self.region = region
        self._op_timeout_s = op_timeout_s
        self._generation_cls = generation_cls

    @classmethod
    def from_settings(cls, settings: "Settings") -> "QwenModelProvider":
        if not settings.qwen_configured:
            raise ModelUnavailable("Qwen is not configured")
        return cls(
            api_key=settings.qwen_api_key,
            base_url=settings.qwen_base_url,
            model=settings.qwen_model,
            region=settings.qwen_region,
        )

    def __repr__(self) -> str:
        """Explicit, because the default would print `self.__dict__` and
        the first thing in it is the API key. See
        `E2BSandboxGateway.__repr__` for the same reasoning."""
        return (
            f"<QwenModelProvider model={self.default_model!r} "
            f"region={self.region!r} base_url={self._base_url!r}>"
        )

    __str__ = __repr__

    # -- SDK plumbing -------------------------------------------------------

    def _client(self) -> Any:
        if self._generation_cls is None:
            try:
                import dashscope
                from dashscope import Generation
            except ImportError as exc:  # pragma: no cover - dependency guard
                raise ModelUnavailable(
                    "dashscope is not installed"
                ) from exc
            if self._base_url:
                # The SDK has no per-call `base_url` kwarg — it reads this
                # module-level attribute instead. Set once, at first real
                # use, rather than on every call.
                dashscope.base_http_api_url = self._base_url
            self._generation_cls = Generation
        return self._generation_cls

    #: `dashscope.common.error` class names that mean "this request is
    #: malformed or asks for something that does not exist" — verified
    #: against `dashscope==1.26.7` 2026-08-13. Never retryable: a loop
    #: around one of these turns a bad model id into a hammering client.
    _TERMINAL_EXC_NAMES = frozenset(
        {
            "AuthenticationError",
            "InvalidParameter",
            "InvalidModel",
            "InvalidInput",
            "InvalidTask",
            "UnsupportedModel",
            "UnsupportedTask",
            "UnsupportedData",
            "UnsupportedDataType",
            "UnsupportedApiProtocol",
            "UnsupportedHTTPMethod",
            "ModelRequired",
            "InputRequired",
            "InputDataRequired",
            "InvalidFileFormat",
        }
    )
    #: Same source, the other direction — retryable, nothing about the
    #: request itself was wrong.
    _TRANSPORT_EXC_NAMES = frozenset({"TimeoutException", "ServiceUnavailableError"})

    def _classify(
        self, exc: BaseException, *, operation: str, model: str
    ) -> ModelError:
        """Map an SDK exception onto our three. Redacts on the way through.

        DashScope's documented convention is that authentication and quota
        failures come back as a normal RESPONSE with a non-200
        `status_code`, not a raised exception — see `_reply_from_response`
        for that path. What lands here is everything else: a timeout, a
        connection failure, or a `dashscope.common.error.DashScopeException`
        subclass, classified by name so this module never has to import
        `dashscope.common.error` at module scope (that would defeat the lazy
        import the rest of this file is built on).
        """
        message = redact(exc, self._api_key)
        name = type(exc).__name__

        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return ModelTransportError(
                f"{operation} timed out after {self._op_timeout_s:.0f}s",
                operation=operation,
                model=model,
            )

        if name == "AuthenticationError":
            log.error("Qwen refused this deployment's credentials on %s", operation)
            return ModelTerminalError(
                "Qwen refused this deployment's credentials",
                operation=operation,
                model=model,
            )

        if name == "RequestFailure":
            # Carries its own status as `http_code`, not folded into the
            # message — read it the same way the response path does.
            http_code = _as_int(getattr(exc, "http_code", None))
            if http_code in (401, 403):
                log.error(
                    "Qwen refused this deployment's credentials on %s", operation
                )
                return ModelTerminalError(
                    "Qwen refused this deployment's credentials",
                    operation=operation,
                    model=model,
                )
            if http_code is None or http_code >= 500:
                return ModelTransportError(
                    f"{operation}: {message}", operation=operation, model=model
                )
            return ModelTerminalError(
                f"{operation}: {message}", operation=operation, model=model
            )

        if name in self._TRANSPORT_EXC_NAMES:
            return ModelTransportError(
                f"{operation}: {message}", operation=operation, model=model
            )

        if name in self._TERMINAL_EXC_NAMES:
            return ModelTerminalError(
                f"{operation}: {message}", operation=operation, model=model
            )

        # A message-shape fallback for anything not raised by `dashscope`
        # itself — a bare `RuntimeError` echoing "401 Unauthorized", say,
        # which is exactly what a stubbed SDK boundary can raise in a test.
        lowered = message.lower()
        if (
            "unauthorized" in lowered
            or "invalid api" in lowered
            or "invalid_api_key" in lowered
            or " 401" in f" {lowered}"
            or " 403" in f" {lowered}"
        ):
            log.error("Qwen refused this deployment's credentials on %s", operation)
            return ModelTerminalError(
                "Qwen refused this deployment's credentials",
                operation=operation,
                model=model,
            )

        if isinstance(exc, (OSError, ConnectionError)):
            # A connection reset, a DNS failure, a proxy closing the socket.
            # Retryable.
            return ModelTransportError(
                f"{operation}: {name}: {message}", operation=operation, model=model
            )

        # Unrecognised. Terminal, so a retry loop stops rather than
        # hammering something we do not understand — same direction
        # `alibaba_sandbox._classify` rounds an unparsed failure.
        return ModelTerminalError(
            f"{operation}: {name}: {message}", operation=operation, model=model
        )

    async def _call(
        self,
        operation: str,
        fn: Callable[[], T],
        *,
        model: str,
        timeout_s: float,
    ) -> tuple[T, int]:
        """Run one blocking SDK call off the loop, with a ceiling. Mirrors
        `E2BSandboxGateway._call` — see its docstring for why the ceiling is
        `asyncio.wait_for` rather than anything thread-cancelling."""
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout_s)
        except BaseException as exc:  # noqa: BLE001 - classified, then re-raised
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
                raise
            if isinstance(exc, ModelError):
                raise
            raise self._classify(exc, operation=operation, model=model) from None
        return result, _elapsed_ms(started)

    def _reply_from_response(
        self, response: Any, *, model: str, latency_ms: int
    ) -> ModelReply:
        """Turn one `Generation.call` response into a `ModelReply`, or raise
        the classified error DashScope encoded as data rather than as an
        exception. See the module docstring's point 3."""
        status_code = _as_int(_get(response, "status_code"))
        if status_code is not None and status_code != 200:
            code = redact(_get(response, "code") or "", self._api_key)
            provider_message = redact(_get(response, "message") or "", self._api_key)
            detail = f"{code}: {provider_message}" if code else provider_message
            if status_code in (401, 403):
                log.error("Qwen refused this deployment's credentials on complete")
                raise ModelTerminalError(
                    f"complete: Qwen refused this deployment's credentials "
                    f"({detail})",
                    operation="complete",
                    model=model,
                )
            if status_code >= 500:
                raise ModelTransportError(
                    f"complete: {detail}", operation="complete", model=model
                )
            raise ModelTerminalError(
                f"complete: {detail}", operation="complete", model=model
            )

        output = _get(response, "output")
        choices = _get(output, "choices") or []
        first = choices[0] if choices else {}
        message_obj = _get(first, "message") or {}
        text = _get(message_obj, "content") or ""
        finish_reason = _get(first, "finish_reason")

        usage = _get(response, "usage") or {}
        prompt_tokens = _as_int(_get(usage, "input_tokens"))
        completion_tokens = _as_int(_get(usage, "output_tokens"))

        return ModelReply(
            text=text,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            observed_at=_now(),
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            truncated=False,
        )

    # -- ModelProvider --------------------------------------------------

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        timeout_s: int = DEFAULT_OP_TIMEOUT_S,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ModelReply:
        """Ask the model something and return what it said.

        Verified call shape, 2026-08-13 (see module docstring):
        `Generation.call(api_key=, model=, messages=, result_format=
        "message", max_tokens=, temperature=)`. `max_tokens`/`temperature`
        are omitted from the call entirely when `None`, rather than sent as
        `None` — the SDK's own default for each applies, instead of this
        module inventing one.
        """
        generation_cls = self._client()
        kwargs: dict[str, Any] = {
            "api_key": self._api_key,
            "model": model,
            "messages": messages,
            "result_format": "message",
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if temperature is not None:
            kwargs["temperature"] = temperature

        def _run() -> Any:
            return generation_cls.call(**kwargs)

        response, latency_ms = await self._call(
            "complete", _run, model=model, timeout_s=timeout_s
        )
        return self._reply_from_response(response, model=model, latency_ms=latency_ms)


# ---------------------------------------------------------------------------
# The fake
# ---------------------------------------------------------------------------


@dataclass
class _Fault:
    """One injected failure, armed for a single call. Mirrors
    `alibaba_sandbox._Fault`."""

    kind: type[ModelError]
    message: str


class FakeModelProvider:
    """A `ModelProvider` with no network. **Tests only.**

    Not a mock: `calls` is a real log a test can assert on (`("complete",
    model)` pairs), and `replies` are returned round-robin so a multi-turn
    caller can be scripted deterministically. `fail_next` arms exactly one
    failure for the next `complete()`, then disarms itself — the same
    one-shot shape `alibaba_sandbox.py`'s sandbox fake uses for its own
    `fail_next`.

    `simulated_latency_ms` (default 40, an arbitrary but plausible chat
    round trip) is *added* to a real monotonic measurement of the fake's own
    work, exactly as the sandbox fake does — nothing sleeps, and it exists
    so a caller with a latency budget can be tested against
    production-shaped numbers offline.
    """

    def __init__(
        self, *, replies: list[str] | None = None, simulated_latency_ms: int = 40
    ) -> None:
        self._replies = list(replies) if replies else ["ok"]
        self._next = 0
        self.simulated_latency_ms = simulated_latency_ms
        #: Every call made, in order, as (operation, model). Lets a test
        #: prove a caller asked exactly once, or asked the right model.
        self.calls: list[tuple[str, str]] = []
        self._faults: dict[str, _Fault] = {}

    def __repr__(self) -> str:
        return f"<FakeModelProvider replies={len(self._replies)}>"

    __str__ = __repr__

    def fail_next(
        self, operation: str, *, kind: type[ModelError], message: str
    ) -> None:
        """Arm one failure for the next call to `operation`. `kind` is one
        of the three `ModelError` subclasses — passed as the class itself,
        not an instance, so this fake never has to know its constructor
        signature beyond `(message, *, operation=, model=)`."""
        self._faults[operation] = _Fault(kind=kind, message=message)

    def _latency(self, started: float) -> int:
        return _elapsed_ms(started) + self.simulated_latency_ms

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        timeout_s: int = DEFAULT_OP_TIMEOUT_S,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ModelReply:
        started = time.monotonic()
        self.calls.append(("complete", model))
        fault = self._faults.pop("complete", None)
        if fault is not None:
            raise fault.kind(fault.message, operation="complete", model=model)

        text = self._replies[self._next % len(self._replies)]
        self._next += 1
        return ModelReply(
            text=text,
            model=model,
            prompt_tokens=None,
            completion_tokens=None,
            observed_at=_now(),
            latency_ms=self._latency(started),
            finish_reason="stop",
            truncated=False,
        )
