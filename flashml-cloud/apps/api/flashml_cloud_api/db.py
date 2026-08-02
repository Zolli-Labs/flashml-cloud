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
from psycopg.types.json import Json

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


def reactivate_machine(
    db: psycopg.Connection,
    *,
    machine_id: str,
    name: str | None,
    platform: str | None,
) -> str:
    """Return a revoked machine to 'pending' so it can redeem a fresh token.

    Reuses the existing row rather than inserting a second one: contributions
    reference this machine id, and a duplicate would split one machine's
    history in two while also colliding with the node_id unique constraint.

    **Clears token_hash and token_prefix.** The revoked token must stay dead —
    re-enrolment issues a new one through the normal redeem path, and anything
    still holding the old token remains locked out. `revoked_at` is left as it
    is: it records that a revocation happened, which is worth keeping even
    after the machine returns.

    `name` and `platform` are refreshed from the new enrolment, so a machine
    that was renamed or reinstalled reports its current identity.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            update public.machines
               set status = 'pending',
                   token_hash = null,
                   token_prefix = null,
                   name = %s,
                   platform = %s
             where id = %s
            returning id
            """,
            (name, platform, machine_id),
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


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------

def insert_job(
    db: psycopg.Connection,
    *,
    job_id: str,
    owner_id: str,
    name: str | None,
    source: dict[str, Any] | None,
    spec: dict[str, Any] | None,
    status: str,
) -> None:
    """Record a job as owned by ``owner_id``.

    ``owner_id`` must come from a verified JWT ``sub`` — never from the
    request body. This row is the *only* place ownership is recorded:
    every subsequent read, cancel, or artifact fetch for this job_id
    consults it before ever forwarding to the coordinator, so a job the
    coordinator knows about but this table doesn't is simply invisible to
    every caller, including its nominal owner.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.jobs (id, owner_id, name, source, spec, status)
            values (%s, %s, %s, %s, %s, %s)
            """,
            (
                job_id,
                owner_id,
                name,
                Json(source) if source is not None else None,
                Json(spec) if spec is not None else None,
                status,
            ),
        )


def fetch_job_for_owner(
    db: psycopg.Connection, job_id: str, owner_id: str
) -> dict[str, Any] | None:
    """Owner-scoped read: returns None if job_id does not exist *or*
    belongs to someone else. Callers must not distinguish those two cases
    in a response — a 403 for "exists but not yours" would confirm to a
    guesser that the id is real; a 404 for both cases is the whole point."""
    with db.cursor() as cur:
        cur.execute(
            "select * from public.jobs where id = %s and owner_id = %s",
            (job_id, owner_id),
        )
        return cur.fetchone()


def set_job_status(
    db: psycopg.Connection, job_id: str, status: str, *, finished: bool
) -> None:
    """Record a job's terminal (or in-flight) status.

    Deliberately **not** owner-scoped, and that is the one exception to this
    module's rule: the only caller is the in-API federated driver, which is
    not acting for a request and has no ``owner_id`` to fold in — it is
    reporting what happened to a job it was itself started for. Making it
    take an owner would mean carrying a user id into a background thread for
    no security gain, since the job_id is not attacker-supplied there.

    ``finished`` stamps ``finished_at``; a driver that fails must not leave
    a job looking like it is still running, so the failure path sets both in
    one statement rather than two that could half-apply.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            update public.jobs
               set status = %s,
                   finished_at = case when %s then now() else finished_at end
             where id = %s
            """,
            (status, finished, job_id),
        )


# ---------------------------------------------------------------------------
# job_rounds
# ---------------------------------------------------------------------------

def insert_job_round(
    db: psycopg.Connection,
    *,
    job_id: str,
    round_index: int,
    participants: int,
    mean_loss: float | None,
    contributors: list[str],
    coordinator_job_id: str | None,
) -> None:
    """Record one completed federated-averaging round.

    ``on conflict do nothing`` on ``(job_id, round)``: a round is aggregated
    once, but a driver resumed onto a run whose history is already written
    must be able to re-report it without either crashing or appending a
    second, contradictory row. Idempotent commits, same rule as everywhere
    else money and metrics are counted.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.job_rounds
                (job_id, round, participants, mean_loss, contributors,
                 coordinator_job_id)
            values (%s, %s, %s, %s, %s, %s)
            on conflict (job_id, round) do nothing
            """,
            (
                job_id,
                round_index,
                participants,
                mean_loss,
                Json(list(contributors)),
                coordinator_job_id,
            ),
        )


#: Columns of ``public.job_rounds`` that may leave the API. Spelled out for
#: the same reason ``MACHINE_PUBLIC_COLUMNS`` is: adding a column to the
#: schema must not silently add it to a response.
JOB_ROUND_PUBLIC_COLUMNS = (
    "round", "participants", "mean_loss", "contributors",
    "coordinator_job_id", "recorded_at",
)


def list_job_rounds_for_owner(
    db: psycopg.Connection, job_id: str, owner_id: str
) -> list[dict[str, Any]]:
    """Every recorded round of ``job_id``, but only if ``owner_id`` owns it.

    The ownership test is the join, not a check the caller is trusted to
    have done first: a route that forgot it would return an empty list here
    rather than another user's loss curve.
    """
    columns = ", ".join(f"r.{c}" for c in JOB_ROUND_PUBLIC_COLUMNS)
    with db.cursor() as cur:
        cur.execute(
            f"""
            select {columns}
              from public.job_rounds r
              join public.jobs j on j.id = r.job_id
             where r.job_id = %s and j.owner_id = %s
             order by r.round
            """,
            (job_id, owner_id),
        )
        return list(cur.fetchall())


def list_round_job_ids(db: psycopg.Connection, job_id: str) -> list[tuple[int, str]]:
    """``(round, coordinator_job_id)`` for every completed round, oldest first.

    This is exactly the shape ``fedavg_driver.resume_state`` takes: it is
    the persisted form of a driver's in-memory round history, which is what
    makes a run resumable across an API restart at all. Rounds with no
    recorded coordinator job are skipped — ``resume_state`` probes an
    artifact key built from that id, so a null would look like a round that
    never completed.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select round, coordinator_job_id
              from public.job_rounds
             where job_id = %s and coordinator_job_id is not null
             order by round
            """,
            (job_id,),
        )
        return [(int(r["round"]), str(r["coordinator_job_id"]))
                for r in cur.fetchall()]


def list_federated_jobs_for_owner(
    db: psycopg.Connection, owner_id: str
) -> list[dict[str, Any]]:
    """The caller's federated runs, which the coordinator cannot list.

    A federated run is one coordinator job *per round*, so it has no single
    coordinator job id and never appears in the coordinator's job list. This
    table is the only place it exists as one thing, which is why the job
    list has to union the two rather than filtering one.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select id, name, status, created_at, finished_at
              from public.jobs
             where owner_id = %s and source->>'mode' = 'federated'
             order by created_at
            """,
            (owner_id,),
        )
        return list(cur.fetchall())


def list_job_ids_for_owner(db: psycopg.Connection, owner_id: str) -> set[str]:
    """Every job id belonging to owner_id, and nothing else. Used to filter
    the coordinator's (unscoped, operator-token) job list down to exactly
    the caller's own jobs — the coordinator has no notion of accounts, so
    this table is the only place that scoping can happen."""
    with db.cursor() as cur:
        cur.execute("select id from public.jobs where owner_id = %s", (owner_id,))
        return {row["id"] for row in cur.fetchall()}
