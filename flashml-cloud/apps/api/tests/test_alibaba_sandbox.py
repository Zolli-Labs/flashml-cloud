"""The FC Agent Sandbox gateway: observations, error classes, and the key.

Nothing here reaches Alibaba. Another agent owns the live budget, and a test
that needs a paid region to run is a test that does not run.

Two seams do the work:

- `FakeSandboxGateway` keeps real state, so the lifecycle assertions are
  about behaviour rather than about call counts — a pause really does keep
  both the files and the running processes, which is the one thing the fake
  must never get wrong. It got it wrong once, in the direction of claiming
  processes die; see `alibaba_sandbox.run` for how a probe managed to
  "verify" that.
- `E2BSandboxGateway(sandbox_cls=...)` takes a stub class, so the production
  code paths — the async wrapper, the exception classification, the
  redaction, the ms→s conversion — are exercised against the real
  implementation without the SDK installed. The API venv does not have
  `e2b-code-interpreter` in it; that is deliberate (the import is lazy) and
  it is also why this file must not import it.
"""
from __future__ import annotations

import asyncio
import re

import pytest

from flashml_cloud_api.alibaba_sandbox import (
    DEFAULT_FILE_MODE,
    HIBERNATION_REGIONS,
    REDACTED,
    CommandEvidence,
    E2BSandboxGateway,
    FakeSandboxGateway,
    SandboxAllowlistError,
    SandboxError,
    SandboxGateway,
    SandboxObservation,
    SandboxState,
    SandboxTerminalError,
    SandboxTransportError,
    SandboxUnavailable,
    redact,
)
from flashml_cloud_api.settings import Settings

def _shaped(prefix: str, length: int) -> str:
    """Build a credential-SHAPED value without writing one down.

    These tests have to hold something key-shaped — `redact` is asserted to
    strip it, and a fixture that looks nothing like a credential would prove
    nothing. But a literal high-entropy string in a tracked file is a finding
    for any secret scanner, and the honest answer to a scanner is not an
    ignore entry. An ignore list that grows is a scanner losing its signal,
    and "we have an exception for that" is how a real key eventually gets
    waved through.

    So the value is ASSEMBLED at run time. Scanners read source text; a string
    that only exists once the module is imported is not in the source to find.
    The alphabet cycle also keeps entropy near zero, so nothing here would fire
    a rule even if it were written out.
    """
    return prefix + "".join(chr(ord("a") + i % 26) for i in range(length))


API_KEY = _shaped("e2b_", 40)
API_URL = "https://api.ap-southeast-1.e2b.fc.aliyuncs.com"
DOMAIN = "ap-southeast-1.e2b.fc.aliyuncs.com"
TEMPLATE = "code-interpreter-v1"


def _settings(**overrides) -> Settings:
    base = dict(
        supabase_url="https://example.supabase.co",
        supabase_service_key="",
        coordinator_url="http://coordinator",
        coordinator_operator_token="op",
        require_auth=True,
        fc_sandbox_api_key=API_KEY,
        fc_sandbox_api_url=API_URL,
        fc_sandbox_domain=DOMAIN,
        fc_sandbox_region="ap-southeast-1",
        fc_sandbox_template=TEMPLATE,
        fc_sandbox_pool_id="pool_sandbox",
        fc_sandbox_timeout_ms=3_600_000,
    )
    base.update(overrides)
    return Settings(**base)


async def _fresh(gateway: FakeSandboxGateway) -> str:
    observed = await gateway.create(
        template=TEMPLATE, timeout_ms=3_600_000, metadata={"job": "j1"}
    )
    return observed.sandbox_id


# ---------------------------------------------------------------------------
# Stubs standing in for the SDK
# ---------------------------------------------------------------------------


