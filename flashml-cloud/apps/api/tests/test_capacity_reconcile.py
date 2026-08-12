"""Teardown is the guarantee. The request path is best effort.

Every test here is really one question: *can a machine we are paying for end
up in a state nothing will ever look at again?* The answer has to be no, and
the interesting cases are all the ones where something went wrong — the venue
refused, the venue raised, the venue is not configured, or the row never
learned the handle in the first place.

That last one is the reason the "no handle, therefore nothing was created"
shortcut is not taken anywhere in ``reconcile.py``, and
``test_the_race_a_handleless_row_would_have_lost`` is the test that pins it.

**Every test here cleans up its `rented_capacity`, `machines` and `pools`
rows.** The Postgres fixture is session-scoped and never truncated between
files, and ``budget.window_spend_usd`` has no venue, owner or job filter *on
purpose* — it is one global ceiling. Rows left behind here are refusals
somewhere else, in a file that has no idea why. The fixtures below are a
deliberate copy of ``test_capacity_acquire.py``'s rather than a shared
``conftest.py`` entry, because ``conftest.py`` is loaded by every test in the
suite and a collision there breaks runs that have nothing to do with this
feature.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest

from flashml_cloud_api import db as dbmod
from flashml_cloud_api.capacity import reconcile as reconcile_mod
from flashml_cloud_api.capacity.acquire import acquire_for_job
from flashml_cloud_api.capacity.budget import window_spend_usd
from flashml_cloud_api.capacity.provider import (
    CapacityRequest,
    FakeProvider,
    ProviderState,
    ReleaseOutcome,
)
from flashml_cloud_api.capacity.reconcile import (
    reconcile_rented,
    release_capacity,
    unreleased_rows,
)
from test_jobs_from_repo import db  # noqa: F401 - fixture

SETTLED = 3600.0  # the settle window every sweep in this file uses
OLD = 10800.0     # 3h: comfortably past it
FRESH = 60.0      # a minute: comfortably inside it


class _Settings:
    rented_usd_per_acquisition_max = 2.0
    rented_usd_window_max = 10.0
    rented_usd_window_hours = 24.0
    coordinator_url = "http://coordinator"


@dataclass
class _Venue(FakeProvider):
    """A venue that can be made to behave badly on purpose.

    A subclass of the shipped ``FakeProvider`` so that ``acquire`` stays the
    real thing and only the teardown half is instrumented. The knobs exist
    because **nothing in this repository tested a `destroyed=False` release
    before this file**, and that gap is precisely how a sweep that closed rows
    on weak evidence could have shipped.
    """

    refuse_destroy: bool = False
    release_raises: bool = False
    observe_raises: bool = False
    #: Every handle `release` was called with, in order. A release the sweep
    #: skipped and a release that failed look identical on the row; they do
    #: not look identical here.
    release_calls: list[str] = field(default_factory=list)

    def rent(self) -> str:
        """A handle for a machine that is already billing at this venue.

        Used by the tests that build a row directly instead of acquiring one:
        the pool-isolation assertion in ``provision_sandbox_machine`` allows a
        pool exactly one machine, so a sweep over several rows cannot be built
        out of several real acquisitions.
        """
        handle = f"{self.venue_id}-{uuid.uuid4().hex[:12]}"
        self._live.add(handle)
        return handle

    async def release(self, *, handle: str) -> ReleaseOutcome:
        self.release_calls.append(handle)
        if self.release_raises:
            raise RuntimeError("the venue is not answering")
        if self.refuse_destroy:
            # Still live afterwards: a venue that says no and means it.
            return ReleaseOutcome(destroyed=False, detail="deletion refused")
        return await super().release(handle=handle)

    async def observe(self, *, handle: str) -> ProviderState:
        if self.observe_raises:
            raise RuntimeError("the venue is not answering")
        return await super().observe(handle=handle)


@pytest.fixture
def an_owner(db):  # noqa: F811 - the imported `db` fixture
    """A real profile to charge against, and a promise to clean up.

    ``rented_capacity.owner_id`` is a real foreign key to ``public.profiles``,
    so an invented ``gen_random_uuid()`` is refused by the database. Deleting
    the ``auth.users`` row cascades everything below it; the explicit deletes
    first are there so the intent survives a future change to the cascade —
    and so that a leak shows up as a failure here rather than as somebody
    else's budget refusal three files later.
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
def a_pool(db, an_owner):  # noqa: F811
    """A pool through the real constructor, which seats its owner as a
    member. Membership is not decoration: ``provision_sandbox_machine`` calls
    ``lock_pool_for_owner``, which joins through ``pool_members`` and refuses
    an owner who is not also a member — a raw ``insert into public.pools``
    yields a pool its own creator cannot mint into."""
    return str(
        dbmod.create_pool(
            db, name="rented-capacity-reconcile", owner_id=an_owner
        )["id"]
    )


