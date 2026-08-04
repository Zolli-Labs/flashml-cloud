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
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from flashml_cloud_api.settings import Settings

if TYPE_CHECKING:
    # Type-only: this data layer writes what the validator already accepted
    # and must not depend on it at runtime — the import direction stays
    # route -> validation -> db, never db -> validation.
    from flashml_cloud_api.access import OnboardingSubmission


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
# access requests
#
# `admitted_at` on profiles remains the switch every gate reads. This table
# is the paperwork behind it: who asked, what they said, who decided.
# ---------------------------------------------------------------------------


def access_state_for(db: psycopg.Connection, user_id: str) -> str:
    """``needs_onboarding`` | ``pending`` | ``admitted`` | ``declined``.

    DERIVED, never stored.

    Two sources, in order. The request row wins when there is one. With no
    row, ``admitted_at`` decides: 0009's backfill covers every account that
    existed WHEN IT RAN, but an account admitted afterwards by any other
    path — the owner running one UPDATE, which is exactly how `is_admin` is
    granted — would otherwise compute as ``needs_onboarding`` and be shown
    the onboarding form despite already being admitted. Falling back to the
    flag every gate already reads keeps the two from disagreeing.
    """
    with db.cursor() as cur:
        cur.execute(
            "select status from public.access_requests where user_id = %s", (user_id,)
        )
        row = cur.fetchone()
        if row:
            return row["status"]
        # No request on file. An account already carrying admitted_at is
        # admitted; anything else has not asked yet.
        cur.execute(
            "select admitted_at from public.profiles where id = %s", (user_id,)
        )
        profile = cur.fetchone()
    return "admitted" if profile and profile["admitted_at"] else "needs_onboarding"


def email_for_user(db: psycopg.Connection, user_id: str) -> str | None:
    """The signup address, from ``auth.users``.

    Read here rather than from the JWT: the access token's ``email`` claim
    is not guaranteed present, and this API already holds the service-role
    key that can see the table. Never written — that schema is Supabase's.
    """
    with db.cursor() as cur:
        cur.execute("select email from auth.users where id = %s", (user_id,))
        row = cur.fetchone()
    return row["email"] if row else None


def profile_is_admin(db: psycopg.Connection, user_id: str) -> bool:
    with db.cursor() as cur:
        cur.execute("select is_admin from public.profiles where id = %s", (user_id,))
        row = cur.fetchone()
    return bool(row and row["is_admin"])


def submit_access_request(
    db: psycopg.Connection,
    user_id: str,
    submission: "OnboardingSubmission",
    *,
    email_domain: str | None,
    is_personal_email: bool | None,
) -> None:
    """Write the profile facts and create (or update) the pending request.

    One transaction: a profile written without its request row would leave
    the account computing as ``needs_onboarding`` with the form already
    filled, and it would be shown again with everything blank.

    Deliberately does NOT touch ``admitted_at``. Submitting is asking.

    ``display_name`` is only SEEDED — ``coalesce`` leaves a name the user
    chose alone, so filling this form never renames somebody.
    """
    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                """
                insert into public.profiles
                    (id, first_name, last_name, company_name, role, team_size,
                     email_domain, is_personal_email, display_name)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (id) do update
                   set first_name        = excluded.first_name,
                       last_name         = excluded.last_name,
                       company_name      = excluded.company_name,
                       role              = excluded.role,
                       team_size         = excluded.team_size,
                       email_domain      = excluded.email_domain,
                       is_personal_email = excluded.is_personal_email,
                       display_name      = coalesce(public.profiles.display_name,
                                                    excluded.display_name)
                """,
                (
                    user_id,
                    submission.first_name,
                    submission.last_name,
                    submission.company_name,
                    submission.role,
                    submission.team_size,
                    email_domain,
                    is_personal_email,
                    f"{submission.first_name} {submission.last_name}",
                ),
            )
            cur.execute(
                """
                insert into public.access_requests
                    (user_id, status, use_case, compute_sources, heard_from)
                values (%s, 'pending', %s, %s, %s)
                on conflict (user_id) do update
                   set use_case        = excluded.use_case,
                       compute_sources = excluded.compute_sources,
                       heard_from      = excluded.heard_from,
                       requested_at    = now()
                 where public.access_requests.status = 'pending'
                """,
                (
                    user_id,
                    submission.use_case,
                    submission.compute_sources,
                    submission.heard_from,
                ),
            )


