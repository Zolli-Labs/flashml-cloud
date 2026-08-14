"""Submit-time routing: resolve what a job needs, plan against the book.

Design: docs/superpowers/specs/2026-08-11-zolli-marketplace-design.md §4/§6
and docs (repo root)/research/2026-08-13-automatic-routing-marketplace-matching.md §5.
This module never imports the runtime; eligibility stays the coordinator's,
matching stays `marketplace`'s. Everything here is orchestration and reasons.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

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


class UnroutableResources(ValueError):
    """`resources` names a `cpus`/`gpus` value `job_capability_class` cannot
    read as a count — a string, a list, a dict, anything `int()`/`float()`
    itself refuses.

    Sibling to :class:`GpuRoutingUnavailable`, and refused the same way:
    loudly and by name, rather than letting a bare `TypeError`/`ValueError`
    from the coercion escape to a caller that only expects the GPU refusal.
    The submit hook's pre-coordinator validation catches both, turning
    either into a 400 instead of this one surfacing as an unhandled 500.
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
    try:
        wants_gpu = raw_gpus is True or (
            not isinstance(raw_gpus, bool) and int(raw_gpus or 0) > 0
        )
    except (TypeError, ValueError):
        raise UnroutableResources(
            f"price: resources.gpus must be a number, got {raw_gpus!r}"
        ) from None
    if wants_gpu:
        raise GpuRoutingUnavailable(
            "price: routing for gpus > 0 is not available yet — the pinned "
            "runtime drops gpuPerTask (compile.py:608). Remove price: or set "
            "gpus: 0 until the 0.6.1 pin bump."
        )
    cpus = res.get("cpus") or 0
    try:
        cpu_count = float(cpus)
    except (TypeError, ValueError):
        raise UnroutableResources(
            f"price: resources.cpus must be a number, got {cpus!r}"
        ) from None
    if cpu_count >= marketplace.CPU_LARGE_MIN_CORES:
        return "cpu-large"
    return "cpu-small"


def workspace_machine_ids_for(db: psycopg.Connection, user_id: str) -> set[str]:
    """Every machine withheld from ``user_id``'s priced bids because it is
    already theirs for free — the ``workspace_machine_ids`` set
    :func:`plan_pool_routing` passes to :func:`marketplace.match_bid` as
    ``workspace_reserved`` (M12: workspace demand takes priority over the
    open book, C1 final review).

    Reuses :func:`db.router_candidates_for_owner` rather than re-deriving
    the pool-membership query: that function already computes exactly this
    set as its ``venue == "workspace"`` rows — the account's own machines,
    plus every machine bound to a pool it is a live member of — for the
    routing/cost-preview surface. A second, hand-rolled membership query
    here would be the same drift ``tests/test_router_evidence.py`` polices
    for acceptance-rate rows (see :func:`plan_pool_routing`'s own
    docstring): one owner for "which machines are this account's
    workspace", not two.
    """
    candidates = dbmod.router_candidates_for_owner(db, user_id)
    return {c["machine_id"] for c in candidates if c["venue"] == "workspace"}


