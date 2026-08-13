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
  work, and pays for them anyway. ``sandbox_identity.LEASED_LIFECYCLE``
  records the same hazard as the reason rentals are minted ``leased`` rather
  than ``ephemeral``.
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

WHY LIVENESS ALONE IS A FAILURE BACKSTOP AND NOT A COST ONE
------------------------------------------------------------
Everything above stops a rental that BROKE. Nothing above stops a rental that
WORKED. ``flashnode`` keeps heartbeating after the job it was rented for has
finished — that is correct behaviour for a host agent and this module cannot
change it — so a rental that boots successfully refreshes ``last_seen_at`` for
ever, matches the quiet branch never, and **bills for ever.** The budget
ceilings do not close it either: ``budget.window_spend_usd`` sums
``usd_per_hour`` over rows *created* in a rolling window, which bounds the rate
of new commitments and stops nothing that is already running.

So two more inputs, both meaning *this machine has nothing left to do*, and
both governed by one window, ``idle_after_s``:

* **The job is over** (``JOB_FINISHED``). ``rented_capacity.job_id`` names the
  one job the rental was opened for, and a terminal ``public.jobs.status`` is
  the coordinator's own verdict that it has stopped. Measured from
  ``finished_at`` rather than acted on instantly, because that column is
  written by an observation — see ``db.sync_observed_job_states`` — and a
  rental whose fate turns on a single write being right deserves the same
  grace as one that merely looks idle.
* **Nothing has been claimed** (``IDLE``). ``public.attempts`` records every
  lease this API proxied to the machine. A rental whose machine holds no
  attempt that could still be live and has claimed nothing for
  ``idle_after_s`` is finished with its work whatever its heartbeat says. It
  is written by the machine's own traffic through this API rather than by
  anybody's browser — though see the paragraph on the job-state guard below
  for how much of that independence survives.

  It needs one more test to be safe in both directions, and the *pair* is the
  point. A machine must have **claimed something before** — otherwise a rented
  host still pulling a multi-gigabyte image, which has claimed nothing because
  it cannot yet, is destroyed mid-boot at fifteen minutes and paid for
  anyway. **Or** the rental must be past ``boot_grace_s`` — otherwise a
  machine that boots, enrols, heartbeats and is never handed any work at all
  matches no branch in this query and bills for ever, which is the same hole
  in a different corner. One without the other is a bug either way.

WORK IN FLIGHT GOVERNS **BOTH** COST BRANCHES
----------------------------------------------
**The dangerous half of IDLE is the long task**, and it is why "no claim for N
minutes" alone would be a machine-killer: a task that runs for three hours
claims once and then says nothing. The guard is the unresolved-attempt test —
an attempt with no ``resolved_at`` whose ``lease_deadline`` has not long passed
counts as work in flight, and a rental with work in flight is never swept for
cost. The deadline is the coordinator's own instant, refreshed from every
renewal it grants (``db.note_attempt_deadline``), and the grace allowed past it
is ``db.EXPIRY_GRACE_SECONDS`` — the same figure ``reconcile_expired_attempts``
uses to call an attempt dead, for the same reason and with the same argument
behind it.

That test was written inside the IDLE branch alone for one revision, and the
invariant this module asserts was false for exactly as long. ``JOB_FINISHED``
had no in-flight guard at all: a rental whose own job succeeded an hour ago,
on a machine holding an unresolved attempt for a **different** job with a
lease deadline ten minutes in the future, was selected and — armed — destroyed
mid-task. A rented machine sits in the submitter's own pool and is an eligible
claimant for that pool's OTHER jobs; the runtime is pull-based, so nothing
about "the job we rented it for" bounds what the machine may be running now.
The test is therefore computed once, as ``work_in_flight``, and read by both
cost branches.

It deliberately does **not** govern the liveness branches. A machine that has
been silent for ``quiet_after_s``, or whose credential is revoked, is not doing
the work its unresolved attempt describes — that attempt is the wreckage of
it, and ``reconcile_expired_attempts`` will resolve it as expired. Extending
the guard there would exempt precisely the rentals that broke mid-task, which
is the case this sweep was written for first.

**A null deadline is bounded, not eternal**, and that is a correction rather
than a softening. "We never learned when this lease ends" must not read as "it
is finished" — ``reconcile_expired_attempts`` refuses to resolve such a row for
the same reason. But for one revision it read as "it never finishes", which is
worse: an unresolved attempt with a null ``lease_deadline`` made its rental
match no branch here **at all**, for ever, at any ``idle_after_s`` down to one
second — and since a job nobody polls never goes terminal either, nothing in
the system could stop that machine billing. So a null-deadline attempt counts
as work in flight only until ``claimed_at`` passes
``DEFAULT_UNKNOWN_DEADLINE_MAX_S``, which is argued at that constant.

IDLE ALSO ASKS WHETHER THE JOB IS STILL RUNNING, AND THAT COSTS SOMETHING
--------------------------------------------------------------------------
The in-flight test protects a machine *holding* a lease. A machine waiting for
its next one holds nothing, and there are ordinary reasons to wait longer than
``idle_after_s``: federated aggregation between rounds, a long checkpoint
upload, a straggler barrier, a coordinator with nothing queued this instant.
Reproduced before this guard existed — one attempt claimed twenty minutes ago
and resolved, ``public.jobs`` still ``RUNNING`` — the machine was selected as
IDLE and, armed, destroyed between two tasks of a job that was still going.
So IDLE now also requires that ``rc.job_id`` name no job in a non-terminal
state.

**The price is real and it is paid in money.** ``public.jobs.status`` is a
cache written only when somebody looks (``db.sync_observed_job_states``, from
the two job routes and ``app._require_stopped``), and that staleness is the
very thing IDLE was invented to route around: a job that finished at 3am with
nobody watching reads ``RUNNING`` for ever. With this guard such a rental is no
longer caught by IDLE, and it is not caught by ``JOB_FINISHED`` either, since
that reads the same stale column. What still catches it: anybody opening the
console (both routes settle *and* sync), the machine going quiet, or its
credential being revoked. What does not: nothing else. A rental for a
never-observed finished job bills until one of those happens.

That is the trade, taken deliberately in the one direction this module can
take it. Destroying a machine under a running task is silent, irreversible,
and takes a customer's job with it; over-paying is loud — the rental is a row
anybody can list, and one page load ends it. Between a failure that announces
itself and one that does not, this module takes the one that does. But it is
a real bill and it is not bounded by anything here, which is why it is written
down rather than left implied. The clean way to buy both back is to make the
column fresh rather than to stop reading it — have the deployed loop ask the
coordinator for the state
of the jobs named by unreleased rentals before it sweeps (a handful of ids,
once per interval), at which point ``JOB_FINISHED`` needs no observer either.
That is a change to ``app.py``'s loop, not to this predicate, and it is not
done yet.

**What IDLE still cannot survive**, stated plainly because it is the residual
risk of this whole module: ``db.record_attempt`` is best effort on the claim
path. A machine whose claims stop being recorded while it goes on working
looks idle from here. That failure needs the database to be unreachable from
the claim path while it is reachable from this sweep, and it is the reason the
sweep ships log-only by default (see :func:`reconcile_rented`).

TEARDOWN IS TWO THINGS, AND ``cleanup_session`` IS THE MODEL
------------------------------------------------------------
*Both halves, independently.* It kills the sandbox **and** revokes the
credential, in separate try blocks, so neither failure can hide the other.
Teardown here is the same two things: destroy the machine at the venue, and
kill the identity it was renting under. A provider that will not answer must
still leave a legible row behind and must still give the pool back; one row's
failure must not end the sweep for the rows after it; and a failure to
*record* must never be mistaken for a failure to *destroy*.

**The credential half is not tidiness, and since D6 (2026-08-12) it is the
only thing there is.** A rented machine's identity is a LEASE, not a deed: true
while we hold the hardware and false the moment we give it back. It is minted
``lifecycle = 'leased'`` (migration 0023), which says exactly that and is
deliberately outside ``expire_stale_ephemeral_machines`` — that sweep measures
from ``coalesce(last_seen_at, created_at)`` and would revoke a rented host that
is still pulling its image. **Nothing expires a lease on a timer.** Ending it
is this module's job, on every path a rental ends.

Left alive after release it is a valid machine token, bound to a user's
workspace, for hardware a third party has since rented. That used to be
self-correcting by accident: the leftover binding made
``provision_sandbox_machine``'s closing ``assert_pool_isolated`` refuse the
NEXT rental into that pool, so renting once poisoned the pool loudly. D6
replaced that path with ``provision_rented_machine``, which asserts nothing —
renting into a pool that already holds the submitter's laptop is the entire
point of the feature. So the next rental now succeeds and the stale credential
is *silent*. The revoke below is the mechanism, not a backstop behind one.

Because that half can fail on its own, it is swept on its own too:
:func:`finished_rentals_with_live_credentials` finds rentals that are over
and whose credential is not, and the sweep revokes them. Without it, a revoke
that failed on the one call that mattered would never be retried — the row is
RELEASED and out of :func:`unreleased_rows` for good, and no other sweep in
this system looks at a leased machine at all.

A LEASE NO RENTAL ROW EVER NAMED IS FOUND BY THE MACHINE, NOT BY THE RENTAL
----------------------------------------------------------------------------
Every query above keys on ``rented_capacity``, and there is a window in which
the row does not name the machine yet. ``provision_rented_machine`` COMMITS
the machine, its pool binding and its token in a transaction of its own;
``rented_capacity.machine_id`` is written afterwards, by
``acquire._move_to_active`` on success or ``acquire._record_evidence`` on
failure. Every *exception* in that window is covered — ``_abandon`` catches
``BaseException`` — but a process death is not, and what it leaves is a
``leased``, unrevoked, pool-bound machine named on no rental row at all.

