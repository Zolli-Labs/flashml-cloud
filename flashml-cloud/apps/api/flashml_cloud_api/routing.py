"""Submit-time routing: resolve what a job needs, plan against the book.

Design: docs/superpowers/specs/2026-08-11-zolli-marketplace-design.md §4/§6
and docs (repo root)/research/2026-08-13-automatic-routing-marketplace-matching.md §5.
This module never imports the runtime; eligibility stays the coordinator's,
matching stays `marketplace`'s. Everything here is orchestration and reasons.
"""
from __future__ import annotations

from typing import Any, Mapping

import psycopg

from . import db as dbmod
from . import marketplace
from . import metrics


class GpuRoutingUnavailable(ValueError):
    """GPU-class routing is blocked on the runtime pin.

    `gpuPerTask` is silently dropped by the pinned flashruntime ResourcesSpec
    (compile.py:608-624), so a routed GPU job would be priced for hardware the
    coordinator cannot yet reserve. Refuse loudly instead of routing a fiction;
    lands with the 0.6.1 release + 4-site pin bump.
    """


def job_capability_class(resources: Mapping[str, Any] | None) -> str:
    """The class the JOB needs — a property of the work (marketplace.py:1639)."""
    res = dict(resources or {})
    # The bool check runs on the RAW value, before any `or 0` coercion.
    # `res.get("gpus") or 0` collapses `False` to `0` before a later
    # `isinstance(gpus, bool)` ever sees it — dead for that branch, and only
    # silently correct because `0` also fails `int(gpus) > 0` right next to
    # it. `True` survives the same coercion (`True or 0` is `True`), so the
    # old guard caught one bool value and not the other. Checking the raw
    # value first makes both explicit and symmetric.
    raw_gpus = res.get("gpus")
    wants_gpu = raw_gpus is True or (
        not isinstance(raw_gpus, bool) and int(raw_gpus or 0) > 0
    )
    if wants_gpu:
        raise GpuRoutingUnavailable(
            "price: routing for gpus > 0 is not available yet — the pinned "
            "runtime drops gpuPerTask (compile.py:608). Remove price: or set "
            "gpus: 0 until the 0.6.1 pin bump."
        )
    cpus = res.get("cpus") or 0
    if float(cpus) >= marketplace.CPU_LARGE_MIN_CORES:
        return "cpu-large"
    return "cpu-small"