def _request(owner_id, pool_id, job="job-1"):
    return CapacityRequest(
        venue_id="fake", owner_id=str(owner_id), pool_id=str(pool_id),
        job_id=job, gpu_count=1, min_vram_gb=24.0,
        coordinator_url="http://coordinator", quoted_usd_per_hour=0.5,
    )


def _row(db, rid):  # noqa: F811
    with db.cursor() as cur:
        cur.execute(
            "select * from public.rented_capacity where id = %s", (rid,)
        )
        return cur.fetchone()


def _machine_status(db, machine_id):  # noqa: F811
    with db.cursor() as cur:
        cur.execute(
            "select status from public.machines where id = %s", (machine_id,)
        )
        row = cur.fetchone()
    return row["status"] if row else None


def _insert_row(
    db,  # noqa: F811
    *,
    owner_id,
    pool_id,
    venue_id="fake",
    state="ACTIVE",
    handle=None,
    age_s=OLD,
    usd_per_hour=0.5,
):
    """One rented_capacity row, aged, without going through the venue."""
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.rented_capacity
                (venue_id, state, owner_id, pool_id, job_id, provider_handle,
                 usd_per_hour, created_at, acquired_at)
            values (%s, %s, %s, %s, 'job-sweep', %s, %s,
                    now() - make_interval(secs => %s),
                    case when %s = 'REQUESTED' then null
                         else now() - make_interval(secs => %s) end)
            returning id
            """,
            (venue_id, state, str(owner_id), str(pool_id), handle,
             usd_per_hour, float(age_s), state, float(age_s)),
        )
        return str(cur.fetchone()["id"])


def _aged(db, rid, seconds=OLD):  # noqa: F811
    """Push a row's clock back past the settle window."""
    with db.cursor() as cur:
        cur.execute(
            """
            update public.rented_capacity
               set created_at = now() - make_interval(secs => %s),
                   acquired_at = case when acquired_at is null then null
                        else now() - make_interval(secs => %s) end
             where id = %s
            """,
            (float(seconds), float(seconds), rid),
        )
    db.commit()


# ---------------------------------------------------------------------------
# the happy path, end to end through a real acquisition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_destroys_and_marks_released(db, an_owner, a_pool):  # noqa: F811
    venue = _Venue()
    rid = await acquire_for_job(
        db, venue, _Settings(), request=_request(an_owner, a_pool)
    )
    assert await release_capacity(db, venue, rented_id=rid) is True
    assert venue.live_handles() == []
    row = _row(db, rid)
    assert row["state"] == "RELEASED"
    assert row["released_at"] is not None


@pytest.mark.asyncio
async def test_release_is_idempotent(db, an_owner, a_pool):  # noqa: F811
    """The sweep calls this repeatedly by design, so a second call must be
    both harmless and cheap: an already-RELEASED row asks the venue nothing."""
    venue = _Venue()
    rid = await acquire_for_job(
        db, venue, _Settings(), request=_request(an_owner, a_pool)
    )
    await release_capacity(db, venue, rented_id=rid)
    first_released_at = _row(db, rid)["released_at"]

    assert await release_capacity(db, venue, rented_id=rid) is True
    assert len(venue.release_calls) == 1
    # The timestamp is when the money stopped, not when we last looked.
    assert _row(db, rid)["released_at"] == first_released_at


