"""Destroying what we rented, whether or not anybody remembered to ask.

Release is attempted when a job settles. **Correctness does not depend on
that call happening, or succeeding.** The sweep here is the guarantee: it
finds rentals that are still costing money and nothing is using, destroys
what they name, and does it on a timer for ever. Everything in this module
is written for the case where the request path is already gone.

WHAT MAKES A RENTAL SWEEPABLE, AND WHY IT IS NOT AGE
----------------------------------------------------
**Read this before wiring the loop.** An earlier version of this module
selected on state and age alone. Since nothing in the repository calls
:func:`release_capacity` yet, no row ever leaves ACTIVE by settling normally
-- so an age-only sweep is a *hard maximum rental lifetime*, and the first
loop to call it would destroy every rental older than the window, including a
machine three hours into a training run and heartbeating perfectly.

So the predicate is liveness, not age. ``machines.last_seen_at`` is the
signal — the same column the console renders Online/Offline from — and a
rental whose machine is still heartbeating is never swept, however old it is.
Three cases, mutually exclusive by construction in
:func:`unreleased_rows`, each with its own allowance because they are not the
same kind of doubt:

* **Heartbeating and then quiet** (``quiet_after_s``). The machine spoke and
  stopped. The rental is dead and the money is not, so this needs no help
  from the row's own age: a rental five minutes old whose machine went quiet
  is swept, and a rental five hours old whose machine is talking is not.
* **Never seen at all** (``boot_grace_s``), measured from acquisition. A
  rented host has no ``last_seen_at`` until it has booted, pulled a
  multi-gigabyte image and enrolled. This allowance is deliberately the
  longest of the three: cutting it short destroys machines that were about to
  work, and pays for them anyway. ``acquire.py`` records the same hazard as
  its reason for not minting rentals ``lifecycle = 'ephemeral'``.
* **Nothing to ask** (``abandoned_after_s``), measured from acquisition. No
  machine row is bound at all — which, now that ``acquire.py`` records
  ``machine_id`` on its failure paths too (see ``_record_evidence`` there),
  no longer describes every REQUESTED row: one whose acquisition failed
  after minting a credential carries a bound, revoked machine and is caught
  by the revoked-credential case below instead, with no allowance at all.
  What is left in *this* case is a row whose acquisition never got as far as
  binding a machine, or an ACTIVE row whose machine was since deleted. So
  this window is how long we wait before assuming the process that opened
  the row is not coming back.

A fourth case has no window at all: a bound credential that is already
**revoked** can never claim our work again, so the rental is waste from that
instant and is swept immediately. This is also where a failed acquisition
lands once it has minted and then revoked its credential: a REQUESTED row
with no machine bound is doubt about whether anything was ever created, but
a REQUESTED row with a *revoked* machine bound is not doubt at all.

**None of the three is a poll interval.** The interval belongs to the caller
and is a different order of magnitude: sweep often (minutes, as
``DEFAULT_RECONCILE_INTERVAL_S`` does for sandboxes), but only ever destroy
things these windows say nobody is using. The old name for this,
``settle_after_s``, read as "seconds after settlement" while being measured
from acquisition, which is exactly the misreading that turns a sweep into a
lifetime cap.

None of this constrains the settle path: :func:`release_capacity` called
directly with a ``rented_id`` destroys immediately and asks nothing about
heartbeats, because a caller who has just watched a job finish knows
something the machine's liveness cannot tell us.

TEARDOWN IS TWO THINGS, AND ``cleanup_session`` IS THE MODEL
------------------------------------------------------------
*Both halves, independently.* It kills the sandbox **and** revokes the
credential, in separate try blocks, so neither failure can hide the other.
Teardown here is the same two things: destroy the machine at the venue, and
kill the identity it was renting under. A provider that will not answer must
still leave a legible row behind and must still give the pool back; one row's
failure must not end the sweep for the rows after it; and a failure to
*record* must never be mistaken for a failure to *destroy*.

The credential half is not tidiness. A rented machine is minted with
``lifecycle = 'persistent'``, so ``expire_stale_ephemeral_machines`` — the
sweep that cleans up rental *sessions* — never touches it. Left alone after
release it is two live problems: a valid machine token for a machine that no
longer exists, on hardware we handed back to a third party; and a binding
that makes ``provision_sandbox_machine``'s closing ``assert_pool_isolated``
refuse the NEXT rental into that pool. ``acquire.py`` already revokes on its
failure path for exactly that reason; without the same on the success path,
renting once would poison the pool for good.

Because that half can fail on its own, it is swept on its own too:
:func:`finished_rentals_with_live_credentials` finds rentals that are over
and whose credential is not, and the sweep revokes them. Without it, a revoke
that failed on the one call that mattered would never be retried — the row is
RELEASED and out of :func:`unreleased_rows` for good.

WHY A MISSING HANDLE IS THE DANGEROUS CASE, NOT THE EASY ONE
------------------------------------------------------------
A ``REQUESTED`` row with a null ``provider_handle`` is the window between
"we decided to spend money" and "the venue answered". It is tempting to read
"no handle" as "nothing was created, so nothing is billing, so close the
row". That reasoning is backwards: **absence of a handle is not evidence of
absence of a machine.** It is the one case where something may exist at the
venue that we cannot name.

Closing such a row is not merely optimistic, it is a live race. The sweep
marks the row RELEASED; the acquisition it raced then returns from the venue
with a real handle, loses its compare-and-set against ``state =
'REQUESTED'``, records the handle, and if that release fails, the result is a
RELEASED row carrying a live handle. :func:`unreleased_rows` selects only
``REQUESTED`` and ``ACTIVE``, so nothing would ever look at that row again and
the machine would bill for ever. ``acquire._keep_sweepable`` is the other half
of this agreement: it forces a terminal row back to REQUESTED when its own
outcome is unknown.

So a handleless row is **left exactly where it is**: sweepable, visible, and
re-examined on every pass. That is deliberate, and it means a crashed
acquisition that truly created nothing also stays for ever. A permanently
stuck row is a cheap, visible defect. A silently closed one is an invoice.

:func:`ResourceProvider.observe` is what would settle the question, and it
cannot be asked: it reads the venue by handle, and a handle is the one thing
this row does not have. It *is* used where it can be -- see
:func:`_venue_says_gone` -- to turn a release the provider refused to confirm
into a fact about the venue rather than a guess about our own rows. Finding a
machine we can name nowhere at all is a venue-listing problem (an enumeration
by tag or label), not something this sweep can solve, and it is why the
handle is written to the row *before* anything is destroyed.
"""
from __future__ import annotations

