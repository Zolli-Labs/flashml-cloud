"""Minting a GitHub App installation token.

Two hops, and the first one is pure crypto: sign a short-lived RS256 JWT as
the App, hand it to GitHub, get back a token that reads one installation's
repositories for an hour.

Nothing here reaches GitHub. The JWT is verified against the PUBLIC half of a
keypair generated in this process — so a wrong algorithm, a wrong issuer or a
wrong key fails the assertion rather than passing on a token nobody checked.
The HTTP hop goes through `httpx.MockTransport`, which also lets the tests
count requests, which is how the cache is proven to be a cache.
"""
from __future__ import annotations

import datetime as dt

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from flashml_cloud_api.github_app import GitHubApp, GitHubAppError
from flashml_cloud_api.settings import Settings

APP_ID = "123456"
SLUG = "flashml"
INSTALLATION_ID = 42


def _keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


PRIVATE_PEM, PUBLIC_PEM = _keypair()


def _settings(**overrides) -> Settings:
    base = dict(
        supabase_url="https://example.supabase.co",
        supabase_service_key="",
        coordinator_url="http://coordinator",
        coordinator_operator_token="op",
        require_auth=True,
        github_app_id=APP_ID,
        github_app_slug=SLUG,
        github_app_private_key=PRIVATE_PEM,
    )
    base.update(overrides)
    return Settings(**base)


class _Recorder:
    """A MockTransport that records every request and answers from a queue."""

    def __init__(self, responses: list[httpx.Response]):
        self.requests: list[httpx.Request] = []
        self._responses = list(responses)

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if not self._responses:
                raise AssertionError(
                    f"unexpected extra request to {request.url}"
                )
            return self._responses.pop(0)

        return httpx.MockTransport(handler)


