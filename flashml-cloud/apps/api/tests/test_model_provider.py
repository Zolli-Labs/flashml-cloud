"""The `ModelProvider` Protocol, its fake, and the Qwen (DashScope) gateway.

Nothing here reaches DashScope. As with `test_alibaba_sandbox.py`, two seams
do the work:

- `FakeModelProvider` keeps real state (a call log, scripted replies), so a
  caller-side test is about behaviour rather than mock call counts.
- `QwenModelProvider(generation_cls=...)` takes a stub standing in for
  `dashscope.Generation`, so the production code paths — the async wrapper,
  the exception classification, the redaction — are exercised against the
  real implementation without the `dashscope` wheel installed. The API venv
  does not have it; the import in `model_provider.py` is lazy, and this file
  must not import it either.
"""
from __future__ import annotations

import time

import pytest

from flashml_cloud_api.model_provider import (
    DEFAULT_OP_TIMEOUT_S,
    FakeModelProvider,
    ModelError,
    ModelProvider,
    ModelReply,
    ModelTerminalError,
    ModelTransportError,
    ModelUnavailable,
    QwenModelProvider,
)
from flashml_cloud_api.settings import Settings


def _shaped(prefix: str, length: int) -> str:
    """Build a credential-SHAPED value without writing one down.

    See `test_alibaba_sandbox.py::_shaped` for why this is assembled at run
    time rather than written as a literal: a scanner reads source text, and a
    string that exists only once the module is imported is not in it.
    """
    return prefix + "".join(chr(ord("a") + i % 26) for i in range(length))


API_KEY = _shaped("sk-", 40)


def _settings(**overrides) -> Settings:
    base = dict(
        supabase_url="https://example.supabase.co",
        supabase_service_key="",
        coordinator_url="http://coordinator",
        coordinator_operator_token="op",
        require_auth=True,
        qwen_api_key=API_KEY,
        qwen_model="qwen-plus",
        qwen_base_url="https://dashscope.aliyuncs.com/api/v1",
        qwen_region="cn-beijing",
    )
    base.update(overrides)
    return Settings(**base)


# ---------------------------------------------------------------------------
# Stubs standing in for the SDK
# ---------------------------------------------------------------------------