import asyncio
import logging

import psycopg

from flashml_cloud_api import sandbox_identity
from flashml_cloud_api.capacity.provider import ResourceProvider

__all__ = [
    "DEFAULT_ABANDONED_AFTER_S",
    "DEFAULT_BOOT_GRACE_S",
    "DEFAULT_QUIET_AFTER_S",
    "finished_rentals_with_live_credentials",
    "reconcile_rented",
    "release_capacity",
    "unreleased_rows",
]

log = logging.getLogger(__name__)

#: How much of a failure message is kept, matching ``acquire.py``.
#: ``failure_detail`` is for a human reading a row, not for a stack trace.
_DETAIL_MAX = 2000

# THE SWEEPABLE SET, AND WHY IT IS NOT A CONSTANT
# -----------------------------------------------
# ``('REQUESTED', 'ACTIVE')`` are the states that still cost money and that
# the sweep may act on. A row outside that set is either finished
# (``RELEASED``) or already closed with its reason (``FAILED``) -- and
# ``FAILED`` is deliberately not swept, which is precisely why ``acquire.py``
# refuses to write it while a handle may still be live.
#
# There was a `SWEEPABLE = ("REQUESTED", "ACTIVE")` here, and it was deleted.
# It named the invariant and changed nothing. Every site that depends on the
# set carries its own SQL literal -- six of them:
#
#   * `unreleased_rows` and `_mark_released`, here;
#   * `acquire._close_failed`, and `acquire._keep_sweepable` twice (state
#     and `released_at`);
#   * the partial index `rented_capacity_unreleased_idx` in migration 0022.
#
# `finished_rentals_with_live_credentials` then spells the COMPLEMENT,
# ('RELEASED', 'FAILED'), which has to keep agreeing with all six.
#
# Editing the constant moved none of that and left the suite green, which is
# worse than having no constant at all: the next reader edits it, runs the
# tests, and believes the sweep changed.
#
# Routing the statements through it would mean interpolating a Python tuple
# into SQL, exactly what `_note`'s `guard` argument refuses to allow for
# anything but a static fragment -- and it still could not reach the index,
# in another language in another file, or the CHECK constraint beside it. So
# the literals stay inline, this comment is what names the invariant, and
# changing the set means changing all six sites and the complement together.
# The test that tells you if you missed one:
# test_unreleased_rows_selects_only_what_is_still_billing_and_settled.

