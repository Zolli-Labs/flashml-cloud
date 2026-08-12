"""The curve between cheapest and fastest.

The honest core: speedup is bounded by TASK COUNT. A fill spreads N tasks
over M machines, so if N is 1 no fleet on earth makes it faster, and the
curve must say so rather than sloping downward to sell capacity."""
from __future__ import annotations

from flashml_cloud_api.router.tradeoff import tradeoff_curve


def test_a_single_task_job_gains_nothing_from_more_machines():
    points = tradeoff_curve(task_count=1, task_seconds=600.0, owned_slots=1,
                      rentable_slots=8, usd_per_hour=1.0)
    assert len(points) >= 1
    # Every point finishes at the same time as the first.
    assert len({round(p.finish_seconds) for p in points}) == 1
    assert points[-1].advice_code == "no_parallelism"
    # An implementation that always returned 0 would pass every assertion
    # above. Pin the real number: one task takes exactly task_seconds, no
    # matter how many slots are offered.
    assert points[0].finish_seconds == 600.0
    assert all(p.finish_seconds == 600.0 for p in points)


def test_speedup_stops_at_the_task_count():
    points = tradeoff_curve(task_count=4, task_seconds=600.0, owned_slots=1,
                      rentable_slots=8, usd_per_hour=1.0)
    by_slots = {p.total_slots: p for p in points}
    # Four tasks over four slots is as fast as it gets; a fifth slot is
    # spend for nothing and must be labelled as such.
    assert by_slots[4].finish_seconds == by_slots[8].finish_seconds
    assert by_slots[8].advice_code == "beyond_task_count"
    # Pin the concrete numbers, not just their equality: 4 tasks over 4
    # slots is one task each, and total_slots=8 (7 rented slots at $1/h)
    # bills for the whole job's 600s wall clock: 7 * 1.0 * (600/3600).
    assert by_slots[4].finish_seconds == 600.0
    assert by_slots[8].usd_cost == 1.1667


def test_a_fleet_size_that_costs_more_and_finishes_no_sooner_never_helps():
    """The regression this suite exists to prevent: `ceil(task_count /
    slots)` is a step function, so some fleet sizes below the task-count
    cutoff cost more and buy zero time. Those must never be labelled
    `helps` -- that label is reserved for a point that is genuinely faster
    than the one before it."""
    points = tradeoff_curve(task_count=4, task_seconds=600.0, owned_slots=1,
                      rentable_slots=8, usd_per_hour=1.0)
    by_slots = {p.total_slots: p for p in points}
    # total_slots=2: ceil(4/2)=2 -> 1200.0s, half of total_slots=1's 2400.0s.
    # Genuinely faster than the point before it.
    assert by_slots[2].finish_seconds == 1200.0
    assert by_slots[2].advice_code == "helps"
    # total_slots=3: ceil(4/3)=2 -> 1200.0s, identical to total_slots=2. The
    # third slot costs money and buys zero time; it must not say "helps".
    assert by_slots[3].finish_seconds == 1200.0
    assert by_slots[3].advice_code != "helps"
    assert by_slots[3].advice_code == "no_marginal_gain"


def test_the_owned_baseline_is_not_advertised_as_a_purchase_win():
    """`helps` means genuinely faster than the point before it -- a
    comparison that only makes sense once there IS a point before it. The
    zero-rented-slots point is the buyer's own hardware at zero cost, with
    no predecessor in the sweep, so it must carry its own `baseline` code
    rather than falling through to `helps` (or to any other code that
    exists to describe whether a purchase paid off)."""
    points = tradeoff_curve(task_count=4, task_seconds=600.0, owned_slots=1,
                      rentable_slots=8, usd_per_hour=1.0)
    owned_only = [p for p in points if p.rented_slots == 0]
    assert len(owned_only) == 1
    assert owned_only[0].advice_code == "baseline"
    assert owned_only[0].advice_code != "helps"
    assert owned_only[0].usd_cost == 0.0
    # Every other point in this sweep is a real purchase decision and must
    # not be baseline.
    assert all(p.advice_code != "baseline" for p in points if p.rented_slots > 0)


def test_cost_rises_only_with_rented_slots():
    points = tradeoff_curve(task_count=8, task_seconds=600.0, owned_slots=2,
                      rentable_slots=2, usd_per_hour=1.0)
    zero = [p for p in points if p.rented_slots == 0]
    assert zero and all(p.usd_cost == 0.0 for p in zero)
    assert any(p.usd_cost > 0.0 for p in points if p.rented_slots > 0)


def test_points_are_ordered_by_fleet_size():
    points = tradeoff_curve(task_count=8, task_seconds=60.0, owned_slots=1,
                      rentable_slots=3, usd_per_hour=1.0)
    assert [p.total_slots for p in points] == sorted(
        p.total_slots for p in points
    )
