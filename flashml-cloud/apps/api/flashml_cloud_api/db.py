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

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from flashml_cloud_api import marketplace as marketplacemod
from flashml_cloud_api.router.estimator import hardware_class
from flashml_cloud_api.settings import Settings

#: Same logger as the app. Everything logged from this module is a
#: BEST-EFFORT accounting hop that was allowed to fail — never a request the
#: caller is still waiting on, and never a failure the caller could act on.
log = logging.getLogger("flashml-cloud-api")

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


@dataclass(frozen=True)
class CliCredential:
    """A row from ``public.cli_credentials``, as returned to callers that
    have already authenticated the credential (never constructed from
    caller-supplied data). ``token_hash`` is deliberately absent — nothing
    that leaves this module needs it."""

    id: str
    owner_id: str
    label: str | None
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
                      created_at, first_name, last_name, company_name, role,
                      team_size, email_domain, is_personal_email
            """,
            (user_id, display_name),
        )
        row = cur.fetchone()
        assert row is not None
        return row


def update_profile_fields(
    db: psycopg.Connection, user_id: str, **fields: str
) -> dict[str, Any]:
    """Set exactly the named columns and return the whole row.

    The caller decides which fields are writable; this refuses to be a
    generic column setter by whitelisting here as well, so a future caller
    cannot turn it into one by accident.
    """
    allowed = {
        "display_name", "first_name", "last_name", "company_name",
        "role", "team_size",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"not a writable profile field: {', '.join(sorted(unknown))}")
    if not fields:
        return upsert_profile(db, user_id)

    assignments = ", ".join(f"{name} = %s" for name in fields)
    with db.cursor() as cur:
        upsert_profile(db, user_id)  # guarantee the row exists
        cur.execute(
            f"""
            update public.profiles set {assignments}
             where id = %s
         returning id, display_name, github_login, is_host, is_developer,
                   created_at, first_name, last_name, company_name, role,
                   team_size, email_domain, is_personal_email
            """,
            (*fields.values(), user_id),
        )
        return cur.fetchone()


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

    ONE ROW IS NOT A REQUEST. ``record_pending_invite`` stubs a ``pending``
    row for an account that redeemed a workspace invite before it ever saw
    the form — the primary invited-teammate path. Reporting that stub as
    ``pending`` would park a brand-new account on "we'll get back to you"
    forever: it is never offered the form, and its admin-queue row renders
    with a NULL name, company, role, and use case. A NULL ``use_case`` is
    the reliable marker of a stub, because a submitted row cannot have one
    — ``parse_submission`` rejects an empty ``use_case`` before
    ``submit_access_request`` is ever reached. Only ``pending`` is treated
    this way: a decided row says what an admin decided, and 0009's backfill
    writes ``admitted`` rows with no ``use_case`` on purpose.
    """
    with db.cursor() as cur:
        cur.execute(
            "select status, use_case from public.access_requests where user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        if row:
            if row["status"] == "pending" and row["use_case"] is None:
                return "needs_onboarding"
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
                    (user_id, status, use_case, compute_sources, heard_from,
                     linkedin_url)
                values (%s, 'pending', %s, %s, %s, %s)
                on conflict (user_id) do update
                   set use_case        = excluded.use_case,
                       compute_sources = excluded.compute_sources,
                       heard_from      = excluded.heard_from,
                       linkedin_url    = excluded.linkedin_url,
                       requested_at    = now()
                 where public.access_requests.status = 'pending'
                """,
                (
                    user_id,
                    submission.use_case,
                    submission.compute_sources,
                    submission.heard_from,
                    submission.linkedin_url,
                ),
            )


def record_pending_invite(
    db: psycopg.Connection, user_id: str, *, pool_id: str, invited_by: str
) -> bool:
    """Bank a workspace invite redeemed before approval.

    Creates a stub request if the account has not onboarded yet, so an
    invite clicked before the form is never lost. The stub is still
    ``pending`` — banking an invite is not being admitted.

    "Stub" covers a missing ``access_requests`` row only. THE CALLER MUST
    ENSURE THE ``public.profiles`` ROW EXISTS FIRST: this table's
    ``user_id`` is a foreign key to ``public.profiles(id)``, so calling
    this for an account with no profile raises ``ForeignKeyViolation``
    rather than stubbing anything. Route it after ``upsert_profile``.

    Returns whether a row was actually banked. The upsert's
    ``where status = 'pending'`` refuses to touch a DECIDED request, which
    is right — a declined account must not re-queue itself by clicking a
    link — but it refuses SILENTLY, and the caller has already spent one
    use of somebody else's invite by the time it gets here. So the outcome
    is reported rather than swallowed: ``consume_pool_invite`` turns False
    into a refusal that rolls the decrement back.

    Performs NO authorization on ``pool_id`` or ``invited_by``, deliberately
    — the invite TOKEN is the authorization, and the caller verifies it.
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
        return cur.rowcount == 1


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
                   ar.heard_from, ar.linkedin_url, ar.requested_at,
                   ar.pending_pool_id, ar.invited_by,
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
    lifecycle: str = "persistent",
) -> None:
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.device_codes
                (device_code, user_code, node_id, hostname, platform, expires_at,
                 lifecycle)
            values (%s, %s, %s, %s, %s, %s, %s)
            """,
            (device_code, user_code, node_id, hostname, platform, expires_at,
             lifecycle),
        )


def fetch_device_code(
    db: psycopg.Connection, device_code: str
) -> dict[str, Any] | None:
    """Read a device code by its long half. Used only to learn which flow
    a code belongs to before redemption — the redemption itself stays the
    atomic claim, so this read cannot introduce a race: a code that changes
    hands between this SELECT and that UPDATE still redeems exactly once,
    and reading the wrong kind would only route it to a claim query whose
    ``kind =`` filter then matches nothing."""
    with db.cursor() as cur:
        cur.execute(
            "select * from public.device_codes where device_code = %s",
            (device_code,),
        )
        return cur.fetchone()


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
    lifecycle: str = "persistent",
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
            insert into public.machines
                (owner_id, node_id, name, platform, status, lifecycle)
            values (%s, %s, %s, %s, 'pending', %s)
            returning id
            """,
            (owner_id, node_id, name, platform, lifecycle),
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

    **THIS COLUMN IS NOW LOAD-BEARING FOR MONEY, NOT ONLY FOR DISPLAY.**
    `capacity/reconcile.py` selects rentals to destroy on it: a rented GPU
    whose machine has not been seen for `quiet_after_s` is swept, and one that
    was never seen at all is destroyed at `boot_grace_s`. So this write is what
    stands between a live rental and its destruction, and the two ways to break
    it are not symmetrical:

    * **Stop writing it** and every rented machine looks dead — destroyed 15
      minutes into a healthy job, silently, on every rental.
    * **Write it for a machine that is not really speaking** and a rental
      billing for nothing looks alive for ever.

    Deliberately best-effort at the call site even so: a machine's work must
    not fail because this could not be written, and the failure mode of the
    first kind is bounded by `capacity.reconcile`'s other inputs (a job in a
    terminal state, or a machine claiming no leases) which do not read this
    column at all. That is why the cost backstop was not built on liveness.
    Anything that changes when or whether this is written belongs in the same
    conversation as `capacity/reconcile.py`.
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
    lifecycle: str = "persistent",
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
                   platform = %s,
                   lifecycle = %s
             where id = %s
            returning id
            """,
            (name, platform, lifecycle, machine_id),
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


#: Fields of ``NodeCapabilities`` this API keeps a copy of, and the type each
#: has to arrive as to be kept.
#:
#: **The allowlist is the point, and ``pools`` is the reason it is one.** A
#: node's pools are stamped SERVER-SIDE by the register proxy from the
#: owner's live memberships precisely because an agent may not name its own;
#: copying the agent's claim into the same jsonb would put an unauthorised
#: value one careless read away from being believed. Everything here is a
#: statement about the host's own hardware, which is the one subject an agent
#: is the authority on.
#:
#: ``cpu_cores`` and ``gpus`` are what ``router.estimator.hardware_class``
#: and ``marketplace.capability_class`` read, and until 2026-08-11 nothing
#: wrote them: the column held ``{"dataset_cache_bytes": n}`` and nothing
#: else, so every machine in the deployment classed as ``cpu-small`` — a
#: 4090 rig included — and no acceptance rate could be keyed by class at all.
_REPORTED_CAPABILITY_FIELDS: tuple[tuple[str, tuple[type, ...]], ...] = (
    ("cpu_cores", (int, float)),
    ("memory_bytes", (int, float)),
    ("gpus", (list,)),
    ("os", (str,)),
    ("architecture", (str,)),
)


def _reported_capabilities(reported: Mapping[str, Any] | None) -> dict[str, Any]:
    """The allowlisted hardware snapshot, with every key always present.

    Always present, including as ``None``: a machine that re-registers
    without a field must RETRACT what it advertised before, and a merge that
    only writes the keys it was given cannot do that. A driver that broke
    between two registrations would otherwise keep selling the GPU it can no
    longer see.

    ``bool`` is refused where a number is expected for the reason
    ``verify._as_finite_float`` refuses it: ``cpu_cores: true`` would arrive
    as one core, a plausible number derived from something that was never a
    measurement.
    """
    source = reported if isinstance(reported, Mapping) else {}
    snapshot: dict[str, Any] = {}
    for name, types in _REPORTED_CAPABILITY_FIELDS:
        value = source.get(name)
        if isinstance(value, bool) or not isinstance(value, types):
            snapshot[name] = None
            continue
        if name == "gpus":
            # Devices only, as mappings. A list of strings would satisfy
            # `isinstance(list)` and then class every entry as unreadable.
            snapshot[name] = [g for g in value if isinstance(g, Mapping)]
        else:
            snapshot[name] = value
    return snapshot


def set_machine_capabilities(
    db: psycopg.Connection,
    *,
    machine_id: str,
    sandbox_capable: bool,
    argv_capable: bool,
    unsandboxed_argv_capable: bool,
    module_capable: bool,
    dataset_cache_bytes: int = 0,
    reported: Mapping[str, Any] | None = None,
) -> None:
    """Overwrite the capability snapshot from the latest registration
    (register proxy, best-effort — see migration 0008's header). A single
    UPDATE, every field together, so a machine that re-registers with a
    narrower capability set shows the narrower set — not the union of every
    registration it has ever made.

    **The four booleans stay display-only.** They are never read by
    placement or authorization, and nothing here changes that.

    ``dataset_cache_bytes`` is the exception, and it is deliberate: it is
    room, not permission — how many bytes of a declared dataset this machine
    is willing to hold — and ``dataset_capacity_in_pool`` reads it at submit
    time so a job whose slices fit on nobody is refused in the console
    instead of after twenty machines each download for forty minutes. It
    grants no access and relaxes no gate; the worst a machine can do by
    over-advertising is take work it then fails to fetch, which is the
    ordinary failure it could already cause by unplugging itself.

    It lands in the ``capabilities`` jsonb rather than a new column
    (migration 0001 already ships that column, defaulted to ``{}`` and, until
    now, written by nothing). A merge, not a replace, so a future key written
    by something else is not silently dropped — but the key itself is always
    written, including as ``0``, because absent and ``0`` must mean the same
    thing here: send me no dataset work. That is the same polarity the
    runtime's own placement gate applies.

    Defaulting to ``0`` keeps the parameter optional for callers that have
    nothing to say about datasets, and re-registering without the field
    correctly retracts a capacity a machine used to advertise.

    ``reported`` is the agent's own ``NodeCapabilities`` object, and the
    allowlisted part of it (:data:`_REPORTED_CAPABILITY_FIELDS`) is merged in
    beside ``dataset_cache_bytes``. It is the hardware description, nothing
    more: it is not read by placement, by authorization, or by any gate. It IS
    read by ``router.estimator.hardware_class`` and by
    ``marketplace.capability_class``, which is exactly why it has to be
    persisted — the class a machine may be listed and measured in is derived
    from what its driver reported, never from anything a person typed, and
    before this the column carried no reading to derive it from.

    Self-reported, and that is the trust level the marketplace already
    designed for: a host who lies about VRAM sells a promise their machine
    cannot keep and the buyer's task OOMs. Nothing here makes that better or
    worse; it makes the honest reading reach the ladder at all.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            update public.machines
               set sandbox_capable = %s,
                   argv_capable = %s,
                   unsandboxed_argv_capable = %s,
                   module_capable = %s,
                   capabilities = coalesce(capabilities, '{}'::jsonb)
                                  || %s::jsonb
                                  || jsonb_build_object(
                                         'dataset_cache_bytes', %s::bigint
                                     )
             where id = %s
            """,
            (sandbox_capable, argv_capable, unsandboxed_argv_capable,
             module_capable, Json(_reported_capabilities(reported)),
             int(dataset_cache_bytes), machine_id),
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
    would be a missing argument, not a missing ``if``.

    **A revoked machine whose identity was never permanent is not in this
    person's fleet and is hidden.** Two lifecycles qualify and the reason is
    the same for both: a rental session (``ephemeral``) and a GPU this control
    plane rented on their behalf (``leased``, migration 0023) are borrowed
    hardware, and once the credential is dead the row describes a machine
    somebody else now has. A revoked ``persistent`` machine is the opposite
    case and stays: a laptop its owner revoked on purpose is still theirs, and
    hiding it would hide the evidence that they did.
    """
    columns = ", ".join(MACHINE_PUBLIC_COLUMNS)
    with db.cursor() as cur:
        cur.execute(
            f"select {columns} from public.machines "
            "where owner_id = %s "
            "and not (lifecycle in ('ephemeral', 'leased') "
            "         and status = 'revoked') "
            "order by created_at",
            (owner_id,),
        )
        return list(cur.fetchall())


def expire_stale_ephemeral_machines(
    db: psycopg.Connection, *, stale_seconds: float
) -> list[str]:
    """Revoke and unbind rental sessions that stopped heartbeating.

    Persistent machines are deliberately outside this query: a laptop being
    off for a month does not transfer its identity to somebody else. Rental
    sessions opt into the shorter lifetime during device-code enrolment.

    **``leased`` machines are outside it too, and that is the whole reason
    they are not filed as ``ephemeral``.** A GPU this control plane rented has
    no ``last_seen_at`` until it has booted, pulled a multi-gigabyte image and
    enrolled — routinely longer than the 15 minutes ``stale_seconds`` defaults
    to — so this window would revoke the credential of a machine that is still
    starting up, on hardware we have already paid for. A lease is ended by the
    ``public.rented_capacity`` row that opened it (``capacity/reconcile.py``),
    never by age. Adding ``'leased'`` to the predicate below would break every
    slow-booting rental; see migration 0023.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            with expired as (
                update public.machines
                   set status = 'revoked', revoked_at = now()
                 where lifecycle = 'ephemeral'
                   and status <> 'revoked'
                   and coalesce(last_seen_at, created_at)
                       < now() - make_interval(secs => %s)
                returning id
            ), unbound as (
                delete from public.machine_pools
                 where machine_id in (select id from expired)
                returning machine_id
            )
            select id from expired order by id
            """,
            (float(stale_seconds),),
        )
        return [str(row["id"]) for row in cur.fetchall()]


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
# cli credentials
# ---------------------------------------------------------------------------