#: Heartbeat silence that counts as gone. Ten times the 90-second window
#: ``db.MACHINE_ONLINE_PREDICATE`` calls "online", and the same figure
#: ``expire_stale_ephemeral_machines`` defaults to for a rental session that
#: stopped speaking.
DEFAULT_QUIET_AFTER_S = 15 * 60.0

#: How long a machine that has NEVER been seen is given, from acquisition.
#: The longest of the three on purpose: a rented host has to boot, pull a
#: multi-gigabyte image and enrol before it can heartbeat once, and a window
#: shorter than that destroys machines that were about to start working —
#: having already paid for the boot.
DEFAULT_BOOT_GRACE_S = 60 * 60.0

#: How long a rental with no machine to ask about is given, from acquisition:
#: no credential bound, or one already revoked. This is a bound on how long we
#: wait for a process that opened a row to come back, not on a healthy
#: acquisition.
DEFAULT_ABANDONED_AFTER_S = 30 * 60.0

#: Written on a row the sweep could not act on because it names no machine.
NO_HANDLE = "RECONCILE_NO_HANDLE"
#: Written on a row whose machine the venue would not confirm destroying.
NOT_DESTROYED = "RECONCILE_NOT_DESTROYED"
#: Written on the FIRST sweep at which the venue claims the handle is gone.
#: A second, independent sweep saying the same thing is what closes the row;
#: see :func:`release_capacity`.
VENUE_SAYS_GONE = "RECONCILE_VENUE_SAYS_GONE"


