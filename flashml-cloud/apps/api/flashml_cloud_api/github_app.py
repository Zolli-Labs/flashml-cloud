"""Acting as the FlashML GitHub App, so a submitter's private repo can be read.

Two hops. First we prove we are the App by signing a short-lived RS256 JWT
with the App's private key. Then we exchange that for an *installation*
token — scoped to one account's installation, valid an hour, and the only
credential that ever touches a repository.

**No long-lived user credential is stored anywhere.** That is the whole
argument for a GitHub App over an OAuth `repo` token: what we hold is a
number (`installation_id`) that is useless without our private key, and the
person who installed can revoke it from GitHub without asking us.

The token cache is per-instance and in memory. A restart re-mints, which
costs one round trip; the alternative — persisting a live credential to the
database — would be storing exactly the thing this design exists to avoid.
"""
from __future__ import annotations

import datetime as dt
import logging
import urllib.parse
from typing import Callable

import httpx
import jwt

from .settings import Settings

log = logging.getLogger("flashml-cloud-api")

GITHUB_API = "https://api.github.com"

#: GitHub refuses an App JWT whose `exp` is more than 10 minutes out. Nine
#: leaves room for the backdated `iat` below without crossing that line.
_JWT_LIFETIME = dt.timedelta(minutes=9)

#: `iat` is backdated because GitHub validates it against *their* clock. A
#: JWT issued "in the future" by a second is rejected outright, and a host
#: whose clock runs slightly fast would otherwise fail every mint.
_JWT_BACKDATE = dt.timedelta(seconds=60)

#: Re-mint this long before expiry rather than at it. A token that passes our
#: check and expires in flight fails the submitter's job for a reason they
#: cannot see or act on.
_EXPIRY_MARGIN = dt.timedelta(seconds=60)


class GitHubAppError(Exception):
    """A mint that failed, carrying WHY in a form the route can map to an
    answer. The three kinds mean genuinely different things:

    - ``uninstalled`` — the App is no longer installed. The person must
      reconnect. Their fault to fix, ours to explain.
    - ``misconfigured`` — our App id or key is wrong. Never the submitter's
      fault; it must not be reported to them as a problem with their repo.
    - ``unavailable`` — GitHub could not be reached. Transient; telling
      someone to reconnect a working installation sends them to fix
      something that is not broken.
    """

    def __init__(self, message: str, kind: str):
        super().__init__(message)
        self.kind = kind


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class GitHubApp:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
        now: Callable[[], dt.datetime] = _utcnow,
    ):
        self._app_id = settings.github_app_id
        self._slug = settings.github_app_slug
        self._private_key = settings.github_app_private_key
        self._configured = settings.github_app_configured
        self._transport = transport
        self._timeout = httpx.Timeout(timeout, connect=min(timeout, 5.0))
        self._now = now
        #: installation_id -> (token, expires_at)
        self._cache: dict[int, tuple[str, dt.datetime]] = {}

    @property
    def configured(self) -> bool:
        return self._configured

    def install_url(self, state: str) -> str:
        """Where to send someone to install the App.

        `state` is quoted rather than concatenated. It is ours today, but a
        URL built by concatenation is where an injection arrives the day
        something else starts supplying the value.
        """
        return (
            f"https://github.com/apps/{self._slug}/installations/new"
            f"?state={urllib.parse.quote(state, safe='')}"
        )

    def app_jwt(self) -> str:
        """A short-lived assertion that we are this App."""
        now = self._now()
        return jwt.encode(
            {
                "iss": self._app_id,
                "iat": int((now - _JWT_BACKDATE).timestamp()),
                "exp": int((now + _JWT_LIFETIME).timestamp()),
            },
            self._private_key,
            algorithm="RS256",
        )

    async def installation_token(self, installation_id: int) -> str:
        """A token that reads exactly one installation's repositories.

        Raises ``GitHubAppError`` and never returns a stale or empty token:
        a caller that got a string back can use it.
        """
        if not self._configured:
            # Before any network call — an unconfigured deploy must not
            # generate outbound traffic on a route it cannot serve.
            raise GitHubAppError(
                "the GitHub App is not configured on this deployment",
                kind="misconfigured",
            )

        cached = self._cache.get(installation_id)
        if cached is not None:
            token, expires_at = cached
            if self._now() + _EXPIRY_MARGIN < expires_at:
                return token

        url = f"{GITHUB_API}/app/installations/{installation_id}/access_tokens"
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.app_jwt()}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
        except Exception as exc:
            # Deliberately broader than httpx.HTTPError, matching Mailer:
            # httpx.InvalidURL, httpx.CookieConflict and httpx.StreamError
            # all sit outside that hierarchy and would escape otherwise.
            raise GitHubAppError(
                f"could not reach GitHub: {exc}", kind="unavailable"
            ) from exc

        if response.status_code == 404:
            raise GitHubAppError(
                f"installation {installation_id} not found — the App was "
                f"probably uninstalled",
                kind="uninstalled",
            )
        if response.status_code in (401, 403):
            # Logged, because this one is ours to fix and nobody else will
            # report it. The body is not logged: it can echo the request.
            log.error(
                "GitHub refused our App credentials (HTTP %s) minting a token "
                "for installation %s",
                response.status_code,
                installation_id,
            )
            raise GitHubAppError(
                "GitHub refused this deployment's App credentials",
                kind="misconfigured",
            )
        if response.status_code >= 300:
            raise GitHubAppError(
                f"GitHub returned HTTP {response.status_code} minting an "
                f"installation token",
                kind="unavailable",
            )

        try:
            body = response.json()
            token = body["token"]
            expires_at = _parse_expiry(body["expires_at"])
        except Exception as exc:
            raise GitHubAppError(
                f"GitHub returned an unreadable token response: {exc}",
                kind="unavailable",
            ) from exc

        self._cache[installation_id] = (token, expires_at)
        return token


def _parse_expiry(value: str) -> dt.datetime:
    """GitHub sends RFC3339 with a `Z`, which `fromisoformat` only learned
    to read in 3.11. Normalising keeps this working on the 3.12 venv today
    without depending on that having been the case all along."""
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed
