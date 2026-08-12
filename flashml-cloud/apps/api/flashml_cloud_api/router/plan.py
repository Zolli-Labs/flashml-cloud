"""The frontier: which machines should be eligible, what it costs, how long.

Tasks in the jobs this targets are independent — HPO trials, eval shards, the
workloads decision M8 names — which is what makes the arithmetic below a sort
and a fill rather than a linear program. Homogeneous tasks over machines with
a rate and a price collapse the assignment problem: **cheapest** is a scan
down a price-ordered list, **fastest** is a water-fill that equalises finish
times, and there is no LP solver anywhere in this file. It has to return in
well under a second behind a submit button, and it does — ``O(M log M + N log
min(N, S))``, where ``S`` is the number of concurrent slots the fleet offers.

**What a plan is, and what it is not.** A plan names a fleet and a split:
"eight machines, roughly five trials each, 14.2 ZC, done in 38 minutes". It
does **not** name which machine runs trial 17, because nothing in FlashML can
make that true — the runtime is a pull system and the coordinator never picks
a node for a task. The split is what the fill *predicts* the pull loop will
converge to given that fleet; it is arithmetic for the buyer, never an
instruction to the scheduler.

**Three invariants, each with a test.**

*Gates before price.* ``eligible_fleet`` runs the placement predicate and
nothing else, before a single price is read. The predicate is **injected** —
this module holds no eligibility rule of its own, because a second copy of
seven fail-closed gates would drift and the drift would be a security
regression rather than a wrong number. See ``eligible_fleet`` for why it is
injected rather than imported.

*Reliability ranks; it never excludes and it never divides.* A tier orders
candidates that are otherwise tied on venue and price. It cannot remove one.
The first draft of the marketplace design had ``effective_price = ask ÷
P(accepted)``; that was removed for manufacturing a quantity the ledger does
not hold, and nothing here reintroduces it.

*Cost is a vector.* ``Cost`` carries ZC and USD side by side and offers no
way to reduce them to one number, because the exchange rate that would take
is precisely what decision M4 forbids and what ``contributions.py`` warns a
credit balance must never imply. Cross-currency ordering is therefore done by
**venue precedence** — a stated product preference — never by comparing
magnitudes.
"""
from __future__ import annotations

import heapq
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from flashruntime.protocol.v1alpha1 import TaskSpec

from flashml_cloud_api.router.estimator import (
    BASIS_PROJECTED,
    TIER_MIXED,
    TIER_SHAKY,
    TIER_STEADY,
    TIER_UNPROVEN,
    Estimate,
    planning_seconds,
)

__all__ = [
    "CURRENCY_USD",
    "CURRENCY_ZC",
    "DEFAULT_VENUE_ORDER",
    "PLAN_BALANCED",
    "PLAN_CHEAPEST",
    "PLAN_FASTEST",
    "VENUE_MARKET",
    "VENUE_RENTED",
    "VENUE_WORKSPACE",
    "Allocation",
    "Canary",
    "Candidate",
    "Cost",
    "EligibilityPredicate",
    "Plan",
    "PlanRequest",
    "PlanSet",
    "balanced_plan",
    "canary_for",
    "cheapest_plan",
    "eligible_fleet",
    "fastest_plan",
    "frontier",
    "plan_job",
]

CURRENCY_ZC = "ZC"
CURRENCY_USD = "USD"

VENUE_WORKSPACE = "workspace"
VENUE_MARKET = "market"
VENUE_RENTED = "rented"

#: Which venue is spent first. A **preference order**, not an exchange rate —
#: the distinction is the whole reason this constant exists.
#:
#: Sorting a mixed list of ZC and USD asks by magnitude would silently invent
#: the rate M4 forbids: saying 0.40 ZC/h is "cheaper" than $0.34/h is a
#: conversion, however it is spelled. So crossing a currency boundary is a
#: tier decision taken once, in the open, and never a comparison of numbers.
#:
#: The order encodes two product decisions and nothing else. Workspace first
#: because workspace machines are free to their own members (M1), so using
#: them is a strict improvement in both currencies at once — §6.2's argument
#: that a plan leaving your own idle gear alone while paying for cloud is
#: nearly always wrong. Market before rented because Zolli capacity being
#: structurally cheaper is decision M6, stated by the owner rather than
#: computed here. A caller with a different preference passes its own.
DEFAULT_VENUE_ORDER = (VENUE_WORKSPACE, VENUE_MARKET, VENUE_RENTED)

PLAN_CHEAPEST = "cheapest"
PLAN_BALANCED = "balanced"
PLAN_FASTEST = "fastest"

#: Ordering among candidates that are otherwise identical, best first.
#:
#: This is the ONLY place a host's record is read, and it reaches exactly one
#: decision: when a price tier offers more concurrent slots than the job has
#: tasks left, whose slots get taken. It never changes a price, never removes
#: a candidate, and never splits a price tier — splitting one would hand all
#: forty trials to the steadiest machine while an identically-priced,
#: identically-fast neighbour idled, which costs the same and finishes far
#: later, i.e. a strictly dominated plan chosen on reliability grounds.
#:
#: ``unproven`` sits between ``mixed`` and ``steady``, and its placement is
#: the careful bit. It is NOT an adjustment: no number is synthesised for an
#: unproven host, its price is untouched, and it is never removed. What this
#: expresses is only what the evidence supports — a host with a demonstrated
#: record beats one with no record, and one with no record beats one with a
#: demonstrated bad record. Between two unproven hosts nothing separates them
#: but the machine id, which is what ``score`` returning 0.0 means upstream:
#: the queue decides, exactly as today.
_TIER_RANK = {
    TIER_STEADY: 0,
    TIER_UNPROVEN: 1,
    TIER_MIXED: 2,
    TIER_SHAKY: 3,
}