def unreleased_rows(
    db: psycopg.Connection,
    *,
    quiet_after_s: float = DEFAULT_QUIET_AFTER_S,
    boot_grace_s: float = DEFAULT_BOOT_GRACE_S,
    abandoned_after_s: float = DEFAULT_ABANDONED_AFTER_S,
) -> list[dict]:
    """Rentals still costing money that nothing is using.

    The ``case`` is the whole design and is written as a case rather than a
    chain of ``or``s so that the three allowances cannot silently overlap:
    each row is judged by exactly one of them, and which one is a fact about
    the machine, not about the row's age. **A machine that has heartbeated
    within ``quiet_after_s`` matches nothing here at all** — that is the
    clause standing between this sweep and a running training job. See the
    module docstring for what each window means and why they differ.

    ``REQUESTED`` is included on purpose: a row that never learned its handle
    may still have created something at the venue. It is also why
    ``acquire.py`` leaves a doubtful row REQUESTED rather than FAILED -- this
    query is the only thing that will ever go looking.

    Oldest first: the rental that has been billing longest is the one worth
    settling first.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select rc.id, rc.venue_id, rc.state, rc.provider_handle,
                   rc.machine_id, m.status as machine_status, m.last_seen_at
              from public.rented_capacity rc
              left join public.machines m on m.id = rc.machine_id
             where rc.state in ('REQUESTED', 'ACTIVE')
               and case
                     -- A revoked credential can never claim our work again,
                     -- so the rental is waste from this instant and gets no
                     -- allowance at all. Without this branch, revoking would
                     -- push a row we are mid-way through releasing back OUT
                     -- of the sweep until it aged into the window below --
                     -- the one thing a failed destroy must not do.
                     when m.id is not null and m.status = 'revoked'
                       then true
                     -- No machine bound at all: acquisition never got as far
                     -- as binding one, or an ACTIVE row whose machine row
                     -- was since deleted. Not a REQUESTED row with a revoked
                     -- credential -- acquire.py now records machine_id on
                     -- its failure paths too, so that row is caught by the
                     -- revoked branch above instead.
                     when m.id is null
                       then coalesce(rc.acquired_at, rc.created_at)
                            < now() - make_interval(secs => %(abandoned)s)
                     -- It spoke, and then it stopped.
                     when m.last_seen_at is not null
                       then m.last_seen_at
                            < now() - make_interval(secs => %(quiet)s)
                     -- It has never spoken: still booting, or never will.
                     else coalesce(rc.acquired_at, rc.created_at)
                          < now() - make_interval(secs => %(boot)s)
                   end
             order by coalesce(rc.acquired_at, rc.created_at)
            """,
            # `secs` is the one make_interval() argument typed `double
            # precision`, which is why every window here is expressed in
            # seconds exactly as in `budget.window_spend_usd`.
            {
                "abandoned": float(abandoned_after_s),
                "quiet": float(quiet_after_s),
                "boot": float(boot_grace_s),
            },
        )
        return [dict(r) for r in cur.fetchall()]


def finished_rentals_with_live_credentials(
    db: psycopg.Connection,
) -> list[dict]:
    """Rentals that are over and whose credential is not.

    The retry vehicle for the half of teardown that costs no money and is
    therefore easiest to lose: ``_revoke_credential`` is best effort, and a
    row that reached ``RELEASED`` is out of :func:`unreleased_rows` for good,
    so without this query one failed revoke would leave a live token and a
    bound pool for ever — and the next rental into that pool refused by an
    isolation assertion about a machine nobody meant to keep.

    A binding with no live token still counts: ``revoke_sandbox_machine``
    unbinds on every call including ones where the row is already revoked,
    which is exactly the half-done state a crashed revoke leaves behind.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select rc.id, rc.owner_id, rc.machine_id
              from public.rented_capacity rc
              join public.machines m on m.id = rc.machine_id
             where rc.state in ('RELEASED', 'FAILED')
               and (m.status <> 'revoked'
                    or exists (select 1 from public.machine_pools mp
                                where mp.machine_id = m.id))
             order by coalesce(rc.released_at, rc.created_at)
            """
        )
        return [dict(r) for r in cur.fetchall()]


def _note(
    db: psycopg.Connection, rented_id: str, code: str, detail: str,
    *, guard: str = "",
) -> None:
    """Record why, WITHOUT closing the row.

    Copied in spirit from ``acquire._keep_sweepable``: the failure is worth
    reading, but the state has to stay somewhere the sweep looks. This one
    does not force the state, because it is only ever called about a row it
    just read as sweepable.

    ``guard`` is a static SQL fragment — never anything from outside this
    module — naming what must still be true for the note to be truthful. A
    row can change under us between the read and this write, and a stale
    diagnosis is worse than none: it sends somebody to a venue console
    looking for a machine that is working fine.

    Writing it is best effort. Failing to annotate a row must not look like
    failing to destroy a machine, so this never raises.
    """
    try:
        with db.cursor() as cur:
            cur.execute(
                f"""
                update public.rented_capacity
                   set failure_code = %s, failure_detail = %s
                 where id = %s {guard}
                """,
                (code, detail[:_DETAIL_MAX], rented_id),
            )
        db.commit()
    except Exception:  # noqa: BLE001 - the annotation is not the guarantee
        log.warning(
            "capacity reconcile: could not annotate %s with %s",
            rented_id, code, exc_info=True,
        )


