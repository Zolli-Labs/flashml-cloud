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

    # A fresh heartbeat from the owner's machine, bound to the pool — this
    # is the one that should count. A stale one from the member's, also
    # bound, must not count (offline). A fresh but UNBOUND machine from the
    # owner must not count either — pool capacity is bindings, not
    # ownership — and member_count must stay unaffected by any of this.
    fresh = _enrol(db, owner, _node_id("fresh"), last_seen_at=datetime.now(timezone.utc))
    dbmod.bind_machine_pool(db, machine_id=fresh, pool_id=pool_id)
    stale = _enrol(
        db, member, _node_id("stale"),
        last_seen_at=datetime.now(timezone.utc) - timedelta(seconds=200),
    )
    dbmod.bind_machine_pool(db, machine_id=stale, pool_id=pool_id)
    _enrol(
        db, owner, _node_id("unbound-online"),
        last_seen_at=datetime.now(timezone.utc),
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


def test_list_pools_for_user_excludes_unbound_machines_from_online_count(db):
    """Regression pin: ``machines_online`` used to join machines straight
    off ownership, with no ``machine_pools`` intersection — so a machine
    that was merely owned, never bound to the pool, still inflated
    'Workers online'. An unbound online machine must contribute 0; binding
    it must flip it to 1."""
    owner = _new_user(db)
    pool_id = _pool(db, owner)
    machine_id = _enrol(
        db, owner, _node_id("unbound-then-bound"),
        last_seen_at=datetime.now(timezone.utc),
    )

    assert dbmod.list_pools_for_user(db, owner)[0]["machines_online"] == 0

    dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=pool_id)

    assert dbmod.list_pools_for_user(db, owner)[0]["machines_online"] == 1


def test_list_pools_for_user_only_returns_own_pools(db):
    owner = _new_user(db)
    stranger = _new_user(db)
    _pool(db, owner)

    assert dbmod.list_pools_for_user(db, stranger) == []


def test_a_revoked_machine_never_counts_as_online(db):
    """``machines_online`` requires ``status = 'active'`` — a fresh
    heartbeat timestamp left over from before revocation must not count,
    even for a machine that is bound to the pool."""
    owner = _new_user(db)
    pool_id = _pool(db, owner)
    machine_id = _enrol(
        db, owner, _node_id("revoked"),
        last_seen_at=datetime.now(timezone.utc), status="revoked",
    )
    dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=pool_id)

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
    m1 = _enrol(db, owner, _node_id("m1"), last_seen_at=datetime.now(timezone.utc))
    dbmod.bind_machine_pool(db, machine_id=m1, pool_id=pool_id)
    m2 = _enrol(
        db, owner, _node_id("m2"),
        last_seen_at=datetime.now(timezone.utc) - timedelta(seconds=200),
    )
    dbmod.bind_machine_pool(db, machine_id=m2, pool_id=pool_id)
    # Owned but never bound to this pool — must not inflate either count.
    _enrol(
        db, owner, _node_id("m3-unbound"),
        last_seen_at=datetime.now(timezone.utc),
    )

    members = dbmod.list_pool_members(db, pool_id)
    assert len(members) == 1
    member = members[0]
    assert str(member["user_id"]) == owner
    assert member["display_name"] == "Ada"
    assert member["machine_count"] == 2
    assert member["machines_online"] == 1


def test_list_pool_members_excludes_unbound_machines_from_both_counts(db):
    """Same regression, per-member: an owned-but-unbound machine must not
    inflate ``machine_count`` or ``machines_online`` for that member."""
    owner = _new_user(db)
    pool_id = _pool(db, owner)
    machine_id = _enrol(
        db, owner, _node_id("member-unbound-then-bound"),
        last_seen_at=datetime.now(timezone.utc),
    )

    member = dbmod.list_pool_members(db, pool_id)[0]
    assert member["machine_count"] == 0
    assert member["machines_online"] == 0

    dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=pool_id)

    member = dbmod.list_pool_members(db, pool_id)[0]
    assert member["machine_count"] == 1
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
    # Unadmitted on purpose: `_new_user` marks its profile admitted by
    # default now (Task 10 — most fixtures across the suite need that), so
    # this is the one call site that opts back out to keep exercising the
    # property this test is actually named for.
    joiner = _new_user(db, admitted=False)
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


# ---------------------------------------------------------------------------
# pool_ids_for_machine / bind_machine_pool / unbind_machine_pool (Task 2)
# ---------------------------------------------------------------------------


