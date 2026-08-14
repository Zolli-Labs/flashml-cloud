"""Objective-aware ranking: the same book, ordered three defensible ways.

Split out of ``test_marketplace.py`` — which pins the ledger, the classes and
the matching rule — because these tests are about ONE question: given a book,
what order does each objective put it in, and is that order exact and
reproducible?

The properties pinned here:

- **``cheapest`` is byte-for-byte today's ranking.** It is the default of
  every engine entry point, so a regression here silently re-prices every
  caller that never heard of objectives.
- **``fastest`` ranks on measured medians and ranks unmeasured LAST** — never
  excludes it, and never invents a duration for it. Free-but-slow is not
  fastest, so a donated ask gets no exemption.
- **``balanced`` is exact Fraction arithmetic**, clamped at 1/2 and 2, and
  falls back to the effective price the moment either median is missing.
- **Determinism under every objective.** The same asks in a different input
  order produce the same output order — the guarantee ``_rank_key``'s own
  docstring makes, extended to the two new keys.
- **The published formula cannot drift from the code**: one constant per
  objective, and a test that there is exactly one per objective.
"""
from __future__ import annotations

from fractions import Fraction

import pytest

from flashml_cloud_api import marketplace as mk


def _ask(listing, ask, *, rate=None, median=None, cap=1, machine=None):
    return mk.Ask(
        listing_id=listing,
        machine_id=machine or f"m-{listing}",
        host_id=f"h-{listing}",
        ask_zc_per_hour=ask,
        max_concurrent_tasks=cap,
        acceptance_rate=rate,
        median_seconds=median,
    )


def _order(asks, objective):
    return [a.listing_id for a in mk.rank_asks(asks, objective=objective)]


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


def test_an_ask_is_unmeasured_until_something_measures_it():
    """`median_seconds` arrives None and stays None, the same discipline
    `acceptance_rate` follows: None is "not asked yet", never 0."""
    assert _ask("a", 100).median_seconds is None
    assert mk.class_median_seconds([_ask("a", 100), _ask("b", 200)]) is None


def test_every_objective_has_exactly_one_published_formula():
    """The explain surface publishes `OBJECTIVE_FORMULAS[objective]` verbatim.
    A formula defined anywhere but next to the ranking code is a formula that
    describes what the engine used to do."""
    assert set(mk.OBJECTIVE_FORMULAS) == set(mk.OBJECTIVES)
    assert mk.DEFAULT_RANK_OBJECTIVE in mk.OBJECTIVES
    assert all(mk.OBJECTIVE_FORMULAS[name] for name in mk.OBJECTIVES)


def test_an_unknown_objective_is_refused_by_name():
    """Not silently ranked cheapest: a caller who asked for something this
    engine cannot do must find out, and the message says what it can do."""
    with pytest.raises(ValueError) as exc_info:
        mk.rank_asks([_ask("a", 100)], objective="fastest-and-cheapest")
    message = str(exc_info.value)
    assert "fastest-and-cheapest" in message
    for name in mk.OBJECTIVES:
        assert name in message


# ---------------------------------------------------------------------------
# cheapest — today's ranking, unchanged
# ---------------------------------------------------------------------------


def test_cheapest_is_the_default_and_is_todays_ranking_unchanged():
    """Every existing caller passes no objective, so the default IS the
    compatibility guarantee: effective price ascending, unclearable last."""
    asks = [
        _ask("dear", 400, rate=1.0, median=1.0),
        _ask("cheap", 300, rate=0.6, median=900.0),
        _ask("broken", 400, rate=0.0),
    ]
    # 300/0.6 = 500 is dearer per accepted result than 400/1.0 = 400, and the
    # 0.0-rate host clears no finite bid at all.
    assert _order(asks, "cheapest") == ["dear", "cheap", "broken"]
    assert [a.listing_id for a in mk.rank_asks(asks)] == ["dear", "cheap", "broken"]
    assert mk.DEFAULT_RANK_OBJECTIVE == "cheapest"


