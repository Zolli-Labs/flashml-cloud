"""Device-code login for a developer's CLI: turning a program into a
caller that acts as its owner.

A SIBLING OF enrolment.py, NOT AN EXTENSION OF IT
-------------------------------------------------
Both flows mint a short user_code a human types at /activate, and they
share the ``device_codes`` table so that code is unique across both. They
share nothing else. ``enrolment.approve_device_code`` is three pages of
node_id uniqueness reasoning — a machine identity is globally unique,
survives revocation, and must never be adoptable by a second account. A
CLI credential has no equivalent of any of that: it is one of many a
person may hold, it grants exactly its owner's access, and revoking it
disturbs nothing else. Folding the two into one function would mean every
reader of either has to hold both sets of rules.

The flow, spelled out because the security properties live in the order of
operations:

1. The CLI calls ``start_cli_code`` and gets a long ``device_code`` (for
   itself) and a short ``user_code`` (for a human). Neither identifies
   anyone yet.
2. A signed-in person approves the user_code in the console. This is the
   only place ``owner_id`` enters the flow, and it comes from the verified
   JWT ``sub`` — never from a request body.
3. The CLI polls ``redeem_cli_code``. Only once approval has happened does
   this return a token, and it returns the raw token *exactly once* — the
   raw value is never persisted, only its hash, so after that one response
   it is gone even from the database's point of view.

No FastAPI here — pure functions the app layer wraps into HTTP responses.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import psycopg

from flashml_cloud_api import db as dbmod
from flashml_cloud_api.auth import hash_user_token, new_user_token
from flashml_cloud_api.db import CliCredential
from flashml_cloud_api.enrolment import (
    POLL_INTERVAL_SECONDS,
    USER_CODE_ALPHABET,
    USER_CODE_INSERT_ATTEMPTS,
    USER_CODE_LENGTH,
)

#: The same ten minutes ``enrolment.DEVICE_CODE_TTL`` gives a machine. A
#: person approving a CLI login is doing the same thing at the same desk.
CLI_CODE_TTL = timedelta(minutes=10)

#: How much of the raw token is kept in the clear, for the console to show
#: so a person can tell two credentials apart. Matches what
#: ``enrolment.redeem_device_code`` keeps for a machine.
TOKEN_PREFIX_CHARS = 12


class CliCodeError(Exception):
    """Base class for CLI-login failures the app layer must turn into a
    clean HTTP response — never let one surface as an unhandled 500."""


class CliCodeNotFound(CliCodeError):
    """No CLI device_codes row matches this user_code. A code belonging to
    the *machine* flow raises this too: the flows share a table, and one
    must not be approvable through the other's path."""


class CliCodeExpired(CliCodeError):
    """The code existed but its ten-minute window passed unapproved."""


def _new_user_code() -> str:
    return "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(USER_CODE_LENGTH))


def _new_device_code() -> str:
    return secrets.token_urlsafe(32)


def start_cli_code(db: psycopg.Connection, label: str | None) -> dict:
    """Issue a fresh device_code/user_code pair for a CLI that wants to log
    in. Nobody is authenticated yet, and ``label`` is only a claim — it is
    display text, never read by any authorization decision."""
    device_code = _new_device_code()
    expires_at = datetime.now(timezone.utc) + CLI_CODE_TTL

    user_code = ""
    for _ in range(USER_CODE_INSERT_ATTEMPTS):
        candidate = _new_user_code()
        try:
            dbmod.insert_cli_device_code(
                db,
                device_code=device_code,
                user_code=candidate,
                label=label,
                expires_at=expires_at,
            )
        except psycopg.errors.UniqueViolation:
            # user_code collision (astronomically unlikely at 32**8) or, in
            # principle, a device_code collision. Retry with fresh random
            # values rather than surfacing a 500. Note the uniqueness is
            # across BOTH kinds, which is the whole reason one table.
            device_code = _new_device_code()
            continue
        user_code = candidate
        break
    else:
        raise CliCodeError("could not allocate a unique device code")

    return {
        "device_code": device_code,
        "user_code": user_code,
        "expires_at": expires_at,
        "interval": POLL_INTERVAL_SECONDS,
    }


def approve_cli_code(db: psycopg.Connection, user_code: str, user_id: str) -> str:
    """A signed-in user approves a code they read off their own terminal.
    Returns the credential's id. Raises rather than ever minting a
    credential nobody approved."""
    row = dbmod.fetch_device_code_by_user_code(db, user_code)
    if row is None or row.get("kind") != "cli":
        # A machine code folds into "not found" here on purpose. Telling a
        # caller "that code is real but belongs to the other flow" is a fact
        # they can do nothing with and a guesser can.
        raise CliCodeNotFound(user_code)

    if row["credential_id"] is not None:
        # Already approved — approving twice must not mint a second
        # credential, so this is a no-op returning the existing id.
        return str(row["credential_id"])

    if row["expires_at"] <= datetime.now(timezone.utc):
        raise CliCodeExpired(user_code)

    # Ownership is established here and nowhere else. The label comes from
    # the code row (what the CLI reported about itself), never from the
    # approver's request body.
    with db.transaction():
        credential_id = dbmod.insert_cli_credential(
            db, owner_id=user_id, label=row["hostname"]
        )
        dbmod.mark_cli_device_code_approved(db, user_code, user_id, credential_id)
    return credential_id


def redeem_cli_code(db: psycopg.Connection, device_code: str) -> str | None:
    """The CLI exchanges its device_code for a token. Returns the raw token
    exactly once: the atomic claim in
    ``claim_cli_device_code_for_redemption`` ensures a second call — or a
    concurrent one — gets None instead of a second copy. Returns None
    (never raises) for an unknown, unapproved, expired, or already-redeemed
    code, all indistinguishably."""
    credential_id = dbmod.claim_cli_device_code_for_redemption(db, device_code)
    if credential_id is None:
        return None

    token = new_user_token()
    dbmod.set_cli_credential_token(
        db, credential_id, hash_user_token(token), token[:TOKEN_PREFIX_CHARS]
    )
    return token


def authenticate_cli(
    db: psycopg.Connection, token: str | None
) -> CliCredential | None:
    """Resolve a token to the credential it belongs to. Returns None
    immediately for an unknown token or a revoked credential — revocation
    flips ``status`` in the row this reads, so it takes effect on the very
    next request; there is no cache to expire and no refresh to wait for.
    ``fmu_`` tokens do not expire on their own."""
    if not token:
        return None
    row = dbmod.fetch_cli_credential_by_token_hash(db, hash_user_token(token))
    if row is None:
        return None
    if row["status"] == "revoked":
        return None
    dbmod.touch_cli_credential_last_used(db, str(row["id"]))
    return CliCredential(
        id=str(row["id"]),
        owner_id=str(row["owner_id"]),
        label=row["label"],
        status=row["status"],
        created_at=row.get("created_at"),
        revoked_at=row.get("revoked_at"),
    )
