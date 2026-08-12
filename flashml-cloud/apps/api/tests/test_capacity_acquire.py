"""Acquisition: the row exists before the money does.

The ordering under test is the whole point. A crash between "we decided to
spend" and "the venue answered" must leave a row the reconciler still selects,
because that row is the only thing that will ever find the orphan.

Most of these tests are really one question, the same one
``test_capacity_reconcile.py`` asks: *can a machine we are paying for end up
in a state nothing will ever look at again?* The answer has to be no, and the
interesting cases are the ones where something went wrong — the venue raised,
the venue refused to destroy what it made, or a sweep settled the row while
the venue was still answering.

**Every test here cleans up its `rented_capacity`, `machines` and `pools`
rows.** The Postgres fixture is session-scoped and never truncated between
files, and `window_spend_usd` has no venue, owner or job filter *on purpose* —
it is one global ceiling. Rows left behind here are refusals somewhere else, in
a file that has no idea why. `test_capacity_budget.py::spender` states the same
rule at length; the fixtures below are a deliberate copy of it rather than a
shared `conftest.py` entry, because `conftest.py` is loaded by every test in
the suite and a collision there breaks runs that have nothing to do with this
feature.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

import pytest

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import sandbox_identity as si
from flashml_cloud_api.capacity.acquire import (
    ACQUIRE_NOT_DESTROYED,
    ACQUIRE_UNCONFIRMED,
    acquire_for_job,
)
from flashml_cloud_api.capacity.budget import (
    BudgetRefused,
    assert_within_budget,
    window_spend_usd,
)
from flashml_cloud_api.capacity.provider import (
    CapacityRequest,
    FakeProvider,
    ReleaseOutcome,
)
from flashml_cloud_api.capacity.reconcile import unreleased_rows
from test_jobs_from_repo import db  # noqa: F401 - fixture


class _Settings:
    rented_usd_per_acquisition_max = 2.0
    rented_usd_window_max = 10.0
    rented_usd_window_hours = 24.0
    coordinator_url = "http://coordinator"


@dataclass
class _Venue(FakeProvider):
    """A venue that can be made to behave badly on purpose.

    A subclass of the shipped `FakeProvider` so `acquire` stays the real thing
    and only the named half is instrumented. It mirrors
    `test_capacity_reconcile.py::_Venue` rather than importing it: a test
    module reaching into another test module's private names couples two files
    that are only meant to share a subject.

    `refuse_destroy` exists because **the `destroyed=False` branch had no
    coverage at all in this file**, and that gap is what allowed a RELEASED row
    to keep a live handle.
    """

    #: Refuse the destroy, the way a venue that answers and says no does.
    refuse_destroy: bool = False
    #: Answer a different hourly rate than the request quoted.
    answers_usd_per_hour: float | None = None
    #: A live connection. When set, the row is settled mid-`acquire`, the way
    #: a reconciler sweeping in another task would.
    sweeps_with: object = None

    async def acquire(self, *, request: CapacityRequest):
        got = await super().acquire(request=request)
        if self.sweeps_with is not None:
            with self.sweeps_with.cursor() as cur:
                cur.execute(
                    """
                    update public.rented_capacity
                       set state = 'RELEASED', released_at = now()
                     where owner_id = %s and state = 'REQUESTED'
                    """,
                    (request.owner_id,),
                )
        if self.answers_usd_per_hour is not None:
            got = replace(got, usd_per_hour=self.answers_usd_per_hour)
        return got

    async def release(self, *, handle: str) -> ReleaseOutcome:
        if self.refuse_destroy:
            # Still live afterwards: a venue that says no and means it.
            return ReleaseOutcome(destroyed=False, detail="deletion refused")
        return await super().release(handle=handle)


@pytest.fixture
def an_owner(db):
    """A real profile to charge against, and a promise to clean up.

    `rented_capacity.owner_id` is a real foreign key to `public.profiles`, so
    an invented `gen_random_uuid()` is refused by the database. Deleting the
    `auth.users` row cascades everything below it; the explicit deletes first
    are there so the intent survives a future change to the cascade — and so a
    leak fails here rather than as somebody else's budget refusal three files
    later.
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
            cur.execute(
                "delete from public.machines where owner_id = %s", (user_id,)
            )
            cur.execute(
                "delete from public.pools where owner_id = %s", (user_id,)
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


def _machine_status(db, machine_id):
    with db.cursor() as cur:
        cur.execute(
            "select status from public.machines where id = %s", (machine_id,)
        )
        row = cur.fetchone()
    return row["status"] if row else None


def _is_swept(db, rid) -> bool:
    """Would the reconciler find this row? The real query, not a restatement
    of it — the whole failure-path design is written against this list, so a
    test that asserted a state name instead would pass the day the list
    changed.

    Every window zeroed, which is this file's way of saying "ignoring time".
    The reconciler's real windows are about liveness — how long a machine gets
    to boot, or to go quiet, before nobody is using it — and none of that is
    what these tests are about: the question here is only whether a row a
    failed acquisition left behind is still in the list at all.

    **It is also blind to WHEN, and that hid a real bug for a whole branch**:
    every failure row here was selected by "old enough", so nothing noticed
    that the rows carrying a live handle were reaching the sweep with no
    `machine_id` and therefore waiting 30 minutes. `_swept_now` below asks the
    same question with the deployed windows, where only an immediate branch
    can answer yes; use it for anything that is about how FAST a row is found.
    """
    return str(rid) in {
        str(r["id"])
        for r in unreleased_rows(
            db, quiet_after_s=0.0, boot_grace_s=0.0, abandoned_after_s=0.0,
        )
    }


def _swept_now(db, rid) -> bool:
    """Would the reconciler find this row on its DEFAULT windows, today?

    No arguments, exactly as `reconcile_rented` calls it in production. A row
    these tests just created is seconds old, so every time-based branch —
    `abandoned_after_s` at 30 minutes, `boot_grace_s` at an hour — says no.
    Only `unreleased_rows`' first branch, a bound credential that is already
    revoked, can put it in the list, which makes this a direct measurement of
    the thing the zeroed windows cannot see.
    """
    return str(rid) in {str(r["id"]) for r in unreleased_rows(db)}


# ---------------------------------------------------------------------------
# the happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_successful_acquisition_lands_active_with_a_handle(
    db, an_owner, a_pool
):
    rid = await acquire_for_job(
        db, _Venue(), _Settings(), request=_request(an_owner, a_pool),
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
    provider = _Venue()

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


# ---------------------------------------------------------------------------
# what the venue answers about money
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_answered_rate_above_the_quote_is_re_gated_and_destroyed(
    db, an_owner, a_pool
):
    """The ceilings ran against the QUOTE. This is the first sight of what the
    venue will actually charge, and without a second look a venue that quotes
    $0.50 and answers $50.00/hr is recorded at $50.00 and refused by nothing.

    The refusal is not a note in a log: the machine is destroyed before it is
    raised.
    """
    venue = _Venue(answers_usd_per_hour=50.0)
    with pytest.raises(BudgetRefused) as exc:
        await acquire_for_job(
            db, venue, _Settings(), request=_request(an_owner, a_pool),
        )
    assert "50.0" in str(exc.value)
    assert venue.live_handles() == []
    row = _rows_for(db, an_owner)[0]
    assert row["state"] == "FAILED"
    assert row["failure_code"] == "BudgetRefused"
    assert row["released_at"] is not None
    # Never ACTIVE, so the answered rate was never recorded: the row keeps the
    # quote it was gated on, and the window is not left carrying $50/hr for a
    # machine that lived for a second.
    assert float(row["usd_per_hour"]) == 0.5
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == []


@pytest.mark.asyncio
async def test_an_answered_rate_within_the_ceilings_is_recorded_not_ignored(
    db, an_owner, a_pool
):
    """The other half of the re-gate: a higher-but-affordable answer is what
    we are billed, so it is what the window counts."""
    rid = await acquire_for_job(
        db, _Venue(answers_usd_per_hour=1.5), _Settings(),
        request=_request(an_owner, a_pool),
    )
    row = _row(db, rid)
    assert row["state"] == "ACTIVE"
    assert float(row["usd_per_hour"]) == 1.5


# ---------------------------------------------------------------------------
# the failure paths, which are the reason this module is written the way it is
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_acquire_leaves_a_row_the_sweep_still_selects(
    db, an_owner, a_pool
):
    """`acquire` raising is the most likely orphan there is: the pod is
    created, then waiting for it to register times out.

    `FakeProvider` destroys what it made before raising and is *required* to —
    but that obligation lives in a docstring, and "the venue refused before
    creating anything" is indistinguishable from "the venue created something
    and we lost it" from here. So the row is not closed on the strength of it.
    """
    provider = _Venue(fail_after_create=True)
    with pytest.raises(RuntimeError):
        await acquire_for_job(
            db, provider, _Settings(), request=_request(an_owner, a_pool),
        )
    assert provider.live_handles() == []
    rows = _rows_for(db, an_owner)
    assert len(rows) == 1
    assert rows[0]["failure_code"] == ACQUIRE_UNCONFIRMED
    assert rows[0]["failure_detail"]
    # Nothing to name, so nothing was named.
    assert rows[0]["provider_handle"] is None
    # The property, asked the way the reconciler asks it.
    assert _is_swept(db, rows[0]["id"])


@pytest.mark.asyncio
async def test_a_failure_before_the_venue_is_closed_not_left_for_the_sweep(
    db, an_owner, a_pool
):
    """The other side of the rule, and what keeps the sweep's list worth
    reading.

    A failure that happens before `provider.acquire` is ever called cannot
    have created anything, because nothing was asked. Closing it FAILED is
    provable rather than optimistic — and if these rows were left sweepable
    too, the list an operator reconciles against the venue would fill up with
    attempts that provably never reached it.

    A pool that already holds a machine is the failure used here because it is
    also the KNOWN LIMIT of this feature: it is meant to put a rented machine
    into the submitter's ordinary pool, alongside the machines they already
    have, and it cannot yet — `provision_sandbox_machine` ends with
    `assert_pool_isolated`, which requires the pool to hold exactly the one
    machine being minted. Relaxing that is out of scope in the design (§6);
    this test fails the moment somebody changes it, which is the conversation
    that should happen.
    """
    sitting_tenant = str(
        dbmod.insert_machine(
            db, owner_id=an_owner, node_id=f"laptop-{uuid.uuid4()}",
            name="the owner's own laptop", platform="linux",
        )
    )
    dbmod.bind_machine_pool(db, machine_id=sitting_tenant, pool_id=str(a_pool))

    provider = _Venue()
    with pytest.raises(si.PoolNotIsolated):
        await acquire_for_job(
            db, provider, _Settings(), request=_request(an_owner, a_pool),
        )
    assert provider.live_handles() == []
    rows = _rows_for(db, an_owner)
    assert len(rows) == 1
    assert rows[0]["state"] == "FAILED"
    assert rows[0]["failure_code"] == "PoolNotIsolated"
    assert rows[0]["provider_handle"] is None
    assert not _is_swept(db, rows[0]["id"])
    # The pool is exactly as it was: the failed mint rolled back with its
    # transaction rather than leaving a half-bound machine behind.
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == [sitting_tenant]


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
            db, _Venue(fail_after_create=True), _Settings(),
            request=_request(an_owner, a_pool, job="job-doomed"),
        )
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == []

    # ...and the retry, into the same pool, works.
    rid = await acquire_for_job(
        db, _Venue(), _Settings(),
        request=_request(an_owner, a_pool, job="job-retry"),
    )
    assert _row(db, rid)["state"] == "ACTIVE"


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
    venue = _Venue(sweeps_with=db)
    with pytest.raises(RuntimeError):
        await acquire_for_job(
            db, venue, _Settings(), request=_request(an_owner, a_pool),
        )
    # The money stopped.
    assert venue.live_handles() == []
    row = _rows_for(db, an_owner)[0]
    # ...and the row names what was destroyed, rather than the handle living
    # and dying in a local variable.
    assert row["provider_handle"]
    assert row["failure_code"]
    # RELEASED, written by the sweep that raced us, is now TRUE — so it is
    # left alone. Relabelling another actor's settled row as FAILED would
    # discard a correct statement for a less useful one.
    assert row["state"] == "RELEASED"
    assert row["released_at"] is not None
    # The credential went with it, so the pool can be rented into again.
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == []


