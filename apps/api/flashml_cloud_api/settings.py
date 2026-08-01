"""Environment configuration for the FlashML Cloud API.

Fails loudly at startup when auth is required and a secret is missing,
rather than silently running open and failing at the first request. This
mirrors the coordinator's ``FLASHML_REQUIRE_NODE_AUTH`` guard.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _with_default_scheme(url: str, scheme: str) -> str:
    """Prepend ``scheme://`` to `url` when it has none.

    Render's Blueprint ``fromService: {property: hostport}`` returns a bare
    ``host:port`` (e.g. ``flashml-coordinator:10000``) for COORDINATOR_URL —
    never a scheme, because a private-service hostport isn't a URL as far as
    Render is concerned. Passed straight to httpx that raises
    ``UnsupportedProtocol`` on every outbound call (register, claim,
    complete, artifact upload) even though ``/healthz`` looks fine, since it
    never touches the coordinator. This is the single place that value
    enters the process, so this is where it gets normalized rather than at
    each call site. An already-scheme'd value (``http://`` or ``https://``)
    and an empty value both pass through unchanged.
    """
    if not url or "://" in url:
        return url
    return f"{scheme}://{url}"


@dataclass
class Settings:
    supabase_url: str
    supabase_jwt_secret: str
    supabase_service_key: str
    coordinator_url: str
    coordinator_operator_token: str
    require_auth: bool
    database_url: str = ""
    #: Public base URL of the browser console, used to build the
    #: `verification_uri` a machine prints during device-code enrolment.
    #: Deliberately NOT in the require_auth missing-secret check: an empty
    #: value degrades to a relative path (a human can still find the page
    #: from whatever host they are on), which is a cosmetic problem, not a
    #: security one, and refusing to boot over it would take the whole API
    #: down for a display string.
    console_url: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        require_auth = os.environ.get("FLASHML_REQUIRE_AUTH", "true").strip().lower() not in (
            "0", "false", "no", "off", "",
        )

        supabase_url = os.environ.get("SUPABASE_URL", "")
        supabase_jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "")
        supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        # http, not https: Render private-service traffic (COORDINATOR_URL
        # above) is plain HTTP on the internal network, which does not
        # terminate TLS — forcing https would fail the handshake outright.
        coordinator_url = _with_default_scheme(
            os.environ.get("COORDINATOR_URL", ""), "http"
        )
        coordinator_operator_token = os.environ.get("COORDINATOR_OPERATOR_TOKEN", "")
        # Standard libpq connection string/URI for the Postgres database
        # (see flashml_cloud_api.db). Not included in the require_auth
        # missing-secret check below: browser/machine auth can be verified
        # without a DB connection (e.g. in unit tests), so a deploy that is
        # only missing this should not be treated the same as one missing a
        # signing secret. db.connect() raises its own clear error when this
        # is absent and a connection is actually requested.
        database_url = os.environ.get("DATABASE_URL", "")
        console_url = os.environ.get("FLASHML_CONSOLE_URL", "")

        settings = cls(
            supabase_url=supabase_url,
            supabase_jwt_secret=supabase_jwt_secret,
            supabase_service_key=supabase_service_key,
            coordinator_url=coordinator_url,
            coordinator_operator_token=coordinator_operator_token,
            require_auth=require_auth,
            database_url=database_url,
            console_url=console_url,
        )

        if require_auth:
            missing = [
                name
                for name, value in (
                    ("SUPABASE_URL", supabase_url),
                    ("SUPABASE_JWT_SECRET", supabase_jwt_secret),
                    ("SUPABASE_SERVICE_KEY", supabase_service_key),
                    ("COORDINATOR_URL", coordinator_url),
                    ("COORDINATOR_OPERATOR_TOKEN", coordinator_operator_token),
                )
                if not value
            ]
            if missing:
                raise RuntimeError(
                    "Missing required settings while FLASHML_REQUIRE_AUTH is on: "
                    + ", ".join(missing)
                    + ". Refusing to start with auth silently disabled."
                )

        return settings
