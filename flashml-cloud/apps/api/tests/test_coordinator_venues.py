"""Two control planes, one client.

`CoordinatorClient` used to hold exactly one base URL and one operator
token. It now holds a pair per VENUE: `render` (the incumbent Render
`type: pserv` private service) and `fc` (an Alibaba Function Compute Web
Function running the same coordinator). A job is served by one of them, and
which one is a per-request choice made by the caller.

Three properties are worth more than the plumbing and are what this module
pins:

  1. **The default did not move.** Every call site written before there was
     a second venue passes no `venue=` and must produce byte-identical
     requests to the ones it produced yesterday.
  2. **A venue selects a PAIR.** The likely bug is not "wrong URL" — it is
     the FC URL carrying the Render operator token, which leaks the
     credential for the private control plane to a public endpoint and
     fails in a way that looks like an FC misconfiguration. So every venue
     test asserts the URL *and* the Authorization header.
  3. **Unconfigured refuses; it never falls back.** The second venue exists
     to be measured against the first. An FC-labelled job that silently ran
     on Render would make that measurement a lie with nothing downstream
     able to tell.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from flashml_cloud_api import app as appmod
from flashml_cloud_api.app import (
    COORDINATOR_VENUES,
    CoordinatorClient,
    CoordinatorVenueError,
)
from flashml_cloud_api.settings import Settings

RENDER_URL = "http://flashml-coordinator:10000"
RENDER_TOKEN = "render-operator-token"
FC_URL = "https://coordinator-abc.fcapp.run"
FC_TOKEN = "fc-operator-token"


def _settings(**over) -> Settings:
    base = dict(
        supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
        supabase_service_key="service-key-not-used-here",
        coordinator_url=RENDER_URL,
        coordinator_operator_token=RENDER_TOKEN,
        require_auth=True,
    )
    base.update(over)
    return Settings(**base)


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        return httpx.Response(200, json={"ok": True})


# ---------------------------------------------------------------------------
# 1. The default venue is exactly what it was
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_call_with_no_venue_still_goes_to_render_with_the_render_token():
    """No existing call site passes `venue=`. All of them must keep working,
    unchanged, against the same host with the same credential."""
    transport = RecordingTransport()
    client = CoordinatorClient(
        _settings(coordinator_url_fc=FC_URL, coordinator_operator_token_fc=FC_TOKEN),
        transport=transport,
    )

    response = await client.forward("GET", "/v1alpha1/jobs")

    assert response.status_code == 200
    sent = transport.requests[-1]
    assert str(sent.url) == f"{RENDER_URL}/v1alpha1/jobs"
    assert sent.headers["authorization"] == f"Bearer {RENDER_TOKEN}"


@pytest.mark.asyncio
async def test_the_default_holds_even_when_fc_is_the_only_other_thing_configured():
    """Configuring the second venue must not make it the default by
    accident. `venue` has to be asked for."""
    transport = RecordingTransport()
    client = CoordinatorClient(
        _settings(coordinator_url_fc=FC_URL, coordinator_operator_token_fc=FC_TOKEN),
        transport=transport,
    )

    await client.forward(
        "POST", "/v1alpha1/nodes/register", on_behalf_of="node-abc", content=b"{}"
    )

    sent = transport.requests[-1]
    assert sent.url.host == "flashml-coordinator"
    assert sent.headers["authorization"] == f"Bearer {RENDER_TOKEN}"


# ---------------------------------------------------------------------------
# 2. The FC venue takes the FC base AND the FC token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_fc_venue_uses_the_fc_base_and_the_fc_token():
    """Both, in one assertion pair. The FC URL with the Render token is the
    bug this test exists for: it would hand the private control plane's
    operator credential to a public endpoint."""
    transport = RecordingTransport()
    client = CoordinatorClient(
        _settings(coordinator_url_fc=FC_URL, coordinator_operator_token_fc=FC_TOKEN),
        transport=transport,
    )

    response = await client.forward("GET", "/v1alpha1/jobs", venue="fc")

    assert response.status_code == 200
    sent = transport.requests[-1]
    assert str(sent.url) == f"{FC_URL}/v1alpha1/jobs"
    assert sent.headers["authorization"] == f"Bearer {FC_TOKEN}"
    assert RENDER_TOKEN not in sent.headers["authorization"]


@pytest.mark.asyncio
async def test_a_trailing_slash_on_the_fc_url_does_not_double_up():
    """Same normalization the render base has had since the Blueprint
    started supplying one."""
    transport = RecordingTransport()
    client = CoordinatorClient(
        _settings(
            coordinator_url_fc=f"{FC_URL}/", coordinator_operator_token_fc=FC_TOKEN
        ),
        transport=transport,
    )

    await client.forward("GET", "/v1alpha1/jobs", venue="fc")

    assert str(transport.requests[-1].url) == f"{FC_URL}/v1alpha1/jobs"


@pytest.mark.asyncio
async def test_the_venue_does_not_change_which_headers_are_built():
    """The security property is per-class, not per-venue: headers are
    constructed here from scratch, and delegation is emitted exactly once
    whichever control plane is being addressed."""
    transport = RecordingTransport()
    client = CoordinatorClient(
        _settings(coordinator_url_fc=FC_URL, coordinator_operator_token_fc=FC_TOKEN),
        transport=transport,
    )

    await client.forward(
        "POST",
        "/v1alpha1/leases/claim",
        on_behalf_of="node-abc",
        content=b"{}",
        media_type="application/json",
        venue="fc",
    )

    sent = transport.requests[-1]
    assert sent.headers.get_list(appmod.DELEGATION_HEADER) == ["node-abc"]
    assert sent.headers["content-type"] == "application/json"


# ---------------------------------------------------------------------------
# 3. Unconfigured refuses — it does not fall back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unconfigured_fc_venue_raises_rather_than_using_render():
    """The one that matters most. A silent fallback would run an
    FC-labelled job on Render and make the whole comparison worthless."""
    transport = RecordingTransport()
    client = CoordinatorClient(_settings(), transport=transport)

    with pytest.raises(CoordinatorVenueError, match="COORDINATOR_URL_FC"):
        await client.forward("GET", "/v1alpha1/jobs", venue="fc")

    # Nothing left the process. Not "went somewhere harmless" — nothing.
    assert transport.requests == []


@pytest.mark.asyncio
async def test_an_fc_url_without_its_token_refuses_rather_than_sending_an_empty_bearer():
    """The mirror of the test above, and the likelier deploy mistake: the
    URL gets pasted in and the token does not. Sending `Bearer ` would come
    back 401 from the far end, which reads like a wrong credential rather
    than a missing one — on the newest, least familiar control plane."""
    transport = RecordingTransport()
    client = CoordinatorClient(
        _settings(coordinator_url_fc=FC_URL), transport=transport
    )

    with pytest.raises(CoordinatorVenueError, match="COORDINATOR_OPERATOR_TOKEN_FC"):
        await client.forward("GET", "/v1alpha1/jobs", venue="fc")

    assert transport.requests == []


@pytest.mark.asyncio
async def test_the_default_venue_still_allows_an_empty_token_for_local_dev():
    """`./scripts/dev.sh --all` runs a coordinator with no tokens set at
    all, which `authenticator_from_env` answers with an OpenAuthenticator.
    The empty-token refusal must not reach that, or it breaks the standard
    local loop."""
    transport = RecordingTransport()
    client = CoordinatorClient(
        _settings(coordinator_operator_token=""), transport=transport
    )

    await client.forward("GET", "/v1alpha1/jobs")

    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_an_fc_token_without_an_fc_url_is_still_unconfigured():
    """Half-configured reads as off, like every other optional venue in
    `settings.py`. A token with nowhere to send it is not a control plane."""
    client = CoordinatorClient(
        _settings(coordinator_operator_token_fc=FC_TOKEN), transport=RecordingTransport()
    )

    assert client.venue_configured("fc") is False
    with pytest.raises(CoordinatorVenueError):
        await client.forward("GET", "/v1alpha1/jobs", venue="fc")


@pytest.mark.asyncio
async def test_an_unknown_venue_is_refused_by_name():
    """A typo must not resolve to the default either, for the same reason."""
    client = CoordinatorClient(
        _settings(coordinator_url_fc=FC_URL, coordinator_operator_token_fc=FC_TOKEN),
        transport=RecordingTransport(),
    )

    with pytest.raises(CoordinatorVenueError, match="unknown coordinator venue"):
        await client.forward("GET", "/v1alpha1/jobs", venue="Render")


def test_the_configured_check_agrees_with_the_venue_list():
    client = CoordinatorClient(
        _settings(coordinator_url_fc=FC_URL, coordinator_operator_token_fc=FC_TOKEN)
    )

    assert set(COORDINATOR_VENUES) == {"render", "fc"}
    assert all(client.venue_configured(v) for v in COORDINATOR_VENUES)
    assert client.venue_configured("nope") is False


# ---------------------------------------------------------------------------
# Settings — the environment boundary
# ---------------------------------------------------------------------------

REQUIRED_ENV = {
    "SUPABASE_URL": "https://yualksqjjvlfscbbsygq.supabase.co",
    "SUPABASE_SERVICE_KEY": "service-key-not-used-here",
    "COORDINATOR_URL": "flashml-coordinator:10000",
    "COORDINATOR_OPERATOR_TOKEN": "op-secret",
}


def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    for key in ("COORDINATOR_URL_FC", "COORDINATOR_OPERATOR_TOKEN_FC"):
        monkeypatch.delenv(key, raising=False)


def test_an_unset_fc_coordinator_still_loads_and_still_boots(monkeypatch):
    """The default deploy. No FC function exists yet for most environments,
    and one that has never heard of the venue must behave exactly as it did
    before it was added — not warn, not degrade, and above all not refuse to
    start."""
    _set_required_env(monkeypatch)

    settings = Settings.from_env()

    assert settings.coordinator_url_fc == ""
    assert settings.coordinator_operator_token_fc == ""
    assert settings.fc_coordinator_configured is False
    # And the real entrypoint still produces an app.
    assert isinstance(appmod.create_app(), FastAPI)


def test_a_scheme_less_fc_url_becomes_https_not_http(monkeypatch):
    """The opposite default to COORDINATOR_URL, deliberately. That one is a
    private-network hostport with no TLS; this one is a public function URL,
    and http here would send the operator token in the clear."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COORDINATOR_URL_FC", "coordinator-abc.fcapp.run")

    settings = Settings.from_env()

    assert settings.coordinator_url_fc == "https://coordinator-abc.fcapp.run"
    # The render side is untouched by that choice.
    assert settings.coordinator_url == "http://flashml-coordinator:10000"


