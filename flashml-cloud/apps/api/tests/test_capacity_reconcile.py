"""Teardown is the guarantee. The request path is best effort.

Every test here is really one of two questions.

*Can a machine we are paying for end up in a state nothing will ever look at
again?* The answer has to be no, and the interesting cases are all the ones
where something went wrong — the venue refused, the venue raised, the venue is
not configured, or the row never learned the handle in the first place.

*Can this sweep destroy a machine somebody is using?* The answer has to be no
as well, and it is the newer half of the file. An age-only sweep is a hard
maximum rental lifetime, because nothing in the repository calls
``release_capacity`` yet and so no row ever leaves ACTIVE by settling
normally. ``test_a_heartbeating_machine_is_never_swept_however_old_the_rental``
is the test that stands between this module and a training job three hours in.

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

import logging
import uuid
from dataclasses import dataclass, field

import pytest

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import sandbox_identity
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
    NO_HANDLE,
    NOT_DESTROYED,
    VENUE_SAYS_GONE,
    finished_rentals_with_live_credentials,
    reconcile_rented,
    release_capacity,
    teardown_plan,
    unreleased_rows,
)
from test_jobs_from_repo import db  # noqa: F401 - fixture

OLD = 10800.0    # 3h: past every default window
QUIET = 1200.0   # 20m of silence: past the 15m quiet window
YOUNG = 60.0     # a minute: inside every window


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

        Used by the tests that build a row directly instead of acquiring one.
        Several real acquisitions into one pool are legal since D6 (see
        ``test_capacity_acquire.py::test_two_rentals_can_share_one_pool``);
        what this still buys is a row in any state, at any age, with any
        machine — or none — without a venue conversation to arrange it.
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
        enrolment_url="http://api.example", quoted_usd_per_hour=0.5,
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


def _lifecycle(db, machine_id):  # noqa: F811
    with db.cursor() as cur:
        cur.execute(
            "select lifecycle from public.machines where id = %s", (machine_id,)
        )
        row = cur.fetchone()
    return row["lifecycle"] if row else None


def _machine(
    db,  # noqa: F811
    owner_id,
    *,
    status="pending",
    last_seen_s=None,
    pool_id=None,
):
    """A machine row standing in for a rented host.

    ``last_seen_s`` is seconds ago, or ``None`` for a host that has never
    heartbeated — which is what a real one looks like until it has booted,
    pulled its image and enrolled.
    """
    machine_id = str(
        dbmod.insert_machine(
            db, owner_id=str(owner_id), node_id=f"rented-{uuid.uuid4()}",
            name="a rented host", platform="linux",
        )
    )
    if pool_id is not None:
        dbmod.bind_machine_pool(
            db, machine_id=machine_id, pool_id=str(pool_id)
        )
    with db.cursor() as cur:
        cur.execute(
            "update public.machines set status = %s where id = %s",
            (status, machine_id),
        )
        if last_seen_s is not None:
            cur.execute(
                "update public.machines set last_seen_at = "
                "now() - make_interval(secs => %s) where id = %s",
                (float(last_seen_s), machine_id),
            )
    return machine_id


def _seen(db, machine_id, seconds_ago):  # noqa: F811
    """Move a machine's heartbeat. ``None`` means it has never been seen."""
    with db.cursor() as cur:
        if seconds_ago is None:
            cur.execute(
                "update public.machines set last_seen_at = null where id = %s",
                (machine_id,),
            )
        else:
            cur.execute(
                "update public.machines set last_seen_at = "
                "now() - make_interval(secs => %s) where id = %s",
                (float(seconds_ago), machine_id),
            )
    db.commit()


def _insert_row(
    db,  # noqa: F811
    *,
    owner_id,
    pool_id,
    venue_id="fake",
    state="ACTIVE",
    handle=None,
    machine_id=None,
    age_s=OLD,
    usd_per_hour=0.5,
    job_id="job-sweep",
):
    """One rented_capacity row, aged, without going through the venue."""
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.rented_capacity
                (venue_id, state, owner_id, pool_id, job_id, provider_handle,
                 machine_id, usd_per_hour, created_at, acquired_at)
            values (%s, %s, %s, %s, %s, %s, %s, %s,
                    now() - make_interval(secs => %s),
                    case when %s = 'REQUESTED' then null
                         else now() - make_interval(secs => %s) end)
            returning id
            """,
            (venue_id, state, str(owner_id), str(pool_id), job_id, handle,
             machine_id, usd_per_hour, float(age_s), state, float(age_s)),
        )
        return str(cur.fetchone()["id"])


def _job(
    db,  # noqa: F811
    owner_id,
    *,
    job_id=None,
    status="SUCCEEDED",
    finished_s=OLD,
):
    """A ``public.jobs`` row: what the sweep reads to learn a job is over.

    ``finished_s`` is seconds ago, or ``None`` for a job with no finish time
    recorded — which is what an unfinished one looks like.
    """
    job_id = job_id or f"job-{uuid.uuid4().hex[:12]}"
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.jobs (id, owner_id, status, finished_at)
            values (%s, %s, %s,
                    case when %s::float8 is null then null
                         else now() - make_interval(secs => %s) end)
            """,
            (job_id, str(owner_id), status,
             None if finished_s is None else float(finished_s),
             None if finished_s is None else float(finished_s)),
        )
    return job_id


def _attempt(
    db,  # noqa: F811
    machine_id,
    *,
    job_id="job-sweep",
    claimed_s,
    resolved=True,
    deadline_s=None,
):
    """One row in the attempt ledger — the record of a lease this machine took.

    ``resolved`` is what the completion hop writes. ``deadline_s`` is seconds
    ago (negative for a deadline still in the future), or ``None`` for an
    attempt whose deadline was never recorded — the pre-0015 shape, which the
    sweep must treat as work in flight for ever.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.attempts
                (lease_id, machine_id, job_id, task_id, claimed_at,
                 resolved_at, outcome, lease_deadline)
            values (%s, %s, %s, 't1',
                    now() - make_interval(secs => %s),
                    case when %s then now() - make_interval(secs => %s)
                         else null end,
                    case when %s then 'accepted' else null end,
                    case when %s::float8 is null then null
                         else now() - make_interval(secs => %s) end)
            """,
            (f"lease-{uuid.uuid4().hex[:12]}", machine_id, job_id,
             float(claimed_s), resolved, float(claimed_s), resolved,
             None if deadline_s is None else float(deadline_s),
             None if deadline_s is None else float(deadline_s)),
        )