def test_cheapest_scores_a_row_at_its_effective_price():
    """`rank_score` is what the explain surface publishes as the number the
    row was ordered by. Under `cheapest` that is the effective price the book
    already shows — kept as its own field anyway so every objective's row has
    the same shape."""
    ask = _ask("a", 300, rate=0.6, median=12.0)
    assert mk.rank_score(ask, objective="cheapest") == Fraction(500)
    assert mk.rank_score(_ask("b", 400, rate=0.0), objective="cheapest") is None


# ---------------------------------------------------------------------------
# fastest
# ---------------------------------------------------------------------------


def test_fastest_ranks_on_the_measured_median_and_ignores_the_price():
    """The buyer asked for the answer soonest and said what they would pay;
    within the cap, price is the tiebreak and not the key."""
    asks = [
        _ask("slow", 100, rate=1.0, median=50.0),
        _ask("quick", 900, rate=1.0, median=5.0),
        _ask("middling", 500, rate=1.0, median=20.0),
    ]
    assert _order(asks, "fastest") == ["quick", "middling", "slow"]
    assert _order(asks, "cheapest") == ["slow", "middling", "quick"]


def test_fastest_ranks_an_unmeasured_host_last_and_never_drops_it():
    """Ranking, never exclusion (scheduler/__init__.py:637). A machine nobody
    has timed follows every measured one and stays in the book — which is
    also the only way it ever gets timed."""
    asks = [
        _ask("unmeasured", 10, rate=1.0),
        _ask("slow", 100, rate=1.0, median=900.0),
        _ask("quick", 900, rate=1.0, median=5.0),
    ]
    assert _order(asks, "fastest") == ["quick", "slow", "unmeasured"]
    assert mk.rank_score(asks[0], objective="fastest") is None
    assert mk.rank_score(asks[2], objective="fastest") == Fraction(5)


def test_fastest_gives_a_donated_ask_no_head_start():
    """A zero ask ranks first under cheapest and balanced for the honest
    reason that 0 x anything is 0. Under `fastest` the ordering quantity is
    time, and free-but-slow is not fast — so the volunteer is ranked on the
    same median as everybody else."""
    asks = [
        _ask("donated", 0, rate=1.0, median=600.0),
        _ask("paid", 900, rate=1.0, median=5.0),
    ]
    assert _order(asks, "fastest") == ["paid", "donated"]
    assert _order(asks, "cheapest") == ["donated", "paid"]
    assert _order(asks, "balanced") == ["donated", "paid"]


def test_fastest_breaks_a_tie_on_price_then_ask_then_listing():
    """Same median is the ordinary case on a fleet of identical rented
    machines. Without the tiebreaks the same bid fills different hosts on two
    runs, which looks like the engine changing its mind."""
    asks = [
        _ask("b", 300, rate=1.0, median=12.0),
        _ask("a", 300, rate=1.0, median=12.0),
        _ask("c", 100, rate=1.0, median=12.0),
    ]
    assert _order(asks, "fastest") == ["c", "a", "b"]


# ---------------------------------------------------------------------------
# balanced
# ---------------------------------------------------------------------------


def test_balanced_multiplies_the_effective_price_by_the_clamped_ratio():
    """Three machines at one price, timed at 10x apart: the class median is
    100, so the fast one is discounted to the clamp floor (1/2) and the slow
    one is penalised to the clamp ceiling (2). The clamped factor is asserted
    EXACTLY, as a Fraction — a float here would make two genuinely equal
    scores order themselves by the last bit of a division."""
    quick = _ask("quick", 100, rate=1.0, median=10.0)
    typical = _ask("typical", 100, rate=1.0, median=100.0)
    slow = _ask("slow", 100, rate=1.0, median=1000.0)

    class_median = mk.class_median_seconds([quick, typical, slow])
    assert class_median == Fraction(100)

    score = lambda a: mk.rank_score(a, objective="balanced", class_median=class_median)
    # ratio 1/10, clamped UP to the 1/2 floor.
    assert score(quick) == Fraction(50)
    assert score(typical) == Fraction(100)
    # ratio 10, clamped DOWN to the 2 ceiling.
    assert score(slow) == Fraction(200)
    assert _order([slow, typical, quick], "balanced") == ["quick", "typical", "slow"]


