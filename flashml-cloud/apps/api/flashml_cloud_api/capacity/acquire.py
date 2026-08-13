"""Rent one machine for one job.

The order is the design, not an implementation detail:

1. **Budget gate first.** Before a row, before a credential, before the
   venue is asked anything. A refusal must cost nothing and leave nothing.
2. **Open the row.** From here every step has somewhere durable to be
   recorded and a restart has something to find.
3. **Mint the credential into the SUBMITTER'S pool**, through
   :func:`sandbox_identity.provision_rented_machine`. Not an isolation pool --
   this machine is meant to share a pool with the user's other machines, and
   that minting path asserts no isolation, because
   :func:`sandbox_identity.assert_pool_isolated` is an evaluation-session
   invariant and applying it here forbade the thing this function exists to do.
   See "the lease, and what it replaced" below.

   That identity **travels on to the provider**, on the request's ``node_id``
   and ``machine_token``. A pull-style venue has no exec channel: the
   credential is in the machine's first boot or the host never learns who it
   is and can never enrol. Minting before the venue is asked for anything is
   therefore ordering, not tidiness.
4. **Re-gate the answered rate**, then acquire -- and record the handle in
   the same update that moves REQUESTED -> ACTIVE.

Any failure from step 2 onwards records the failure and releases whatever the
provider created, because the row and its handle are the evidence of what went
wrong and the only route to the money still running.

THE ONE QUESTION THE FAILURE PATH ASKS
--------------------------------------
*Can this row be closed, or might something still be running?* ``FAILED`` and
``RELEASED`` are both terminal to :func:`reconcile.unreleased_rows`, which
selects ``REQUESTED`` and ``ACTIVE`` only. So a terminal state is a claim that
nothing is billing, and it is written **only on evidence**:

* **The venue was never asked** (the gate refused, the row would not open, the
  credential would not mint). Nothing can exist at a venue that was not
  called. Closed, ``FAILED``, with the exception's own name as the code.
* **We hold a handle and something said the machine is gone.** Closed,
  ``FAILED``, ``released_at`` stamped.
* **Anything else** -- the release refused or raised, or ``acquire`` itself
  raised so there may be a machine we cannot even name -- is *unknown*, and an
  unknown row is forced back into a state the sweep selects. Not left in
  whatever state it happens to hold: the row may have been marked ``RELEASED``
  by a reconciler that raced this acquisition, and a ``RELEASED`` row naming a
  live handle is exactly the invoice this module exists to prevent.

The handle is written to the row **before** anything is destroyed, so a crash
between "the venue answered" and "we destroyed it" still leaves the machine
named. ``provider_handle`` is only ever filled in, never cleared.

**So is ``machine_id``, and that is not symmetry for its own sake.** It used
to be written on the success path only -- inside :func:`_move_to_active` --
which meant the rows likeliest to hold a machine that is still billing
reached :func:`reconcile.unreleased_rows` naming no machine at all. That
query's fastest branch is *a bound credential that is already revoked, swept
at once with no allowance*, and it is unreachable from a row whose
``machine_id`` is null. So an ``ACQUIRE_NOT_DESTROYED`` row -- the one case
where we hold a handle, could not destroy what it names, and have just
revoked its credential -- fell into *nothing to ask* instead and waited
``abandoned_after_s``: thirty minutes of billing for the case we are surest
about. Recording the machine on the way out also puts the row back in reach
of :func:`reconcile.finished_rentals_with_live_credentials`, which joins on
``machine_id``: without it, a revoke that failed here had nothing anywhere
that would ever retry it.

``failure_code`` follows ``reconcile.py``'s convention: a stable, greppable
``ACQUIRE_*`` token whenever the row is left for the sweep -- those are the
rows an operator hunts for -- and the exception class name when the row is
closed, where the only question left is why it failed.

The credential is revoked on every failure path, independently of the release
(``cleanup_session``'s rule, that neither failure may hide the other). It is
not tidiness, and since OC-6 it is not self-correcting either: a rental's
identity is a LEASE, true only while we hold the hardware, so a failed
acquisition that leaves a live token bound to the submitter's pool has left a
working credential for a machine we do not have. It used to be caught by
accident -- the next acquisition into that pool failed an isolation assertion
-- and that accident is gone. See below.

WHY A FAILED ``acquire`` IS NOT TREATED AS CLEAN
------------------------------------------------
:meth:`ResourceProvider.acquire` is required to destroy whatever it created
before it raises, and ``FakeProvider`` honours that. **The row is not closed on
the strength of it.** A venue call that raised is the most likely orphan there
is -- the pod is created, then waiting for it to register times out -- and the
difference between "the venue refused before creating anything" and "the venue
created something and then we lost it" is invisible from here. So the row keeps
a sweepable state and the ``ACQUIRE_UNCONFIRMED`` code.

The sweep cannot destroy what it cannot name, so this does not stop the money
by itself; what it does is keep the attempt in
:func:`reconcile.unreleased_rows`, which is the list an operator reconciles
against the venue's own machine listing. The rows deliberately *not* left there
are the ones where the venue was never called at all, so the list stays worth
reading. This is the same trade ``reconcile.py`` makes for a handleless row:
a permanently stuck row is a cheap, visible defect; a silently closed one is an
invoice.

THE LEASE, AND WHAT IT REPLACED (OC-6, 2026-08-12)
------------------------------------------------
This module used to reuse ``provision_sandbox_machine`` and inherit its final
``assert_pool_isolated``. That assertion is one level below this module and
this module never called it, but the effect reached here anyway: renting into a
pool that already held a machine was refused, which is exactly the case this
feature is for -- the submitter's workspace already holds their laptop -- and
it made ``gpu_count > 1`` unreachable as well. OC-6 settled it with a sibling
minting path, ``provision_rented_machine``, which asserts nothing about the
pool. ``provision_sandbox_machine`` and its invariant are untouched: they
protect an evaluation sandbox holding a session credential, where a second
machine in the pool could claim the session's tasks. A rented GPU has neither
property.

**What that assertion was also doing, by accident, is now nobody's accident.**
A dead machine left bound to the pool used to make the *next* acquisition fail
loudly. Now it does not: the next rental succeeds and the stale credential sits
there, valid, bound to a user's workspace, for hardware a third party has since
rented. So the revoke on every failure path here, and
``reconcile.finished_rentals_with_live_credentials``' unbounded retry of it,
are the entire mechanism -- not a backstop behind one. That is what "a rental's
binding is a lease" means operationally, and ``machines.lifecycle = 'leased'``
(migration 0023) is the row saying so.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

import psycopg

from flashml_cloud_api import sandbox_identity
from flashml_cloud_api.capacity.budget import assert_within_budget
from flashml_cloud_api.capacity.provider import CapacityRequest, ResourceProvider

__all__ = ["acquire_for_job"]

log = logging.getLogger(__name__)

#: How much of a failure message is kept, matching ``reconcile.py``.
#: ``failure_detail`` is for a human reading a row, not for a stack trace.
_DETAIL_MAX = 2000

#: ``acquire`` raised: the venue was asked, and may hold a machine we cannot
#: name. Stable and greppable, matching ``reconcile.RECONCILE_NO_HANDLE``.
ACQUIRE_UNCONFIRMED = "ACQUIRE_UNCONFIRMED"

#: We hold a handle and could not destroy what it names. The sibling of
#: ``reconcile.RECONCILE_NOT_DESTROYED``, and the same meaning: still billing,
#: as far as anything knows.
ACQUIRE_NOT_DESTROYED = "ACQUIRE_NOT_DESTROYED"


def _open_row(
    db: psycopg.Connection, request: CapacityRequest, usd_per_hour: float | None
) -> str:
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.rented_capacity
                (venue_id, state, owner_id, pool_id, job_id, gpu_count,
                 usd_per_hour)
            values (%s, 'REQUESTED', %s, %s, %s, %s, %s)
            returning id
            """,
            (request.venue_id, request.owner_id, request.pool_id,
             request.job_id, request.gpu_count, usd_per_hour),
        )
        rid = str(cur.fetchone()["id"])
    db.commit()
    return rid


def _move_to_active(
    db: psycopg.Connection, rid: str, *, handle: str, machine_id: str,
    usd_per_hour: float | None,
) -> bool:
    """The handle, the machine and ACTIVE in one statement.

    Guarded on ``state = 'REQUESTED'``: if a reconciler settled this row while
    the venue was answering, this must not overwrite its decision. Returns
    whether the row moved.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            update public.rented_capacity
               set state = 'ACTIVE', provider_handle = %s,
                   machine_id = %s, acquired_at = now(),
                   usd_per_hour = coalesce(%s, usd_per_hour)
             where id = %s and state = 'REQUESTED'
            """,
            (handle, machine_id, usd_per_hour, rid),
        )
        return cur.rowcount == 1


def _record_evidence(
    db: psycopg.Connection, rid: str, *, handle: str | None,
    machine_id: str | None,
) -> None:
    """Write what this acquisition created, without touching the state.

    Runs on the failure path before anything is destroyed. Both columns are
    only ever filled in, never cleared -- the one thing that must not happen
    to a machine we are paying for is losing its name, and the credential it
    is renting under is the other half of that name.

    ``machine_id`` matters to the *sweep*, not just to the record: a rental
    with a bound, revoked credential is swept immediately, while one with no
    machine bound waits ``reconcile.DEFAULT_ABANDONED_AFTER_S``. Leaving this
    to the success path alone gave the rows we are surest about -- we hold a
    handle and something refused to destroy it -- the slowest window there
    is. See the module docstring.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            update public.rented_capacity
               set provider_handle = coalesce(provider_handle, %s),
                   machine_id = coalesce(machine_id, %s::uuid)
             where id = %s
            """,
            (handle, machine_id, rid),
        )
    db.commit()


def _close_failed(
    db: psycopg.Connection, rid: str, code: str, detail: str, *,
    released: bool,
) -> None:
    """Close the row as FAILED. Only ever called on evidence that nothing is
    running.

    ``FAILED`` is written only over a state the sweep would otherwise have
    selected, the mirror of ``reconcile``'s ``RELEASED`` transition: a row some
    other actor has already settled keeps what it says, and this call adds only
    the reason. ``released`` records that we destroyed something, so a FAILED
    row that cleaned up after itself is not indistinguishable from one that
    never created anything.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            update public.rented_capacity
               set state = case when state in ('REQUESTED', 'ACTIVE')
                                then 'FAILED' else state end,
                   failure_code = %s, failure_detail = %s,
                   released_at = case when %s then coalesce(released_at, now())
                                      else released_at end
             where id = %s
            """,
            (code, detail[:_DETAIL_MAX], released, rid),
        )
    db.commit()


def _keep_sweepable(
    db: psycopg.Connection, rid: str, code: str, detail: str
) -> None:
    """Record why, and guarantee the row is one :func:`unreleased_rows` selects.

    **Forcing the state is the point, not leaving it alone.** Leaving it alone
    is only safe when it is already sweepable, and it is not always: a
    reconciler may have marked this row RELEASED while the venue was answering
    -- see ``reconcile``'s "why a missing handle is the dangerous case" -- and
    a RELEASED row carrying a live handle is one nothing will ever look at
    again. So a terminal state is reopened to REQUESTED, and the ``released_at``
    that went with it is dropped, because it was a claim about a machine that
    turned out to be alive.

    A row already REQUESTED or ACTIVE keeps exactly what it has: it is already
    in the list, and ``acquired_at`` is what the settle window is measured
    from.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            update public.rented_capacity
               set state = case when state in ('REQUESTED', 'ACTIVE')
                                then state else 'REQUESTED' end,
                   released_at = case when state in ('REQUESTED', 'ACTIVE')
                                      then released_at else null end,
                   failure_code = %s, failure_detail = %s
             where id = %s
            """,
            (code, detail[:_DETAIL_MAX], rid),
        )
    db.commit()