def plan_pool_routing(
    db: psycopg.Connection,
    *,
    capability_class: str,
    max_zc_per_hour: int,
    tasks_wanted: int,
    workspace_machine_ids: Collection[str] = (),
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

    - **``"workspace-free"``** — the machine is in ``workspace_machine_ids``:
      bound to a pool the caller belongs to, and withheld from this bid
      entirely by :func:`marketplace.match_bid`'s ``workspace_reserved``
      exclusion (M12: workspace demand takes priority over the open book).
      Checked first, and unconditionally: a reserved machine was never
      actually offered to this bid, so describing it as too expensive or out
      of tasks would describe a negotiation that never happened. It is FREE
      to the caller for exactly the same reason it is withheld — not priced
      out, priced at zero, elsewhere (M1).
    - **``"ask-above-cap"``** — its effective price does not clear the bid at
      all (unclearable counts as this too). Checked next, and independent of
      how much of the bid was already spent: a listing that could never have
      cleared this bid is a more useful answer than "there was nothing left to
      buy", even when both happen to be true of the same listing.
    - **``"no-tasks-left"``** — it would have cleared, but higher-ranked
      (cheaper) listings had already filled every task wanted by the time the
      walk reached it.
    - **``"unproven-cap"``** — it would have cleared and tasks remained, but
      it is unproven and the plan's own unproven share was already spent by
      unproven listings ranked ahead of it.

    ``workspace_machine_ids`` is the caller's to derive — see
    :func:`workspace_machine_ids_for` for the one sanctioned way to compute
    "which machines are this account's workspace" — and defaults to empty,
    so an existing caller that never passes it (the routing-inspection GET
    route re-explaining a bid that predates this parameter) sees no change.
    """
    reserved = {str(machine_id) for machine_id in workspace_machine_ids}

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
        workspace_reserved=reserved,
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
        elif str(ask.machine_id) in reserved:
            # `match_bid` already withheld this machine from `asks` via
            # `workspace_reserved`, so it can never appear in
            # `fills_by_listing` — label it here rather than let it fall
            # through the price/cap reasons below, which would describe a
            # machine that was never actually offered to this bid.
            tasks_assigned = 0
            excluded = "workspace-free"
        else:
            tasks_assigned = 0
            above_cap = price is None or price > max_zc_per_hour
            if above_cap:
                excluded = "ask-above-cap"
            elif ask.unproven and remaining > 0 and unproven_used >= plan.unproven_task_cap:
                excluded = "unproven-cap"
            else:
                # `remaining <= 0` is the common way here: higher-ranked
                # listings already filled every task wanted. The only other
                # path into this branch is a listing with capacity, headroom
                # under the cap and an untapped unproven budget — which
                # `match_bid` would already have matched it. `max_concurrent_
                # tasks > 0` is a CHECK constraint (migration
                # 0018_marketplace.sql), so a live listing can never carry
                # zero capacity: that second path is provably unreachable,
                # leaving exactly one live reason to report here — collapsed
                # into a single emission point rather than a second, silent
                # catch-all guessing at a fourth reason that cannot occur.
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


def _estimate_task_seconds(
    db: psycopg.Connection, config: Any, capability_class: str
) -> int:
    """How long one task of this job is expected to take, for sizing the
    bid's hold (``marketplace.create_bid``'s ``est_task_seconds``).

    Mirrors the cost-quote route's own precedence (``_cost_quote_row`` in
    app.py): a caller-declared duration wins over any recorded-evidence
    estimate. It never reaches for the evidence rung itself, for two
    reasons that both hold at once — this is called from
    :func:`route_submitted_job`, on a job that was JUST minted by the
    coordinator, so ``db.peer_task_observations`` (keyed by ``job_id``,
    filtered to accepted ``contributions``) is *structurally* empty: no
    attempt has run yet, so there is nothing for that rung to find. And
    reaching it at all would mean importing ``router.estimator``, which
    sits outside this module's permitted imports (``marketplace``,
    ``metrics``, ``db``, ``psycopg`` — the routing module never imports
    the runtime, directly or by way of the router package). So the only
    honest source here is what the submitter declared: ``timeout_seconds``,
    falling back to one hour when absent. Never zero —
    ``marketplace.create_bid`` refuses a zero estimate (marketplace.py:1655).

    ``db`` and ``capability_class`` are accepted, not read, so this call
    site does not have to change shape if a class-level source is ever
    wired up here without reaching past the permitted imports above.
    """
    seconds = getattr(config, "timeout_seconds", None)
    return int(seconds) if seconds else 3600


def route_submitted_job(
    db: psycopg.Connection,
    *,
    user_id: str,
    job_id: str,
    config: Any,
    task_count: int,
) -> dict[str, Any]:
    """Post a priced job's bid and grant whatever the open book clears for
    it, right after the coordinator has accepted the job.

    **This function is atomic: no partial write escapes it.** The two real
    writes — :func:`marketplace.create_bid` and, when the plan clears
    anything, :func:`marketplace.grant_matches` — run inside one
    ``with db.transaction():`` block, so an exception raised anywhere
    between the bid and the grant (including inside ``grant_matches``
    itself) leaves neither behind. This matters under autocommit (this
    deployment's current connection mode): without an explicit transaction
    each statement commits the instant it runs, so a caller's own
    ``db.rollback()`` after catching this function's exception is a no-op —
    it cannot undo a `create_bid` that already committed. A caller who
    reports "routing was skipped" must be describing a book with no trace of
    the attempt, not a caller that hoped rollback would clean up after it.
    ``plan_pool_routing`` runs BEFORE the transaction (it only reads), so a
    failure there costs nothing to roll back in the first place — the
    transaction wraps exactly the two statements that write.

    The caller (the submit handler) still owns fail-open: it decides that an
    exception here degrades the response to
    ``{"state": "skipped", "reason": "routing-error"}`` rather than failing
    the submit. What it does NOT need to do, and must not be read as
    needing to do, is undo this function's own writes — there are none left
    un-done to undo. Any ``db.rollback()`` on the caller's side is
    belt-and-braces for a future non-autocommit connection mode, not the
    mechanism that keeps this function's writes atomic today.

    ``config.resources`` deciding the capability class here is the SECOND
    time it is checked: the handler already refused a GPU job with a price
    block at validation time, before the coordinator ever saw it
    (:class:`GpuRoutingUnavailable`). Re-deriving the class here rather than
    accepting it as a parameter keeps this function the single place that
    reads "what class does this job's resources need" — a caller passing a
    stale or hand-computed class is exactly the drift a second argument
    would invite.

    The book must respect pools/workspace (C1, final review): every priced
    job — not only pool-scoped ones — withholds ``user_id``'s own workspace
    machines from the bid it posts, via :func:`workspace_machine_ids_for`.
    An unpooled submitter with a teammate's cheap listing in the book must
    not have that machine matched and charged against when the same
    capacity is theirs for free through the pool; the label the caller sees
    for it is ``"workspace-free"`` (:func:`plan_pool_routing`). This
    derivation is itself inside the caller's fail-open guard: it happens
    here, before the write transaction below, exactly where
    ``plan_pool_routing``'s own read already sits.
    """
    price = config.price
    klass = job_capability_class(config.resources)
    est_seconds = _estimate_task_seconds(db, config, klass)
    planned = plan_pool_routing(
        db,
        capability_class=klass,
        max_zc_per_hour=price["max_zc_per_hour"],
        tasks_wanted=task_count,
        workspace_machine_ids=workspace_machine_ids_for(db, user_id),
    )

    # One transaction for both writes. psycopg3's `transaction()` nests via
    # savepoints, and `create_bid`/`grant_matches` already each open their
    # own `with db.transaction():` internally — proven safe to nest by
    # tests/test_routing_routes.py's atomicity test, not merely assumed.
    # Anything raised inside this block, from either write, rolls both back
    # before it ever reaches the caller.
    with db.transaction():
        bid = marketplace.create_bid(
            db,
            job_id=job_id,
            owner_id=user_id,
            capability_class_name=klass,
            max_zc_per_hour=price["max_zc_per_hour"],
            tasks_wanted=task_count,
            est_task_seconds=est_seconds,
        )
        if planned["plan"].fills:
            marketplace.grant_matches(db, bid_id=str(bid["id"]), plan=planned["plan"])

    out = {
        key: planned[key]
        for key in (
            "capability_class",
            "tasks_wanted",
            "tasks_filled",
            "tasks_unfilled",
            "book",
            "nearest_miss",
        )
    }
    out.update(
        {
            "state": "routed",
            "bid_id": str(bid["id"]),
        }
    )
    return out