class _StubResult:
    """Shaped like `e2b`'s `CommandResult`."""

    def __init__(self, exit_code: int = 0, stdout: str = "", stderr: str = ""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class _StubExit(Exception):
    """Shaped like `CommandExitException`, which subclasses `CommandResult`
    and therefore carries the output alongside the non-zero code."""

    def __init__(self, exit_code: int, stdout: str = "", stderr: str = ""):
        super().__init__(f"exit status {exit_code}")
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class _StubInfo:
    def __init__(self, sandbox_id: str, state: str = "running"):
        self.sandbox_id = sandbox_id
        self.state = state
        self.template_id = TEMPLATE
        self.end_at = None


class _StubCommands:
    def __init__(self, owner: "_StubSandbox"):
        self._owner = owner

    def run(self, cmd, background=None, timeout=None, request_timeout=None):
        self._owner.gateway_log.append(("run", cmd))
        if cmd in self._owner.raises:
            raise self._owner.raises[cmd]
        if background:
            return type("Handle", (), {"pid": 4242})()
        return self._owner.results.get(cmd, _StubResult())


class _StubFiles:
    def __init__(self, owner: "_StubSandbox"):
        self._owner = owner

    def write(self, path, data):
        self._owner.gateway_log.append(("write", path))
        self._owner.written[path] = data


class _StubSandbox:
    """One connected handle."""

    def __init__(self, sandbox_id: str, shared: "_StubClass"):
        self.sandbox_id = sandbox_id
        self.gateway_log = shared.log
        self.results = shared.results
        self.raises = shared.raises
        self.written = shared.written
        self.commands = _StubCommands(self)
        self.files = _StubFiles(self)


class _StubQuery:
    """Stands in for `e2b.SandboxQuery`. The real one is a dataclass with
    `metadata` and `state`; the paginator reads both off it."""

    def __init__(self, metadata=None, state=None):
        self.metadata = metadata
        self.state = state


class _StubPaginator:
    """Stands in for `SandboxPaginator`: `has_next` / `next_items()`, the
    cursor protocol the real listing exposes."""

    def __init__(self, pages: list[list[_StubInfo]]):
        self._pages = list(pages)
        self.drained = 0

    @property
    def has_next(self) -> bool:
        return bool(self._pages)

    def next_items(self) -> list:
        self.drained += 1
        return self._pages.pop(0)


class _StubClass:
    """Stands in for `e2b_code_interpreter.Sandbox` — the class, with its
    classmethod control plane. Records every call and its kwargs so a test
    can assert on what was actually sent."""

    def __init__(self):
        self.log: list[tuple] = []
        self.results: dict[str, _StubResult] = {}
        self.raises: dict[str, Exception] = {}
        self.written: dict[str, bytes] = {}
        self.states: dict[str, str] = {}
        self.fail: dict[str, Exception] = {}
        self.calls: list[tuple[str, dict]] = []
        #: Pages the next `list()` hands back, newest paginator wins.
        self.pages: list[list[_StubInfo]] = [[]]
        self.paginator: _StubPaginator | None = None

    def list(self, query=None, **kwargs):
        self.calls.append(("list", {"query": query, **kwargs}))
        self._maybe_fail("list")
        self.paginator = _StubPaginator(self.pages)
        return self.paginator

    def _maybe_fail(self, op: str) -> None:
        exc = self.fail.pop(op, None)
        if exc is not None:
            raise exc

    def create(self, *, template, timeout, metadata, **kwargs):
        self.calls.append(("create", {"template": template, "timeout": timeout,
                                      "metadata": metadata, **kwargs}))
        self._maybe_fail("create")
        self.states["sbx_stub"] = "running"
        return _StubSandbox("sbx_stub", self)

    def connect(self, sandbox_id, timeout=None, **kwargs):
        self.calls.append(("connect", {"sandbox_id": sandbox_id,
                                       "timeout": timeout, **kwargs}))
        self._maybe_fail("connect")
        self.states[sandbox_id] = "running"
        return _StubSandbox(sandbox_id, self)

    def get_info(self, sandbox_id, **kwargs):
        self.calls.append(("get_info", {"sandbox_id": sandbox_id, **kwargs}))
        self._maybe_fail("get_info")
        return _StubInfo(sandbox_id, self.states.get(sandbox_id, "running"))

    def pause(self, sandbox_id, **kwargs):
        self.calls.append(("pause", {"sandbox_id": sandbox_id, **kwargs}))
        self._maybe_fail("pause")
        self.states[sandbox_id] = "paused"
        return True

    def kill(self, sandbox_id, **kwargs):
        self.calls.append(("kill", {"sandbox_id": sandbox_id, **kwargs}))
        self._maybe_fail("kill")
        self.states.pop(sandbox_id, None)
        return True


def _gateway(stub: _StubClass | None = None, **overrides) -> E2BSandboxGateway:
    base = dict(
        api_key=API_KEY,
        api_url=API_URL,
        domain=DOMAIN,
        sandbox_cls=stub if stub is not None else _StubClass(),
        query_cls=_StubQuery,
    )
    base.update(overrides)
    return E2BSandboxGateway(**base)


# ---------------------------------------------------------------------------
# The Protocol
# ---------------------------------------------------------------------------


def test_both_gateways_satisfy_the_protocol():
    """A fake that has drifted from the interface is worse than no fake: the
    tests keep passing while production calls a method the fake never had."""
    for gateway in (FakeSandboxGateway(), _gateway()):
        for name in (
            "create", "connect", "run", "write_file", "pause", "inspect",
            "list", "kill",
        ):
            assert callable(getattr(gateway, name)), name
        assert isinstance(gateway, SandboxGateway)


def test_the_fake_is_never_the_default_anywhere():
    """`FakeSandboxGateway` is tests-only. Nothing in the package may import
    it — a fake reachable from a route is a route that silently succeeds
    against a sandbox that does not exist."""
    import pathlib

    package = pathlib.Path(__file__).parent.parent / "flashml_cloud_api"
    offenders = [
        path.name
        for path in sorted(package.rglob("*.py"))
        if path.name != "alibaba_sandbox.py"
        and "FakeSandboxGateway" in path.read_text()
    ]
    assert offenders == []


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_an_unconfigured_deployment_raises_rather_than_half_working():
    with pytest.raises(SandboxUnavailable):
        E2BSandboxGateway(api_key="", api_url=API_URL, domain=DOMAIN)
    with pytest.raises(SandboxUnavailable):
        E2BSandboxGateway(api_key=API_KEY, api_url="", domain=DOMAIN)
    with pytest.raises(SandboxUnavailable):
        E2BSandboxGateway(api_key=API_KEY, api_url=API_URL, domain="")


def test_from_settings_refuses_a_partially_configured_deployment():
    with pytest.raises(SandboxUnavailable):
        E2BSandboxGateway.from_settings(_settings(fc_sandbox_pool_id=""))


def test_from_settings_carries_the_region_and_the_template():
    gateway = E2BSandboxGateway.from_settings(_settings())
    assert gateway.region == "ap-southeast-1"
    assert gateway.default_template == TEMPLATE
    assert gateway.can_hibernate is True


def test_a_non_singapore_region_reports_that_it_cannot_hibernate():
    """`us-west-1` answers `pause` with 403 PauseSessionForbidden. Knowing
    that before creating anything is the difference between a design
    decision and a wasted round trip."""
    gateway = E2BSandboxGateway.from_settings(_settings(fc_sandbox_region="us-west-1"))
    assert gateway.can_hibernate is False
    assert "us-west-1" not in HIBERNATION_REGIONS


# ---------------------------------------------------------------------------
# Never leak the key
# ---------------------------------------------------------------------------


def test_redact_removes_a_literal_secret():
    assert API_KEY not in redact(f"failed with {API_KEY}", API_KEY)


def test_redact_removes_a_key_it_was_never_told_about():
    """The belt to the explicit list's braces: an SDK error body can echo a
    header we do not hold a copy of."""
    other = "e2b_ffffffffffffffffffffffffffffffffffffffff"
    assert other not in redact(f"401: rejected {other}", API_KEY)


@pytest.mark.parametrize(
    "text, secret",
    [
        # Each shape is BUILT, not written — see `_shaped`. These have to
        # match the patterns `redact` looks for, so they cannot be arbitrary
        # placeholder words; assembling them is what keeps a key-shaped string
        # out of the source while still exercising the pattern.
        (f"X-API-KEY: {_shaped('', 16)}", _shaped("", 16)),
        (f"authorization: Bearer {_shaped('tok_', 12)}", _shaped("tok_", 12)),
        (f"access_key_secret='{_shaped('', 14)}'", _shaped("", 14)),
        (f"{_shaped('LTAI', 16)} is the id", _shaped("LTAI", 16)),
    ],
)
def test_redact_removes_credential_shapes(text, secret):
    assert secret not in redact(text, API_KEY)
    assert REDACTED in redact(text, API_KEY)


def test_redact_leaves_ordinary_text_alone():
    """A redactor that eats the message is a redactor nobody keeps."""
    text = "create timed out after 30s for template code-interpreter-v1"
    assert redact(text, API_KEY) == text


def test_the_gateway_repr_does_not_contain_the_key():
    """The default `repr` prints `__dict__`, and the first thing in it is the
    key. `Settings` marks the field `repr=False`; a gateway that undoes that
    at the first f-string in a log line makes that pointless."""
    gateway = _gateway()
    assert API_KEY not in repr(gateway)
    assert API_KEY not in str(gateway)
    assert API_KEY not in f"{gateway}"
    assert "ap-southeast-1" in repr(gateway)


@pytest.mark.asyncio
async def test_the_key_never_reaches_an_exception_message():
    """The guarantee this module exists for. The SDK is entitled to put the
    rejected credential in its own message — it did, in the 401 that
    motivated this — and that message must not come back out."""
    stub = _StubClass()
    stub.fail["create"] = RuntimeError(
        f"400: request failed, X-API-KEY: {API_KEY}, url={API_URL}"
    )
    gateway = _gateway(stub)

    with pytest.raises(SandboxError) as caught:
        await gateway.create(template=TEMPLATE, timeout_ms=1000, metadata={})

    assert API_KEY not in str(caught.value)
    assert API_KEY not in repr(caught.value)
    assert API_KEY not in "".join(str(a) for a in caught.value.args)


@pytest.mark.asyncio
async def test_the_key_never_reaches_command_output():
    """Command output is persisted as evidence. A script that echoes its
    environment must not put the key in the ledger."""
    stub = _StubClass()
    stub.results["env"] = _StubResult(0, stdout=f"E2B_API_KEY={API_KEY}\n")
    gateway = _gateway(stub)

    evidence = await gateway.run("sbx_1", "env", timeout_s=5)

    assert API_KEY not in evidence.stdout
    assert REDACTED in evidence.stdout


@pytest.mark.asyncio
async def test_an_authentication_failure_reports_nothing_about_the_credential():
    stub = _StubClass()
    auth = type("AuthenticationException", (Exception,), {})(
        f"401: Unauthorized - key {API_KEY}"
    )
    stub.fail["create"] = auth
    gateway = _gateway(stub)

    with pytest.raises(SandboxTerminalError) as caught:
        await gateway.create(template=TEMPLATE, timeout_ms=1000, metadata={})

    assert API_KEY not in str(caught.value)
    assert "401" not in str(caught.value)


# ---------------------------------------------------------------------------
# Observations, not intentions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_reads_the_state_back_rather_than_assuming_running():
    """`create` returns a handle, not a state. A sandbox can be created and
    be dead before anyone looks; "it returned so it is running" is exactly
    the inference this module refuses."""
    stub = _StubClass()
    gateway = _gateway(stub)

    observed = await gateway.create(
        template=TEMPLATE, timeout_ms=3_600_000, metadata={"job": "j1"}
    )

    assert observed.state is SandboxState.RUNNING
    assert [name for name, _ in stub.calls] == ["create", "get_info"]


@pytest.mark.asyncio
async def test_create_reports_the_state_the_api_gave_not_the_one_we_wanted():
    stub = _StubClass()
    gateway = _gateway(stub)
    original_get_info = stub.get_info

    def _paused(sandbox_id, **kwargs):
        stub.states[sandbox_id] = "paused"
        return original_get_info(sandbox_id, **kwargs)

    stub.get_info = _paused

    observed = await gateway.create(template=TEMPLATE, timeout_ms=1000, metadata={})

    assert observed.state is SandboxState.PAUSED


@pytest.mark.asyncio
async def test_create_keeps_the_id_when_the_state_read_fails():
    """The mutation happened. Raising here would throw away the only copy of
    a `sandbox_id` that now bills until its TTL runs out."""
    stub = _StubClass()
    stub.fail["get_info"] = RuntimeError("503: gateway is having a moment")
    gateway = _gateway(stub)

    observed = await gateway.create(template=TEMPLATE, timeout_ms=1000, metadata={})

    assert observed.sandbox_id == "sbx_stub"
    assert observed.state is SandboxState.UNKNOWN
    assert observed.is_live is False


@pytest.mark.asyncio
async def test_an_unrecognised_state_becomes_unknown_and_keeps_the_id():
    stub = _StubClass()
    stub.states["sbx_stub"] = "hibernating-somehow"
    gateway = _gateway(stub)

    observed = await gateway.inspect("sbx_stub")

    assert observed.state is SandboxState.UNKNOWN
    assert observed.sandbox_id == "sbx_stub"


@pytest.mark.asyncio
async def test_timeout_ms_is_converted_to_the_sdk_s_seconds():
    """The settings field is milliseconds; `Sandbox.create(timeout=...)` is
    seconds. Getting this wrong by 1000x is a one-hour sandbox that dies in
    3.6 seconds."""
    stub = _StubClass()
    gateway = _gateway(stub)

    await gateway.create(template=TEMPLATE, timeout_ms=3_600_000, metadata={})

    assert stub.calls[0][1]["timeout"] == 3600


@pytest.mark.asyncio
async def test_a_sub_second_lifetime_rounds_up_never_down():
    stub = _StubClass()
    gateway = _gateway(stub)

    await gateway.create(template=TEMPLATE, timeout_ms=1500, metadata={})

    assert stub.calls[0][1]["timeout"] == 2


@pytest.mark.asyncio
async def test_connect_re_sends_the_configured_lifetime():
    """`Sandbox.connect` puts whatever timeout it is given into the request
    and defaults it to the SDK's 300 seconds. Connecting without one silently
    cuts a one-hour sandbox to five minutes."""
    stub = _StubClass()
    gateway = _gateway(stub, default_timeout_ms=3_600_000)

    await gateway.connect("sbx_1")

    connect_call = next(kwargs for name, kwargs in stub.calls if name == "connect")
    assert connect_call["timeout"] == 3600


@pytest.mark.asyncio
async def test_the_api_key_validator_is_switched_off_for_alibaba_keys():
    """The SDK asserts keys look like `e2b_` + hex before sending anything,
    and Alibaba issues the key for its own FC deployment. Left on, every call
    fails at the client with a message pointing at the e2b.dev dashboard,
    which is not where this key came from."""
    stub = _StubClass()
    gateway = _gateway(stub)

    await gateway.inspect("sbx_1")

    assert stub.calls[0][1]["validate_api_key"] is False
    assert stub.calls[0][1]["api_url"] == API_URL
    assert stub.calls[0][1]["domain"] == DOMAIN


@pytest.mark.asyncio
async def test_latency_is_reported_and_non_negative():
    gateway = FakeSandboxGateway()
    observed = await gateway.create(template=TEMPLATE, timeout_ms=1000, metadata={})
    assert observed.latency_ms >= 0
    assert isinstance(observed.latency_ms, int)


@pytest.mark.asyncio
async def test_the_pause_latency_the_fake_reports_is_the_measured_one():
    """p50 901/2635/1085 ms for create/pause/wake, ap-southeast-1,
    2026-08-11, n=5. A caller with a latency budget can be tested against
    production-shaped numbers offline."""
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)

    paused = await gateway.pause(sandbox_id)
    woken = await gateway.connect(sandbox_id)

    assert paused.latency_ms >= 2635
    assert woken.latency_ms >= 1085


