"""``db.py``'s pool functions, against a real, freshly migrated Postgres.

Team pools (migration 0007): membership, invites, admission, viewer scope,
and the credit view a pool's contribution page renders. Every property
worth pinning here is a property of the database — an exactly-once
decrement under two redemptions, a member-scope join that returns None for
a non-member and an unknown id alike — so these run against
``postgres_dsn``, the same real ephemeral database ``test_contributions.py``
uses, and never a mock.

No skips in this file, same rule ``test_jobs_from_repo.py`` states: a test
that asserts a redemption did *not* admit is worthless if it silently
doesn't run.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from flashml_cloud_api import db as dbmod

from test_jobs_from_repo import (
    _new_user,
    db,  # noqa: F401 - fixture
)

RUN_MARKER = uuid.uuid4().hex[:8]


def _node_id(tag: str) -> str:
    """A node id no other test can collide with on ``machines.node_id``."""
    return f"node-{RUN_MARKER}-{tag}-{uuid.uuid4().hex[:6]}"


def _job_id() -> str:
    return f"job-{RUN_MARKER}-{uuid.uuid4().hex[:10]}"


def _enrol(db, owner_id: str, node_id: str, *, last_seen_at: datetime | None = None,
           status: str = "active") -> str:
    """A machine belonging to ``owner_id``, for the online/offline counts.

    Goes through ``insert_machine`` for the row itself — the same helper
    ``test_contributions.py``'s own ``_enrol`` uses — and then stamps
    ``status``/``last_seen_at`` directly, since enrolment's token dance is
    irrelevant to a pool count and this way a test can build a fresh row or
    a stale one on demand.
    """
    machine_id = dbmod.insert_machine(
        db, owner_id=owner_id, node_id=node_id, name=f"machine-{node_id}",
        platform="linux",
    )
    with db.cursor() as cur:
        cur.execute(
            "update public.machines set status = %s, last_seen_at = %s where id = %s",
            (status, last_seen_at, machine_id),
        )
    return machine_id


def _pool(db, owner_id: str, name: str = "Team") -> str:
    return dbmod.create_pool(db, name=name, owner_id=owner_id)["id"]


def _add_member(db, pool_id: str, user_id: str) -> None:
    """Join ``user_id`` to ``pool_id`` directly, bypassing invites — for
    tests whose point is not the invite flow itself."""
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pool_members (pool_id, user_id) values (%s, %s)",
            (pool_id, user_id),
        )


def _invite(db, pool_id: str, created_by: str, *, uses: int = 1,
            expires_at: datetime | None = None) -> str:
    token_hash = uuid.uuid4().hex
    dbmod.create_pool_invite(
        db,
        pool_id=pool_id,
        created_by=created_by,
        token_hash=token_hash,
        expires_at=expires_at or (datetime.now(timezone.utc) + timedelta(hours=1)),
        uses=uses,
    )
    return token_hash


def _seed_job(db, owner_id: str, pool_id: str | None = None) -> str:
    """A jobs row with a pool_id, direct SQL — ``insert_job`` (Task 8's
    surface, unchanged by this task) does not yet take one."""
    job_id = _job_id()
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.jobs (id, owner_id, name, status, pool_id)
            values (%s, %s, %s, 'RUNNING', %s)
            """,
            (job_id, owner_id, "test-job", pool_id),
        )
    return job_id


# ---------------------------------------------------------------------------
# create_pool
# ---------------------------------------------------------------------------


def test_create_pool_seats_the_owner_as_a_member(db):
    owner = _new_user(db)
    pool = dbmod.create_pool(db, name="Ada's Team", owner_id=owner)

    assert pool["name"] == "Ada's Team"
    assert str(pool["owner_id"]) == owner
    assert dbmod.is_pool_member(db, pool["id"], owner) is True


# ---------------------------------------------------------------------------
# list_pools_for_user
# ---------------------------------------------------------------------------


def test_list_pools_counts_members_and_online_machines(db):
    owner = _new_user(db)
    member = _new_user(db)
    pool_id = _pool(db, owner)
    _add_member(db, pool_id, member)

    # A fresh heartbeat from the owner's machine, a stale one from the
    # member's — only the fresh one should count as online.
    _enrol(db, owner, _node_id("fresh"), last_seen_at=datetime.now(timezone.utc))
    _enrol(
        db, member, _node_id("stale"),
        last_seen_at=datetime.now(timezone.utc) - timedelta(seconds=200),
    )

    rows = dbmod.list_pools_for_user(db, owner)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == pool_id
    assert row["member_count"] == 2
    assert row["machines_online"] == 1

    # The member sees the same pool, with the same counts.
    member_rows = dbmod.list_pools_for_user(db, member)
    assert len(member_rows) == 1
    assert member_rows[0]["machines_online"] == 1