def record_pending_invite(
    db: psycopg.Connection, user_id: str, *, pool_id: str, invited_by: str
) -> None:
    """Bank a workspace invite redeemed before approval.

    Creates a stub request if the account has not onboarded yet, so an
    invite clicked before the form is never lost. The stub is still
    ``pending`` — banking an invite is not being admitted.

    "Stub" covers a missing ``access_requests`` row only. THE CALLER MUST
    ENSURE THE ``public.profiles`` ROW EXISTS FIRST: this table's
    ``user_id`` is a foreign key to ``public.profiles(id)``, so calling
    this for an account with no profile raises ``ForeignKeyViolation``
    rather than stubbing anything. Route it after ``upsert_profile``.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.access_requests
                (user_id, status, pending_pool_id, invited_by)
            values (%s, 'pending', %s, %s)
            on conflict (user_id) do update
               set pending_pool_id = excluded.pending_pool_id,
                   invited_by      = excluded.invited_by
             where public.access_requests.status = 'pending'
            """,
            (user_id, pool_id, invited_by),
        )


def approve_access_request(
    db: psycopg.Connection, user_id: str, *, decided_by: str
) -> bool:
    """Admit the account and materialise any banked workspace join.

    ONE TRANSACTION, deliberately: an approval that admits but silently
    drops the queued pool join puts the person in a console with no pool,
    which is indistinguishable from the invite never having worked.

    Returns False for an account with no pending request — already decided,
    or never asked — so the route can 404 rather than report a success that
    changed nothing.
    """
    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                """
                update public.access_requests
                   set status = 'admitted', decided_at = now(), decided_by = %s
                 where user_id = %s and status = 'pending'
             returning pending_pool_id
                """,
                (decided_by, user_id),
            )
            row = cur.fetchone()
            if row is None:
                return False

            cur.execute(
                "update public.profiles set admitted_at = coalesce(admitted_at, now()) "
                " where id = %s",
                (user_id,),
            )

            if row["pending_pool_id"] is not None:
                cur.execute(
                    """
                    insert into public.pool_members (pool_id, user_id)
                    values (%s, %s)
                    on conflict (pool_id, user_id) do nothing
                    """,
                    (row["pending_pool_id"], user_id),
                )
    return True


def decline_access_request(
    db: psycopg.Connection, user_id: str, *, decided_by: str
) -> bool:
    """Refuse the request. ``admitted_at`` is left alone rather than
    cleared: this route decides a pending request, and using it to revoke
    an already-admitted account would be a different, unaudited action."""
    with db.cursor() as cur:
        cur.execute(
            """
            update public.access_requests
               set status = 'declined', decided_at = now(), decided_by = %s
             where user_id = %s and status = 'pending'
            """,
            (decided_by, user_id),
        )
        return cur.rowcount == 1


def list_access_requests(
    db: psycopg.Connection, *, status: str = "pending"
) -> list[dict[str, Any]]:
    """The queue. Joins ``auth.users`` for the address — possible only
    because this API holds the service-role key; a browser cannot reach
    that table, which is the entire reason this is a server route."""
    with db.cursor() as cur:
        cur.execute(
            """
            select ar.user_id, ar.status, ar.use_case, ar.compute_sources,
                   ar.heard_from, ar.requested_at, ar.pending_pool_id,
                   ar.invited_by,
                   u.email,
                   p.first_name, p.last_name, p.company_name, p.role,
                   p.team_size, p.email_domain, p.is_personal_email,
                   po.name as pending_pool_name,
                   inv.display_name as invited_by_name
              from public.access_requests ar
              join public.profiles p on p.id = ar.user_id
              left join auth.users u on u.id = ar.user_id
              left join public.pools po on po.id = ar.pending_pool_id
              left join public.profiles inv on inv.id = ar.invited_by
             where ar.status = %s
             order by ar.requested_at
            """,
            (status,),
        )
        return list(cur.fetchall())


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


