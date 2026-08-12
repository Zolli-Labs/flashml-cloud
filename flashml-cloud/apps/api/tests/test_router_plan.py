"""The planner's invariants: gates first, money as a vector, no assignment.

Three things this file pins, in the order they matter.

**Gates before price.** Eligibility comes from the real
``IsolationAwarePlacement.eligible`` — the tests below drive the planner with
the actual runtime predicate rather than a stand-in, so a price can never buy
past a capability, a pool, a host's local data or an isolation requirement.
The planner holds no copy of those gates to drift from.

**Cost is a vector.** ZC and USD are separate axes with no rate between them
(decision M4), so nothing here may reduce them to one number. There is a test
for the absence of every method that could.

**A plan is a fleet, not an assignment.** FlashML is a pull system; nothing
in the runtime can make a host run a particular task. Allocations are counts
and predictions, never bindings, and there is a test that they carry no task
identity at all.
"""
from __future__ import annotations

import pytest
from flashruntime.protocol.v1alpha1 import TaskSpec
from flashruntime.scheduler import IsolationAwarePlacement, ReliabilityAwarePlacement

from flashml_cloud_api.router import estimator as est
from flashml_cloud_api.router import plan as P

ELIGIBLE = IsolationAwarePlacement().eligible


def _task(**payload) -> TaskSpec:
    return TaskSpec(task_id="t1", job_id="j1", commit_key="c1", payload=payload)


def _gpu_node(node_id: str, count: int = 1, megabytes: int = 24564) -> dict:
    return {
        "node_id": node_id,
        "capabilities": {
            "gpus": [
                {"index": i, "memory_total_mb": megabytes} for i in range(count)
            ]
        },
    }


def _cpu_node(node_id: str, cores: float = 8.0) -> dict:
    return {"node_id": node_id, "capabilities": {"cpu_cores": cores, "gpus": []}}


def _machine(
    machine_id: str,
    *,
    node: dict | None = None,
    venue: str = P.VENUE_MARKET,
    currency: str = P.CURRENCY_ZC,
    price_per_hour: float = 0.0,
    concurrency: int = 1,
    seconds: float | None = None,
    tier: str = est.TIER_UNPROVEN,
) -> P.Candidate:
    return P.Candidate(
        machine_id=machine_id,
        node=node if node is not None else _gpu_node(machine_id),
        venue=venue,
        currency=currency,
        price_per_hour=price_per_hour,
        max_concurrent_tasks=concurrency,
        seconds_per_task=seconds,
        reliability_tier=tier,
    )


#: 6 seconds a task, well measured. 600 ZC/h is 1.0 ZC per task, which keeps
#: the arithmetic in these tests readable.
MEASURED = est.Estimate(
    low=6.0, high=6.0, basis=est.BASIS_MEASURED, n=9, note="p50/IQR of 9 accepted runs"
)
PROJECTED = est.Estimate(
    low=3.0, high=12.0, basis=est.BASIS_PROJECTED, n=1, note="1 accepted run"
)


