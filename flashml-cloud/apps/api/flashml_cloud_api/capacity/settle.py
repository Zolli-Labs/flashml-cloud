"""Give the machine back when the job it was rented for is over.

This is the PROMPT half of teardown, and it is explicitly not the guarantee.
``reconcile.reconcile_rented`` is the guarantee; everything here is an attempt
to stop the money minutes earlier than the sweep would, from the one place in
this API that learns a job has stopped.

WHERE "THE JOB FINISHED" ACTUALLY ARRIVES, AND WHY IT IS NOT A CALLBACK
-----------------------------------------------------------------------
It arrives on a **page poll**. The runtime is pull-based and this API is a
thin wrapper over it: a machine claims leases and commits work against the
coordinator, and the coordinator finishes a job without telling us. Nothing
calls in. Four places in this process ever observe a job's terminal state, and
every one of them is somebody's request:

* ``GET /v1alpha1/jobs`` and ``GET /v1alpha1/jobs/{id}`` in ``app.py``, where
  the coordinator's own answer is already in hand -- which is why
  ``db.sync_observed_job_states`` exists at all. The detail route used to call
  itself "THE ONLY PLACE A NON-FEDERATED JOB IS EVER OBSERVED TO HAVE STOPPED";
  that claim was annotated here and left standing there until 2026-08-12, and
  both sites now say the narrower true thing: it is the only hook that sees one
  job's end with that job's own detail body in hand, which is what its
  footprint and mirror writes need and the list route cannot give them;
* ``app._require_stopped``, behind ``DELETE /v1alpha1/jobs/{id}/artifacts``,
  which asks the coordinator directly *because* the local column is a cache
  and then writes what it learned. It is hooked here too: a caller deleting a
  finished job's artifacts has proved the job is over more thoroughly than any
  page poll does, and a rental still billing for it should not outlive the
  files;
* ``fedavg._finish``, for federated runs, which is deliberately NOT hooked --
  see below.

This list said "the only places" and named the two routes (plus ``fedavg``
below) until 2026-08-12, when the delete route was found to be a fourth. That
is the kind of error that matters in this file: the entire argument for the
sweep is a claim about how few places learn this fact, so a reader who checks
the list, finds a route it does not mention, and cannot tell whether the
omission was an oversight or a decision has lost the argument with it.

So the settle hook is only as reliable as somebody's browser. That is a fine
property for recording a status and a hopeless one for stopping money: a job
that finishes at 3am with nobody watching would bill until somebody opened the
console. The cost backstop therefore does NOT live here -- it lives in
``reconcile.unreleased_rows``'s ``JOB_FINISHED`` and ``IDLE`` branches. This
module is the optimisation; the sweep is the promise.

**Only one of those two branches needs no observer, and this line used to claim
both did.** ``JOB_FINISHED`` reads ``public.jobs.status``, the same cache the
hooks above write, so it fires only for a job somebody already looked at -- it
buys promptness over this module, not independence from it. ``IDLE`` is the one
that needs nobody: it reads the machine's own traffic through this API. It
consults the cached job status too, but since 2026-08-12 as a longer window
rather than as a veto (``reconcile.DEFAULT_LIVE_JOB_IDLE_AFTER_S``), so a
rental whose job row is stale is delayed by hours and not exempted for ever.
The distinction matters here because it is the answer to "what happens if
nobody ever opens the console again": ``IDLE``, at six hours, and nothing else.

(The federated driver, ``fedavg._finish``, observes its own runs ending
without any page being open. It is deliberately not hooked here: it runs in a
daemon thread with its own connection and no event loop, and the sweep covers
its rentals within ``idle_after_s`` anyway. If federated runs ever rent
capacity in volume, that is where the second hook goes.)

THE JOB BEING OVER SAYS NOTHING ABOUT WHAT THE MACHINE IS DOING
----------------------------------------------------------------
``reconcile.unreleased_rows``' ``JOB_FINISHED`` branch refuses to sweep a
rental whose machine holds a lease that could still be live, because a rented
machine sits in the submitter's own pool and the runtime is pull-based -- "the
job we rented it for is over" says nothing about what the machine is running
now. That guard was added after a reproduction; see
``test_capacity_reconcile.py::test_a_finished_job_never_sweeps_a_machine_
holding_a_live_lease``.

**This module asked nothing of the kind until 2026-08-12, and that was the
last known way an armed teardown could destroy a working machine.**
``rentals_for_jobs`` selected on ``job_id`` alone and ``release_capacity`` asks
about liveness by design, so a page load observing job A finishing destroyed
the machine rented for A **while it was mid-task on job B** in the same pool --
the exact scenario the sweep was fixed for, on the one path the fix did not
reach. The docstring below argues that a caller who has just watched a job
finish knows something no heartbeat can tell us, and that is true of the JOB.
It was never true of the MACHINE.

``rentals_for_jobs`` now carries the same ``public.attempts`` test, and carries
it as literally the same text: ``reconcile.WORK_IN_FLIGHT_SQL``, spliced into
both queries. It is not copied, and copying it would be the defect rather than
a shortcut -- one hand-synced copy going stale in one branch is how the guard
went missing from ``JOB_FINISHED``, and a second copy a module away is how it
went missing from here.

**What the guard costs is latency, not correctness**, and that asymmetry is the
whole reason it is affordable on the prompt path. A rental this hook declines
to settle is not lost: the sweep's ``JOB_FINISHED`` branch picks it up on a
later pass, once the machine's work has actually ended, with no page open and
no observer -- see ``reconcile``'s docstring on why that guard expires on its
own rather than exempting anything for ever. So the cost of declining is a few
more minutes of one machine's hourly rate; the cost of not declining is a
customer's running task, silently, and the evidence arrives afterwards.

EVERY CALL IS IDEMPOTENT AND EVERY CALL IS BEST EFFORT
------------------------------------------------------
A page left polling a finished job re-enters this on every poll, so the query
is written to find nothing the second time: a released rental is no longer in
``('REQUESTED', 'ACTIVE')``. And nothing here may raise. It is invoked from a
route whose job is to answer a user's read; a rented-capacity failure that
turned a finished job's page into a 500 would be a strictly worse outcome than
one settled by the sweep four minutes later.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

import psycopg

from flashml_cloud_api.capacity.provider import ResourceProvider
from flashml_cloud_api.capacity.reconcile import (
    WORK_IN_FLIGHT_SQL,
    release_capacity,
    work_in_flight_params,
)

__all__ = ["rentals_for_jobs", "settle_finished_jobs"]

log = logging.getLogger(__name__)


def rentals_for_jobs(
    db: psycopg.Connection, job_ids: Iterable[str]
) -> list[dict]:
    """Rentals still costing money on behalf of any of these jobs.

    One statement for a whole page of job ids, not one per job. The list route
    observes every visible job at once and is polled every two seconds, so a
    per-job loop would turn one indexed statement into N at exactly the
    frequency that makes N expensive -- the same argument
    ``db.sync_observed_job_states`` makes for its batched UPDATE.

    ``REQUESTED`` is selected alongside ``ACTIVE`` for the reason
    ``reconcile.unreleased_rows`` gives: a row that never learned its handle
    may still have created something at the venue. ``release_capacity`` knows
    what to do with a handleless row -- leave it sweepable -- and that decision
    belongs in one place, not two.

    **A rental whose machine holds work in flight is not returned**, and this
    predicate is the only thing between an armed hook and a running task. The
    job named on the ROW being over says nothing about what the MACHINE is
    doing: it sits in the submitter's own pool, the runtime is pull-based, and
    it may be mid-task on a different job in that pool right now. See the
    module docstring for the failure this closed and for why declining costs
    latency rather than correctness.

    The test is ``reconcile.WORK_IN_FLIGHT_SQL`` itself, not a copy of it. Its
    contract is why the ``rc`` alias below is not cosmetic and why the
    parameters are named rather than positional.
    """
    ids = [str(j) for j in job_ids if j]
    if not ids:
        return []
    with db.cursor() as cur:
        cur.execute(
            # An f-string for ONE reason -- the shared fragment below. Every
            # value is still a named parameter; nothing from a caller is
            # interpolated into this statement.
            f"""
            select rc.id, rc.venue_id, rc.job_id, rc.state, rc.provider_handle
              from public.rented_capacity rc
             where rc.state in ('REQUESTED', 'ACTIVE')
               and rc.job_id = any(%(job_ids)s)
               and not {WORK_IN_FLIGHT_SQL}
             order by coalesce(rc.acquired_at, rc.created_at)
            """,
            {"job_ids": ids, **work_in_flight_params()},
        )
        return [dict(r) for r in cur.fetchall()]


async def settle_finished_jobs(
    db: psycopg.Connection,
    providers: dict[str, ResourceProvider],
    *,
    job_ids: Iterable[str],
    dry_run: bool = True,
) -> list[str]:
    """Release every rental held for these finished jobs. Never raises.

    Call it only with jobs already known to be terminal: this asks nothing
    about the JOB's liveness, exactly as ``release_capacity`` documents. A
    caller who has just watched a job finish knows something no heartbeat can
    tell us, and a ``flashnode`` that goes on heartbeating after its work is
    done is precisely why the heartbeat cannot be asked.

    It does ask one thing about the MACHINE, and only one: whether it holds a
    lease that could still be live. That is a different question from liveness
    — it reads the attempt ledger, not a heartbeat column — and it is the
    difference between settling a rental and destroying a machine mid-task on
    another job in the same pool. ``rentals_for_jobs`` carries it.

    ``dry_run`` defaults to **True** here, the opposite of
    ``reconcile_rented``'s default and on purpose: that function is called by
    one wired loop that passes the deployment's flag explicitly, while this one
    is called from request handlers, where the cost of somebody forgetting the
    argument is a machine destroyed out of a page load. The safe direction is
    the default at the site where it is easiest to forget.
    """
    ids = [str(j) for j in job_ids if j]
    if not ids:
        # The overwhelmingly common case on the list route: a page of jobs
        # none of which has finished. Returning before the thread hop keeps
        # the cost of this hook at zero for a poll that has nothing to settle.
        return []
    try:
        rows = await asyncio.to_thread(rentals_for_jobs, db, ids)
    except Exception:  # noqa: BLE001 - the sweep is the guarantee, not this
        log.warning(
            "capacity settle: could not list rentals for finished jobs",
            exc_info=True,
        )
        return []

    settled: list[str] = []
    for row in rows:
        rented_id = str(row["id"])
        provider = providers.get(str(row["venue_id"]))
        if provider is None:
            # Identical handling to the sweep's, and for the same reason: a
            # venue with no adapter cannot be settled, and a row left visible
            # is the only thing that will ever say so.
            log.warning(
                "capacity settle: no provider configured for venue %s; %s "
                "left unreleased after job %s finished",
                row["venue_id"], rented_id, row["job_id"],
            )
            continue
        if dry_run:
            log.warning(
                "capacity settle: LOG ONLY -- job %s is over and would "
                "release rental %s (%s at venue %s). Nothing was destroyed.",
                row["job_id"], rented_id, row["provider_handle"],
                row["venue_id"],
            )
            continue
        try:
            if await release_capacity(db, provider, rented_id=rented_id):
                settled.append(rented_id)
        except Exception:  # noqa: BLE001 - one row, one job, one page load
            log.error(
                "capacity settle: releasing %s for finished job %s failed; "
                "the sweep will retry it",
                rented_id, row["job_id"], exc_info=True,
            )
    return settled
