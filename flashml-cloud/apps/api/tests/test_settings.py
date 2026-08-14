"""Scheme normalization for `COORDINATOR_URL`.

Render's Blueprint (`render.yaml`) resolves `COORDINATOR_URL` via
`fromService: {type: pserv, property: hostport}`, which yields a bare
`host:port` — never a scheme. Passed straight through, `CoordinatorClient`
hands that to httpx verbatim and every agent call (register, claim,
complete, artifact upload) raises `UnsupportedProtocol`, while `/healthz`
stays green because it never touches the coordinator. That combination —
deploy succeeds, first real request fails — is exactly what these tests
pin: not just the string `Settings.coordinator_url` ends up holding, but
the actual request URL httpx sends.
"""
from __future__ import annotations

import httpx
import pytest

from flashml_cloud_api.app import CoordinatorClient
from flashml_cloud_api.settings import Settings, _with_default_scheme

REQUIRED_ENV = {
    "SUPABASE_URL": "https://yualksqjjvlfscbbsygq.supabase.co",
    "SUPABASE_SERVICE_KEY": "service-key-not-used-here",
    "COORDINATOR_OPERATOR_TOKEN": "op-secret",
}


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    # Deliberately absent: SUPABASE_JWT_SECRET. It is legacy and optional
    # now that the project signs ES256 and the API verifies against JWKS.
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)


# ---------------------------------------------------------------------------
# Which Supabase inputs are mandatory
# ---------------------------------------------------------------------------


def test_a_jwt_secret_is_not_required_when_auth_is_on(monkeypatch):
    """Our project rotated to ECC (P-256); there is no shared secret to set.
    Demanding one would refuse to boot the API over a value that no longer
    exists."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COORDINATOR_URL", "flashml-coordinator:10000")

    settings = Settings.from_env()

    assert settings.require_auth is True
    assert settings.supabase_jwt_secret == ""


def test_a_jwt_secret_is_still_carried_through_when_one_is_set(monkeypatch):
    """Legacy/self-hosted projects: the secret must still reach `Settings`,
    since it is what keeps not-yet-expired HS256 tokens verifiable."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COORDINATOR_URL", "flashml-coordinator:10000")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "legacy-shared-secret")

    assert Settings.from_env().supabase_jwt_secret == "legacy-shared-secret"


def test_missing_supabase_url_still_fails_loudly_when_auth_is_required(monkeypatch):
    """SUPABASE_URL is the mandatory Supabase input now — the JWKS is derived
    from it, so without it nothing can be verified. A missing one must not
    degrade silently."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COORDINATOR_URL", "flashml-coordinator:10000")
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        Settings.from_env()


# ---------------------------------------------------------------------------
# _with_default_scheme — the string-level unit
# ---------------------------------------------------------------------------


def test_scheme_less_url_gets_default_scheme_prepended():
    assert (
        _with_default_scheme("flashml-coordinator:10000", "http")
        == "http://flashml-coordinator:10000"
    )


def test_http_scheme_left_exactly_as_is():
    assert _with_default_scheme("http://coordinator.internal:8100", "http") == (
        "http://coordinator.internal:8100"
    )


def test_https_scheme_left_exactly_as_is_even_with_a_different_default():
    # Default is http (the coordinator case), but an explicit https:// must
    # never be overridden by it.
    assert _with_default_scheme("https://coordinator.example", "http") == (
        "https://coordinator.example"
    )


def test_empty_value_handled_as_it_is_today():
    assert _with_default_scheme("", "http") == ""


# ---------------------------------------------------------------------------
# Settings.from_env — the boundary where COORDINATOR_URL is read
# ---------------------------------------------------------------------------


def test_from_env_prepends_http_to_a_scheme_less_coordinator_url(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COORDINATOR_URL", "flashml-coordinator:10000")

    settings = Settings.from_env()

    assert settings.coordinator_url == "http://flashml-coordinator:10000"


def test_from_env_leaves_an_explicit_http_scheme_unchanged(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COORDINATOR_URL", "http://coordinator.internal:8100")

    settings = Settings.from_env()

    assert settings.coordinator_url == "http://coordinator.internal:8100"


def test_from_env_leaves_an_explicit_https_scheme_unchanged(monkeypatch):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COORDINATOR_URL", "https://coordinator.example")

    settings = Settings.from_env()

    assert settings.coordinator_url == "https://coordinator.example"


def test_from_env_still_strips_a_trailing_slash_once_it_reaches_the_client(
    monkeypatch,
):
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COORDINATOR_URL", "flashml-coordinator:10000/")

    settings = Settings.from_env()
    client = CoordinatorClient(settings)

    assert client._bases["render"] == "http://flashml-coordinator:10000"


def test_missing_coordinator_url_still_fails_loudly_when_auth_is_required(
    monkeypatch,
):
    _set_required_env(monkeypatch)
    monkeypatch.delenv("COORDINATOR_URL", raising=False)

    with pytest.raises(RuntimeError, match="COORDINATOR_URL"):
        Settings.from_env()


# ---------------------------------------------------------------------------
# Alibaba ECS — the venue that spends real money
# ---------------------------------------------------------------------------

ECS_ENV = {
    "ECS_ACCESS_KEY_ID": "LTAI-not-a-real-key",
    "ECS_ACCESS_KEY_SECRET": "not-a-real-secret",
    "ECS_IMAGE_ID": "m-gpu-image",
    "ECS_SECURITY_GROUP_ID": "sg-1",
    "ECS_VSWITCH_ID": "vsw-1",
}


def _set_ecs_env(monkeypatch: pytest.MonkeyPatch, **over: str) -> None:
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COORDINATOR_URL", "flashml-coordinator:10000")
    for key, value in {**ECS_ENV, **over}.items():
        if value:
            monkeypatch.setenv(key, value)
        else:
            monkeypatch.delenv(key, raising=False)


def test_an_unconfigured_deployment_can_rent_nothing(monkeypatch):
    """The default, and it must stay the default: no credentials, no
    provider, and `capacity/registry.providers_for` returns `{}`. This is
    the whole safety story for every deploy that is not renting."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COORDINATOR_URL", "flashml-coordinator:10000")
    for key in ECS_ENV:
        monkeypatch.delenv(key, raising=False)

    assert Settings.from_env().ecs_configured is False


