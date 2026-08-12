"""Two ceilings, because they answer different questions.

A per-acquisition cap bounds ONE mistake. A rolling-window cap bounds a
LOOP of correct-looking decisions, which is what actually empties an
account. The window one is the load-bearing half.

Both refuse. Neither queues.
"""
from __future__ import annotations

from typing import Any

import psycopg

__all__ = ["BudgetRefused", "assert_within_budget", "window_spend_usd"]


class BudgetRefused(RuntimeError):
    """Acquisition would exceed a ceiling. Not retryable by waiting."""


def window_spend_usd(db: psycopg.Connection, *, hours: float) -> float:
    """Committed hourly rate across acquisitions opened in the window.

    Deliberately counts REQUESTED as well as ACTIVE and RELEASED: a row in
    REQUESTED may already have created something at the venue that we have
    not yet learned the handle for, and pretending it costs nothing is how
    a retry loop spends without ever being counted.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select coalesce(sum(usd_per_hour), 0)::float8 as total
              from public.rented_capacity
             where created_at > now() - make_interval(secs => %s)
            """,
            # `secs` is the one make_interval() argument typed `double
            # precision`; `hours` is `integer`, and psycopg binds a Python
            # float as float8 -- named notation then finds no matching
            # overload ("make_interval(hours => double precision) does not
            # exist"). Converting to seconds keeps the fractional-hour
            # precision `rented_usd_window_hours` allows for.
            (float(hours) * 3600.0,),
        )
        row = cur.fetchone()
    return float(row["total"] if row else 0.0)


def assert_within_budget(
    db: psycopg.Connection, *, venue_id: str, usd_per_hour: float | None,
    settings: Any,
) -> None:
    """Raise :class:`BudgetRefused` unless this acquisition fits both caps."""
    # An unpriced acquisition is refused rather than treated as free. A
    # venue that will not tell us the price is a venue we cannot bound.
    if usd_per_hour is None:
        raise BudgetRefused(
            f"venue {venue_id} quoted no price; an unpriced acquisition "
            "cannot be bounded and is refused"
        )

    per = float(settings.rented_usd_per_acquisition_max)
    if usd_per_hour > per:
        raise BudgetRefused(
            f"{venue_id} at ${usd_per_hour}/hr exceeds the per-acquisition "
            f"ceiling of ${per}/hr"
        )

    hours = float(settings.rented_usd_window_hours)
    cap = float(settings.rented_usd_window_max)
    already = window_spend_usd(db, hours=hours)
    if already + usd_per_hour > cap:
        raise BudgetRefused(
            f"the {hours}h window already commits ${already}/hr; adding "
            f"${usd_per_hour}/hr would exceed the window ceiling of ${cap}/hr"
        )