#: Where an unrecognised venue label sorts. Last, always — a mislabelled
#: candidate must never be preferred by accident, and dropping it would be
#: worse still (it is eligible; the gates said so).
_UNKNOWN_VENUE_RANK = 1 << 30

#: Decimal places a quoted cost keeps. Settlement happens in integer
#: millicredits in a ledger this module does not touch; four places is a
#: quote, kept fixed so the same inputs render the same string.
_COST_PLACES = 4

_SECONDS_PLACES = 3

#: Float slack when comparing a finish time against a deadline. A task that
#: lands exactly on the deadline made it; floating-point accumulation over a
#: few thousand additions must not turn that into a miss.
_DEADLINE_EPSILON = 1e-9

#: A pure predicate over ``(task, node_view)``. See ``eligible_fleet``.
EligibilityPredicate = Callable[[TaskSpec, Mapping[str, Any]], bool]


# ---------------------------------------------------------------------------
# money, as a vector
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cost:
    """What a plan costs, in every currency it costs anything in.

    **There is deliberately no total.** No ``total()``, no ``__float__``, no
    ``__int__``, no ``amount`` — and none may be added. Any of them would
    have to answer "how many dollars is a Zolli credit?", which decision M4
    forbids and which ``contributions.py`` refuses in the strongest terms it
    has: a number that implies a drawdown "promises an exchange rate this
    product has not designed and cannot honour."

    ``Cost + Cost`` is component-wise and stays a ``Cost``, which is the only
    arithmetic that is meaningful: 9.8 ZC and $6.40 side by side is the
    honest comparison, and 9.8 + 6.40 is not a number about anything.
    """

    zc: float = 0.0
    usd: float = 0.0

    def __add__(self, other: "Cost") -> "Cost":
        if not isinstance(other, Cost):
            return NotImplemented
        return Cost(zc=self.zc + other.zc, usd=self.usd + other.usd)

    def rounded(self) -> "Cost":
        return Cost(zc=round(self.zc, _COST_PLACES), usd=round(self.usd, _COST_PLACES))

    def currencies(self) -> tuple[str, ...]:
        """The currencies actually spent. Empty for a free plan."""
        spent = []
        if self.zc:
            spent.append(CURRENCY_ZC)
        if self.usd:
            spent.append(CURRENCY_USD)
        return tuple(spent)


