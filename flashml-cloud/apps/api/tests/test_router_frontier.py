"""The curve between cheapest and fastest.

The honest core: speedup is bounded by TASK COUNT. A fill spreads N tasks
over M machines, so if N is 1 no fleet on earth makes it faster, and the
frontier must say so rather than sloping downward to sell capacity."""
from __future__ import annotations

from flashml_cloud_api.router.frontier import frontier


def test_a_single_task_job_gains_nothing_from_more_machines():
    points = frontier(task_count=1, task_seconds=600.0, owned_slots=1,
                      rentable_slots=8, usd_per_hour=1.0)
    assert len(points) >= 1
    # Every point finishes at the same time as the first.
    assert len({round(p.finish_seconds) for p in points}) == 1
    assert points[-1].advice_code == "no_parallelism"


def test_speedup_stops_at_the_task_count():
    points = frontier(task_count=4, task_seconds=600.0, owned_slots=1,
                      rentable_slots=8, usd_per_hour=1.0)
    by_slots = {p.total_slots: p for p in points}
    # Four tasks over four slots is as fast as it gets; a fifth slot is
    # spend for nothing and must be labelled as such.
    assert by_slots[4].finish_seconds == by_slots[8].finish_seconds
    assert by_slots[8].advice_code == "beyond_task_count"


def test_cost_rises_only_with_rented_slots():
    points = frontier(task_count=8, task_seconds=600.0, owned_slots=2,
                      rentable_slots=2, usd_per_hour=1.0)
    zero = [p for p in points if p.rented_slots == 0]
    assert zero and all(p.usd_cost == 0.0 for p in zero)
    assert any(p.usd_cost > 0.0 for p in points if p.rented_slots > 0)


def test_points_are_ordered_by_fleet_size():
    points = frontier(task_count=8, task_seconds=60.0, owned_slots=1,
                      rentable_slots=3, usd_per_hour=1.0)
    assert [p.total_slots for p in points] == sorted(
        p.total_slots for p in points
    )
