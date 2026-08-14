"""Submit-time routing: resolve what a job needs, plan against the book.

Design: docs/superpowers/specs/2026-08-11-zolli-marketplace-design.md §4/§6
and docs (repo root)/research/2026-08-13-automatic-routing-marketplace-matching.md §5.
This module never imports the runtime; eligibility stays the coordinator's,
matching stays `marketplace`'s. Everything here is orchestration and reasons.

A job resolves to an ORDERED list of capability classes it will accept
(`job_accept_classes` — derived from `resources`, or taken from
`placement.accept` and checked against the ladder), and the walk bids the
remainder into each in turn until the tasks run out (`plan_pool_routing`,
`route_submitted_job`). The class names live in `marketplace`; this module
is the only place that decides which of them a given job may have.

Submit time is not the only time. `refill_open_bids` runs the same planning
against the demand that is STILL waiting whenever new supply appears in a
class, so a bid the book could not clear at submission is matched when a
listing shows up rather than staying starved for ever (research doc §5.2
step 8: "partial fills stay open and refill as listings appear").
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from fractions import Fraction
from typing import Any

import psycopg

from . import db as dbmod
from . import marketplace
from . import metrics


class RoutingValidationError(ValueError):
    """A priced job that cannot be routed as written, refused BEFORE the
    coordinator is asked to accept it.

    One base class, because the submit handler has exactly one thing to do
    with every one of these: answer 400, at validation time, with the reason
    verbatim. It is raised for a ``resources`` value that is not a count, a
    ``placement.accept`` naming a class that does not exist, and an
    ``accept`` that contradicts the job's own ``resources`` — three
    different mistakes with one shape, all of them the submitter's to fix
    and none of them anything a retry would change.

    Deliberately NOT raised for "the book had nothing for you". An empty or
    too-expensive book is a *result* — reported as ``tasks_unfilled`` with a
    ``nearest_miss`` — and turning it into an exception would refuse a
    submission because nobody happened to be selling this minute.
    """


class UnroutableResources(RoutingValidationError):
    """`resources` names a `cpus`/`gpus` value :func:`job_accept_classes`
    cannot read as a count — a string, a list, a dict, anything
    `int()`/`float()` itself refuses.

    A named subclass rather than a bare :class:`RoutingValidationError`
    because the submit hook is not the only reader: a caller catching this
    specifically is asking "was the config malformed?", which is a different
    question from "was the placement inconsistent?", and the answer should
    not depend on matching a message. The hook itself catches the base class
    and never needs to know the difference.
    """


def _requested_counts(resources: Mapping[str, Any] | None) -> tuple[bool, float]:
    """``(wants_gpu, cpu_count)`` from a job's ``resources`` block.

    The bool check runs on the RAW ``gpus`` value, before any ``or 0``
    coercion. ``res.get("gpus") or 0`` collapses ``False`` to ``0`` before a
    later ``isinstance(gpus, bool)`` ever sees it — dead for that branch, and
    only silently correct because ``0`` also fails ``int(gpus) > 0`` right
    next to it. ``True`` survives the same coercion (``True or 0`` is
    ``True``), so the old guard caught one bool value and not the other.
    Checking the raw value first makes both explicit and symmetric.
    """
    res = dict(resources or {})
    raw_gpus = res.get("gpus")
    try:
        wants_gpu = raw_gpus is True or (
            not isinstance(raw_gpus, bool) and int(raw_gpus or 0) > 0
        )
    except (TypeError, ValueError):
        raise UnroutableResources(
            f"price: resources.gpus must be a number, got {raw_gpus!r}"
        ) from None

    cpus = res.get("cpus") or 0
    try:
        cpu_count = float(cpus)
    except (TypeError, ValueError):
        raise UnroutableResources(
            f"price: resources.cpus must be a number, got {cpus!r}"
        ) from None

    return wants_gpu, cpu_count


def _gpu_classes_cheapest_first() -> tuple[str, ...]:
    """Every GPU book, ascending by reference price.

    Computed from :data:`marketplace.CAPABILITY_CLASSES` and
    :data:`marketplace.REFERENCE_ZC_PER_HOUR` rather than written out, because
    a second hardcoded ladder here is a second thing to remember when a class
    is added or repriced — and the one that would be forgotten is this one,
    since nothing about a new class forces anybody to open this file.

    Reference price is a defensible ordering and not a real one: it is the
    RunPod-derived table (§2.5.2), not what anybody is asking today. It says
    "try the class the market usually prices cheapest first", which is the
    right default for a job that named no preference — and a submitter who
    disagrees writes ``placement.accept`` and gets their own order.

    Ties (``gpu-16gb`` and ``gpu-24gb`` are both 1_000) break on the ladder's
    own order, so this is total and stable: the same book on two runs is
    walked in the same sequence, which is the same argument
    :func:`marketplace._rank_key` makes for its own tiebreaks.
    """
    return tuple(
        sorted(
            (c for c in marketplace.CAPABILITY_CLASSES if c.startswith("gpu-")),
            key=lambda c: (
                marketplace.REFERENCE_ZC_PER_HOUR[c],
                marketplace.CAPABILITY_CLASSES.index(c),
            ),
        )
    )


def job_accept_classes(
    resources: Mapping[str, Any] | None = None,
    placement: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Which books this job may bid in, in the order to try them.

    Replaces the single ``job_capability_class`` answer: a job is rarely
    satisfiable by exactly one class, and pretending otherwise strands work
    whose class is thin while an adjacent book sits open. The result is an
    ORDERED preference — :func:`plan_pool_routing` walks it and stops when
    the tasks run out — so the first entry is where the job wants to be and
    the rest are where it is willing to end up.

    **Derived, when the submitter said nothing:**

    - ``gpus > 0`` → every GPU class, cheapest reference price first
      (:func:`_gpu_classes_cheapest_first`). A GPU job cannot run on a CPU
      machine at all, so no CPU class is offered at any price.
    - ``gpus == 0`` and ``cpus >= CPU_LARGE_MIN_CORES`` → ``("cpu-large",)``
      alone. The threshold is the marketplace's (a workstation is not a
      laptop), and a job at or above it cannot fall back down the ladder —
      the smaller class is not cheaper capacity for this job, it is capacity
      that cannot run it.
    - otherwise → ``("cpu-small", "cpu-large")``. Small first because it is
      the cheaper supply and the job named no reason to need more; large
      second so a thin ``cpu-small`` book does not strand a job any
      workstation could run this second.

    **Checked, when the submitter wrote ``placement.accept``:** the yaml layer
    validated the SHAPE (a non-empty, duplicate-free list of non-empty
    strings) and could validate nothing else — ``flashml_yaml`` must not
    import ``marketplace``, so it does not know the ladder exists. Every
    semantic rule is therefore here, and each refusal names the offending
    entry rather than the list:

    - an entry that is not a capability class — a typo, a made-up size, a
      class from a design document that the priced ladder never got;
    - a CPU class on a GPU job, or a GPU class on a CPU job. The first is a
      promise the machine cannot keep (the task lands and finds no device);
      the second is not a safety problem but a pricing one — a GPU machine
      would happily run CPU work, at the price of its GPU, while being
      unavailable to work that needs it;
    - ``cpu-small`` on a job at or above ``CPU_LARGE_MIN_CORES``. Same rule
      as the derived default, restated as a refusal so ``accept`` cannot be
      used to route around it.
    - a repeated class. The walk re-reads the same open book for each entry
      with nothing written between the reads, so a class named twice would
      plan the same listing's capacity twice and report a fill only one
      machine can honour.

    A ``placement`` mapping with no ``accept`` key derives as if it were
    absent. The yaml parser cannot produce one (``accept`` is required
    there), but a hand-built dict can, and an empty constraint constrains
    nothing. ``accept: []`` is different and refused: it says "run nowhere",
    and deriving from it would do the exact opposite of what it says.
    """
    wants_gpu, cpu_count = _requested_counts(resources)
    wants_large_cpu = cpu_count >= marketplace.CPU_LARGE_MIN_CORES

    declared = None if placement is None else dict(placement).get("accept")
    if declared is None and placement is not None and "accept" in dict(placement):
        # `accept: null` — written, and saying nothing. Treated as the empty
        # list below rather than as absent: the key is there on purpose.
        declared = []

    if declared is None:
        if wants_gpu:
            return _gpu_classes_cheapest_first()
        if wants_large_cpu:
            return ("cpu-large",)
        return ("cpu-small", "cpu-large")

    if not isinstance(declared, (list, tuple)) or not declared:
        raise RoutingValidationError(
            "placement.accept must be a non-empty list of capability class "
            f"names, got {declared!r}"
        )

    ladder = ", ".join(marketplace.CAPABILITY_CLASSES)
    accept: list[str] = []
    for entry in declared:
        name = entry if isinstance(entry, str) else None
        if name not in marketplace.CAPABILITY_CLASSES:
            raise RoutingValidationError(
                f"placement.accept: {entry!r} is not a capability class. "
                f"The ladder is: {ladder}"
            )
        if name in accept:
            raise RoutingValidationError(
                f"placement.accept lists {name!r} twice; a duplicate class "
                "would plan the same machines' capacity twice"
            )
        if wants_gpu and not name.startswith("gpu-"):
            raise RoutingValidationError(
                f"placement.accept: {name!r} is a CPU class, but this job "
                "asks for a gpu — a gpu job may only accept gpu-* classes, "
                "because a CPU machine has no device for the task to find"
            )
        if not wants_gpu and not name.startswith("cpu-"):
            raise RoutingValidationError(
                f"placement.accept: {name!r} is a GPU class, but this job "
                "asks for no gpu — a cpu job may only accept cpu-* classes, "
                "or it pays GPU prices for work that does not use one"
            )
        if wants_large_cpu and name == "cpu-small":
            raise RoutingValidationError(
                f"placement.accept: this job asks for {cpu_count:g} cpus, at "
                f"or above the {marketplace.CPU_LARGE_MIN_CORES}-core "
                "cpu-large threshold, so 'cpu-small' cannot run it — a job "
                "that needs a workstation may not fall back to a laptop"
            )
        accept.append(name)

    return tuple(accept)


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