def test_list_pools_for_user_only_returns_own_pools(db):
    owner = _new_user(db)
    stranger = _new_user(db)
    _pool(db, owner)

    assert dbmod.list_pools_for_user(db, stranger) == []


def test_a_revoked_machine_never_counts_as_online(db):
    """``machines_online`` requires ``status = 'active'`` — a fresh
    heartbeat timestamp left over from before revocation must not count."""
    owner = _new_user(db)
    pool_id = _pool(db, owner)
    _enrol(
        db, owner, _node_id("revoked"),
        last_seen_at=datetime.now(timezone.utc), status="revoked",
    )

    assert dbmod.list_pools_for_user(db, owner)[0]["machines_online"] == 0


# ---------------------------------------------------------------------------
# fetch_pool_for_member
# ---------------------------------------------------------------------------


def test_fetch_pool_for_member_none_for_non_member_and_unknown_id(db):
    owner = _new_user(db)
    outsider = _new_user(db)
    pool_id = _pool(db, owner)

    assert dbmod.fetch_pool_for_member(db, pool_id, owner) is not None
    # A real pool, but this caller isn't in it...
    assert dbmod.fetch_pool_for_member(db, pool_id, outsider) is None
    # ...indistinguishable from a pool id that doesn't exist at all.
    assert dbmod.fetch_pool_for_member(db, str(uuid.uuid4()), owner) is None


# ---------------------------------------------------------------------------
# list_pool_members
# ---------------------------------------------------------------------------


def test_list_pool_members_reports_names_and_machine_counts(db):
    owner = _new_user(db)
    dbmod.upsert_profile(db, owner, display_name="Ada")
    pool_id = _pool(db, owner)
    _enrol(db, owner, _node_id("m1"), last_seen_at=datetime.now(timezone.utc))
    _enrol(
        db, owner, _node_id("m2"),
        last_seen_at=datetime.now(timezone.utc) - timedelta(seconds=200),
    )

    members = dbmod.list_pool_members(db, pool_id)
    assert len(members) == 1
    member = members[0]
    assert str(member["user_id"]) == owner
    assert member["display_name"] == "Ada"
    assert member["machine_count"] == 2
    assert member["machines_online"] == 1


# ---------------------------------------------------------------------------
# is_pool_member
# ---------------------------------------------------------------------------


def test_is_pool_member(db):
    owner = _new_user(db)
    outsider = _new_user(db)
    pool_id = _pool(db, owner)

    assert dbmod.is_pool_member(db, pool_id, owner) is True
    assert dbmod.is_pool_member(db, pool_id, outsider) is False


# ---------------------------------------------------------------------------
# pool_ids_for_machine_owner
# ---------------------------------------------------------------------------


def test_pool_ids_for_machine_owner_is_sorted(db):
    owner = _new_user(db)
    created = [str(_pool(db, owner, name=f"pool-{i}")) for i in range(3)]

    got = dbmod.pool_ids_for_machine_owner(db, owner)

    assert got == sorted(created)
    assert got == sorted(got)


def test_pool_ids_for_machine_owner_empty_for_a_lone_user(db):
    owner = _new_user(db)
    assert dbmod.pool_ids_for_machine_owner(db, owner) == []


# ---------------------------------------------------------------------------
# create_pool_invite / consume_pool_invite
# ---------------------------------------------------------------------------


def test_consume_pool_invite_decrements_exactly_once(db):
    """Two sequential redemptions of a one-use invite: only the first
    succeeds. This is the property the single ``UPDATE ... RETURNING``
    guarantees even under concurrent redemption — pinned here with two
    calls in sequence, the same way ``test_claim_attempt_credit_is_once_only``
    pins ``claim_attempt_credit``."""
    owner = _new_user(db)
    joiner = _new_user(db)
    pool_id = _pool(db, owner, name="Solo Squad")
    token_hash = _invite(db, pool_id, owner, uses=1)

    first = dbmod.consume_pool_invite(db, token_hash=token_hash, user_id=joiner)
    assert first is not None
    assert first["pool_id"] == pool_id
    assert first["name"] == "Solo Squad"
    assert dbmod.is_pool_member(db, pool_id, joiner) is True

    second = dbmod.consume_pool_invite(db, token_hash=token_hash, user_id=joiner)
    assert second is None