def _token_response(token: str, expires_at: dt.datetime) -> httpx.Response:
    return httpx.Response(
        201,
        json={
            "token": token,
            "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )


# ---------------------------------------------------------------------------
# The App JWT
# ---------------------------------------------------------------------------


def test_app_jwt_is_signed_with_the_app_key_and_names_the_app():
    app = GitHubApp(_settings())

    token = app.app_jwt()
    claims = jwt.decode(token, PUBLIC_PEM, algorithms=["RS256"])

    assert claims["iss"] == APP_ID


def test_app_jwt_is_rejected_by_a_different_key():
    """Pins that the signature is real rather than the payload merely being
    shaped right — a test that only decoded without verification would pass
    on an unsigned token."""
    other_public = _keypair()[1]
    app = GitHubApp(_settings())

    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(app.app_jwt(), other_public, algorithms=["RS256"])


def test_app_jwt_backdates_iat_and_expires_inside_githubs_ceiling():
    """GitHub refuses a JWT whose `exp` is more than 10 minutes out, and one
    whose `iat` is in the future by its clock — which is a different clock
    from ours. The backdate is the standard allowance for that skew."""
    now = dt.datetime(2026, 8, 10, 12, 0, 0, tzinfo=dt.timezone.utc)
    app = GitHubApp(_settings(), now=lambda: now)

    claims = jwt.decode(
        app.app_jwt(),
        PUBLIC_PEM,
        algorithms=["RS256"],
        options={"verify_exp": False},
    )

    issued = dt.datetime.fromtimestamp(claims["iat"], tz=dt.timezone.utc)
    expires = dt.datetime.fromtimestamp(claims["exp"], tz=dt.timezone.utc)
    assert issued < now
    assert expires > now
    assert (expires - now) < dt.timedelta(minutes=10)


# ---------------------------------------------------------------------------
# Minting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_installation_token_posts_to_the_access_tokens_endpoint():
    now = dt.datetime(2026, 8, 10, 12, 0, 0, tzinfo=dt.timezone.utc)
    recorder = _Recorder([_token_response("ghs_abc", now + dt.timedelta(hours=1))])
    app = GitHubApp(_settings(), transport=recorder.transport(), now=lambda: now)

    token = await app.installation_token(INSTALLATION_ID)

    assert token == "ghs_abc"
    (request,) = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == (
        f"https://api.github.com/app/installations/{INSTALLATION_ID}/access_tokens"
    )
    # The App JWT, not the installation token: this is the hop that proves
    # we are the App. Verified rather than merely present.
    #
    # `verify_exp` off because this test freezes the clock at a fixed instant
    # to pin the request, so the JWT's `exp` is relative to that instant and
    # not to now. The expiry window has its own test above, against the same
    # frozen clock, which is where that property belongs.
    bearer = request.headers["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(
        bearer, PUBLIC_PEM, algorithms=["RS256"], options={"verify_exp": False}
    )
    assert claims["iss"] == APP_ID


@pytest.mark.asyncio
async def test_a_second_call_inside_the_window_does_not_re_mint():
    """A submit would otherwise pay a GitHub round trip per job for a token
    that is valid for an hour."""
    now = dt.datetime(2026, 8, 10, 12, 0, 0, tzinfo=dt.timezone.utc)
    clock = {"now": now}
    recorder = _Recorder([_token_response("ghs_abc", now + dt.timedelta(hours=1))])
    app = GitHubApp(
        _settings(), transport=recorder.transport(), now=lambda: clock["now"]
    )

    first = await app.installation_token(INSTALLATION_ID)
    clock["now"] = now + dt.timedelta(minutes=30)
    second = await app.installation_token(INSTALLATION_ID)

    assert first == second == "ghs_abc"
    assert len(recorder.requests) == 1


@pytest.mark.asyncio
async def test_an_expiring_token_is_re_minted_before_it_expires():
    """Re-minted at the margin, not at expiry: a token that is valid when
    checked and expired when GitHub reads it fails a job for no reason the
    submitter can act on."""
    now = dt.datetime(2026, 8, 10, 12, 0, 0, tzinfo=dt.timezone.utc)
    clock = {"now": now}
    expires = now + dt.timedelta(hours=1)
    recorder = _Recorder(
        [
            _token_response("ghs_first", expires),
            _token_response("ghs_second", expires + dt.timedelta(hours=1)),
        ]
    )
    app = GitHubApp(
        _settings(), transport=recorder.transport(), now=lambda: clock["now"]
    )

    assert await app.installation_token(INSTALLATION_ID) == "ghs_first"
    clock["now"] = expires - dt.timedelta(seconds=30)
    assert await app.installation_token(INSTALLATION_ID) == "ghs_second"
    assert len(recorder.requests) == 2


@pytest.mark.asyncio
async def test_two_installations_do_not_share_a_cache_entry():
    """A token is scoped to ONE installation. Serving installation B the
    token minted for A would read A's repositories on B's behalf — a
    cross-tenant read, caused by a cache key."""
    now = dt.datetime(2026, 8, 10, 12, 0, 0, tzinfo=dt.timezone.utc)
    expires = now + dt.timedelta(hours=1)
    recorder = _Recorder(
        [_token_response("ghs_a", expires), _token_response("ghs_b", expires)]
    )
    app = GitHubApp(_settings(), transport=recorder.transport(), now=lambda: now)

    assert await app.installation_token(1) == "ghs_a"
    assert await app.installation_token(2) == "ghs_b"


# ---------------------------------------------------------------------------
# Failure classes — each means something different to the person submitting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_404_reports_the_app_as_uninstalled():
    recorder = _Recorder([httpx.Response(404, json={"message": "Not Found"})])
    app = GitHubApp(_settings(), transport=recorder.transport())

    with pytest.raises(GitHubAppError) as caught:
        await app.installation_token(INSTALLATION_ID)

    assert caught.value.kind == "uninstalled"


@pytest.mark.asyncio
async def test_a_401_reports_our_own_misconfiguration():
    """Our key is wrong. This is never the submitter's fault and must not be
    reported to them as though their repo were the problem."""
    recorder = _Recorder([httpx.Response(401, json={"message": "Bad credentials"})])
    app = GitHubApp(_settings(), transport=recorder.transport())

    with pytest.raises(GitHubAppError) as caught:
        await app.installation_token(INSTALLATION_ID)

    assert caught.value.kind == "misconfigured"


@pytest.mark.asyncio
async def test_a_transport_failure_is_unavailable_not_uninstalled():
    """GitHub being unreachable must not be reported as "you uninstalled
    the App" — that sends the person to reconnect something that is fine."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    app = GitHubApp(_settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(GitHubAppError) as caught:
        await app.installation_token(INSTALLATION_ID)

    assert caught.value.kind == "unavailable"


# ---------------------------------------------------------------------------
# Unconfigured
# ---------------------------------------------------------------------------


def test_an_unconfigured_app_reports_itself_unconfigured():
    app = GitHubApp(
        _settings(github_app_id="", github_app_slug="", github_app_private_key="")
    )
    assert app.configured is False


@pytest.mark.asyncio
async def test_an_unconfigured_app_refuses_to_mint_without_a_network_call():
    recorder = _Recorder([])  # any request at all raises
    app = GitHubApp(
        _settings(github_app_id="", github_app_slug="", github_app_private_key=""),
        transport=recorder.transport(),
    )

    with pytest.raises(GitHubAppError) as caught:
        await app.installation_token(INSTALLATION_ID)

    assert caught.value.kind == "misconfigured"
    assert recorder.requests == []


def test_install_url_carries_the_slug_and_the_state():
    app = GitHubApp(_settings())
    url = app.install_url("st_abc123")
    assert url == (
        f"https://github.com/apps/{SLUG}/installations/new?state=st_abc123"
    )


def test_install_url_percent_encodes_the_state():
    """The state is ours to generate, but building a URL by concatenation is
    how an injection gets in later when something else starts supplying it."""
    app = GitHubApp(_settings())
    assert "a%2Bb%26c" in app.install_url("a+b&c")