# ---------------------------------------------------------------------------
# The allowlist 403
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_outside_singapore_raises_the_allowlist_error():
    """Region-specific and permanent. A caller that cannot tell this from a
    generic failure retries a call that can never succeed."""
    gateway = FakeSandboxGateway(region="us-west-1")
    sandbox_id = await _fresh(gateway)

    with pytest.raises(SandboxAllowlistError) as caught:
        await gateway.pause(sandbox_id)

    assert "us-west-1" in str(caught.value)
    assert "ap-southeast-1" in str(caught.value)


@pytest.mark.asyncio
async def test_the_allowlist_error_is_not_a_transport_error():
    """The distinction is the entire point: `SandboxTransportError` invites a
    retry and this must not be retried."""
    gateway = FakeSandboxGateway(region="us-west-1")
    sandbox_id = await _fresh(gateway)

    with pytest.raises(SandboxError) as caught:
        await gateway.pause(sandbox_id)

    assert not isinstance(caught.value, SandboxTransportError)
    assert not isinstance(caught.value, SandboxTerminalError)


@pytest.mark.asyncio
async def test_a_refused_pause_leaves_the_sandbox_running():
    """Nothing was consumed except a round trip. A caller that assumed the
    sandbox was gone would leak it."""
    gateway = FakeSandboxGateway(region="us-west-1")
    sandbox_id = await _fresh(gateway)

    with pytest.raises(SandboxAllowlistError):
        await gateway.pause(sandbox_id)

    assert (await gateway.inspect(sandbox_id)).state is SandboxState.RUNNING