def _aged(db, rid, seconds=OLD):  # noqa: F811
    """Push a row's clock back."""
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


def _swept(db, **windows):  # noqa: F811
    return {str(r["id"]) for r in unreleased_rows(db, **windows)}


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
    _aged(db, rid)  # never heartbeated, and long past the boot allowance

    touched = await reconcile_rented(db, {"fake": venue})
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
    touched = await reconcile_rented(db, {"fake": venue})
    assert rid not in touched
    assert len(venue.live_handles()) == 1
    assert venue.release_calls == []
    assert _row(db, rid)["state"] == "ACTIVE"


@pytest.mark.asyncio
async def test_a_released_rental_gives_its_credential_and_its_pool_back(
    db, an_owner, a_pool  # noqa: F811
):
    """Teardown is two things, and the second one is not tidiness.

    A rented machine is minted ``lifecycle = 'leased'`` — deliberately outside
    ``expire_stale_ephemeral_machines``, which would revoke a host still
    pulling its image — so nothing comes for the credential on a timer. Left
    bound, its token still authenticates for a machine we have handed back to a
    third party. **Ending the lease is this call's job and nobody else's**: the
    isolation assertion that used to refuse the next rental into a poisoned
    pool is gone from this path since D6, so the second rental below proves the
    pool is reusable rather than proving the credential died. The credential
    dying is the assertion above it.
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
# liveness: the difference between a sweep and a maximum rental lifetime
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_heartbeating_machine_is_never_swept_however_old_the_rental(
    db, an_owner, a_pool  # noqa: F811
):
    """**The test this module's predicate exists for.**

    Nothing in the repository calls ``release_capacity`` yet, so no row leaves
    ACTIVE by settling normally. A sweep that selected on age alone would
    therefore be a hard maximum rental lifetime, and the first loop to run it
    would destroy a machine three hours into a training run and heartbeating
    perfectly.

    Same row, twice. Only the heartbeat changes.

    THE ATTEMPT ROW IS NEW AND IT IS NOT SCAFFOLDING. It is what "three hours
    into a training run" actually looks like in this database: the machine
    claimed a lease and has not finished it. The cost backstop
    (``IDLE``) reads exactly that, because a heartbeat cannot distinguish a
    machine mid-run from one that finished an hour ago and kept saying hello —
    ``flashnode`` does the second, which is the whole reason liveness alone was
    never a bound on money. Delete the attempt and this row IS swept, correctly:
    a machine past its boot allowance that has never claimed anything and holds
    nothing in flight is being paid for and doing nothing. See
    ``test_a_machine_that_is_never_given_work_does_not_bill_for_ever``.
    """
    venue = _Venue()
    rid = await acquire_for_job(
        db, venue, _Settings(), request=_request(an_owner, a_pool)
    )
    machine_id = str(_row(db, rid)["machine_id"])
    _aged(db, rid)  # three hours in
    _attempt(db, machine_id, claimed_s=OLD, resolved=False, deadline_s=-60.0)

    _seen(db, machine_id, 5.0)  # ...and talking to us five seconds ago
    assert await reconcile_rented(db, {"fake": venue}) == []
    assert venue.release_calls == []
    assert _row(db, rid)["state"] == "ACTIVE"

    _seen(db, machine_id, QUIET)  # the machine goes quiet
    assert await reconcile_rented(db, {"fake": venue}) == [rid]
    assert venue.live_handles() == []


@pytest.mark.asyncio
async def test_a_quiet_machine_is_swept_even_when_the_rental_is_young(
    db, an_owner, a_pool  # noqa: F811
):
    """The quiet window needs no help from the row's age, and must not wait
    for one: a machine that heartbeated and stopped is dead, and the money is
    not. Twenty-five minutes old here — inside both the boot allowance and the
    abandoned one, so age alone would have left it running."""
    venue = _Venue()
    machine_id = _machine(db, an_owner, status="active", last_seen_s=QUIET)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle=venue.rent(),
        machine_id=machine_id, age_s=1500.0,
    )
    assert await reconcile_rented(db, {"fake": venue}) == [rid]
    assert venue.live_handles() == []


@pytest.mark.asyncio
async def test_a_machine_that_has_never_been_seen_keeps_its_boot_grace(
    db, an_owner, a_pool  # noqa: F811
):
    """A rented host has no ``last_seen_at`` until it has booted, pulled a
    multi-gigabyte image and enrolled. Cutting that short destroys machines
    that were about to start working, having already paid for the boot — the
    same hazard ``acquire.py`` records as its reason for not minting rentals
    ``lifecycle = 'ephemeral'``."""
    venue = _Venue()
    machine_id = _machine(db, an_owner, last_seen_s=None)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle=venue.rent(),
        machine_id=machine_id, age_s=1500.0,  # 25 minutes into a slow boot
    )
    assert await reconcile_rented(db, {"fake": venue}) == []
    assert len(venue.live_handles()) == 1

    _aged(db, rid, OLD)  # and now it has plainly never been coming
    assert await reconcile_rented(db, {"fake": venue}) == [rid]


@pytest.mark.asyncio
async def test_a_revoked_credential_is_swept_at_once(
    db, an_owner, a_pool  # noqa: F811
):
    """No allowance at all: a revoked machine can never claim our work again,
    so the rental is waste from that instant.

    It is also what keeps a failed destroy retryable. ``release_capacity``
    revokes even when the venue refuses, so an age-gated branch here would
    push the row we are mid-way through releasing back OUT of the sweep until
    it aged into the window — the one thing a failed destroy must not do.
    """
    venue = _Venue()
    machine_id = _machine(db, an_owner, status="revoked", last_seen_s=5.0)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle=venue.rent(),
        machine_id=machine_id, age_s=YOUNG,
    )
    assert await reconcile_rented(db, {"fake": venue}) == [rid]
    assert venue.live_handles() == []


@pytest.mark.asyncio
async def test_a_refusal_leaves_the_row_visible_to_the_very_next_sweep(
    db, an_owner, a_pool  # noqa: F811
):
    """The interaction between the two halves, pinned end to end: a venue
    refuses, the credential is revoked anyway, and the row is still there on
    the next pass rather than hidden by its own teardown."""
    venue = _Venue(refuse_destroy=True)
    rid = await acquire_for_job(
        db, venue, _Settings(), request=_request(an_owner, a_pool)
    )
    _aged(db, rid)
    assert await reconcile_rented(db, {"fake": venue}) == []
    assert _machine_status(db, str(_row(db, rid)["machine_id"])) == "revoked"

    # No ageing, no waiting: the next pass sees it.
    assert rid in _swept(db)
    venue.refuse_destroy = False
    assert await reconcile_rented(db, {"fake": venue}) == [rid]


def test_the_windows_are_not_a_poll_interval(db, an_owner, a_pool):  # noqa: F811
    """The misreading this signature is shaped to prevent.

    ``settle_after_s`` used to be one knob, read as "seconds after
    settlement", measured from acquisition, sitting beside a docstring line
    about sweeping every few minutes. Someone wiring a loop sets that to 300.
    Here, 300 seconds is passed as every window at once — the worst a confused
    caller could do — and a machine that heartbeated ten seconds ago, holding
    a lease it has not finished, is still not selected.

    What the assertion means changed when the cost backstop landed, and it is
    worth saying which half moved. It used to be "no window governs a live
    machine"; that is no longer true, because a live machine that has finished
    its work is precisely what ``IDLE`` and ``JOB_FINISHED`` are for. What
    survives is the part that was ever load-bearing: **no window, however
    badly misconfigured, destroys a machine that is doing something.** Work in
    flight is not a duration and cannot be shortened by typing a small number
    into a variable.
    """
    machine_id = _machine(db, an_owner, status="active", last_seen_s=10.0)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle="h-live",
        machine_id=machine_id, age_s=OLD,
    )
    _attempt(db, machine_id, claimed_s=OLD, resolved=False, deadline_s=-60.0)
    assert rid not in _swept(
        db, quiet_after_s=300.0, boot_grace_s=300.0, abandoned_after_s=300.0,
        idle_after_s=300.0,
    )


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
            age_s=YOUNG,
        ),
        "requested_fresh": _insert_row(
            db, owner_id=an_owner, pool_id=a_pool, state="REQUESTED",
            age_s=YOUNG,
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
    swept = _swept(db)
    got = {name for name, rid in mine.items() if rid in swept}
    assert got == {"requested_old", "active_old"}


def test_a_requested_row_is_measured_from_created_at(db, an_owner, a_pool):  # noqa: F811
    """A REQUESTED row has no ``acquired_at`` at all — and no machine to ask
    about either, because this test inserts the row directly with
    ``_insert_row`` rather than through ``acquire_for_job``, which since a
    later fix may leave a REQUESTED row with ``machine_id`` bound (see
    ``reconcile.py``'s module docstring). So the window has to fall back to
    ``created_at``, otherwise a null would compare as null and the rows most
    likely to be orphans would be the ones never swept."""
    rid = _insert_row(db, owner_id=an_owner, pool_id=a_pool, state="REQUESTED")
    row = _row(db, rid)
    assert row["acquired_at"] is None
    assert row["machine_id"] is None
    assert rid in _swept(db)


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
    touched = await reconcile_rented(db, {"fake": venue})

    assert rid not in touched
    row = _row(db, rid)
    assert row["state"] == "REQUESTED"
    assert row["released_at"] is None
    assert row["failure_code"] == NO_HANDLE
    assert "provider_handle" in (row["failure_detail"] or "")
    # And it is still sweepable, which is the entire claim.
    assert rid in _swept(db)


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
    assert await reconcile_rented(db, {"fake": venue}) == []

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
    assert await reconcile_rented(db, {"fake": venue}) == [rid]
    assert venue.live_handles() == []
    assert _row(db, rid)["state"] == "RELEASED"


def test_a_note_never_libels_a_row_that_moved_on(db, an_owner, a_pool):  # noqa: F811
    """The diagnosis is written after the row was read, and the row can change
    in between — a handleless REQUESTED row is exactly the one that is about
    to learn its handle. Stamping ``RECONCILE_NO_HANDLE`` on a healthy rental
    sends somebody to a venue console looking for a machine that is working
    fine, so the guard is part of the write."""
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle="learned-it-just-now",
    )
    reconcile_mod._note(
        db, rid, NO_HANDLE, "stale diagnosis",
        guard="and provider_handle is null",
    )
    assert _row(db, rid)["failure_code"] is None


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
    touched = await reconcile_rented(db, {"fake": venue})

    assert touched == []
    row = _row(db, rid)
    assert row["state"] == "ACTIVE"
    assert row["released_at"] is None
    assert row["failure_code"] == NOT_DESTROYED
    # The note names the machine that is still running. A row that says only
    # "failed" sends somebody to the venue's console with nothing to search.
    assert row["provider_handle"] in row["failure_detail"]
    assert len(venue.live_handles()) == 1

    # ...and the next sweep, once the venue is healthy again, finishes it.
    venue.refuse_destroy = False
    assert await reconcile_rented(db, {"fake": venue}) == [rid]
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
    assert await reconcile_rented(db, {"fake": venue}) == []
    row = _row(db, rid)
    assert row["state"] == "ACTIVE"
    assert row["failure_code"] == NOT_DESTROYED
    assert "RuntimeError" in row["failure_detail"]


@pytest.mark.asyncio
async def test_one_absent_reading_is_not_enough_to_close_a_row(
    db, an_owner, a_pool  # noqa: F811
):
    """A single ``exists=False`` is not proof.

    A transient 404, a read against a replica that has not caught up, an id
    looked up in the wrong region — and the cost of being wrong is the worst
    outcome this module has: a machine billing for ever behind a RELEASED row
    that nothing will ever select again. So the first absent reading is
    written down, not acted on.
    """
    venue = _Venue(refuse_destroy=True)
    gone = "fake-already-destroyed"  # never added to the venue's live set
    rid = _insert_row(db, owner_id=an_owner, pool_id=a_pool, handle=gone)

    assert await release_capacity(db, venue, rented_id=rid) is False
    row = _row(db, rid)
    assert row["state"] == "ACTIVE"
    assert row["released_at"] is None
    assert row["failure_code"] == VENUE_SAYS_GONE
    assert rid in _swept(db)


@pytest.mark.asyncio
async def test_two_sweeps_agreeing_the_machine_is_gone_close_the_row(
    db, an_owner, a_pool  # noqa: F811
):
    """``observe`` reads the VENUE, never our own rows — and corroboration is
    what makes an absent reading evidence rather than a guess.

    Two passes, minutes apart in production, each through a fresh call. The
    row itself is the memory, so nothing lives in the process and a restart
    loses only time. This is also what makes releasing an already-destroyed
    machine terminate instead of retrying for ever.
    """
    venue = _Venue(refuse_destroy=True)
    gone = "fake-already-destroyed"
    rid = _insert_row(db, owner_id=an_owner, pool_id=a_pool, handle=gone)

    await release_capacity(db, venue, rented_id=rid)  # pass one: noted
    assert await release_capacity(db, venue, rented_id=rid) is True

    row = _row(db, rid)
    assert row["state"] == "RELEASED"
    assert row["released_at"] is not None
    assert venue.release_calls == [gone, gone]


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
    # Twice, so that this is a statement about the observation and not about
    # corroboration: neither pass may close the row.
    assert await release_capacity(db, venue, rented_id=rid) is False
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
    touched = await reconcile_rented(db, {"fake": venue})

    assert touched == [mine]  # the configured venue is still swept
    row = _row(db, orphan)
    assert row["state"] == "ACTIVE"
    assert row["released_at"] is None
    assert orphan in _swept(db)


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
    touched = await reconcile_rented(db, {"fake": venue})

    # Oldest first, so the exploding row is the one the sweep meets first.
    assert touched == [healthy]
    assert _row(db, doomed)["state"] == "ACTIVE"
    assert _row(db, healthy)["state"] == "RELEASED"


@pytest.mark.asyncio
async def test_a_failed_listing_does_not_end_the_sweep(
    db, an_owner, a_pool, monkeypatch  # noqa: F811
):
    """The listing is outside every per-row guard, so it was the one thing
    that could still take the whole pass down — and the credential half with
    it, which never touches a venue at all and had no reason to fail."""
    machine_id = _machine(db, an_owner, status="active", pool_id=a_pool)
    _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, state="RELEASED",
        handle="h-done", machine_id=machine_id,
    )

    def _explode(*_args, **_kwargs):
        raise RuntimeError("the connection went away mid-listing")

    monkeypatch.setattr(reconcile_mod, "unreleased_rows", _explode)
    assert await reconcile_rented(db, {"fake": _Venue()}) == []

    assert _machine_status(db, machine_id) == "revoked"


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


# ---------------------------------------------------------------------------
# the credential half, which fails on its own and is retried on its own
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_revoke_that_failed_is_retried_by_a_later_sweep(
    db, an_owner, a_pool, monkeypatch  # noqa: F811
):
    """The hole a best-effort revoke leaves, and the sweep that closes it.

    A RELEASED row is out of ``unreleased_rows`` for good, and rentals are
    minted ``leased``, which no heartbeat sweep in this schema selects, so
    nothing else comes for the machine either. Without this second sweep, one
    failed revoke leaves a live token bound to a live workspace for ever.

    Until D6 that had a loud consequence — the next rental into the pool was
    refused by an isolation assertion — and the noise was doing some of the
    work. It no longer is; see
    ``test_a_stale_lease_is_silent_now_and_the_sweep_is_what_ends_it``.
    """
    venue = _Venue()
    rid = await acquire_for_job(
        db, venue, _Settings(), request=_request(an_owner, a_pool)
    )
    machine_id = str(_row(db, rid)["machine_id"])

    def _explode(*_args, **_kwargs):
        raise RuntimeError("the connection went away mid-revoke")

    monkeypatch.setattr(sandbox_identity, "revoke_sandbox_machine", _explode)
    assert await release_capacity(db, venue, rented_id=rid) is True
    # The money stopped; the credential did not.
    assert _row(db, rid)["state"] == "RELEASED"
    assert _machine_status(db, machine_id) != "revoked"
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == [machine_id]
    assert rid in {
        str(r["id"]) for r in finished_rentals_with_live_credentials(db)
    }

    monkeypatch.undo()
    await reconcile_rented(db, {"fake": venue})
    assert _machine_status(db, machine_id) == "revoked"
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == []


@pytest.mark.asyncio
async def test_releasing_an_already_released_row_still_revokes(
    db, an_owner, a_pool  # noqa: F811
):
    """Idempotent on BOTH halves. The early return for a RELEASED row used to
    skip the credential entirely, which made the one call that mattered the
    only call there would ever be."""
    venue = _Venue()
    machine_id = _machine(db, an_owner, status="active", pool_id=a_pool)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, state="RELEASED",
        handle="h-done", machine_id=machine_id,
    )
    assert await release_capacity(db, venue, rented_id=rid) is True
    assert _machine_status(db, machine_id) == "revoked"
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == []
    # ...and the venue was never asked about a rental that is already over.
    assert venue.release_calls == []


@pytest.mark.asyncio
async def test_a_stale_lease_is_silent_now_and_the_sweep_is_what_ends_it(
    db, an_owner, a_pool, monkeypatch  # noqa: F811
):
    """**What D6 removed, and what has to carry the weight instead.**

    Before D6 a credential this module failed to revoke announced itself: the
    leftover ``machine_pools`` row made the next rental into that pool fail
    ``assert_pool_isolated``. Renting into an occupied pool is now the whole
    point of the feature, so that refusal is gone — the next rental succeeds,
    and the stale lease sits there, valid, bound to a live workspace, for
    hardware a third party has since rented.

    Nothing else is watching it: ``leased`` is outside
    ``expire_stale_ephemeral_machines`` on purpose, and a RELEASED row is
    outside ``unreleased_rows`` for good. So this test asserts both halves —
    that the silence is real, and that
    ``finished_rentals_with_live_credentials`` still ends the lease anyway.
    """
    venue = _Venue()
    rid = await acquire_for_job(
        db, venue, _Settings(), request=_request(an_owner, a_pool)
    )
    machine_id = str(_row(db, rid)["machine_id"])

    def _explode(*_args, **_kwargs):
        raise RuntimeError("the connection went away mid-revoke")

    monkeypatch.setattr(sandbox_identity, "revoke_sandbox_machine", _explode)
    assert await release_capacity(db, venue, rented_id=rid) is True
    monkeypatch.undo()

    # 1. The silence. The machine is gone from the venue and its token still
    #    works, and nothing about the NEXT rental notices.
    assert _machine_status(db, machine_id) != "revoked"
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == [machine_id]
    nxt = await acquire_for_job(
        db, venue, _Settings(),
        request=_request(an_owner, a_pool, job="job-after-a-stale-lease"),
    )
    assert _row(db, nxt)["state"] == "ACTIVE"

    # 2. ...and no timer will end it either: the machine is `leased`, which
    #    `expire_stale_ephemeral_machines` deliberately does not select. That
    #    sweep is global and this file must not call it (see the header on
    #    leaving the database clean); the guarantee is asserted directly in
    #    `test_sandbox_identity.py::test_no_heartbeat_timer_ever_expires_a_lease`.
    assert _lifecycle(db, machine_id) == "leased"

    # 3. The one thing that does come back for it. Note the live rental from
    #    step 1 is NOT in this list — its lease is current.
    stale = {str(r["machine_id"]) for r in
             finished_rentals_with_live_credentials(db)}
    assert machine_id in stale
    assert str(_row(db, nxt)["machine_id"]) not in stale

    await reconcile_rented(db, {"fake": venue})
    assert _machine_status(db, machine_id) == "revoked"
    assert dbmod.pool_ids_bound_to_machine(db, machine_id) == []
    # The live rental kept its lease and its binding.
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == [
        str(_row(db, nxt)["machine_id"])
    ]


def test_a_bound_pool_alone_is_enough_to_come_back_for(
    db, an_owner, a_pool  # noqa: F811
):
    """``revoke_sandbox_machine`` unbinds on every call, including ones where
    the row is already revoked — the half-done state a crashed revoke leaves.
    A query that only looked at ``status`` would walk past exactly that."""
    machine_id = _machine(db, an_owner, status="revoked", pool_id=a_pool)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, state="RELEASED",
        handle="h-done", machine_id=machine_id,
    )
    assert rid in {
        str(r["id"]) for r in finished_rentals_with_live_credentials(db)
    }


# ---------------------------------------------------------------------------
# the COST backstop: stopping a rental that WORKED
#
# Everything above this line stops a rental that broke. A rental that boots,
# runs its job and finishes is invisible to all of it — flashnode keeps
# heartbeating after the work is done, so the machine looks alive for ever and
# bills for ever. These are the branches that stop it, and the tests that keep
# them from stopping a machine that is still working.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rental_whose_job_is_over_is_swept_though_it_is_heartbeating(
    db, an_owner, a_pool  # noqa: F811
):
    """**The test the cost backstop exists for.**

    Same shape as ``test_a_heartbeating_machine_is_never_swept_however_old_the
    _rental`` and the opposite answer, because one fact changed: the job is
    over. Liveness says this machine is fine — it is, it is heartbeating — and
    liveness is exactly the wrong question once there is no work left.
    """
    venue = _Venue()
    machine_id = _machine(db, an_owner, status="active", last_seen_s=5.0)
    job_id = _job(db, an_owner, status="RUNNING", finished_s=None)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle=venue.rent(),
        machine_id=machine_id, age_s=YOUNG, job_id=job_id,
    )
    assert await reconcile_rented(db, {"fake": venue}) == []

    with db.cursor() as cur:
        cur.execute(
            "update public.jobs set status = 'SUCCEEDED', "
            "finished_at = now() - make_interval(secs => %s) where id = %s",
            (OLD, job_id),
        )
    assert await reconcile_rented(db, {"fake": venue}) == [rid]
    assert venue.live_handles() == []


@pytest.mark.asyncio
async def test_a_finished_job_never_sweeps_a_machine_holding_a_live_lease(
    db, an_owner, a_pool  # noqa: F811
):
    """**The reproduction that blocked arming.**

    ``JOB_FINISHED`` had no in-flight guard at all: the guard was written
    inside ``IDLE`` only, so a rental whose own job succeeded an hour ago, on a
    machine holding an unresolved attempt for a DIFFERENT job with ten minutes
    left on its lease, was selected — and armed, destroyed mid-task.

    A rented machine sits in the submitter's own pool and is an eligible
    claimant for that pool's OTHER jobs; the runtime is pull-based, so nothing
    about "the job we rented it for" bounds what the machine is running now.
    The test is one expression read by both cost branches, which is the only
    shape in which it cannot go missing from one of them again.
    """
    venue = _Venue()
    machine_id = _machine(db, an_owner, status="active", last_seen_s=5.0)
    over = _job(db, an_owner, status="SUCCEEDED", finished_s=3600.0)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle=venue.rent(),
        machine_id=machine_id, age_s=OLD, job_id=over,
    )
    _attempt(db, machine_id, job_id="job-somebody-elses", claimed_s=300.0,
             resolved=False, deadline_s=-600.0)

    assert rid not in _swept(db)
    assert await reconcile_rented(db, {"fake": venue}) == []
    assert venue.release_calls == []
    assert len(venue.live_handles()) == 1

    # ...and when that lease is finished, the rental is swept — correctly.
    with db.cursor() as cur:
        cur.execute(
            "update public.attempts set resolved_at = now(), "
            "outcome = 'accepted' where machine_id = %s", (machine_id,)
        )
    db.commit()
    assert await reconcile_rented(db, {"fake": venue}) == [rid]
    assert venue.live_handles() == []


def test_a_machine_between_two_tasks_of_a_running_job_is_not_idle(
    db, an_owner, a_pool  # noqa: F811
):
    """**The second reproduction that blocked arming.**

    The in-flight test protects a machine HOLDING a lease. A machine waiting
    for its next one holds nothing at all, and waiting longer than
    ``idle_after_s`` is ordinary: federated aggregation between rounds, a long
    checkpoint upload, a straggler barrier, a coordinator with nothing queued
    this instant. One attempt claimed twenty minutes ago and resolved, with the
    job still ``RUNNING``, used to read as IDLE — a machine destroyed between
    two tasks of a job that was still going.
    """
    machine_id = _machine(db, an_owner, status="active", last_seen_s=5.0)
    job_id = _job(db, an_owner, status="RUNNING", finished_s=None)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle="h-between-tasks",
        machine_id=machine_id, age_s=OLD, job_id=job_id,
    )
    _attempt(db, machine_id, job_id=job_id, claimed_s=1200.0, resolved=True,
             deadline_s=1140.0)
    assert rid not in _swept(db)

    # The same row, once the job's own state says it stopped. Note this is
    # still IDLE and not JOB_FINISHED: the job finished a minute ago, inside
    # the grace that branch measures, while the machine has been empty for
    # twenty.
    with db.cursor() as cur:
        cur.execute(
            "update public.jobs set status = 'SUCCEEDED', finished_at = "
            "now() - make_interval(secs => %s) where id = %s", (60.0, job_id)
        )
    db.commit()
    assert {str(r["id"]): r["sweep_reason"] for r in unreleased_rows(db)}.get(
        rid
    ) == "IDLE"


def test_the_price_of_that_guard_is_a_finished_job_nobody_observed(
    db, an_owner, a_pool  # noqa: F811
):
    """**The cost of the test above, pinned so nobody pays it by accident.**

    ``jobs.status`` is a cache written only when somebody looks
    (``db.sync_observed_job_states``), and routing around exactly that
    staleness is why ``IDLE`` was invented. Consulting it costs that
    independence back: a job that finished at 3am with nobody watching reads
    ``RUNNING`` for ever, and its rental is now caught by neither cost branch.

    That is a bounded, logged bill weighed against a silent, irreversible
    destruction of somebody's running job, and it was taken deliberately. It is
    a test rather than a comment because the next reader's instinct will be to
    delete the guard, and this says what deleting it buys and what it costs.

    The liveness branches still work, which is the floor under the trade: the
    moment the machine stops talking, the rental goes.
    """
    machine_id = _machine(db, an_owner, status="active", last_seen_s=5.0)
    # Terminal at the coordinator hours ago; nobody ever opened the page, so
    # this column has never been written.
    job_id = _job(db, an_owner, status="RUNNING", finished_s=None)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle="h-unobserved",
        machine_id=machine_id, age_s=OLD, job_id=job_id,
    )
    _attempt(db, machine_id, job_id=job_id, claimed_s=OLD, resolved=True,
             deadline_s=OLD)
    assert rid not in _swept(db)

    _seen(db, machine_id, QUIET)
    assert rid in _swept(db)


def test_a_job_that_has_only_just_finished_keeps_the_idle_grace(
    db, an_owner, a_pool  # noqa: F811
):
    """``jobs.status`` is written by an OBSERVATION — a console poll landing on
    ``db.sync_observed_job_states`` — not by the coordinator calling us. A
    rental destroyed the instant that column changes is a rental whose fate
    turns on one write being right, so the same grace applies as to idleness.
    """
    machine_id = _machine(db, an_owner, status="active", last_seen_s=5.0)
    job_id = _job(db, an_owner, status="SUCCEEDED", finished_s=60.0)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle="h-fresh-finish",
        machine_id=machine_id, age_s=YOUNG, job_id=job_id,
    )
    assert rid not in _swept(db)
    assert rid in _swept(db, idle_after_s=30.0)


@pytest.mark.asyncio
async def test_a_machine_that_stopped_claiming_work_is_swept_though_it_beats(
    db, an_owner, a_pool  # noqa: F811
):
    """The input that needs NOBODY to have opened a console.

    ``jobs.status`` only goes terminal when someone loads a page. This branch
    reads the machine's own traffic through this API instead: it claimed, it
    finished, and it has asked for nothing since.
    """
    venue = _Venue()
    machine_id = _machine(db, an_owner, status="active", last_seen_s=5.0)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle=venue.rent(),
        machine_id=machine_id, age_s=OLD,
    )
    # Claimed twenty minutes ago and resolved: nothing in flight, nothing new.
    _attempt(db, machine_id, claimed_s=1200.0, resolved=True, deadline_s=1140.0)
    assert await reconcile_rented(db, {"fake": venue}) == [rid]
    assert venue.live_handles() == []


def test_a_machine_running_one_long_task_is_never_idle(
    db, an_owner, a_pool  # noqa: F811
):
    """**The test that stands between this branch and a training job.**

    A three-hour task is ONE claim followed by three hours of silence, so "has
    claimed nothing for fifteen minutes" describes a machine hard at work just
    as well as it describes an empty one. The unresolved attempt is what tells
    them apart, and its deadline — refreshed by every heartbeat the
    coordinator grants — is what says the attempt is still real.
    """
    machine_id = _machine(db, an_owner, status="active", last_seen_s=5.0)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle="h-working",
        machine_id=machine_id, age_s=OLD,
    )
    # Claimed three hours ago, still unresolved, deadline a minute from now.
    _attempt(db, machine_id, claimed_s=OLD, resolved=False, deadline_s=-60.0)
    assert rid not in _swept(db)

    # Even at a minute's idleness, and even with the job's own row finished:
    # work in flight outranks both.
    assert rid not in _swept(db, idle_after_s=60.0)


def test_an_attempt_with_no_deadline_is_work_in_flight_but_not_for_ever(
    db, an_owner, a_pool  # noqa: F811
):
    """``lease_deadline`` is null for an attempt claimed before migration 0015
    or one whose claim response could not be parsed. ``reconcile_expired_
    attempts`` refuses to resolve such a row for the same reason this refuses
    to sweep it: "we never learned when this lease ends" is not "it ended".

    **And "it never ends" is not the answer either**, which is the half this
    test grew. Read as eternal, one null-deadline attempt made its rental match
    NO branch in this query, at any ``idle_after_s`` down to a second — and a
    job nobody polls never goes terminal to catch it either, so nothing in the
    system could stop that machine billing. The cautious reading arrived at the
    same place as the careless one.

    The bound is ``DEFAULT_UNKNOWN_DEADLINE_MAX_S`` from ``claimed_at``, and it
    is an argument about evidence: every accepted heartbeat writes the deadline
    (``db.note_attempt_deadline``) and the agent renews at a third of the lease
    window, so hours of silence on that column is not a long task, it is a
    record we never managed to write.
    """
    machine_id = _machine(db, an_owner, status="active", last_seen_s=5.0)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle="h-unknown-deadline",
        machine_id=machine_id, age_s=OLD,
    )
    _attempt(db, machine_id, claimed_s=OLD, resolved=False, deadline_s=None)
    assert rid not in _swept(db)
    # Not even at a one-second idle window: unknown outranks idleness.
    assert rid not in _swept(db, idle_after_s=1.0)

    with db.cursor() as cur:
        cur.execute(
            "update public.attempts set claimed_at = "
            "now() - make_interval(secs => %s) where machine_id = %s",
            (reconcile_mod.DEFAULT_UNKNOWN_DEADLINE_MAX_S + 60.0, machine_id),
        )
    db.commit()
    assert rid in _swept(db)


def test_a_deadline_long_past_is_not_evidence_of_work(
    db, an_owner, a_pool  # noqa: F811
):
    """The other side of the same test. An unresolved attempt whose deadline
    passed hours ago is a machine that vanished mid-task, not one that is busy
    — the coordinator refuses its heartbeats and rejects its commits. The
    grace allowed is ``db.EXPIRY_GRACE_SECONDS``, the same figure the attempt
    ledger uses to call that attempt expired, so the two cannot disagree."""
    machine_id = _machine(db, an_owner, status="active", last_seen_s=5.0)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle="h-dead-lease",
        machine_id=machine_id, age_s=OLD,
    )
    _attempt(db, machine_id, claimed_s=OLD, resolved=False, deadline_s=OLD / 2)
    assert rid in _swept(db)


def test_a_machine_still_booting_is_never_idle(
    db, an_owner, a_pool  # noqa: F811
):
    """A rented host that has claimed nothing because it CANNOT yet — it is
    still pulling a multi-gigabyte image — must not be destroyed at
    ``idle_after_s``. That is the boot hazard ``boot_grace_s`` exists for, and
    the idle branch would have walked straight into it: no attempts, nothing
    in flight, nothing claimed lately, all true of a machine twenty minutes
    into a healthy boot."""
    machine_id = _machine(db, an_owner, status="active", last_seen_s=5.0)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle="h-booting",
        machine_id=machine_id, age_s=1200.0,  # 20 minutes in
    )
    assert rid not in _swept(db)


def test_a_machine_that_is_never_given_work_does_not_bill_for_ever(
    db, an_owner, a_pool  # noqa: F811
):
    """The other half of that pair, and a hole that was open until the idle
    branch grew its second disjunct: a machine that boots, enrols and
    heartbeats but is never handed a single lease matches NOTHING else here.
    Not quiet (it is talking), not never-seen (it has been), not abandoned (it
    has a machine row), and its job may never go terminal. Past the boot
    allowance, having done nothing at all, it is waste."""
    machine_id = _machine(db, an_owner, status="active", last_seen_s=5.0)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle="h-never-worked",
        machine_id=machine_id, age_s=OLD,  # past boot_grace_s
    )
    assert rid in _swept(db)


def test_the_reason_a_row_was_selected_travels_with_it(
    db, an_owner, a_pool  # noqa: F811
):
    """``sweep_reason`` is what a log-only deployment reports, so it has to be
    the same expression that does the selecting rather than a second guess
    about it. Ordered branches, so each row carries the strongest true reason:
    a machine that never booted says so rather than reporting as idle."""
    quiet_m = _machine(db, an_owner, status="active", last_seen_s=QUIET)
    unseen_m = _machine(db, an_owner, last_seen_s=None)
    revoked_m = _machine(db, an_owner, status="revoked", last_seen_s=5.0)
    idle_m = _machine(db, an_owner, status="active", last_seen_s=5.0)
    done_m = _machine(db, an_owner, status="active", last_seen_s=5.0)
    job_id = _job(db, an_owner, status="FAILED", finished_s=OLD)

    rows = {
        "QUIET": _insert_row(db, owner_id=an_owner, pool_id=a_pool,
                             machine_id=quiet_m, handle="h1"),
        "NEVER_SEEN": _insert_row(db, owner_id=an_owner, pool_id=a_pool,
                                  machine_id=unseen_m, handle="h2"),
        "REVOKED_CREDENTIAL": _insert_row(db, owner_id=an_owner,
                                          pool_id=a_pool,
                                          machine_id=revoked_m, handle="h3"),
        "ABANDONED": _insert_row(db, owner_id=an_owner, pool_id=a_pool,
                                 handle="h4"),
        "IDLE": _insert_row(db, owner_id=an_owner, pool_id=a_pool,
                            machine_id=idle_m, handle="h5"),
        "JOB_FINISHED": _insert_row(db, owner_id=an_owner, pool_id=a_pool,
                                    machine_id=done_m, handle="h6",
                                    job_id=job_id),
    }
    _attempt(db, idle_m, claimed_s=1200.0, resolved=True, deadline_s=1140.0)

    by_id = {str(r["id"]): r["sweep_reason"] for r in unreleased_rows(db)}
    assert {expected: by_id.get(rid) for expected, rid in rows.items()} == {
        reason: reason for reason in rows
    }


# ---------------------------------------------------------------------------
# log-only: the setting a first deployment ships with
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_dry_run_reports_and_destroys_nothing(
    db, an_owner, a_pool  # noqa: F811
):
    """The failure this whole module fears is silent and irreversible, so the
    deployed sweep starts disarmed. A dry run must touch NOTHING — not the
    venue, not the row, not the credential. A log-only pass that quietly
    revoked identities would not be log-only, and the operator reading the
    report would be reading it after the fact."""
    venue = _Venue()
    machine_id = _machine(db, an_owner, status="active", last_seen_s=QUIET,
                          pool_id=a_pool)
    rid = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle=venue.rent(),
        machine_id=machine_id, age_s=OLD,
    )
    assert await reconcile_rented(db, {"fake": venue}, dry_run=True) == []
    assert venue.release_calls == []
    assert len(venue.live_handles()) == 1
    row = _row(db, rid)
    assert row["state"] == "ACTIVE"
    assert row["failure_code"] is None
    assert _machine_status(db, machine_id) == "active"

    # ...and armed, the same pass does the work.
    assert await reconcile_rented(db, {"fake": venue}) == [rid]
    assert venue.live_handles() == []


@pytest.mark.asyncio
async def test_a_log_only_pass_still_ends_a_finished_rentals_lease(
    db, an_owner, a_pool  # noqa: F811
):
    """**The one thing a dry run does, and why it is not a hole in the gate.**

    ``dry_run`` exists because destroying a machine is irreversible and can
    take a running job with it. Revoking the credential of a rental that is
    ALREADY over destroys nothing at any venue: it touches our own row and our
    own token, both describing hardware we have already handed back. There is
    no irreversible act to withhold and no report to read first.

    Meanwhile it is the only thing in this system that ends a lease at all —
    D6 removed the isolation assertion that used to make a leftover binding
    fail loudly — so behind the gate, the shipped default was a deployment
    where leases never end. The money half is still untouched below, which is
    what makes this a split rather than a weakening.
    """
    venue = _Venue()
    finished_m = _machine(db, an_owner, status="active", pool_id=a_pool)
    _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, state="RELEASED",
        handle="h-done", machine_id=finished_m,
    )
    live_m = _machine(db, an_owner, status="active", last_seen_s=QUIET)
    live = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle=venue.rent(),
        machine_id=live_m, age_s=OLD,
    )

    assert await reconcile_rented(db, {"fake": venue}, dry_run=True) == []

    # The money half: nothing destroyed, nothing annotated, nothing revoked.
    assert venue.release_calls == []
    assert len(venue.live_handles()) == 1
    assert _row(db, live)["state"] == "ACTIVE"
    assert _row(db, live)["failure_code"] is None
    assert _machine_status(db, live_m) == "active"

    # The lease half: over is over, log-only or not.
    assert _machine_status(db, finished_m) == "revoked"
    assert dbmod.machine_ids_bound_to_pool(db, str(a_pool)) == []


@pytest.mark.asyncio
async def test_the_dry_run_headline_counts_what_arming_would_destroy(
    db, an_owner, a_pool, caplog  # noqa: F811
):
    """The number an operator reads before arming the sweep.

    It used to be "would destroy N rental(s)" for N = every row the query
    matched, which over-states it in the direction that matters. An armed pass
    destroys nothing at a venue it has no adapter for, and nothing it cannot
    name — a handleless row is annotated and left, deliberately. With the
    production registry empty, the old headline claimed every row and the true
    answer was zero.
    """
    venue = _Venue()
    reachable_m = _machine(db, an_owner, status="active", last_seen_s=QUIET)
    reachable = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle=venue.rent(),
        machine_id=reachable_m, age_s=OLD,
    )
    handleless = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, state="REQUESTED", handle=None,
    )
    orphan_m = _machine(db, an_owner, status="active", last_seen_s=QUIET)
    orphan = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, venue_id="a-venue-nobody-wired",
        handle="h-unknown", machine_id=orphan_m, age_s=OLD,
    )

    with caplog.at_level(logging.WARNING, logger=reconcile_mod.log.name):
        assert await reconcile_rented(db, {"fake": venue}, dry_run=True) == []

    headline = [r for r in caplog.records if "LOG ONLY" in str(r.msg)][-1]
    selected, would, cannot, would_rows, cannot_rows = headline.args
    assert selected == would + cannot
    assert would == len(would_rows) and cannot == len(cannot_rows)

    by_id = {e["rented_id"] for e in would_rows}
    unreachable = {e["rented_id"] for e in cannot_rows}
    assert reachable in by_id
    # Neither of these is a machine an armed pass could destroy, and counting
    # them was the whole bug.
    assert {handleless, orphan} <= unreachable
    assert not ({handleless, orphan} & by_id)

    # ...and the pass still wrote nothing.
    assert _row(db, handleless)["failure_code"] is None
    assert _row(db, reachable)["state"] == "ACTIVE"


def test_the_plan_says_which_rows_no_configured_venue_can_reach(
    db, an_owner, a_pool  # noqa: F811
):
    """What an operator reads before arming the sweep, and the one distinction
    the log has to make: a row nothing selected is fine, while a row selected
    at a venue with no adapter is money NOTHING in this process can stop. The
    production registry is empty, so today every row is the second kind."""
    machine_id = _machine(db, an_owner, status="active", last_seen_s=QUIET)
    mine = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, handle="h-known",
        machine_id=machine_id, age_s=OLD,
    )
    orphan_m = _machine(db, an_owner, status="active", last_seen_s=QUIET)
    orphan = _insert_row(
        db, owner_id=an_owner, pool_id=a_pool, venue_id="a-venue-nobody-wired",
        handle="h-unknown", machine_id=orphan_m, age_s=OLD,
    )

    plan = {e["rented_id"]: e for e in teardown_plan(db, {"fake": _Venue()})}
    assert plan[mine]["provider_configured"] is True
    assert plan[orphan]["provider_configured"] is False
    assert plan[orphan]["reason"] == "QUIET"
    assert plan[orphan]["provider_handle"] == "h-unknown"

    # Reads only. Naming a row in a report must never move it.
    assert _row(db, orphan)["state"] == "ACTIVE"


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
