"""Device-code enrolment: turning a machine into an authenticated worker.

The flow, spelled out because the security properties live in the order
of operations, not just the individual queries:

1. The machine calls ``start_device_code`` and gets back a long
   ``device_code`` (for itself) and a short ``user_code`` (for a human to
   type into a browser). Neither identifies anyone yet.
2. A signed-in person reads the user_code off the machine's terminal and
   calls ``approve_device_code``. This is the only place ``owner_id``
   enters the flow, and it comes from the verified JWT ``sub`` — never
   from the request body.
3. The machine polls ``redeem_device_code`` with its device_code. Only
   once approval has happened does this return a token, and it returns
   the raw token *exactly once* — the raw value is never persisted, only
   its hash, so after that one response it is gone even from the
   database's point of view.

No FastAPI here — pure functions the app layer wraps into HTTP responses.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import psycopg

from flashml_cloud_api import db as dbmod
from flashml_cloud_api.auth import hash_machine_token, new_machine_token
from flashml_cloud_api.db import Machine

# Unambiguous alphabet: no O/0 or I/1, which are hard to tell apart when
# read off a terminal and typed into a phone.
USER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
USER_CODE_LENGTH = 8
USER_CODE_INSERT_ATTEMPTS = 5

DEVICE_CODE_TTL = timedelta(minutes=10)
POLL_INTERVAL_SECONDS = 5


class EnrolmentError(Exception):
    """Base class for enrolment failures the app layer must turn into a
    clean HTTP response — never let one of these surface as an unhandled
    500, and never let the underlying DB integrity error do so either."""


class DeviceCodeNotFound(EnrolmentError):
    """No device_codes row matches this user_code."""


class DeviceCodeExpired(EnrolmentError):
    """The code existed but its 10-minute window has passed and it was
    never approved."""


class NodeAlreadyEnrolled(EnrolmentError):
    """node_id is already bound to a different machine. Refused rather
    than allowed, or a new enrolment could impersonate one that already
    exists — see machines.node_id's unique constraint."""


def _new_user_code() -> str:
    return "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(USER_CODE_LENGTH))


def _new_device_code() -> str:
    return secrets.token_urlsafe(32)


def start_device_code(
    db: psycopg.Connection,
    node_id: str,
    hostname: str | None,
    platform: str | None,
    lifecycle: str = "persistent",
) -> dict:
    """Issue a fresh device_code/user_code pair for a machine that wants
    to enrol. Nobody is authenticated yet — node_id is only a claim at
    this point; ``approve_device_code`` is where the security properties
    that matter (uniqueness, ownership) get enforced."""
    device_code = _new_device_code()
    expires_at = datetime.now(timezone.utc) + DEVICE_CODE_TTL

    user_code = ""
    for _ in range(USER_CODE_INSERT_ATTEMPTS):
        candidate = _new_user_code()
        try:
            dbmod.insert_device_code(
                db,
                device_code=device_code,
                user_code=candidate,
                node_id=node_id,
                hostname=hostname,
                platform=platform,
                expires_at=expires_at,
                lifecycle=lifecycle,
            )
        except psycopg.errors.UniqueViolation:
            # user_code collision (astronomically unlikely at 32**8) or,
            # in principle, a device_code collision. Retry with fresh
            # random values rather than surfacing a 500.
            device_code = _new_device_code()
            continue
        user_code = candidate
        break
    else:
        raise EnrolmentError("could not allocate a unique device code")

    return {
        "device_code": device_code,
        "user_code": user_code,
        "expires_at": expires_at,
        "interval": POLL_INTERVAL_SECONDS,
    }


def approve_device_code(db: psycopg.Connection, user_code: str, user_id: str) -> str:
    """A signed-in user approves a code they read off a machine. Returns
    the new machine's id. Raises DeviceCodeNotFound / DeviceCodeExpired /
    NodeAlreadyEnrolled rather than ever minting a machine that shouldn't
    exist."""
    row = dbmod.fetch_device_code_by_user_code(db, user_code)
    if row is None:
        raise DeviceCodeNotFound(user_code)

    if row["machine_id"] is not None:
        # Already approved — approving twice must not mint a second
        # machine, so this is a no-op that returns the existing id.
        return row["machine_id"]

    now = datetime.now(timezone.utc)
    if row["expires_at"] <= now:
        raise DeviceCodeExpired(user_code)

    existing = dbmod.fetch_machine_by_node_id(db, row["node_id"])
    if existing is not None:
        # machines.node_id is globally unique and revoking only sets
        # status='revoked' — the row, and the node_id with it, stays. Without
        # the branch below, revoking a machine you own made its node_id
        # permanently unusable: re-enrolment hit the unique constraint and the
        # owner was told "this machine is already enrolled", with no way back
        # short of deleting the agent's identity file. Revoking and enrolling
        # again is an ordinary thing to do, and item 7 of the acceptance bar
        # requires revocation to work — a feature should not punish its user
        # for using it.
        #
        # Owner-scoped, deliberately. A revoked node_id must not become a way
        # for a second account to adopt someone else's machine identity; that
        # impersonation is exactly what the unique constraint is for, and
        # revoking must not open a door into it. Moving a machine between
        # accounts means resetting its identity on the machine itself.
        #
        # `deleted` re-enrols on exactly the same terms (migration 0028), and
        # leaving it out of this branch would have reintroduced the bug the
        # branch was written to fix, one status along: `delete_machine_row`
        # keeps the row and its node_id, so without this a machine its owner
        # deleted could never enrol again — "this machine is already enrolled"
        # for ever, with no way back short of deleting the agent's identity
        # file. Deleting your own laptop and later plugging it back in is an
        # ordinary thing to do.
        if (
            existing["status"] not in ("revoked", "deleted")
            or str(existing["owner_id"]) != str(user_id)
        ):
            raise NodeAlreadyEnrolled(row["node_id"])

        # Reuse the row rather than inserting a second one: contributions and
        # any other history reference this machine id, and a duplicate would
        # silently split a machine's record in two. reactivate_machine clears
        # the old token_hash, so the revoked token stays dead and the agent
        # must redeem a fresh one.
        machine_id = dbmod.reactivate_machine(
            db,
            machine_id=existing["id"],
            name=row["hostname"],
            platform=row["platform"],
            lifecycle=row["lifecycle"],
        )
    else:
        try:
            machine_id = dbmod.insert_machine(
                db,
                owner_id=user_id,
                node_id=row["node_id"],
                name=row["hostname"],
                platform=row["platform"],
                lifecycle=row["lifecycle"],
            )
        except psycopg.errors.UniqueViolation as exc:
            # Lost a race against a concurrent approval of a different code
            # for the same node_id. Same refusal either way.
            raise NodeAlreadyEnrolled(row["node_id"]) from exc

    dbmod.mark_device_code_approved(db, user_code, user_id, machine_id)
    return machine_id