class _StubResponse:
    """Shaped like `dashscope`'s `GenerationResponse`: attribute access on
    `status_code`, `code`, `message`, `output`, `usage`."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        output: dict | None = None,
        usage: dict | None = None,
        code: str = "",
        message: str = "",
    ):
        self.status_code = status_code
        self.request_id = "req_stub"
        self.code = code
        self.message = message
        self.output = output or {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ok"},
                }
            ]
        }
        self.usage = usage or {"input_tokens": 3, "output_tokens": 5}


class _StubGeneration:
    """Stands in for `dashscope.Generation`: a `.call(**kwargs)` entry point,
    called the same way whether the real class or this stub backs it."""

    def __init__(self):
        self.log: list[dict] = []

    def call(self, **kwargs):
        self.log.append(kwargs)
        return _StubResponse()


def _raises_auth() -> object:
    """A stub whose `.call` raises the way the SDK is entitled to: with the
    rejected credential echoed back in the message. `model_provider.py` must
    still keep it out of anything that reaches a caller."""

    class _Stub:
        def call(self, **kwargs):
            raise RuntimeError(
                f"401: Unauthorized - api_key={kwargs.get('api_key')}"
            )

    return _Stub()


def _hangs() -> object:
    """A stub whose `.call` blocks — long enough that `asyncio.wait_for`'s
    ceiling is reached first regardless of thread scheduling."""

    class _Stub:
        def call(self, **kwargs):
            time.sleep(0.3)
            return _StubResponse()

    return _Stub()


def _provider(stub=None, **overrides) -> QwenModelProvider:
    settings = _settings(**overrides)
    return QwenModelProvider(
        api_key=settings.qwen_api_key,
        base_url=settings.qwen_base_url,
        model=settings.qwen_model,
        region=settings.qwen_region,
        generation_cls=stub if stub is not None else _StubGeneration(),
    )


# ---------------------------------------------------------------------------
# The Protocol
# ---------------------------------------------------------------------------


def test_both_providers_satisfy_the_protocol():
    assert isinstance(FakeModelProvider(), ModelProvider)
    assert isinstance(_provider(), ModelProvider)      # the real one, with stubs injected


def test_from_settings_refuses_a_deployment_with_no_key():
    with pytest.raises(ModelUnavailable):
        QwenModelProvider.from_settings(_settings(qwen_api_key=""))


def test_the_key_is_not_in_the_repr():
    p = _provider(qwen_api_key=_shaped("sk-", 40))
    assert _shaped("sk-", 40) not in repr(p)


@pytest.mark.asyncio
async def test_the_key_never_reaches_an_exception_message():
    secret = _shaped("sk-", 40)
    p = _provider(qwen_api_key=secret, stub=_raises_auth())
    with pytest.raises(ModelError) as ei:
        await p.complete([{"role": "user", "content": "hi"}], model="qwen-plus")
    assert secret not in str(ei.value)


@pytest.mark.asyncio
async def test_the_fake_returns_scripted_replies_and_logs_calls():
    f = FakeModelProvider(replies=["hello"])
    r = await f.complete([{"role": "user", "content": "x"}], model="qwen-plus")
    assert r.text == "hello" and r.latency_ms >= 40
    assert f.calls == [("complete", "qwen-plus")]


@pytest.mark.asyncio
async def test_a_timeout_becomes_transport_error():
    p = _provider(stub=_hangs())
    with pytest.raises(ModelTransportError):
        await p.complete([{"role": "user", "content": "x"}], model="qwen-plus", timeout_s=0)


def test_the_fake_is_never_the_default_anywhere():
    """`FakeModelProvider` is tests-only. Nothing else in the package may
    import it — a fake reachable from a route is a route that silently
    succeeds against a model that was never called.

    Excludes `model_provider.py` itself, which is where the class is
    DEFINED and therefore the one file that legitimately contains its name
    — the same exclusion `test_alibaba_sandbox.py` makes for
    `alibaba_sandbox.py` and `FakeSandboxGateway`.

    Detects a *code* reference — an import, a bare name, or an attribute
    access — via the AST, not a raw substring. A neighbouring module that
    merely NAMES the fake in a docstring (e.g. `agent_loop.py` explaining
    that its own tests use it) is documentation, not a wiring, and must not
    trip this. Docstrings and string literals are `ast.Constant` nodes, so
    the walk below never sees them.
    """
    import ast
    import pathlib

    def references_fake_in_code(path: pathlib.Path) -> bool:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "FakeModelProvider":
                return True
            if isinstance(node, ast.Attribute) and node.attr == "FakeModelProvider":
                return True
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "FakeModelProvider" for alias in node.names
            ):
                return True
        return False

    pkg = pathlib.Path(__file__).resolve().parents[1] / "flashml_cloud_api"
    hits = [
        p.name
        for p in sorted(pkg.rglob("*.py"))
        if p.name != "model_provider.py" and references_fake_in_code(p)
    ]
    assert hits == []


# ---------------------------------------------------------------------------
# Extra coverage beyond the seven headline tests: cheap and load-bearing
# ---------------------------------------------------------------------------


def test_an_unconfigured_deployment_raises_rather_than_half_working():
    with pytest.raises(ModelUnavailable):
        QwenModelProvider(api_key="", base_url="https://dashscope.aliyuncs.com/api/v1")


@pytest.mark.asyncio
async def test_the_fake_can_be_made_to_fail_on_demand():
    f = FakeModelProvider()
    f.fail_next("complete", kind=ModelTerminalError, message="refused")
    with pytest.raises(ModelTerminalError):
        await f.complete([{"role": "user", "content": "x"}], model="qwen-plus")
    # Armed for exactly one call.
    r = await f.complete([{"role": "user", "content": "x"}], model="qwen-plus")
    assert isinstance(r, ModelReply)


@pytest.mark.asyncio
async def test_a_successful_reply_carries_usage_and_latency():
    p = _provider()
    r = await p.complete([{"role": "user", "content": "hi"}], model="qwen-plus")
    assert r.text == "ok"
    assert r.prompt_tokens == 3
    assert r.completion_tokens == 5
    assert r.finish_reason == "stop"
    assert r.latency_ms >= 0


@pytest.mark.asyncio
async def test_a_provider_error_response_becomes_a_terminal_error_not_an_exception_leak():
    """DashScope's own convention: a rejected key comes back as a normal
    response with a non-200 `status_code`, not a raised exception. That must
    still classify into one of ours, and the key must still never appear."""
    secret = _shaped("sk-", 40)

    class _Stub:
        def call(self, **kwargs):
            return _StubResponse(
                status_code=401,
                code="InvalidApiKey",
                message=f"Invalid API-key provided: {kwargs.get('api_key')}",
            )

    p = _provider(qwen_api_key=secret, stub=_Stub())
    with pytest.raises(ModelTerminalError) as ei:
        await p.complete([{"role": "user", "content": "hi"}], model="qwen-plus")
    assert secret not in str(ei.value)


def test_default_op_timeout_matches_the_sandbox_convention():
    assert DEFAULT_OP_TIMEOUT_S == 30.0