def set_machine_capabilities(
    db: psycopg.Connection,
    *,
    machine_id: str,
    sandbox_capable: bool,
    argv_capable: bool,
    unsandboxed_argv_capable: bool,
    module_capable: bool,
) -> None:
    """Overwrite the display-only capability snapshot from the latest
    registration (register proxy, best-effort — see migration 0008's
    header). A single UPDATE, all four columns together, so a machine that
    re-registers with a narrower capability set shows the narrower set —
    not the union of every registration it has ever made. Never read by
    placement or authorization; that stays server-side only, same as
    ``machines.capabilities`` above it.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            update public.machines
               set sandbox_capable = %s,
                   argv_capable = %s,
                   unsandboxed_argv_capable = %s,
                   module_capable = %s
             where id = %s
            """,
            (sandbox_capable, argv_capable, unsandboxed_argv_capable,
             module_capable, machine_id),
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
    "sandbox_capable", "argv_capable", "unsandboxed_argv_capable",
    "module_capable",
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
    ``is_pool_member``, ``pool_ids_for_machine``, ``list_pools_for_user``
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


def rename_pool(
    db: psycopg.Connection, *, pool_id: str, name: str
) -> dict[str, Any] | None:
    """Set a pool's name and return the updated row.

    Takes no viewer or owner argument on purpose: authorization for a write
    this consequential belongs at the route, checked against the pool's own
    row before anything is written, the same shape ``revoke_pool_invites``
    uses. None here therefore means the row vanished between that check and
    this write — not that the caller was refused.
    """
    with db.cursor() as cur:
        cur.execute(
            "update public.pools set name = %s where id = %s returning *",
            (name, pool_id),
        )
        return cur.fetchone()


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

    ``machines_online`` counts bound, online machines only: the
    ``machine_pools`` join narrows the owner-inherited candidate set from
    ``machines`` down to machines actually opted into *this* pool, the same
    intersection ``pool_ids_for_machine`` applies for the single-machine
    case. A member's machine that is merely owned, not bound, must not
    inflate the pool's online count — it serves nothing for this pool.
    ``member_count`` is untouched by this: pool membership is independent
    of any machine binding.
    """
    with db.cursor() as cur:
        cur.execute(
            f"""
            select p.id, p.name, p.owner_id,
                   count(distinct pm.user_id) as member_count,
                   count(distinct m.id) filter (
                       where mp.pool_id is not null
                         and {MACHINE_ONLINE_PREDICATE}
                   ) as machines_online,
                   p.created_at
              from public.pools p
              join public.pool_members pm on pm.pool_id = p.id
              left join public.machines m on m.owner_id = pm.user_id
              left join public.machine_pools mp
                on mp.machine_id = m.id and mp.pool_id = p.id
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

    ``machine_count``/``machines_online`` are per-member *and* per-pool: a
    member's candidate machines are resolved via
    ``machines.owner_id -> pool_members.user_id`` (a machine is never a
    member in its own right), but that ownership join alone is only the
    candidate set. Counting lands on a machine only once it is also bound
    to *this* pool via ``machine_pools`` — the same intersection
    ``pool_ids_for_machine`` applies for the single-machine case — so a
    member's machine that is merely owned, not opted in, contributes 0 to
    both counts.
    """
    with db.cursor() as cur:
        cur.execute(
            f"""
            select pm.user_id, pr.display_name, pm.joined_at,
                   count(distinct m.id) filter (
                       where mp.pool_id is not null
                   ) as machine_count,
                   count(distinct m.id) filter (
                       where mp.pool_id is not null
                         and {MACHINE_ONLINE_PREDICATE}
                   ) as machines_online
              from public.pool_members pm
              join public.profiles pr on pr.id = pm.user_id
              left join public.machines m on m.owner_id = pm.user_id
              left join public.machine_pools mp
                on mp.machine_id = m.id and mp.pool_id = pm.pool_id
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


def pool_ids_for_machine(db: psycopg.Connection, machine_id: str) -> list[str]:
    """Pools this MACHINE serves: its explicit bindings, intersected with
    its owner's live memberships.

    The join through ``machines`` to ``pool_members`` is the authority
    check: a binding to a pool the owner has left (or was removed from)
    must be inert — otherwise removing a member would leave their machines
    still claiming the pool's unsandboxed work through stale bindings.
    Opt-in is the default by construction: no binding row, no pools.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select mp.pool_id
              from public.machine_pools mp
              join public.machines m  on m.id = mp.machine_id
              join public.pool_members pm
                on pm.pool_id = mp.pool_id and pm.user_id = m.owner_id
             where mp.machine_id = %s
            """,
            (machine_id,),
        )
        return sorted(str(row["pool_id"]) for row in cur.fetchall())