def _fetch_row(db: psycopg.Connection, rented_id: str) -> dict | None:
    with db.cursor() as cur:
        cur.execute(
            """
            select id, state, provider_handle, venue_id, owner_id, machine_id,
                   failure_code
              from public.rented_capacity
             where id = %s
            """,
            (rented_id,),
        )
        row = cur.fetchone()
    return dict(row) if row is not None else None


def _mark_released(db: psycopg.Connection, rented_id: str) -> None:
    """Close the row on evidence that the machine is gone.

    ``FAILED`` keeps its state and gains a ``released_at``, exactly as
    ``acquire._close_failed`` writes it: the reason a row failed is worth more
    than relabelling it, and either way the row stops being swept. Only a
    sweepable row becomes ``RELEASED``.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            update public.rented_capacity
               set state = case when state in ('REQUESTED', 'ACTIVE')
                                then 'RELEASED' else state end,
                   released_at = coalesce(released_at, now())
             where id = %s and state <> 'RELEASED'
            """,
            (rented_id,),
        )
    db.commit()


async def _venue_says_gone(provider: ResourceProvider, handle: str) -> bool:
    """Ask the VENUE whether the machine is gone. Never our own rows.

    This runs only when ``release`` refused to confirm the destroy -- either
    it reported ``destroyed=False`` or it raised. A provider's own report is
    a claim about a call; ``observe`` is a claim about the world, and the
    world is what the bill is computed from.

    Only ``exists=False`` counts as gone. A machine that exists but is not
    running is NOT proof the money stopped -- a stopped instance still bills
    for its storage at most venues -- and in any case ``RELEASED`` says the
    thing is gone, not that it is idle. Treating ``running=False`` as release
    would close rows over machines that are still on the invoice.

    Its own try block, and a refusal to guess on error: an ``observe`` that
    fails tells us nothing, and "we could not ask" must never read as "the
    answer was yes".
    """
    try:
        state = await provider.observe(handle=handle)
    except Exception as exc:  # noqa: BLE001 - recorded by the caller
        log.warning(
            "capacity reconcile: observe(%s) failed: %s",
            handle, type(exc).__name__, exc_info=True,
        )
        return False
    return not state.exists


async def _revoke_credential(db: psycopg.Connection, row: dict) -> None:
    """Kill the identity the rented machine was working under.

    Runs whether the destroy succeeded, failed, or reported nothing at all —
    ``cleanup_session``'s rule, that neither half may hide the other. There is
    no ordering dependency to respect: ``revoke_sandbox_machine`` touches
    nothing outside this database, so a machine that is already gone, still
    running, or was never reachable revokes exactly as cleanly. It is also
    idempotent, which is what lets every path here call it unconditionally.

    Best effort by construction. It is a second liability, not the guarantee:
    losing it must not make a destroyed machine look undestroyed, so it is
    logged and never raised. :func:`finished_rentals_with_live_credentials` is
    what comes back for the ones this loses.
    """
    machine_id = row.get("machine_id")
    if not machine_id:
        return
    try:
        await asyncio.to_thread(
            sandbox_identity.revoke_sandbox_machine,
            db,
            machine_id=str(machine_id),
            owner_id=str(row["owner_id"]),
        )
    except Exception:  # noqa: BLE001 - logged, never masking the destroy
        log.error(
            "capacity reconcile: could not revoke machine %s for %s",
            machine_id, row["id"], exc_info=True,
        )