async def _abandon(
    db: psycopg.Connection,
    provider: ResourceProvider,
    rid: str,
    *,
    handle: str | None,
    venue_asked: bool,
    credential: Any,
    owner_id: str,
    exc: BaseException,
) -> None:
    """Undo what this acquisition managed to do, in the order that keeps the
    machine findable at every point.

    Nothing in here may raise: it runs from an ``except`` block whose job is
    to re-raise the original failure, and an exception thrown while cleaning
    up would replace the reason with the reason for the reason.
    """
    code = type(exc).__name__
    notes = [f"{code}: {exc}"]

    # 1. Durable before destructive. If this process dies on the next line,
    #    the row still names the thing that is billing AND the credential it
    #    is billing under -- which is what puts it in the sweep's no-allowance
    #    branch the moment the revoke below lands.
    machine_id = credential.machine_id if credential is not None else None
    if handle or machine_id:
        try:
            _record_evidence(db, rid, handle=handle, machine_id=machine_id)
        except Exception as note:  # pragma: no cover - defensive
            notes.append(f"[handle/machine not recorded: {note}]")

    # 2. Stop the money, and decide what we actually know afterwards.
    if handle:
        settled = False
        try:
            settled = bool((await provider.release(handle=handle)).destroyed)
        except Exception as note:
            notes.append(f"[release of {handle} raised: {note}]")
        if not settled:
            code = ACQUIRE_NOT_DESTROYED
            notes.append(
                f"[{handle} may still be running; left for the sweep]"
            )
    elif venue_asked:
        # No handle, but the venue WAS called. It may hold a machine we cannot
        # name, and cannot be asked about one we cannot name. Unknown.
        settled = False
        code = ACQUIRE_UNCONFIRMED
        notes.append(
            "[the venue was asked and did not return a handle; anything it "
            "created cannot be named from here and must be reconciled against "
            "the venue's own listing]"
        )
    else:
        # The venue was never called. Nothing can exist at it.
        settled = True

    # 3. Kill the identity, independently of the release: neither failure may
    #    hide the other, and the lease has to end either way. It used to be
    #    self-correcting -- a leftover binding made the next acquisition into
    #    this pool fail an isolation assertion -- and since OC-6 it is not. A
    #    credential left alive here is a working token, bound to a user's
    #    workspace, for a machine that exists nowhere but in this row.
    #    `reconcile.finished_rentals_with_live_credentials` is what comes back
    #    for the ones this loses; nothing else will.
    if credential is not None:
        try:
            await asyncio.to_thread(
                sandbox_identity.revoke_sandbox_machine,
                db,
                machine_id=credential.machine_id,
                owner_id=owner_id,
            )
        except Exception as note:
            notes.append(
                f"[credential {credential.machine_id} not revoked: {note}]"
            )

    detail = " ".join(notes)
    try:
        if settled:
            await asyncio.to_thread(
                _close_failed, db, rid, code, detail, released=bool(handle),
            )
        else:
            await asyncio.to_thread(_keep_sweepable, db, rid, code, detail)
    except Exception:  # pragma: no cover - defensive
        # The row is already open and, if there was a handle, already names
        # it. Losing the annotation is survivable; losing the original
        # exception is not.
        log.error(
            "capacity acquire: could not annotate %s with %s",
            rid, code, exc_info=True,
        )