Nothing above can reach it. :func:`finished_rentals_with_live_credentials`
inner-joins ``rc.machine_id`` and cannot select it; :func:`unreleased_rows`
sees a null machine and calls the rental ABANDONED, where
:func:`release_capacity` stops at the no-handle branch — and even past that,
:func:`_revoke_credential` returns early on a falsy ``machine_id``;
``expire_stale_ephemeral_machines`` excludes ``leased`` by design. The benign
version of this leaves a token nobody holds. The bad version — death after the
venue answered — leaves a live pod holding a live token, with neither the
handle nor the machine named anywhere.

:func:`orphaned_leases` is the answer, and it is keyed on the MACHINE:
``lifecycle = 'leased'`` with no ``REQUESTED``/``ACTIVE`` rental pointing at
it. The machine row is the only thing that always exists, because the mint's
own transaction is what creates it. Writing ``machine_id`` onto the rental
inside that transaction was the other candidate and it is a weaker fix: it
would make the identity module write the capacity module's table, it could not
reach a lease that is ALREADY orphaned, and it does not even close the window
it aims at — the row it repairs stays ``REQUESTED`` with no handle for ever,
and the no-handle branch deliberately refuses to revoke that row's credential.

Its one real hazard is the opposite mistake: revoking the token of an
acquisition that is still in flight, which would kill a machine mid-boot. So
the query is aged from ``machines.created_at`` by ``boot_grace_s`` — the same
figure, and the same hazard, as the NEVER_SEEN branch above: how long a rented
host may take to boot, pull a multi-gigabyte image and enrol.

**That half runs in log-only mode as well**, and it is the one thing here that
does. ``dry_run`` exists because destroying a machine is irreversible and can
take a running job with it; ending the lease of a rental that is already over
destroys nothing at any venue, touches only our own row and our own token, and
is the entire mechanism keeping a lease from outliving the hardware. Leaving
it behind the gate meant the shipped default — log only — disabled the only
thing that ever ends a lease, on the exact deployment least likely to be
watched. See :func:`reconcile_rented` for the line this draws.

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
from flashruntime.protocol.v1alpha1 import JobState

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import sandbox_identity
from flashml_cloud_api.capacity.provider import ResourceProvider

