"""Destroying what we rented, whether or not anybody remembered to ask.

Release is attempted when a job settles. **Correctness does not depend on
that call happening, or succeeding.** The sweep here is the guarantee: it
reads the rows that are still costing money and destroys what they name,
for ever, on a timer. Everything in this module is written for the case
where the request path is already gone.

``cleanup_session`` is the model in two respects.

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
refuse the NEXT rental into that pool, for a reason that has nothing to do
with it. ``acquire.py`` already revokes on its failure path for exactly that
reason; without the same on the success path, renting once would poison the
pool for good.

*Positive evidence before a terminal state.* ``cleanup_session`` records
TERMINATED only once the API confirms the sandbox is gone, because
TERMINATED is a promise that nothing is running. ``RELEASED`` is the same
promise about a machine that bills by the second, so it is written only
when something actually said the machine is gone -- never on the strength of
an assumption, and never on the strength of a *missing* handle.

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
'REQUESTED'``, records the handle on the row and tries to release it -- and
if that release fails, the result is a RELEASED row carrying a live handle.
:func:`unreleased_rows` selects only ``REQUESTED`` and ``ACTIVE``, so nothing
would ever look at that row again and the machine would bill for ever.

So a handleless row is **left exactly where it is**: sweepable, visible, and
re-examined on every pass. That is deliberate, and it means a crashed
acquisition that truly created nothing also stays for ever. A permanently
stuck row is a cheap, visible defect. A silently closed one is an invoice.
This is the same trade ``acquire.py`` makes when it declines to mark a row
FAILED while a handle may still be live.

:func:`ResourceProvider.observe` is what would settle the question, and it
cannot be asked: it reads the venue by handle, and a handle is the one thing
this row does not have. It *is* used where it can be -- see
:func:`_venue_says_gone` -- to turn a release the provider refused to confirm
into a fact about the venue rather than a guess about our own rows. Finding a
machine we can name nowhere at all is a venue-listing problem (an enumeration
by tag or label), not something this sweep can solve, and it is why the
handle is written to the row *before* anything is destroyed.

The sweep races money. ``DEFAULT_RECONCILE_INTERVAL_S`` says it for the
sandbox reconciler and it is just as true here: minutes, not hours.
"""
from __future__ import annotations

import asyncio
import logging

import psycopg

from flashml_cloud_api import sandbox_identity
from flashml_cloud_api.capacity.provider import ResourceProvider

__all__ = ["release_capacity", "reconcile_rented", "unreleased_rows"]

log = logging.getLogger(__name__)

#: How much of a failure message is kept, matching ``acquire.py``.
#: ``failure_detail`` is for a human reading a row, not for a stack trace.
_DETAIL_MAX = 2000

#: The states that still cost money and that the sweep may act on. A row
#: outside this set is either finished (``RELEASED``) or already closed with
#: its reason (``FAILED``) -- and ``FAILED`` is deliberately not swept, which
#: is precisely why ``acquire.py`` refuses to write it while a handle may
#: still be live.
SWEEPABLE = ("REQUESTED", "ACTIVE")


def unreleased_rows(
    db: psycopg.Connection, *, settle_after_s: float
) -> list[dict]:
    """Rows still costing money and old enough that nothing is mid-flight.

    ``REQUESTED`` is included on purpose: a row that never learned its handle
    may still have created something at the venue. It is also why
    ``acquire.py`` leaves a doubtful row REQUESTED rather than FAILED -- this
    query is the only thing that will ever go looking.

    ``settle_after_s`` measures from ``acquired_at`` when there is one and
    ``created_at`` otherwise, so an acquisition still in flight is not
    destroyed out from under itself. Oldest first: the row that has been
    billing longest is the one worth settling first.
    """
    with db.cursor() as cur:
        cur.execute(
            f"""
            select id, venue_id, state, provider_handle, machine_id
              from public.rented_capacity
             where state in ({", ".join(["%s"] * len(SWEEPABLE))})
               and coalesce(acquired_at, created_at)
                   < now() - make_interval(secs => %s)
             order by coalesce(acquired_at, created_at)
            """,
            # `secs` is the one make_interval() argument typed `double
            # precision`, which is why the window is expressed in seconds
            # here exactly as in `budget.window_spend_usd`.
            (*SWEEPABLE, float(settle_after_s)),
        )
        return [dict(r) for r in cur.fetchall()]