#: The columns of ``public.cli_credentials`` that may ever leave the API.
#: Spelled out rather than ``select *`` for the same reason
#: ``MACHINE_PUBLIC_COLUMNS`` is: ``token_hash`` lives in the same table, and
#: a ``select *`` feeding a JSON response is exactly how a credential digest
#: ends up in a browser.
CLI_CREDENTIAL_PUBLIC_COLUMNS = (
    "id", "label", "status", "token_prefix",
    "last_used_at", "created_at", "revoked_at",
)


def insert_cli_device_code(
    db: psycopg.Connection,
    *,
    device_code: str,
    user_code: str,
    label: str | None,
    expires_at: datetime,
) -> None:
    """A device code for the CLI flow. ``node_id`` is left null — the check
    constraint added in 0012 permits that only for ``kind = 'cli'``. The
    label rides in ``hostname``, the column that already carries "what the
    human will recognise this as"."""
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.device_codes
                (device_code, user_code, kind, hostname, expires_at)
            values (%s, %s, 'cli', %s, %s)
            """,
            (device_code, user_code, label, expires_at),
        )


def insert_cli_credential(
    db: psycopg.Connection, *, owner_id: str, label: str | None
) -> str:
    """Create the credential row at APPROVAL time, before any token exists.
    token_hash is not null, so a placeholder that cannot collide and cannot
    be presented is written and then overwritten by
    ``set_cli_credential_token``. The placeholder is not a valid sha256 hex
    digest, so no token can ever hash to it."""
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.cli_credentials
                (owner_id, label, token_hash, token_prefix)
            values (%s, %s, 'pending:' || gen_random_uuid()::text, '')
            returning id
            """,
            (owner_id, label),
        )
        row = cur.fetchone()
        assert row is not None
        return str(row["id"])


def mark_cli_device_code_approved(
    db: psycopg.Connection, user_code: str, user_id: str, credential_id: str
) -> None:
    with db.cursor() as cur:
        cur.execute(
            """
            update public.device_codes
               set credential_id = %s, approved_by = %s
             where user_code = %s and kind = 'cli'
            """,
            (credential_id, user_id, user_code),
        )


def claim_cli_device_code_for_redemption(
    db: psycopg.Connection, device_code: str
) -> str | None:
    """Atomically mark a CLI device_code consumed and return its
    credential_id — but only if it is approved, unexpired, and not already
    consumed. Returns None in every other case without distinguishing which,
    so a caller cannot use this as an oracle for which codes exist. The
    single ``UPDATE ... WHERE consumed_at is null ... RETURNING`` is what
    makes "redeemed exactly once" hold under concurrent attempts."""
    with db.cursor() as cur:
        cur.execute(
            """
            update public.device_codes
               set consumed_at = now()
             where device_code = %s
               and kind = 'cli'
               and consumed_at is null
               and credential_id is not null
               and expires_at > now()
            returning credential_id
            """,
            (device_code,),
        )
        row = cur.fetchone()
        return str(row["credential_id"]) if row else None


def set_cli_credential_token(
    db: psycopg.Connection, credential_id: str, token_hash: str, token_prefix: str
) -> None:
    with db.cursor() as cur:
        cur.execute(
            """
            update public.cli_credentials
               set token_hash = %s, token_prefix = %s, status = 'active'
             where id = %s
            """,
            (token_hash, token_prefix, credential_id),
        )


def fetch_cli_credential_by_token_hash(
    db: psycopg.Connection, token_hash: str
) -> dict[str, Any] | None:
    with db.cursor() as cur:
        cur.execute(
            "select * from public.cli_credentials where token_hash = %s",
            (token_hash,),
        )
        return cur.fetchone()


def touch_cli_credential_last_used(db: psycopg.Connection, credential_id: str) -> None:
    """Rate-limited in SQL, not in Python: the WHERE clause means a
    credential used a hundred times a second costs one write a minute, and
    it holds across API processes, which a Python-side cache would not."""
    with db.cursor() as cur:
        cur.execute(
            """
            update public.cli_credentials
               set last_used_at = now()
             where id = %s
               and (last_used_at is null or last_used_at < now() - interval '1 minute')
            """,
            (credential_id,),
        )


def list_cli_credentials_for_owner(
    db: psycopg.Connection, owner_id: str
) -> list[dict[str, Any]]:
    """Every credential belonging to owner_id, and nothing else. The owner
    filter is in the SQL, not applied afterwards in Python — omitting it
    would be a missing argument, not a missing ``if``."""
    columns = ", ".join(CLI_CREDENTIAL_PUBLIC_COLUMNS)
    with db.cursor() as cur:
        cur.execute(
            f"select {columns} from public.cli_credentials "
            "where owner_id = %s order by created_at",
            (owner_id,),
        )
        return list(cur.fetchall())


def revoke_cli_credential_row(
    db: psycopg.Connection, credential_id: str, owner_id: str
) -> bool:
    """Owner-scoped revoke. Returns True only if a row belonging to owner_id
    was actually updated — a bad id and an id owned by someone else both
    return False, indistinguishably."""
    with db.cursor() as cur:
        try:
            cur.execute(
                """
                update public.cli_credentials
                   set status = 'revoked', revoked_at = now()
                 where id = %s and owner_id = %s and status <> 'revoked'
                """,
                (credential_id, owner_id),
            )
        except psycopg.errors.InvalidTextRepresentation:
            # Not even a uuid. Same answer as "no such credential".
            return False
        return cur.rowcount > 0


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