__all__ = [
    "DEFAULT_ABANDONED_AFTER_S",
    "DEFAULT_BOOT_GRACE_S",
    "DEFAULT_IDLE_AFTER_S",
    "DEFAULT_QUIET_AFTER_S",
    "DEFAULT_UNKNOWN_DEADLINE_MAX_S",
    "TERMINAL_JOB_STATES",
    "finished_rentals_with_live_credentials",
    "orphaned_leases",
    "reconcile_rented",
    "release_capacity",
    "teardown_plan",
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
# set carries its own SQL literal -- seven of them:
#
#   * `unreleased_rows`, `_mark_released` and `orphaned_leases`, here;
#   * `acquire._close_failed`, and `acquire._keep_sweepable` twice (state
#     and `released_at`);
#   * the partial index `rented_capacity_unreleased_idx` in migration 0022.
#
# `finished_rentals_with_live_credentials` then spells the COMPLEMENT,
# ('RELEASED', 'FAILED'), which has to keep agreeing with all seven.
# `orphaned_leases` spells the set itself, negated -- a machine no sweepable
# rental names -- so it is the site that would silently start revoking live
# rentals if the set here ever grew a state and it was not updated.
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

#: How long a rental with **nothing left to do** is given: the job it was
#: opened for is over, or its machine has stopped claiming work. This is the
#: COST backstop and the only window here that ever stops a healthy rental;
#: the other three stop broken ones. Fifteen minutes, matching
#: ``DEFAULT_QUIET_AFTER_S`` and ``db.EXPIRY_GRACE_SECONDS`` so an operator
#: carries one number rather than three, and bounding the waste after a
#: finished job to a quarter-hour of one machine's rate.
DEFAULT_IDLE_AFTER_S = 15 * 60.0

#: How long an unresolved attempt whose ``lease_deadline`` was never recorded
#: counts as work in flight, measured from ``claimed_at``.
#:
#: A null deadline is an attempt claimed before migration 0015, or one whose
#: claim response ``db.record_attempt`` could not parse a ``lease.deadline``
#: out of. It means we do not know when the lease ends, and "we do not know"
#: must never read as "it is finished" — ``reconcile_expired_attempts``
#: refuses to resolve exactly these rows, for exactly that reason.
#:
#: It must not read as "it never finishes" either, and for one revision it
#: did: an unresolved null-deadline attempt made its rental unsweepable by
#: every branch here, for ever, at any ``idle_after_s``. Paired with a job
#: nobody polls — which never goes terminal — that is a machine nothing in
#: this system can stop billing, reached from the cautious side of the same
#: judgement.
#:
#: SIX HOURS, and the number is an argument about evidence rather than about
#: task length. Every heartbeat the coordinator accepts writes the renewed
#: deadline (``db.note_attempt_deadline``) and ``flashnode`` renews at a third
#: of the lease window, so six hours of a live lease is hundreds of separate
#: chances to learn a deadline. A row that has taken none of them is not
#: evidence of work in flight; it is a record we never managed to write. It is
#: also far longer than any single task this system has run, so a machine that
#: really is mid-task keeps its cover — and if it is, its heartbeats are
#: filling this column in anyway, which moves it out of this case entirely.
DEFAULT_UNKNOWN_DEADLINE_MAX_S = 6 * 60 * 60.0

#: Job states that mean the work has stopped, from the protocol package rather
#: than a tuple of strings kept here — the same reasoning
#: ``app.is_terminal_state`` gives: the set is a wire fact and a private copy
#: of it drifts the first time the runtime adds one. A state this API does not
#: recognise is not terminal, which is the safe direction: the cost of missing
#: one is a rental swept by a later window instead of this one, while the cost
#: of guessing is a machine destroyed under a running job.
TERMINAL_JOB_STATES = tuple(s.value for s in JobState if s.terminal)

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
    idle_after_s: float = DEFAULT_IDLE_AFTER_S,
) -> list[dict]:
    """Rentals still costing money that nothing is using.

    The ``case`` is the whole design and is written as a case rather than a
    chain of ``or``s so that the allowances cannot silently overlap: each row
    is judged by exactly one of them, and which one is a fact about the
    machine and its work, not about the row's age. **A machine that has
    heartbeated within ``quiet_after_s`` matches no LIVENESS branch here at
    all** — that is the clause standing between this sweep and a running
    training job, and the two branches that can select such a machine anyway
    (``JOB_FINISHED`` and ``IDLE``) do it on evidence that the work is over,
    never on how long the rental has existed. Both of them are governed by
    ``work_in_flight``, computed once in the ``rental`` CTE: a rental whose
    machine holds a lease that could still be live is not swept for cost, for
    any job, whatever its own job's state says. See the module docstring for
    what each window means and why they differ.

    It returns the reason as ``sweep_reason`` rather than a bare boolean, and
    that is not decoration: it is the whole content of a log-only deployment's
    report (:func:`teardown_plan`), and computing it in the same expression
    that does the selecting is what stops the report and the decision from
    drifting apart. ``null`` means "not sweepable" and is filtered out.

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
            with rental as (
              select rc.id, rc.venue_id, rc.state, rc.provider_handle,
                     rc.machine_id, rc.job_id,
                     m.id as machine_row_id,
                     m.status as machine_status, m.last_seen_at,
                     coalesce(rc.acquired_at, rc.created_at) as billing_since,
                     -- WORK IN FLIGHT. Computed once here rather than inside a
                     -- branch, because it governs BOTH cost branches and
                     -- writing it twice is how one of them loses it: it lived
                     -- in IDLE alone for a revision, and JOB_FINISHED
                     -- destroyed machines holding a live lease for another
                     -- job in that pool. See the module docstring.
                     --
                     -- A recorded deadline is the coordinator's own instant
                     -- and is trusted until `lease_grace` past it. A NULL
                     -- deadline is not a deadline of infinity: it is a lease
                     -- whose end we never learned, trusted from `claimed_at`
                     -- for `unknown_deadline` and no longer, or the rental
                     -- becomes unsweepable for ever.
                     exists (
                       select 1 from public.attempts a
                        where a.machine_id = rc.machine_id
                          and a.resolved_at is null
                          and case when a.lease_deadline is not null
                                   then a.lease_deadline > now()
                                        - make_interval(
                                            secs => %(lease_grace)s)
                                   else a.claimed_at > now()
                                        - make_interval(
                                            secs => %(unknown_deadline)s)
                              end
                     ) as work_in_flight,
                     -- Has this machine ever been handed anything at all?
                     exists (
                       select 1 from public.attempts a
                        where a.machine_id = rc.machine_id
                     ) as has_ever_claimed,
                     -- ...and anything lately?
                     exists (
                       select 1 from public.attempts a
                        where a.machine_id = rc.machine_id
                          and a.claimed_at
                              > now() - make_interval(secs => %(idle)s)
                     ) as claimed_recently,
                     -- The coordinator's verdict, as last observed: over.
                     exists (
                       select 1 from public.jobs j
                        where j.id = rc.job_id
                          and j.status = any(%(terminal)s)
                          and coalesce(j.finished_at, j.created_at)
                              < now() - make_interval(secs => %(idle)s)
                     ) as job_is_over,
                     -- ...and its opposite, which is NOT the negation of the
                     -- column above: a job that finished thirty seconds ago is
                     -- neither `job_is_over` (the grace has not passed) nor
                     -- `job_still_live`. A rental whose job_id names no
                     -- `public.jobs` row at all -- a coordinator-side id -- is
                     -- not live either, which is what keeps IDLE working for
                     -- the rentals this API never recorded a job row for.
                     exists (
                       select 1 from public.jobs j
                        where j.id = rc.job_id
                          and j.status <> all(%(terminal)s)
                     ) as job_still_live
                from public.rented_capacity rc
                left join public.machines m on m.id = rc.machine_id
               where rc.state in ('REQUESTED', 'ACTIVE')
            )
            select id, venue_id, state, provider_handle, machine_id, job_id,
                   machine_status, last_seen_at, billing_since, sweep_reason
              from (
                select r.*,
                       case
                         -- A revoked credential can never claim our work
                         -- again, so the rental is waste from this instant and
                         -- gets no allowance at all. Without this branch,
                         -- revoking would push a row we are mid-way through
                         -- releasing back OUT of the sweep until it aged into
                         -- a window below -- the one thing a failed destroy
                         -- must not do.
                         when r.machine_row_id is not null
                              and r.machine_status = 'revoked'
                           then 'REVOKED_CREDENTIAL'
                         -- THE COST BACKSTOP, first half: the one job this
                         -- rental was opened for has stopped. Nothing above
                         -- this line stops a rental that worked, because a
                         -- flashnode goes on heartbeating after its job ends.
                         --
                         -- `not work_in_flight` is the whole of fix #1. The
                         -- job named on the ROW being over says nothing about
                         -- what the MACHINE is doing: it sits in the
                         -- submitter's own pool, the runtime is pull-based,
                         -- and it may hold a live lease for a different job in
                         -- that pool right now.
                         when r.job_is_over and not r.work_in_flight
                           then 'JOB_FINISHED'
                         -- No machine bound at all: acquisition never got as
                         -- far as binding one, or an ACTIVE row whose machine
                         -- row was since deleted. Not a REQUESTED row with a
                         -- revoked credential -- acquire.py now records
                         -- machine_id on its failure paths too, so that row is
                         -- caught by the revoked branch above instead.
                         when r.machine_row_id is null
                              and r.billing_since < now()
                                  - make_interval(secs => %(abandoned)s)
                           then 'ABANDONED'
                         -- It spoke, and then it stopped. Deliberately NOT
                         -- guarded on work_in_flight: an unresolved attempt on
                         -- a machine that has been silent for a quarter of an
                         -- hour is the wreckage of the task, not the task.
                         when r.machine_row_id is not null
                              and r.last_seen_at is not null
                              and r.last_seen_at
                                  < now() - make_interval(secs => %(quiet)s)
                           then 'QUIET'
                         -- It has never spoken: still booting, or never will.
                         when r.machine_row_id is not null
                              and r.last_seen_at is null
                              and r.billing_since
                                  < now() - make_interval(secs => %(boot)s)
                           then 'NEVER_SEEN'
                         -- THE COST BACKSTOP, second half: the machine holds
                         -- nothing that could still be running and has claimed
                         -- nothing for `idle_after_s`. LAST on purpose --
                         -- everything above is a stronger statement about the
                         -- same row, and a machine that never booted should
                         -- report NEVER_SEEN rather than "idle".
                         --
                         -- The four tests are each load-bearing:
                         --
                         -- * has worked, OR is past the boot allowance.
                         --   Without the first half a rented host pulling a
                         --   multi-gigabyte image is destroyed mid-boot at
                         --   `idle_after_s`; without the second, a machine
                         --   that enrols, heartbeats and is never given any
                         --   work bills for ever, matching no branch at all.
                         -- * nothing in flight. This is what keeps a
                         --   three-hour task -- one claim, then silence --
                         --   from reading as an idle machine.
                         -- * the job is not still running. A machine BETWEEN
                         --   two tasks holds no lease at all, so the in-flight
                         --   test cannot see it, and fifteen minutes of
                         --   waiting is ordinary: federated aggregation
                         --   between rounds, a long checkpoint upload, a
                         --   straggler barrier, a coordinator with nothing
                         --   queued. Without this, IDLE destroys a machine
                         --   mid-job -- reproduced, and the reason this line
                         --   exists.
                         --
                         --   AND IT COSTS: `jobs.status` is written only when
                         --   somebody looks, so a job that finished with
                         --   nobody watching reads RUNNING for ever and its
                         --   rental is now caught by neither cost branch --
                         --   the exact staleness IDLE was invented to route
                         --   around. A bill that one page load ends, against
                         --   a destruction that nothing undoes; taken
                         --   deliberately, argued at length in the module
                         --   docstring, which also names the fix that buys
                         --   both back.
                         -- * nothing claimed lately. The actual idleness.
                         when r.machine_id is not null
                              and (r.has_ever_claimed
                                   or r.billing_since < now()
                                      - make_interval(secs => %(boot)s))
                              and not r.work_in_flight
                              and not r.job_still_live
                              and not r.claimed_recently
                           then 'IDLE'
                       end as sweep_reason
                  from rental r
              ) candidate
             where sweep_reason is not null
             order by billing_since
            """,
            # `secs` is the one make_interval() argument typed `double
            # precision`, which is why every window here is expressed in
            # seconds exactly as in `budget.window_spend_usd`.
            #
            # `lease_grace` is deliberately NOT a parameter of this function.
            # It is not a policy this module gets to choose: it is how long
            # after a lease deadline the coordinator's own rule says the
            # attempt is dead, and `db.reconcile_expired_attempts` already
            # argues the number. Two copies of that judgement would let a
            # sweep call an attempt live that the ledger has already resolved
            # as expired, or the reverse.
            #
            # `unknown_deadline` is not a parameter either, but for the
            # opposite reason: it is not a window an operator tunes per sweep,
            # it is how long this module is willing to call an unmeasurable
            # attempt live. One number, argued once, at the constant.
            {
                "abandoned": float(abandoned_after_s),
                "quiet": float(quiet_after_s),
                "boot": float(boot_grace_s),
                "idle": float(idle_after_s),
                "lease_grace": float(dbmod.EXPIRY_GRACE_SECONDS),
                "unknown_deadline": float(DEFAULT_UNKNOWN_DEADLINE_MAX_S),
                "terminal": list(TERMINAL_JOB_STATES),
            },
        )
        return [dict(r) for r in cur.fetchall()]


