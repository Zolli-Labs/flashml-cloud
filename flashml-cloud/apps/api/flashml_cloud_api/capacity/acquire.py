"""Rent one machine for one job.

The order is the design, not an implementation detail:

1. **Budget gate first.** Before a row, before a credential, before the
   venue is asked anything. A refusal must cost nothing and leave nothing.
2. **Open the row.** From here every step has somewhere durable to be
   recorded and a restart has something to find.
3. **Mint the credential into the SUBMITTER'S pool.** Not an isolation
   pool -- this machine is meant to share a pool with the user's other
   machines. Nothing here calls :func:`sandbox_identity.assert_pool_isolated`:
   it is an evaluation-session invariant, and applying it deliberately would
   forbid the thing this function exists to do. See the known limit below.
4. **Acquire**, and record the handle in the same update that moves
   REQUESTED -> ACTIVE.

Any failure from step 2 onwards records the failure and releases whatever the
provider created, because the row and its handle are the evidence of what went
wrong and the only route to the money still running.

THE FAILURE PATH RECORDS THE HANDLE BEFORE IT DESTROYS ANYTHING
---------------------------------------------------------------
A handle we hold in a local variable and nowhere else is a machine that bills
until somebody reads a log. So the failure path writes the handle to the row
*first*, then destroys, and only marks the row FAILED once nothing can still
be running. While something might be, the state is left exactly as it was --
REQUESTED -- because ``reconcile.unreleased_rows`` sweeps ``REQUESTED`` and
``ACTIVE`` and never looks at ``FAILED``. Marking a row FAILED while its
machine is alive is the one way to hide a billing machine from the only thing
that would have found it.

The credential is revoked on the same path, independently of the release --
``cleanup_session``'s rule, that neither failure may hide the other. It is not
tidiness: ``provision_sandbox_machine`` ends by asserting the pool holds
exactly the machine it just minted, so a dead machine left bound to the pool
makes the *next* acquisition fail for a reason that has nothing to do with it.

A KNOWN LIMIT, AND WHERE IT ACTUALLY LIVES
------------------------------------------
Reusing ``provision_sandbox_machine`` inherits its final
``assert_pool_isolated``. That assertion is one level below this module and
this module does not call it, but the effect reaches here anyway: renting into
a pool that already holds a machine is refused today, which is precisely the
case this feature is for. The refusal is clean -- it happens before the venue
is asked anything, so no money is spent, and the row records it -- and it is
pinned by ``test_a_pool_that_already_holds_a_machine_is_refused_today``.
Relaxing the assertion is out of scope in the design (§6); it needs its own
decision, because the assertion is what keeps an evaluation session's tasks
from being claimed by a second machine.
"""
from __future__ import annotations

import asyncio
from typing import Any

import psycopg

from flashml_cloud_api import sandbox_identity
from flashml_cloud_api.capacity.budget import assert_within_budget
from flashml_cloud_api.capacity.provider import CapacityRequest, ResourceProvider

__all__ = ["acquire_for_job"]

#: How much of a failure message is kept. `failure_detail` is for a human
#: reading a row, not for a stack trace.
_DETAIL_MAX = 2000


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


def _record_handle(db: psycopg.Connection, rid: str, handle: str) -> None:
    """Write the handle without moving the state.

    The row stays REQUESTED on purpose: this runs on the failure path, before
    anything has been destroyed, and REQUESTED is a state the reconciler
    sweeps. `provider_handle` is only ever filled in, never cleared -- the one
    thing that must not happen to a machine we are paying for is losing its
    name.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            update public.rented_capacity
               set provider_handle = coalesce(provider_handle, %s)
             where id = %s
            """,
            (handle, rid),
        )
    db.commit()


def _fail_row(
    db: psycopg.Connection, rid: str, code: str, detail: str, *,
    released: bool,
) -> None:
    """Close the row as FAILED. Only ever called once nothing can still run.

    ``released`` records that we destroyed something, so a FAILED row is not
    silently indistinguishable from one that failed before creating anything.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            update public.rented_capacity
               set state = 'FAILED', failure_code = %s, failure_detail = %s,
                   released_at = case when %s then now() else released_at end
             where id = %s
            """,
            (code, detail[:_DETAIL_MAX], released, rid),
        )
    db.commit()


