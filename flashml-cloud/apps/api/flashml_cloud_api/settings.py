"""Environment configuration for the FlashML Cloud API.

Fails loudly at startup when auth is required and a secret is missing,
rather than silently running open and failing at the first request. This
mirrors the coordinator's ``FLASHML_REQUIRE_NODE_AUTH`` guard.

Note that ``SUPABASE_JWT_SECRET`` is NOT among those required secrets:
modern Supabase projects sign with asymmetric ES256 keys and have no shared
secret to hand out. ``SUPABASE_URL`` is the mandatory input instead, because
the public keys are fetched from it (see ``auth.jwks_url``).
"""
from __future__ import annotations

import logging
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
    supabase_service_key: str
    coordinator_url: str
    coordinator_operator_token: str
    require_auth: bool
    #: LEGACY, and optional. Only projects still issuing HS256 tokens from a
    #: shared secret need this. Our project rotated to ECC (P-256) and every
    #: newly-issued token is ES256, verified against the JWKS public key —
    #: which is why this is not in the require_auth check below. Left set, it
    #: keeps not-yet-expired tokens signed by the PREVIOUS key working
    #: through a rotation.
    supabase_jwt_secret: str = ""
    database_url: str = ""
    #: Public base URL of the browser console, used to build the
    #: `verification_uri` a machine prints during device-code enrolment.
    #: Deliberately NOT in the require_auth missing-secret check: refusing to
    #: boot over a display string would take the whole API down for it.
    #:
    #: It is not, however, merely cosmetic when unset, which is what this
    #: comment used to claim. The old reasoning was that a relative
    #: "/activate" still lets "a human find the page from whatever host they
    #: are on" — but the consumer of this string is `flashnode login`,
    #: printing into a TERMINAL on a volunteer's own laptop. There is no
    #: current host there. Hands-on QA on 2026-08-04 hit exactly that: the
    #: agent printed "Approve at: /activate" and the operator had no way to
    #: know which host to put in front of it. Unset is a real, if
    #: non-fatal, defect — hence the startup warning in `from_env`.
    console_url: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        require_auth = os.environ.get("FLASHML_REQUIRE_AUTH", "true").strip().lower() not in (
            "0", "false", "no", "off", "",
        )

        # Mandatory when auth is on: the JWKS the API verifies browser tokens
        # against is derived from it.
        supabase_url = os.environ.get("SUPABASE_URL", "")
        # Optional — see the field comment. Absent on any project migrated to
        # asymmetric signing keys.
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
                    # SUPABASE_JWT_SECRET is deliberately absent from this
                    # list. It is legacy and optional; SUPABASE_URL is what
                    # verification actually needs now, and it must still be
                    # a hard startup failure.
                    #
                    # SUPABASE_SERVICE_KEY is deliberately absent too, for a
                    # different reason: NOTHING READS IT. The API reaches
                    # Postgres directly over libpq (db.connect, DATABASE_URL)
                    # and verifies browser tokens against the project JWKS —
                    # it never calls PostgREST or the Auth Admin API, which
                    # are the only things a service-role key is for. Making a
                    # never-used credential mandatory pressures whoever
                    # deploys this into copying the one key that bypasses
                    # every RLS policy and every ownership check in db.py
                    # into one more system, for nothing. The field stays on
                    # Settings as the seam for a future Storage/Admin-API
                    # caller; the day something actually reads it, add it
                    # back here.
                    ("SUPABASE_URL", supabase_url),
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

            # Warn, do not refuse: enrolment still works, but every machine
            # that runs `flashnode login` against this deploy is told to
            # "Approve at: /activate" — a path with no host, printed into a
            # terminal. See the field comment above.
            if not console_url:
                logging.getLogger("flashml-cloud-api").warning(
                    "FLASHML_CONSOLE_URL is unset: device-code enrolment will "
                    "print a relative /activate path that a volunteer cannot "
                    "resolve. Set it to the console's public base URL."
                )

        return settings
