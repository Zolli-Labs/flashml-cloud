"""Thin Postgres access for the FlashML Cloud API.

Every function that reads or writes rows scoped to a specific owner takes
that owner as an explicit parameter and folds it into the query itself —
never as a filter applied afterwards in Python. That way a query cannot
return (or mutate) another user's rows by omission: leaving out the owner
is a missing function argument, not a missing ``if``.

``db`` throughout this module (and in ``enrolment.py``) is a live psycopg
connection opened by ``connect()``. Connections are opened in autocommit
mode so every statement (or, where noted, every atomically-scoped ``UPDATE
... RETURNING``) takes effect immediately and callers never need to
remember a ``commit()``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from flashml_cloud_api.settings import Settings


def connect(settings: Settings) -> psycopg.Connection:
    """Open a new autocommit connection to the configured Postgres database.

    ``settings.database_url`` is a standard libpq connection string/URI,
    read from the ``DATABASE_URL`` env var. Never hardcode a connection
    string or credential here — this function only ever reads one that
    was already resolved from the environment.
    """
    if not settings.database_url:
        raise RuntimeError(
            "DATABASE_URL is not configured; cannot open a Postgres connection."
        )
    conn = psycopg.connect(settings.database_url, row_factory=dict_row)
    conn.autocommit = True
    return conn


@dataclass(frozen=True)
class Machine:
    """A row from ``public.machines``, as returned to callers that have
    already authenticated the machine (never constructed from
    caller-supplied data)."""

    id: str
    owner_id: str
    node_id: str
    name: str | None
    platform: str | None
    status: str
    created_at: datetime | None = None
    revoked_at: datetime | None = None


# ---------------------------------------------------------------------------
# profiles
# ---------------------------------------------------------------------------

def upsert_profile(
    db: psycopg.Connection, user_id: str, display_name: str | None = None
) -> dict[str, Any]:
    """Create-or-fetch the profile row for a verified Supabase user.

    ``user_id`` must come from a verified JWT ``sub`` — never from a body.
    The ``do update`` (rather than ``do nothing``) is what guarantees a row
    comes back on the second and every subsequent call; ``do nothing``
    returns no row on conflict and would make a returning user look absent.
    display_name is only ever *filled in*, never overwritten with null, so
    a later token without the claim cannot blank a name the user set.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.profiles (id, display_name)
            values (%s, %s)
            on conflict (id) do update
               set display_name = coalesce(excluded.display_name,
                                           public.profiles.display_name)
            returning id, display_name, github_login, is_host, is_developer,
                      created_at
            """,
            (user_id, display_name),
        )
        row = cur.fetchone()
        assert row is not None
        return row


# ---------------------------------------------------------------------------
# device_codes
# ---------------------------------------------------------------------------

def insert_device_code(
    db: psycopg.Connection,
    *,
    device_code: str,
    user_code: str,
    node_id: str,
    hostname: str | None,
    platform: str | None,
    expires_at: datetime,
) -> None:
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.device_codes
                (device_code, user_code, node_id, hostname, platform, expires_at)
            values (%s, %s, %s, %s, %s, %s)
            """,
            (device_code, user_code, node_id, hostname, platform, expires_at),
        )


def fetch_device_code_by_user_code(
    db: psycopg.Connection, user_code: str
) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute(
            "select * from public.device_codes where user_code = %s",
            (user_code,),
        )
        return cur.fetchone()


def mark_device_code_approved(
    db: psycopg.Connection, user_code: str, user_id: str, machine_id: str
) -> None:
    with db.cursor() as cur:
        cur.execute(
            """
            update public.device_codes
               set machine_id = %s, approved_by = %s
             where user_code = %s
            """,
            (machine_id, user_id, user_code),
        )