def test_consume_pool_invite_refuses_expired(db):
    owner = _new_user(db)
    joiner = _new_user(db)
    pool_id = _pool(db, owner)
    token_hash = _invite(
        db, pool_id, owner, uses=5,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    assert dbmod.consume_pool_invite(db, token_hash=token_hash, user_id=joiner) is None
    assert dbmod.is_pool_member(db, pool_id, joiner) is False


def test_consume_pool_invite_unknown_token_is_none(db):
    joiner = _new_user(db)
    assert dbmod.consume_pool_invite(
        db, token_hash="not-a-real-token", user_id=joiner
    ) is None


def test_consume_pool_invite_admits_the_profile(db):
    """Consuming an invite is the alpha gate's *only* other door besides
    being grandfathered at migration time — see 0007's header."""
    owner = _new_user(db)
    joiner = _new_user(db)
    pool_id = _pool(db, owner)
    token_hash = _invite(db, pool_id, owner, uses=1)

    assert dbmod.profile_is_admitted(db, joiner) is False

    result = dbmod.consume_pool_invite(db, token_hash=token_hash, user_id=joiner)

    assert result is not None
    assert dbmod.profile_is_admitted(db, joiner) is True


def test_consume_pool_invite_multi_use_is_exhausted_after_its_count(db):
    owner = _new_user(db)
    a, b, c = _new_user(db), _new_user(db), _new_user(db)
    pool_id = _pool(db, owner)
    token_hash = _invite(db, pool_id, owner, uses=2)

    assert dbmod.consume_pool_invite(db, token_hash=token_hash, user_id=a) is not None
    assert dbmod.consume_pool_invite(db, token_hash=token_hash, user_id=b) is not None
    assert dbmod.consume_pool_invite(db, token_hash=token_hash, user_id=c) is None
    assert dbmod.is_pool_member(db, pool_id, c) is False


def test_profile_is_admitted_false_for_unknown_user(db):
    assert dbmod.profile_is_admitted(db, str(uuid.uuid4())) is False


# ---------------------------------------------------------------------------
# fetch_job_for_viewer
# ---------------------------------------------------------------------------


def test_fetch_job_for_viewer_owner_member_and_stranger(db):
    owner = _new_user(db)
    teammate = _new_user(db)
    stranger = _new_user(db)
    pool_id = _pool(db, owner)
    _add_member(db, pool_id, teammate)
    job_id = _seed_job(db, owner, pool_id)

    assert dbmod.fetch_job_for_viewer(db, job_id, owner) is not None
    assert dbmod.fetch_job_for_viewer(db, job_id, teammate) is not None
    assert dbmod.fetch_job_for_viewer(db, job_id, stranger) is None
    # A job id that doesn't exist reads the same as one that isn't theirs.
    assert dbmod.fetch_job_for_viewer(db, "no-such-job", owner) is None


def test_fetch_job_for_viewer_null_pool_is_owner_only(db):
    """Every pre-pools job (``pool_id`` null) keeps exactly the visibility
    ``fetch_job_for_owner`` always gave it — the pool half of the check can
    never match a null ``pool_id``."""
    owner = _new_user(db)
    other = _new_user(db)
    job_id = _seed_job(db, owner, pool_id=None)

    assert dbmod.fetch_job_for_viewer(db, job_id, owner) is not None
    assert dbmod.fetch_job_for_viewer(db, job_id, other) is None


# ---------------------------------------------------------------------------
# list_pool_job_ids_for_member
# ---------------------------------------------------------------------------


def test_list_pool_job_ids_for_member(db):
    owner = _new_user(db)
    teammate = _new_user(db)
    stranger = _new_user(db)
    pool_id = _pool(db, owner)
    _add_member(db, pool_id, teammate)
    pool_job = _seed_job(db, owner, pool_id)
    _seed_job(db, owner, pool_id=None)  # not pool-scoped, must not appear

    assert dbmod.list_pool_job_ids_for_member(db, teammate) == [pool_job]
    assert dbmod.list_pool_job_ids_for_member(db, stranger) == []


# ---------------------------------------------------------------------------
# list_job_contributions
# ---------------------------------------------------------------------------


def test_list_job_contributions_joins_machine_and_member_names(db):
    owner = _new_user(db)
    dbmod.upsert_profile(db, owner, display_name="Grace Hopper")
    node_a, node_b = _node_id("credit-a"), _node_id("credit-b")
    _enrol(db, owner, node_a, last_seen_at=datetime.now(timezone.utc))
    _enrol(db, owner, node_b, last_seen_at=datetime.now(timezone.utc))
    job_id = _job_id()

    dbmod.record_contributions(
        db,
        job_id=job_id,
        entries=[
            {"node_id": node_a, "task_id": "task-000", "duration_s": 10.0},
            {"node_id": node_a, "task_id": "task-001", "duration_s": 5.0},
            {"node_id": node_b, "task_id": "task-000", "duration_s": 2.5},
        ],
    )

    rows = dbmod.list_job_contributions(db, job_id)
    assert [r["node_id"] for r in rows] == [node_a, node_b]
    a, b = rows
    assert a["machine_name"] == f"machine-{node_a}"
    assert a["member_display_name"] == "Grace Hopper"
    assert a["tasks_credited"] == 2
    assert a["total_duration_s"] == 15.0
    assert isinstance(a["total_duration_s"], float)
    assert b["tasks_credited"] == 1
    assert b["total_duration_s"] == 2.5


def test_list_job_contributions_empty_for_an_uncredited_job(db):
    assert dbmod.list_job_contributions(db, _job_id()) == []