def plan_pool_routing(
    db: psycopg.Connection,
    *,
    capability_class: str,
    max_zc_per_hour: int,
    tasks_wanted: int,
) -> dict[str, Any]:
    """Rank the open book for one class, match a bid against it, and explain
    every listing's place in the outcome — matched or not.

    Orchestration only. Ranking, effective-price arithmetic and the unproven
    cap all stay in :mod:`marketplace` (:func:`marketplace.rank_asks`,
    :func:`marketplace.effective_price`, :func:`marketplace.match_bid`); this
    function calls them and narrates the result. The one thing it does that
    ``marketplace`` cannot: source real acceptance rates. Those come from
    :func:`db.acceptance_rate_rows` fed through :func:`metrics.acceptance_rates`
    — the SAME two-call shape ``list_market_listings``/``machine_market_hint``
    /``_plan_preview`` in ``app.py`` already use to turn resolved attempts into
    a ``(machine_id, capability_class) -> rate`` mapping. That pairing is
    documented as the ONE producer of these rows
    (``tests/test_router_evidence.py``'s module docstring): a second, hand-
    rolled query here would be exactly the drift that guard exists to catch,
    so this module reaches past ``marketplace``/``metrics`` for ``db`` rather
    than reimplement it.

    The book is queried twice — once unrated, to know which machines are even
    candidates and scope the rate lookup to them (``db.acceptance_rate_rows``'s
    own docstring: a caller "on a request path" should pass the bounded fleet
    it is about to plan, not every machine); once rated, to build the asks
    :func:`marketplace.match_bid` actually ranks against. Both reads are the
    same indexed, open-listings-in-one-class query — cheap, and it keeps the
    acceptance-rate lookup scoped rather than fetching every machine's history
    to price one class's book.

    ``excluded`` is derived by walking the ranked book once, replaying the
    plan's own fills and its own ``unproven_task_cap`` (never a locally
    recomputed one) to attribute a reason to whatever did not fill:

    - **``"ask-above-cap"``** — its effective price does not clear the bid at
      all (unclearable counts as this too). Checked first, and independent of
      how much of the bid was already spent: a listing that could never have
      cleared this bid is a more useful answer than "there was nothing left to
      buy", even when both happen to be true of the same listing.
    - **``"no-tasks-left"``** — it would have cleared, but higher-ranked
      (cheaper) listings had already filled every task wanted by the time the
      walk reached it.
    - **``"unproven-cap"``** — it would have cleared and tasks remained, but
      it is unproven and the plan's own unproven share was already spent by
      unproven listings ranked ahead of it.
    """
    unrated = marketplace.open_asks(db, capability_class)
    machine_ids = [ask.machine_id for ask in unrated]
    rate_rows = metrics.acceptance_rates(
        dbmod.acceptance_rate_rows(db, machine_ids=machine_ids)
    )
    rates = {
        (row["machine_id"], row["capability_class"]): row["acceptance_rate"]
        for row in rate_rows
    }
    asks = marketplace.open_asks(db, capability_class, acceptance_rates=rates)

    plan = marketplace.match_bid(
        max_zc_per_hour=max_zc_per_hour,
        tasks_wanted=tasks_wanted,
        asks=asks,
    )

    fills_by_listing = {fill.listing_id: fill for fill in plan.fills}
    ranked = marketplace.rank_asks(asks)

    book: list[dict[str, Any]] = []
    nearest_miss: dict[str, Any] | None = None
    remaining = max(int(tasks_wanted), 0)
    unproven_used = 0

    for ask in ranked:
        price = marketplace.effective_price(ask.ask_zc_per_hour, ask.acceptance_rate)
        fill = fills_by_listing.get(ask.listing_id)

        if fill is not None:
            excluded: str | None = None
            tasks_assigned = fill.tasks
            remaining -= fill.tasks
            if fill.unproven_host:
                unproven_used += fill.tasks
        else:
            tasks_assigned = 0
            above_cap = price is None or price > max_zc_per_hour
            if above_cap:
                excluded = "ask-above-cap"
            elif remaining <= 0:
                excluded = "no-tasks-left"
            elif ask.unproven and unproven_used >= plan.unproven_task_cap:
                excluded = "unproven-cap"
            else:
                # Every listing with capacity, room under the cap and an
                # untapped unproven budget should have been a fill; reaching
                # here means a candidate's own capacity was zero rather than
                # one of the three named reasons.
                excluded = "no-tasks-left"

            if nearest_miss is None and price is not None and price > max_zc_per_hour:
                nearest_miss = {
                    "ask_zc_per_hour": int(ask.ask_zc_per_hour),
                    "listing_id": ask.listing_id,
                }

        book.append(
            {
                "listing_id": ask.listing_id,
                "machine_id": ask.machine_id,
                "ask_zc_per_hour": int(ask.ask_zc_per_hour),
                "acceptance_rate": ask.acceptance_rate,
                "effective_zc_per_hour": None if price is None else str(price),
                "tasks_assigned": tasks_assigned,
                "excluded": excluded,
            }
        )

    return {
        "capability_class": capability_class,
        "tasks_wanted": max(int(tasks_wanted), 0),
        "tasks_filled": plan.tasks_filled,
        "tasks_unfilled": plan.tasks_unfilled,
        "book": book,
        "nearest_miss": nearest_miss if plan.tasks_unfilled > 0 else None,
        "plan": plan,
    }