@pytest.mark.asyncio
async def test_the_sweep_releases_what_the_request_path_missed(
    db, an_owner, a_pool  # noqa: F811
):
    """The whole point: nobody called release, and the money still stops."""
    venue = _Venue()
    rid = await acquire_for_job(
        db, venue, _Settings(), request=_request(an_owner, a_pool)
    )
    _aged(db, rid)

    touched = await reconcile_rented(db, {"fake": venue}, settle_after_s=SETTLED)
    assert rid in touched
    assert venue.live_handles() == []
    assert _row(db, rid)["state"] == "RELEASED"


@pytest.mark.asyncio
async def test_the_sweep_leaves_fresh_rentals_alone(db, an_owner, a_pool):  # noqa: F811
    """A sweep that destroyed a machine still being handed to its job would
    be a worse bug than the one this module exists to prevent."""
    venue = _Venue()
    rid = await acquire_for_job(
        db, venue, _Settings(), request=_request(an_owner, a_pool)
    )
    touched = await reconcile_rented(db, {"fake": venue}, settle_after_s=SETTLED)
    assert rid not in touched
    assert len(venue.live_handles()) == 1
    assert venue.release_calls == []
    assert _row(db, rid)["state"] == "ACTIVE"


@pytest.mark.asyncio
async def test_a_released_rental_gives_its_credential_and_its_pool_back(
    db, an_owner, a_pool  # noqa: F811
):
    """Teardown is two things, and the second one is not tidiness.

    A rented machine is minted ``lifecycle = 'persistent'``, so
    ``expire_stale_ephemeral_machines`` never comes for it. Left bound, its
    token still authenticates for a machine we have handed back to a third
    party — and ``provision_sandbox_machine``'s closing isolation assertion
    refuses the next rental into that pool. Renting once would poison the pool
    for good, which is why the final assertion here is a second rental rather
    than a database state.
    """
    venue = _Venue()
    rid = await acquire_for_job(
        db, venue, _Settings(), request=_request(an_owner, a_pool)
    )
    machine_id = str(_row(db, rid)["machine_id"])

    await release_capacity(db, venue, rented_id=rid)

    assert _machine_status(db, machine_id) == "revoked"
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == []
    again = await acquire_for_job(
        db, venue, _Settings(),
        request=_request(an_owner, a_pool, job="job-after-release"),
    )
    assert _row(db, again)["state"] == "ACTIVE"


@pytest.mark.asyncio
async def test_the_credential_dies_even_when_the_venue_will_not(
    db, an_owner, a_pool  # noqa: F811
):
    """``cleanup_session``'s rule: neither failure may hide the other.

    The venue refusing to destroy the machine is not a reason to leave a live
    token on it. The row stays sweepable because the money may still be
    running; the credential is dead regardless.
    """
    venue = _Venue()
    rid = await acquire_for_job(
        db, venue, _Settings(), request=_request(an_owner, a_pool)
    )
    machine_id = str(_row(db, rid)["machine_id"])
    venue.refuse_destroy = True

    assert await release_capacity(db, venue, rented_id=rid) is False
    assert len(venue.live_handles()) == 1
    assert _row(db, rid)["state"] == "ACTIVE"
    assert _machine_status(db, machine_id) == "revoked"
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == []


# ---------------------------------------------------------------------------
# what gets swept, and what deliberately does not
# ---------------------------------------------------------------------------