def test_stamp_requires_both_binding_and_membership(db):
    """Opt-in: member+unbound stamps nothing; bound+member stamps; and a
    binding to a pool the owner is no longer a member of is inert."""
    owner = _new_user(db)
    pool = dbmod.create_pool(db, name=f"t2-{RUN_MARKER}", owner_id=owner)
    machine_id = _enrol(db, owner, _node_id("stamp"))
    assert dbmod.pool_ids_for_machine(db, machine_id) == []          # member, unbound
    dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=str(pool["id"]))
    assert dbmod.pool_ids_for_machine(db, machine_id) == [str(pool["id"])]
    with db.cursor() as cur:                                          # membership revoked, binding remains
        cur.execute("delete from public.pool_members where pool_id = %s and user_id = %s",
                    (pool["id"], owner))
    assert dbmod.pool_ids_for_machine(db, machine_id) == []           # binding alone grants nothing


def test_pool_ids_for_machine_is_sorted_across_multiple_bindings(db):
    owner = _new_user(db)
    machine_id = _enrol(db, owner, _node_id("multi"))
    pools = [dbmod.create_pool(db, name=f"p-{i}", owner_id=owner) for i in range(3)]
    for pool in pools:
        dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=str(pool["id"]))

    got = dbmod.pool_ids_for_machine(db, machine_id)
    assert got == sorted(str(p["id"]) for p in pools)


def test_bind_machine_pool_is_idempotent(db):
    """Double-binding the same machine to the same pool writes one row —
    ``on conflict do nothing``, the same idiom ``consume_pool_invite`` uses
    for pool membership."""
    owner = _new_user(db)
    pool = dbmod.create_pool(db, name=f"t2-{RUN_MARKER}", owner_id=owner)
    machine_id = _enrol(db, owner, _node_id("idempotent"))

    dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=str(pool["id"]))
    dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=str(pool["id"]))

    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.machine_pools "
            "where machine_id = %s and pool_id = %s",
            (machine_id, pool["id"]),
        )
        assert cur.fetchone()["n"] == 1


def test_unbind_machine_pool_removes_the_binding(db):
    owner = _new_user(db)
    pool = dbmod.create_pool(db, name=f"t2-{RUN_MARKER}", owner_id=owner)
    machine_id = _enrol(db, owner, _node_id("unbind"))
    dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=str(pool["id"]))
    assert dbmod.pool_ids_for_machine(db, machine_id) == [str(pool["id"])]

    dbmod.unbind_machine_pool(db, machine_id=machine_id, pool_id=str(pool["id"]))

    assert dbmod.pool_ids_for_machine(db, machine_id) == []


def test_unbind_machine_pool_is_a_noop_when_not_bound(db):
    owner = _new_user(db)
    pool = dbmod.create_pool(db, name=f"t2-{RUN_MARKER}", owner_id=owner)
    machine_id = _enrol(db, owner, _node_id("unbind-noop"))

    dbmod.unbind_machine_pool(db, machine_id=machine_id, pool_id=str(pool["id"]))

    assert dbmod.pool_ids_for_machine(db, machine_id) == []


# ---------------------------------------------------------------------------
# set_machine_capabilities (Task 2)
# ---------------------------------------------------------------------------


def test_set_machine_capabilities_writes_all_four_and_a_recall_overwrites(db):
    owner = _new_user(db)
    machine_id = _enrol(db, owner, _node_id("caps"))

    dbmod.set_machine_capabilities(
        db, machine_id=machine_id, sandbox_capable=True, argv_capable=True,
        unsandboxed_argv_capable=False, module_capable=True,
    )
    with db.cursor() as cur:
        cur.execute(
            "select sandbox_capable, argv_capable, unsandboxed_argv_capable, "
            "module_capable from public.machines where id = %s",
            (machine_id,),
        )
        row = cur.fetchone()
    assert row["sandbox_capable"] is True
    assert row["argv_capable"] is True
    assert row["unsandboxed_argv_capable"] is False
    assert row["module_capable"] is True

    # A re-call overwrites, not merges — a machine re-registering with a
    # narrower set of capabilities must show the narrower set, not the union.
    dbmod.set_machine_capabilities(
        db, machine_id=machine_id, sandbox_capable=False, argv_capable=False,
        unsandboxed_argv_capable=False, module_capable=False,
    )
    with db.cursor() as cur:
        cur.execute(
            "select sandbox_capable, argv_capable, unsandboxed_argv_capable, "
            "module_capable from public.machines where id = %s",
            (machine_id,),
        )
        row = cur.fetchone()
    assert row["sandbox_capable"] is False
    assert row["argv_capable"] is False
    assert row["unsandboxed_argv_capable"] is False
    assert row["module_capable"] is False


# ---------------------------------------------------------------------------
# pools_for_machines_of_owner (Task 2)
# ---------------------------------------------------------------------------


def test_pools_for_machines_of_owner_returns_the_chip_map(db):
    owner = _new_user(db)
    pool = dbmod.create_pool(db, name=f"t2-{RUN_MARKER}", owner_id=owner)
    bound = _enrol(db, owner, _node_id("chip-bound"))
    unbound = _enrol(db, owner, _node_id("chip-unbound"))
    dbmod.bind_machine_pool(db, machine_id=bound, pool_id=str(pool["id"]))

    chips = dbmod.pools_for_machines_of_owner(db, owner)

    assert chips[str(bound)] == [{"id": str(pool["id"]), "name": pool["name"]}]
    # A machine with no bindings is simply absent — callers default to [].
    assert str(unbound) not in chips