async def release_capacity(
    db: psycopg.Connection, provider: ResourceProvider, *, rented_id: str
) -> bool:
    """Destroy one rental and mark it released. Idempotent, on both halves.

    Two things are torn down, independently: the machine at the venue and the
    credential it was renting under. The return value reports only the first,
    because only the first costs money by the second.

    Returns ``True`` only when something is known to have stopped: the row was
    already ``RELEASED``, the provider destroyed the machine, or two
    independent sweeps agree the venue no longer has the handle. ``False``
    means *unknown*, and an unknown row is deliberately left in a state
    :func:`unreleased_rows` selects, so the next sweep tries again.

    Called both from the settle path and from the sweep, repeatedly and
    concurrently, so every step is safe to repeat: an already-gone handle is a
    success, the revoke runs on every call including ones where the row is
    already closed, and the state transition is a guarded compare-and-set.

    No liveness check. The sweep decides *which* rentals to hand here by
    asking whether anything is still using them; a caller naming a
    ``rented_id`` outright has already decided, and usually knows something —
    that the job finished — which no heartbeat can tell us.
    """
    row = await asyncio.to_thread(_fetch_row, db, rented_id)

    if row is None:
        # Nothing to destroy and nothing to record. Not an error: rows cascade
        # away with the account, and the sweep may be holding a stale list.
        log.warning("capacity reconcile: no rented_capacity row %s", rented_id)
        return False
    if row["state"] == "RELEASED":
        # Idempotent on BOTH halves. Returning early without this was a way
        # for a revoke that failed once to be retried by nothing: the row is
        # out of `unreleased_rows` for good, and rentals are minted
        # `persistent` so no other sweep comes for the machine either.
        await _revoke_credential(db, row)
        return True

    handle = row["provider_handle"]
    if not handle:
        # The dangerous case, argued at length in the module docstring: we
        # cannot name the machine, so we cannot ask the venue about it, so we
        # have no evidence either way. Leave it sweepable.
        #
        # The credential is left alone too, deliberately. Revoking is the one
        # destructive thing that could still be done to this row, and doing it
        # on no evidence would sabotage the acquisition that may be in flight
        # right now -- the machine it is booting would find its token already
        # dead -- while stopping no money at all.
        await asyncio.to_thread(
            _note, db, rented_id, NO_HANDLE,
            f"state {row['state']} at venue {row['venue_id']} with no "
            "provider_handle: nothing to destroy and nothing to observe "
            "with. The venue may hold a machine we cannot name; the row is "
            "left sweepable rather than closed on that assumption.",
            # Only while that is still true. A row that has just learned its
            # handle is a row this diagnosis would libel.
            guard="and provider_handle is null",
        )
        return False

    # 1. Stop the money. Independent of every write below: a database that
    #    will not take the update must not stop the destroy, and a destroy
    #    that fails must not stop the row from being annotated.
    destroyed = False
    detail = ""
    try:
        outcome = await provider.release(handle=handle)
        destroyed = bool(outcome.destroyed)
        detail = outcome.detail or ""
    except Exception as exc:  # noqa: BLE001 - recorded on the row, not raised
        detail = f"release raised {type(exc).__name__}: {exc}"
        log.error(
            "capacity reconcile: release(%s) for %s raised",
            handle, rented_id, exc_info=True,
        )

    # 2. If the provider would not confirm it, ask the venue -- and make it
    #    say so twice.
    #
    #    One `exists=False` is not proof: a transient 404, a read against a
    #    replica that has not caught up, an id typed into the wrong region.
    #    Acting on a single one closes the row for ever, and the cost of being
    #    wrong is a machine that bills with a RELEASED row in front of it,
    #    which is the exact failure this module exists to prevent. So the
    #    first observation is only WRITTEN DOWN, and the row stays sweepable;
    #    a later sweep that sees the same thing, minutes apart and through a
    #    fresh call, is what closes it. The row is the memory, so no state
    #    lives in the process and a restart loses nothing but time.
    corroborated = row.get("failure_code") == VENUE_SAYS_GONE
    if not destroyed and await _venue_says_gone(provider, str(handle)):
        if corroborated:
            destroyed = True
            detail = (
                f"release did not confirm ({detail or 'no detail'}), and two "
                f"sweeps agree the venue no longer has {handle}"
            )
        else:
            await asyncio.to_thread(
                _note, db, rented_id, VENUE_SAYS_GONE,
                f"release did not confirm ({detail or 'no detail'}) and the "
                f"venue reports {handle} no longer exists. Held for a second "
                "sweep to agree before closing the row: one absent reading "
                "can be a transient 404, and closing on it is how a machine "
                "bills for ever behind a RELEASED row.",
                guard="and state <> 'RELEASED'",
            )
            # The credential still goes, on the same reasoning as below.
            await _revoke_credential(db, row)
            return False

    # 3. Kill the identity, whatever happened above. A machine that outlived
    #    its destroy must not keep a working token, and the pool has to be
    #    given back either way or the next rental into it is refused by an
    #    isolation assertion about a machine nobody meant to keep.
    await _revoke_credential(db, row)

    if not destroyed:
        # Still billing, as far as anything knows. The row keeps its
        # sweepable state on purpose -- this is the only thing that will come
        # back for it.
        await asyncio.to_thread(
            _note, db, rented_id, NOT_DESTROYED,
            f"{handle} at venue {row['venue_id']} may still be running: "
            f"{detail or 'the provider reported no destroy and no detail'}",
            guard="and state <> 'RELEASED'",
        )
        return False

    await asyncio.to_thread(_mark_released, db, rented_id)
    return True


