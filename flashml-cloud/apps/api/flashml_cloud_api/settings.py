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

import base64
import binascii
import logging
import os
from dataclasses import dataclass


def _decode_pem(value: str) -> str:
    """Return an RSA private key PEM from either of the two forms it
    legitimately arrives in.

    The GitHub App private key is multi-line. Render's dashboard accepts
    newlines in an env var; a `.env` file does not, and the three-place
    map in `.env.dev.example` exists precisely because values drift when
    one place cannot hold what another can. Base64 is the one encoding
    that survives every place this has to live, so both are accepted and
    normalised here — the single point the value enters the process.

    A value that is neither is returned UNCHANGED rather than mangled.
    `github_app.py` then fails with "not a valid private key", which
    names the actual problem; a forced base64 decode of a typo produces
    binary noise and an error about padding.

    A PEM is returned byte-for-byte, trailing newline included. Stripping
    a credential to tidy it is how a valid value becomes an invalid one —
    the whitespace is only removed for the base64 *attempt*, where
    `validate=True` would reject it.
    """
    if "-----BEGIN" in value:
        return value
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return value
    return decoded if "-----BEGIN" in decoded else value


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
    #: Resend API key. Optional: an unconfigured deploy must still boot and
    #: serve — the failure mode of a missing mail provider is a silent
    #: product, not a dead API, so this is deliberately NOT in the
    #: require_auth missing-secret check. Same reasoning as `console_url`,
    #: which warns rather than refusing.
    resend_api_key: str = ""
    #: The From address, e.g. "FlashML <no-reply@mail.zolliai.com>". Mail is
    #: sent only when this AND `resend_api_key` are both set.
    email_from: str = ""
    #: Reply-to. Falls back to `email_from`. The declined email invites a
    #: reply (re-applying is refused by design — POST /access-request 409s
    #: once decided), so this should be a monitored mailbox.
    email_reply_to: str = ""
    #: GitHub App credentials, for reading a submitter's PRIVATE repos. All
    #: three or none — see `github_app_configured`. Optional for the same
    #: reason as `resend_api_key` above: an unconfigured deploy must still
    #: boot and serve, because public-repo submission is the whole product
    #: for everyone who has not connected GitHub.
    github_app_id: str = ""
    #: The App's URL slug, used only to build the install redirect
    #: (`https://github.com/apps/<slug>/installations/new`).
    github_app_slug: str = ""
    #: The App's RSA private key, PEM. Accepted base64-encoded as well —
    #: see `_decode_pem`.
    github_app_private_key: str = ""

    @property
    def github_app_configured(self) -> bool:
        """All three present. Deliberately all-or-nothing.

        A half-configured App can mint no token, so reporting it as
        configured would have the console render a Connect button that
        leads a person through granting us access to their code and then
        fails. Off is the safe direction to round to.
        """
        return bool(
            self.github_app_id
            and self.github_app_slug
            and self.github_app_private_key
        )

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
        resend_api_key = os.environ.get("RESEND_API_KEY", "")
        email_from = os.environ.get("EMAIL_FROM", "")
        email_reply_to = os.environ.get("EMAIL_REPLY_TO", "")
        github_app_id = os.environ.get("GITHUB_APP_ID", "").strip()
        github_app_slug = os.environ.get("GITHUB_APP_SLUG", "").strip()
        # NOT stripped before the call: a PEM's trailing newline is part of
        # the credential, and `_decode_pem` strips only for its base64 probe.
        github_app_private_key = _decode_pem(
            os.environ.get("GITHUB_APP_PRIVATE_KEY", "")
        )

        settings = cls(
            supabase_url=supabase_url,
            supabase_jwt_secret=supabase_jwt_secret,
            supabase_service_key=supabase_service_key,
            coordinator_url=coordinator_url,
            coordinator_operator_token=coordinator_operator_token,
            require_auth=require_auth,
            database_url=database_url,
            console_url=console_url,
            resend_api_key=resend_api_key,
            email_from=email_from,
            email_reply_to=email_reply_to,
            github_app_id=github_app_id,
            github_app_slug=github_app_slug,
            github_app_private_key=github_app_private_key,
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

            # Warn, do not refuse. Half-configured mail is the case worth
            # naming: a key with no From address (or the reverse) looks
            # configured in the dashboard and sends nothing, so approvals go
            # back to being silent with no signal anywhere.
            if bool(resend_api_key) != bool(email_from):
                logging.getLogger("flashml-cloud-api").warning(
                    "Mail is half-configured: RESEND_API_KEY and EMAIL_FROM "
                    "must both be set. No approval or decline email will be "
                    "sent until they are."
                )

            # Same shape, same signal. A partly-set App reads as OFF (see
            # `github_app_configured`), so without this it is off with no
            # explanation — and the person who set two of three variables
            # is precisely the one who believes it is on.
            github_app_values = (
                github_app_id,
                github_app_slug,
                github_app_private_key,
            )
            if any(github_app_values) and not all(github_app_values):
                logging.getLogger("flashml-cloud-api").warning(
                    "The GitHub App is half-configured: GITHUB_APP_ID, "
                    "GITHUB_APP_SLUG and GITHUB_APP_PRIVATE_KEY must all be "
                    "set. Private-repo submission stays off until they are."
                )

        return settings
