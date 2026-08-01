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
    "SUPABASE_JWT_SECRET": "test-jwt-secret-long-enough-for-hs256",
    "SUPABASE_SERVICE_KEY": "service-key-not-used-here",
    "COORDINATOR_OPERATOR_TOKEN": "op-secret",
}


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)


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

    assert client._base == "http://flashml-coordinator:10000"


def test_missing_coordinator_url_still_fails_loudly_when_auth_is_required(
    monkeypatch,
):
    _set_required_env(monkeypatch)
    monkeypatch.delenv("COORDINATOR_URL", raising=False)

    with pytest.raises(RuntimeError, match="COORDINATOR_URL"):
        Settings.from_env()


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