def test_balanced_stays_exact_when_neither_price_nor_ratio_is_a_whole_number():
    """`effective_price` is a Fraction precisely so equal prices compare
    equal; multiplying it by a float ratio would throw that away one line
    later."""
    # 100 / 0.3 = 1000/3 per accepted result, halved by the clamp floor.
    ask = _ask("odd", 100, rate=0.3, median=1.0)
    other = _ask("typical", 100, rate=0.3, median=100.0)
    class_median = mk.class_median_seconds([ask, other])
    assert class_median == Fraction(1_010, 20)  # (1 + 100) / 2

    score = mk.rank_score(ask, objective="balanced", class_median=class_median)
    assert score == Fraction(1_000, 3) * Fraction(1, 2)
    assert isinstance(score, Fraction)


def test_balanced_falls_back_to_the_effective_price_without_medians():
    """A book nobody has timed must rank exactly as `cheapest` does. The
    factor is 1 — not a guess, not an exclusion — for a missing machine
    median and for an undefined class median alike."""
    asks = [
        _ask("dear", 400, rate=1.0),
        _ask("cheap", 300, rate=0.6),
    ]
    assert mk.class_median_seconds(asks) is None
    assert _order(asks, "balanced") == _order(asks, "cheapest") == ["dear", "cheap"]
    assert mk.rank_score(asks[0], objective="balanced") == Fraction(400)

    # ...and one unmeasured machine in a measured book keeps its own factor of
    # 1 while everybody else is scaled around the class median.
    measured = _ask("measured", 100, rate=1.0, median=1_000.0)
    unmeasured = _ask("unmeasured", 100, rate=1.0)
    class_median = mk.class_median_seconds([measured, unmeasured])
    assert class_median == Fraction(1_000)
    assert mk.rank_score(unmeasured, objective="balanced", class_median=class_median) == (
        Fraction(100)
    )


def test_balanced_leaves_an_unclearable_ask_last_like_every_objective():
    """A 0.0-rate host has no finite effective price, so there is nothing to
    scale. It stays in the book, at the end of it."""
    asks = [
        _ask("broken", 1, rate=0.0, median=1.0),
        _ask("working", 900, rate=1.0, median=900.0),
    ]
    for objective in mk.OBJECTIVES:
        assert _order(asks, objective)[-1] == "broken", objective
    # And the SCORE says so under every objective, not only the two whose
    # arithmetic happens to divide by the missing number. `rank_score`
    # publishes the value a row was ORDERED by (it rides beside `formula` in
    # the routing explain), and `fastest_key` orders this row last on its
    # unclearability — never on the 1.0s median it has. Publishing that
    # median would publish a number that decided nothing, and would rank the
    # row FIRST to anybody re-deriving the order from the published score.
    for objective in mk.OBJECTIVES:
        assert mk.rank_score(asks[0], objective=objective) is None, objective


# ---------------------------------------------------------------------------
# Determinism, under all three
# ---------------------------------------------------------------------------


def test_every_objective_is_deterministic_under_a_reordered_input():
    """The book arrives in whatever order the query returned it. Two runs of
    the same bid against the same listings must fill the same machines."""
    asks = [
        _ask("a", 300, rate=1.0, median=12.0),
        _ask("b", 300, rate=1.0, median=12.0),
        _ask("c", 300, rate=None, median=12.0),
        _ask("d", 0, rate=None),
        _ask("e", 300, rate=0.0, median=1.0),
    ]
    for objective in mk.OBJECTIVES:
        forward = _order(asks, objective)
        backward = _order(list(reversed(asks)), objective)
        assert forward == backward, objective
        assert sorted(forward) == ["a", "b", "c", "d", "e"], objective


# ---------------------------------------------------------------------------
# match_bid — the objective, and the caller-supplied unproven cap
# ---------------------------------------------------------------------------


def test_the_objective_changes_which_machine_a_bid_fills():
    """The end of the chain: same book, same cap, one task, and the fill
    follows the objective rather than the price alone."""
    asks = [
        _ask("slow-and-cheap", 100, rate=1.0, median=50.0),
        _ask("quick-and-dear", 900, rate=1.0, median=5.0),
    ]
    cheapest = mk.match_bid(max_zc_per_hour=1_000, tasks_wanted=1, asks=asks)
    fastest = mk.match_bid(
        max_zc_per_hour=1_000, tasks_wanted=1, asks=asks, objective="fastest"
    )
    assert [f.listing_id for f in cheapest.fills] == ["slow-and-cheap"]
    assert [f.listing_id for f in fastest.fills] == ["quick-and-dear"]
    # Executed at the host's own ask under both — the objective picks the
    # machine, it never moves the price (design §4 rule 4).
    assert fastest.fills[0].agreed_zc_per_hour == 900