# ---------------------------------------------------------------------------
# the shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One machine as the router sees it.

    ``node`` is the registry's node view — the same mapping the coordinator
    hands the placement policy (``{"node_id", "capabilities": {...},
    "sandbox_capable": ..., ...}``). It is passed to the injected predicate
    untouched; this module never reads a field out of it, so a gate that
    starts consulting a new capability keeps working here with no change.

    ``price_per_hour`` is the host's ask per machine-hour in ``currency``, or
    0 for workspace capacity (M1) and for a donated listing (M13, "labelled
    donated rather than priced" — a zero ask is legal and is not a floor).

    ``seconds_per_task`` overrides the job-wide estimate for this machine.
    ``None`` means "use the job's", and if the job has none either the
    machine is *eligible but unplannable* — a real state, reported rather
    than smoothed over, and the one the canary answers.

    ``reliability_tier`` must come from ``estimator.reliability_tier`` over
    the ``metrics.acceptance_rates`` row for **the class of work this job
    is**, on this machine — never an all-class rollup, and never borrowed
    from a neighbouring class. ``estimator.select_acceptance`` is what
    enforces that; it is a separate function precisely so the refusal is
    written down somewhere it can be reviewed.

    ``capability_class`` is the machine's HARDWARE class from
    ``estimator.hardware_class``, carried for the note a plan renders. It is
    not read by any solver.
    """

    machine_id: str
    node: Mapping[str, Any]
    venue: str = VENUE_MARKET
    currency: str = CURRENCY_ZC
    price_per_hour: float = 0.0
    max_concurrent_tasks: int = 1
    seconds_per_task: float | None = None
    reliability_tier: str = TIER_UNPROVEN
    capability_class: str | None = None


@dataclass(frozen=True)
class Allocation:
    """How many tasks one machine is expected to carry, and what that costs.

    ``tasks`` is a prediction about a pull system, not an assignment. Nothing
    downstream may read it as a binding — see this module's header.
    """

    machine_id: str
    tasks: int
    finish_seconds: float
    cost: Cost
    venue: str
    currency: str
    reliability_tier: str


@dataclass(frozen=True)
class Plan:
    """One point on the frontier.

    ``makespan_seconds`` is ``None`` when nothing was placed — never 0, which
    would read as "instantaneous". ``deadline_met`` is ``None`` when no
    deadline was asked for; it is a fact about a question, and there was no
    question.

    ``achievable_deadline_seconds`` is what a deadline-constrained plan
    reports when the fleet cannot make the deadline: the makespan the whole
    eligible fleet would achieve if it were all used. Reporting it beats
    failing — "the earliest this fleet can finish is 51 minutes" is
    actionable and "infeasible" is not.
    """

    name: str
    allocations: tuple[Allocation, ...] = ()
    tasks_placed: int = 0
    tasks_unplaced: int = 0
    cost: Cost = Cost()
    makespan_seconds: float | None = None
    deadline_seconds: float | None = None
    deadline_met: bool | None = None
    achievable_deadline_seconds: float | None = None
    duration_basis: str | None = None
    dominated_by: str | None = None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Canary:
    """Run one task, measure it, re-plan the rest. The cold-start answer.

    Roughly 29 credited tasks exist in the whole ledger and the coordinator's
    reliability record does not survive a deploy, so a novel workload has no
    history and no amount of arithmetic will conjure one. Measuring is the
    only honest route from "we cannot predict this" to a number — and for a
    40-trial sweep at six seconds a trial, it costs one trial to calibrate
    thirty-nine.

    This is the SEAM, not the trigger. It names the probe and what it would
    buy; the caller decides whether to run it, runs it, and comes back
    through ``estimator.from_canary`` and a second ``plan_job``. Nothing here
    submits anything.
    """

    machine_id: str
    tasks_to_calibrate: int
    reason: str
    current_basis: str | None = None


@dataclass(frozen=True)
class PlanRequest:
    """Everything the planner needs, and nothing it could fetch itself.

    ``task`` is a real ``TaskSpec`` and it is what the gates read. A
    hand-built stand-in with the payload fields stripped would pass gates the
    real one fails, which is the drift the injected predicate exists to
    prevent — pass the spec the compiler produced.
    """

    task: TaskSpec
    tasks: int
    candidates: tuple[Candidate, ...] = ()
    duration: Estimate | None = None
    deadline_seconds: float | None = None
    venue_order: tuple[str, ...] = DEFAULT_VENUE_ORDER


@dataclass(frozen=True)
class PlanSet:
    """The three plans, the frontier they sit on, and what to say instead.

    Any of the three may be ``None``. ``balanced`` is ``None`` when no
    deadline was given, and that is not a gap — with nothing to balance
    against, a third row would be a UI convenience, which is exactly what the
    design says these must not be. All three are ``None`` when no duration
    estimate exists at all, and then ``canary`` carries the answer.
    """

    cheapest: Plan | None = None
    balanced: Plan | None = None
    fastest: Plan | None = None
    recommended: str | None = None
    canary: Canary | None = None
    duration: Estimate | None = None
    eligible_machines: int = 0
    excluded_machines: int = 0
    unplannable_machines: int = 0
    notes: tuple[str, ...] = ()

    def plans(self) -> tuple[Plan, ...]:
        """The plans that exist, in frontier order (cheapest → fastest)."""
        return tuple(
            plan
            for plan in (self.cheapest, self.balanced, self.fastest)
            if plan is not None
        )


# ---------------------------------------------------------------------------
# gates, before price, always
# ---------------------------------------------------------------------------


def eligible_fleet(
    task: TaskSpec,
    candidates: Sequence[Candidate],
    *,
    eligible: EligibilityPredicate,
) -> tuple[Candidate, ...]:
    """The candidates that may legally run ``task``. No price is read here.

    **The predicate is injected and there is no default.** The real rule is
    ``flashruntime.scheduler.IsolationAwarePlacement.eligible`` — seven
    fail-closed gates (argv runner, module availability, host-local datasets,
    GPU count, node exclusion, pool membership, sandbox tier) whose docstring
    runs to three hundred lines of argument about polarity. Copying them here
    would produce a second implementation that drifts, and drift in *these*
    gates is not a wrong price: it is unsandboxed argv on a machine outside a
    trust boundary, or a task placed on the machine already holding the other
    half of its verification pair.

    So this module contains no eligibility rule at all — there is nothing to
    drift. It cannot import the real one either: the workspace rule is that
    this repo imports ``flashruntime.protocol`` and nothing else, and
    ``tests/test_import_boundary.py`` enforces it by name. Widening that
    boundary is a decision to record in a commit, not one to take inside a
    router. The caller wiring the endpoint passes
    ``IsolationAwarePlacement().eligible`` (or
    ``ReliabilityAwarePlacement``'s, which inherits it unchanged — that
    policy overrides ``score`` only, and deliberately never ``eligible``).

    **Fails closed.** A predicate that raises drops the candidate rather than
    crashing the page, and only a literal ``True`` counts — a truthy
    stand-in does not, matching every boolean capability gate upstream.

    Ordering is preserved from ``candidates`` so this stays a pure filter;
    every ordering decision downstream is made where it can be read.
    """
    return tuple(
        candidate
        for candidate in candidates
        if _passes(eligible, task, candidate.node)
    )


def _passes(
    eligible: EligibilityPredicate, task: TaskSpec, node: Mapping[str, Any]
) -> bool:
    try:
        return eligible(task, node) is True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# the fill
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Resolved:
    """A candidate with its planning duration and per-task price settled."""

    candidate: Candidate
    seconds: float
    unit_price: float  # per task, in the candidate's own currency
    venue_rank: int

    @property
    def machine_id(self) -> str:
        return self.candidate.machine_id


def _resolve(
    candidates: Sequence[Candidate],
    *,
    duration: Estimate | None,
    venue_order: Sequence[str],
) -> tuple[list[_Resolved], list[Candidate]]:
    """Split eligible candidates into plannable and unplannable.

    Unplannable means "no duration for this machine and none for the job" —
    eligible, allowed, and simply not something a number can be attached to
    yet. It is reported, never dropped silently and never filled in with a
    plausible substitute.
    """
    fallback = planning_seconds(duration) if duration is not None else None
    plannable: list[_Resolved] = []
    unplannable: list[Candidate] = []

    for candidate in candidates:
        seconds = candidate.seconds_per_task
        if seconds is None:
            seconds = fallback
        if seconds is None or not math.isfinite(seconds) or seconds <= 0:
            unplannable.append(candidate)
            continue
        plannable.append(
            _Resolved(
                candidate=candidate,
                seconds=float(seconds),
                unit_price=_unit_price(candidate, float(seconds)),
                venue_rank=_venue_rank(candidate.venue, venue_order),
            )
        )
    return plannable, unplannable


def _venue_rank(venue: str, venue_order: Sequence[str]) -> int:
    try:
        return venue_order.index(venue)
    except ValueError:
        return _UNKNOWN_VENUE_RANK


def _unit_price(candidate: Candidate, seconds: float) -> float:
    """What one accepted task costs on this machine, in its own currency.

    ``ask_per_hour x hours_per_task``, which is the marketplace design §6.1
    formula and equals machine-hours exactly when ``max_concurrent_tasks``
    is 1. A host running four tasks at once for an hour gives one
    machine-hour and is quoted four task-hours here, so the quote
    OVER-states. That is the deliberate direction: over-stating a budget
    costs a buyer nothing, while under-stating one spends a grant that has no
    refill (M10). It also partly self-corrects, because a machine running
    four trials at once produces slower per-task durations and those are what
    the estimator measured.

    Note what is NOT here: no ``÷ P(accepted)``, no retry-overhead multiplier.
    The first draft of the design had both; they were removed for inventing
    a quantity the ledger does not hold. And no staging term either —
    ``db.py`` documents ``duration_s`` as "lease-held wall clock (claim to
    credit), which includes input download and output upload", so staging is
    already inside every second the estimator saw. Adding it would double
    count.

    A **zero** ask is legal and quoted at zero — M13, "labelled donated
    rather than priced", because volunteers are a real supply tier and
    pricing them at a floor would misrepresent them. A **negative** ask
    raises: it is not a cheaper machine, it is a machine that pays you to use
    it, and "cheapest" would take every task on the network to it.
    """
    ask = float(candidate.price_per_hour)
    if not math.isfinite(ask) or ask < 0:
        raise ValueError(
            f"machine {candidate.machine_id!r} asks {ask!r} per hour. A zero "
            f"ask is legal and means donated; a negative or non-finite one is "
            f"malformed, and quoting it would make it the cheapest capacity "
            f"on the network."
        )
    return ask * seconds / 3600.0


def _slots(members: Sequence[_Resolved], tasks: int) -> list[tuple[float, str]]:
    """The ``tasks`` best concurrency slots the fleet offers, best first.

    Every slot on one machine has that machine's per-task duration, so the
    fastest ``tasks`` slots are found by walking machines in duration order
    and taking as many as each offers. Capping at ``tasks`` is what keeps the
    fill linear: a host advertising ten thousand concurrent tasks contributes
    at most one slot per task in the job, never ten thousand.

    Taking more than ``tasks`` slots could not help. With one task per slot
    the makespan is the slowest slot used, and a slot slower than all of
    those cannot lower it.

    **The cap is the one place a reliability tier is consulted.** When the
    fleet offers more slots than the job has tasks, some machines' slots go
    unused, and choosing the steadier host's is free — same price, same
    speed, better record. When there are enough tasks to go round, every slot
    is taken and the tier changes nothing, which is the correct amount for a
    signal that must rank and never exclude.
    """
    ordered = sorted(
        members,
        key=lambda item: (
            item.seconds,
            _TIER_RANK.get(
                item.candidate.reliability_tier, _TIER_RANK[TIER_UNPROVEN]
            ),
            item.machine_id,
        ),
    )
    slots: list[tuple[float, str]] = []
    for item in ordered:
        if len(slots) >= tasks:
            break
        available = min(int(item.candidate.max_concurrent_tasks), tasks - len(slots))
        slots.extend((item.seconds, item.machine_id) for _ in range(max(available, 0)))
    return slots


def _fill(
    members: Sequence[_Resolved], tasks: int, *, deadline: float | None
) -> tuple[dict[str, int], float | None]:
    """Water-fill ``tasks`` across ``members``; return counts and makespan.

    Greedy earliest-completion over the slots, which for identical tasks on
    machines of differing speed is not a heuristic — it is the optimum, and
    it *is* the water-fill: the next task always goes to whichever slot frees
    up first, so finish times equalise and a machine too slow to help is
    never reached.

    That is also where "stop when a marginal machine cannot reduce the max"
    falls out for free rather than being coded as a rule. A slot whose first
    task would finish after every other slot's last one is simply never the
    minimum of the heap.

    Under a ``deadline``, a slot that cannot complete another task in time is
    finished with. Because the heap yields the earliest finish, the moment
    the minimum exceeds the deadline no slot can take another task and the
    fill stops — which is how "report the achievable deadline rather than
    failing" gets something honest to report.
    """
    slots = _slots(members, tasks)
    if not slots or tasks <= 0:
        return {}, None

    heap = [
        (seconds, machine_id, index, seconds)
        for index, (seconds, machine_id) in enumerate(slots)
    ]
    heapq.heapify(heap)

    assigned: dict[str, int] = {}
    makespan = 0.0
    remaining = tasks
    while remaining > 0 and heap:
        finish, machine_id, index, seconds = heapq.heappop(heap)
        if deadline is not None and finish > deadline + _DEADLINE_EPSILON:
            break  # the earliest finisher is late ⇒ every slot is late
        assigned[machine_id] = assigned.get(machine_id, 0) + 1
        makespan = max(makespan, finish)
        remaining -= 1
        heapq.heappush(heap, (finish + seconds, machine_id, index, seconds))

    if not assigned:
        return {}, None
    return assigned, round(makespan, _SECONDS_PLACES)


def _assemble(
    name: str,
    members: Sequence[_Resolved],
    assigned: Mapping[str, int],
    *,
    tasks: int,
    makespan: float | None,
    deadline: float | None,
    duration: Estimate | None,
    achievable: float | None,
    notes: Sequence[str] = (),
) -> Plan:
    """Turn a per-machine task count into a Plan, cost vector included."""
    by_id = {item.machine_id: item for item in members}
    allocations: list[Allocation] = []
    cost = Cost()

    for machine_id in sorted(assigned):
        count = assigned[machine_id]
        if count <= 0:
            continue
        item = by_id[machine_id]
        candidate = item.candidate
        component = _cost_of(candidate.currency, item.unit_price * count)
        cost = cost + component
        concurrency = max(int(candidate.max_concurrent_tasks), 1)
        rounds = math.ceil(count / concurrency)
        allocations.append(
            Allocation(
                machine_id=machine_id,
                tasks=count,
                finish_seconds=round(rounds * item.seconds, _SECONDS_PLACES),
                cost=component.rounded(),
                venue=candidate.venue,
                currency=candidate.currency,
                reliability_tier=candidate.reliability_tier,
            )
        )

    placed = sum(allocation.tasks for allocation in allocations)
    unplaced = max(tasks - placed, 0)
    cost = cost.rounded()

    plan_notes = list(notes)
    if len(cost.currencies()) > 1:
        plan_notes.append(
            "this plan spends ZC and USD; the two are reported side by side "
            "and are never summed — there is no exchange rate between them"
        )
    if unplaced:
        plan_notes.append(
            f"{unplaced} of {tasks} tasks do not fit"
            + (
                f"; the whole eligible fleet finishes in "
                f"{achievable:g}s at the earliest"
                if achievable is not None
                else " and no eligible machine can be timed"
            )
        )

    deadline_met: bool | None = None
    if deadline is not None:
        deadline_met = unplaced == 0 and makespan is not None

    return Plan(
        name=name,
        allocations=tuple(allocations),
        tasks_placed=placed,
        tasks_unplaced=unplaced,
        cost=cost,
        makespan_seconds=makespan,
        deadline_seconds=deadline,
        deadline_met=deadline_met,
        achievable_deadline_seconds=achievable,
        duration_basis=duration.basis if duration is not None else None,
        notes=tuple(plan_notes),
    )


def _cost_of(currency: str, amount: float) -> Cost:
    """One machine's spend as a ``Cost``.

    An unrecognised currency raises rather than being dropped. A cost quietly
    discarded is a machine that appears free, which is the single most
    expensive rounding error this file could make.
    """
    if currency == CURRENCY_ZC:
        return Cost(zc=amount)
    if currency == CURRENCY_USD:
        return Cost(usd=amount)
    raise ValueError(
        f"unknown currency {currency!r}: cost is a vector over "
        f"({CURRENCY_ZC}, {CURRENCY_USD}) and a third one has nowhere to go. "
        f"Dropping it would quote the machine as free."
    )


# ---------------------------------------------------------------------------
# the three solvers
# ---------------------------------------------------------------------------


def _price_order(item: _Resolved) -> tuple[int, float, str]:
    """Venue, then price within the venue, then id.

    Venue leads so that no two currencies are ever compared by magnitude —
    crossing from ZC to USD is a preference stated once in
    ``DEFAULT_VENUE_ORDER``, never a comparison of two numbers that have no
    rate between them.

    Reliability is deliberately absent. It belongs in ``_slots``, where it
    decides whose slots go unused; putting it here would split a price tier
    and produce a dominated plan. And no divisor appears anywhere: the first
    draft's ``ask ÷ P(accepted)`` invented a quantity the ledger does not
    hold.
    """
    return (item.venue_rank, item.unit_price, item.machine_id)


def _cheapest_over(
    members: Sequence[_Resolved],
    *,
    tasks: int,
    deadline: float | None,
    duration: Estimate | None,
    achievable: float | None,
    name: str = PLAN_CHEAPEST,
) -> Plan:
    """Minimum cost: fill the cheapest tier to capacity, then the next.

    Candidates are grouped by ``(venue, unit price)`` and the groups
    consumed in that order. **Inside a group the fill water-fills**, which
    costs nothing — every machine in a group charges the same per task, so
    the split cannot change the price — and strictly improves the makespan.
    Without it "cheapest" would hand all forty trials to whichever single
    machine sorted first and quote a makespan forty times longer than the
    identically-priced alternative, which is a dominated plan presented as a
    choice.

    Under a ``deadline`` each group takes only what it can finish in time and
    the rest spills to the next. If tasks remain after every group, the plan
    reports what it placed plus ``achievable_deadline_seconds`` — the fleet's
    best possible finish — rather than failing. A submitter can move a
    deadline; they cannot act on the word "infeasible".
    """
    ordered = sorted(members, key=_price_order)
    assigned: dict[str, int] = {}
    makespan: float | None = None
    remaining = tasks

    index = 0
    while index < len(ordered) and remaining > 0:
        key = _price_order(ordered[index])[:2]
        group = [ordered[index]]
        index += 1
        while index < len(ordered) and _price_order(ordered[index])[:2] == key:
            group.append(ordered[index])
            index += 1

        counts, group_makespan = _fill(group, remaining, deadline=deadline)
        for machine_id, count in counts.items():
            assigned[machine_id] = assigned.get(machine_id, 0) + count
            remaining -= count
        if group_makespan is not None:
            makespan = (
                group_makespan if makespan is None else max(makespan, group_makespan)
            )

    return _assemble(
        name,
        members,
        assigned,
        tasks=tasks,
        makespan=makespan,
        deadline=deadline,
        duration=duration,
        achievable=achievable,
    )


def _fastest_over(
    members: Sequence[_Resolved],
    *,
    tasks: int,
    deadline: float | None,
    duration: Estimate | None,
    achievable: float | None,
) -> Plan:
    """Minimum makespan: water-fill the whole eligible fleet at once.

    Price is not consulted, which is the honest meaning of "fastest" and also
    why this plan is usually the expensive corner of the frontier. Reliability
    is not consulted either — it orders equals under a price and there is no
    price here.
    """
    assigned, makespan = _fill(members, tasks, deadline=deadline)
    return _assemble(
        PLAN_FASTEST,
        members,
        assigned,
        tasks=tasks,
        makespan=makespan,
        deadline=deadline,
        duration=duration,
        achievable=achievable,
    )


def _prepared(
    task: TaskSpec,
    candidates: Sequence[Candidate],
    *,
    eligible: EligibilityPredicate,
    tasks: int,
    duration: Estimate | None,
    venue_order: Sequence[str],
) -> tuple[list[_Resolved], float | None]:
    """Gate, resolve, and compute the fleet's best possible finish. In that
    order, every time — which is what makes it impossible to reach a price
    without having gone through the gates first."""
    survivors = eligible_fleet(task, candidates, eligible=eligible)
    members, _ = _resolve(survivors, duration=duration, venue_order=venue_order)
    _, achievable = _fill(members, tasks, deadline=None)
    return members, achievable


def cheapest_plan(
    task: TaskSpec,
    candidates: Sequence[Candidate],
    *,
    eligible: EligibilityPredicate,
    tasks: int,
    duration: Estimate | None,
    deadline: float | None = None,
    venue_order: Sequence[str] = DEFAULT_VENUE_ORDER,
) -> Plan:
    """``_cheapest_over`` with the gates in front of it.

    Every public entry point in this module takes the eligibility predicate,
    including the ones that only care about price. That is not ceremony: a
    solver reachable without the gates is a path on which a price decides
    placement, and the invariant is worth more than the convenience of one
    shorter signature.
    """
    members, achievable = _prepared(
        task,
        candidates,
        eligible=eligible,
        tasks=tasks,
        duration=duration,
        venue_order=venue_order,
    )
    return _cheapest_over(
        members,
        tasks=tasks,
        deadline=deadline,
        duration=duration,
        achievable=achievable,
    )


def balanced_plan(
    task: TaskSpec,
    candidates: Sequence[Candidate],
    *,
    eligible: EligibilityPredicate,
    tasks: int,
    duration: Estimate | None,
    deadline: float,
    venue_order: Sequence[str] = DEFAULT_VENUE_ORDER,
) -> Plan:
    """Consume what you already own, then buy only the deficit.

    Structurally this is the cheapest plan under a deadline, and that is the
    point rather than a shortcut: the venue order puts free workspace
    capacity first, the deadline is what turns "eventually" into a deficit,
    and the deficit is filled from the cheapest paid tier that can still make
    the time. §6.2's recommendation is not a heuristic about markets — it is
    the utilisation argument, computed.

    It exists as a separate name because it is a different **point** on the
    frontier: unconstrained ``cheapest`` and this one answer different
    questions, and when they answer the same one ``frontier`` says so instead
    of showing the same plan twice.

    ``deadline`` is required here and optional on ``cheapest_plan``. Without
    one there is no deficit — free capacity can do everything eventually —
    and a "balanced" plan would have nothing to balance against.
    """
    members, achievable = _prepared(
        task,
        candidates,
        eligible=eligible,
        tasks=tasks,
        duration=duration,
        venue_order=venue_order,
    )
    return _cheapest_over(
        members,
        tasks=tasks,
        deadline=deadline,
        duration=duration,
        achievable=achievable,
        name=PLAN_BALANCED,
    )


def fastest_plan(
    task: TaskSpec,
    candidates: Sequence[Candidate],
    *,
    eligible: EligibilityPredicate,
    tasks: int,
    duration: Estimate | None,
    deadline: float | None = None,
    venue_order: Sequence[str] = DEFAULT_VENUE_ORDER,
) -> Plan:
    """``_fastest_over`` with the gates in front of it. See ``cheapest_plan``."""
    members, achievable = _prepared(
        task,
        candidates,
        eligible=eligible,
        tasks=tasks,
        duration=duration,
        venue_order=venue_order,
    )
    return _fastest_over(
        members,
        tasks=tasks,
        deadline=deadline,
        duration=duration,
        achievable=achievable,
    )


# ---------------------------------------------------------------------------
# the frontier
# ---------------------------------------------------------------------------


def _objectives(plan: Plan) -> tuple[int, float, float, float]:
    """``(unplaced, ZC, USD, makespan)`` — the vector plans are compared on.

    Four entries, three of them cost-or-time and one of them completeness.
    Unplaced tasks lead because without them a plan that places two of forty
    trials looks like the cheapest and fastest thing on the board.

    ZC and USD stay separate axes. That is the whole reason this is a
    frontier and not a ranking: with no exchange rate, a cheaper-in-ZC and a
    cheaper-in-USD plan are genuinely incomparable, and the correct output is
    both of them.
    """
    makespan = plan.makespan_seconds
    return (
        plan.tasks_unplaced,
        plan.cost.zc,
        plan.cost.usd,
        math.inf if makespan is None else makespan,
    )


def frontier(plans: Sequence[Plan]) -> tuple[Plan, ...]:
    """Mark each dominated plan with the plan that dominates it.

    A dominates B when A is no worse on every axis and strictly better on at
    least one. An exact tie counts as domination for the LATER plan in the
    given order, deliberately: two rows with identical cost, time and
    coverage are one choice shown twice, and a third row that adds nothing is
    the "UI convenience" this design says these plans must never be.

    Nothing is dropped. A dominated plan still renders — labelled — because
    "balanced costs the same as cheapest and finishes no sooner" is
    information about the fleet, and hiding the row hides it.
    """
    marked: list[Plan] = []
    for index, plan in enumerate(plans):
        mine = _objectives(plan)
        dominator: str | None = None
        for other_index, other in enumerate(plans):
            if other_index == index:
                continue
            theirs = _objectives(other)
            if all(t <= m for t, m in zip(theirs, mine)) and (
                any(t < m for t, m in zip(theirs, mine)) or other_index < index
            ):
                dominator = other.name
                break
        marked.append(replace(plan, dominated_by=dominator))
    return tuple(marked)


# ---------------------------------------------------------------------------
# the canary seam
# ---------------------------------------------------------------------------


def canary_for(
    plans: Sequence[Plan],
    eligible: Sequence[Candidate],
    *,
    tasks: int,
    duration: Estimate | None,
    venue_order: Sequence[str],
) -> Canary | None:
    """Where to spend one task to make the other ``tasks - 1`` predictable.

    Offered whenever the duration is absent or ``projected`` — the two states
    in which the panel would otherwise be quoting a number it cannot defend —
    and only when there is more than one task, because calibrating a
    single-task job with a single task is the job.

    The probe goes to the machine the recommendation would lean on hardest,
    since that is the number the plan actually rests on; with no plan at all
    (the no-estimate case, which is the interesting one) it goes to the first
    eligible machine in venue order. Deterministic either way: ties break on
    machine id, so the same fleet always calibrates on the same host.

    Returns the seam and never acts on it. The caller submits the probe,
    feeds the measured seconds to ``estimator.from_canary``, and calls
    ``plan_job`` again.
    """
    if tasks < 2 or not eligible:
        return None
    if duration is not None and duration.basis != BASIS_PROJECTED:
        return None

    machine_id: str | None = None
    for plan in plans:
        if plan.allocations:
            machine_id = max(
                plan.allocations, key=lambda a: (a.tasks, a.machine_id)
            ).machine_id
            break
    if machine_id is None:
        machine_id = min(
            eligible,
            key=lambda c: (_venue_rank(c.venue, venue_order), c.machine_id),
        ).machine_id

    if duration is None:
        reason = (
            "no usable history for this task shape on this fleet, so no "
            "duration can be quoted at all — one task measures it"
        )
    else:
        reason = (
            f"the only evidence is projected (n={duration.n}), which is a "
            f"range and not a prediction — one task replaces it with a "
            f"measurement"
        )

    return Canary(
        machine_id=machine_id,
        tasks_to_calibrate=tasks - 1,
        reason=reason,
        current_basis=duration.basis if duration is not None else None,
    )


# ---------------------------------------------------------------------------
# the whole thing
# ---------------------------------------------------------------------------


def plan_job(request: PlanRequest, *, eligible: EligibilityPredicate) -> PlanSet:
    """Gate the fleet, then price it three ways, then say which to take.

    Order is the contract: ``eligible_fleet`` first and unconditionally, so a
    price can never buy past a capability, a pool, a host's local data or an
    isolation requirement. Everything after it is arithmetic over a set the
    gates already approved.
    """
    survivors = eligible_fleet(request.task, request.candidates, eligible=eligible)
    excluded = len(request.candidates) - len(survivors)
    members, unplannable = _resolve(
        survivors, duration=request.duration, venue_order=request.venue_order
    )

    notes: list[str] = []
    if unplannable:
        notes.append(
            f"{len(unplannable)} eligible machine"
            f"{'s' if len(unplannable) != 1 else ''} carry no duration "
            f"evidence and are left out of the arithmetic — eligible, "
            f"allowed to claim, simply not yet measurable"
        )
    if request.duration is not None and request.duration.basis == BASIS_PROJECTED:
        notes.append(
            f"every time and cost below rests on a projected range "
            f"(n={request.duration.n}): {request.duration.note}"
        )

    if not members or request.tasks <= 0:
        if not members and survivors:
            notes.append(
                "no duration evidence exists for any eligible machine, so no "
                "plan is quoted — a measured second is worth more than three "
                "estimated ones"
            )
        return PlanSet(
            canary=canary_for(
                (),
                survivors,
                tasks=request.tasks,
                duration=request.duration,
                venue_order=request.venue_order,
            ),
            duration=request.duration,
            eligible_machines=len(survivors),
            excluded_machines=excluded,
            unplannable_machines=len(unplannable),
            notes=tuple(notes),
        )

    # The fleet's best possible finish, computed once and reported by every
    # plan that cannot make a deadline.
    _, achievable = _fill(members, request.tasks, deadline=None)

    cheapest = _cheapest_over(
        members,
        tasks=request.tasks,
        deadline=None,
        duration=request.duration,
        achievable=achievable,
    )
    fastest = _fastest_over(
        members,
        tasks=request.tasks,
        deadline=request.deadline_seconds,
        duration=request.duration,
        achievable=achievable,
    )
    balanced: Plan | None = None
    if request.deadline_seconds is not None:
        balanced = _cheapest_over(
            members,
            tasks=request.tasks,
            deadline=request.deadline_seconds,
            duration=request.duration,
            achievable=achievable,
            name=PLAN_BALANCED,
        )
        # `cheapest` is built unconstrained on purpose — it is the frontier's
        # cost corner and a deadline would move it somewhere else. But a
        # reader who asked for a deadline still needs to know this plan
        # misses it, and "2h04, against your 30 minutes" is the whole reason
        # the other two rows exist. So the verdict is filled in after the
        # fact rather than the plan being rebuilt against a constraint that
        # would stop it being the cheapest one.
        cheapest = replace(
            cheapest,
            deadline_seconds=request.deadline_seconds,
            deadline_met=(
                cheapest.tasks_unplaced == 0
                and cheapest.makespan_seconds is not None
                and cheapest.makespan_seconds
                <= request.deadline_seconds + _DEADLINE_EPSILON
            ),
        )
    else:
        notes.append(
            "no deadline was given, so there is no third plan: 'balanced' is "
            "the cheapest fleet that still makes a time, and with no time to "
            "make it would only be 'cheapest' shown twice"
        )

    ordered = [plan for plan in (cheapest, balanced, fastest) if plan is not None]
    marked = {plan.name: plan for plan in frontier(ordered)}
    cheapest = marked[PLAN_CHEAPEST]
    fastest = marked[PLAN_FASTEST]
    balanced = marked.get(PLAN_BALANCED)

    recommended = _recommend(cheapest, balanced, fastest)

    return PlanSet(
        cheapest=cheapest,
        balanced=balanced,
        fastest=fastest,
        recommended=recommended,
        canary=canary_for(
            [plan for plan in (balanced, cheapest, fastest) if plan is not None],
            survivors,
            tasks=request.tasks,
            duration=request.duration,
            venue_order=request.venue_order,
        ),
        duration=request.duration,
        eligible_machines=len(survivors),
        excluded_machines=excluded,
        unplannable_machines=len(unplannable),
        notes=tuple(notes),
    )


def _recommend(
    cheapest: Plan, balanced: Plan | None, fastest: Plan
) -> str | None:
    """Which plan to star. Balanced when it exists and earns its place.

    §6.2: consume what you already own, then buy only the deficit. That is
    the product thesis and it is usually right, so balanced leads — but only
    while it is on the frontier. A balanced plan that another plan dominates
    is not a compromise, it is a worse plan, and starring it would recommend
    paying more for less.
    """
    for plan in (balanced, cheapest, fastest):
        if plan is None:
            continue
        if plan.tasks_unplaced == 0 and plan.dominated_by is None:
            return plan.name
    for plan in (balanced, cheapest, fastest):
        if plan is not None and plan.tasks_unplaced == 0:
            return plan.name
    return None