def claim_device_code_for_redemption(
    db: psycopg.Connection, device_code: str
) -> str | None:
    """Atomically mark a device_code as consumed and return its
    machine_id — but only if it is approved, unexpired, and not already
    consumed. Returns None in every other case (unknown code, not yet
    approved, expired, or already redeemed) without distinguishing which,
    so a caller cannot use this as an oracle for which codes exist.

    The single ``UPDATE ... WHERE consumed_at is null ... RETURNING`` is
    what makes "redeemed exactly once" hold even under concurrent
    redemption attempts: only one call can win the row.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            update public.device_codes
               set consumed_at = now()
             where device_code = %s
               and consumed_at is null
               and machine_id is not null
               and expires_at > now()
            returning machine_id
            """,
            (device_code,),
        )
        row = cur.fetchone()
        return row["machine_id"] if row else None


# ---------------------------------------------------------------------------
# machines
# ---------------------------------------------------------------------------

def fetch_machine_by_node_id(
    db: psycopg.Connection, node_id: str
) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute("select * from public.machines where node_id = %s", (node_id,))
        return cur.fetchone()


def insert_machine(
    db: psycopg.Connection,
    *,
    owner_id: str,
    node_id: str,
    name: str | None,
    platform: str | None,
) -> str:
    """Insert a new pending machine and return its id.

    Raises ``psycopg.errors.UniqueViolation`` if node_id is already bound
    to another machine (the schema's ``machines.node_id`` unique
    constraint) — callers must catch this and turn it into a clean
    refusal, not let it surface as an unhandled 500.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.machines (owner_id, node_id, name, platform, status)
            values (%s, %s, %s, %s, 'pending')
            returning id
            """,
            (owner_id, node_id, name, platform),
        )
        row = cur.fetchone()
        assert row is not None
        return row["id"]


def set_machine_token(
    db: psycopg.Connection, machine_id: str, token_hash: str, token_prefix: str
) -> None:
    with db.cursor() as cur:
        cur.execute(
            """
            update public.machines
               set token_hash = %s, token_prefix = %s, status = 'active'
             where id = %s
            """,
            (token_hash, token_prefix, machine_id),
        )


def fetch_machine_by_token_hash(
    db: psycopg.Connection, token_hash: str
) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute(
            "select * from public.machines where token_hash = %s",
            (token_hash,),
        )
        return cur.fetchone()


def fetch_machine_for_owner(
    db: psycopg.Connection, machine_id: str, owner_id: str
) -> dict[str, Any] | None:
    """Owner-scoped read: returns None if machine_id does not exist *or*
    belongs to someone else. Callers must not distinguish those two cases
    in a response — that would confirm to a guesser that the id exists."""
    with db.cursor() as cur:
        cur.execute(
            "select * from public.machines where id = %s and owner_id = %s",
            (machine_id, owner_id),
        )
        return cur.fetchone()


#: The columns of ``public.machines`` that may ever leave the API. Spelled
#: out rather than ``select *`` on purpose: ``token_hash`` lives in the same
#: table, and a ``select *`` feeding a JSON response is exactly how a
#: credential digest ends up in a browser. Adding a column to the schema
#: must not silently add it to the API's output.
MACHINE_PUBLIC_COLUMNS = (
    "id", "node_id", "name", "platform", "capabilities", "status",
    "token_prefix", "last_seen_at", "created_at", "revoked_at",
)


def list_machines_for_owner(
    db: psycopg.Connection, owner_id: str
) -> list[dict[str, Any]]:
    """Every machine belonging to owner_id, and nothing else. The owner
    filter is in the SQL, not applied afterwards in Python — omitting it
    would be a missing argument, not a missing ``if``."""
    columns = ", ".join(MACHINE_PUBLIC_COLUMNS)
    with db.cursor() as cur:
        cur.execute(
            f"select {columns} from public.machines "
            "where owner_id = %s order by created_at",
            (owner_id,),
        )
        return list(cur.fetchall())


def revoke_machine_row(
    db: psycopg.Connection, machine_id: str, owner_id: str
) -> bool:
    """Owner-scoped revoke. Returns True only if a row belonging to
    owner_id was actually updated — a bad machine_id and a machine_id
    owned by someone else both return False, indistinguishably."""
    with db.cursor() as cur:
        cur.execute(
            """
            update public.machines
               set status = 'revoked', revoked_at = now()
             where id = %s and owner_id = %s and status != 'revoked'
            returning id
            """,
            (machine_id, owner_id),
        )
        return cur.fetchone() is not None
