"""The buyer's curve: what each additional machine buys, and when it buys
nothing.

For one homogeneous task duration and a scalar slot count, this module
sweeps fleet sizes from `owned_slots` up to `owned_slots + rentable_slots` --
one point per rented slot -- and labels each point with why it does or does
not help: a single task that no fleet parallelizes (`no_parallelism`), a
fleet larger than the task count has any use for (`beyond_task_count`), a
fleet size that costs more than the one before it but does not finish any
sooner because `ceil(task_count / slots)` just landed on the same step
(`no_marginal_gain`), or a fleet size that is genuinely faster than the one
before it (`helps`). That last label is the entire point of the module: a
curve that always slopes downward would be a sales tool, not a planner, and
would spend somebody's money on a machine that cannot help them.

This does NOT reuse `plan.py`'s fill, and is not a simplified copy of it.
`plan.py._fill` is a private, unexported water-fill over heterogeneous
machines with mixed per-task durations, currencies and reliability tiers --
a real scheduling problem with a heap and a greedy proof behind it. This
module answers a coarser question -- one task duration, one slot count, what
does finish time look like as fleet size grows -- which has a closed form
(`ceil(task_count / slots) * task_seconds`, see `_finish_seconds`) and needs
none of that machinery. So it imports nothing from `plan.py`, and could
not: `plan.py._fill` is private and not part of `plan.py.__all__`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["ADVICE_BEYOND_TASK_COUNT", "ADVICE_HELPS", "ADVICE_NO_MARGINAL_GAIN",
           "ADVICE_NO_PARALLELISM", "FrontierPoint", "frontier"]

#: More slots than tasks. Spend buys nothing from here on.
ADVICE_BEYOND_TASK_COUNT = "beyond_task_count"
#: One task. No fleet is faster, at any price. Checked first, so this takes
#: priority over every other advice code -- a single-task job is never
#: `beyond_task_count`, `no_marginal_gain`, or `helps`, only this.
ADVICE_NO_PARALLELISM = "no_parallelism"
#: This point costs more than the one before it (one more rented slot) but
#: finishes no sooner. `ceil(task_count / slots)` is a step function, so
#: there are fleet sizes below the task-count cutoff where adding a slot
#: does not change which step the finish time sits on. Distinct from
#: `beyond_task_count`, which is about being past the task count entirely --
#: this point is still at or under it, it just does not pay for itself.
ADVICE_NO_MARGINAL_GAIN = "no_marginal_gain"
#: This point is genuinely faster than the one before it in the sweep (or is
#: the first point, which has no "before it" to compare against).
ADVICE_HELPS = "helps"


@dataclass(frozen=True)
class FrontierPoint:
    total_slots: int
    owned_slots: int
    rented_slots: int
    finish_seconds: float
    usd_cost: float
    advice_code: str


def _finish_seconds(task_count: int, task_seconds: float, slots: int) -> float:
    """Water-fill: the slowest slot's pile is what everyone waits for."""
    if slots <= 0:
        return math.inf
    per_slot = math.ceil(task_count / slots)
    return float(per_slot) * float(task_seconds)


def frontier(
    *,
    task_count: int,
    task_seconds: float,
    owned_slots: int,
    rentable_slots: int,
    usd_per_hour: float,
) -> list[FrontierPoint]:
    """One point per fleet size, from owned-only up to owned + rentable."""
    points: list[FrontierPoint] = []
    previous_finish: float | None = None
    for rented in range(0, max(0, rentable_slots) + 1):
        total = max(0, owned_slots) + rented
        if total <= 0:
            continue
        finish = _finish_seconds(task_count, task_seconds, total)

        if task_count <= 1:
            advice = ADVICE_NO_PARALLELISM
        elif total > task_count:
            advice = ADVICE_BEYOND_TASK_COUNT
        elif previous_finish is not None and finish >= previous_finish:
            # More slots than the previous point, same or worse finish time:
            # `ceil(task_count / slots)` did not step down. This slot costs
            # money and buys zero time.
            advice = ADVICE_NO_MARGINAL_GAIN
        else:
            advice = ADVICE_HELPS
        previous_finish = finish

        # Rented capacity bills for the wall-clock it is held, which is the
        # whole job -- not for the fraction of it that this slot was busy.
        usd = float(rented) * float(usd_per_hour) * (finish / 3600.0)
        points.append(
            FrontierPoint(
                total_slots=total,
                owned_slots=max(0, owned_slots),
                rented_slots=rented,
                finish_seconds=finish,
                usd_cost=round(usd, 4),
                advice_code=advice,
            )
        )
    return points