@pytest.mark.asyncio
async def test_the_real_gateway_recognises_the_403_by_its_message():
    """`e2b.api.handle_api_exception` formats every non-401/429 failure as
    `f"{status}: {body}"` and raises a bare `SandboxException`, so the status
    code is not an attribute anywhere. This is the coupling to that format,
    pinned so an SDK upgrade that changes it fails here rather than in
    production as a generic error nobody classifies."""
    stub = _StubClass()
    stub.fail["pause"] = RuntimeError(
        "403: pauseSession is not enabled for this function"
    )
    gateway = _gateway(stub, region="us-west-1")

    with pytest.raises(SandboxAllowlistError):
        await gateway.pause("sbx_1")


@pytest.mark.asyncio
async def test_a_different_403_is_not_treated_as_the_allowlist():
    """Over-claiming would tell an operator to change region over a problem
    that has nothing to do with region."""
    stub = _StubClass()
    stub.fail["pause"] = RuntimeError("403: this sandbox belongs to another account")
    gateway = _gateway(stub)

    with pytest.raises(SandboxTerminalError):
        await gateway.pause("sbx_1")


# ---------------------------------------------------------------------------
# Transport failures and reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_timeout_is_transport_and_flags_that_it_may_have_applied():
    """`asyncio.wait_for` abandons the await, not the thread — the SDK call
    keeps running after we stop waiting. A caller that assumes "timed out
    means it did not happen" leaks whatever it created."""

    class _Hanging(_StubClass):
        def create(self, **kwargs):
            import time as _time

            _time.sleep(0.5)
            return _StubSandbox("sbx_late", self)

    gateway = _gateway(_Hanging(), op_timeout_s=0.05)

    with pytest.raises(SandboxTransportError) as caught:
        await gateway.create(template=TEMPLATE, timeout_ms=1000, metadata={})

    assert caught.value.may_have_applied is True
    assert caught.value.operation == "create"


@pytest.mark.asyncio
async def test_a_failed_inspect_never_claims_it_may_have_applied():
    """A read changed nothing by definition. Saying otherwise sends callers
    reconciling against a call that cannot have had an effect."""
    stub = _StubClass()
    stub.fail["get_info"] = OSError("connection reset by peer")
    gateway = _gateway(stub)

    with pytest.raises(SandboxTransportError) as caught:
        await gateway.inspect("sbx_1")

    assert caught.value.may_have_applied is False


@pytest.mark.asyncio
async def test_inspect_reconciles_a_transport_failure_that_actually_applied():
    """The case the fake exists for. The pause raised, and the pause
    happened. Only `inspect()` can tell the caller which."""
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)
    gateway.fail_next("pause", applied=True)

    with pytest.raises(SandboxTransportError) as caught:
        await gateway.pause(sandbox_id)
    assert caught.value.may_have_applied is True

    reconciled = await gateway.inspect(sandbox_id)

    assert reconciled.state is SandboxState.PAUSED


