"""The buyer's curve: what each additional machine buys, and when it buys
nothing.

`plan.py` computes the two endpoints -- cheapest as a price-ordered scan,
fastest as a water-fill. This is the sweep between them, and it exists to
make ONE thing visible that a two-endpoint view hides: the point past
which more machines change nothing.

That point is the task count. `plan.py`'s arithmetic works because the
tasks it targets are independent; a fill spreads N tasks over M slots. For
N = 1 -- a COMMAND job, a single TRAINING task -- the answer is that no
fleet is faster, and saying otherwise sells somebody a GPU that cannot
help them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["ADVICE_BEYOND_TASK_COUNT", "ADVICE_HELPS", "ADVICE_NO_PARALLELISM",
           "FrontierPoint", "frontier"]

#: More slots than tasks. Spend buys nothing from here on.
ADVICE_BEYOND_TASK_COUNT = "beyond_task_count"
#: One task. No fleet is faster, at any price.
ADVICE_NO_PARALLELISM = "no_parallelism"
#: This point is genuinely faster than the one before it.
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
    for rented in range(0, max(0, rentable_slots) + 1):
        total = max(0, owned_slots) + rented
        if total <= 0:
            continue
        finish = _finish_seconds(task_count, task_seconds, total)

        if task_count <= 1:
            advice = ADVICE_NO_PARALLELISM
        elif total > task_count:
            advice = ADVICE_BEYOND_TASK_COUNT
        else:
            advice = ADVICE_HELPS

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