def test_an_explicit_scheme_on_the_fc_url_is_left_alone(monkeypatch):
    """Including http, for a local FC emulator: normalization fills a gap,
    it does not overrule an operator who said what they meant."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COORDINATOR_URL_FC", "http://127.0.0.1:9000")

    assert Settings.from_env().coordinator_url_fc == "http://127.0.0.1:9000"


def test_the_fc_block_is_read_from_the_environment(monkeypatch):
    """A typo in either name is otherwise invisible — the venue simply
    cannot be addressed, and says so only when a job asks for it."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COORDINATOR_URL_FC", FC_URL)
    monkeypatch.setenv("COORDINATOR_OPERATOR_TOKEN_FC", FC_TOKEN)

    settings = Settings.from_env()

    assert settings.coordinator_url_fc == FC_URL
    assert settings.coordinator_operator_token_fc == FC_TOKEN
    assert settings.fc_coordinator_configured is True


def test_the_fc_operator_token_is_not_in_the_settings_repr(monkeypatch):
    """Same treatment as every other credential: `Settings` lands in logs
    and tracebacks."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COORDINATOR_URL_FC", FC_URL)
    monkeypatch.setenv("COORDINATOR_OPERATOR_TOKEN_FC", FC_TOKEN)

    assert FC_TOKEN not in repr(Settings.from_env())


def test_a_half_configured_fc_venue_warns_but_does_not_refuse_to_boot(
    monkeypatch, caplog
):
    """A URL with no token collects 401s from a real coordinator. Worth a
    warning; not worth taking the API down, since the default venue — which
    is every current route — is unaffected."""
    _set_required_env(monkeypatch)
    monkeypatch.setenv("COORDINATOR_URL_FC", FC_URL)

    with caplog.at_level("WARNING"):
        settings = Settings.from_env()

    assert settings.coordinator_url_fc == FC_URL
    assert "COORDINATOR_OPERATOR_TOKEN_FC" in caplog.text