def test_unreleased_rows_selects_only_what_is_still_billing_and_settled(
    db, an_owner, a_pool  # noqa: F811
):
    """The selection IS the guarantee, so it is pinned directly.

    ``FAILED`` being absent is the load-bearing half: ``acquire.py``'s failure
    path deliberately refuses to write ``FAILED`` while a handle may still be
    live, and this query is the reason why. If ``FAILED`` were swept, that
    care would be pointless; if it is swept *later*, this test says out loud
    what such a change would mean.
    """
    mine = {
        "requested_old": _insert_row(
            db, owner_id=an_owner, pool_id=a_pool, state="REQUESTED",
        ),
        "active_old": _insert_row(
            db, owner_id=an_owner, pool_id=a_pool, handle="h-active-old",
        ),
        "active_fresh": _insert_row(
            db, owner_id=an_owner, pool_id=a_pool, handle="h-active-fresh",
            age_s=FRESH,
        ),
        "requested_fresh": _insert_row(
            db, owner_id=an_owner, pool_id=a_pool, state="REQUESTED",
            age_s=FRESH,
        ),
        "released_old": _insert_row(
            db, owner_id=an_owner, pool_id=a_pool, state="RELEASED",
            handle="h-released",
        ),
        "failed_old": _insert_row(
            db, owner_id=an_owner, pool_id=a_pool, state="FAILED",
            handle="h-failed",
        ),
    }
    # Intersected with our own ids: the database outlives every test file in
    # the session and a bare list would report on somebody else's rows.
    swept = {str(r["id"]) for r in unreleased_rows(db, settle_after_s=SETTLED)}
    got = {name for name, rid in mine.items() if rid in swept}
    assert got == {"requested_old", "active_old"}


def test_a_requested_row_is_measured_from_created_at(db, an_owner, a_pool):  # noqa: F811
    """A REQUESTED row has no ``acquired_at`` at all, so the window has to
    fall back to ``created_at`` — otherwise a null would compare as null and
    the rows most likely to be orphans would be the ones never swept."""
    rid = _insert_row(db, owner_id=an_owner, pool_id=a_pool, state="REQUESTED")
    assert _row(db, rid)["acquired_at"] is None
    swept = {str(r["id"]) for r in unreleased_rows(db, settle_after_s=SETTLED)}
    assert rid in swept


# ---------------------------------------------------------------------------
# the case the whole design turns on: a row with no handle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_handleless_row_is_never_marked_released(
    db, an_owner, a_pool  # noqa: F811
):
    """Absence of a handle is not evidence of absence of a machine.

    It is the one case where something may exist at the venue that we cannot
    name — and ``observe`` cannot help, because it reads the venue *by
    handle*. So the row stays where the sweep can see it, and the reason is
    written on the row rather than left for somebody to infer.
    """
    venue = _Venue()
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, state="REQUESTED", handle=None,
    )
    touched = await reconcile_rented(db, {"fake": venue}, settle_after_s=SETTLED)

    assert rid not in touched
    row = _row(db, rid)
    assert row["state"] == "REQUESTED"
    assert row["released_at"] is None
    assert row["failure_code"] == "RECONCILE_NO_HANDLE"
    assert "provider_handle" in (row["failure_detail"] or "")
    # And it is still sweepable, which is the entire claim.
    assert rid in {
        str(r["id"]) for r in unreleased_rows(db, settle_after_s=SETTLED)
    }


@pytest.mark.asyncio
async def test_the_race_a_handleless_row_would_have_lost(
    db, an_owner, a_pool  # noqa: F811
):
    """The orphan that closing a handleless row would create, step by step.

    1. The sweep meets a REQUESTED row whose handle is still null — the window
       between "we decided to spend money" and "the venue answered".
    2. The acquisition it raced comes back with a real machine, loses its
       compare-and-set against ``state = 'REQUESTED'``, records the handle,
       and its own release fails.

    Had step 1 marked the row RELEASED, step 2 would leave a RELEASED row
    carrying a live handle, and ``unreleased_rows`` — which selects only
    REQUESTED and ACTIVE — would never look at it again. The machine would
    bill for ever. Here, the row is still swept and the machine still dies.
    """
    venue = _Venue()
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, state="REQUESTED", handle=None,
    )

    # 1. The sweep, mid-acquisition.
    assert await reconcile_rented(
        db, {"fake": venue}, settle_after_s=SETTLED
    ) == []

    # 2. The venue answers, the acquisition loses the race, and the handle is
    #    recorded on the row it can no longer move.
    handle = venue.rent()
    with db.cursor() as cur:
        cur.execute(
            "update public.rented_capacity set provider_handle = %s "
            "where id = %s",
            (handle, rid),
        )
    db.commit()
    venue.refuse_destroy = True  # ...and the acquisition's own release fails.
    assert await release_capacity(db, venue, rented_id=rid) is False
    assert venue.live_handles() == [handle]

    # 3. The next healthy sweep finds it, because the row was never closed.
    venue.refuse_destroy = False
    touched = await reconcile_rented(db, {"fake": venue}, settle_after_s=SETTLED)
    assert touched == [rid]
    assert venue.live_handles() == []
    assert _row(db, rid)["state"] == "RELEASED"