def redeem_device_code(db: psycopg.Connection, device_code: str) -> str | None:
    """The machine exchanges its device_code for a token. Returns the raw
    token exactly once: the atomic claim in
    ``claim_device_code_for_redemption`` ensures a second call — or a
    concurrent one — gets None instead of a second copy. Returns None
    (never raises) for an unknown code, an unapproved code, an expired
    code, or an already-redeemed code, all indistinguishably: no token
    may reach a machine nobody approved, and there is nothing helpful to
    tell a caller that isn't the machine itself."""
    machine_id = dbmod.claim_device_code_for_redemption(db, device_code)
    if machine_id is None:
        return None

    token = new_machine_token()
    token_hash = hash_machine_token(token)
    dbmod.set_machine_token(db, machine_id, token_hash, token[:12])
    return token


def authenticate_machine(db: psycopg.Connection, token: str | None) -> Machine | None:
    """Look up the machine a token belongs to. Returns None immediately
    for an unknown token or a revoked machine — a revoked machine's token
    must stop working the instant it is revoked, not at its next natural
    expiry (machine tokens do not expire on their own).

    ``deleted`` is refused beside ``revoked`` as defence in depth.
    ``delete_machine_row`` clears ``token_hash``, so no token can reach a
    tombstone through the lookup above at all; this line is what keeps that
    true if some later path ever tombstones a row without scrubbing it."""
    if not token:
        return None
    row = dbmod.fetch_machine_by_token_hash(db, hash_machine_token(token))
    if row is None:
        return None
    if row["status"] in ("revoked", "deleted"):
        return None
    return Machine(
        id=row["id"],
        owner_id=row["owner_id"],
        node_id=row["node_id"],
        name=row["name"],
        platform=row["platform"],
        status=row["status"],
        created_at=row.get("created_at"),
        revoked_at=row.get("revoked_at"),
    )


def revoke_machine(db: psycopg.Connection, machine_id: str, user_id: str) -> bool:
    """Revoke a machine. Returns False — not an error, not a distinguishable
    404 — both when the machine doesn't exist and when it exists but is
    owned by someone else, so a caller cannot use this to enumerate other
    users' machine ids."""
    return dbmod.revoke_machine_row(db, machine_id, user_id)


def delete_machine(db: psycopg.Connection, machine_id: str, user_id: str) -> bool:
    """Retire an already-revoked machine for good. Total and idempotent.

    Two things must stop being true, and they are independent — the same
    shape, and the same argument, as ``sandbox_identity.revoke_sandbox_machine``:

    * the machine must stop being a member of anything (every
      ``machine_pools`` row dropped, so a workspace is not left listing a
      machine its owner has retired — ``list_pool_machines`` deliberately
      shows revoked machines to teammates, and a deleted one is not a revoked
      one);
    * the row must become a tombstone with no device detail on it
      (``db.delete_machine_row``).

    Both in ONE transaction. A crash between them would leave a machine that
    is gone from its owner's fleet and still bound to a pool, which is the
    half-deleted state this route exists to make unrepresentable.

    **Revoked first, always.** ``delete_machine_row``'s ``where`` clause is
    the gate, not this function: deleting is tidying, revoking is the security
    action, and folding the second into the first would let one click on a
    console list kill a working machine's credential.

    Returns False — never an exception, never a distinguishable answer — for
    an unknown machine, someone else's, one that is still live, and one
    already deleted. The caller that needs to tell "still live" from "unknown"
    (the route, for its 409) reads the row first with
    ``db.fetch_machine_for_owner``; nothing here will tell it.
    """
    machine_id = str(machine_id)
    user_id = str(user_id)

    with db.transaction():
        # The tombstone runs FIRST, and the unbinds only if it took. This is
        # the one place the order differs from ``revoke_sandbox_machine``,
        # which unbinds on every call including ones that revoke nothing —
        # there the desired end state is always "unbound", here it is not. A
        # call that refuses (still live, not yours, already deleted) must
        # change nothing at all, and unbinding first would quietly drop a
        # working machine's workspace bindings on its way to answering False.
        # The ``where`` clause carries ``owner_id``, so no row this caller
        # cannot see is ever touched.
        if not dbmod.delete_machine_row(db, machine_id, user_id):
            return False

        for bound_pool in dbmod.pool_ids_bound_to_machine(db, machine_id):
            dbmod.unbind_machine_pool(
                db, machine_id=machine_id, pool_id=bound_pool
            )

        return True