async def reconcile_rented(
    db: psycopg.Connection,
    providers: dict[str, ResourceProvider],
    *,
    quiet_after_s: float = DEFAULT_QUIET_AFTER_S,
    boot_grace_s: float = DEFAULT_BOOT_GRACE_S,
    abandoned_after_s: float = DEFAULT_ABANDONED_AFTER_S,
) -> list[str]:
    """Sweep. Returns the ids whose machines it settled.

    Every window has a default, and the defaults are the safe ones. **None of
    them is how often to sweep** — call this as often as you like, minutes
    apart; what these govern is how long a rental gets before we conclude
    nobody is using it. Passing a poll interval here would destroy live
    machines. See the module docstring.

    Two sweeps, not one, because teardown is two things and they fail
    separately: the money half destroys machines nothing is using, and the
    credential half comes back for identities whose rentals are already over.
    Each row is handled inside its own try block and each half inside another,
    so one bad venue -- or one bad row -- never stops the money from stopping
    everywhere else. The same reason the loops in ``app.py`` swallow and log a
    failed pass rather than letting the task die and silently removing the
    backstop.
    """
    settled: list[str] = []

    try:
        rows = await asyncio.to_thread(
            unreleased_rows, db,
            quiet_after_s=quiet_after_s, boot_grace_s=boot_grace_s,
            abandoned_after_s=abandoned_after_s,
        )
    except Exception:  # noqa: BLE001 - a failed read must not end the pass
        log.error(
            "capacity reconcile: could not list unreleased rentals",
            exc_info=True,
        )
        rows = []

    for row in rows:
        rented_id = str(row["id"])
        provider = providers.get(str(row["venue_id"]))
        if provider is None:
            # A venue with no configured provider cannot be swept. Leave the
            # row: a stuck row is visible, a closed one is not.
            log.warning(
                "capacity reconcile: no provider configured for venue %s; "
                "%s left unreleased", row["venue_id"], rented_id,
            )
            continue
        try:
            if await release_capacity(db, provider, rented_id=rented_id):
                settled.append(rented_id)
        except Exception:  # noqa: BLE001 - one row must not end the sweep
            log.error(
                "capacity reconcile: releasing %s failed; continuing",
                rented_id, exc_info=True,
            )

    try:
        for row in await asyncio.to_thread(
            finished_rentals_with_live_credentials, db
        ):
            await _revoke_credential(db, row)
    except Exception:  # noqa: BLE001 - the money half already happened
        log.error(
            "capacity reconcile: the credential sweep failed", exc_info=True
        )

    return settled