def _lease_deadline(value: Any) -> datetime | None:
    """A lease deadline as a ``datetime``, or None for anything unusable.

    The claim and heartbeat proxies hand this straight off a coordinator
    response body, so it arrives as whatever JSON carried — normally the
    ISO-8601 string pydantic serialised a ``Lease.deadline`` into, sometimes
    (in-process callers, tests) an actual ``datetime``, and in the failure
    cases nothing at all or something that is not a timestamp.

    Parsed HERE rather than passed through to Postgres, because the write
    this feeds is best-effort and the value is not. A malformed string
    handed to a ``timestamptz`` parameter raises, and in ``record_attempt``
    that would abort the INSERT — costing the whole lease→(job, task)
    mapping, which is the row the credit path cannot work without, to save a
    column only the expiry reconciler reads. Unparseable therefore degrades
    to None: the attempt is recorded and is simply never reconciled.

    ``Z`` is rewritten to ``+00:00`` because ``fromisoformat`` did not accept
    it before Python 3.11 and the deployed floor is 3.10.
    """
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def record_attempt(
    db: psycopg.Connection,
    *,
    lease_id: str,
    machine_id: str,
    job_id: str,
    task_id: str,
    deadline: Any = None,
) -> None:
    """Remember that ``machine_id`` claimed ``lease_id`` for a task.

    This is the mapping the credit path needs and cannot otherwise get: the
    completion hop carries only a lease id, while ``contributions`` is keyed
    on ``(machine_id, job_id, task_id)``.

    ``on conflict do nothing`` because a claim that is forwarded twice — a
    retry, a duplicated request — describes one lease, not two.

    ``deadline`` is the coordinator's own ``Lease.deadline``, and it is the
    only reason an attempt nobody ever reports on can be resolved at all
    (see ``reconcile_expired_attempts``). Optional and defaulted so a caller
    that has no deadline to give records the attempt anyway — the mapping
    matters more than the metric.
    """
    with db.cursor() as cur:
        cur.execute(
            "insert into public.attempts"
            "            (lease_id, machine_id, job_id, task_id, lease_deadline)"
            "     values (%s, %s, %s, %s, %s)"
            " on conflict (lease_id) do nothing",
            (lease_id, machine_id, job_id, task_id, _lease_deadline(deadline)),
        )


def note_attempt_deadline(
    db: psycopg.Connection,
    *,
    lease_id: str,
    machine_id: str,
    deadline: Any,
) -> None:
    """Carry an attempt's known deadline forward after a renewed heartbeat.

    A heartbeat the coordinator accepts extends the lease, and this is the
    only place that fact reaches Postgres. Without it the deadline recorded
    at claim time would be the deadline for ever, and the expiry reconciler
    would call a healthy hour-long task dead sixty seconds in — the reason
    that reconciler must never run off ``claimed_at`` plus a guess.

    Scoped to the claiming machine and to UNRESOLVED rows only. A machine
    may not extend somebody else's lease here any more than it may at the
    coordinator, and a resolved attempt is finished: moving its deadline
    afterwards could only ever un-resolve a decision already made.

    Silently does nothing when the deadline is unusable, matching
    ``record_attempt`` — a heartbeat must never fail because of a column the
    agent does not know exists.
    """
    parsed = _lease_deadline(deadline)
    if parsed is None:
        return
    with db.cursor() as cur:
        cur.execute(
            "update public.attempts"
            "   set lease_deadline = %s"
            " where lease_id = %s and machine_id = %s and resolved_at is null",
            (parsed, lease_id, machine_id),
        )


# ---------------------------------------------------------------------------
# The hop from an attempt's terminal outcome to the credit ledger.
#
# `marketplace.py` SUPPORTS hard rule 4 — a buyer is never charged for work
# that was not accepted — and until this existed nothing ENFORCED it: the
# three writers below resolved an attempt as accepted / failed / expired and
# the escrow held against that attempt's lease stayed held for ever.
#
# Three properties, in the order they are argued:
#
# **The ledger write shares the outcome write's transaction.** Every writer
# below now opens one `db.transaction()` around both. psycopg makes a nested
# `transaction()` a SAVEPOINT, so `settle_accepted_work` and
# `refund_unaccepted_work` — which open their own — commit or roll back with
# the outcome rather than beside it. A crash between the two is not a state
# this can reach.
#
# **A ledger failure never costs the outcome.** That same savepoint is what
# makes the guarantee cheap: an exception inside it rolls back to the
# savepoint, clears the error state, and leaves the enclosing UPDATE intact,
# so `_close_out_attempt_money` can log and return. Losing the record that an
# attempt failed, in order to protect a credit entry, is the wrong trade — the
# attempt outcome is what every reliability number divides by, and the ledger
# hop is idempotently retryable (below) while a lost failure is not.
#
# **Idempotency is the unique index and nothing else.** No second guard, no
# "have we refunded this already?" column: `credit_entries`' expression unique
# index on (reason, ref_type, ref_id, account_id) makes a repeated refund
# write zero rows and report `refunded_zc = 0`. That is also what makes a
# retry safe after the failure case above — running the hop again for the same
# lease can only ever complete it, never double it.
# ---------------------------------------------------------------------------


def _live_match_for_attempt(
    db: psycopg.Connection, *, machine_id: str, job_id: str
) -> str | None:
    """The claimed entitlement this attempt was pulled under, or ``None``.

    An attempt carries a lease, a machine and a coordinator job id; a match
    carries a machine and (through its bid) a job id. That pair is the join,
    and it is the only one available: nothing writes a match id onto an
    attempt, because a match is not an assignment — the host pulled the work,
    and the entitlement is what made it eligible to.

    **``claimed`` only.** Escrow is held on claim and never on grant, so a
    granted match has nothing held against any lease and refunding it would
    write a movement of zero recording an event that did not happen.
    ``settled``/``refunded``/``expired`` are closed. A match stays ``claimed``
    across every lease it covers — `close_match` is what ends it — so this
    stays correct for a match covering many tasks.

    ``None`` is the ordinary answer and costs one indexed lookup: this
    deployment has no marketplace rows at all until somebody lists a machine,
    and an attempt with no entitlement behind it moves no money.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select m.id
              from public.matches m
              join public.bids b on b.id = m.bid_id
             where m.machine_id = %s::uuid
               and b.job_id = %s
               and m.state = 'claimed'
             order by m.claimed_at desc nulls last, m.granted_at desc, m.id
             limit 1
            """,
            (machine_id, job_id),
        )
        row = cur.fetchone()
    return str(row["id"]) if row is not None else None