async def acquire_for_job(
    db: psycopg.Connection,
    provider: ResourceProvider,
    settings: Any,
    *,
    request: CapacityRequest,
) -> str:
    """Rent one machine. Returns the ``rented_capacity`` row id.

    Raises :class:`budget.BudgetRefused` both for an acquisition refused before
    anything was created and for one whose *answered* rate broke a ceiling the
    quote fit -- in the second case the machine is destroyed before the refusal
    is raised.
    """
    # 1. Gate first. Raises BudgetRefused; nothing has been created.
    await asyncio.to_thread(
        assert_within_budget, db, venue_id=request.venue_id,
        usd_per_hour=request.quoted_usd_per_hour, settings=settings,
    )

    # 2. The row, before the money.
    rid = await asyncio.to_thread(
        _open_row, db, request, request.quoted_usd_per_hour
    )

    credential = None
    handle: str | None = None
    venue_asked = False
    try:
        # 3. Identity, in the submitter's own pool, alongside whatever they
        #    already have there. `provision_rented_machine`, not
        #    `provision_sandbox_machine`: the second asserts pool isolation and
        #    would refuse this outright (OC-6).
        #
        # It mints `lifecycle = 'leased'` -- a lease, not a deed, and
        # deliberately not 'ephemeral'. 'ephemeral' would hand this machine to
        # `expire_stale_ephemeral_machines`, whose TTL runs from
        # `coalesce(last_seen_at, created_at)` -- and a rented host has no
        # `last_seen_at` until it has booted, pulled a multi-gigabyte image
        # and registered, which regularly takes longer than the 15-minute
        # default. That sweep would revoke the credential of a machine that is
        # still starting up. What ends the lease instead is this row: the
        # credential is revoked explicitly on every path out (below, and
        # `reconcile._revoke_credential`, retried for ever by
        # `reconcile.finished_rentals_with_live_credentials`).
        credential = await asyncio.to_thread(
            sandbox_identity.provision_rented_machine,
            db,
            owner_id=request.owner_id,
            pool_id=request.pool_id,
            node_id=f"rented-{rid[:12]}",
            label=f"rented {request.venue_id} for job {request.job_id}",
            platform=request.venue_id,
        )

        # 4. Acquire. Everything from here on may have created a machine.
        #
        # The minted identity travels WITH the request. A pull-style venue —
        # ECS user-data, a RunPod start command — has no exec channel, so the
        # token has to be in the machine's very first boot or the host never
        # learns who it is and can never enrol. `replace` rather than
        # mutation, because `CapacityRequest` is frozen and the row above was
        # opened from the caller's own copy; the caller's object is never
        # given a token it did not ask for.
        venue_asked = True
        acquired = await provider.acquire(
            request=replace(
                request,
                node_id=credential.node_id,
                machine_token=credential.raw_token,
            )
        )
        handle = acquired.provider_handle

        # 5. Re-gate, because the number that was approved is the QUOTE and
        #    this is the first sight of what the venue will actually charge.
        #    Without this, a venue that quotes $0.50 and answers $50.00 is
        #    recorded at $50.00 and never refused by anything -- the ceilings
        #    only ever ran against the quote.
        #
        #    Only a rate ABOVE the quote is re-examined: a cheaper answer was
        #    already approved at a higher number. The window figure this reads
        #    now includes this acquisition's own row, which makes the second
        #    look marginally stricter than the first. That is deliberate --
        #    the price has already moved against us once.
        answered = acquired.usd_per_hour
        if answered is not None and answered > request.quoted_usd_per_hour:
            await asyncio.to_thread(
                assert_within_budget, db, venue_id=request.venue_id,
                usd_per_hour=float(answered), settings=settings,
            )

        # 6. Record the handle in the move out of REQUESTED.
        moved = await asyncio.to_thread(
            _move_to_active, db, rid, handle=acquired.provider_handle,
            machine_id=credential.machine_id, usd_per_hour=answered,
        )
        if not moved:
            raise RuntimeError(
                f"could not record handle {acquired.provider_handle} against "
                f"{rid}: the row is no longer REQUESTED"
            )
        return rid
    except BaseException as exc:
        await _abandon(
            db, provider, rid, handle=handle, venue_asked=venue_asked,
            credential=credential, owner_id=request.owner_id, exc=exc,
        )
        raise