def test_the_objective_never_buys_past_the_cap():
    """`fastest` is a ranking, not a licence to overpay: the quickest machine
    above the bid does not clear, and the bid falls to the next one that
    does."""
    plan = mk.match_bid(
        max_zc_per_hour=200,
        tasks_wanted=1,
        objective="fastest",
        asks=[
            _ask("quick-and-dear", 900, rate=1.0, median=5.0),
            _ask("slow-and-cheap", 100, rate=1.0, median=50.0),
        ],
    )
    assert [f.listing_id for f in plan.fills] == ["slow-and-cheap"]


def test_a_workspace_machine_still_sets_the_class_median_it_is_withheld_from():
    """The class median is a fact about the CLASS's book, not about which
    listings this particular bid happened to be allowed to use — so it is
    taken before the workspace exclusion, and the number the explain surface
    publishes is the number the match was actually computed against.

    The two answers really do differ here. With the withheld machine counted,
    the class median is 10s: `steady` is typical (factor 1, score 100) and
    `cheap-and-slow` is penalised to the ceiling (45 x 2 = 90) and still
    wins. Drop it and the median becomes 505s, which flatters the slow
    machine (45 x 1.98) and discounts `steady` to the floor (100 x 1/2 = 50)
    — a different fill, from the same book, for no reason a buyer could see.
    """
    mine = _ask("mine", 100, rate=1.0, median=10.0, machine="m-mine")
    steady = _ask("steady", 100, rate=1.0, median=10.0)
    cheap_and_slow = _ask("cheap-and-slow", 45, rate=1.0, median=1_000.0)

    assert mk.class_median_seconds([mine, steady, cheap_and_slow]) == Fraction(10)
    assert mk.class_median_seconds([steady, cheap_and_slow]) == Fraction(505)

    plan = mk.match_bid(
        max_zc_per_hour=1_000,
        tasks_wanted=1,
        objective="balanced",
        asks=[mine, steady, cheap_and_slow],
        workspace_reserved={"m-mine"},
    )
    assert [f.listing_id for f in plan.fills] == ["cheap-and-slow"]


def test_a_caller_supplied_unproven_cap_replaces_the_per_bid_one():
    """The job-level cap: `plan_pool_routing` computes one budget for the
    whole job and threads what is LEFT into each class it walks, so a job
    spilling across three books cannot spend three quarter-shares."""
    asks = [_ask("new", 100, rate=None, cap=8), _ask("known", 900, rate=1.0, cap=8)]

    default = mk.match_bid(max_zc_per_hour=1_000, tasks_wanted=8, asks=asks)
    assert default.unproven_task_cap == 2  # unproven_task_budget(8)
    assert default.unproven_tasks == 2

    threaded = mk.match_bid(
        max_zc_per_hour=1_000, tasks_wanted=8, asks=asks, unproven_cap=1
    )
    assert threaded.unproven_task_cap == 1
    assert threaded.unproven_tasks == 1
    assert {f.listing_id: f.tasks for f in threaded.fills} == {"new": 1, "known": 7}


def test_an_exhausted_unproven_allowance_is_zero_and_not_the_floor_of_one():
    """`unproven_task_budget` never returns 0, deliberately — a single-task
    job must still be able to try a newcomer. A cap the CALLER supplies is a
    different statement: it says how much of the job's one budget is left,
    and 0 left means 0, or the walk would hand out a fresh floor per class."""
    plan = mk.match_bid(
        max_zc_per_hour=1_000,
        tasks_wanted=4,
        unproven_cap=0,
        asks=[_ask("new", 100, rate=None, cap=4), _ask("known", 900, rate=1.0, cap=4)],
    )
    assert plan.unproven_task_cap == 0
    assert plan.unproven_tasks == 0
    assert {f.listing_id: f.tasks for f in plan.fills} == {"known": 4}