def test_the_ecs_block_is_read_from_the_environment(monkeypatch):
    """A typo in one of these names is invisible: the deployment simply
    cannot rent, and says so only in a startup warning."""
    _set_ecs_env(monkeypatch)
    monkeypatch.setenv("ECS_REGION", "ap-southeast-1")
    monkeypatch.setenv("ECS_ZONE_ID", "ap-southeast-1a")
    monkeypatch.setenv("ECS_INSTANCE_TYPE", "ecs.gn6i-c4g1.xlarge")
    monkeypatch.setenv("ECS_SYSTEM_DISK_GB", "200")
    monkeypatch.setenv("ECS_BOOTSTRAP_URL", "https://cdn.example/bootstrap.sh")

    settings = Settings.from_env()

    assert settings.ecs_configured is True
    assert settings.ecs_region == "ap-southeast-1"
    assert settings.ecs_zone_id == "ap-southeast-1a"
    assert settings.ecs_instance_type == "ecs.gn6i-c4g1.xlarge"
    assert settings.ecs_system_disk_gb == 200
    assert settings.ecs_bootstrap_url == "https://cdn.example/bootstrap.sh"
    # The measured default, so a deployment that names no instance type gets
    # the one whose $1.279/hr price the design was argued against.
    monkeypatch.delenv("ECS_INSTANCE_TYPE")
    assert Settings.from_env().ecs_instance_type == "ecs.gn6i-c4g1.xlarge"


def test_a_half_configured_venue_reads_as_off(monkeypatch):
    """All-or-nothing, like the sandbox and the GitHub App. A provider that
    cannot authenticate answers "I could not destroy it" for ever, which is
    worse than having no adapter at all."""
    _set_ecs_env(monkeypatch, ECS_VSWITCH_ID="")

    assert Settings.from_env().ecs_configured is False


def test_zero_egress_bandwidth_falls_back_rather_than_being_honoured(
    monkeypatch,
):
    """Unlike the spend ceilings, where an explicit `0` is the emergency
    stop, `0` here is never what an operator wants: an instance with no
    public IP cannot reach this API, can never enrol, and bills until the
    reconciler destroys it."""
    _set_ecs_env(monkeypatch)
    monkeypatch.setenv("ECS_INTERNET_MBPS", "0")

    assert Settings.from_env().ecs_internet_mbps == 10


def test_the_secret_is_not_in_the_settings_repr(monkeypatch):
    """These credentials create and delete machines that bill. `Settings`
    lands in logs and tracebacks."""
    _set_ecs_env(monkeypatch)

    settings = Settings.from_env()

    assert "not-a-real-secret" not in repr(settings)


# ---------------------------------------------------------------------------
# Qwen (DashScope) model provider — the key is the whole gate
# ---------------------------------------------------------------------------


def test_an_unconfigured_deployment_needs_no_model_key():
    """`qwen_configured` is gated on the key alone — `qwen_model`,
    `qwen_base_url` and `qwen_region` all carry usable defaults, so a
    deployment that never set `DASHSCOPE_API_KEY` must read as unconfigured
    with no other field needing to be touched, and the (absent) key must
    still never surface in a repr."""
    s = Settings(
        supabase_url="https://example.supabase.co",
        supabase_service_key="",
        coordinator_url="http://coordinator",
        coordinator_operator_token="op",
        require_auth=True,
        qwen_api_key="",
    )

    assert s.qwen_configured is False
    assert "sk-" not in repr(s)


# ---------------------------------------------------------------------------
# CoordinatorClient — the resulting request URL, not just the string
# ---------------------------------------------------------------------------


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        return httpx.Response(200, json={"ok": True})


@pytest.mark.asyncio
async def test_scheme_less_coordinator_url_still_produces_a_usable_request(
    monkeypatch,
):
    """The end-to-end regression: without normalization this raises
    `httpx.UnsupportedProtocol` on the first real call, exactly the failure
    mode `/healthz` cannot catch."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COORDINATOR_URL", "flashml-coordinator:10000")
    settings = Settings.from_env()

    transport = RecordingTransport()
    client = CoordinatorClient(settings, transport=transport)

    response = await client.forward("GET", "/v1alpha1/jobs")

    assert response.status_code == 200
    sent = transport.requests[-1]
    assert str(sent.url) == "http://flashml-coordinator:10000/v1alpha1/jobs"
    assert sent.url.scheme == "http"