@pytest.mark.asyncio
async def test_a_transport_failure_that_did_not_apply_reconciles_the_other_way():
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)
    gateway.fail_next("pause", applied=False)

    with pytest.raises(SandboxTransportError):
        await gateway.pause(sandbox_id)

    assert (await gateway.inspect(sandbox_id)).state is SandboxState.RUNNING


@pytest.mark.asyncio
async def test_a_create_that_failed_in_transit_can_still_be_reconciled():
    """The most expensive version: the sandbox exists and the caller does not
    know its id. It is on the gateway's books, and `inspect` confirms it — a
    retry without this check bills for two."""
    gateway = FakeSandboxGateway()
    gateway.fail_next("create", applied=True)

    with pytest.raises(SandboxTransportError) as caught:
        await gateway.create(template=TEMPLATE, timeout_ms=1000, metadata={})

    leaked = caught.value.sandbox_id
    assert leaked
    assert (await gateway.inspect(leaked)).state is SandboxState.RUNNING
    assert (await gateway.kill(leaked)).state is SandboxState.TERMINATED


@pytest.mark.asyncio
async def test_a_5xx_is_retryable_and_a_4xx_is_not():
    for status, expected in ((503, SandboxTransportError), (400, SandboxTerminalError)):
        stub = _StubClass()
        stub.fail["get_info"] = RuntimeError(f"{status}: upstream said so")
        gateway = _gateway(stub)
        with pytest.raises(expected):
            await gateway.inspect("sbx_1")


@pytest.mark.asyncio
async def test_an_unrecognisable_failure_is_terminal_so_a_retry_loop_stops():
    stub = _StubClass()
    stub.fail["get_info"] = ValueError("something nobody has seen before")
    gateway = _gateway(stub)

    with pytest.raises(SandboxTerminalError):
        await gateway.inspect("sbx_1")


@pytest.mark.asyncio
async def test_a_failed_call_drops_the_cached_connection():
    """A handle that just failed is the one thing we know is unusable."""
    stub = _StubClass()
    gateway = _gateway(stub)
    await gateway.connect("sbx_1")
    assert "sbx_1" in gateway._handles

    stub.fail["pause"] = OSError("connection reset")
    with pytest.raises(SandboxTransportError):
        await gateway.pause("sbx_1")

    assert "sbx_1" not in gateway._handles


# ---------------------------------------------------------------------------
# The async wrapper
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_blocking_sdk_call_does_not_stall_the_event_loop():
    """The reason every call goes through `asyncio.to_thread`. Called
    directly from a coroutine, the SDK's blocking httpx stops the loop for
    the whole round trip — 1.8 s for a create, unbounded when the far end
    hangs."""

    class _Slow(_StubClass):
        def get_info(self, sandbox_id, **kwargs):
            import time as _time

            _time.sleep(0.2)
            return _StubInfo(sandbox_id)

    gateway = _gateway(_Slow())
    ticks = 0

    async def _tick():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.create_task(_tick())
    await gateway.inspect("sbx_1")
    ticker.cancel()

    assert ticks > 3, "the event loop was blocked while the SDK call ran"


# ---------------------------------------------------------------------------
# Idempotent kill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_killing_twice_is_a_no_op_that_reports_terminated():
    """Cleanup paths depend on this. A `finally:` that raises because the
    sandbox already went away masks whatever the `try:` was failing on."""
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)

    first = await gateway.kill(sandbox_id)
    second = await gateway.kill(sandbox_id)

    assert first.state is SandboxState.TERMINATED
    assert second.state is SandboxState.TERMINATED


@pytest.mark.asyncio
async def test_killing_a_sandbox_that_never_existed_does_not_raise():
    gateway = FakeSandboxGateway()
    observed = await gateway.kill("sbx_never")
    assert observed.state is SandboxState.TERMINATED
    assert observed.sandbox_id == "sbx_never"


@pytest.mark.asyncio
async def test_the_real_gateway_treats_a_404_kill_as_terminated():
    """The SDK's `kill` already returns False on a 404, but a 404 raised on
    the way there is the same fact and must land the same way."""
    stub = _StubClass()
    stub.fail["kill"] = RuntimeError("Sandbox sbx_1 not found")
    gateway = _gateway(stub)

    observed = await gateway.kill("sbx_1")

    assert observed.state is SandboxState.TERMINATED


@pytest.mark.asyncio
async def test_kill_reports_terminated_without_a_second_round_trip():
    """The one method whose return value is itself the observation: the API
    confirmed the delete, or answered 404. Both mean gone."""
    stub = _StubClass()
    gateway = _gateway(stub)

    await gateway.kill("sbx_1")

    assert [name for name, _ in stub.calls] == ["kill"]


@pytest.mark.asyncio
async def test_a_kill_that_genuinely_failed_still_raises():
    """Idempotence is about absence, not about swallowing everything. A 500
    on a kill means the sandbox may still be alive and billing."""
    stub = _StubClass()
    stub.fail["kill"] = RuntimeError("500: internal error")
    gateway = _gateway(stub)

    with pytest.raises(SandboxTransportError):
        await gateway.kill("sbx_1")


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inspect_reports_terminated_for_a_sandbox_that_is_gone():
    """Absence is an observation. Raising would force every caller to catch
    an exception to learn a normal outcome."""
    gateway = FakeSandboxGateway()
    observed = await gateway.inspect("sbx_never")
    assert observed.state is SandboxState.TERMINATED
    assert observed.is_live is False


@pytest.mark.asyncio
async def test_the_real_gateway_turns_a_404_into_terminated():
    stub = _StubClass()
    stub.fail["get_info"] = RuntimeError("Sandbox sbx_1 not found")
    gateway = _gateway(stub)

    assert (await gateway.inspect("sbx_1")).state is SandboxState.TERMINATED


@pytest.mark.asyncio
async def test_is_live_covers_paused_not_just_running():
    """A cleanup path that skips paused sandboxes leaks them."""
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)
    await gateway.pause(sandbox_id)

    observed = await gateway.inspect(sandbox_id)

    assert observed.state is SandboxState.PAUSED
    assert observed.is_live is True


