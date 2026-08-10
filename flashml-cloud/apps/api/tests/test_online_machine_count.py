"""``count_online_machines`` — the number a federated round is cut from.

It has to be the *same* number the console shows on the workspace page. If
the two drift, a submitter reads "6 machines online" and gets a round of four
slots, with nothing anywhere explaining the difference. That is why this
counts through ``MACHINE_ONLINE_PREDICATE`` rather than a fresh threshold of
its own, and why these tests set ``last_seen_at`` explicitly instead of
trusting a default.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from flashml_cloud_api import db as dbmod

from test_jobs_from_repo import (
    _new_user,
    db,  # noqa: F401 - fixture
)


def _machine(db, owner: str, *, seen_seconds_ago: float, status: str = "active",
             pool_id: str | None = None) -> str:
    machine_id = dbmod.insert_machine(
        db, owner_id=owner, node_id=f"node-{uuid.uuid4().hex[:8]}",
        name="a laptop", platform="linux",
    )
    with db.cursor() as cur:
        cur.execute(
            "update public.machines set status = %s, "
            "last_seen_at = now() - %s::interval where id = %s",
            (status, timedelta(seconds=seen_seconds_ago), machine_id),
        )
    if pool_id is not None:
        dbmod.bind_machine_pool(db, machine_id=machine_id, pool_id=pool_id)
    return machine_id


def test_a_machine_seen_just_now_counts(db):
    owner = _new_user(db)
    pool = dbmod.create_pool(db, name="crew", owner_id=owner)["id"]
    _machine(db, owner, seen_seconds_ago=5, pool_id=pool)
    assert dbmod.count_online_machines(db, pool_id=pool) == 1


def test_a_machine_that_stopped_reporting_does_not_count(db):
    """The predicate's whole purpose: a closed laptop is not capacity, and a
    round sized as though it were waits for a slot nobody will claim."""
    owner = _new_user(db)
    pool = dbmod.create_pool(db, name="crew", owner_id=owner)["id"]
    _machine(db, owner, seen_seconds_ago=600, pool_id=pool)
    assert dbmod.count_online_machines(db, pool_id=pool) == 0


def test_a_revoked_machine_does_not_count(db):
    owner = _new_user(db)
    pool = dbmod.create_pool(db, name="crew", owner_id=owner)["id"]
    _machine(db, owner, seen_seconds_ago=5, status="revoked", pool_id=pool)
    assert dbmod.count_online_machines(db, pool_id=pool) == 0


def test_a_machine_in_another_pool_does_not_count(db):
    """A pool job runs on that pool's machines. Counting someone else's would
    size the round for capacity it can never place work on."""
    owner = _new_user(db)
    ours = dbmod.create_pool(db, name="crew", owner_id=owner)["id"]
    theirs = dbmod.create_pool(db, name="other crew", owner_id=owner)["id"]
    _machine(db, owner, seen_seconds_ago=5, pool_id=theirs)
    assert dbmod.count_online_machines(db, pool_id=ours) == 0


def test_an_owned_but_unbound_machine_does_not_count_for_a_pool(db):
    """Opting a machine in is a separate act from owning it — the same
    intersection ``list_pool_members`` applies."""
    owner = _new_user(db)
    pool = dbmod.create_pool(db, name="crew", owner_id=owner)["id"]
    _machine(db, owner, seen_seconds_ago=5)
    assert dbmod.count_online_machines(db, pool_id=pool) == 0


def test_a_poolless_job_counts_every_online_machine(db):
    """``pool_id=None`` is a job on the public queue, which any online machine
    may claim, so the count is fleet-wide — a pool binding neither adds to it
    nor excludes from it.

    Asserted as a delta because the fleet is shared with every other test in
    this database; an absolute number here would pass or fail depending on
    what ran first.
    """
    owner = _new_user(db)
    pool = dbmod.create_pool(db, name="crew", owner_id=owner)["id"]
    before = dbmod.count_online_machines(db, pool_id=None)

    _machine(db, owner, seen_seconds_ago=5, pool_id=pool)
    _machine(db, owner, seen_seconds_ago=5)
    _machine(db, owner, seen_seconds_ago=600)

    assert dbmod.count_online_machines(db, pool_id=None) == before + 2