@pytest.mark.asyncio
async def test_a_machine_that_will_not_die_reopens_the_row_it_lost_the_race_to(
    db, an_owner, a_pool
):
    """THE case this failure path exists for.

    A reconciler marks a handleless REQUESTED row RELEASED. This acquisition
    then returns from the venue with a real handle, loses its compare-and-set,
    records the handle — and its own release fails. Leaving the state alone
    would leave a RELEASED row naming a live machine, and
    `unreleased_rows` selects neither RELEASED nor FAILED, so nothing would
    ever look at it again: the machine bills for ever.

    So an unknown outcome does not leave the state alone. It forces the row
    back into the list.
    """
    venue = _Venue(sweeps_with=db, refuse_destroy=True)
    with pytest.raises(RuntimeError):
        await acquire_for_job(
            db, venue, _Settings(), request=_request(an_owner, a_pool),
        )
    # The machine really is still running. That is the premise, not an
    # accident of the fake.
    assert len(venue.live_handles()) == 1

    row = _rows_for(db, an_owner)[0]
    assert row["provider_handle"] == venue.live_handles()[0]
    assert row["failure_code"] == ACQUIRE_NOT_DESTROYED
    assert row["state"] == "REQUESTED"
    # The released_at the sweep wrote was a claim about a machine that turned
    # out to be alive, so it does not survive either.
    assert row["released_at"] is None
    assert _is_swept(db, row["id"])
    # The credential dies whatever the venue said — `cleanup_session`'s rule.
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == []


# ---------------------------------------------------------------------------
# how fast the sweep finds them, asked with the windows that actually ship
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_machine_that_will_not_die_is_swept_on_the_DEFAULT_windows(
    db, an_owner, a_pool
):
    """The same row as the test above, asked the way the deployed loop asks.

    `_is_swept` zeroes every window, and every other assertion in this file
    is content with that — which is exactly how this shipped: the rows we are
    SUREST hold a live machine were reaching `unreleased_rows` with
    `machine_id` null, missing its no-allowance branch (a revoked credential
    can never claim our work again) and landing in "nothing to ask", which
    waits `DEFAULT_ABANDONED_AFTER_S` — thirty minutes of billing.

    `machine_id` used to be written only by `_move_to_active`, the SUCCESS
    path. Nothing that failed ever named its machine. So this test asserts the
    row names both halves of what the acquisition created, and then asks the
    reconciler with the windows it actually ships with. The row is seconds
    old, so a `True` here can only have come from the revoked credential.
    """
    venue = _Venue(sweeps_with=db, refuse_destroy=True)
    with pytest.raises(RuntimeError):
        await acquire_for_job(
            db, venue, _Settings(), request=_request(an_owner, a_pool),
        )
    # The premise: the machine really is still running at the venue.
    assert len(venue.live_handles()) == 1

    row = _rows_for(db, an_owner)[0]
    assert row["failure_code"] == ACQUIRE_NOT_DESTROYED
    assert row["provider_handle"] == venue.live_handles()[0]
    # Never ACTIVE, and still named.
    assert row["state"] == "REQUESTED"
    assert row["machine_id"] is not None
    # ...and revoked, which is what the sweep's immediate branch keys on.
    assert _machine_status(db, row["machine_id"]) == "revoked"

    assert _swept_now(db, row["id"])