def _close_out_attempt_money(
    db: psycopg.Connection,
    *,
    lease_id: str,
    machine_id: str,
    job_id: str,
    accepted_seconds: int | None = None,
) -> None:
    """Settle or refund the escrow held against one resolved attempt.

    ``accepted_seconds`` is the whole switch: a number means the coordinator
    accepted this attempt's output and the host is paid for that much work at
    the agreed rate; ``None`` means the attempt resolved without producing
    anything accepted (``failed``, ``expired``) and every credit held goes
    back to the buyer. Hard rule 4 is that switch and nothing else.

    **Must be called inside the transaction that wrote the outcome.** The
    whole body sits in its own nested `transaction()` — a savepoint — so that
    a ledger failure rolls back to here and the outcome write survives it. It
    is safe to call outside one; then the savepoint is an ordinary
    transaction.

    Never raises. A caller reaching this has already resolved an attempt, and
    there is nothing it could usefully do with an exception from a credit
    movement that is idempotently retryable.
    """
    try:
        with db.transaction():
            match_id = _live_match_for_attempt(
                db, machine_id=machine_id, job_id=job_id
            )
            if match_id is None:
                return
            if accepted_seconds is None:
                marketplacemod.refund_unaccepted_work(
                    db, match_id=match_id, lease_id=lease_id
                )
            else:
                marketplacemod.settle_accepted_work(
                    db,
                    match_id=match_id,
                    lease_id=lease_id,
                    accepted_seconds=max(int(accepted_seconds), 0),
                )
    except Exception:  # noqa: BLE001 - never lose an outcome over a ledger write
        log.warning(
            "could not %s escrow for lease %s; the attempt outcome stands and "
            "the movement is still idempotently retryable",
            "settle" if accepted_seconds is not None else "refund",
            lease_id,
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

    **The terminal outcome is written by this same statement**, not by a
    second one beside it. ``accepted_at`` and ``outcome='accepted'`` describe
    one event, and two writes for one event is how a ledger ends up with a
    credited attempt that the reliability page counts as unresolved.

    The guard stays on ``accepted_at is null`` and deliberately does NOT
    also require ``outcome is null``: an attempt the reconciler had inferred
    to be ``expired`` may still be credited here, and crediting it corrects
    the inference. The coordinator answering ``{"accepted": true}`` is the
    strongest evidence this API ever receives about an attempt — stronger
    than a deadline having appeared to pass — so it wins, and it must never
    be the case that a wrong inference costs a volunteer their credit.

    ``duration_s`` is lease-held wall clock (claim to credit), which includes
    input download and output upload. That is the honest number for a
    contribution ledger. It is cast to ``float`` because ``extract(epoch …)``
    is ``numeric`` and psycopg returns ``Decimal``, which would otherwise
    reach ``record_contributions`` and land in the column as a different type
    from every row the federated path writes.

    **Settlement rides along, in this transaction.** Taking the credit is the
    moment this API knows work was accepted, so it is the moment the buyer's
    escrow may pay the host — ``marketplace.settle_accepted_work``, keyed on
    the same lease, capped at what was held, with the remainder released back
    to the buyer. It is a no-op for the ordinary case of an attempt with no
    entitlement behind it, and it can never cost the credit: see
    ``_close_out_attempt_money``.

    ``accepted_seconds`` is the lease-held wall clock ROUNDED UP to a whole
    second. Up rather than down because the alternative charges nothing at all
    for a sub-second accepted task, which is free compute rather than cheap
    compute; the buyer's protection is the cap at the hold, not the rounding.
    """
    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                "update public.attempts"
                "   set accepted_at = now(),"
                "       resolved_at = now(),"
                "       outcome = 'accepted'"
                " where lease_id = %s and machine_id = %s and accepted_at is null"
                " returning job_id, task_id,"
                "           extract(epoch from (now() - claimed_at)) as duration_s",
                (lease_id, machine_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        credit = {
            "job_id": row["job_id"],
            "task_id": row["task_id"],
            "duration_s": float(row["duration_s"]),
        }
        _close_out_attempt_money(
            db,
            lease_id=lease_id,
            machine_id=machine_id,
            job_id=str(row["job_id"]),
            accepted_seconds=math.ceil(max(credit["duration_s"], 0.0)),
        )
    return credit


def record_attempt_failure(
    db: psycopg.Connection,
    *,
    lease_id: str,
    machine_id: str,
) -> bool:
    """Mark ``lease_id`` failed, once. Returns whether this call is the one
    that wrote it.

    The mirror of ``claim_attempt_credit`` on the other outcome, and the
    write that turns ``POST /attempts/{lease_id}/fail`` from a pure proxy
    into an event this API remembers. Until it existed, a failed attempt and
    an in-flight one were the same row, which is what made
    ``tasks_accepted / tasks_attempted`` an unbounded ratio rather than a
    reliability measure.

    **Idempotent, and the asymmetry with the accepted path is the point.**
    A retried fail — the agent's own retry, a duplicated request — describes
    one failure, so the second call matches no row and returns False rather
    than moving ``resolved_at`` forward and stretching the attempt's recorded
    duration. An INFERRED ``expired`` may be corrected to ``failed`` (the
    coordinator accepted the report, so the lease was alive after all, and an
    observation beats an inference); an OBSERVED outcome — ``accepted``, or a
    ``failed`` already written — is never overwritten by anything.

    Scoped to the claiming machine for the same reason every other write on
    this table is: a machine may only ever resolve its own attempt, and one
    machine reporting another's lease failed would requeue-in-the-ledger work
    that is still running.

    The caller must only reach this after the coordinator has ACCEPTED the
    failure report. A fail the coordinator refused (unknown lease, dead
    lease, wrong holder) describes nothing that happened, and recording it
    would resolve an attempt on the strength of a request that was rejected.

    **The refund rides along, in this transaction.** A failed attempt produced
    nothing accepted, so every credit held against its lease goes back to the
    buyer and the host earns zero — hard rule 4, and the reason this market
    can decline to bill for time at all. It fires exactly once because the
    outcome write does: the second call matches no row, returns False, and
    never reaches the ledger. Even if it did, ``credit_entries``' unique index
    would refuse the second movement.
    """
    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                "update public.attempts"
                "   set resolved_at = now(),"
                "       outcome = 'failed'"
                " where lease_id = %s and machine_id = %s"
                "   and (resolved_at is null or outcome = 'expired')"
                " returning lease_id, job_id",
                (lease_id, machine_id),
            )
            row = cur.fetchone()
        if row is None:
            return False
        _close_out_attempt_money(
            db,
            lease_id=lease_id,
            machine_id=machine_id,
            job_id=str(row["job_id"]),
        )
    return True


#: How long past its last known deadline an attempt must be before the
#: reconciler is willing to call it expired.
#:
#: The deadline alone would be enough if this API saw every heartbeat land:
#: past it the coordinator refuses the renewal and rejects the commit, so the
#: attempt is dead by the coordinator's own rule. What this covers is the gap
#: between the coordinator accepting a renewal and THIS API recording it —
#: ``note_attempt_deadline`` is best-effort, like every other accounting
#: write on the agent path, so a database blip can leave a live lease
#: carrying a deadline that has passed.
#:
#: Fifteen minutes because the agent renews at a third of the lease window
#: (``flashnode``'s ``_AttemptHeartbeat``), so for the default sixty-second
#: lease this is dozens of consecutive lost writes, not one. The cost of
#: being wrong is bounded anyway and in the safe direction: a wrongly expired
#: attempt that then commits is corrected by ``claim_attempt_credit``, which
#: does not consult ``outcome`` at all.
EXPIRY_GRACE_SECONDS = 900


def reconcile_expired_attempts(
    db: psycopg.Connection, *, grace_seconds: int = EXPIRY_GRACE_SECONDS
) -> int:
    """Resolve attempts whose lease deadline passed with nothing reported.
    Returns how many rows this call resolved.

    **The coordinator never tells us.** Lease expiry happens inside its
    two-second sweeper, which emits an event into its own ledger and makes no
    call anywhere. So a machine that is unplugged mid-task produces no fail
    hop, no completion, and — before this — an attempt row that stayed open
    for ever. That is precisely the event this product exists to survive, and
    it was the one event the reliability page could not see.

    **What makes this a measurement and not a guess.** ``lease_deadline`` is
    the coordinator's own instant, copied from the ``Lease`` it issued and
    refreshed from every renewal it granted. After it, that coordinator
    refuses the heartbeat with 410 and rejects the commit — the attempt is
    over whether or not anybody says so. ``resolved_at`` is therefore set to
    the DEADLINE, not to ``now()``: the deadline is when the work stopped
    counting, while ``now()`` is when a console happened to be opened, and
    stamping the second would make ``lost_task_seconds`` grow every time
    somebody loaded the page — the exact failure ``metrics.py`` refused to
    ship a number for in the first place.

    **Only rows that carry a deadline.** An attempt claimed before migration
    0015, or one whose claim response could not be parsed, has none. It stays
    unresolved for ever and stays out of every denominator, which is the
    honest handling: "we do not know how this ended" is not "it failed".

    ``expired``, never ``abandoned``. The two are different facts — a lease
    that ran out versus a node that vanished and later came back — and only
    the coordinator can tell them apart. Recording the one we can prove and
    leaving the other unwritten is the whole discipline of this table.

    Unscoped by owner, deliberately. Expiry is a fact about a lease, not
    about whose page is open, and an owner-scoped reconciler would make an
    account's own numbers depend on somebody else having visited the console.
    The partial index from 0015 is on this exact predicate, so after the
    first pass this is an indexed statement that matches nothing.

    **Each row it resolves is refunded, in this transaction.** An expired
    attempt is a host that was entitled to pull, pulled, and produced nothing
    — the exact event this product exists to survive, and the one the buyer
    must not be billed for. The refund is per lease and idempotent, so the
    sweep can run on every page load (it does) without the second pass moving
    anything: it resolves no rows, and reaches the ledger for none.
    """
    with db.transaction():
        with db.cursor() as cur:
            cur.execute(
                "update public.attempts"
                "   set resolved_at = lease_deadline,"
                "       outcome = 'expired'"
                " where resolved_at is null"
                "   and lease_deadline is not null"
                "   and lease_deadline < now() - make_interval(secs => %s)"
                " returning lease_id, machine_id, job_id",
                (float(grace_seconds),),
            )
            expired = cur.fetchall()
        for row in expired:
            _close_out_attempt_money(
                db,
                lease_id=str(row["lease_id"]),
                machine_id=str(row["machine_id"]),
                job_id=str(row["job_id"]),
            )
    return len(expired)


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


def count_online_machines(
    db: psycopg.Connection, *, pool_id: str | None
) -> int:
    """Machines that could claim work for a job in ``pool_id`` right now.

    ``pool_id=None`` means the public queue, where any online machine may
    claim, so the count is fleet-wide. A pool id narrows it to machines opted
    in to that pool — the same ``machine_pools`` intersection
    ``list_pool_members`` applies, because owning a machine is not opting it
    in.

    This is the number a federated round's slot count and chunk allotment are
    both derived from (``elastic.fleet_shape``), which is why it goes through
    ``MACHINE_ONLINE_PREDICATE`` rather than its own freshness threshold: the
    console shows a person "N machines online" from that same predicate, and a
    round cut from a different N would be unexplainable to them.

    A count, never a list: the caller needs capacity, and returning rows would
    invite a second place that decides which machines are online.
    """
    with db.cursor() as cur:
        if pool_id is None:
            cur.execute(
                f"select count(*) as n from public.machines m "
                f"where {MACHINE_ONLINE_PREDICATE}"
            )
        else:
            cur.execute(
                f"""
                select count(distinct m.id) as n
                  from public.machines m
                  join public.machine_pools mp on mp.machine_id = m.id
                 where mp.pool_id = %s and {MACHINE_ONLINE_PREDICATE}
                """,
                (pool_id,),
            )
        return int(cur.fetchone()["n"])


def dataset_capacity_in_pool(
    db: psycopg.Connection, *, pool_id: str | None
) -> int:
    """The largest dataset cache any ONLINE machine in the pool advertises.

    **The MAX, not the sum.** A dataset slice is fetched whole, by one
    machine, into that machine's own cache — so the question a submit has to
    answer is whether *any single host* can hold the biggest slice, never
    whether the fleet could hold it between them. Summing would admit a job
    that then sits PENDING forever while every machine in the Crew refuses
    it one at a time, which is precisely the outcome this check exists to
    replace with a sentence in the console.

    Scoping matches ``count_online_machines`` exactly, and for the same
    reason: ``pool_id=None`` is the public queue where any online machine may
    claim, and a pool id narrows to machines actually opted in through
    ``machine_pools``. Both go through ``MACHINE_ONLINE_PREDICATE`` so the
    capacity a submit is judged against belongs to the same machines the
    console is telling the user are online.

    Zero when nobody advertises anything — which refuses every dataset job,
    deliberately. That is the polarity the runtime's placement gate already
    takes (``scheduler``: absent or ``0`` means "send me no dataset work"),
    and disagreeing with it here would only move the refusal from the console
    to a queue nothing ever drains.

    Read defensively: the value is a jsonb field, so a machine that somehow
    stored a string or a boolean there contributes nothing rather than
    raising mid-submit. ``jsonb_typeof`` gates the cast, and the cast goes
    through ``numeric`` so a JSON float cannot abort the statement either.
    """
    # The aggregate carries its own type guard rather than leaning on the
    # WHERE clause: a planner is free to evaluate a select-list expression
    # on rows a qual has not filtered yet, and "the whole submit 500s
    # because one machine wrote a string" is not a failure worth risking to
    # save a CASE.
    capacity = (
        "coalesce(max(case when jsonb_typeof(m.capabilities -> "
        "'dataset_cache_bytes') = 'number' then floor("
        "(m.capabilities ->> 'dataset_cache_bytes')::numeric) end), 0) as n"
    )
    with db.cursor() as cur:
        if pool_id is None:
            cur.execute(
                f"select {capacity} from public.machines m "
                f"where {MACHINE_ONLINE_PREDICATE}"
            )
        else:
            cur.execute(
                f"""
                select {capacity}
                  from public.machines m
                  join public.machine_pools mp on mp.machine_id = m.id
                 where mp.pool_id = %s and {MACHINE_ONLINE_PREDICATE}
                """,
                (pool_id,),
            )
        return max(0, int(cur.fetchone()["n"]))


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


# --- raw bindings, for the isolation assertion -----------------------------
#
# The two readers below deliberately do NOT intersect ``machine_pools`` with
# ``pool_members``, which is what separates them from ``pool_ids_for_machine``
# and ``list_pool_machines`` above. Those answer "what does this machine serve
# *right now*", and dropping a binding whose owner has left the pool is the
# correct answer to that question.
#
# An isolation check asks a different and strictly harsher question: "could
# anything else ever serve this pool, or could this machine ever serve
# anything else". A binding left by a non-member is dormant, not absent — the
# day that person is invited back it becomes live, with no write to
# ``machine_pools`` to notice. Filtering it out here would let a caller pass
# an isolation assertion that a later ``insert into pool_members`` silently
# revokes. See ``sandbox_identity.assert_pool_isolated``.


def machine_ids_bound_to_pool(db: psycopg.Connection, pool_id: str) -> list[str]:
    """Every machine with a ``machine_pools`` row for this pool, membership
    ignored — see the note above."""
    with db.cursor() as cur:
        cur.execute(
            "select machine_id from public.machine_pools where pool_id = %s",
            (pool_id,),
        )
        return sorted(str(row["machine_id"]) for row in cur.fetchall())


def pool_ids_bound_to_machine(db: psycopg.Connection, machine_id: str) -> list[str]:
    """Every pool this machine has a ``machine_pools`` row for, membership
    ignored — see the note above."""
    with db.cursor() as cur:
        cur.execute(
            "select pool_id from public.machine_pools where machine_id = %s",
            (machine_id,),
        )
        return sorted(str(row["pool_id"]) for row in cur.fetchall())


def lock_pool_for_owner(
    db: psycopg.Connection, pool_id: str, owner_id: str
) -> dict[str, Any] | None:
    """Owner-scoped read that also takes a row lock on the pool until the
    surrounding transaction ends. **Must be called inside one** — in
    autocommit the lock is taken and dropped by the same statement, which
    looks like it worked and serialises nothing.

    Stricter than ``fetch_pool_for_member`` in one way and identical in the
    other. Stricter: the caller must be the pool's OWNER, not merely one of
    its members, because the writes this gates (minting a machine into a
    pool) change what the whole pool's other members are exposed to.
    Identical: a missing pool, a pool the caller cannot see, and a pool they
    are in but do not own all return None, indistinguishably — the same
    404-not-403 doctrine, for the same reason.

    ``for update of p`` names the table on purpose. A bare ``for update``
    across this join would lock the ``pool_members`` row too, which is not
    what is being serialised and is a needless way to block an unrelated
    invite from landing.
    """
    columns = ", ".join(f"p.{c}" for c in POOL_PUBLIC_COLUMNS)
    with db.cursor() as cur:
        cur.execute(
            f"""
            select {columns}
              from public.pools p
              join public.pool_members pm
                on pm.pool_id = p.id and pm.user_id = %s
             where p.id = %s and p.owner_id = %s
               for update of p
            """,
            (owner_id, pool_id, owner_id),
        )
        return cur.fetchone()


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


#: The columns ``list_pool_machines`` may return, and NOT
#: ``MACHINE_PUBLIC_COLUMNS``.
#:
#: The two constants answer different questions. ``MACHINE_PUBLIC_COLUMNS``
#: means "everything about this machine except the token hash, shown to the
#: machine's own OWNER" — that is why it can carry ``token_prefix``,
#: ``capabilities``, ``platform``, ``created_at`` and ``revoked_at``.
#: This route serves every MEMBER of a pool, so reusing that list would ship
#: a teammate's live token prefix and enrolment detail across an account
#: boundary — the first place in the API where one user's machine rows are
#: rendered to another user at all.
#:
#: What a member legitimately needs is the fleet view the console draws:
#: which machine, whose, what it can run, and whether it is alive. Anything
#: past that belongs on the owner's own "My machines" page.
_POOL_MACHINE_COLUMNS = (
    "id", "node_id", "name", "owner_id", "status", "last_seen_at",
    "sandbox_capable", "argv_capable", "unsandboxed_argv_capable",
    "module_capable",
)


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
    and the console marks it as such — ``PoolFleetTable`` renders a "Revoked"
    badge off ``status``, which is why ``status`` is in the column list below
    and why this sentence is true rather than aspirational. (Without it a
    revoked machine is indistinguishable from a sleeping one: both are
    "offline".) Unlike the opt-in checkbox list, which filters them because
    ticking one would be meaningless.
    """
    columns = ", ".join(f"m.{c}" for c in _POOL_MACHINE_COLUMNS)
    with db.cursor() as cur:
        cur.execute(
            f"""
            select {columns},
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
    (unknown token, expired, already exhausted, and an account whose
    request is already decided) without distinguishing which — the same
    reason ``claim_attempt_credit`` and ``claim_device_code_for_redemption``
    fold their refusal cases into one ``None``: telling a guesser *which*
    reason an invite failed for is a small oracle for free. Adding the
    last case opens nothing: a caller already knows their own access state.

    The decrement is one ``UPDATE ... WHERE ... RETURNING`` — the
    ``claim_attempt_credit`` idiom — so that two redemptions of the same
    one-use invite arriving together cannot both win the row; only one
    ``UPDATE`` can match ``uses_remaining > 0`` before the other sees the
    decremented value.

    A use is spent when the join is banked, and DECLINING THAT PERSON
    AFTERWARDS does not hand it back. That specific cost is deliberate:
    holding the use until approval would let a single link be claimed by an
    unlimited number of pending accounts. It does NOT extend to a request
    that was ALREADY decided before the click — there the refusal exists
    up front, the join could never be materialised
    (``approve_access_request`` requires ``status = 'pending'``), and the
    cost would fall on an uninvolved pool owner. So when there is nothing
    to bank on, this refuses and rolls the decrement back.

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
                if not record_pending_invite(
                    db, user_id, pool_id=pool_id, invited_by=created_by
                ):
                    # Nothing to bank the join on — the account's request is
                    # already decided, which in practice means DECLINED (an
                    # admitted one took the branch above). Undo everything,
                    # decrement included, so a terminal account cannot spend
                    # a use of somebody else's link on a join that can never
                    # be materialised, and cannot be told "you'll join as
                    # soon as you're approved" when no approval can come.
                    # psycopg.Rollback unwinds the block and is swallowed by
                    # it; execution resumes after the `with`.
                    raise psycopg.Rollback

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
    # Reached only via the psycopg.Rollback above: the use was refunded, and
    # the caller gets the same opaque refusal an invalid token would give.
    return None


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


def contributions_for_owner(
    db: psycopg.Connection, owner_id: str
) -> dict[str, Any]:
    """Everything this account's machines have ever been credited for.

    Returns ``{"machines": [...], "jobs_contributed_to": int}`` — the facts.
    What they MEAN, and the ordering, summing and null-handling the console
    is served, is ``flashml_cloud_api.contributions``, kept apart for the same
    reason the storage budget and the goodput rule are.

    **``contributions``, never ``attempts``.** This is the credit ledger for
    ACCEPTED work — hard rule 4 — and it is also the only one of the two that
    both credit paths write. ``fedavg.on_round`` credits a federated round
    straight into this table and records no attempt at all, so a count over
    ``attempts`` would drop that work entirely (production read 26 credits
    against 16 attempts on one machine on 2026-08-03, and the ten-row gap was
    real federated contributions) while also counting leases that were
    claimed and never accepted. The unique index from migration 0003 is what
    makes ``count(*)`` here safe where the two paths overlap: both compute the
    same ``(machine_id, job_id, task_id)`` for a federated round's task and
    the second insert is absorbed, so no row is ever counted twice.

    **Scoped on ``machines.owner_id``, not on ``jobs.owner_id``.** The whole
    barter premise is that a person's machines mostly run OTHER people's jobs;
    scoping on the job — the way ``metrics_counts_for_owner`` correctly does
    for a page about one's own submissions — would report zero for every
    volunteer in the product.

    **A revoked machine still counts, and that is a decision.** Revoking
    invalidates a token, which stops a machine claiming new leases; it says
    nothing about work already done and already accepted. Excluding it would
    make somebody's total FALL when they retire a laptop or rotate a token
    they suspect was leaked — for a counter that nothing ever debits that is
    indistinguishable from a bug, and it would penalise exactly the hygiene we
    want. (There is no delete-machine route, only revoke. If one is ever
    added, note that ``contributions.machine_id`` cascades: deleting a machine
    erases its history from this total, which is a second reason revoke must
    stay the only exit.) ``machines.owner_id`` is likewise never reassigned
    anywhere in this codebase — the day a transfer route exists, this query
    would silently hand the previous owner's history to the new one, and the
    join would have to start reading a recorded owner at credit time instead.

    Two statements rather than one. The job count needs the ``job_rounds``
    hop, and a ``LEFT JOIN`` for it in the per-machine query could fan a
    contribution row out and inflate ``accepted_tasks`` — ``coordinator_job_id``
    carries no uniqueness constraint, so nothing but convention says one
    coordinator job maps to one round. Keeping the count of credits away from
    that join makes the inflation unrepresentable rather than merely unlikely,
    and it also means an account with no contributions still gets an answer to
    the second question instead of losing it with the empty row set.
    """
    with db.cursor() as cur:
        # Deliberately unordered: "most-contributed first" is a presentation
        # rule and lives in `contributions.report`, the one place it can be
        # tested without a database. A second ORDER BY here would be a copy of
        # that rule, free to drift from it.
        cur.execute(
            """
            select m.id as machine_id,
                   m.name as hostname,
                   m.last_seen_at,
                   count(*) as accepted_tasks
              from public.contributions c
              join public.machines m on m.id = c.machine_id
             where m.owner_id = %s
             group by m.id, m.name, m.last_seen_at
            """,
            (owner_id,),
        )
        machines = list(cur.fetchall())

        # `contributions.job_id` is a COORDINATOR job id. For a Mode A run
        # that is also the id in `public.jobs`; for a federated run it is the
        # ROUND's job, and a five-round run would otherwise tell a volunteer
        # they helped with five jobs when they helped with one. `job_rounds`
        # is the mapping back — the same join `metrics_counts_for_owner` calls
        # not optional, for the same reason. `coalesce` keeps a credit whose
        # job this API never recorded (a job submitted straight to the
        # coordinator) counting as the one job it was.
        cur.execute(
            """
            select count(distinct coalesce(r.job_id, c.job_id)) as jobs
              from public.contributions c
              join public.machines m on m.id = c.machine_id
              left join public.job_rounds r on r.coordinator_job_id = c.job_id
             where m.owner_id = %s
            """,
            (owner_id,),
        )
        row = cur.fetchone()

    return {
        "machines": machines,
        "jobs_contributed_to": int(row["jobs"]) if row else 0,
    }


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


def peer_task_observations(
    db: psycopg.Connection,
    *,
    job_id: str,
    exclude_machine_id: str | None = None,
    limit: int = PEER_SAMPLE_LIMIT,
) -> list[dict[str, Any]]:
    """The same durations as :func:`peer_task_durations`, each LABELLED with
    the capability class of the machine that produced it.

    **A separate function, not a wider return type**, because the two answer
    different questions and the older one is on the credit hot path.
    ``peer_task_durations`` feeds ``verify.timing_verdict``, which compares
    one machine against a pool of peers and has no use for a class; this feeds
    ``router.estimator``, which may not pool two classes at all. Bare floats
    cannot satisfy that rule — a list of numbers with no class on them is
    exactly what a cross-class average looks like — so the labelled shape is
    what the router gets, and the verifier's signature is left alone.

    Each row carries ``machine_id``, ``capability_class``, ``duration_s`` and
    ``federated``, which is the shape ``estimator.Observation`` takes.

    ``capability_class`` is derived HERE, from ``machines.capabilities``, by
    ``router.estimator.hardware_class`` — the same function the planner and
    the acceptance-rate query use. One producer: a second ladder written in
    SQL beside it would agree on the day it was written and never again, and
    the disagreement would surface as a machine whose measured durations pool
    into one class while its acceptance rate is filed under another. ``None``
    is kept rather than dropped: an unclassifiable machine's durations are
    real, they simply match no class, and the estimator drops them for
    whichever class it is asked about.

    **Federated contributions cannot become timing evidence, twice over.**
    ``fedavg.on_round`` credits from the coordinator's task view, which
    reports no duration, so ``duration_s is not null`` already removes them;
    the ``federated`` flag identifies them positively as well, so a federated
    row that somehow acquired a duration is still refused by
    ``estimator._usable``. Two mechanisms for one rule because the first is a
    property of today's writer and the second is a property of the row.

    ``exclude_machine_id`` is optional here and mandatory next door, and the
    asymmetry is deliberate: excluding the subject is what stops a verifier
    grading a machine against its own history, while a PLAN is about the whole
    fleet and has no subject to exclude.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select c.machine_id, c.duration_s, m.capabilities,
                   exists (
                       select 1 from public.job_rounds r
                        where r.coordinator_job_id = c.job_id
                   ) as federated
              from public.contributions c
              join public.machines m on m.id = c.machine_id
             where c.job_id = %s
               and (%s::uuid is null or c.machine_id <> %s::uuid)
               and c.duration_s is not null
             order by c.accepted_at desc
             limit %s
            """,
            (job_id, exclude_machine_id, exclude_machine_id, limit),
        )
        rows = cur.fetchall()

    return [
        {
            "machine_id": str(row["machine_id"]),
            "capability_class": hardware_class(row["capabilities"]),
            "duration_s": float(row["duration_s"]),
            "federated": bool(row["federated"]),
        }
        for row in rows
    ]


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


# ---------------------------------------------------------------------------
# storage accounting (migration 0010)
#
# The policy — whether a given usage is over budget — lives in
# `flashml_cloud_api.storage` as pure arithmetic. These are the measurements
# it reasons about, kept separate so the rule stays testable without a
# database and so neither half can quietly start making the other's
# decisions.
# ---------------------------------------------------------------------------


def storage_usage_for_owner(db: psycopg.Connection, owner_id: str) -> int:
    """Total recorded artifact bytes across every job this account owns.

    Summed from `jobs.artifact_bytes` rather than asked of the coordinator:
    the live answer would cost one HTTP call per job to satisfy a question
    that is asked on every submit. Jobs predating migration 0010 contribute
    0 — honest, because nobody measured them.
    """
    row = db.execute(
        "select coalesce(sum(artifact_bytes), 0) as used "
        "from public.jobs where owner_id = %s",
        (owner_id,),
    ).fetchone()
    return int(row["used"]) if row else 0


def record_job_artifact_bytes(
    db: psycopg.Connection, job_id: str, total_bytes: int
) -> None:
    """Set — never add — a job's measured footprint, and mark it measured.

    A job's state is polled every two seconds while its page is open, and
    the recording hook runs on each poll. Accumulating would let one idle
    browser tab inflate an account's usage without bound until it could no
    longer submit anything. `total_bytes` is always the whole current
    footprint, so assignment is both correct and idempotent.

    `artifact_bytes_recorded_at` (migration 0011) is stamped in the SAME
    statement, never a second one: it is what the hook reads to decide
    whether it still owes the coordinator an artifact-listing call, and a
    bytes column written without its marker — or a marker written without
    its bytes — would either re-list a measured job on every poll for ever
    or record a measurement that never happened. This function is the only
    writer of either column, which is what makes that pairing enforceable.
    """
    db.execute(
        "update public.jobs "
        "   set artifact_bytes = %s, artifact_bytes_recorded_at = now() "
        " where id = %s",
        (int(total_bytes), job_id),
    )


def mark_job_artifacts_mirrored(db: psycopg.Connection, job_id: str) -> None:
    """Record that this job's accepted artifacts are in OSS (migration 0016).

    Its own statement and its own column, NOT a second field on
    `record_job_artifact_bytes`. The two facts are produced by different
    operations that fail differently — measuring is one coordinator listing,
    mirroring is N copies to a third party — and 0016's comment explains at
    length why a shared marker would both record mirrors that never happened
    (the delete route stamps the measurement) and suppress the retry of ones
    that failed.

    Called ONLY after `mirror_job` reports the objects are actually there.
    Never on `NOT_CONFIGURED`, and never in a `finally`: an unstamped job is
    retried on the next observation, which is the recoverable direction, and
    a stamped one never is.
    """
    db.execute(
        "update public.jobs set artifacts_mirrored_at = now() where id = %s",
        (job_id,),
    )


# ---------------------------------------------------------------------------
# reliability metrics (Stage 8)
#
# What this account's ledger actually says, over a window. The rule about
# what those numbers MEAN — and which fields cannot be derived at all — is
# `flashml_cloud_api.metrics`, kept apart for the same reason the storage
# budget is: a policy that lives inside a SQL string cannot be read or
# argued with.
# ---------------------------------------------------------------------------


#: Outcomes that mean "this attempt ended and produced nothing usable".
#: Spelled once, here, because three of the measurements below have to agree
#: on it: lost seconds, the recovery interval, and the resolved denominator
#: that is the complement of the accepted count. ``abandoned`` is in the
#: vocabulary and unwritten today (0015) — listing it now means the day
#: something writes it, it lands in the numbers instead of falling silently
#: out of all three.
_WASTED_OUTCOMES = "('failed', 'expired', 'abandoned')"


def metrics_counts_for_owner(
    db: psycopg.Connection, owner_id: str, window_days: int
) -> dict[str, Any]:
    """Job outcomes, attempt counts, wasted seconds and recovery intervals
    for one account.

    **ONE WINDOW, DEFINED ON THE JOB.** Everything counted here belongs to a
    job this account submitted within ``window_days``; the attempt counts are
    not separately windowed on when the attempt happened. That is deliberate
    and it is what makes the page coherent: ``goodput_ratio`` and
    ``jobs_total`` then describe the same set of jobs, so "4 jobs, 60%
    goodput" is one statement rather than two about different periods. The
    cost is that a long-running job submitted before the window contributes
    nothing even if it ran yesterday — which is right, because its jobs row
    is excluded too, and a task count with no job to belong to would be
    unexplainable on the page.

    **ATTEMPTS, NOT DISTINCT TASKS.** ``tasks_attempted`` counts rows in
    ``attempts``, one per lease claimed. A task retried three times after two
    machines died contributes three. Collapsing to distinct tasks would drive
    goodput toward 1.0 for every job that eventually finished and erase the
    wasted work the whole page exists to show.

    **RESOLVED IS THE DENOMINATOR, ATTEMPTED IS THE COUNT.** These are now
    two different numbers and the split is the point of migration 0015.
    ``tasks_attempted`` is every lease claimed — the honest answer to "how
    much work was handed out". ``tasks_resolved`` counts only the attempts
    that reached a terminal state, and it is what ``goodput_ratio`` divides
    by. Before 0015 there was one number doing both jobs, so an attempt still
    running and an attempt that had failed were the same row and the ratio
    fell with every claim and recovered for nothing. An UNRESOLVED attempt is
    in flight or predates 0015; in neither case is it evidence of anything,
    and counting it as a failure would be the survivorship bias inverted.

    **THE FEDERATED JOIN IS NOT OPTIONAL.** A federated run is one
    coordinator job PER ROUND, and both ``attempts.job_id`` and
    ``contributions.job_id`` carry the ROUND's coordinator job id — an id
    that is not a row in ``public.jobs`` at all (only the parent ``fed-…`` id
    is). Joining attempts to ``jobs.id`` alone would report zero tasks and
    zero machines for every federated run ever submitted, which is the mode
    this product most wants to show off. ``job_rounds`` is the mapping back.

    **ACCEPTED, NOT MERELY ATTEMPTED, DECIDES WHO CONTRIBUTED.** A machine
    that claimed leases and never committed anything appears in
    ``tasks_attempted`` and NOT in ``machines_contributing`` — hard rule 4,
    the same distinction the credit ledger draws.

    **LOST SECONDS COME FROM THE ATTEMPT, NEVER FROM ``contributions``.**
    ``lost_seconds_total`` sums ``resolved_at - claimed_at`` over attempts
    that ended without being accepted. It cannot be read off the credit
    ledger, which by construction holds accepted work only — that is the
    survivorship bias this change removes: every duration in
    ``contributions`` is a duration that succeeded, so a median taken there
    is biased fast by exactly the runs that timed out.

    **RECOVERY IS A PAIR OR IT IS NOTHING.** ``recovery_seconds_total`` and
    ``recoveries_observed`` describe intervals from a failure being resolved
    to the REPLACEMENT attempt on the same ``(job_id, task_id)`` being
    accepted — the replacement being the earliest accepted attempt CLAIMED at
    or after that resolution, so an attempt already running when the failure
    landed is not mistaken for a response to it. A failure that never
    recovered contributes nothing rather than an invented interval, which
    makes this a mean over recoveries and not over failures; ``metrics.py``
    says so where the number is rendered.

    Statuses are compared upper-cased. Every writer today stores the
    protocol's own spelling (``SUCCEEDED``/``PARTIAL``/``FAILED``), and this
    makes a lower-cased row from some future writer count rather than
    silently drop out of the outcome columns while still counting in
    ``jobs_total``.

    Durations are cast to ``float`` here for the reason
    ``claim_attempt_credit`` gives: ``extract(epoch …)`` is ``numeric`` and
    psycopg returns ``Decimal`` for it, which must not reach a JSON response
    as a type nothing else in this module produces.
    """
    with db.cursor() as cur:
        cur.execute(
            f"""
            with owned as (
                select id
                  from public.jobs
                 where owner_id = %s
                   and created_at >= now() - make_interval(days => %s)
            ),
            -- Every coordinator job whose work belongs to one of those jobs:
            -- the job itself for a Mode A run, and one per round for a
            -- federated one.
            coordinator_jobs as (
                select id as job_id from owned
                 union
                select r.coordinator_job_id
                  from public.job_rounds r
                  join owned o on o.id = r.job_id
                 where r.coordinator_job_id is not null
            ),
            -- This account's attempts, once, so the measurements below read
            -- the same rows rather than six repetitions of one join.
            mine as (
                select a.*
                  from public.attempts a
                  join coordinator_jobs c on c.job_id = a.job_id
            ),
            -- One row per resolved failure, carrying the gap to its
            -- replacement — null when the task never recovered.
            recoveries as (
                select (
                    select min(r.resolved_at)
                      from public.attempts r
                     where r.job_id = f.job_id
                       and r.task_id = f.task_id
                       and r.outcome = 'accepted'
                       and r.claimed_at >= f.resolved_at
                ) - f.resolved_at as gap
                  from mine f
                 where f.outcome in {_WASTED_OUTCOMES}
                   and f.resolved_at is not null
            )
            select
                (select count(*) from owned) as jobs_total,
                (select count(*) from public.jobs j
                  where j.owner_id = %s
                    and j.created_at >= now() - make_interval(days => %s)
                    and upper(j.status) = 'SUCCEEDED') as jobs_succeeded,
                (select count(*) from public.jobs j
                  where j.owner_id = %s
                    and j.created_at >= now() - make_interval(days => %s)
                    and upper(j.status) = 'PARTIAL') as jobs_partial,
                (select count(*) from public.jobs j
                  where j.owner_id = %s
                    and j.created_at >= now() - make_interval(days => %s)
                    and upper(j.status) = 'FAILED') as jobs_failed,
                (select count(*) from mine) as tasks_attempted,
                (select count(*) from mine where outcome is not null)
                    as tasks_resolved,
                (select count(*) from mine where outcome = 'accepted')
                    as tasks_accepted,
                (select count(distinct machine_id) from mine
                  where outcome = 'accepted') as machines_contributing,
                -- `greatest(..., 0)` per row, not on the sum. A negative
                -- interval is not a short piece of wasted work, it is a
                -- clock that disagreed with itself — an `expired` attempt is
                -- resolved against the COORDINATOR's deadline while
                -- `claimed_at` is this database's `now()`, so the two come
                -- from different clocks. Clamping per row keeps one skewed
                -- attempt from subtracting from the real wasted time of the
                -- attempts beside it, which is the failure mode of clamping
                -- the total instead.
                (select coalesce(sum(greatest(
                            extract(epoch from (resolved_at - claimed_at)), 0)), 0)
                   from mine
                  where outcome in {_WASTED_OUTCOMES}
                    and resolved_at is not null) as lost_seconds_total,
                (select count(*) from recoveries where gap is not null)
                    as recoveries_observed,
                (select coalesce(sum(extract(epoch from gap)), 0)
                   from recoveries where gap is not null)
                    as recovery_seconds_total
            """,
            (owner_id, window_days) + (owner_id, window_days) * 3,
        )
        row = cur.fetchone()

    seconds = ("lost_seconds_total", "recovery_seconds_total")
    return {
        key: float(value) if key in seconds else int(value)
        for key, value in dict(row or {}).items()
    }


def acceptance_rate_rows(
    db: psycopg.Connection, *, machine_ids: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    """Resolved attempts, each labelled with a capability class — the input
    ``metrics.acceptance_rates`` has always required and nothing supplied.

    ``acceptance_rates`` is keyed on ``(machine_id, capability_class)`` and
    refuses to roll up across classes, so it cannot be called at all without
    a class on every row. This is that producer, and it is the ONLY one: the
    class comes from ``router.estimator.hardware_class`` over
    ``machines.capabilities``, the same function
    :func:`peer_task_observations` and the planner call. A second ladder
    spelled out in SQL would drift from it, and the drift would read as a host
    that is 0.95 in one place and unproven in another.

    **The class labels the machine's HARDWARE, and that is a compromise this
    schema forces.** ``acceptance_rates`` documents the key as the class of
    the WORK — the property that lets one host be 0.95 on cpu work and 0.40 on
    gpu work — but nothing records the class of a task: ``attempts`` carries a
    lease, a machine, a job and a task id, and no column anywhere says what
    class of work that task was. So a machine appears under exactly one class
    here, its own, and the split ``acceptance_rates`` was built for is
    unreachable until something writes the work's class down. Deriving it from
    the hardware is the honest approximation — it is at least a fact about
    something — and it is named as one rather than presented as the other.
    (The schema change that would fix it: a ``capability_class`` column on
    ``attempts``, written at claim time from the bid the task was matched
    under. Not written here; this module does not own migrations.)

    **Unresolved attempts never appear.** ``outcome is null`` means in flight
    or pre-0015, and both are excluded in the query rather than left for the
    caller — an unresolved attempt in a denominator is a machine's rate
    falling because it is busy.

    **A duration only for an accepted attempt.** ``resolved_at - claimed_at``
    on an accepted row is the same lease-held wall clock
    ``claim_attempt_credit`` records; on a failed or expired one it is time
    that was WASTED, not time a task takes, and ``median_seconds`` is a
    statement about the second. So the column is null for those rows and they
    count toward the rate while contributing nothing to the median — which is
    the split ``acceptance_rates`` already documents for federated rows.

    Federated work cannot reach this at all: ``fedavg.on_round`` writes no
    ``attempts`` row, so there is nothing to exclude.

    Machines whose class cannot be derived are DROPPED, not filed under a
    placeholder. ``str(None)`` would key a group under the literal string
    ``"None"``, and a rate reported against a class that does not exist is
    worse than no rate: ``select_acceptance`` refuses to match a ``None``
    class for the same reason, and a host with no readable hardware is
    unproven, which is a state the fleet handles.

    ``machine_ids`` scopes the read; ``None`` means every machine. Callers
    on a request path pass the fleet they are about to plan, which keeps this
    an indexed read of a bounded set (``attempts_machine_id_idx``).
    """
    ids = None if machine_ids is None else [str(m) for m in machine_ids]
    if ids is not None and not ids:
        return []

    with db.cursor() as cur:
        cur.execute(
            """
            select a.machine_id, a.outcome, m.capabilities,
                   case when a.outcome = 'accepted'
                        then extract(epoch from (a.resolved_at - a.claimed_at))
                   end as duration_s
              from public.attempts a
              join public.machines m on m.id = a.machine_id
             where a.outcome is not null
               and (%s::uuid[] is null or a.machine_id = any(%s::uuid[]))
             order by a.machine_id, a.claimed_at
            """,
            (ids, ids),
        )
        rows = cur.fetchall()

    labelled: list[dict[str, Any]] = []
    for row in rows:
        capability_class = hardware_class(row["capabilities"])
        if capability_class is None:
            continue
        duration = row["duration_s"]
        labelled.append(
            {
                "machine_id": str(row["machine_id"]),
                "capability_class": capability_class,
                "outcome": str(row["outcome"]),
                "duration_s": None if duration is None else float(duration),
            }
        )
    return labelled


#: What one machine offers a plan, beyond what the placement gates will say
#: about it. Kept as a tuple so the column list is written once.
_CANDIDATE_COLUMNS = (
    "id", "node_id", "name", "owner_id", "capabilities",
    "sandbox_capable", "argv_capable", "unsandboxed_argv_capable",
    "module_capable",
)


def router_candidates_for_owner(
    db: psycopg.Connection, owner_id: str
) -> list[dict[str, Any]]:
    """Every machine this account could plan work onto, with its venue and
    its price. **Read-only, and it matches nothing** — no listing is consumed,
    no bid is written, no credit moves.

    Two venues, and the difference between them is decision M1:

    - ``workspace`` — the account's own machines, plus every machine bound to
      a pool it is a live member of. **Free** (``ask_zc_per_hour`` 0), because
      members consume each other's machines at no charge. A machine that is
      both a teammate's and listed on the open market appears here and not
      there: workspace demand has priority (M12), and quoting a member a price
      for capacity they already have would be wrong in both directions.
    - ``market`` — machines with an OPEN listing, at the host's own ask. A
      zero ask is legal and means donated (M13), which is why the price and
      the venue are separate fields: "free because it is yours" and "free
      because somebody donated it" are different facts.

    ``rented`` has no producer. Nothing in this deployment sells USD-priced
    capacity, and a venue with no supply behind it would be an empty column
    the console draws as though it were a choice.

    ``pool_ids`` is the machine's bindings intersected with its owner's live
    memberships — the same rule ``pool_ids_for_machine`` applies, because the
    register proxy stamps that exact value onto the node view the coordinator
    gates against. The caller stamps it back onto the view it builds here, so
    the preview's seventh gate sees what the real one will.

    ``status = 'active'`` only: a pending or revoked machine cannot claim
    anything, and planning work onto one would quote a fleet that does not
    exist.
    """
    columns = ", ".join(f"m.{c}" for c in _CANDIDATE_COLUMNS)
    with db.cursor() as cur:
        cur.execute(
            f"""
            with mine as (
                select m.id
                  from public.machines m
                 where m.owner_id = %s::uuid
                 union
                select mp.machine_id
                  from public.machine_pools mp
                  join public.pool_members me
                    on me.pool_id = mp.pool_id and me.user_id = %s::uuid
                  join public.machines om on om.id = mp.machine_id
                  join public.pool_members owner_member
                    on owner_member.pool_id = mp.pool_id
                   and owner_member.user_id = om.owner_id
            ),
            listed as (
                select l.machine_id, l.id as listing_id, l.ask_zc_per_hour,
                       l.max_concurrent_tasks, l.capability_class
                  from public.listings l
                 where l.state = 'open'
            )
            select {columns},
                   (m.id in (select id from mine)) as workspace,
                   listed.listing_id, listed.ask_zc_per_hour,
                   listed.max_concurrent_tasks, listed.capability_class
                       as listed_capability_class,
                   (select coalesce(
                               array_agg(mp.pool_id::text order by mp.pool_id),
                               array[]::text[])
                      from public.machine_pools mp
                      join public.pool_members pm
                        on pm.pool_id = mp.pool_id and pm.user_id = m.owner_id
                     where mp.machine_id = m.id) as pool_ids
              from public.machines m
              left join listed on listed.machine_id = m.id
             where m.status = 'active'
               and (m.id in (select id from mine)
                    or listed.machine_id is not null)
             order by m.created_at, m.id
            """,
            (owner_id, owner_id),
        )
        rows = cur.fetchall()

    candidates: list[dict[str, Any]] = []
    for row in rows:
        workspace = bool(row["workspace"])
        candidates.append(
            {
                "machine_id": str(row["id"]),
                "node_id": row["node_id"],
                "name": row["name"],
                "owner_id": str(row["owner_id"]),
                "capabilities": row["capabilities"] or {},
                "pool_ids": list(row["pool_ids"] or []),
                "sandbox_capable": bool(row["sandbox_capable"]),
                "argv_capable": bool(row["argv_capable"]),
                "unsandboxed_argv_capable": bool(row["unsandboxed_argv_capable"]),
                "module_capable": bool(row["module_capable"]),
                "venue": "workspace" if workspace else "market",
                "listing_id": (
                    None if workspace or row["listing_id"] is None
                    else str(row["listing_id"])
                ),
                # Zero for workspace capacity because it IS zero (M1), not
                # because the price is unknown: `venue` is what says which.
                "ask_zc_per_hour": (
                    0 if workspace else int(row["ask_zc_per_hour"] or 0)
                ),
                "max_concurrent_tasks": (
                    1 if workspace else max(int(row["max_concurrent_tasks"] or 1), 1)
                ),
                "listed_capability_class": (
                    None if workspace else row["listed_capability_class"]
                ),
            }
        )
    return candidates


def sync_observed_job_states(
    db: psycopg.Connection, observed: Sequence[tuple[str, str]]
) -> None:
    """Write down terminal job states this API has just seen the coordinator
    report, for the jobs that do not already say so.

    WHY THIS EXISTS AT ALL. ``jobs.status`` is written once, at submission,
    from whatever the coordinator answered then — ``PENDING`` or
    ``RUNNING``. For a Mode A job nothing ever updated it again: the console
    reads live state by forwarding to the coordinator, so the stale column
    was never visible and never mattered. It matters now, because
    ``GET /me/metrics`` counts outcomes out of this table and cannot make N
    HTTP calls to do it — and a metrics page reporting "0 succeeded" for an
    account whose jobs all succeeded is worse than no page. The federated
    driver has always written its own terminal status here (``set_job_status``);
    this is the same fact arriving for the other mode, from the only place
    that observes it.

    ``status is distinct from`` is the guard, and it is the statement's own
    rather than a read the caller has to perform first: after the first
    observation every later poll matches no row, so an open jobs list costs
    one no-op UPDATE rather than one write per job per two seconds.

    One statement for the whole page, not one per job: the list route sees
    every visible job at once, and a per-row loop would turn a single
    coordinator round trip into N database round trips at exactly the
    polling frequency that makes N expensive.

    ``coalesce(finished_at, now())`` rather than ``now()``: a job whose
    finish time was already recorded (the federated path stamps it) must
    keep the time it actually finished, not the time somebody happened to
    open a page.
    """
    rows = [(job_id, state) for job_id, state in observed if job_id and state]
    if not rows:
        return
    values = ", ".join(["(%s, %s)"] * len(rows))
    with db.cursor() as cur:
        cur.execute(
            f"""
            update public.jobs j
               set status = v.state,
                   finished_at = coalesce(j.finished_at, now())
              from (values {values}) as v(id, state)
             where j.id = v.id
               and j.status is distinct from v.state
            """,
            [field for row in rows for field in row],
        )


def storage_limit_override_for(
    db: psycopg.Connection, owner_id: str
) -> int | None:
    """This account's own limit, or None to mean "use the deployment default".

    None and 0 are different answers and must stay that way: 0 is an admin
    freezing an account, None is an account that simply never had an
    override. Collapsing them freezes everybody.
    """
    row = db.execute(
        "select storage_limit_bytes from public.profiles where id = %s",
        (owner_id,),
    ).fetchone()
    if row is None:
        return None
    value = row["storage_limit_bytes"]
    return None if value is None else int(value)


# ---------------------------------------------------------------------------
# GitHub App installations
#
# Every read here is scoped by user_id, without exception. An installation is
# a licence to read someone's private source; a query that forgets the owner
# would hand it to whoever asked.
# ---------------------------------------------------------------------------


def insert_github_installation(
    db: psycopg.Connection,
    *,
    installation_id: int,
    user_id: str,
    account_login: str,
    account_type: str,
    repository_selection: str,
) -> None:
    """Record a connection, idempotently.

    Idempotent because clicking Connect twice — or GitHub replaying its
    redirect — is ordinary behaviour, not an error worth a 500. The upsert
    also refreshes `repository_selection`, which changes whenever someone
    edits the App's repository access on GitHub without reinstalling.
    """
    db.execute(
        """
        insert into public.github_installations
            (installation_id, user_id, account_login, account_type,
             repository_selection)
        values (%s, %s, %s, %s, %s)
        on conflict (installation_id, user_id) do update
            set account_login = excluded.account_login,
                account_type = excluded.account_type,
                repository_selection = excluded.repository_selection
        """,
        (installation_id, user_id, account_login, account_type,
         repository_selection),
    )


def fetch_github_installation_for_owner(
    db: psycopg.Connection, user_id: str, account_login: str
) -> dict | None:
    """This user's installation covering `account_login`, or None.

    `lower()` on both sides, matching the index: GitHub logins preserve case
    but compare without it, and a repo URL may be typed either way. A
    case-sensitive lookup would miss, fall back to an anonymous fetch, and
    404 — reported to the submitter as "repo not found", which sends them to
    debug the wrong thing entirely.
    """
    return db.execute(
        """
        select installation_id, account_login, account_type,
               repository_selection
          from public.github_installations
         where user_id = %s
           and lower(account_login) = lower(%s)
        """,
        (user_id, account_login),
    ).fetchone()


def list_github_installations(db: psycopg.Connection, user_id: str) -> list[dict]:
    return list(
        db.execute(
            """
            select installation_id, account_login, account_type,
                   repository_selection, created_at
              from public.github_installations
             where user_id = %s
             order by account_login
            """,
            (user_id,),
        ).fetchall()
    )


def delete_github_installation(
    db: psycopg.Connection, user_id: str, installation_id: int
) -> bool:
    """True if a row was removed.

    False lets the route answer 404 for an id this caller never connected,
    rather than a cheerful 204 that implies something was undone.

    Deliberately does NOT uninstall the App on GitHub. That is the account
    admin's decision, it would affect every colleague sharing the
    installation, and a job console is the wrong place to make it.
    """
    result = db.execute(
        """
        delete from public.github_installations
         where user_id = %s and installation_id = %s
        """,
        (user_id, installation_id),
    )
    return result.rowcount > 0


def insert_github_install_state(
    db: psycopg.Connection, state: str, user_id: str, expires_at: datetime
) -> None:
    db.execute(
        """
        insert into public.github_install_states (state, user_id, expires_at)
        values (%s, %s, %s)
        """,
        (state, user_id, expires_at),
    )


def claim_github_install_state(
    db: psycopg.Connection, state: str, user_id: str
) -> bool:
    """Consume a state, returning whether it was this user's to consume.

    One statement, so the check and the consumption cannot interleave: two
    simultaneous callbacks with the same state produce exactly one True.

    The `user_id` predicate is the security property (spec §3). An attacker
    who mints a state as themselves and phishes a victim into installing with
    it gets a callback posted by the VICTIM — different user, no row matched,
    nothing bound. Note it also leaves the row intact for its rightful owner:
    a failed attempt must not consume someone else's pending flow, or the
    attack degrades from theft to denial of service.
    """
    result = db.execute(
        """
        delete from public.github_install_states
         where state = %s and user_id = %s and expires_at > now()
        """,
        (state, user_id),
    )
    return result.rowcount > 0