def _note(db: psycopg.Connection, rented_id: str, code: str, detail: str) -> None:
    """Record why, WITHOUT closing the row.

    Copied in spirit from ``acquire._note_failure``: the failure is worth
    reading, but the state has to stay somewhere the sweep looks. Writing it
    is best effort -- failing to annotate a row must not look like failing to
    destroy a machine, so this never raises.
    """
    try:
        with db.cursor() as cur:
            cur.execute(
                """
                update public.rented_capacity
                   set failure_code = %s, failure_detail = %s
                 where id = %s
                """,
                (code, detail[:_DETAIL_MAX], rented_id),
            )
        db.commit()
    except Exception:  # noqa: BLE001 - the annotation is not the guarantee
        log.warning(
            "capacity reconcile: could not annotate %s with %s",
            rented_id, code, exc_info=True,
        )


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
    running, or was never reachable revokes exactly as cleanly.

    Best effort by construction. It is a second liability, not the guarantee:
    losing it must not make a destroyed machine look undestroyed, so it is
    logged and never raised.
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
    """Destroy one rental and mark it released. Idempotent.

    Two things are torn down, independently: the machine at the venue and the
    credential it was renting under. The return value reports only the first,
    because only the first costs money by the second.

    Returns ``True`` only when something is known to have stopped: the row was
    already ``RELEASED``, the provider destroyed the machine, or the venue
    says the handle no longer exists. ``False`` means *unknown*, and an
    unknown row is deliberately left in a state :func:`unreleased_rows`
    selects, so the next sweep tries again.

    Called both from the settle path and from the sweep, repeatedly and
    concurrently, so every step is safe to repeat: an already-gone handle is a
    success, and the state transition is a guarded compare-and-set.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select id, state, provider_handle, venue_id, owner_id, machine_id
              from public.rented_capacity
             where id = %s
            """,
            (rented_id,),
        )
        row = cur.fetchone()

    if row is None:
        # Nothing to destroy and nothing to record. Not an error: rows cascade
        # away with the account, and the sweep may be holding a stale list.
        log.warning("capacity reconcile: no rented_capacity row %s", rented_id)
        return False
    if row["state"] == "RELEASED":
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
        _note(
            db, rented_id, "RECONCILE_NO_HANDLE",
            f"state {row['state']} at venue {row['venue_id']} with no "
            "provider_handle: nothing to destroy and nothing to observe "
            "with. The venue may hold a machine we cannot name; the row is "
            "left sweepable rather than closed on that assumption.",
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

    # 2. If the provider would not confirm it, ask the venue. A machine the
    #    venue no longer has is released however badly the call went.
    if not destroyed and await _venue_says_gone(provider, str(handle)):
        destroyed = True
        detail = (
            f"release did not confirm ({detail or 'no detail'}), but the "
            f"venue reports {handle} no longer exists"
        )

    # 3. Kill the identity, whatever happened above. A machine that outlived
    #    its destroy must not keep a working token, and the pool has to be
    #    given back either way or the next rental into it is refused by an
    #    isolation assertion about a machine nobody meant to keep.
    await _revoke_credential(db, dict(row))

    if not destroyed:
        # Still billing, as far as anything knows. The row keeps its
        # sweepable state on purpose -- this is the only thing that will come
        # back for it.
        _note(
            db, rented_id, "RECONCILE_NOT_DESTROYED",
            f"{handle} at venue {row['venue_id']} may still be running: "
            f"{detail or 'the provider reported no destroy and no detail'}",
        )
        return False

    with db.cursor() as cur:
        cur.execute(
            """
            update public.rented_capacity
               -- FAILED keeps its state and gains a released_at, exactly as
               -- `acquire._fail_row` writes it: the reason a row failed is
               -- worth more than relabelling it, and either way the row stops
               -- being swept. Only a sweepable row becomes RELEASED.
               set state = case when state in ('REQUESTED', 'ACTIVE')
                                then 'RELEASED' else state end,
                   released_at = coalesce(released_at, now())
             where id = %s and state <> 'RELEASED'
            """,
            (rented_id,),
        )
    db.commit()
    return True


async def reconcile_rented(
    db: psycopg.Connection,
    providers: dict[str, ResourceProvider],
    *,
    settle_after_s: float,
) -> list[str]:
    """Sweep everything still billing. Returns the ids it settled.

    One row's trouble never ends the pass. A venue having a bad minute must
    not stop the money from stopping everywhere else, so each row is handled
    inside its own try block and the sweep carries on -- the same reason the
    loops in ``app.py`` swallow and log a failed pass rather than letting the
    task die and silently removing the backstop.
    """
    settled: list[str] = []
    for row in unreleased_rows(db, settle_after_s=settle_after_s):
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
    return settled