def price_objective(price: Mapping[str, Any] | None) -> str:
    """Which of :data:`marketplace.OBJECTIVES` a parsed ``price`` block asks
    the book to be ranked by.

    One reader for the field, because there are two callers — the submit
    hook's pool-scoped explain and :func:`route_submitted_job` — and a job
    whose explain was ranked one way and whose bid was matched another would
    be the worst possible version of publishing the formula.

    A price block that names none falls back to
    :data:`marketplace.DEFAULT_RANK_OBJECTIVE`. Every block that came through
    ``flashml_yaml`` carries one (that parser defaults it to ``balanced``), so
    this fallback is only ever reached by a hand-built config — and for such a
    caller the honest default is the engine's own, which is what this walk did
    before objectives existed. A value that is not an objective at all is NOT
    silently corrected here: it reaches ``marketplace`` and raises, because a
    config asking for something this engine cannot do should say so rather
    than quietly get something else.
    """
    named = dict(price or {}).get("objective")
    return marketplace.DEFAULT_RANK_OBJECTIVE if named is None else str(named)


def _plan_one_class(
    db: psycopg.Connection,
    *,
    capability_class: str,
    max_zc_per_hour: int,
    tasks_wanted: int,
    reserved: set[str],
    objective: str = marketplace.DEFAULT_RANK_OBJECTIVE,
    unproven_cap: int | None = None,
    exclude_listings: Collection[str] = (),
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

    ``reserved`` is a set of machine ids the caller has already normalised —
    :func:`plan_pool_routing` derives it once and passes the same set to
    every class it walks, because "this machine is my workspace's" is a fact
    about the machine and not about the book it happens to be listed in.

    ``unproven_cap`` is how much of the JOB's newcomer allowance is still
    unspent when this class is reached; None lets ``match_bid`` compute a
    per-bid one, which is right for a single-class caller and wrong for the
    walk (see :func:`plan_pool_routing`). Whatever the plan comes back with
    as ``unproven_task_cap`` is also what the ``"unproven-cap"`` reason below
    is attributed against — never a locally recomputed number, so the label
    cannot claim a listing was capped by a budget the match never used.

    ``objective`` is passed straight through to
    :func:`marketplace.match_bid` and used again to score every row of the
    explain, from the SAME class median the match was computed against
    (:func:`marketplace.class_median_seconds` over the whole book, taken
    before the workspace exclusion). The two must not be computed
    independently: a published ``rank_score`` that disagrees with the order
    the rows are printed in is worse than publishing nothing.

    **``median_seconds`` reaches the book only above ``metrics.MIN_EVIDENCE``.**
    ``metrics.acceptance_rates`` reports a median for any group with a timed
    attempt in it, and one is not evidence of how long a machine takes — the
    identical argument that constant already makes about the rate. So the
    mapping handed to :func:`marketplace.open_asks` is built from the entries
    that cleared the threshold, and everybody else arrives unmeasured, which
    ranks them last under ``fastest`` and at a factor of exactly 1 under
    ``balanced``. Neither excludes them.

    ``exclude_listings`` drops listings from the book BEFORE it is ranked or
    matched, and it exists for exactly one caller: :func:`refill_open_bids`,
    which must not hand a bid a second entitlement on a listing it already
    holds one on. Nothing else in the stack can do that job —
    :func:`marketplace.open_asks` filters on listing state and machine status
    and knows nothing about matches, and :func:`marketplace.match_bid`
    excludes only ``workspace_reserved`` — so an already-matched listing is a
    perfectly ordinary candidate to both, ranked on its ask like any other and
    offering its whole ``max_concurrent_tasks`` again (a match does not
    decrement it; see 0018's header on why capacity is a concurrency hint and
    not a quota). Dropped rather than labelled ``excluded``: unlike a
    workspace machine, this one is not being withheld from the buyer at all —
    they already have it — so a row saying otherwise would misdescribe the
    negotiation. Empty for every submit-time caller, where the bid is new and
    holds nothing.

    Private, and one class at a time, because the two jobs are genuinely
    separate: this reads and explains ONE book, and
    :func:`plan_pool_routing` decides how many books to read and with how
    many tasks left. Keeping the per-class read here means the walk cannot
    accidentally grow a second, subtly different notion of what a book row
    means.
    """
    dropped = {str(listing_id) for listing_id in exclude_listings}
    unrated = [
        ask
        for ask in marketplace.open_asks(db, capability_class)
        if ask.listing_id not in dropped
    ]
    machine_ids = [ask.machine_id for ask in unrated]
    rate_rows = metrics.acceptance_rates(
        dbmod.acceptance_rate_rows(db, machine_ids=machine_ids)
    )
    rates = {
        (row["machine_id"], row["capability_class"]): row["acceptance_rate"]
        for row in rate_rows
    }
    medians = {
        (row["machine_id"], row["capability_class"]): row["median_seconds"]
        for row in rate_rows
        if int(row["resolved"]) >= metrics.MIN_EVIDENCE
    }
    asks = [
        ask
        for ask in marketplace.open_asks(
            db, capability_class, acceptance_rates=rates, median_seconds=medians
        )
        if ask.listing_id not in dropped
    ]

    plan = marketplace.match_bid(
        max_zc_per_hour=max_zc_per_hour,
        tasks_wanted=tasks_wanted,
        asks=asks,
        workspace_reserved=reserved,
        objective=objective,
        unproven_cap=unproven_cap,
    )

    fills_by_listing = {fill.listing_id: fill for fill in plan.fills}
    class_median = marketplace.class_median_seconds(asks)
    ranked = marketplace.rank_asks(
        asks, objective=objective, class_median=class_median
    )

    book: list[dict[str, Any]] = []
    nearest_miss: dict[str, Any] | None = None
    nearest_miss_price: Fraction | None = None
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
                    "capability_class": capability_class,
                }
                nearest_miss_price = price

        score = marketplace.rank_score(
            ask, objective=objective, class_median=class_median
        )
        book.append(
            {
                "listing_id": ask.listing_id,
                "machine_id": ask.machine_id,
                "capability_class": capability_class,
                "ask_zc_per_hour": int(ask.ask_zc_per_hour),
                "acceptance_rate": ask.acceptance_rate,
                "median_seconds": ask.median_seconds,
                "effective_zc_per_hour": None if price is None else str(price),
                # The number this row was ORDERED by under the active
                # objective, rendered from the exact Fraction rather than a
                # float. Under `cheapest` it repeats `effective_zc_per_hour`
                # — kept anyway, so a reader never has to know which
                # objective is live to know which field to look at.
                "rank_score": None if score is None else str(score),
                "tasks_assigned": tasks_assigned,
                "excluded": excluded,
            }
        )

    return {
        "capability_class": capability_class,
        "tasks_wanted": max(int(tasks_wanted), 0),
        "book": book,
        # Unconditional here, unlike the public result: whether a miss is
        # worth reporting is a question about the WHOLE walk (a later class
        # may yet fill everything), and this function cannot see the walk.
        # `plan_pool_routing` drops it when nothing is left unfilled.
        "nearest_miss": nearest_miss,
        "nearest_miss_price": nearest_miss_price,
        "plan": plan,
    }


def plan_pool_routing(
    db: psycopg.Connection,
    *,
    accept_classes: Collection[str],
    max_zc_per_hour: int,
    tasks_wanted: int,
    workspace_machine_ids: Collection[str] = (),
    objective: str = marketplace.DEFAULT_RANK_OBJECTIVE,
) -> dict[str, Any]:
    """Walk the accepted classes in order, bidding the REMAINDER into each,
    and explain the whole outcome as one book.

    The walk is the entire behaviour, and it is ordered rather than
    simultaneous on purpose. A job that accepts three classes is not asking
    for the cheapest listing across all three — it is saying "here, and if
    here cannot take it all, there". So each class is planned against the
    tasks the previous ones could not fill, and the FIRST class to fill the
    job ends the walk: later classes are never queried, never appear in
    ``book``, and never get a ``class_plans`` entry. A plan that listed them
    would be reporting a book read that did not happen.

    Each class's read and explanation is :func:`_plan_one_class`, unchanged
    from the single-class era: same two-query acceptance-rate shape, same
    ``excluded`` vocabulary, same arithmetic. This function adds order,
    subtraction and three cross-class facts:

    - **``book``** is every walked class's rows concatenated in WALK order,
      not re-sorted by price. Rank is meaningful within a book and meaningless
      across books — an ``8gb`` hour and an ``80gb`` hour are not the same
      thing bought at two prices — so the rows keep their class's ordering and
      each carries ``capability_class`` to say which book it came from.
    - **``nearest_miss``** is the cheapest EFFECTIVE price above the cap
      across every class walked, with the class named. Cross-class, because
      the buyer's question ("how much would it have taken?") is about the
      job, not about one book; and by effective price rather than headline
      ask, because that is the number the cap was compared against in the
      first place. ``None`` whenever nothing is unfilled — a filled plan has
      no miss to explain.
    - **``class_plans``** is one entry per class actually walked, in order,
      each with the ``tasks_wanted`` that class was asked for and its
      ``MatchPlan``. :func:`route_submitted_job` turns exactly this into one
      bid per entry, so the bids and the explanation cannot disagree about
      who was asked for what.

    **The unproven share cap is the JOB's, spent once across the whole walk.**
    ``marketplace.unproven_task_budget`` is computed HERE, from the job's
    total ``tasks_wanted``, and what remains of it is threaded into each
    class's :func:`marketplace.match_bid` as ``unproven_cap``; every fill to
    an unproven host draws the allowance down (``MatchPlan.unproven_tasks``)
    and a class reached with nothing left gets exactly zero.

    Letting each ``match_bid`` compute its own budget — which is what it does
    for every other caller, from the tasks THAT call was given — made a job
    spilling across three classes reach three times the blast radius a
    single-class job of the same size would, and the width was set by the
    accident of how many books the job happened to touch rather than by any
    decision. :data:`marketplace.UNPROVEN_TASK_SHARE` is a bound on how much
    of ONE JOB may be lost to newcomers; a bound that multiplies by the
    length of an accept list is not that bound.

    Two details that are not incidental. The budget comes off the job's
    ORIGINAL task count, not the remainder each class is asked for, so
    spilling never shrinks the allowance either — the cap is a property of
    the job, and it is the same number whichever books it ends up in. And an
    exhausted allowance is threaded as an explicit 0: ``unproven_task_budget``
    floors at one so a single-task job can still try a newcomer, and
    re-applying that floor per class is exactly the amplification being
    removed.

    ``class_plans`` carries ``MatchPlan`` dataclasses, which are not
    JSON-safe — every HTTP caller drops the key before serialising (see
    ``app.py``'s routing routes).

    ``workspace_machine_ids`` is the caller's to derive — see
    :func:`workspace_machine_ids_for` for the one sanctioned way to compute
    "which machines are this account's workspace" — and defaults to empty,
    so a caller that never passes it sees no exclusion. It is normalised
    once and applied to every class: workspace priority (M12) is a property
    of the machine, not of the book it is listed in.

    ``objective`` orders every class's book (:data:`marketplace.OBJECTIVES`)
    and is published back in the result, together with the ``formula`` that
    describes it, so the plan says what it optimised for rather than leaving
    a reader to infer it from the ordering. The formula string comes from
    :data:`marketplace.OBJECTIVE_FORMULAS` — never a literal here — so the
    published arithmetic and the arithmetic cannot drift apart. It defaults
    to the engine's ``cheapest``: a caller that names no objective gets the
    order this walk had before objectives existed.
    """
    reserved = {str(machine_id) for machine_id in workspace_machine_ids}
    classes = [str(name) for name in accept_classes]
    wanted = max(int(tasks_wanted), 0)
    # Before the walk, so an objective this engine does not have is refused
    # even when there is no book to rank — an empty accept list or a
    # zero-task job would otherwise validate it nowhere and publish it
    # anyway.
    formula = marketplace.objective_formula(objective)

    book: list[dict[str, Any]] = []
    class_plans: list[dict[str, Any]] = []
    nearest_miss: dict[str, Any] | None = None
    nearest_miss_price: Fraction | None = None
    remaining = wanted
    # One allowance for the whole job, from the whole job's task count. See
    # the docstring: computed once here, drawn down as each class spends it.
    unproven_remaining = marketplace.unproven_task_budget(wanted)

    for capability_class in classes:
        if remaining <= 0:
            break

        one = _plan_one_class(
            db,
            capability_class=capability_class,
            max_zc_per_hour=max_zc_per_hour,
            tasks_wanted=remaining,
            reserved=reserved,
            objective=objective,
            unproven_cap=unproven_remaining,
        )
        book.extend(one["book"])
        class_plans.append(
            {
                "capability_class": capability_class,
                "tasks_wanted": remaining,
                "plan": one["plan"],
            }
        )

        price = one["nearest_miss_price"]
        if price is not None and (nearest_miss_price is None or price < nearest_miss_price):
            nearest_miss = one["nearest_miss"]
            nearest_miss_price = price

        remaining -= one["plan"].tasks_filled
        unproven_remaining -= one["plan"].unproven_tasks

    return {
        "accept": list(classes),
        "objective": objective,
        "formula": formula,
        "tasks_wanted": wanted,
        "tasks_filled": wanted - remaining,
        "tasks_unfilled": remaining,
        "book": book,
        "nearest_miss": nearest_miss if remaining > 0 else None,
        "class_plans": class_plans,
    }


def _live_tasks_assigned(db: psycopg.Connection, bid_ids: Collection[str]) -> int:
    """Tasks currently entitled across ``bid_ids`` — non-expired matches only.

    ``expired`` is the ordinary end of an entitlement nobody claimed (0018's
    header: a matched host that never claims "produces no work and no
    charge"), so its tasks are unspent demand again. Counting them as filled
    is how a job stays permanently short by however many hosts ghosted it.
    The same ``state <> 'expired'`` predicate :func:`marketplace.grant_matches`
    uses to decide ``partial`` versus ``filled`` — deliberately, so the
    remainder this module computes and the state that function writes cannot
    disagree about the same bid.
    """
    ids = [str(bid_id) for bid_id in bid_ids]
    if not ids:
        return 0
    with db.cursor() as cur:
        cur.execute(
            "select coalesce(sum(tasks_assigned), 0) as assigned"
            "  from public.matches"
            " where bid_id = any(%s::uuid[]) and state <> 'expired'",
            (ids,),
        )
        return int(cur.fetchone()["assigned"])


def _unproven_tasks_spent(db: psycopg.Connection, bid_ids: Collection[str]) -> int:
    """Tasks already placed on unproven hosts across ``bid_ids``.

    Summed over ``tasks_assigned``, not counted as rows: the budget it is
    compared against (:func:`marketplace.unproven_task_budget`) is denominated
    in TASKS, and one match may carry several. A row count would report a
    three-task entitlement on one newcomer as a single task spent and let the
    job place three more.
    """
    ids = [str(bid_id) for bid_id in bid_ids]
    if not ids:
        return 0
    with db.cursor() as cur:
        cur.execute(
            "select coalesce(sum(tasks_assigned), 0) as assigned"
            "  from public.matches"
            " where bid_id = any(%s::uuid[]) and state <> 'expired'"
            "   and unproven_host",
            (ids,),
        )
        return int(cur.fetchone()["assigned"])


def refill_open_bids(
    db: psycopg.Connection, *, capability_class: str
) -> list[dict[str, Any]]:
    """New supply appeared in ``capability_class``: match it against the demand
    that is still waiting, and return what each bid got.

    Design: research doc §5.2 step 8 — "partial fills stay open and refill as
    listings appear". Before this function nothing ever re-matched. A bid
    posted into a thin book stayed exactly as starved as it was that second,
    for ever, even when a listing that would have cleared it appeared a minute
    later; the only way to reach the new supply was to resubmit the job. The
    bid states were already right for this (``open`` and ``partial`` are both
    live demand, and 0018 made ``partial`` first-class precisely because a
    partial fill is the expected outcome) — nothing was reading them.

    **Called when a listing appears in a class**, from
    ``create_market_listing``. Reactivation should call it too: when
    :func:`marketplace.resume_listing` grows a route — it has none today, and
    this increment deliberately does not add one — that route must call this
    same hook, because a paused listing coming back is new supply by every
    measure a waiting bid can see.

    **Bid order is** :func:`marketplace.open_bids`' **and is not re-derived
    here.** Highest cap first, ties by creation time: that is the fairness
    rule the matching engine already states in its own words ("live demand in
    one book, highest bid first — the order matching walks"), and a refill
    that sorted its own way would be a second, silent auction rule competing
    with the first.

    Four things are recomputed for every bid, and each of them is a way this
    function could have been wrong:

    - **The TRUE remainder**, ``tasks_wanted`` minus what non-expired matches
      already hold (:func:`_live_tasks_assigned`). Never ``tasks_wanted``: a
      refill that re-bid the whole thing would entitle a four-task job's worth
      of machines *again*, every time anybody listed anything. Zero or less
      and the bid is skipped rather than matched for nothing.
    - **The JOB's unproven allowance, not a fresh one.**
      :data:`marketplace.UNPROVEN_TASK_SHARE` bounds how much of ONE JOB may
      be lost to newcomers, so the budget is computed over the summed
      ``tasks_wanted`` of every bid this job posted
      (:func:`marketplace.bids_for_job` — a job spilling across classes has
      several) and drawn down by what those bids have already spent on
      unproven hosts. Letting each refill compute a budget from its own
      remainder would hand the job a new quarter-share every time a listing
      appeared, which is the same amplification
      :func:`plan_pool_routing` removes across classes, one axis over.
    - **The bid's STORED objective** (``bids.objective``, migration 0032) and
      its stored cap. A refill that fell back to the engine's ``cheapest`` —
      all it could have done before that column — would buy a ``fastest``
      buyer the opposite machine to the one their submission chose, at the
      same price, with nothing anywhere saying the objective had changed.
    - **The listings this bid already holds**, excluded from the book
      (``exclude_listings``). Neither :func:`marketplace.open_asks` nor
      :func:`marketplace.match_bid` knows a bid has been here before, and
      ``max_concurrent_tasks`` is not decremented by a match, so an
      already-matched listing would otherwise be ranked and filled a second
      time for the same bid — a double entitlement on one machine, which is
      the one shape of over-entitlement 0018's "over-entitling is how
      volunteer supply finishes a job" argument does NOT cover.

    **Workspace exclusion is the submit path's, unchanged.** The set comes
    from :func:`workspace_machine_ids_for` applied to ``bid["owner_id"]``,
    which IS the account :func:`route_submitted_job` passed as ``user_id``
    when it posted the row — so the context is fully recoverable from the bid
    and no conservative guess is needed. Pool SCOPING needs no thought either:
    a pool-scoped priced job posts no bid at all (the submit hook skips
    routing with ``pool-capacity-is-free``), so every bid this function can
    reach belongs to an unpooled submission, exactly the shape
    ``route_submitted_job`` handles. The lookup is cached per owner across the
    walk; two bids of one job must not produce two different answers to "whose
    workspace is this", and it is a query per owner rather than per bid.

    **One transaction for the whole refill.** Every grant in the walk shares
    it, so a failure on the third bid leaves the first two's entitlements
    un-written rather than half a refill nobody asked for. That is what lets
    the caller's fail-open be honest: ``create_market_listing`` reports the
    listing as created and the refill as not-having-happened, and the book
    agrees with it.

    Returns one ``{"bid_id", "tasks_granted"}`` entry per bid that ACTUALLY
    got something, in walk order — so an empty list means the new supply moved
    nothing, which is the ordinary case and the one the route reports as
    ``"refilled": []``. Bids that were considered and skipped are not
    reported: the caller's question is what changed.
    """
    granted: list[dict[str, Any]] = []
    workspace_by_owner: dict[str, set[str]] = {}

    with db.transaction():
        for bid in marketplace.open_bids(db, capability_class):
            bid_id = str(bid["id"])
            owner_id = str(bid["owner_id"])

            # Every bid this JOB posted, because the newcomer allowance is the
            # job's. `bids_for_job` is owner-scoped and this bid's owner IS the
            # job's submitter, so it returns the whole spill.
            siblings = marketplace.bids_for_job(
                db, job_id=str(bid["job_id"]), owner_id=owner_id
            )
            sibling_ids = [str(row["id"]) for row in siblings] or [bid_id]

            remainder = int(bid["tasks_wanted"]) - _live_tasks_assigned(db, [bid_id])
            if remainder <= 0:
                continue

            job_tasks = sum(int(row["tasks_wanted"]) for row in siblings) or int(
                bid["tasks_wanted"]
            )
            unproven_remaining = max(
                0,
                marketplace.unproven_task_budget(job_tasks)
                - _unproven_tasks_spent(db, sibling_ids),
            )

            if owner_id not in workspace_by_owner:
                workspace_by_owner[owner_id] = workspace_machine_ids_for(db, owner_id)

            held = {
                str(match["listing_id"])
                for match in marketplace.matches_for_bid(db, bid_id)
                if match["state"] != "expired"
            }

            one = _plan_one_class(
                db,
                capability_class=capability_class,
                max_zc_per_hour=int(bid["max_zc_per_hour"]),
                tasks_wanted=remainder,
                reserved=workspace_by_owner[owner_id],
                objective=str(bid["objective"]),
                unproven_cap=unproven_remaining,
                exclude_listings=held,
            )
            plan = one["plan"]
            if not plan.fills:
                continue

            # `grant_matches` owns the legality of the bid's state and the
            # `partial`/`filled` recomputation, exactly as it does at submit
            # time. This function never writes a bid state itself.
            marketplace.grant_matches(db, bid_id=bid_id, plan=plan)
            granted.append({"bid_id": bid_id, "tasks_granted": plan.tasks_filled})

    return granted


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
    """Post a priced job's bids and grant whatever the open book clears for
    them, right after the coordinator has accepted the job.

    **One bid per class the walk actually visited**, each for the tasks that
    class was asked for — the first for the whole job, each later one for
    only what its predecessors could not fill
    (:func:`plan_pool_routing`'s ``class_plans``). A single bid naming one
    class cannot express "either of these books will do", and two bids each
    wanting the full job would entitle twice the machines.

    **Several open bids for one job CAN, later, entitle more machines than
    the job has tasks.** If a ``cpu-small`` bid fills one of two tasks and
    the ``cpu-large`` bid takes the other, both bids stay open for their
    unfilled remainder, and a listing appearing later can match against
    either. That is deliberate, and it is safe because an entitlement is not
    an assignment: a match says these machines MAY pull this job's work at
    these prices, escrow is held only when a host actually claims
    (:func:`marketplace.hold_escrow_on_claim`), and settlement is on
    measured accepted work. Over-entitling is how Salad-style volunteer
    supply gets a job finished when a third of the hosts never show up; the
    cost of the ones that do show up late is an expired match, which costs
    nobody anything.

    **This function is atomic: no partial write escapes it.** Every write —
    each :func:`marketplace.create_bid` and, where a class's plan cleared
    anything, its :func:`marketplace.grant_matches` — runs inside one
    ``with db.transaction():`` block, so an exception raised anywhere in the
    walk (including inside ``grant_matches`` itself, and including on the
    second class after the first has already been written) leaves NONE of
    them behind. A partially-walked job with one of its two bids committed
    would be the worst of both shapes: a response that says "skipped" and a
    book that says otherwise. This matters under autocommit (this
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

    ``config.price["objective"]`` decides how every class's book is ranked
    (:func:`price_objective`), and the response says so: ``objective`` and
    ``formula`` ride back with the book, next to the ``rank_score`` on each
    row. Publishing the formula is deliberate — a buyer who can see the
    arithmetic can check that a dearer machine really did win on measured
    evidence, which is the only thing that makes "we picked this one for you"
    a claim rather than an assertion.

    **It is also written to every bid** (``marketplace.create_bid``'s
    ``objective``, migration 0032), which is what makes the bid re-usable
    after this call returns. A bid that recorded its class, its cap and its
    task count but not its ordering could be re-matched only by a caller
    willing to guess — and :func:`refill_open_bids`, walking the open book
    when new supply appears, would then quietly buy a ``fastest`` job the
    cheapest machine that cleared.

    ``config.resources``/``config.placement`` deciding the accepted classes
    here is the SECOND time they are read: the handler already ran
    :func:`job_accept_classes` at validation time, before the coordinator
    ever saw the job, so an inconsistent ``placement.accept`` is a 400 with
    no job row rather than a routing skip after one exists. Re-deriving here
    rather than accepting the list as a parameter keeps this function the
    single place that answers "which books may this job bid in" — a caller
    passing a stale or hand-computed list is exactly the drift a second
    argument would invite.

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
    objective = price_objective(price)
    accept = job_accept_classes(config.resources, getattr(config, "placement", None))
    # One estimate for the whole job, reused for every class's bid. A task
    # is not faster on a bigger machine by any number this function can
    # honestly produce — the only source it may reach is what the submitter
    # declared (see `_estimate_task_seconds`), which says nothing about
    # class. A per-class estimate needs the recorded evidence rung
    # (`router.estimator`'s median_seconds), which is outside this module's
    # permitted imports and is the next task's problem.
    est_seconds = _estimate_task_seconds(db, config, accept[0])
    planned = plan_pool_routing(
        db,
        accept_classes=accept,
        max_zc_per_hour=price["max_zc_per_hour"],
        tasks_wanted=task_count,
        workspace_machine_ids=workspace_machine_ids_for(db, user_id),
        objective=objective,
    )

    # One transaction for EVERY write in the walk. psycopg3's `transaction()`
    # nests via savepoints, and `create_bid`/`grant_matches` already each open
    # their own `with db.transaction():` internally — proven safe to nest by
    # tests/test_routing_routes.py's atomicity test, not merely assumed.
    # Anything raised inside this block, from any class's write, rolls all of
    # them back before it ever reaches the caller.
    bids: list[dict[str, str]] = []
    with db.transaction():
        for entry in planned["class_plans"]:
            bid = marketplace.create_bid(
                db,
                job_id=job_id,
                owner_id=user_id,
                capability_class_name=entry["capability_class"],
                max_zc_per_hour=price["max_zc_per_hour"],
                tasks_wanted=entry["tasks_wanted"],
                est_task_seconds=est_seconds,
                # Recorded on the row, not merely obeyed here (migration
                # 0032). Every class's bid carries the same objective
                # because one walk planned them all; storing it per bid
                # rather than per job is what lets `refill_open_bids` and
                # the routing inspection route come back to a SINGLE bid
                # later and rank the book the way this walk did.
                objective=objective,
            )
            if entry["plan"].fills:
                marketplace.grant_matches(
                    db, bid_id=str(bid["id"]), plan=entry["plan"]
                )
            bids.append(
                {
                    "capability_class": entry["capability_class"],
                    "bid_id": str(bid["id"]),
                }
            )

    out = {
        key: planned[key]
        for key in (
            "accept",
            # Published, not merely obeyed: `objective` says what this job
            # asked the book to be ranked by and `formula` says what that
            # meant in arithmetic, beside the `rank_score` every row carries.
            # A buyer can then check the ordering instead of trusting it.
            "objective",
            "formula",
            "tasks_wanted",
            "tasks_filled",
            "tasks_unfilled",
            "book",
            "nearest_miss",
        )
    }
    out.update({"state": "routed", "bids": bids})
    return out