def test_pools_for_machines_of_owner_empty_dict_for_an_ownerless_machine_set(db):
    owner = _new_user(db)
    assert dbmod.pools_for_machines_of_owner(db, owner) == {}


def test_pools_for_machines_of_owner_hides_a_chip_after_membership_is_revoked(db):
    """Carried from Task 2's review: this query joined ``machine_pools`` to
    ``pools`` without intersecting ``pool_members`` on the owner, so a
    binding to a pool the owner had since left still rendered a chip —
    ``pool_ids_for_machine``'s stamp already excludes exactly this case
    (``test_stamp_requires_both_binding_and_membership`` above), and the
    chip map must agree with the stamp rather than show a pool the machine
    is not actually allowed to serve. The binding row itself is untouched:
    only the chip is gated on live membership, the same as the stamp.
    """
    owner = _new_user(db)
    pool = dbmod.create_pool(db, name=f"t2-{RUN_MARKER}", owner_id=owner)
    machine_id = _enrol(db, owner, _node_id("chip-revoked"))
    dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=str(pool["id"]))
    assert dbmod.pools_for_machines_of_owner(db, owner)[str(machine_id)] == [
        {"id": str(pool["id"]), "name": pool["name"]}
    ]

    with db.cursor() as cur:
        cur.execute(
            "delete from public.pool_members where pool_id = %s and user_id = %s",
            (pool["id"], owner),
        )

    assert str(machine_id) not in dbmod.pools_for_machines_of_owner(db, owner)
    # ...while the binding row itself still exists.
    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.machine_pools "
            "where machine_id = %s and pool_id = %s",
            (machine_id, pool["id"]),
        )
        assert cur.fetchone()["n"] == 1


# ---------------------------------------------------------------------------
# fetch_outstanding_invite / revoke_pool_invites (Task 2)
# ---------------------------------------------------------------------------


def test_fetch_outstanding_invite_none_when_there_are_no_invites(db):
    owner = _new_user(db)
    pool = dbmod.create_pool(db, name=f"t2-{RUN_MARKER}", owner_id=owner)
    assert dbmod.fetch_outstanding_invite(db, str(pool["id"])) is None


def test_fetch_outstanding_invite_none_when_expired(db):
    owner = _new_user(db)
    pool = dbmod.create_pool(db, name=f"t2-{RUN_MARKER}", owner_id=owner)
    _invite(
        db, pool["id"], owner, uses=5,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    assert dbmod.fetch_outstanding_invite(db, str(pool["id"])) is None


def test_fetch_outstanding_invite_none_when_exhausted(db):
    owner = _new_user(db)
    joiner = _new_user(db)
    pool = dbmod.create_pool(db, name=f"t2-{RUN_MARKER}", owner_id=owner)
    token_hash = _invite(db, pool["id"], owner, uses=1)
    assert dbmod.consume_pool_invite(db, token_hash=token_hash, user_id=joiner) is not None

    assert dbmod.fetch_outstanding_invite(db, str(pool["id"])) is None


def test_fetch_outstanding_invite_returns_the_newest_valid_one(db):
    owner = _new_user(db)
    pool = dbmod.create_pool(db, name=f"t2-{RUN_MARKER}", owner_id=owner)
    _invite(db, pool["id"], owner, uses=3)
    newest_hash = _invite(db, pool["id"], owner, uses=7)

    invite = dbmod.fetch_outstanding_invite(db, str(pool["id"]))

    assert invite is not None
    assert invite["uses_remaining"] == 7
    assert set(invite.keys()) == {"uses_remaining", "expires_at", "created_at"}
    assert "token_hash" not in invite
    assert newest_hash  # sanity: the helper did return a hash, just never exposed


def test_revoke_pool_invites_deletes_all_valid_and_spent_and_returns_the_count(db):
    owner = _new_user(db)
    joiner = _new_user(db)
    pool = dbmod.create_pool(db, name=f"t2-{RUN_MARKER}", owner_id=owner)
    spent_hash = _invite(db, pool["id"], owner, uses=1)
    assert dbmod.consume_pool_invite(db, token_hash=spent_hash, user_id=joiner) is not None
    valid_hash = _invite(db, pool["id"], owner, uses=3)

    count = dbmod.revoke_pool_invites(db, pool_id=str(pool["id"]))

    assert count == 2
    assert dbmod.fetch_outstanding_invite(db, str(pool["id"])) is None
    assert dbmod.consume_pool_invite(
        db, token_hash=valid_hash, user_id=joiner
    ) is None