# ---------------------------------------------------------------------------
# venues that misbehave
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_venue_that_refuses_leaves_the_row_sweepable(
    db, an_owner, a_pool  # noqa: F811
):
    """``destroyed=False`` means *unknown*, and unknown must never close a
    row. Nothing in this repository tested this path before; it is the shape
    in which a machine keeps billing while the row says it is finished."""
    venue = _Venue(refuse_destroy=True)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle=venue.rent(),
    )
    touched = await reconcile_rented(db, {"fake": venue}, settle_after_s=SETTLED)

    assert touched == []
    row = _row(db, rid)
    assert row["state"] == "ACTIVE"
    assert row["released_at"] is None
    assert row["failure_code"] == "RECONCILE_NOT_DESTROYED"
    # The note names the machine that is still running. A row that says only
    # "failed" sends somebody to the venue's console with nothing to search.
    assert row["provider_handle"] in row["failure_detail"]
    assert len(venue.live_handles()) == 1

    # ...and the next sweep, once the venue is healthy again, finishes it.
    venue.refuse_destroy = False
    assert await reconcile_rented(
        db, {"fake": venue}, settle_after_s=SETTLED
    ) == [rid]
    assert _row(db, rid)["state"] == "RELEASED"


@pytest.mark.asyncio
async def test_a_venue_that_raises_is_recorded_not_swallowed(
    db, an_owner, a_pool  # noqa: F811
):
    """An exception from the venue is the same fact as a refusal — we do not
    know that the machine is gone — and it is recorded the same way."""
    venue = _Venue(release_raises=True, observe_raises=True)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle=venue.rent(),
    )
    assert await reconcile_rented(
        db, {"fake": venue}, settle_after_s=SETTLED
    ) == []
    row = _row(db, rid)
    assert row["state"] == "ACTIVE"
    assert row["failure_code"] == "RECONCILE_NOT_DESTROYED"
    assert "RuntimeError" in row["failure_detail"]


@pytest.mark.asyncio
async def test_a_machine_the_venue_no_longer_has_counts_as_released(
    db, an_owner, a_pool  # noqa: F811
):
    """``observe`` reads the VENUE, never our own rows.

    A release call that will not confirm anything is not the end of the
    question: if the venue says the handle does not exist, the machine is
    gone and the row may be closed on that evidence — which is what makes a
    release of an already-destroyed machine a success rather than a row stuck
    for ever.
    """
    venue = _Venue(refuse_destroy=True)
    gone = "fake-already-destroyed"  # never added to the venue's live set
    rid = _insert_row(db, owner_id=an_owner, pool_id=a_pool, handle=gone)

    assert await release_capacity(db, venue, rented_id=rid) is True
    assert venue.release_calls == [gone]
    row = _row(db, rid)
    assert row["state"] == "RELEASED"
    assert row["released_at"] is not None


@pytest.mark.asyncio
async def test_a_machine_that_merely_stopped_is_not_released(
    db, an_owner, a_pool  # noqa: F811
):
    """``exists=False`` is the only evidence that counts.

    A stopped instance still exists, and at most venues still bills for its
    disk. Reading ``running=False`` as released would close rows over machines
    that are still on the invoice.
    """

    @dataclass
    class _Stopped(_Venue):
        async def observe(self, *, handle: str) -> ProviderState:
            return ProviderState(exists=True, running=False, detail="stopped")

    venue = _Stopped(refuse_destroy=True)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle=venue.rent(),
    )
    assert await release_capacity(db, venue, rented_id=rid) is False
    assert _row(db, rid)["state"] == "ACTIVE"