def finished_rentals_with_live_credentials(
    db: psycopg.Connection,
) -> list[dict]:
    """Rentals that are over and whose credential is not.

    **This query is what makes a rental's identity a lease rather than a
    deed.** ``_revoke_credential`` is best effort, a row that reached
    ``RELEASED`` is out of :func:`unreleased_rows` for good, and a ``leased``
    machine is outside ``expire_stale_ephemeral_machines`` by design (a rented
    host has no heartbeat until it has finished booting). So this is the only
    thing in the system that comes back for a credential whose rental is over:
    without it, one failed revoke leaves a working machine token bound to a
    user's workspace, for hardware somebody else has since rented, for ever.

    Until D6 a leftover binding was also caught by accident, because it made
    the next rental into that pool fail an isolation assertion. That path is
    gone — renting into a pool that already holds a machine is now the point —
    so the accident is gone with it and nothing else is watching.

    A binding with no live token still counts: ``revoke_sandbox_machine``
    unbinds on every call including ones where the row is already revoked,
    which is exactly the half-done state a crashed revoke leaves behind.

    Both terminal states are selected. ``FAILED`` is not a lesser case: an
    acquisition that minted a credential and then failed is precisely the row
    whose machine never existed at a venue, and its lease has to end too.
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


def orphaned_leases(
    db: psycopg.Connection, *, boot_grace_s: float = DEFAULT_BOOT_GRACE_S
) -> list[dict]:
    """Leases no rental row still claims — INCLUDING the ones none ever named.

    The sibling of :func:`finished_rentals_with_live_credentials`, keyed on the
    machine instead of the rental, and that is the whole of it: a rental-keyed
    query cannot see a machine the rental never recorded. ``machines`` is where
    a lease is always written, because ``provision_rented_machine`` commits the
    machine, the binding and the token together; ``rented_capacity.machine_id``
    is a separate, later write, and a process death between the two leaves a
    live credential nothing else in this system can reach. The module docstring
    argues why this shape was chosen over closing the window at the mint.

    ``lifecycle = 'leased'`` is the only membership test that matters —
    ``provision_rented_machine`` is the sole writer of that value, so every row
    it selects is a lease this control plane minted, and no laptop, no sandbox
    worker and no device-code enrolment can appear here.

    A live rental is excluded by the one predicate that is really a claim about
    the world: a ``REQUESTED`` or ``ACTIVE`` row naming this machine means the
    lease is current. Anything else — a terminal row, or NO row — means it is
    not. The terminal case overlaps
    :func:`finished_rentals_with_live_credentials` on purpose: this query is
    then a complete backstop rather than one that depends on that join being
    right, and :func:`reconcile_rented` revokes each machine once per pass.

    ``boot_grace_s`` IS THE SAFETY PROPERTY, not a tuning knob. Measured from
    ``machines.created_at`` — the mint's own commit, which is exactly when the
    window opens — because an acquisition in flight looks identical to an
    abandoned one from here: both are a leased machine with no row naming it.
    Revoking a machine whose ``provider.acquire`` is still waiting for it to
    register kills it mid-boot, on hardware already paid for, which is the
    hazard ``DEFAULT_BOOT_GRACE_S`` already exists to measure. Cheaper to be
    slow: an orphan costs a token, and killing a live acquisition costs a boot.

    Half-revoked counts, the same as next door: ``revoke_sandbox_machine``
    unbinds on every call, so a crashed revoke leaves a revoked row still bound
    to a pool, and for an orphan there is nothing else that would ever unbind
    it.

    ``node_id`` is returned because it is the only correlation there is: the
    acquisition names it ``rented-{rented_id[:12]}``, so an operator reading
    the log can find the rental row that should have named this machine.
    Oldest first.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select m.id as machine_id, m.owner_id, m.node_id, m.created_at
              from public.machines m
             where m.lifecycle = %(leased)s
               and m.created_at < now() - make_interval(secs => %(boot)s)
               and (m.status <> 'revoked'
                    or exists (select 1 from public.machine_pools mp
                                where mp.machine_id = m.id))
               and not exists (
                     select 1 from public.rented_capacity rc
                      where rc.machine_id = m.id
                        and rc.state in ('REQUESTED', 'ACTIVE'))
             order by m.created_at
            """,
            {
                "leased": sandbox_identity.LEASED_LIFECYCLE,
                "boot": float(boot_grace_s),
            },
        )
        return [dict(r) for r in cur.fetchall()]


def _plan_entries(
    rows: list[dict], providers: dict[str, ResourceProvider]
) -> list[dict]:
    """One legible line per row an armed sweep would act on.

    ``provider_configured`` is in here because the two ways a rental survives
    a sweep look identical from outside and are not the same problem at all: a
    row nothing selected is fine, while a row selected at a venue with no
    adapter is money nothing in this process can stop. See
    :func:`reconcile_rented`.
    """
    return [
        {
            "rented_id": str(row["id"]),
            "venue_id": str(row["venue_id"]),
            "state": row["state"],
            "reason": row["sweep_reason"],
            "job_id": row.get("job_id"),
            "provider_handle": row.get("provider_handle"),
            "billing_since": str(row.get("billing_since")),
            "provider_configured": str(row["venue_id"]) in providers,
        }
        for row in rows
    ]


def _would_destroy(entry: dict) -> bool:
    """Could an ARMED pass actually stop this row's money right now?

    Two things have to be true before ``release_capacity`` can even ask a
    venue: somebody configured an adapter for it, and the row names the
    machine. A row missing either is not destroyed by an armed sweep -- it is
    annotated (``NO_HANDLE``) or skipped with a warning, and left exactly
    where it is on purpose.

    Not a prediction that the destroy will SUCCEED. A venue that refuses is
    unknowable until it is asked, and the armed pass records that on the row
    (``NOT_DESTROYED``). This is the split between what would be attempted and
    what could not be.
    """
    return bool(entry["provider_configured"]) and bool(entry["provider_handle"])


def _report_plan(
    rows: list[dict], providers: dict[str, ResourceProvider]
) -> None:
    """The log-only pass's whole output. Writes nothing anywhere else.

    The headline counts what ARMING WOULD DESTROY, not how many rows the query
    matched. It used to say "would destroy N rental(s)" for every selected
    row, which over-states it in the one direction that matters: an operator
    reads that number to decide whether to arm the sweep, and today -- with an
    empty provider registry -- the true answer for every row is zero while the
    old headline would have claimed all of them. The rows an armed pass could
    not touch are still reported, separately and by name, because they are the
    more urgent half: money nothing in this process can stop.
    """
    plan = _plan_entries(rows, providers)
    if not plan:
        return
    destroyable = [e for e in plan if _would_destroy(e)]
    unreachable = [e for e in plan if not _would_destroy(e)]
    log.warning(
        "capacity reconcile: LOG ONLY -- of %d rental(s) selected, arming "
        "would destroy %d and could not destroy %d (no configured venue "
        "adapter, or no provider_handle to name the machine with; an armed "
        "pass annotates those and leaves them). No machine was destroyed and "
        "no row annotated. Arm the sweep (RENTED_CAPACITY_DESTROY=true) to "
        "stop this money. WOULD DESTROY: %s. CANNOT: %s",
        len(plan), len(destroyable), len(unreachable), destroyable, unreachable,
    )


def teardown_plan(
    db: psycopg.Connection,
    providers: dict[str, ResourceProvider],
    *,
    quiet_after_s: float = DEFAULT_QUIET_AFTER_S,
    boot_grace_s: float = DEFAULT_BOOT_GRACE_S,
    abandoned_after_s: float = DEFAULT_ABANDONED_AFTER_S,
    idle_after_s: float = DEFAULT_IDLE_AFTER_S,
) -> list[dict]:
    """What an armed sweep would destroy right now. Reads only, writes never.

    This is what a log-only deployment ships instead of a teardown, and it is
    the evidence an operator reads before arming one: the same rows, selected
    by the same expression, with the reason each was selected. It exists as a
    function of its own rather than as a flag inside the sweep so that the
    report cannot quietly diverge from the decision -- both go through
    :func:`unreleased_rows` and neither has a predicate of its own.
    """
    rows = unreleased_rows(
        db, quiet_after_s=quiet_after_s, boot_grace_s=boot_grace_s,
        abandoned_after_s=abandoned_after_s, idle_after_s=idle_after_s,
    )
    return _plan_entries(rows, providers)


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
    what comes back for the ones this loses, and :func:`orphaned_leases` for
    the ones no rental row names.

    ``row`` is a rental row on every path but one: :func:`orphaned_leases`
    returns machine rows, which carry no rental ``id`` because there is no
    rental to carry — hence the ``node_id`` fallback in the failure log, which
    is what an operator can correlate back to an acquisition.
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
            machine_id, row.get("id") or row.get("node_id"), exc_info=True,
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
        # out of `unreleased_rows` for good, and rentals are minted `leased`,
        # which no heartbeat sweep in this schema selects, so no other sweep
        # comes for the machine either.
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

    # 3. Kill the identity, whatever happened above. The lease ends when the
    #    rental does, not when the venue gets around to confirming it: a
    #    machine that outlived its destroy must not keep a working token, and
    #    the pool has to be given back either way. Nothing else will notice if
    #    it is not -- see this module's docstring on what D6 removed.
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
    idle_after_s: float = DEFAULT_IDLE_AFTER_S,
    dry_run: bool = False,
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

    ``dry_run`` REPORTS INSTEAD OF DESTROYING, and it is what the deployed loop
    passes by default (``settings.rented_capacity_destroy`` is false until an
    operator says otherwise). No machine is destroyed and no row is annotated
    -- the operator reading the report must be reading it BEFORE anything
    happened, not after. The failure this defends against is the one this
    module cannot undo: a wrongly destroyed machine takes a running job with
    it, and the evidence that it was wrong arrives afterwards. Note the
    asymmetry it accepts in exchange -- a dry run stops no money at all, so
    running one is a decision to keep paying while the report is read.

    **THE CREDENTIAL SWEEP RUNS EITHER WAY**, and it is the one exception.
    Revoking the credential of a rental that is already RELEASED or FAILED
    destroys nothing at any venue: it touches our own row and our own token,
    both of which describe hardware we have already given up. There is no
    irreversible act to hold back and no report to read first. Meanwhile it is
    the ONLY thing in this system that ends a lease -- since D6 removed the
    isolation assertion that used to make a leftover binding fail loudly, a
    stale lease is silent -- so leaving it behind this gate meant the shipped
    default disabled the mechanism entirely on the deployment least likely to
    be watched. The gate is about destroying machines; it was never about
    keeping dead identities alive.

    That sweep is TWO queries. :func:`finished_rentals_with_live_credentials`
    is keyed on the rental, and :func:`orphaned_leases` on the machine, because
    a lease whose rental row never recorded it is invisible to anything keyed
    on the rental -- and a log-only deployment is exactly where a crashed
    acquisition is likeliest to go unnoticed, so the second belongs on this
    side of the gate for the same reason as the first. ``boot_grace_s`` reaches
    it: an acquisition still in flight is indistinguishable from a dead one
    here, so the orphan query will not touch a machine younger than the time a
    rented host is allowed to spend booting.

    The parameter defaults to ``False`` rather than ``True`` because this
    function's contract is "stop the money"; the DEPLOYMENT decides whether it
    is armed, and there is exactly one production caller (``app.py``) which
    defaults to disarmed. A test calling this bare is asking for the sweep it
    is testing.
    """
    settled: list[str] = []

    try:
        rows = await asyncio.to_thread(
            unreleased_rows, db,
            quiet_after_s=quiet_after_s, boot_grace_s=boot_grace_s,
            abandoned_after_s=abandoned_after_s, idle_after_s=idle_after_s,
        )
    except Exception:  # noqa: BLE001 - a failed read must not end the pass
        log.error(
            "capacity reconcile: could not list unreleased rentals",
            exc_info=True,
        )
        rows = []

    if dry_run:
        _report_plan(rows, providers)
    else:
        for row in rows:
            rented_id = str(row["id"])
            provider = providers.get(str(row["venue_id"]))
            if provider is None:
                # A venue with no configured provider cannot be swept. Leave
                # the row: a stuck row is visible, a closed one is not.
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

    # OUTSIDE the dry-run branch on purpose -- see this function's docstring
    # and the module's. Ending a finished rental's lease destroys nothing at a
    # venue, and it is the only mechanism that ends one at all; gating it on
    # the destroy flag made the shipped default a deployment where leases
    # never end.
    #
    # TWO QUERIES, ONE REVOKE PER MACHINE. The first is keyed on the rental and
    # the second on the machine, because a lease whose rental row never
    # recorded it is invisible to the first -- see the module docstring. They
    # overlap deliberately (a terminal rental's machine satisfies both), so the
    # pass keeps the set it has already acted on rather than revoking twice and
    # logging a second, meaningless "already revoked".
    try:
        seen: set[str] = set()
        orphans = await asyncio.to_thread(
            orphaned_leases, db, boot_grace_s=boot_grace_s
        )
        if orphans:
            # Worth a line of its own: each of these is a credential that
            # outlived the process that minted it, which means an acquisition
            # died between the mint and recording it on the row.
            log.warning(
                "capacity reconcile: %d leased machine(s) that no live rental "
                "names -- revoking. node_id embeds the rental id "
                "(rented-<id[:12]>): %s",
                len(orphans),
                [str(r["node_id"]) for r in orphans],
            )
        for row in (
            await asyncio.to_thread(finished_rentals_with_live_credentials, db)
        ) + orphans:
            machine_id = str(row.get("machine_id") or "")
            if machine_id in seen:
                continue
            seen.add(machine_id)
            await _revoke_credential(db, row)
    except Exception:  # noqa: BLE001 - the money half already happened
        log.error(
            "capacity reconcile: the credential sweep failed", exc_info=True
        )

    return settled
