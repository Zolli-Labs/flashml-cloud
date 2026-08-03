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

from collections.abc import Mapping, Sequence
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


def touch_machine_last_seen(db: psycopg.Connection, machine_id: str) -> None:
    """Record that this machine just spoke to us.

    `machines.last_seen_at` is the ONLY thing the console renders
    Online/Offline from, and nothing wrote it — so every machine displayed
    "Offline / Last seen never" however healthily it was heartbeating, while
    the coordinator's own liveness view (kept separately, for scheduling) saw
    it as alive. A host who has just enrolled and started their agent should
    not be shown a dead-looking dashboard.

    Deliberately best-effort at the call site: a machine's work must not fail
    because a display column could not be updated.
    """
    with db.cursor() as cur:
        cur.execute(
            "update public.machines set last_seen_at = now() where id = %s",
            (machine_id,),
        )


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
    pool_id: str | None = None,
) -> None:
    """Record a job as owned by ``owner_id``.

    ``owner_id`` must come from a verified JWT ``sub`` — never from the
    request body. This row is the *only* place ownership is recorded:
    every subsequent read, cancel, or artifact fetch for this job_id
    consults it before ever forwarding to the coordinator, so a job the
    coordinator knows about but this table doesn't is simply invisible to
    every caller, including its nominal owner.

    ``pool_id`` is ``None`` for every job outside a pool — which is every
    job before pools existed and every raw ``/v1alpha1/jobs`` submission
    today (that route refuses a pool spec outright). Only the from-repo
    route ever passes one, and only after ``fetch_pool_for_member`` has
    already confirmed the caller belongs to it.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.jobs (id, owner_id, name, source, spec, status, pool_id)
            values (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                job_id,
                owner_id,
                name,
                Json(source) if source is not None else None,
                Json(spec) if spec is not None else None,
                status,
                pool_id,
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
    clipped: Sequence[Mapping[str, Any]] = (),
) -> None:
    """Record one completed federated-averaging round.

    ``on conflict do nothing`` on ``(job_id, round)``: a round is aggregated
    once, but a driver resumed onto a run whose history is already written
    must be able to re-report it without either crashing or appending a
    second, contradictory row. Idempotent commits, same rule as everywhere
    else money and metrics are counted.

    ``clipped`` is the round's bounded-influence report — the contributions
    whose norm exceeded the round's own cap, as
    ``{task_id, norm, cap, scale, node_id}`` — and is empty on an honest
    round. Stored the same way ``contributors`` is, as one jsonb document
    rather than rows of its own: it is read straight back out as JSON and is
    never joined on. Optional because it is evidence, not accounting: a
    caller that has none to report (or a runtime older than the feature)
    records the round with the column's own ``[]``, and never a null.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.job_rounds
                (job_id, round, participants, mean_loss, contributors,
                 coordinator_job_id, clipped)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (job_id, round) do nothing
            """,
            (
                job_id,
                round_index,
                participants,
                mean_loss,
                Json(list(contributors)),
                coordinator_job_id,
                Json(list(clipped)),
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


# ---------------------------------------------------------------------------
# contributions
# ---------------------------------------------------------------------------

def record_contributions(
    db: psycopg.Connection,
    *,
    job_id: str,
    entries: Sequence[Mapping[str, Any]],
) -> int:
    """Credit every machine whose work on ``job_id`` was ACCEPTED.

    ``entries`` is one mapping per accepted task — ``node_id``, ``task_id``,
    ``duration_s`` — and the acceptance judgement is the caller's: this
    function writes what it is given and never re-decides who deserves
    credit. ``job_id`` is the id of the job the tasks actually belong to (for
    a federated run, the *round's* coordinator job, not the parent run —
    otherwise round 1's ``task-000`` would collide with round 0's under the
    unique index and only the first round of an N-round run would ever be
    paid). Returns the number of rows written.

    **A node with no ``machines`` row is skipped, not raised on.** A node
    registered against a self-hosted coordinator has no cloud enrolment at
    all: nobody signed in, nobody owns it, and there is nothing to credit.
    That is a supported deployment — flashruntime runs with no Postgres and
    no cloud — so an unresolvable node_id is an expected, ordinary outcome
    and not an error condition. Raising would turn somebody else's perfectly
    correct self-hosted pool into a failure on our side, and (because this is
    called from a round callback) would put at risk a round that had already
    aggregated successfully. The write is deliberately partial: the machines
    we do know about are credited, the rest are silently not.

    **Idempotent by schema, not by convention.** ``on conflict do nothing``
    against the unique index from migration 0003 on ``(machine_id, job_id,
    coalesce(task_id, ''))``. The caller may be retried — a round callback
    can run twice and a driver can be restarted onto a run whose rounds are
    already recorded — and a credit ledger that double-counts fails silently
    and compounds. Hard rule 4: idempotent commits, no double counting.

    One resolve query for the whole batch rather than one per row: per-row
    resolution is a database round trip per contributor per round, which
    becomes O(pool size) load exactly when the pool is large enough for that
    to hurt.
    """
    rows: list[tuple[str, str, str | None, float | None]] = []
    seen: set[tuple[str, str | None]] = set()

    node_ids = []
    for entry in entries:
        node_id = entry.get("node_id")
        if isinstance(node_id, str) and node_id:
            node_ids.append(node_id)
    if not node_ids:
        # Nothing to resolve and nothing to write. An empty round (or a round
        # in which every contributor was self-hosted) is a no-op, not an
        # error — and it must not cost a query either.
        return 0

    with db.cursor() as cur:
        cur.execute(
            "select id, node_id from public.machines where node_id = any(%s)",
            (list(set(node_ids)),),
        )
        machine_by_node = {row["node_id"]: row["id"] for row in cur.fetchall()}

        for entry in entries:
            node_id = entry.get("node_id")
            if not isinstance(node_id, str) or node_id not in machine_by_node:
                continue
            task_id = entry.get("task_id")
            task_id = task_id if isinstance(task_id, str) and task_id else None
            key = (node_id, task_id)
            if key in seen:
                # The batch itself is deduplicated so the statement below
                # cannot depend on how Postgres handles a conflict between
                # two rows of the same command.
                continue
            seen.add(key)
            duration = entry.get("duration_s")
            duration = (
                float(duration)
                if isinstance(duration, (int, float)) and not isinstance(duration, bool)
                else None
            )
            rows.append((machine_by_node[node_id], job_id, task_id, duration))

        if not rows:
            return 0

        values = ", ".join(["(%s, %s, %s, %s)"] * len(rows))
        cur.execute(
            f"""
            insert into public.contributions
                (machine_id, job_id, task_id, duration_s)
            values {values}
            on conflict do nothing
            returning id
            """,
            [field for row in rows for field in row],
        )
        return len(cur.fetchall())


def record_attempt(
    db: psycopg.Connection,
    *,
    lease_id: str,
    machine_id: str,
    job_id: str,
    task_id: str,
) -> None:
    """Remember that ``machine_id`` claimed ``lease_id`` for a task.

    This is the mapping the credit path needs and cannot otherwise get: the
    completion hop carries only a lease id, while ``contributions`` is keyed
    on ``(machine_id, job_id, task_id)``.

    ``on conflict do nothing`` because a claim that is forwarded twice — a
    retry, a duplicated request — describes one lease, not two.
    """
    with db.cursor() as cur:
        cur.execute(
            "insert into public.attempts"
            "            (lease_id, machine_id, job_id, task_id)"
            "     values (%s, %s, %s, %s)"
            " on conflict (lease_id) do nothing",
            (lease_id, machine_id, job_id, task_id),
        )


def claim_attempt_credit(
    db: psycopg.Connection,
    *,
    lease_id: str,
    machine_id: str,
) -> dict[str, Any] | None:
    """Take the right to credit ``lease_id``, exactly once.

    Returns ``{"job_id", "task_id", "duration_s"}`` for a lease this machine
    claimed and has not yet been credited for, or ``None``. ``None`` covers
    every "do not pay" case at once: unknown lease, already credited, or a
    different machine asking.

    One ``UPDATE ... RETURNING`` rather than a select then an update — two
    completions arriving together must not both come back with a row, and a
    single statement makes that the database's problem rather than this
    process's.

    ``duration_s`` is lease-held wall clock (claim to credit), which includes
    input download and output upload. That is the honest number for a
    contribution ledger. It is cast to ``float`` because ``extract(epoch …)``
    is ``numeric`` and psycopg returns ``Decimal``, which would otherwise
    reach ``record_contributions`` and land in the column as a different type
    from every row the federated path writes.
    """
    with db.cursor() as cur:
        cur.execute(
            "update public.attempts"
            "   set accepted_at = now()"
            " where lease_id = %s and machine_id = %s and accepted_at is null"
            " returning job_id, task_id,"
            "           extract(epoch from (now() - claimed_at)) as duration_s",
            (lease_id, machine_id),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "job_id": row["job_id"],
        "task_id": row["task_id"],
        "duration_s": float(row["duration_s"]),
    }


# ---------------------------------------------------------------------------
# pools
# ---------------------------------------------------------------------------

#: The columns of ``public.pools`` that may ever leave the API. Nothing on
#: this table is sensitive today — unlike ``machines`` there is no
#: ``token_hash`` sitting next to it — but the tuple is spelled out anyway,
#: same discipline as ``MACHINE_PUBLIC_COLUMNS``, so a column added to the
#: table later does not silently start leaving the API by default.
#: ``public.pool_invites`` (below) deliberately never gets a tuple like
#: this one: it has a ``token_hash`` column, and the day someone adds a
#: "list my invites" endpoint is the day a careless ``select *`` there would
#: leak a credential digest the way ``MACHINE_PUBLIC_COLUMNS`` exists to
#: prevent.
POOL_PUBLIC_COLUMNS = ("id", "name", "owner_id", "created_at")

#: The one server-side home of "is this machine online" for pool counts —
#: matching the console's client-side ``ONLINE_WITHIN_MS``. Every
#: ``machines_online`` count below uses this exact predicate (same ``m``
#: alias in both queries) so the number shown in a user's pool list and the
#: number shown inside one pool's member list can never silently drift
#: apart by one query changing the threshold and the other not.
MACHINE_ONLINE_PREDICATE = (
    "m.status = 'active' and m.last_seen_at > now() - interval '90 seconds'"
)


def create_pool(
    db: psycopg.Connection, *, name: str, owner_id: str
) -> dict[str, Any]:
    """Create a pool and seat its owner as a member, atomically.

    Every reachability check below — ``fetch_pool_for_member``,
    ``is_pool_member``, ``pool_ids_for_machine_owner``, ``list_pools_for_user``
    — is a join through ``pool_members``, not ``pools.owner_id``. A pool
    whose owner had no membership row would be invisible to its own creator:
    absent from their pool list, 404 on fetch, and contributing none of
    their machines to its online count. The membership insert is therefore
    not a follow-up call a route could forget — it happens inside the same
    transaction as the pool row, so the two can never be observed apart.
    """
    columns = ", ".join(POOL_PUBLIC_COLUMNS)
    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                f"""
                insert into public.pools (name, owner_id)
                values (%s, %s)
                returning {columns}
                """,
                (name, owner_id),
            )
            row = cur.fetchone()
            assert row is not None
            cur.execute(
                """
                insert into public.pool_members (pool_id, user_id)
                values (%s, %s)
                """,
                (row["id"], owner_id),
            )
    return row


def list_pools_for_user(
    db: psycopg.Connection, user_id: str
) -> list[dict[str, Any]]:
    """Every pool ``user_id`` belongs to (as owner or plain member), with
    the two counts the console's pool list renders.

    ``member_count`` and ``machines_online`` are aggregates computed here,
    in one query, rather than one query per pool plus a Python loop — the
    same reasoning ``record_contributions`` gives for its single resolve
    query: per-pool round trips become O(pool count) load exactly when a
    user belongs to enough pools for that to matter.
    """
    with db.cursor() as cur:
        cur.execute(
            f"""
            select p.id, p.name, p.owner_id,
                   count(distinct pm.user_id) as member_count,
                   count(distinct m.id) filter (
                       where {MACHINE_ONLINE_PREDICATE}
                   ) as machines_online,
                   p.created_at
              from public.pools p
              join public.pool_members pm on pm.pool_id = p.id
              left join public.machines m on m.owner_id = pm.user_id
             where p.id in (
                 select pool_id from public.pool_members where user_id = %s
             )
             group by p.id, p.name, p.owner_id, p.created_at
             order by p.created_at
            """,
            (user_id,),
        )
        return list(cur.fetchall())


def fetch_pool_for_member(
    db: psycopg.Connection, pool_id: str, user_id: str
) -> dict[str, Any] | None:
    """Member-scoped read: returns None if pool_id does not exist *or*
    user_id is not a member of it. Callers must not distinguish those two
    cases in a response — same 404 doctrine as ``fetch_machine_for_owner``:
    a 403 for "exists but you're not in it" would confirm to a guesser that
    the id is real."""
    columns = ", ".join(f"p.{c}" for c in POOL_PUBLIC_COLUMNS)
    with db.cursor() as cur:
        cur.execute(
            f"""
            select {columns}
              from public.pools p
              join public.pool_members pm
                on pm.pool_id = p.id and pm.user_id = %s
             where p.id = %s
            """,
            (user_id, pool_id),
        )
        return cur.fetchone()


def list_pool_members(
    db: psycopg.Connection, pool_id: str
) -> list[dict[str, Any]]:
    """Every member of ``pool_id``, with their own machine counts.

    ``machine_count``/``machines_online`` are per-member, not per-pool: a
    member's machines are resolved the same way ``pool_members`` documents
    machine-pool membership works everywhere else in this schema —
    ``machines.owner_id -> pool_members.user_id`` — because a machine is
    never a member in its own right.
    """
    with db.cursor() as cur:
        cur.execute(
            f"""
            select pm.user_id, pr.display_name, pm.joined_at,
                   count(distinct m.id) as machine_count,
                   count(distinct m.id) filter (
                       where {MACHINE_ONLINE_PREDICATE}
                   ) as machines_online
              from public.pool_members pm
              join public.profiles pr on pr.id = pm.user_id
              left join public.machines m on m.owner_id = pm.user_id
             where pm.pool_id = %s
             group by pm.user_id, pr.display_name, pm.joined_at
             order by pm.joined_at
            """,
            (pool_id,),
        )
        return list(cur.fetchall())


def is_pool_member(db: psycopg.Connection, pool_id: str, user_id: str) -> bool:
    with db.cursor() as cur:
        cur.execute(
            "select 1 from public.pool_members where pool_id = %s and user_id = %s",
            (pool_id, user_id),
        )
        return cur.fetchone() is not None


def pool_ids_for_machine_owner(
    db: psycopg.Connection, owner_id: str
) -> list[str]:
    """Sorted pool ids ``owner_id`` belongs to, for the agent proxy's
    per-request pool stamp.

    Sorted (not merely "in some order") because that stamp is compared
    across requests — an unordered list would make two calls that returned
    the identical set of pools look like a change when nothing moved.
    """
    with db.cursor() as cur:
        cur.execute(
            "select pool_id from public.pool_members where user_id = %s",
            (owner_id,),
        )
        return sorted(str(row["pool_id"]) for row in cur.fetchall())


def create_pool_invite(
    db: psycopg.Connection,
    *,
    pool_id: str,
    created_by: str,
    token_hash: str,
    expires_at: datetime,
    uses: int,
) -> None:
    """Persist one invite link's hash. Never the raw token.

    Same discipline as ``set_machine_token``: only the sha256 digest is
    ever written, and the raw token is handed to the caller exactly once,
    by the route that generated it, and never stored anywhere this
    function can see it again.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.pool_invites
                (token_hash, pool_id, created_by, expires_at, uses_remaining)
            values (%s, %s, %s, %s, %s)
            """,
            (token_hash, pool_id, created_by, expires_at, uses),
        )


def consume_pool_invite(
    db: psycopg.Connection, *, token_hash: str, user_id: str
) -> dict[str, Any] | None:
    """Redeem an invite: decrement its use, join the pool, admit the
    profile — or refuse all three at once.

    Returns ``{"pool_id", "name"}`` on success, or ``None`` for every
    do-not-admit case together (unknown token, expired, already exhausted)
    without distinguishing which — the same reason
    ``claim_attempt_credit`` and ``claim_device_code_for_redemption`` fold
    their refusal cases into one ``None``: telling a guesser *which* reason
    an invite failed for is a small oracle for free.

    The decrement is one ``UPDATE ... WHERE ... RETURNING`` — the
    ``claim_attempt_credit`` idiom — so that two redemptions of the same
    one-use invite arriving together cannot both win the row; only one
    ``UPDATE`` can match ``uses_remaining > 0`` before the other sees the
    decremented value.

    Decrement, membership, and admission are one transaction
    (``db.transaction()``, explicit despite this connection being
    autocommit — psycopg supports that). A membership joined without the
    matching admission would leave a still-gated account sitting inside a
    team it cannot otherwise reach; a decrement that "succeeded" without
    either would burn a one-use invite for nothing. All three commit
    together or none do.
    """
    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                """
                update public.pool_invites
                   set uses_remaining = uses_remaining - 1
                 where token_hash = %s
                   and expires_at > now()
                   and uses_remaining > 0
                returning pool_id
                """,
                (token_hash,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            pool_id = row["pool_id"]

            cur.execute(
                """
                insert into public.pool_members (pool_id, user_id)
                values (%s, %s)
                on conflict do nothing
                """,
                (pool_id, user_id),
            )
            cur.execute(
                """
                update public.profiles
                   set admitted_at = coalesce(admitted_at, now())
                 where id = %s
                """,
                (user_id,),
            )
            cur.execute(
                "select name from public.pools where id = %s",
                (pool_id,),
            )
            pool_row = cur.fetchone()
            assert pool_row is not None
            return {"pool_id": pool_id, "name": pool_row["name"]}


def profile_is_admitted(db: psycopg.Connection, user_id: str) -> bool:
    """Whether ``user_id`` has cleared the alpha signup gate.

    Distinct from "does a profile row exist" — every profile row exists
    from the moment a JWT is first seen (``upsert_profile``), so the gate
    lives in one nullable column on that same row, not in a separate
    allow-list table. An unknown ``user_id`` reads as not admitted, the
    same refusal as an expired invite.
    """
    with db.cursor() as cur:
        cur.execute(
            "select admitted_at from public.profiles where id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        return row is not None and row["admitted_at"] is not None


def fetch_job_for_viewer(
    db: psycopg.Connection, job_id: str, user_id: str
) -> dict[str, Any] | None:
    """Viewer-scoped read: the owner, or any member of the job's pool, may
    see it. Anyone else gets None indistinguishably from a job that does
    not exist — same 404 doctrine as ``fetch_job_for_owner``, widened from
    "the owner" to "the owner or a teammate" now that a job can opt into
    pool scoping.

    A job with a null ``pool_id`` (every pre-pools job) can never match the
    ``pool_members`` half of the check — there is no pool to be a member
    of — so it is reachable by its owner only, exactly the pre-pools
    behaviour ``fetch_job_for_owner`` still provides.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select * from public.jobs j
             where j.id = %s
               and (
                     j.owner_id = %s
                  or exists (
                       select 1 from public.pool_members pm
                        where pm.pool_id = j.pool_id and pm.user_id = %s
                     )
               )
            """,
            (job_id, user_id, user_id),
        )
        return cur.fetchone()


def list_pool_job_ids_for_member(
    db: psycopg.Connection, user_id: str
) -> list[str]:
    """Every pool-scoped job id visible to ``user_id`` through pool
    membership — the ids ``fetch_job_for_viewer`` would admit them to that
    ``list_job_ids_for_owner`` (owner-only) would not.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select j.id
              from public.jobs j
              join public.pool_members pm
                on pm.pool_id = j.pool_id and pm.user_id = %s
             order by j.created_at
            """,
            (user_id,),
        )
        return [row["id"] for row in cur.fetchall()]


def list_job_contributions(
    db: psycopg.Connection, job_id: str
) -> list[dict[str, Any]]:
    """Per-machine credit summary for ``job_id``, with the names a pool's
    contribution view renders: which machine, whose machine, how much work.

    ``contributions`` only knows ``machine_id``; ``machines`` only knows
    ``node_id``/``name``/``owner_id``. Getting from a credit row to
    "Ada's laptop, 12 tasks, 340s" needs both joins in one query — the same
    reasoning ``list_job_rounds_for_owner`` gives for joining ``jobs`` into
    its own scoping check, done here for display instead of authorization.

    ``total_duration_s`` is cast to ``float`` for the same reason
    ``claim_attempt_credit`` casts its single duration: ``sum(numeric)`` is
    ``numeric``, and psycopg returns ``Decimal`` for it, which must not
    reach a JSON response as a type the rest of this module never produces.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select m.node_id, m.name as machine_name,
                   pr.display_name as member_display_name,
                   count(c.id) as tasks_credited,
                   coalesce(sum(c.duration_s), 0) as total_duration_s
              from public.contributions c
              join public.machines m on m.id = c.machine_id
              join public.profiles pr on pr.id = m.owner_id
             where c.job_id = %s
             group by m.node_id, m.name, pr.display_name
             order by m.node_id
            """,
            (job_id,),
        )
        return [
            {
                "node_id": row["node_id"],
                "machine_name": row["machine_name"],
                "member_display_name": row["member_display_name"],
                "tasks_credited": row["tasks_credited"],
                "total_duration_s": float(row["total_duration_s"]),
            }
            for row in cur.fetchall()
        ]


# ---------------------------------------------------------------------------
# verifications
# ---------------------------------------------------------------------------

#: Most peer samples one verdict will read. The timing slice runs on the
#: credit hot path, so its cost must not scale with the size of the job: a
#: sweep with ten thousand tasks would otherwise pull ten thousand rows to
#: compute one median. Newest first, because "how long does this work take"
#: is a question about now.
PEER_SAMPLE_LIMIT = 200


def peer_task_durations(
    db: psycopg.Connection,
    *,
    job_id: str,
    machine_id: str,
    limit: int = PEER_SAMPLE_LIMIT,
) -> list[float]:
    """Other machines' recorded durations on ``job_id``. Never this one's.

    **The exclusion is the point of the function.** A machine's own history
    must not form its own baseline: return in 0.3s often enough and 0.3s
    becomes the median it is measured against, so a consistently fast liar
    passes forever while the first honest machine to join is the one that
    looks anomalous. The degenerate case is worse still — a machine that has
    worked a job alone would be compared only against itself, which always
    passes, and "nobody has ever checked this" would be recorded as ``pass``.

    Rows with no ``duration_s`` are excluded rather than returned as
    ``None``. ``fedavg.on_round`` credits from the coordinator's task view,
    which reports no duration, so those rows are real contributions and
    useless as timing evidence; letting them through would count toward the
    peer minimum on a sample of nothing.

    Cast to ``float`` here for the same reason ``claim_attempt_credit``
    casts: ``duration_s`` is ``numeric`` and psycopg returns ``Decimal``,
    which would otherwise be mixed with float thresholds in the verdict.

    The peer group is one ``job_id``. For a federated run that is one
    *round's* coordinator job — the same key ``record_contributions`` uses —
    so a round's peer group is only as large as its quorum, which on a small
    fleet means the timing slice usually answers ``unknown`` for federated
    work. That is the correct answer, not a bug to route around.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select duration_s
              from public.contributions
             where job_id = %s
               and machine_id <> %s
               and duration_s is not null
             order by accepted_at desc
             limit %s
            """,
            (job_id, machine_id, limit),
        )
        return [float(row["duration_s"]) for row in cur.fetchall()]


def record_verification(
    db: psycopg.Connection,
    *,
    machine_id: str | None,
    job_id: str,
    task_id: str,
    slice_name: str,
    verdict: str,
    detail: Mapping[str, Any] | None = None,
) -> None:
    """Record one slice's verdict on one task. Advisory, always.

    Nothing in this system reads what this writes to refuse a lease, withhold
    a credit, fail a commit or change placement — see the design spec §5 and
    the header of migration 0006. This function exists so an operator can
    look; it is not a gate and must never become one by accident.

    ``verdict`` is one of ``pass`` / ``flag`` / ``unknown`` and is passed
    straight to the database, which constrains it. Deliberately not
    normalised, defaulted or coerced here: the one mistake this whole layer
    is built to avoid is a "could not tell" arriving as a ``pass``, and a
    tolerant writer is exactly how that happens. A caller with nothing to say
    says ``unknown``, and an invalid verdict raises rather than being quietly
    rewritten into a valid one.

    ``slice_name`` rather than ``slice`` for the same reason
    ``insert_job_round`` takes ``round_index``: the column name is a Python
    builtin, and shadowing it inside the function is worse than the small
    asymmetry at the call site.

    ``machine_id`` may be ``None``. A redundancy mismatch is about a pair and
    names neither as the liar (§8.5); a row forced to blame one of the two
    would be a fabricated accusation.

    No ``on conflict`` clause, because there is no unique index to conflict
    with: three slices judge one task independently and each gets a row. The
    once-only guarantee for the timing slice comes from
    ``claim_attempt_credit``, which hands out the right to record a lease
    exactly once — a second writer added later must bring its own guard.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.verifications
                (machine_id, job_id, task_id, slice, verdict, detail)
            values (%s, %s, %s, %s, %s, %s)
            """,
            (
                machine_id,
                job_id,
                task_id,
                slice_name,
                verdict,
                Json(dict(detail or {})),
            ),
        )


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
