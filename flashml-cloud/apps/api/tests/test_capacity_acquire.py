"""Acquisition: the row exists before the money does.

The ordering under test is the whole point. A crash between "we decided to
spend" and "the venue answered" must leave a REQUESTED row, because that row
is the only thing that will ever find the orphan.

**Every test here cleans up its `rented_capacity` rows.** The Postgres
fixture is session-scoped and never truncated between files, and
`window_spend_usd` has no venue, owner or job filter *on purpose* — it is one
global ceiling. Rows left behind here are refusals somewhere else, in a file
that has no idea why. `test_capacity_budget.py::spender` states the same rule
at length; the fixtures below are a deliberate copy of it rather than a
shared `conftest.py` entry, because `conftest.py` is loaded by every test in
the suite and a collision there breaks runs that have nothing to do with this
feature.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import sandbox_identity as si
from flashml_cloud_api.capacity.acquire import acquire_for_job
from flashml_cloud_api.capacity.budget import BudgetRefused, assert_within_budget
from flashml_cloud_api.capacity.provider import CapacityRequest, FakeProvider
from test_jobs_from_repo import db  # noqa: F401 - fixture


class _Settings:
    rented_usd_per_acquisition_max = 2.0
    rented_usd_window_max = 10.0
    rented_usd_window_hours = 24.0
    coordinator_url = "http://coordinator"


@pytest.fixture
def an_owner(db):
    """A real profile to charge against, and a promise to clean up.

    `rented_capacity.owner_id` is a real foreign key to `public.profiles`, so
    an invented `gen_random_uuid()` is refused by the database. Deleting the
    `auth.users` row cascades the profile, its machines and its rented rows;
    the explicit delete first is there so the intent survives a future change
    to the cascade.
    """
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id, email) values (%s, %s)",
            (user_id, f"{user_id[:8]}@example.com"),
        )
        cur.execute("insert into public.profiles (id) values (%s)", (user_id,))
    try:
        yield user_id
    finally:
        with db.cursor() as cur:
            cur.execute(
                "delete from public.rented_capacity where owner_id = %s",
                (user_id,),
            )
            cur.execute("delete from auth.users where id = %s", (user_id,))


@pytest.fixture
def a_pool(db, an_owner):
    """A pool through the real constructor, which seats its owner as a
    member. Membership is not decoration: `provision_sandbox_machine` calls
    `lock_pool_for_owner`, which joins through `pool_members` and refuses an
    owner who is not also a member — a raw `insert into public.pools` yields a
    pool its own creator cannot mint into."""
    return str(
        dbmod.create_pool(db, name="rented-capacity-acquire", owner_id=an_owner)["id"]
    )


def _request(owner_id, pool_id, job="job-1"):
    return CapacityRequest(
        venue_id="fake", owner_id=str(owner_id), pool_id=str(pool_id),
        job_id=job, gpu_count=1, min_vram_gb=24.0,
        coordinator_url="http://coordinator", quoted_usd_per_hour=0.5,
    )


def _row(db, rid):
    with db.cursor() as cur:
        cur.execute("select * from public.rented_capacity where id = %s", (rid,))
        return cur.fetchone()


def _rows_for(db, owner_id):
    with db.cursor() as cur:
        cur.execute(
            "select * from public.rented_capacity where owner_id = %s"
            " order by created_at",
            (str(owner_id),),
        )
        return cur.fetchall()


@pytest.mark.asyncio
async def test_a_successful_acquisition_lands_active_with_a_handle(
    db, an_owner, a_pool
):
    rid = await acquire_for_job(
        db, FakeProvider(), _Settings(), request=_request(an_owner, a_pool),
    )
    row = _row(db, rid)
    assert row["state"] == "ACTIVE"
    assert row["provider_handle"]
    assert row["acquired_at"] is not None
    # The handle and the machine land in the SAME update that leaves
    # REQUESTED. A row that is ACTIVE without one of them is a machine we are
    # paying for and cannot name.
    assert row["machine_id"] is not None
    # And the machine went into the submitter's own pool, which is the whole
    # point of renting rather than opening an isolation session.
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == [
        str(row["machine_id"])
    ]


@pytest.mark.asyncio
async def test_a_refused_budget_creates_no_row_and_calls_no_provider(
    db, an_owner, a_pool
):
    """The gate runs BEFORE anything is created, so a refusal costs nothing
    and leaves nothing."""
    provider = FakeProvider()

    class _Tight(_Settings):
        rented_usd_per_acquisition_max = 0.0

    with pytest.raises(BudgetRefused):
        await acquire_for_job(
            db, provider, _Tight(), request=_request(an_owner, a_pool),
        )
    assert provider.live_handles() == []
    # Owner-scoped rather than a global `count(*)`: the database outlives
    # every test file in the session, and a global count would make this test
    # report on somebody else's rows.
    assert _rows_for(db, an_owner) == []
    # Nothing was minted either — a refusal that left a credential behind
    # would be a refusal that still changed the pool.
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == []


@pytest.mark.asyncio
async def test_a_provider_failure_records_FAILED_and_leaves_nothing_live(
    db, an_owner, a_pool
):
    provider = FakeProvider(fail_after_create=True)
    with pytest.raises(RuntimeError):
        await acquire_for_job(
            db, provider, _Settings(), request=_request(an_owner, a_pool),
        )
    assert provider.live_handles() == []
    rows = _rows_for(db, an_owner)
    assert len(rows) == 1
    assert rows[0]["state"] == "FAILED"
    assert rows[0]["failure_code"]
    # The failure is legible without reading source: which venue, and what
    # went wrong.
    assert rows[0]["failure_detail"]


@pytest.mark.asyncio
async def test_a_failed_acquisition_gives_the_pool_back(db, an_owner, a_pool):
    """A failure after minting must take the credential with it.

    Not tidiness. `provision_sandbox_machine` asserts the pool holds exactly
    the machine it just made, so a dead machine left bound to the pool makes
    the NEXT acquisition fail for a reason that has nothing to do with it —
    one bad venue call and the pool can never be rented into again.
    """
    with pytest.raises(RuntimeError):
        await acquire_for_job(
            db, FakeProvider(fail_after_create=True), _Settings(),
            request=_request(an_owner, a_pool, job="job-doomed"),
        )
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == []

    # ...and the retry, into the same pool, works.
    rid = await acquire_for_job(
        db, FakeProvider(), _Settings(),
        request=_request(an_owner, a_pool, job="job-retry"),
    )
    assert _row(db, rid)["state"] == "ACTIVE"


@dataclass
class _SweptFromUnderUs(FakeProvider):
    """A venue that answers while the reconciler releases the row.

    Not a contrivance: the row is opened before the venue is asked anything
    precisely so that a sweep can find it, and a sweep that fires during a
    slow acquisition is the race the `and state = 'REQUESTED'` guard on the
    ACTIVE update exists for. What matters is what happens next — the machine
    the venue just created is ours, it is billing, and nothing else knows its
    handle.
    """

    db: object = None

    async def acquire(self, *, request):
        got = await super().acquire(request=request)
        with self.db.cursor() as cur:  # type: ignore[union-attr]
            cur.execute(
                """
                update public.rented_capacity
                   set state = 'RELEASED', released_at = now()
                 where owner_id = %s and state = 'REQUESTED'
                """,
                (request.owner_id,),
            )
        return got


@pytest.mark.asyncio
async def test_a_machine_acquired_into_a_lost_race_is_destroyed_not_dropped(
    db, an_owner, a_pool
):
    """The failure path destroys what the venue made, and names it first.

    `FakeProvider` cleans up after its own failed `acquire`, so every other
    test here would pass against an implementation that simply forgot a live
    handle. This one hands back a machine the provider will NOT clean up and
    then makes the acquisition fail, which is the only shape in which
    "releases whatever the provider created" is actually a claim.
    """
    provider = _SweptFromUnderUs(db=db)
    with pytest.raises(RuntimeError):
        await acquire_for_job(
            db, provider, _Settings(), request=_request(an_owner, a_pool),
        )
    # The money stopped.
    assert provider.live_handles() == []
    row = _rows_for(db, an_owner)[0]
    # ...and the row names what was destroyed, rather than the handle living
    # and dying in a local variable.
    assert row["provider_handle"]
    assert row["failure_code"]
    # The credential went with it, so the pool can be rented into again.
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == []


@pytest.mark.asyncio
async def test_a_pool_that_already_holds_a_machine_is_refused_today(
    db, an_owner, a_pool
):
    """A KNOWN LIMIT, pinned here so it is discovered by a test rather than
    by a buyer.

    This feature is meant to put a rented machine into the submitter's
    ordinary pool, alongside the machines they already have. It cannot yet:
    `provision_sandbox_machine` ends with `assert_pool_isolated`, which
    requires the pool to hold exactly the one machine being minted. So the
    first rental into an already-populated pool is refused, and a second
    rental into the same pool is refused by the first one.

    The refusal is clean — it happens before the venue is asked anything, so
    no money is spent — and the row records it. Relaxing the assertion is
    explicitly out of scope in the design (§6 "Out of scope"); this test
    fails the moment somebody changes that, which is the conversation that
    should happen.
    """
    sitting_tenant = str(
        dbmod.insert_machine(
            db, owner_id=an_owner, node_id=f"laptop-{uuid.uuid4()}",
            name="the owner's own laptop", platform="linux",
        )
    )
    dbmod.bind_machine_pool(db, machine_id=sitting_tenant, pool_id=str(a_pool))

    provider = FakeProvider()
    with pytest.raises(si.PoolNotIsolated):
        await acquire_for_job(
            db, provider, _Settings(), request=_request(an_owner, a_pool),
        )
    # Refused before the venue was asked for anything.
    assert provider.live_handles() == []
    rows = _rows_for(db, an_owner)
    assert len(rows) == 1
    assert rows[0]["state"] == "FAILED"
    assert rows[0]["provider_handle"] is None
    # The pool is exactly as it was: the failed mint rolled back with its
    # transaction rather than leaving a half-bound machine behind.
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == [sitting_tenant]


def test_the_window_is_left_clean_for_the_next_file(db):
    """The guard on the guard, copied from `test_capacity_budget.py` for the
    same reason: if this file ever commits rows it does not remove, every
    later test file inherits a ceiling it never spent — and the failure lands
    somewhere else entirely, which is the worst possible place to debug it
    from."""
    assert_within_budget(
        db, venue_id="runpod", usd_per_hour=0.5, settings=_Settings(),
    )