@pytest.mark.asyncio
async def test_an_unconfirmed_acquisition_is_listed_now_not_in_half_an_hour(
    db, an_owner, a_pool
):
    """The other row `_abandon` leaves behind, measured the same way.

    Nothing can be destroyed from here — `acquire` raised before returning a
    handle, so there is no name to destroy. But this list is what an operator
    reconciles against the venue's own machine listing, and a possible orphan
    belongs on it while they are still looking, not thirty minutes after the
    process that created it died.
    """
    provider = _Venue(fail_after_create=True)
    with pytest.raises(RuntimeError):
        await acquire_for_job(
            db, provider, _Settings(), request=_request(an_owner, a_pool),
        )
    row = _rows_for(db, an_owner)[0]
    assert row["failure_code"] == ACQUIRE_UNCONFIRMED
    # Nothing to name, so nothing was named — but the credential we minted is
    # ours to account for either way.
    assert row["provider_handle"] is None
    assert row["machine_id"] is not None
    assert _machine_status(db, row["machine_id"]) == "revoked"

    assert _swept_now(db, row["id"])


@pytest.mark.asyncio
async def test_a_row_closed_before_the_venue_is_not_swept_by_any_window(
    db, an_owner, a_pool
):
    """The guard on the fix: making failure rows visible sooner must not make
    the provably-empty ones visible at all.

    A pool that already holds a machine fails before `provider.acquire` is
    ever called, so nothing can exist at the venue; the row is closed FAILED
    and `unreleased_rows` selects neither FAILED nor RELEASED. If this ever
    turns True, the list an operator reconciles against the venue has started
    filling with attempts that provably never reached it.
    """
    sitting_tenant = str(
        dbmod.insert_machine(
            db, owner_id=an_owner, node_id=f"laptop-{uuid.uuid4()}",
            name="the owner's own laptop", platform="linux",
        )
    )
    dbmod.bind_machine_pool(db, machine_id=sitting_tenant, pool_id=str(a_pool))

    with pytest.raises(si.PoolNotIsolated):
        await acquire_for_job(
            db, _Venue(), _Settings(), request=_request(an_owner, a_pool),
        )
    row = _rows_for(db, an_owner)[0]
    assert row["state"] == "FAILED"
    # The mint rolled back with its transaction, so there is no machine to
    # name — and nothing invented one.
    assert row["machine_id"] is None
    assert not _swept_now(db, row["id"])
    assert not _is_swept(db, row["id"])


# ---------------------------------------------------------------------------
# the guard on the guard
# ---------------------------------------------------------------------------


def test_the_window_is_left_clean_for_the_next_file(db):
    """If this file ever commits rows it does not remove, every later test
    file inherits a ceiling it never spent — and the failure lands somewhere
    else entirely, which is the worst possible place to debug it from.

    Asserted as a NUMBER, not by calling `assert_within_budget`: the most this
    file could ever leak is a couple of dollars an hour against a $10 cap, and
    `FakeProvider` answers $0.00/hr, so a gate-shaped guard here would pass no
    matter how much it left behind.

    A failure means either this file leaked or an earlier one did; both are
    worth stopping for.
    """
    assert window_spend_usd(db, hours=24.0) == 0.0
    # And the gate itself still passes, which is what a later file will
    # actually depend on.
    assert_within_budget(
        db, venue_id="runpod", usd_per_hour=0.5, settings=_Settings(),
    )