@pytest.mark.asyncio
async def test_inspect_carries_the_expiry_across_a_hibernation():
    """`end_at` is readable while paused and does not move across the pause —
    measured 2026-08-11, byte-identical to the value read at create, and a
    90 s sandbox reconnected 64 s past its nominal expiry came back intact.
    Alibaba doc 3028695 says the clock keeps running; the module docstring
    holds that argument. Either way the deadline is what a caller planning a
    long wait needs, so it is carried."""
    import datetime as dt

    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)
    deadline = dt.datetime(2026, 8, 11, 12, 0, tzinfo=dt.timezone.utc)
    gateway.set_expiry(sandbox_id, deadline)

    await gateway.pause(sandbox_id)

    assert (await gateway.inspect(sandbox_id)).expires_at == deadline


# ---------------------------------------------------------------------------
# list — recovering a sandbox whose id was lost
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_create_lost_in_transit_is_recovered_by_listing_on_metadata():
    """The hole this method was added to close. `create` failed, the id never
    reached the caller, and the sandbox is alive and billing. `inspect`
    cannot help — it needs the id that went missing. The session id the
    caller already knows is the way back in."""
    gateway = FakeSandboxGateway()
    session_id = "sess_abc123"
    gateway.fail_next("create", applied=True)

    with pytest.raises(SandboxTransportError):
        await gateway.create(
            template=TEMPLATE,
            timeout_ms=3_600_000,
            metadata={"flashml_session_id": session_id},
        )

    orphans = await gateway.list(metadata={"flashml_session_id": session_id})

    assert len(orphans) == 1
    assert orphans[0].is_live
    assert (await gateway.kill(orphans[0].sandbox_id)).state is SandboxState.TERMINATED
    assert await gateway.list(metadata={"flashml_session_id": session_id}) == []


@pytest.mark.asyncio
async def test_listing_finds_a_paused_orphan_too():
    """The property that makes this usable for orphan-hunting at all. A
    hibernated sandbox is the easiest kind to forget and it still holds its
    resources; a listing that returned only running ones would report zero
    orphans and be wrong."""
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)
    await gateway.pause(sandbox_id)

    found = await gateway.list()

    assert [o.sandbox_id for o in found] == [sandbox_id]
    assert found[0].state is SandboxState.PAUSED


@pytest.mark.asyncio
async def test_the_metadata_filter_is_a_subset_match():
    gateway = FakeSandboxGateway()
    mine = await gateway.create(
        template=TEMPLATE, timeout_ms=1000,
        metadata={"flashml_session_id": "s1", "owner": "u1"},
    )
    await gateway.create(
        template=TEMPLATE, timeout_ms=1000, metadata={"flashml_session_id": "s2"}
    )

    found = await gateway.list(metadata={"flashml_session_id": "s1"})

    assert [o.sandbox_id for o in found] == [mine.sandbox_id]


@pytest.mark.asyncio
async def test_every_pair_must_match_not_just_one():
    gateway = FakeSandboxGateway()
    await gateway.create(
        template=TEMPLATE, timeout_ms=1000, metadata={"flashml_session_id": "s1"}
    )

    assert await gateway.list(
        metadata={"flashml_session_id": "s1", "owner": "nobody"}
    ) == []


@pytest.mark.asyncio
async def test_listing_without_a_filter_returns_everything_live():
    gateway = FakeSandboxGateway()
    await _fresh(gateway)
    await _fresh(gateway)

    assert len(await gateway.list()) == 2


@pytest.mark.asyncio
async def test_a_killed_sandbox_does_not_appear_in_a_listing():
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)
    await gateway.kill(sandbox_id)

    assert await gateway.list() == []


@pytest.mark.asyncio
async def test_the_real_gateway_filters_server_side():
    """The pairs go on the request rather than being filtered out of a full
    listing here — a busy account should not have to be paged through to
    find one sandbox."""
    stub = _StubClass()
    gateway = _gateway(stub)

    await gateway.list(metadata={"flashml_session_id": "s1"})

    query = stub.calls[0][1]["query"]
    assert query is not None
    assert query.metadata == {"flashml_session_id": "s1"}


@pytest.mark.asyncio
async def test_an_unfiltered_listing_sends_no_query_and_no_state_filter():
    """With no state filter the API returns running and paused both, which is
    the behaviour the orphan hunt depends on. Sending a query we did not mean
    would risk narrowing that."""
    stub = _StubClass()
    gateway = _gateway(stub)

    await gateway.list()

    assert stub.calls[0][1]["query"] is None


@pytest.mark.asyncio
async def test_listing_drains_every_page():
    """One page is not the answer. A reconciler that stops at the first page
    concludes there are no orphans on a busy account."""
    stub = _StubClass()
    stub.pages = [
        [_StubInfo("sbx_1"), _StubInfo("sbx_2")],
        [_StubInfo("sbx_3", state="paused")],
    ]
    gateway = _gateway(stub)

    found = await gateway.list()

    assert [o.sandbox_id for o in found] == ["sbx_1", "sbx_2", "sbx_3"]
    assert found[2].state is SandboxState.PAUSED
    assert stub.paginator.drained == 2


@pytest.mark.asyncio
async def test_a_listing_that_never_terminates_is_reported_not_truncated():
    """Silently returning a partial listing is the one outcome that would
    make this method actively dangerous: the caller concludes there are no
    orphans and stops looking."""
    stub = _StubClass()
    stub.pages = [[_StubInfo(f"sbx_{i}")] for i in range(500)]
    gateway = _gateway(stub)

    with pytest.raises(SandboxTransportError) as caught:
        await gateway.list()

    assert "did not terminate" in str(caught.value)


@pytest.mark.asyncio
async def test_a_listing_of_exactly_the_page_ceiling_is_not_an_error():
    """The boundary. Draining the last allowed page and finding the cursor
    clear is success, not a truncation."""
    from flashml_cloud_api.alibaba_sandbox import MAX_LIST_PAGES

    stub = _StubClass()
    stub.pages = [[_StubInfo(f"sbx_{i}")] for i in range(MAX_LIST_PAGES)]
    gateway = _gateway(stub)

    assert len(await gateway.list()) == MAX_LIST_PAGES