def _note_failure(db: psycopg.Connection, rid: str, code: str, detail: str) -> None:
    """Record why, WITHOUT closing the row.

    Used when the venue may still be running something: the failure is worth
    reading, but the state has to stay somewhere the sweep looks.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            update public.rented_capacity
               set failure_code = %s, failure_detail = %s
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
    notes = [str(exc)]

    # 1. Durable before destructive. If this process dies on the next line,
    #    the row still names the thing that is billing.
    if handle:
        try:
            _record_handle(db, rid, handle)
        except Exception as note:  # pragma: no cover - defensive
            notes.append(f"[handle not recorded: {note}]")

    # 2. Stop the money. `destroyed` stays True when there was nothing to
    #    destroy -- the provider contract is that a failed `acquire` has
    #    already removed whatever it created.
    destroyed = True
    if handle:
        try:
            destroyed = bool((await provider.release(handle=handle)).destroyed)
        except Exception as note:
            destroyed = False
            notes.append(f"[release of {handle} raised: {note}]")
        if not destroyed:
            notes.append(f"[{handle} may still be running; left for the sweep]")

    # 3. Kill the identity, independently of the release: neither failure may
    #    hide the other, and the pool has to be given back either way or the
    #    next acquisition into it fails on an isolation assertion about a
    #    machine that no longer exists anywhere but here.
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
        if destroyed:
            _fail_row(db, rid, code, detail, released=bool(handle))
        else:
            # Something may still be alive at the venue. FAILED would hide it
            # from the sweep, which is the only thing that will ever destroy
            # it now.
            _note_failure(db, rid, code, detail)
    except Exception:  # pragma: no cover - defensive
        # The row is already open and, if there was a handle, already names
        # it. Losing the annotation is survivable; losing the original
        # exception is not.
        pass


async def acquire_for_job(
    db: psycopg.Connection,
    provider: ResourceProvider,
    settings: Any,
    *,
    request: CapacityRequest,
) -> str:
    """Rent one machine. Returns the ``rented_capacity`` row id."""
    # 1. Gate first. Raises BudgetRefused; nothing has been created.
    assert_within_budget(
        db, venue_id=request.venue_id,
        usd_per_hour=request.quoted_usd_per_hour, settings=settings,
    )

    # 2. The row, before the money.
    rid = _open_row(db, request, request.quoted_usd_per_hour)

    credential = None
    handle: str | None = None
    try:
        # 3. Identity, in the submitter's own pool.
        credential = await asyncio.to_thread(
            sandbox_identity.provision_sandbox_machine,
            db,
            owner_id=request.owner_id,
            pool_id=request.pool_id,
            node_id=f"rented-{rid[:12]}",
            label=f"rented {request.venue_id} for job {request.job_id}",
        )

        # 4. Acquire, then record the handle in the move out of REQUESTED.
        acquired = await provider.acquire(request=request)
        handle = acquired.provider_handle
        with db.cursor() as cur:
            cur.execute(
                """
                update public.rented_capacity
                   set state = 'ACTIVE', provider_handle = %s,
                       machine_id = %s, acquired_at = now(),
                       usd_per_hour = coalesce(%s, usd_per_hour)
                 where id = %s and state = 'REQUESTED'
                """,
                (acquired.provider_handle, credential.machine_id,
                 acquired.usd_per_hour, rid),
            )
            moved = cur.rowcount == 1
        db.commit()
        if not moved:
            raise RuntimeError(
                f"could not record handle {acquired.provider_handle} against "
                f"{rid}: the row is no longer REQUESTED"
            )
        return rid
    except BaseException as exc:
        await _abandon(
            db, provider, rid, handle=handle, credential=credential,
            owner_id=request.owner_id, exc=exc,
        )
        raise