def bind_machine_pool(db: psycopg.Connection, *, machine_id: str, pool_id: str) -> None:
    """Opt one machine into serving one pool. ``on conflict do nothing`` —
    the same idempotent-write idiom ``consume_pool_invite`` uses for pool
    membership — so a caller that re-sends an already-bound pair (a UI
    double-click, a retried request) does not raise a duplicate-key error."""
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.machine_pools (machine_id, pool_id)
            values (%s, %s)
            on conflict do nothing
            """,
            (machine_id, pool_id),
        )


def unbind_machine_pool(db: psycopg.Connection, *, machine_id: str, pool_id: str) -> None:
    """Opt one machine out of serving one pool. A no-op, not an error, when
    the pair was never bound — the same tolerant-delete stance
    ``revoke_machine_row`` takes: the caller's desired end state (unbound)
    already holds."""
    with db.cursor() as cur:
        cur.execute(
            "delete from public.machine_pools where machine_id = %s and pool_id = %s",
            (machine_id, pool_id),
        )


def pools_for_machines_of_owner(
    db: psycopg.Connection, owner_id: str
) -> dict[str, list[dict[str, Any]]]:
    """Every pool binding for every machine ``owner_id`` owns, as the chip
    map the machines page renders — one query rather than one per machine,
    the same reasoning ``list_pools_for_user`` gives for its own aggregate.

    A machine with no bindings is simply absent from the returned dict
    (callers default to ``[]``), not present with an empty list — the join
    below produces no row at all for it, and there is no reason to manufacture
    one.

    Joined through ``pool_members`` on the owner, same as
    ``pool_ids_for_machine``'s own join: without it, a binding to a pool the
    owner has since left would still render a chip here even though the
    stamp that same binding feeds correctly treats it as inert. The chip
    map and the stamp must agree on which pools a machine actually serves.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select m.id as machine_id, p.id as pool_id, p.name as pool_name
              from public.machines m
              join public.machine_pools mp on mp.machine_id = m.id
              join public.pools p on p.id = mp.pool_id
              join public.pool_members pm
                on pm.pool_id = mp.pool_id and pm.user_id = m.owner_id
             where m.owner_id = %s
            """,
            (owner_id,),
        )
        chips: dict[str, list[dict[str, Any]]] = {}
        for row in cur.fetchall():
            chips.setdefault(str(row["machine_id"]), []).append(
                {"id": str(row["pool_id"]), "name": row["pool_name"]}
            )
        return chips


def list_pool_machines(
    db: psycopg.Connection, pool_id: str
) -> list[dict[str, Any]]:
    """Every machine bound to ``pool_id``, across all of its members, with
    the owner label the console renders beside each row.

    ``list_machines_for_owner`` cannot answer this and is not supposed to:
    it is scoped to one caller by design, so it shows you your own machines
    and none of your teammates'. This is the workspace-wide view — what
    compute the pool actually has.

    Joined against live ``pool_members``, the same guard
    ``pool_ids_for_machine`` and ``pools_for_machines_of_owner`` both apply:
    a binding left behind by an owner who has since left the pool is already
    inert for placement, so listing it here would overstate the workspace's
    capacity to every member looking at it. The three views must agree on
    which machines a pool actually has.

    Revoked machines are NOT filtered out. A revoked machine's token is dead
    and it can never claim work, but it is still a row the workspace can see,
    and the console renders its status — unlike the opt-in checkbox list,
    which filters them because ticking one would be meaningless.
    """
    columns = ", ".join(f"m.{c}" for c in MACHINE_PUBLIC_COLUMNS)
    with db.cursor() as cur:
        cur.execute(
            f"""
            select {columns},
                   m.owner_id,
                   pr.display_name as owner_display_name
              from public.machine_pools mp
              join public.machines m on m.id = mp.machine_id
              join public.pool_members pm
                on pm.pool_id = mp.pool_id and pm.user_id = m.owner_id
              left join public.profiles pr on pr.id = m.owner_id
             where mp.pool_id = %s
             order by m.created_at
            """,
            (pool_id,),
        )
        return list(cur.fetchall())


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
    """Redeem an invite: decrement its use, then either join the pool or
    bank the join for later — or refuse both at once.

    ADMISSION IS NO LONGER THIS FUNCTION'S BUSINESS (0009). It used to
    write ``admitted_at`` as well, which made a workspace invite the
    product's only front door and left an uninvited signup with nothing to
    ask for. Access is now an account property an admin decides, in
    ``approve_access_request``; pool membership stays a workspace property
    its owner decides. So:

    * an already-admitted caller joins ``pool_members`` immediately,
      exactly as before;
    * anyone else has the join BANKED on their access request by
      ``record_pending_invite``, and ``approve_access_request``
      materialises it the moment somebody approves them.

    Returns ``{"pool_id", "name", "created_by", "admitted"}`` on success —
    ``admitted`` is what the caller ALREADY WAS, and therefore says which
    of the two happened — or ``None`` for every refusal case together
    (unknown token, expired, already exhausted) without distinguishing
    which — the same reason ``claim_attempt_credit`` and
    ``claim_device_code_for_redemption`` fold their refusal cases into one
    ``None``: telling a guesser *which* reason an invite failed for is a
    small oracle for free.

    The decrement is one ``UPDATE ... WHERE ... RETURNING`` — the
    ``claim_attempt_credit`` idiom — so that two redemptions of the same
    one-use invite arriving together cannot both win the row; only one
    ``UPDATE`` can match ``uses_remaining > 0`` before the other sees the
    decremented value.

    A use is spent even when the join is only banked, and declining that
    person later does not hand it back. That cost is deliberate: holding
    the use until approval would let a single link be claimed by an
    unlimited number of pending accounts.

    Decrement and join-or-bank are one transaction (``db.transaction()``,
    explicit despite this connection being autocommit — psycopg supports
    that). A decrement that "succeeded" while neither the membership nor
    the banked row landed would burn a one-use invite for nothing.
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
                returning pool_id, created_by
                """,
                (token_hash,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            pool_id = row["pool_id"]
            created_by = row["created_by"]

            cur.execute(
                "select admitted_at from public.profiles where id = %s",
                (user_id,),
            )
            profile = cur.fetchone()
            admitted = bool(profile and profile["admitted_at"])

            if admitted:
                cur.execute(
                    """
                    insert into public.pool_members (pool_id, user_id)
                    values (%s, %s)
                    on conflict do nothing
                    """,
                    (pool_id, user_id),
                )
            else:
                # The invite TOKEN is the authorization for this pool_id;
                # it was verified by the UPDATE above, which is why
                # record_pending_invite deliberately checks nothing itself.
                record_pending_invite(
                    db, user_id, pool_id=pool_id, invited_by=created_by
                )

            cur.execute(
                "select name from public.pools where id = %s",
                (pool_id,),
            )
            pool_row = cur.fetchone()
            assert pool_row is not None
            return {
                "pool_id": pool_id,
                "name": pool_row["name"],
                "created_by": created_by,
                "admitted": admitted,
            }


def fetch_outstanding_invite(db: psycopg.Connection, pool_id: str) -> dict[str, Any] | None:
    """The newest still-redeemable invite for ``pool_id``, or None.

    "Newest valid" is ``order by created_at desc limit 1`` over the same
    validity predicate ``consume_pool_invite`` enforces at redemption time
    (``expires_at > now() and uses_remaining > 0``) — an expired or
    exhausted invite is not "outstanding" even though its row still exists,
    same as a revoked machine is not "active" even though its row still
    exists.

    The returned dict is deliberately narrow: ``uses_remaining``,
    ``expires_at``, ``created_at`` only — never ``token_hash``. This is the
    one place ``POOL_PUBLIC_COLUMNS`` warns about in its own docstring: an
    "outstanding invite" surface that leaked the hash would hand a caller
    the one thing ``create_pool_invite`` exists to keep off this table's
    read path.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select uses_remaining, expires_at, created_at
              from public.pool_invites
             where pool_id = %s
               and expires_at > now()
               and uses_remaining > 0
             order by created_at desc
             limit 1
            """,
            (pool_id,),
        )
        return cur.fetchone()


def revoke_pool_invites(db: psycopg.Connection, *, pool_id: str) -> int:
    """Delete every invite ever issued for ``pool_id`` — valid and already-
    spent alike — and return how many rows that was.

    All of them, not just the outstanding one: a spent invite's row is
    otherwise inert, but leaving it behind is not what "revoke" means to a
    caller who just asked this pool's invite links to stop existing.
    ``returning token_hash`` + ``len(fetchall())`` mirrors
    ``claim_attempt_credit``'s discipline of returning only what a caller
    needs — a count, here, with the hashes read off the wire and discarded
    rather than the digests of soon-to-be-dead credentials leaving this
    function at all.
    """
    with db.cursor() as cur:
        cur.execute(
            "delete from public.pool_invites where pool_id = %s returning token_hash",
            (pool_id,),
        )
        return len(cur.fetchall())


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


def list_job_scopes_for_viewer(
    db: psycopg.Connection, user_id: str
) -> dict[str, dict[str, Any]]:
    """Every job id ``user_id`` can see — owned outright, or reachable
    through a shared pool — mapped to the two fields the console scopes and
    labels on: which pool the job belongs to, and who submitted it.

    Replaces the ``list_job_ids_for_owner`` + ``list_pool_job_ids_for_member``
    pair at ``list_jobs_route``. Those ran the owner half and the pool half
    as two queries and unioned the ids in Python, throwing away the
    ``pool_id`` that came back with them. This is the same union expressed
    once in SQL, and it keeps that column — so the route gets a scoping
    filter and a display mapping out of strictly less work than before.

    ``pool_id`` is None for every pre-pools job. Those rows are reachable by
    their owner alone: a null pool can never match the ``pool_members`` half
    of the check, exactly as ``fetch_job_for_viewer`` documents for itself.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select j.id, j.pool_id, pr.display_name as submitted_by
              from public.jobs j
              left join public.profiles pr on pr.id = j.owner_id
             where j.owner_id = %s
                or exists (
                     select 1 from public.pool_members pm
                      where pm.pool_id = j.pool_id and pm.user_id = %s
                   )
            """,
            (user_id, user_id),
        )
        return {
            row["id"]: {
                "pool_id": None if row["pool_id"] is None else str(row["pool_id"]),
                "submitted_by": row["submitted_by"],
            }
            for row in cur.fetchall()
        }


def display_name_for(db: psycopg.Connection, user_id: str) -> str | None:
    """The profile display name for ``user_id``. None when the profile row
    does not exist yet (a brand-new sign-in that has not hit ``upsert_profile``)
    or the name was never set — both are "no label to show", and the caller
    renders the same fallback for each."""
    with db.cursor() as cur:
        cur.execute(
            "select display_name from public.profiles where id = %s", (user_id,)
        )
        row = cur.fetchone()
        return row["display_name"] if row else None


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


def list_federated_jobs_for_viewer(
    db: psycopg.Connection, user_id: str
) -> list[dict[str, Any]]:
    """Every federated run ``user_id`` can see — as owner, or as a member of
    the run's pool — widened from ``list_federated_jobs_for_owner`` exactly
    as ``fetch_job_for_viewer`` widens ``fetch_job_for_owner``.

    Without this, a pool member could still *open* a teammate's federated
    job directly by id (``fetch_job_for_viewer`` already admits them) but
    could never *discover* it through ``GET /v1alpha1/jobs`` — federated ids
    never appear in the coordinator's own list, and
    ``list_federated_jobs_for_owner`` is owner-only, so the run would be
    invisible to exactly the teammates it was submitted to share.

    A job with a null ``pool_id`` (every pre-pools federated run, and every
    federated run submitted with no ``pool``) can never match the
    ``pool_members`` half of the check — there is no pool to be a member of
    — so it stays reachable by its owner only, the same null-``pool_id``
    behaviour ``fetch_job_for_viewer`` documents.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select id, name, status, created_at, finished_at
              from public.jobs j
             where source->>'mode' = 'federated'
               and (
                     j.owner_id = %s
                  or exists (
                       select 1 from public.pool_members pm
                        where pm.pool_id = j.pool_id and pm.user_id = %s
                     )
               )
             order by created_at
            """,
            (user_id, user_id),
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