@pytest.mark.asyncio
async def test_a_listing_failure_never_claims_it_may_have_applied():
    """A read changed nothing."""
    stub = _StubClass()
    stub.fail["list"] = OSError("connection reset by peer")
    gateway = _gateway(stub)

    with pytest.raises(SandboxTransportError) as caught:
        await gateway.list()

    assert caught.value.may_have_applied is False


@pytest.mark.asyncio
async def test_a_401_from_the_wrong_region_does_not_leak_the_key():
    """The key is region-scoped and 401s elsewhere, so this is a failure an
    operator will actually hit while pointing a deployment at a new region."""
    stub = _StubClass()
    stub.fail["list"] = type("AuthenticationException", (Exception,), {})(
        f"401: Unauthorized - key {API_KEY}"
    )
    gateway = _gateway(stub)

    with pytest.raises(SandboxTerminalError) as caught:
        await gateway.list()

    assert API_KEY not in str(caught.value)


@pytest.mark.asyncio
async def test_the_fake_can_fail_a_listing():
    gateway = FakeSandboxGateway()
    await _fresh(gateway)
    gateway.fail_next("list")

    with pytest.raises(SandboxTransportError):
        await gateway.list()

    assert len(await gateway.list()) == 1


# ---------------------------------------------------------------------------
# connect / wake
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_auto_resumes_a_paused_sandbox():
    """There is no resume call. `connect` is the wake path, which is why the
    Protocol has no `resume()` — adding one would invent an operation the
    platform does not have."""
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)
    await gateway.pause(sandbox_id)

    woken = await gateway.connect(sandbox_id)

    assert woken.state is SandboxState.RUNNING


@pytest.mark.asyncio
async def test_a_sandbox_that_fails_to_wake_raises_terminally():
    """Not retryable: the snapshot is gone, and hammering connect will not
    bring it back. The caller's answer is to start over, which it can only
    decide if it can tell this from a transient failure."""
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)
    await gateway.pause(sandbox_id)
    gateway.break_wake(sandbox_id)

    with pytest.raises(SandboxTerminalError):
        await gateway.connect(sandbox_id)


@pytest.mark.asyncio
async def test_connecting_to_a_terminated_sandbox_is_terminal():
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)
    await gateway.kill(sandbox_id)

    with pytest.raises(SandboxTerminalError):
        await gateway.connect(sandbox_id)


# ---------------------------------------------------------------------------
# What survives a hibernation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_filesystem_survives_a_hibernation():
    """Verified 2026-08-11: the marker file's hash matched after the wake."""
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)
    await gateway.write_file(sandbox_id, "/home/user/marker", b"sha-me")

    await gateway.pause(sandbox_id)
    await gateway.connect(sandbox_id)

    assert gateway.sandboxes[sandbox_id].files["/home/user/marker"] == b"sha-me"


@pytest.mark.asyncio
async def test_a_background_process_survives_a_hibernation():
    """Re-measured 2026-08-11, n=5: alive 5/5 after the wake, and a running
    `flashnode work` kept serving lease claims across the pause (13 → 19) —
    it resumed working, not merely existing.

    This test previously asserted the opposite, on a probe
    (`ps -p $PID && echo ALIVE || echo GONE`) that exited 127 on the missing
    `ps` and took the GONE branch unconditionally. It never tested anything.
    Nothing built on this gateway needs a checkpoint-and-restart dance around
    a pause."""
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)
    started = await gateway.run(sandbox_id, "sleep 3600", timeout_s=5, background=True)
    assert started.background_pid is not None

    await gateway.pause(sandbox_id)
    await gateway.connect(sandbox_id)

    assert gateway.sandboxes[sandbox_id].background_pids == [started.background_pid]


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_non_zero_exit_is_evidence_not_an_exception():
    """The SDK raises `CommandExitException` on any non-zero exit. Letting it
    through would send a failed evaluation script out as "the sandbox broke",
    and those two get retried very differently."""
    stub = _StubClass()
    stub.raises["pytest -q"] = _StubExit(1, stdout="1 failed", stderr="boom")
    gateway = _gateway(stub)

    evidence = await gateway.run("sbx_1", "pytest -q", timeout_s=30)

    assert isinstance(evidence, CommandEvidence)
    assert evidence.exit_code == 1
    assert evidence.ok is False
    assert evidence.stdout == "1 failed"
    assert evidence.stderr == "boom"


@pytest.mark.asyncio
async def test_a_zero_exit_is_ok():
    stub = _StubClass()
    stub.results["true"] = _StubResult(0, stdout="fine")
    gateway = _gateway(stub)

    evidence = await gateway.run("sbx_1", "true", timeout_s=5)

    assert evidence.ok is True
    assert evidence.exit_code == 0
    assert evidence.duration_ms >= 0


@pytest.mark.asyncio
async def test_a_background_command_reports_no_exit_code_and_is_not_ok():
    """Reporting 0 for a process that has not exited is a lie that reads as
    success."""
    stub = _StubClass()
    gateway = _gateway(stub)

    evidence = await gateway.run(
        "sbx_1", "python train.py", timeout_s=5, background=True
    )

    assert evidence.exit_code is None
    assert evidence.ok is False
    assert evidence.background_pid == 4242


@pytest.mark.asyncio
async def test_an_unreachable_sandbox_is_an_error_not_a_failed_command():
    stub = _StubClass()
    stub.raises["true"] = OSError("connection reset by peer")
    gateway = _gateway(stub)

    with pytest.raises(SandboxTransportError):
        await gateway.run("sbx_1", "true", timeout_s=5)


@pytest.mark.asyncio
async def test_command_output_is_truncated_rather_than_stored_whole():
    """This lands in the event ledger. An unbounded capture is a way to put a
    gigabyte of somebody's training log into Postgres with a `print` in a
    loop."""
    stub = _StubClass()
    stub.results["spew"] = _StubResult(0, stdout="x" * 200_000)
    gateway = _gateway(stub)

    evidence = await gateway.run("sbx_1", "spew", timeout_s=5)

    assert evidence.truncated is True
    assert len(evidence.stdout) < 200_000
    assert "truncated" in evidence.stdout