# ---------------------------------------------------------------------------
# the sweep itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_venue_with_no_configured_provider_is_left_visible(
    db, an_owner, a_pool  # noqa: F811
):
    """A row we cannot sweep is left exactly as it is. A stuck row is
    visible; a closed one is not, and closing it would be a claim about a
    venue nothing in this process can even talk to."""
    venue = _Venue()
    orphan = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, venue_id="a-venue-we-dropped",
        handle="somebody-elses-handle",
    )
    mine = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle=venue.rent(),
    )
    touched = await reconcile_rented(db, {"fake": venue}, settle_after_s=SETTLED)

    assert touched == [mine]  # the configured venue is still swept
    row = _row(db, orphan)
    assert row["state"] == "ACTIVE"
    assert row["released_at"] is None
    assert orphan in {
        str(r["id"]) for r in unreleased_rows(db, settle_after_s=SETTLED)
    }


@pytest.mark.asyncio
async def test_one_rows_failure_does_not_end_the_sweep(
    db, an_owner, a_pool, monkeypatch  # noqa: F811
):
    """A single bad row must not stop the money from stopping everywhere
    else. The failure is injected below the venue — a raising provider is
    already handled inside ``release_capacity`` — so this exercises the
    sweep's own guard, the one that decides whether row two ever gets looked
    at."""
    venue = _Venue()
    doomed = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle=venue.rent(), age_s=OLD,
    )
    healthy = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle=venue.rent(),
        age_s=OLD / 2,
    )
    real = reconcile_mod.release_capacity

    async def _explode(conn, provider, *, rented_id):
        if rented_id == doomed:
            raise RuntimeError("the connection went away mid-release")
        return await real(conn, provider, rented_id=rented_id)

    monkeypatch.setattr(reconcile_mod, "release_capacity", _explode)
    touched = await reconcile_rented(db, {"fake": venue}, settle_after_s=SETTLED)

    # Oldest first, so the exploding row is the one the sweep meets first.
    assert touched == [healthy]
    assert _row(db, doomed)["state"] == "ACTIVE"
    assert _row(db, healthy)["state"] == "RELEASED"


@pytest.mark.asyncio
async def test_releasing_a_row_that_does_not_exist_is_false_not_an_error(
    db,  # noqa: F811
):
    """Rows cascade away with the account, and a sweep may be holding a list
    that is a few seconds old. That is not a reason to raise into a loop whose
    death removes the only backstop there is."""
    assert await release_capacity(
        db, _Venue(), rented_id=str(uuid.uuid4())
    ) is False


@pytest.mark.asyncio
async def test_a_failed_row_keeps_its_reason_when_its_machine_is_destroyed(
    db, an_owner, a_pool  # noqa: F811
):
    """``release_capacity`` is also reachable from the settle path, and a
    FAILED row may still name a live machine. Destroying it must stamp
    ``released_at`` — the money stopped — without relabelling the row: why an
    acquisition failed is worth more than restating that it is over, and
    either way the row is already outside the sweep."""
    venue = _Venue()
    handle = venue.rent()
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, state="FAILED", handle=handle,
    )
    assert await release_capacity(db, venue, rented_id=rid) is True
    row = _row(db, rid)
    assert row["state"] == "FAILED"
    assert row["released_at"] is not None
    assert venue.live_handles() == []


def test_the_window_is_left_clean_for_the_next_file(db):  # noqa: F811
    """The guard on the guard.

    Asserted as a number rather than as "an acquisition still succeeds": this
    file leaks far less than the $10 window ceiling, so the sibling files'
    version of this test could not fail here even with every row left behind.
    Zero is the only assertion that actually checks anything — and if it fails
    because an EARLIER file leaked, that is worth knowing too, since the
    failure it would otherwise cause lands somewhere else entirely.
    """
    assert window_spend_usd(db, hours=24.0) == 0.0