def _request(candidates, **kwargs) -> P.PlanRequest:
    return P.PlanRequest(
        task=kwargs.pop("task", _task(gpus=1)),
        tasks=kwargs.pop("tasks", 40),
        candidates=tuple(candidates),
        duration=kwargs.pop("duration", MEASURED),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# gates before price, always
# ---------------------------------------------------------------------------


def test_the_real_runtime_predicate_is_what_filters():
    """Not a reimplementation. The fifth placement gate fails closed on GPU
    count, and a CPU-only host must not reach the priced part of the router
    however cheap it is."""
    fleet = P.eligible_fleet(
        _task(gpus=1),
        [
            _machine("gpu", price_per_hour=600.0),
            _machine("cpu", node=_cpu_node("cpu"), price_per_hour=0.0),
        ],
        eligible=ELIGIBLE,
    )
    assert [c.machine_id for c in fleet] == ["gpu"]


def test_the_reliability_policy_inherits_the_same_gates():
    """``ReliabilityAwarePlacement`` overrides ``score`` and deliberately not
    ``eligible``, so wiring either policy in gates identically."""
    candidates = [_machine("gpu"), _machine("cpu", node=_cpu_node("cpu"))]
    task = _task(gpus=1)
    assert P.eligible_fleet(
        task, candidates, eligible=ELIGIBLE
    ) == P.eligible_fleet(
        task, candidates, eligible=ReliabilityAwarePlacement().eligible
    )


def test_price_cannot_buy_past_a_gate():
    """The single most important invariant in the design: the marketplace may
    be wrong about money without ever being wrong about safety."""
    free = _machine("cpu", node=_cpu_node("cpu"), price_per_hour=0.0)
    expensive = _machine("cpu", node=_cpu_node("cpu"), price_per_hour=99999.0)
    task = _task(gpus=1)
    assert P.eligible_fleet(task, [free], eligible=ELIGIBLE) == ()
    assert P.eligible_fleet(task, [expensive], eligible=ELIGIBLE) == ()


def test_eligibility_does_not_depend_on_price_at_all():
    cheap = _machine("m", price_per_hour=0.0)
    dear = _machine("m", price_per_hour=1e9)
    task = _task(gpus=1)
    assert len(P.eligible_fleet(task, [cheap], eligible=ELIGIBLE)) == len(
        P.eligible_fleet(task, [dear], eligible=ELIGIBLE)
    )


def test_a_predicate_that_raises_drops_the_candidate():
    """Fails closed, and does not take the submit page down with it."""

    def explodes(task, node):
        raise RuntimeError("registry unreachable")

    assert P.eligible_fleet(_task(), [_machine("m")], eligible=explodes) == ()


def test_a_truthy_stand_in_is_not_eligibility():
    """Matching every boolean capability gate upstream: only ``True`` counts."""
    assert P.eligible_fleet(_task(), [_machine("m")], eligible=lambda t, n: 1) == ()
    assert (
        P.eligible_fleet(_task(), [_machine("m")], eligible=lambda t, n: "yes") == ()
    )


def test_every_public_solver_takes_the_gate():
    """A solver reachable without the predicate is a path on which a price
    decides placement, so there is no default and no way to omit it."""
    for solver in (P.cheapest_plan, P.fastest_plan, P.balanced_plan):
        with pytest.raises(TypeError):
            solver(_task(gpus=1), [], tasks=1, duration=MEASURED)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        P.plan_job(_request([]))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        P.eligible_fleet(_task(gpus=1), [])  # type: ignore[call-arg]


def test_the_gates_run_inside_the_solvers_too():
    got = P.cheapest_plan(
        _task(gpus=1),
        [_machine("cpu", node=_cpu_node("cpu"), price_per_hour=0.0)],
        eligible=ELIGIBLE,
        tasks=10,
        duration=MEASURED,
    )
    assert got.tasks_placed == 0 and got.tasks_unplaced == 10


def test_pool_scoped_work_stays_inside_its_pool():
    """The seventh gate, fail closed. A host outside the pool is excluded
    however it is priced, and the router never sees it."""
    inside = P.Candidate(
        machine_id="in",
        node={"node_id": "in", "capabilities": {"pools": ["team-a"], "gpus": []}},
        price_per_hour=999.0,
    )
    outside = P.Candidate(
        machine_id="out",
        node={"node_id": "out", "capabilities": {"pools": ["team-b"], "gpus": []}},
        price_per_hour=0.0,
    )
    fleet = P.eligible_fleet(
        _task(pool="team-a"), [inside, outside], eligible=ELIGIBLE
    )
    assert [c.machine_id for c in fleet] == ["in"]


# ---------------------------------------------------------------------------
# min cost
# ---------------------------------------------------------------------------


def test_cheapest_spends_the_cheapest_tier_first():
    got = P.cheapest_plan(
        _task(gpus=1),
        [
            _machine("dear", price_per_hour=1200.0),
            _machine("cheap", price_per_hour=600.0),
        ],
        eligible=ELIGIBLE,
        tasks=10,
        duration=MEASURED,
    )
    assert [(a.machine_id, a.tasks) for a in got.allocations] == [("cheap", 10)]
    assert got.cost == P.Cost(zc=10.0, usd=0.0)


def test_cheapest_water_fills_inside_a_price_tier():
    """Cost is identical whichever way an equally-priced tier is split, so
    the split that finishes sooner is a free improvement. Without it,
    'cheapest' would present a plan another plan strictly dominates."""
    got = P.cheapest_plan(
        _task(gpus=1),
        [_machine("a", price_per_hour=600.0), _machine("b", price_per_hour=600.0)],
        eligible=ELIGIBLE,
        tasks=10,
        duration=MEASURED,
    )
    assert sorted(a.tasks for a in got.allocations) == [5, 5]
    assert got.cost == P.Cost(zc=10.0, usd=0.0)
    assert got.makespan_seconds == 30.0


def test_free_workspace_capacity_is_spent_before_anything_priced():
    """§6.2, computed rather than asserted: a plan that leaves your own idle
    gear alone while paying for cloud is nearly always wrong."""
    got = P.cheapest_plan(
        _task(gpus=1),
        [
            _machine("market", price_per_hour=600.0),
            _machine("mine", venue=P.VENUE_WORKSPACE, price_per_hour=0.0),
        ],
        eligible=ELIGIBLE,
        tasks=10,
        duration=MEASURED,
    )
    assert [a.machine_id for a in got.allocations] == ["mine"]
    assert got.cost == P.Cost()


def test_a_donated_zero_ask_is_priced_at_zero_not_at_a_floor():
    """M13: a zero ask is legal and labelled donated. Pricing it at a floor
    would misrepresent a real supply tier."""
    got = P.cheapest_plan(
        _task(gpus=1),
        [_machine("donor", price_per_hour=0.0)],
        eligible=ELIGIBLE,
        tasks=10,
        duration=MEASURED,
    )
    assert got.cost == P.Cost() and got.tasks_placed == 10


def test_a_negative_ask_raises_rather_than_becoming_the_cheapest_capacity():
    """Zero is donated; below zero is a machine that pays you to use it, and
    'cheapest' would take every task on the network to it."""
    for ask in (-0.01, float("-inf"), float("nan")):
        with pytest.raises(ValueError, match="per hour"):
            P.cheapest_plan(
                _task(gpus=1),
                [_machine("m", price_per_hour=ask)],
                eligible=ELIGIBLE,
                tasks=1,
                duration=MEASURED,
            )


def test_the_deadline_spills_the_overflow_to_the_next_cheapest_tier():
    got = P.balanced_plan(
        _task(gpus=1),
        [
            _machine("cheap", price_per_hour=600.0),
            _machine("dear", price_per_hour=1200.0),
        ],
        eligible=ELIGIBLE,
        tasks=10,
        duration=MEASURED,
        deadline=36.0,
    )
    counts = {a.machine_id: a.tasks for a in got.allocations}
    assert counts == {"cheap": 6, "dear": 4}
    assert got.deadline_met is True
    assert got.cost == P.Cost(zc=6.0 + 8.0, usd=0.0)


def test_an_unmeetable_deadline_reports_the_achievable_one_rather_than_failing():
    """A submitter can move a deadline. They cannot act on 'infeasible'."""
    got = P.balanced_plan(
        _task(gpus=1),
        [_machine("only", price_per_hour=600.0, seconds=60.0)],
        eligible=ELIGIBLE,
        tasks=40,
        duration=MEASURED,
        deadline=100.0,
    )
    assert got.tasks_placed == 1
    assert got.tasks_unplaced == 39
    assert got.deadline_met is False
    assert got.achievable_deadline_seconds == 2400.0
    assert any("do not fit" in note for note in got.notes)


def test_a_deadline_that_fits_exactly_is_met():
    """The boundary belongs to the machine; float accumulation must not turn
    an exact landing into a miss."""
    got = P.balanced_plan(
        _task(gpus=1),
        [_machine("m", price_per_hour=600.0)],
        eligible=ELIGIBLE,
        tasks=10,
        duration=MEASURED,
        deadline=60.0,
    )
    assert got.tasks_placed == 10 and got.deadline_met is True


def test_no_deadline_means_no_verdict_on_one():
    got = P.cheapest_plan(
        _task(gpus=1),
        [_machine("m", price_per_hour=600.0)],
        eligible=ELIGIBLE,
        tasks=4,
        duration=MEASURED,
    )
    assert got.deadline_met is None, "a fact about a question nobody asked"


# ---------------------------------------------------------------------------
# min makespan
# ---------------------------------------------------------------------------


def test_fastest_water_fills_to_equalise_finish_times():
    got = P.fastest_plan(
        _task(gpus=1),
        [
            _machine("a", price_per_hour=600.0),
            _machine("b", price_per_hour=600.0),
            _machine("c", price_per_hour=600.0),
        ],
        eligible=ELIGIBLE,
        tasks=9,
        duration=MEASURED,
    )
    assert sorted(a.tasks for a in got.allocations) == [3, 3, 3]
    assert got.makespan_seconds == 18.0


def test_fastest_uses_concurrency_as_extra_slots():
    got = P.fastest_plan(
        _task(gpus=1),
        [_machine("wide", concurrency=4, price_per_hour=600.0)],
        eligible=ELIGIBLE,
        tasks=8,
        duration=MEASURED,
    )
    assert got.makespan_seconds == 12.0, "two rounds of four"


def test_a_marginal_machine_too_slow_to_help_is_never_reached():
    """The water-fill's own stopping rule, not a separate one: a slot whose
    first task lands after every other slot's last is never the minimum of
    the heap."""
    got = P.fastest_plan(
        _task(gpus=1),
        [
            _machine("quick", seconds=1.0, price_per_hour=0.0),
            _machine("glacial", seconds=10_000.0, price_per_hour=0.0),
        ],
        eligible=ELIGIBLE,
        tasks=3,
        duration=MEASURED,
    )
    assert [a.machine_id for a in got.allocations] == ["quick"]
    assert got.makespan_seconds == 3.0


def test_a_slow_machine_is_used_when_it_does_reduce_the_max():
    got = P.fastest_plan(
        _task(gpus=1),
        [
            _machine("quick", seconds=1.0, price_per_hour=0.0),
            _machine("slow", seconds=2.0, price_per_hour=0.0),
        ],
        eligible=ELIGIBLE,
        tasks=3,
        duration=MEASURED,
    )
    counts = {a.machine_id: a.tasks for a in got.allocations}
    assert counts == {"quick": 2, "slow": 1}
    assert got.makespan_seconds == 2.0


def test_fastest_ignores_price_entirely():
    """It spends a million an hour to save 98 seconds and does not blink,
    which is the honest meaning of "fastest" and also why this plan is
    usually the expensive corner of the frontier."""
    fleet = [
        _machine("free-slow", price_per_hour=0.0, seconds=100.0),
        _machine("dear-fast", price_per_hour=1e6, seconds=1.0),
    ]
    fastest = P.fastest_plan(
        _task(gpus=1), fleet, eligible=ELIGIBLE, tasks=2, duration=MEASURED
    )
    assert [a.machine_id for a in fastest.allocations] == ["dear-fast"]
    assert fastest.makespan_seconds == 2.0
    assert fastest.cost.zc > 0

    cheapest = P.cheapest_plan(
        _task(gpus=1), fleet, eligible=ELIGIBLE, tasks=2, duration=MEASURED
    )
    assert [a.machine_id for a in cheapest.allocations] == ["free-slow"]
    assert cheapest.cost == P.Cost()


def test_a_plan_that_places_nothing_reports_no_makespan_rather_than_zero():
    got = P.fastest_plan(
        _task(gpus=1), [], eligible=ELIGIBLE, tasks=5, duration=MEASURED
    )
    assert got.makespan_seconds is None, "0.0 would read as instantaneous"
    assert got.tasks_unplaced == 5


# ---------------------------------------------------------------------------
# reliability ranks, never excludes, never divides
# ---------------------------------------------------------------------------


def test_a_shaky_host_is_still_offered_work():
    """``scheduler/__init__.py:637`` — refusing work to flaky hosts starves
    small pools, and a volunteer network is made of unreliable machines by
    construction; surviving them is the product."""
    got = P.cheapest_plan(
        _task(gpus=1),
        [_machine("flaky", price_per_hour=600.0, tier=est.TIER_SHAKY)],
        eligible=ELIGIBLE,
        tasks=10,
        duration=MEASURED,
    )
    assert got.tasks_placed == 10


def test_reliability_never_removes_a_candidate_from_the_fleet():
    fleet = P.eligible_fleet(
        _task(gpus=1),
        [
            _machine("a", tier=est.TIER_SHAKY),
            _machine("b", tier=est.TIER_UNPROVEN),
            _machine("c", tier=est.TIER_STEADY),
        ],
        eligible=ELIGIBLE,
    )
    assert len(fleet) == 3


def test_the_steadier_host_takes_the_slot_when_there_are_not_enough_tasks():
    got = P.cheapest_plan(
        _task(gpus=1),
        [
            _machine("shaky", price_per_hour=600.0, tier=est.TIER_SHAKY),
            _machine("steady", price_per_hour=600.0, tier=est.TIER_STEADY),
        ],
        eligible=ELIGIBLE,
        tasks=1,
        duration=MEASURED,
    )
    assert [a.machine_id for a in got.allocations] == ["steady"]


def test_an_unproven_host_outranks_a_demonstrably_bad_one():
    got = P.cheapest_plan(
        _task(gpus=1),
        [
            _machine("shaky", price_per_hour=600.0, tier=est.TIER_SHAKY),
            _machine("new", price_per_hour=600.0, tier=est.TIER_UNPROVEN),
        ],
        eligible=ELIGIBLE,
        tasks=1,
        duration=MEASURED,
    )
    assert [a.machine_id for a in got.allocations] == ["new"]


def test_a_proven_host_outranks_an_unproven_one():
    got = P.cheapest_plan(
        _task(gpus=1),
        [
            _machine("new", price_per_hour=600.0, tier=est.TIER_UNPROVEN),
            _machine("steady", price_per_hour=600.0, tier=est.TIER_STEADY),
        ],
        eligible=ELIGIBLE,
        tasks=1,
        duration=MEASURED,
    )
    assert [a.machine_id for a in got.allocations] == ["steady"]


def test_an_unproven_host_is_priced_at_its_ask_with_no_synthesised_denominator():
    """No ``ask ÷ goodput`` and no ``1 / P(accepted)`` retry overhead: both
    were in the first draft and both invented a number the ledger does not
    hold. 10 tasks x 600 ZC/h x 6s is exactly 10 ZC, whatever the tier."""
    costs = {}
    for tier in (est.TIER_UNPROVEN, est.TIER_STEADY, est.TIER_MIXED, est.TIER_SHAKY):
        got = P.cheapest_plan(
            _task(gpus=1),
            [_machine("m", price_per_hour=600.0, tier=tier)],
            eligible=ELIGIBLE,
            tasks=10,
            duration=MEASURED,
        )
        costs[tier] = got.cost
    assert set(costs.values()) == {P.Cost(zc=10.0, usd=0.0)}


def test_reliability_never_splits_a_price_tier():
    """Splitting one would hand every task to the steadiest machine while an
    identically-priced, identically-fast neighbour idled: same cost, far
    later finish, i.e. a dominated plan chosen on reliability grounds."""
    got = P.cheapest_plan(
        _task(gpus=1),
        [
            _machine("steady", price_per_hour=600.0, tier=est.TIER_STEADY),
            _machine("shaky", price_per_hour=600.0, tier=est.TIER_SHAKY),
        ],
        eligible=ELIGIBLE,
        tasks=10,
        duration=MEASURED,
    )
    assert sorted(a.tasks for a in got.allocations) == [5, 5]


def test_a_tier_the_router_does_not_recognise_is_treated_as_unproven():
    got = P.cheapest_plan(
        _task(gpus=1),
        [
            _machine("odd", price_per_hour=600.0, tier="excellent"),
            _machine("shaky", price_per_hour=600.0, tier=est.TIER_SHAKY),
        ],
        eligible=ELIGIBLE,
        tasks=1,
        duration=MEASURED,
    )
    assert [a.machine_id for a in got.allocations] == ["odd"]


# ---------------------------------------------------------------------------
# cost is a vector
# ---------------------------------------------------------------------------


def test_cost_offers_no_way_to_reduce_two_currencies_to_one_number():
    """Every one of these would have to answer "how many dollars is a Zolli
    credit?", which M4 forbids and which ``contributions.py`` refuses in the
    strongest terms it has."""
    for forbidden in ("total", "sum", "amount", "as_usd", "as_zc", "value"):
        assert not hasattr(P.Cost, forbidden), forbidden
    cost = P.Cost(zc=9.8, usd=6.4)
    with pytest.raises(TypeError):
        float(cost)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        int(cost)  # type: ignore[call-overload]
    with pytest.raises(TypeError):
        cost + 1  # type: ignore[operator]


def test_adding_costs_stays_component_wise():
    assert P.Cost(zc=1.0) + P.Cost(usd=2.0) == P.Cost(zc=1.0, usd=2.0)
    assert P.Cost(zc=1.0, usd=1.0) + P.Cost(zc=2.0, usd=3.0) == P.Cost(zc=3.0, usd=4.0)


def test_a_mixed_currency_plan_reports_both_and_sums_neither():
    got = P.fastest_plan(
        _task(gpus=1),
        [
            _machine("zolli", currency=P.CURRENCY_ZC, price_per_hour=600.0),
            _machine("rented", venue=P.VENUE_RENTED, currency=P.CURRENCY_USD,
                     price_per_hour=1200.0),
        ],
        eligible=ELIGIBLE,
        tasks=10,
        duration=MEASURED,
    )
    assert got.cost == P.Cost(zc=5.0, usd=10.0)
    assert set(got.cost.currencies()) == {P.CURRENCY_ZC, P.CURRENCY_USD}
    assert any("never summed" in note for note in got.notes)


def test_each_allocation_carries_its_own_currency():
    got = P.fastest_plan(
        _task(gpus=1),
        [
            _machine("zolli", currency=P.CURRENCY_ZC, price_per_hour=600.0),
            _machine("rented", venue=P.VENUE_RENTED, currency=P.CURRENCY_USD,
                     price_per_hour=600.0),
        ],
        eligible=ELIGIBLE,
        tasks=10,
        duration=MEASURED,
    )
    by_id = {a.machine_id: a for a in got.allocations}
    assert by_id["zolli"].cost == P.Cost(zc=5.0)
    assert by_id["rented"].cost == P.Cost(usd=5.0)


def test_an_unknown_currency_raises_rather_than_quoting_a_machine_as_free():
    with pytest.raises(ValueError, match="unknown currency"):
        P.cheapest_plan(
            _task(gpus=1),
            [_machine("m", currency="BTC", price_per_hour=1.0)],
            eligible=ELIGIBLE,
            tasks=1,
            duration=MEASURED,
        )


def test_currencies_never_decide_an_order_by_magnitude():
    """Crossing from ZC to USD is a preference stated once in the venue order,
    not a comparison of two numbers with no rate between them. A USD machine
    priced far below a ZC one is still second."""
    got = P.cheapest_plan(
        _task(gpus=1),
        [
            _machine("zolli", venue=P.VENUE_MARKET, currency=P.CURRENCY_ZC,
                     price_per_hour=600.0),
            _machine("rented", venue=P.VENUE_RENTED, currency=P.CURRENCY_USD,
                     price_per_hour=0.006),
        ],
        eligible=ELIGIBLE,
        tasks=5,
        duration=MEASURED,
    )
    assert [a.machine_id for a in got.allocations] == ["zolli"]


def test_a_caller_may_state_a_different_venue_preference():
    got = P.cheapest_plan(
        _task(gpus=1),
        [
            _machine("zolli", venue=P.VENUE_MARKET, currency=P.CURRENCY_ZC,
                     price_per_hour=600.0),
            _machine("rented", venue=P.VENUE_RENTED, currency=P.CURRENCY_USD,
                     price_per_hour=600.0),
        ],
        eligible=ELIGIBLE,
        tasks=5,
        duration=MEASURED,
        venue_order=(P.VENUE_RENTED, P.VENUE_WORKSPACE, P.VENUE_MARKET),
    )
    assert [a.machine_id for a in got.allocations] == ["rented"]


def test_an_unrecognised_venue_sorts_last_and_is_never_dropped():
    got = P.cheapest_plan(
        _task(gpus=1),
        [
            _machine("mystery", venue="somewhere-else", price_per_hour=0.0),
            _machine("market", venue=P.VENUE_MARKET, price_per_hour=600.0),
        ],
        eligible=ELIGIBLE,
        tasks=5,
        duration=MEASURED,
    )
    assert [a.machine_id for a in got.allocations] == ["market"]
    assert P.eligible_fleet(
        _task(gpus=1),
        [_machine("mystery", venue="somewhere-else")],
        eligible=ELIGIBLE,
    ) != ()


# ---------------------------------------------------------------------------
# the frontier
# ---------------------------------------------------------------------------


def _plan(name, zc=0.0, usd=0.0, makespan=10.0, unplaced=0) -> P.Plan:
    return P.Plan(
        name=name,
        cost=P.Cost(zc=zc, usd=usd),
        makespan_seconds=makespan,
        tasks_unplaced=unplaced,
    )


def test_a_strictly_worse_plan_is_marked_dominated():
    marked = P.frontier([_plan("a", zc=5.0, makespan=10.0),
                         _plan("b", zc=9.0, makespan=20.0)])
    assert marked[0].dominated_by is None
    assert marked[1].dominated_by == "a"


def test_cheaper_in_one_currency_and_dearer_in_another_is_not_dominated():
    """The whole reason this is a frontier and not a ranking: with no rate
    between ZC and USD the two plans are genuinely incomparable, and the
    correct output is both of them."""
    marked = P.frontier([_plan("zolli", zc=9.8, makespan=31.0),
                         _plan("rented", usd=6.4, makespan=9.0)])
    assert all(plan.dominated_by is None for plan in marked)


def test_an_identical_plan_shown_twice_is_marked_rather_than_presented_as_a_choice():
    marked = P.frontier([_plan("cheapest", zc=5.0), _plan("balanced", zc=5.0)])
    assert marked[0].dominated_by is None
    assert marked[1].dominated_by == "cheapest"


def test_unplaced_tasks_lead_the_dominance_vector():
    """Without that, a plan placing two of forty trials looks like the
    cheapest and fastest thing on the board."""
    marked = P.frontier([_plan("partial", zc=0.5, makespan=2.0, unplaced=38),
                         _plan("whole", zc=9.0, makespan=30.0, unplaced=0)])
    assert marked[0].dominated_by is None
    assert marked[1].dominated_by is None


def test_nothing_is_dropped_from_the_frontier():
    plans = [_plan("a", zc=1.0), _plan("b", zc=2.0), _plan("c", zc=3.0)]
    assert len(P.frontier(plans)) == 3


# ---------------------------------------------------------------------------
# the whole thing
# ---------------------------------------------------------------------------


def test_plan_job_produces_three_points_when_a_deadline_is_given():
    got = P.plan_job(
        _request(
            [
                _machine("mine", venue=P.VENUE_WORKSPACE, price_per_hour=0.0),
                _machine("market", price_per_hour=600.0),
                _machine("rented", venue=P.VENUE_RENTED, currency=P.CURRENCY_USD,
                         price_per_hour=1200.0, concurrency=4),
            ],
            deadline_seconds=600.0,
        ),
        eligible=ELIGIBLE,
    )
    assert got.cheapest is not None
    assert got.balanced is not None
    assert got.fastest is not None
    assert got.recommended in {P.PLAN_CHEAPEST, P.PLAN_BALANCED, P.PLAN_FASTEST}
    assert got.eligible_machines == 3 and got.excluded_machines == 0


def test_with_no_deadline_there_is_no_third_row_invented():
    """'Balanced' is the cheapest fleet that still makes a time. With no time
    to make it would only be 'cheapest' shown twice, and the design says
    these are points on a frontier, not a UI convenience."""
    got = P.plan_job(
        _request([_machine("m", price_per_hour=600.0)]), eligible=ELIGIBLE
    )
    assert got.balanced is None
    assert any("no deadline" in note for note in got.notes)


def test_the_cheapest_plan_still_says_whether_it_misses_your_deadline():
    """It is built unconstrained — it is the frontier's cost corner — but
    "2h04, against your 30 minutes" is the whole reason the other rows are
    on the page."""
    got = P.plan_job(
        _request(
            [
                _machine("slow-free", venue=P.VENUE_WORKSPACE, price_per_hour=0.0),
                _machine("fast-paid", price_per_hour=600.0, concurrency=20),
            ],
            tasks=40,
            deadline_seconds=100.0,
        ),
        eligible=ELIGIBLE,
    )
    assert got.cheapest is not None and got.balanced is not None
    assert got.cheapest.cost == P.Cost()
    assert got.cheapest.deadline_met is False
    assert got.cheapest.makespan_seconds is not None
    assert got.cheapest.makespan_seconds > 100.0
    assert got.balanced.deadline_met is True
    assert got.recommended == P.PLAN_BALANCED


def test_the_recommendation_is_never_a_dominated_plan():
    got = P.plan_job(
        _request(
            [_machine("only", venue=P.VENUE_WORKSPACE, price_per_hour=0.0)],
            deadline_seconds=1000.0,
        ),
        eligible=ELIGIBLE,
    )
    named = {plan.name: plan for plan in got.plans()}
    assert got.recommended is not None
    assert named[got.recommended].dominated_by is None


def test_ineligible_machines_are_counted_not_hidden():
    got = P.plan_job(
        _request(
            [
                _machine("gpu", price_per_hour=600.0),
                _machine("cpu", node=_cpu_node("cpu"), price_per_hour=0.0),
            ]
        ),
        eligible=ELIGIBLE,
    )
    assert got.eligible_machines == 1 and got.excluded_machines == 1


def test_an_eligible_but_unmeasurable_machine_is_reported_not_guessed():
    """Eligible, allowed to claim, simply not yet something a number can be
    attached to. Not dropped, and not filled in with a plausible substitute."""
    got = P.plan_job(
        _request([_machine("m")], duration=None),
        eligible=ELIGIBLE,
    )
    assert got.eligible_machines == 1
    assert got.unplannable_machines == 1
    assert got.plans() == ()
    assert got.cheapest is None and got.fastest is None


def test_a_projected_duration_is_flagged_on_the_whole_plan_set():
    got = P.plan_job(
        _request([_machine("m", price_per_hour=600.0)], duration=PROJECTED),
        eligible=ELIGIBLE,
    )
    assert got.cheapest is not None
    assert got.cheapest.duration_basis == est.BASIS_PROJECTED
    assert any("projected range" in note for note in got.notes)


def test_a_plan_is_built_on_the_top_of_a_projected_band():
    got = P.plan_job(
        _request([_machine("m", price_per_hour=600.0)], tasks=1, duration=PROJECTED),
        eligible=ELIGIBLE,
    )
    assert got.cheapest is not None
    assert got.cheapest.makespan_seconds == PROJECTED.high


# ---------------------------------------------------------------------------
# a plan is a fleet, not an assignment
# ---------------------------------------------------------------------------


def test_an_allocation_names_no_task():
    """FlashML is a pull system: nodes claim, and the coordinator never picks
    a node for a task. A task->machine binding would describe a mechanism
    that does not exist and could not be enforced if it did."""
    fields = set(P.Allocation.__dataclass_fields__)
    assert "task_id" not in fields and "task_ids" not in fields
    assert "tasks" in fields, "counts, which is a prediction, not a binding"


def test_a_plan_never_reserves_capacity():
    """Nothing here writes, holds, or claims anything. Two identical calls
    produce two identical quotes rather than the second seeing the first's
    reservation."""
    request = _request([_machine("m", price_per_hour=600.0)], deadline_seconds=600.0)
    assert P.plan_job(request, eligible=ELIGIBLE) == P.plan_job(
        request, eligible=ELIGIBLE
    )


# ---------------------------------------------------------------------------
# the canary seam
# ---------------------------------------------------------------------------


def test_no_history_offers_a_canary_instead_of_a_number():
    got = P.plan_job(
        _request([_machine("m")], duration=None, tasks=40), eligible=ELIGIBLE
    )
    assert got.canary is not None
    assert got.canary.machine_id == "m"
    assert got.canary.tasks_to_calibrate == 39
    assert "no usable history" in got.canary.reason


def test_a_projected_estimate_also_offers_a_canary():
    got = P.plan_job(
        _request([_machine("m", price_per_hour=600.0)], duration=PROJECTED),
        eligible=ELIGIBLE,
    )
    assert got.canary is not None
    assert got.canary.current_basis == est.BASIS_PROJECTED


def test_a_measured_estimate_needs_no_canary():
    got = P.plan_job(
        _request([_machine("m", price_per_hour=600.0)], duration=MEASURED),
        eligible=ELIGIBLE,
    )
    assert got.canary is None


def test_a_single_task_job_is_its_own_canary():
    got = P.plan_job(
        _request([_machine("m")], duration=None, tasks=1), eligible=ELIGIBLE
    )
    assert got.canary is None


def test_the_probe_goes_to_the_machine_the_plan_leans_on():
    got = P.plan_job(
        _request(
            [
                _machine("bulk", price_per_hour=600.0, concurrency=8),
                _machine("edge", price_per_hour=600.0),
            ],
            duration=PROJECTED,
            tasks=40,
            deadline_seconds=1000.0,
        ),
        eligible=ELIGIBLE,
    )
    assert got.canary is not None and got.canary.machine_id == "bulk"


def test_the_canary_round_trip_replaces_a_refusal_with_a_measurement():
    """One task calibrates thirty-nine. This is the seam, end to end: no
    estimate, run the probe, feed the measured seconds back, get a plan."""
    fleet = [_machine("m", price_per_hour=600.0)]
    blind = P.plan_job(
        _request(fleet, duration=None, tasks=40), eligible=ELIGIBLE
    )
    assert blind.plans() == () and blind.canary is not None

    measured = est.from_canary(
        6.0,
        capability_class="gpu-24gb",
        tasks_remaining=blind.canary.tasks_to_calibrate,
    )
    assert measured is not None

    informed = P.plan_job(
        _request(fleet, duration=measured, tasks=39), eligible=ELIGIBLE
    )
    assert informed.cheapest is not None
    assert informed.cheapest.tasks_placed == 39
    assert informed.cheapest.duration_basis == est.BASIS_PROJECTED
    assert informed.canary is not None, "one run is still not five"


def test_nothing_in_the_canary_submits_anything():
    """It names the probe and what it would buy. The caller decides."""
    fields = set(P.Canary.__dataclass_fields__)
    assert fields == {"machine_id", "tasks_to_calibrate", "reason", "current_basis"}


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_the_same_fleet_in_a_different_order_produces_the_same_plans():
    fleet = [
        _machine("a", price_per_hour=600.0, tier=est.TIER_STEADY),
        _machine("b", price_per_hour=600.0, tier=est.TIER_SHAKY),
        _machine("c", venue=P.VENUE_WORKSPACE, price_per_hour=0.0),
        _machine("d", venue=P.VENUE_RENTED, currency=P.CURRENCY_USD,
                 price_per_hour=1200.0, concurrency=3),
    ]
    forward = P.plan_job(
        _request(fleet, deadline_seconds=300.0), eligible=ELIGIBLE
    )
    backward = P.plan_job(
        _request(list(reversed(fleet)), deadline_seconds=300.0), eligible=ELIGIBLE
    )
    assert forward.plans() == backward.plans()
    assert forward.recommended == backward.recommended
    assert forward.canary == backward.canary


def test_equal_machines_split_the_same_way_every_time():
    fleet = [_machine(f"m{i}", price_per_hour=600.0) for i in range(5)]
    first = P.plan_job(_request(fleet, tasks=13), eligible=ELIGIBLE)
    second = P.plan_job(_request(fleet, tasks=13), eligible=ELIGIBLE)
    assert first.cheapest == second.cheapest


def test_a_large_job_stays_fast():
    """It renders behind a submit button. O(M log M + N log min(N, S))."""
    fleet = [_machine(f"m{i}", price_per_hour=600.0, concurrency=4) for i in range(50)]
    got = P.plan_job(
        _request(fleet, tasks=5000, deadline_seconds=100_000.0), eligible=ELIGIBLE
    )
    assert got.cheapest is not None and got.cheapest.tasks_placed == 5000


def test_plans_are_frozen():
    got = P.plan_job(
        _request([_machine("m", price_per_hour=600.0)]), eligible=ELIGIBLE
    )
    assert got.cheapest is not None
    with pytest.raises(Exception):
        got.cheapest.name = "tampered"  # type: ignore[misc]