@pytest.mark.asyncio
async def test_run_reuses_the_connection_rather_than_waking_each_time():
    """A wake is a measured 1085 ms (p50). Without the cache a twenty-command
    evaluation pays nineteen extra ones."""
    stub = _StubClass()
    gateway = _gateway(stub)

    await gateway.run("sbx_1", "true", timeout_s=5)
    await gateway.run("sbx_1", "true", timeout_s=5)

    assert [name for name, _ in stub.calls].count("connect") == 1


@pytest.mark.asyncio
async def test_run_wakes_a_paused_sandbox_as_a_side_effect():
    """It needs a connection, and connecting resumes. Worth knowing: it puts
    the sandbox back on billed running time."""
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)
    await gateway.pause(sandbox_id)

    await gateway.run(sandbox_id, "echo hi", timeout_s=5)

    assert (await gateway.inspect(sandbox_id)).state is SandboxState.RUNNING


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_file_writes_and_then_sets_the_mode():
    """Two calls, because the SDK's `files.write` has no mode parameter."""
    stub = _StubClass()
    gateway = _gateway(stub)

    await gateway.write_file("sbx_1", "/home/user/config.json", b"{}", mode=0o600)

    assert stub.written["/home/user/config.json"] == b"{}"
    assert ("run", "chmod 600 /home/user/config.json") in stub.log


@pytest.mark.asyncio
async def test_write_file_defaults_to_a_restrictive_mode():
    stub = _StubClass()
    gateway = _gateway(stub)

    await gateway.write_file("sbx_1", "/home/user/f", b"x")

    assert DEFAULT_FILE_MODE == 0o600
    assert ("run", "chmod 600 /home/user/f") in stub.log


@pytest.mark.asyncio
async def test_write_file_quotes_the_path_for_the_chmod():
    """The path is ours today, but a command built by concatenation is where
    an injection arrives the day something else supplies the value."""
    stub = _StubClass()
    gateway = _gateway(stub)

    await gateway.write_file("sbx_1", "/tmp/a b; rm -rf /", b"x")

    chmods = [cmd for kind, cmd in stub.log if kind == "run"]
    assert chmods == ["chmod 600 '/tmp/a b; rm -rf /'"]


@pytest.mark.asyncio
async def test_write_file_refuses_a_path_with_a_newline_or_a_nul():
    stub = _StubClass()
    gateway = _gateway(stub)

    for path in ("/tmp/a\nb", "/tmp/a\x00b"):
        with pytest.raises(SandboxTerminalError):
            await gateway.write_file("sbx_1", path, b"x")
    assert stub.written == {}


@pytest.mark.asyncio
async def test_a_failed_chmod_is_reported_rather_than_ignored():
    """Silently leaving a file world-readable is worse than failing."""
    stub = _StubClass()
    stub.raises["chmod 600 /home/user/f"] = _StubExit(1, stderr="permission denied")
    gateway = _gateway(stub)

    with pytest.raises(SandboxTerminalError):
        await gateway.write_file("sbx_1", "/home/user/f", b"x")


@pytest.mark.asyncio
async def test_the_fake_records_the_bytes_and_the_mode():
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)

    await gateway.write_file(sandbox_id, "/home/user/f", b"payload", mode=0o644)

    assert gateway.sandboxes[sandbox_id].files["/home/user/f"] == b"payload"
    assert gateway.sandboxes[sandbox_id].modes["/home/user/f"] == 0o644


# ---------------------------------------------------------------------------
# No SDK objects escape
# ---------------------------------------------------------------------------


def test_the_returned_values_are_frozen_and_carry_only_scalars():
    """SDK responses carry envd access tokens and fields we have no story
    for. What comes out of this module is small, typed and immutable, so
    nothing downstream can persist a struct that gains a credential in a
    minor release."""
    import dataclasses

    for cls in (SandboxObservation, CommandEvidence):
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen is True
        for f in dataclasses.fields(cls):
            assert not re.search(r"dict|Any|Mapping", str(f.type)), (cls, f.name)


@pytest.mark.asyncio
async def test_an_observation_cannot_be_mutated_after_the_fact():
    gateway = FakeSandboxGateway()
    observed = await gateway.create(template=TEMPLATE, timeout_ms=1000, metadata={})

    with pytest.raises(Exception):
        observed.state = SandboxState.TERMINATED  # type: ignore[misc]


# ---------------------------------------------------------------------------
# The fake's own contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_fake_records_every_call_in_order():
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)
    await gateway.pause(sandbox_id)
    await gateway.kill(sandbox_id)

    assert [op for op, _ in gateway.calls] == ["create", "pause", "kill"]


@pytest.mark.asyncio
async def test_an_injected_fault_is_armed_for_exactly_one_call():
    """Otherwise a test asserting a retry succeeds would pass against a
    gateway that never recovered."""
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)
    gateway.fail_next("inspect")

    with pytest.raises(SandboxTransportError):
        await gateway.inspect(sandbox_id)

    assert (await gateway.inspect(sandbox_id)).state is SandboxState.RUNNING


@pytest.mark.asyncio
async def test_the_fake_can_inject_each_error_class():
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)

    for kind, expected in (
        ("transport", SandboxTransportError),
        ("terminal", SandboxTerminalError),
        ("allowlist", SandboxAllowlistError),
        ("unavailable", SandboxUnavailable),
    ):
        gateway.fail_next("inspect", kind=kind)
        with pytest.raises(expected):
            await gateway.inspect(sandbox_id)


@pytest.mark.asyncio
async def test_the_fake_can_script_a_command_result():
    gateway = FakeSandboxGateway()
    sandbox_id = await _fresh(gateway)
    gateway.command_results["evaluate"] = (2, "acc=0.91", "warning")

    evidence = await gateway.run(sandbox_id, "evaluate", timeout_s=5)

    assert evidence.exit_code == 2
    assert evidence.ok is False
    assert evidence.stdout == "acc=0.91"


def test_the_fake_repr_carries_no_credential():
    gateway = FakeSandboxGateway(api_key="not-a-real-key-but-still")
    assert "not-a-real-key-but-still" not in repr(gateway)
