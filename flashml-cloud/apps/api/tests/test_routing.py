from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import marketplace as mk
from flashml_cloud_api import metrics
from flashml_cloud_api import routing
from flashml_cloud_api.routing import (
    RoutingValidationError,
    UnroutableResources,
    job_accept_classes,
    plan_pool_routing,
    refill_open_bids,
)


def _gpu_ladder() -> tuple[str, ...]:
    """The derived GPU accept order, recomputed here from the SAME two
    marketplace constants ``routing`` reads.

    Deliberately not a literal list of six class names: a literal would pass
    while the ladder and the reference prices moved underneath it, which is
    the one failure this assertion exists to catch. Cheapest reference price
    first; ties (``gpu-16gb`` and ``gpu-24gb`` are both 1_000) broken by the
    ladder's own order, so the result is total and stable.
    """
    return tuple(
        sorted(
            (c for c in mk.CAPABILITY_CLASSES if c.startswith("gpu-")),
            key=lambda c: (mk.REFERENCE_ZC_PER_HOUR[c], mk.CAPABILITY_CLASSES.index(c)),
        )
    )


# ---------------------------------------------------------------------------
# job_accept_classes — the derived default
# ---------------------------------------------------------------------------


def test_no_resources_accepts_both_cpu_classes_small_first():
    """A job that asked for nothing runs anywhere a CPU runs. Small first
    because a laptop is the cheaper supply and the job named no reason to
    need more; ``cpu-large`` follows so a thin ``cpu-small`` book does not
    strand a job a workstation could run this second."""
    assert job_accept_classes(None) == ("cpu-small", "cpu-large")
    assert job_accept_classes({}) == ("cpu-small", "cpu-large")


def test_the_cpu_split_mirrors_the_marketplace_threshold():
    """Unchanged from the single-class era: `CPU_LARGE_MIN_CORES` is where a
    workstation stops looking like a laptop, and a job at or above it cannot
    fall back down the ladder."""
    assert job_accept_classes({"cpus": mk.CPU_LARGE_MIN_CORES}) == ("cpu-large",)
    assert job_accept_classes({"cpus": mk.CPU_LARGE_MIN_CORES - 1}) == (
        "cpu-small", "cpu-large",
    )


def test_a_gpu_job_accepts_every_gpu_class_cheapest_reference_first():
    """The refusal this replaced ("routing for gpus > 0 is not available")
    was pinned to a runtime gap that is closed: the pinned flashruntime
    declares `ResourcesSpec.gpuPerTask` and its command recipe stamps
    `payload["gpus"]`, so the coordinator's GPU count gate is live and a GPU
    bid buys hardware somebody can actually claim against."""
    accept = job_accept_classes({"gpus": 1})
    assert accept == _gpu_ladder()
    assert all(c.startswith("gpu-") for c in accept)
    prices = [mk.REFERENCE_ZC_PER_HOUR[c] for c in accept]
    assert prices == sorted(prices), "cheapest reference class is tried first"


def test_a_boolean_gpu_flag_is_checked_before_any_coercion():
    """Carried from the single-class era: the old `isinstance(gpus, bool)`
    guard ran AFTER `res.get("gpus") or 0`, which collapses `False` to `0`
    before the guard ever sees it — dead for that branch, and only silently
    correct because `0` also fails the `int(gpus) > 0` check beside it.
    `True` survived the same coercion, so the guard caught one bool value and
    not the other. The fix reads the raw value first."""
    assert job_accept_classes({"gpus": True}) == _gpu_ladder()
    assert job_accept_classes({"gpus": False}) == ("cpu-small", "cpu-large")


def test_a_non_numeric_gpus_is_a_typed_refusal_not_a_bare_typeerror():
    """A string `int()` cannot parse used to escape as a raw `ValueError` —
    500 territory for the submit hook. `UnroutableResources` is named so the
    hook's validation-time except clause can catch it and answer 400, and it
    is a `RoutingValidationError` so that clause needs to name exactly one
    base class."""
    assert issubclass(UnroutableResources, RoutingValidationError)
    with pytest.raises(UnroutableResources, match="gpus"):
        job_accept_classes({"gpus": "one"})


def test_a_list_or_dict_gpus_is_also_refused_not_a_bare_typeerror():
    with pytest.raises(UnroutableResources, match="gpus"):
        job_accept_classes({"gpus": [1]})
    with pytest.raises(UnroutableResources, match="gpus"):
        job_accept_classes({"gpus": {"count": 1}})


def test_a_non_numeric_cpus_is_a_typed_refusal_not_a_bare_valueerror():
    with pytest.raises(UnroutableResources, match="cpus"):
        job_accept_classes({"cpus": "many"})


def test_every_derived_class_is_a_real_ladder_class():
    for resources in ({}, {"cpus": 2}, {"cpus": 64}, {"gpus": 1}, {"gpus": 4}):
        assert set(job_accept_classes(resources)) <= set(mk.CAPABILITY_CLASSES)


# ---------------------------------------------------------------------------
# job_accept_classes — an explicit placement.accept
# ---------------------------------------------------------------------------


def test_an_explicit_accept_is_honoured_in_the_order_written():
    """Order is the walk order, so it is preserved verbatim — not sorted into
    the derived cheapest-first ladder. A submitter who names the expensive
    class first has said something, and re-sorting would silently overrule
    it."""
    accept = job_accept_classes(
        {"gpus": 1}, {"accept": ["gpu-80gb", "gpu-8gb", "gpu-24gb"]}
    )
    assert accept == ("gpu-80gb", "gpu-8gb", "gpu-24gb")


def test_a_placement_without_accept_falls_back_to_the_derived_default():
    """`placement:` cannot reach here without `accept` through the yaml
    parser, which requires it. A hand-built mapping still can, and deriving
    is the honest answer: an empty constraint constrains nothing."""
    assert job_accept_classes({"cpus": 2}, {}) == ("cpu-small", "cpu-large")
    assert job_accept_classes({"cpus": 2}, None) == ("cpu-small", "cpu-large")


def test_an_unknown_class_is_refused_by_name_with_the_ladder_offered():
    with pytest.raises(RoutingValidationError) as exc_info:
        job_accept_classes({"gpus": 1}, {"accept": ["gpu-12gb"]})
    message = str(exc_info.value)
    assert "gpu-12gb" in message
    # The suggestion is the real ladder, not a restated copy of it.
    for klass in mk.CAPABILITY_CLASSES:
        assert klass in message


def test_a_gpu_job_may_not_accept_a_cpu_class():
    """A promise the class cannot keep: `cpu-large` machines have no GPU, so
    a `gpus: 1` job matched there would be entitled to hardware that is not
    on the machine — an OOM or a placement refusal at claim time, paid for."""
    with pytest.raises(RoutingValidationError) as exc_info:
        job_accept_classes({"gpus": 1}, {"accept": ["gpu-8gb", "cpu-large"]})
    message = str(exc_info.value)
    assert "cpu-large" in message
    assert "gpu" in message


def test_a_cpu_job_may_not_accept_a_gpu_class():
    """The mirror, and it is not symmetric reasoning: a GPU machine could
    physically run CPU work, but its book is priced for its GPU, so buying
    an hour of it for a CPU job is the buyer overpaying by the width of the
    ladder — and it takes the machine out of reach of work that needs it."""
    with pytest.raises(RoutingValidationError) as exc_info:
        job_accept_classes({"cpus": 2}, {"accept": ["cpu-small", "gpu-24gb"]})
    message = str(exc_info.value)
    assert "gpu-24gb" in message
    assert "cpu" in message


def test_a_large_cpu_job_may_not_fall_back_to_cpu_small():
    """A job that asked for a workstation cannot accept a laptop. This is the
    one consistency rule that is about a NUMBER rather than a kind: the same
    `CPU_LARGE_MIN_CORES` threshold that derives the default enforces the
    explicit list, so `accept` cannot be used to route around it."""
    with pytest.raises(RoutingValidationError) as exc_info:
        job_accept_classes(
            {"cpus": mk.CPU_LARGE_MIN_CORES},
            {"accept": ["cpu-large", "cpu-small"]},
        )
    message = str(exc_info.value)
    assert "cpu-small" in message
    assert str(mk.CPU_LARGE_MIN_CORES) in message


def test_a_large_cpu_job_may_still_accept_cpu_large_alone():
    assert job_accept_classes(
        {"cpus": mk.CPU_LARGE_MIN_CORES * 2}, {"accept": ["cpu-large"]}
    ) == ("cpu-large",)


def test_a_repeated_class_is_refused():
    """The walk re-reads the same open book for each entry, with nothing
    written between the reads, so a class named twice would plan the same
    listing's capacity twice and promise a fill only one machine can honour.
    The yaml layer refuses this too; this is the check for every caller that
    did not come through a yaml file."""
    with pytest.raises(RoutingValidationError, match="cpu-small"):
        job_accept_classes({"cpus": 2}, {"accept": ["cpu-small", "cpu-small"]})


def test_an_empty_accept_is_refused_rather_than_derived():
    """`accept: []` says "run nowhere". Falling back to the derived default
    would do the exact opposite of what it says."""
    with pytest.raises(RoutingValidationError, match="accept"):
        job_accept_classes({"cpus": 2}, {"accept": []})


# ---------------------------------------------------------------------------
# plan_pool_routing — against a real, freshly migrated Postgres.
#
# Fixture pattern and seeding helpers copied from tests/test_marketplace.py
# (`db`, `make_user`, `make_machine`) and tests/test_router_evidence.py
# (`resolve_attempt`, for real resolved attempts an acceptance rate can be
# computed from). The ephemeral Postgres in conftest.py is session-scoped and
# never rolled back between tests, so — exactly as
# tests/test_marketplace_class_board.py's module docstring documents for its
# own `cpu-large`/`gpu-16gb` — every book here is real state that persists
# across the whole session.
#
# TWO classes now, because the walk is multi-class: `gpu-80gb-hopper`
# (`CLASS`) and `gpu-80gb` (`SECOND`). Grepping the suite for the first turns
# up nothing that ever lists a machine there, and the only other file that
# lists into the second (`test_marketplace.py`'s donated-listing test) reads
# `price_series` NEWEST-first and asserts a live `open_listings` count for its
# own observation, so an earlier listing of ours cannot reach it — and
# `_clean_books` withdraws ours on teardown regardless, so it runs even if an
# assertion above it fails, leaving both classes exactly as empty as they were
# found.
# ---------------------------------------------------------------------------

CLASS = "gpu-80gb-hopper"
SECOND = "gpu-80gb"
H100 = {
    "index": 0,
    "name": "NVIDIA H100 PCIe",
    "memory_total_mb": 81559,
    "compute_capability": "9.0",
}
#: Same VRAM tier, Ampere rather than Hopper — the one field that separates
#: the two classes (marketplace.HOPPER_MIN_COMPUTE_CAPABILITY), copied from
#: tests/test_marketplace.py's own `A100_80`.
A100 = {
    "index": 0,
    "name": "NVIDIA A100-SXM4-80GB",
    "memory_total_mb": 81920,
    "compute_capability": "8.0",
}


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def _clean_books(db):
    """Leave both of this file's books as empty as they were found.

    Listings are withdrawn, and any live BID is cancelled: since
    ``routing.refill_open_bids`` a bid left `open`/`partial` in a class is not
    inert state, it is live demand that the next listing to appear in that
    class will be matched against. The refill tests below are the only ones
    here that post bids, but the sweep is unconditional for the same reason
    the listing sweep is — a test that fails midway must still hand the class
    back clean.

    **And the book is RE-RECORDED afterwards.** This used to be a bare
    ``update ... set state = 'withdrawn'``, which moves the rows and leaves
    the last `price_observations` row frozen at whatever this file's asks
    were — so `class_board`'s `last_zc` (what `GET /prices` publishes as
    `best_ask_zc`) kept quoting a listing nobody could buy. That is a real
    cross-file leak and it was reproducible:
    ``test_market_routes.py::test_the_zc_ladder_matches_the_live_book_and_never
    _zero_fills`` compares that published number against the LIVE asks and
    failed with `assert 100 == None` whenever this file ran before it. One
    observation per class, exactly as `marketplace.withdraw_listing` would
    have written, is what closes it — the same argument
    ``test_routing_routes.py``'s `_withdraw_after` docstring makes for going
    through the real withdrawal path rather than raw SQL.
    """
    yield
    with db.cursor() as cur:
        cur.execute(
            "update public.listings set state = 'withdrawn'"
            " where capability_class = any(%s) and state = 'open'",
            ([CLASS, SECOND],),
        )
        cur.execute(
            "update public.bids set state = 'cancelled'"
            " where capability_class = any(%s) and state in ('open', 'partial')",
            ([CLASS, SECOND],),
        )
    for klass in (CLASS, SECOND):
        mk.record_price_observation(db, capability_class_name=klass, cause="listing")


def make_user(db) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id, email) values (%s::uuid, %s)",
            (user_id, f"{user_id}@example.test"),
        )
        cur.execute("insert into public.profiles (id) values (%s::uuid)", (user_id,))
    return user_id


def make_machine(db, owner_id, *, capabilities=None) -> str:
    machine_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into public.machines (id, owner_id, node_id, capabilities, status)"
            " values (%s::uuid, %s::uuid, %s, %s, 'active')",
            (
                machine_id,
                owner_id,
                f"node-{machine_id}",
                Json(capabilities if capabilities is not None else {"gpus": [H100]}),
            ),
        )
    return machine_id


def resolve_attempts(
    db, machine_id, *, accepted: int, failed: int, seconds: float = 12
) -> None:
    """`accepted` + `failed` RESOLVED attempts for one machine, written the
    way the three real writers leave them (mirrors
    tests/test_router_evidence.py's `resolve_attempt`) — enough for
    `metrics.acceptance_rates` (via `db.acceptance_rate_rows`) to report a
    real rate once the total reaches `metrics.MIN_EVIDENCE`.

    ``seconds`` is how long each attempt is made to have taken:
    `db.acceptance_rate_rows` derives `duration_s` as
    `resolved_at - claimed_at` on ACCEPTED rows only, so this is what a
    machine's `median_seconds` comes out as — and a failed attempt
    contributes to the rate while timing nothing, exactly as production
    leaves it."""
    with db.cursor() as cur:
        for outcome, count in (("accepted", accepted), ("failed", failed)):
            for _ in range(count):
                lease = f"lease-{uuid.uuid4().hex[:12]}"
                # One `now` per row, not three: `resolved_at - claimed_at` IS
                # the duration this fixture is declaring, and re-reading the
                # clock between the two would leave it `seconds` plus however
                # long the insert took — enough to turn an exact median into
                # a number no test can assert.
                now = datetime.now(timezone.utc)
                cur.execute(
                    "insert into public.attempts"
                    " (lease_id, machine_id, job_id, task_id, claimed_at,"
                    "  accepted_at, resolved_at, outcome)"
                    " values (%s, %s::uuid, %s, %s, %s, %s, %s, %s)",
                    (
                        lease,
                        machine_id,
                        f"job-{uuid.uuid4().hex[:8]}",
                        "t1",
                        now - timedelta(seconds=seconds),
                        now if outcome == "accepted" else None,
                        now,
                        outcome,
                    ),
                )


def only_plan(result) -> object:
    """The single walked class's `MatchPlan`, for the one-class tests below."""
    assert len(result["class_plans"]) == 1
    return result["class_plans"][0]["plan"]


def test_the_book_is_ranked_and_matched_at_asks(db, _clean_books):
    """Design's ranking rule end to end: three machines at 100/300/200 with
    rates 0.5/None/1.0 rank on EFFECTIVE price (200, 300-unproven, 200), a
    250 bid over 4 tasks fills the two clearing listings at their own asks,
    and the listing that cannot clear stays in the book, labelled why."""
    host = make_user(db)
    half = make_machine(db, host)
    new = make_machine(db, host)
    perfect = make_machine(db, host)

    resolve_attempts(db, half, accepted=3, failed=3)  # 6 resolved -> rate 0.5
    resolve_attempts(db, perfect, accepted=5, failed=0)  # 5 resolved -> rate 1.0
    # `new` gets no attempts at all: fewer than MIN_EVIDENCE, stays unproven.

    listing_half = mk.create_listing(
        db, machine_id=half, owner_id=host, ask_zc_per_hour=100,
        max_concurrent_tasks=2,
    )
    listing_new = mk.create_listing(
        db, machine_id=new, owner_id=host, ask_zc_per_hour=300,
        max_concurrent_tasks=2,
    )
    listing_perfect = mk.create_listing(
        db, machine_id=perfect, owner_id=host, ask_zc_per_hour=200,
        max_concurrent_tasks=2,
    )

    result = plan_pool_routing(
        db, accept_classes=(CLASS,), max_zc_per_hour=250, tasks_wanted=4,
    )

    assert result["accept"] == [CLASS]
    assert result["tasks_wanted"] == 4
    assert result["tasks_filled"] == 4
    assert result["tasks_unfilled"] == 0
    assert result["nearest_miss"] is None  # nothing unfilled to miss by

    # Ranked: the two tied-at-200 listings by ask (100 before 200), then the
    # 300 unproven listing last.
    assert [row["listing_id"] for row in result["book"]] == [
        str(listing_half["id"]), str(listing_perfect["id"]), str(listing_new["id"]),
    ]

    by_id = {row["listing_id"]: row for row in result["book"]}

    row_half = by_id[str(listing_half["id"])]
    assert row_half["machine_id"] == half
    assert row_half["capability_class"] == CLASS
    assert row_half["acceptance_rate"] == 0.5
    assert row_half["effective_zc_per_hour"] == "200"
    assert row_half["tasks_assigned"] == 2
    assert row_half["excluded"] is None

    row_perfect = by_id[str(listing_perfect["id"])]
    assert row_perfect["acceptance_rate"] == 1.0
    assert row_perfect["effective_zc_per_hour"] == "200"
    assert row_perfect["tasks_assigned"] == 2
    assert row_perfect["excluded"] is None

    row_new = by_id[str(listing_new["id"])]
    assert row_new["acceptance_rate"] is None
    assert row_new["effective_zc_per_hour"] == "300"
    assert row_new["tasks_assigned"] == 0
    assert row_new["excluded"] == "ask-above-cap"

    plan = only_plan(result)
    assert plan.tasks_filled == 4
    assert {(f.listing_id, f.tasks) for f in plan.fills} == {
        (str(listing_half["id"]), 2), (str(listing_perfect["id"]), 2),
    }


def test_a_starved_bid_reports_the_nearest_miss(db, _clean_books):
    host = make_user(db)
    machine = make_machine(db, host)
    listing = mk.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=500,
    )

    result = plan_pool_routing(
        db, accept_classes=(CLASS,), max_zc_per_hour=250, tasks_wanted=3,
    )

    assert result["tasks_filled"] == 0
    assert result["tasks_unfilled"] == 3
    assert result["nearest_miss"] == {
        "ask_zc_per_hour": 500,
        "listing_id": str(listing["id"]),
        "capability_class": CLASS,
    }
    assert len(result["book"]) == 1
    assert result["book"][0]["excluded"] == "ask-above-cap"
    assert result["book"][0]["tasks_assigned"] == 0
    assert only_plan(result).fills == ()


def test_an_empty_book_explains_itself(db, _clean_books):
    result = plan_pool_routing(
        db, accept_classes=(CLASS,), max_zc_per_hour=250, tasks_wanted=5,
    )
    assert result["accept"] == [CLASS]
    assert result["book"] == []
    assert result["nearest_miss"] is None
    assert result["tasks_wanted"] == 5
    assert result["tasks_filled"] == 0
    assert result["tasks_unfilled"] == 5
    assert only_plan(result).fills == ()


# ---------------------------------------------------------------------------
# I4 (final review): the rest of the `excluded` vocabulary.
# `test_the_book_is_ranked_and_matched_at_asks` above only ever exercises
# "ask-above-cap"; these two pin the other two named reasons — an unproven
# host past the 1/4 share cap, and a clearing listing the bid ran out of
# tasks before reaching.
# ---------------------------------------------------------------------------


def test_an_unproven_host_past_the_share_cap_is_excluded(db, _clean_books):
    """Two unproven hosts (zero resolved attempts each, so both stay below
    `metrics.MIN_EVIDENCE`) both clear an 8-task bid's cap.
    `marketplace.unproven_task_budget(8)` is 2, so the cheaper one fills the
    whole unproven share and the second — which would ALSO have cleared,
    with 6 of the 8 tasks still wanted — is excluded `unproven-cap`, not
    folded into `no-tasks-left` or any other reason."""
    host = make_user(db)
    first = make_machine(db, host)
    second = make_machine(db, host)
    # Neither gets `resolve_attempts`: both stay unproven.

    listing_first = mk.create_listing(
        db, machine_id=first, owner_id=host, ask_zc_per_hour=100,
        max_concurrent_tasks=2,
    )
    listing_second = mk.create_listing(
        db, machine_id=second, owner_id=host, ask_zc_per_hour=200,
        max_concurrent_tasks=6,
    )

    result = plan_pool_routing(
        db, accept_classes=(CLASS,), max_zc_per_hour=250, tasks_wanted=8,
    )

    by_id = {row["listing_id"]: row for row in result["book"]}

    row_first = by_id[str(listing_first["id"])]
    assert row_first["excluded"] is None
    assert row_first["tasks_assigned"] == 2  # unproven_task_budget(8) == 2

    row_second = by_id[str(listing_second["id"])]
    assert row_second["excluded"] == "unproven-cap"
    assert row_second["tasks_assigned"] == 0

    assert result["tasks_filled"] == 2
    assert result["tasks_unfilled"] == 6


def test_a_clearing_listing_beyond_the_wanted_tasks_is_no_tasks_left(
    db, _clean_books
):
    """A cheap, PROVEN listing with enough capacity fills the whole bid by
    itself; a second, still-clearing, still-proven listing ranked right
    behind it is excluded `no-tasks-left` — it never got the chance to
    clear because nothing was left to buy, not because its own price or
    evidence disqualified it (both would otherwise be `ask-above-cap`- or
    `unproven-cap`-eligible, and neither is what actually excluded it)."""
    host = make_user(db)
    cheap = make_machine(db, host)
    also_clears = make_machine(db, host)

    resolve_attempts(db, cheap, accepted=5, failed=0)
    resolve_attempts(db, also_clears, accepted=5, failed=0)

    listing_cheap = mk.create_listing(
        db, machine_id=cheap, owner_id=host, ask_zc_per_hour=100,
        max_concurrent_tasks=5,
    )
    listing_also = mk.create_listing(
        db, machine_id=also_clears, owner_id=host, ask_zc_per_hour=150,
        max_concurrent_tasks=5,
    )

    result = plan_pool_routing(
        db, accept_classes=(CLASS,), max_zc_per_hour=250, tasks_wanted=2,
    )

    by_id = {row["listing_id"]: row for row in result["book"]}

    row_cheap = by_id[str(listing_cheap["id"])]
    assert row_cheap["excluded"] is None
    assert row_cheap["tasks_assigned"] == 2

    row_also = by_id[str(listing_also["id"])]
    assert row_also["excluded"] == "no-tasks-left"
    assert row_also["tasks_assigned"] == 0

    assert result["tasks_filled"] == 2
    assert result["tasks_unfilled"] == 0


# ---------------------------------------------------------------------------
# The ordered multi-class walk.
#
# Every machine below is given five accepted attempts before it is listed, so
# it is PROVEN: the unproven share cap (`unproven_task_budget`) would spill
# tasks into the next class all by itself, and a spill test that cannot tell
# "the first class ran out of capacity" from "the first class ran out of
# unproven budget" is not testing the walk.
# ---------------------------------------------------------------------------


def _proven_listing(db, host, klass, *, ask, capacity):
    machine = make_machine(
        db, host, capabilities={"gpus": [H100 if klass == CLASS else A100]}
    )
    resolve_attempts(db, machine, accepted=5, failed=0)
    listing = mk.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=ask,
        max_concurrent_tasks=capacity,
    )
    assert listing["capability_class"] == klass
    return listing


def test_the_remainder_spills_into_the_next_class_in_walk_order(db, _clean_books):
    """Three tasks, one task of capacity in the first class and plenty in the
    second: the first class fills what it can and the SECOND is asked for the
    remainder only — never for the original three, which would over-buy by
    the amount the first class already covered."""
    host = make_user(db)
    first = _proven_listing(db, host, CLASS, ask=100, capacity=1)
    second = _proven_listing(db, host, SECOND, ask=200, capacity=5)

    result = plan_pool_routing(
        db, accept_classes=(CLASS, SECOND), max_zc_per_hour=250, tasks_wanted=3,
    )

    assert result["accept"] == [CLASS, SECOND]
    assert result["tasks_filled"] == 3
    assert result["tasks_unfilled"] == 0

    assert [entry["capability_class"] for entry in result["class_plans"]] == [
        CLASS, SECOND,
    ]
    assert [entry["tasks_wanted"] for entry in result["class_plans"]] == [3, 2]
    assert [entry["plan"].tasks_filled for entry in result["class_plans"]] == [1, 2]

    by_id = {row["listing_id"]: row for row in result["book"]}
    assert by_id[str(first["id"])]["tasks_assigned"] == 1
    assert by_id[str(second["id"])]["tasks_assigned"] == 2


def test_book_rows_carry_the_class_they_came_from(db, _clean_books):
    """One flat book across several classes is only readable if every row
    says which book it is from — otherwise two listings at 100 in two
    different classes are indistinguishable in the response."""
    host = make_user(db)
    first = _proven_listing(db, host, CLASS, ask=100, capacity=1)
    second = _proven_listing(db, host, SECOND, ask=200, capacity=5)

    result = plan_pool_routing(
        db, accept_classes=(CLASS, SECOND), max_zc_per_hour=250, tasks_wanted=3,
    )

    rows = {row["listing_id"]: row for row in result["book"]}
    assert rows[str(first["id"])]["capability_class"] == CLASS
    assert rows[str(second["id"])]["capability_class"] == SECOND
    # Walk order, not price order: the whole first class precedes the second,
    # even though the second class's listing is not the cheaper of the two.
    classes_in_order = [row["capability_class"] for row in result["book"]]
    assert classes_in_order == sorted(
        classes_in_order, key=lambda c: [CLASS, SECOND].index(c)
    )


def test_a_class_that_fills_everything_ends_the_walk(db, _clean_books):
    """Early stop. The second class is never queried, so it is absent from
    the book and from `class_plans` — a plan that listed it would be
    reporting a book read that never happened."""
    host = make_user(db)
    first = _proven_listing(db, host, CLASS, ask=100, capacity=4)
    second = _proven_listing(db, host, SECOND, ask=50, capacity=4)

    result = plan_pool_routing(
        db, accept_classes=(CLASS, SECOND), max_zc_per_hour=250, tasks_wanted=2,
    )

    assert result["tasks_filled"] == 2
    assert result["tasks_unfilled"] == 0
    assert [entry["capability_class"] for entry in result["class_plans"]] == [CLASS]
    listed = {row["listing_id"] for row in result["book"]}
    assert str(first["id"]) in listed
    # Cheaper, and still not consulted: `accept` is a preference order, not a
    # price search across every class at once.
    assert str(second["id"]) not in listed


def test_the_nearest_miss_is_the_cheapest_across_every_walked_class(
    db, _clean_books
):
    """Both classes are walked and neither clears. The reported miss is the
    cheapest ask above the cap ANYWHERE in the walk — 300 in the second
    class — not the first one the walk happened to see (500, in the first
    class), which is what a per-class "first wins" would report."""
    host = make_user(db)
    _proven_listing(db, host, CLASS, ask=500, capacity=4)
    cheaper_miss = _proven_listing(db, host, SECOND, ask=300, capacity=4)

    result = plan_pool_routing(
        db, accept_classes=(CLASS, SECOND), max_zc_per_hour=250, tasks_wanted=2,
    )

    assert result["tasks_filled"] == 0
    assert result["tasks_unfilled"] == 2
    assert result["nearest_miss"] == {
        "ask_zc_per_hour": 300,
        "listing_id": str(cheaper_miss["id"]),
        "capability_class": SECOND,
    }


def test_a_fully_filled_walk_reports_no_nearest_miss(db, _clean_books):
    """`nearest_miss` answers "why did this not fill" — a filled plan has no
    such question, so an above-cap listing in a walked class is reported in
    the book and nowhere else."""
    host = make_user(db)
    _proven_listing(db, host, CLASS, ask=500, capacity=4)
    _proven_listing(db, host, SECOND, ask=100, capacity=4)

    result = plan_pool_routing(
        db, accept_classes=(CLASS, SECOND), max_zc_per_hour=250, tasks_wanted=2,
    )

    assert result["tasks_filled"] == 2
    assert result["nearest_miss"] is None


def test_workspace_machines_stay_withheld_across_the_whole_walk(db, _clean_books):
    """C1: workspace demand takes priority over the open book, and that is a
    property of the MACHINE, not of the class it is listed in — so the
    reserved set is applied to every class the walk touches, not only the
    first."""
    host = make_user(db)
    first = _proven_listing(db, host, CLASS, ask=100, capacity=4)
    second = _proven_listing(db, host, SECOND, ask=100, capacity=4)

    result = plan_pool_routing(
        db, accept_classes=(CLASS, SECOND), max_zc_per_hour=250, tasks_wanted=2,
        workspace_machine_ids=[first["machine_id"], second["machine_id"]],
    )

    rows = {row["listing_id"]: row for row in result["book"]}
    assert rows[str(first["id"])]["excluded"] == "workspace-free"
    assert rows[str(second["id"])]["excluded"] == "workspace-free"
    assert result["tasks_filled"] == 0


# ---------------------------------------------------------------------------
# The objective, and the math the explain surface publishes.
#
# `_proven_fast_listing` below is `_proven_listing` with a duration: five
# accepted attempts of a stated length, which is what turns
# `metrics.acceptance_rates`' `median_seconds` into a real number for this
# machine in this class. Everything here is `CLASS`/`SECOND`, still this
# file's own two books.
# ---------------------------------------------------------------------------


def _proven_timed_listing(db, host, klass, *, ask, capacity, seconds):
    machine = make_machine(
        db, host, capabilities={"gpus": [H100 if klass == CLASS else A100]}
    )
    resolve_attempts(db, machine, accepted=5, failed=0, seconds=seconds)
    listing = mk.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=ask,
        max_concurrent_tasks=capacity,
    )
    assert listing["capability_class"] == klass
    return listing


def test_the_plan_publishes_the_objective_and_the_formula_it_used(db, _clean_books):
    """Publishing the formula is the point (owner-approved): a buyer who can
    read `objective`, `formula` and each row's `rank_score` together can
    check the engine's arithmetic without being asked to trust it. The string
    comes from `marketplace.OBJECTIVE_FORMULAS`, never from a literal here or
    in the response builder, so the published formula cannot drift from the
    code that ranked the book."""
    host = make_user(db)
    _proven_timed_listing(db, host, CLASS, ask=100, capacity=2, seconds=30)

    for objective in mk.OBJECTIVES:
        result = plan_pool_routing(
            db, accept_classes=(CLASS,), max_zc_per_hour=250, tasks_wanted=1,
            objective=objective,
        )
        assert result["objective"] == objective
        assert result["formula"] == mk.OBJECTIVE_FORMULAS[objective]

    # The engine's fallback, for a caller that names none — `cheapest`, which
    # is what this walk did before objectives existed. (A job that arrived
    # through flashml.yaml always names one; its default is `balanced`.)
    default = plan_pool_routing(
        db, accept_classes=(CLASS,), max_zc_per_hour=250, tasks_wanted=1,
    )
    assert default["objective"] == mk.DEFAULT_RANK_OBJECTIVE == "cheapest"


def test_every_book_row_carries_its_median_and_the_score_it_was_ranked_by(
    db, _clean_books
):
    """Two fields per row, on every objective, so a reader never has to know
    which one is live to know what they are looking at: `median_seconds` (the
    measurement, None when there is none) and `rank_score` (what that row was
    ORDERED by, as an exact Fraction rendered to a string)."""
    host = make_user(db)
    slow = _proven_timed_listing(db, host, CLASS, ask=100, capacity=1, seconds=120)
    quick = _proven_timed_listing(db, host, CLASS, ask=200, capacity=1, seconds=5)

    cheapest = plan_pool_routing(
        db, accept_classes=(CLASS,), max_zc_per_hour=250, tasks_wanted=2,
        objective="cheapest",
    )
    rows = {row["listing_id"]: row for row in cheapest["book"]}
    assert rows[str(slow["id"])]["median_seconds"] == 120.0
    assert rows[str(quick["id"])]["median_seconds"] == 5.0
    # Under `cheapest` the score IS the effective price the row already
    # shows. Both fields are kept anyway, so every objective's row has one
    # shape.
    for row in rows.values():
        assert row["rank_score"] == row["effective_zc_per_hour"]

    fastest = plan_pool_routing(
        db, accept_classes=(CLASS,), max_zc_per_hour=250, tasks_wanted=2,
        objective="fastest",
    )
    rows = {row["listing_id"]: row for row in fastest["book"]}
    assert rows[str(quick["id"])]["rank_score"] == "5"
    assert rows[str(slow["id"])]["rank_score"] == "120"

    balanced = plan_pool_routing(
        db, accept_classes=(CLASS,), max_zc_per_hour=250, tasks_wanted=2,
        objective="balanced",
    )
    rows = {row["listing_id"]: row for row in balanced["book"]}
    # class median of {120, 5} is 62.5; 120/62.5 clamps to 2 (100 x 2), and
    # 5/62.5 clamps to 1/2 (200 x 1/2). Exact, and asserted exactly.
    assert rows[str(slow["id"])]["rank_score"] == "192"
    assert rows[str(quick["id"])]["rank_score"] == "100"


def test_the_objective_decides_which_machine_the_job_fills(db, _clean_books):
    """The whole point of the knob, end to end against a real book: a cheap
    slow machine and a dearer quick one, one task, and the fill follows what
    the submitter asked for rather than the ask alone."""
    host = make_user(db)
    slow = _proven_timed_listing(db, host, CLASS, ask=100, capacity=1, seconds=120)
    quick = _proven_timed_listing(db, host, CLASS, ask=200, capacity=1, seconds=5)

    def filled(objective):
        result = plan_pool_routing(
            db, accept_classes=(CLASS,), max_zc_per_hour=250, tasks_wanted=1,
            objective=objective,
        )
        assert result["tasks_filled"] == 1
        return [f.listing_id for f in only_plan(result).fills]

    assert filled("cheapest") == [str(slow["id"])]
    assert filled("fastest") == [str(quick["id"])]
    # 100 x clamp(120/62.5) = 192 against 200 x clamp(5/62.5) = 100: the
    # quicker machine is the better buy per result even though it asks twice
    # as much, which is the case `balanced` exists to notice.
    assert filled("balanced") == [str(quick["id"])]


def test_a_machine_below_the_evidence_threshold_reports_no_median(db, _clean_books):
    """`metrics.MIN_EVIDENCE` gates the median exactly as it gates the rate.
    Four timed attempts are not evidence of how long this machine takes, and
    reporting their middle value would let `fastest` rank a machine top on
    one lucky short task."""
    host = make_user(db)
    machine = make_machine(db, host)
    resolve_attempts(db, machine, accepted=metrics.MIN_EVIDENCE - 1, failed=0, seconds=3)
    listing = mk.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=100,
    )

    result = plan_pool_routing(
        db, accept_classes=(CLASS,), max_zc_per_hour=250, tasks_wanted=1,
        objective="fastest",
    )
    row = next(r for r in result["book"] if r["listing_id"] == str(listing["id"]))
    assert row["acceptance_rate"] is None
    assert row["median_seconds"] is None
    assert row["rank_score"] is None
    # Unmeasured is not excluded: it is the only listing in the book and it
    # still fills the job.
    assert result["tasks_filled"] == 1


# ---------------------------------------------------------------------------
# The unproven share cap is the JOB's, not each class's.
# ---------------------------------------------------------------------------


def test_the_unproven_cap_is_spent_once_across_every_walked_class(db, _clean_books):
    """Before this fix each `match_bid` recomputed the quarter-share from the
    tasks IT was given, so a job walking two classes could hand newcomers
    half its tasks and a job walking three could hand them three quarters —
    the blast radius growing with the accident of how many books the job
    touched.

    Eight tasks, so the job's whole unproven allowance is 2
    (`unproven_task_budget(8)`). The first class has one unproven machine
    with room for both; the second class has another with room for six. The
    second must be able to spend NOTHING, because the first already spent the
    job's allowance."""
    host = make_user(db)
    first = make_machine(db, host, capabilities={"gpus": [H100]})
    second = make_machine(db, host, capabilities={"gpus": [A100]})
    # Neither is given attempts: both stay below MIN_EVIDENCE, both unproven.
    listing_first = mk.create_listing(
        db, machine_id=first, owner_id=host, ask_zc_per_hour=100,
        max_concurrent_tasks=2,
    )
    listing_second = mk.create_listing(
        db, machine_id=second, owner_id=host, ask_zc_per_hour=100,
        max_concurrent_tasks=6,
    )
    assert listing_first["capability_class"] == CLASS
    assert listing_second["capability_class"] == SECOND

    result = plan_pool_routing(
        db, accept_classes=(CLASS, SECOND), max_zc_per_hour=250, tasks_wanted=8,
    )

    assert [entry["plan"].unproven_task_cap for entry in result["class_plans"]] == [2, 0]
    assert [entry["plan"].unproven_tasks for entry in result["class_plans"]] == [2, 0]
    assert result["tasks_filled"] == 2
    assert result["tasks_unfilled"] == 6

    rows = {row["listing_id"]: row for row in result["book"]}
    assert rows[str(listing_first["id"])]["tasks_assigned"] == 2
    # Excluded by the JOB's exhausted allowance, and labelled as such rather
    # than as "no tasks left" — six of the eight tasks were still wanted.
    assert rows[str(listing_second["id"])]["tasks_assigned"] == 0
    assert rows[str(listing_second["id"])]["excluded"] == "unproven-cap"


def test_a_proven_class_leaves_the_whole_unproven_allowance_for_the_next(
    db, _clean_books
):
    """The other half of the same rule: what the walk threads is what is
    LEFT, so a first class that spent none of the allowance hands all of it
    on. Otherwise the fix would trade one wrong answer for another."""
    host = make_user(db)
    proven = _proven_listing(db, host, CLASS, ask=100, capacity=1)
    newcomer = make_machine(db, host, capabilities={"gpus": [A100]})
    listing_new = mk.create_listing(
        db, machine_id=newcomer, owner_id=host, ask_zc_per_hour=100,
        max_concurrent_tasks=6,
    )
    assert listing_new["capability_class"] == SECOND

    result = plan_pool_routing(
        db, accept_classes=(CLASS, SECOND), max_zc_per_hour=250, tasks_wanted=8,
    )

    assert [entry["plan"].unproven_tasks for entry in result["class_plans"]] == [0, 2]
    assert [entry["plan"].unproven_task_cap for entry in result["class_plans"]] == [2, 2]
    assert result["tasks_filled"] == 3  # 1 proven + 2 of the job's allowance


def test_the_module_never_reaches_for_the_runtime():
    """routing.py orchestrates `marketplace`/`metrics`/`db` and nothing else —
    eligibility stays the coordinator's. A GPU class arriving in this module
    is a marketplace name, not a runtime import."""
    import inspect

    source = inspect.getsource(routing)
    assert "flashruntime" not in source


# ---------------------------------------------------------------------------
# refill_open_bids — partial demand refills when supply appears.
#
# Research doc §5.2 step 8: "partial fills stay open and refill as listings
# appear". Until this function existed nothing ever re-matched: a bid left
# `open`/`partial` because the book was thin at submit time stayed starved for
# ever, even when a listing that would have cleared it appeared a minute
# later. Everything below is `CLASS`, this file's own book, and `_clean_books`
# now cancels the bids as well as withdrawing the listings.
# ---------------------------------------------------------------------------


def _bid_for(db, buyer, klass, *, cap, tasks, objective="cheapest", job_id=None):
    """One bid, posted through the real writer so it carries a real
    objective, a real estimate and a real state machine."""
    return mk.create_bid(
        db,
        job_id=job_id or f"job-refill-{uuid.uuid4().hex[:10]}",
        owner_id=buyer,
        capability_class_name=klass,
        max_zc_per_hour=cap,
        tasks_wanted=tasks,
        est_task_seconds=3600,
        objective=objective,
    )


def _grant_from_the_live_book(db, bid, *, tasks, klass=CLASS):
    """Match `tasks` of `bid` against the book AS IT STANDS, through the same
    two functions the submit hook uses. Never a hand-built `MatchPlan`: the
    thing under test is what a real grant leaves behind, and a fixture-shaped
    plan can leave behind a shape production never produces."""
    planned = plan_pool_routing(
        db, accept_classes=(klass,), max_zc_per_hour=int(bid["max_zc_per_hour"]),
        tasks_wanted=tasks,
    )
    plan = planned["class_plans"][0]["plan"]
    assert plan.fills, "the fixture book should have cleared this bid"
    return mk.grant_matches(db, bid_id=str(bid["id"]), plan=plan)


def _matches(db, bid_id):
    return mk.matches_for_bid(db, str(bid_id))


def _assigned(db, bid_id) -> int:
    return sum(
        int(m["tasks_assigned"]) for m in _matches(db, bid_id)
        if m["state"] != "expired"
    )


def _bid_row(db, bid_id) -> dict:
    with db.cursor() as cur:
        cur.execute(
            "select * from public.bids where id = %s::uuid", (str(bid_id),)
        )
        return cur.fetchone()


def test_a_new_listing_refills_an_open_bid_for_its_whole_remainder(
    db, _clean_books
):
    """The base case the function exists for: a bid posted into an empty book
    is starved, a listing appears, and the bid is matched WITHOUT anybody
    resubmitting the job. Nothing else in the system does this — before it, a
    thin book at submit time was permanent."""
    buyer, host = make_user(db), make_user(db)
    bid = _bid_for(db, buyer, CLASS, cap=250, tasks=3)
    assert _bid_row(db, bid["id"])["state"] == "open"

    listing = _proven_listing(db, host, CLASS, ask=100, capacity=10)

    summary = refill_open_bids(db, capability_class=CLASS)

    assert summary == [{"bid_id": str(bid["id"]), "tasks_granted": 3}]
    granted = _matches(db, bid["id"])
    assert [str(m["listing_id"]) for m in granted] == [str(listing["id"])]
    assert sum(int(m["tasks_assigned"]) for m in granted) == 3
    # `grant_matches` owns the state move; the refill does not second-guess it.
    assert _bid_row(db, bid["id"])["state"] == "filled"


def test_a_refill_grants_the_gap_and_never_the_whole_bid_again(db, _clean_books):
    """The failure mode this function must not have.

    The bid wants four tasks and already holds one. A refill that re-bid
    `tasks_wanted` would entitle five machines to a four-task job — and an
    entitlement is not free just because no money moves at grant: it is
    capacity promised to work that does not exist, and the ledger's escrow
    holds it the moment one of those hosts claims.

    The already-matched listing is deliberately the CHEAPEST in the book and
    is deliberately still open. Ranked first, it would be matched again if the
    refill did not exclude the listings this bid already holds — its
    `max_concurrent_tasks` is not decremented by a match and nothing in
    `open_asks` or `match_bid` knows the bid has been here before.
    """
    buyer, host = make_user(db), make_user(db)
    first = _proven_listing(db, host, CLASS, ask=100, capacity=1)
    bid = _bid_for(db, buyer, CLASS, cap=250, tasks=4)
    _grant_from_the_live_book(db, bid, tasks=1)
    assert _bid_row(db, bid["id"])["state"] == "partial"
    assert _assigned(db, bid["id"]) == 1

    second = _proven_listing(db, host, CLASS, ask=200, capacity=10)

    summary = refill_open_bids(db, capability_class=CLASS)

    assert summary == [{"bid_id": str(bid["id"]), "tasks_granted": 3}]
    by_listing: dict[str, int] = {}
    for m in _matches(db, bid["id"]):
        by_listing[str(m["listing_id"])] = (
            by_listing.get(str(m["listing_id"]), 0) + int(m["tasks_assigned"])
        )
    assert by_listing == {str(first["id"]): 1, str(second["id"]): 3}
    assert _assigned(db, bid["id"]) == 4 == int(bid["tasks_wanted"])
    assert _bid_row(db, bid["id"])["state"] == "filled"


def test_an_expired_match_gives_its_tasks_back_to_the_remainder(db, _clean_books):
    """The remainder is computed over NON-EXPIRED matches, which is the whole
    reason it is recomputed rather than read off the bid.

    An expired match is the ordinary end of an entitlement nobody claimed
    (0018's header: "a matched host that never claims produces no work and no
    charge"). The tasks it was holding are unspent demand again, and a refill
    that counted them as filled would leave the job permanently short by
    however many hosts ghosted it.

    The SAME predicate governs the exclusion set, and this pins both halves:
    the host who ghosted is offered to this bid again, because what the
    exclusion prevents is a second LIVE entitlement on one listing, not a
    second chance for a listing whose entitlement has ended.
    """
    buyer, host = make_user(db), make_user(db)
    ghosted = _proven_listing(db, host, CLASS, ask=100, capacity=1)
    bid = _bid_for(db, buyer, CLASS, cap=250, tasks=4)
    match = _grant_from_the_live_book(db, bid, tasks=1)[0]
    assert mk.close_match(
        db, match_id=str(match["id"]), from_state="granted", to_state="expired"
    )

    fresh = _proven_listing(db, host, CLASS, ask=200, capacity=10)

    summary = refill_open_bids(db, capability_class=CLASS)

    # Four, not three: the expired task came back into the remainder.
    assert summary == [{"bid_id": str(bid["id"]), "tasks_granted": 4}]
    live: dict[str, int] = {}
    for m in _matches(db, bid["id"]):
        if m["state"] == "expired":
            continue
        live[str(m["listing_id"])] = (
            live.get(str(m["listing_id"]), 0) + int(m["tasks_assigned"])
        )
    assert live == {str(ghosted["id"]): 1, str(fresh["id"]): 3}
    assert _assigned(db, bid["id"]) == 4


def test_a_refill_spends_the_jobs_unproven_share_not_a_fresh_one(db, _clean_books):
    """`UNPROVEN_TASK_SHARE` is a bound on how much of ONE JOB may be lost to
    newcomers, and a refill is not a new job.

    The bid has already spent its whole allowance on an unproven host. A
    second unproven listing appears alongside a proven one, both clearing and
    the newcomer CHEAPER — so it would be matched first if the refill computed
    a fresh per-call budget from the remainder. It gets nothing; the proven
    machine takes the gap.
    """
    buyer, host = make_user(db), make_user(db)
    newcomer = make_machine(db, host, capabilities={"gpus": [H100]})
    first = mk.create_listing(
        db, machine_id=newcomer, owner_id=host, ask_zc_per_hour=50,
        max_concurrent_tasks=4,
    )
    assert first["capability_class"] == CLASS

    bid = _bid_for(db, buyer, CLASS, cap=250, tasks=4)
    # `unproven_task_budget(4)` is 1, so this grant spends the lot.
    _grant_from_the_live_book(db, bid, tasks=4)
    assert _assigned(db, bid["id"]) == 1
    assert [m["unproven_host"] for m in _matches(db, bid["id"])] == [True]

    cheap_newcomer = make_machine(db, host, capabilities={"gpus": [H100]})
    second = mk.create_listing(
        db, machine_id=cheap_newcomer, owner_id=host, ask_zc_per_hour=60,
        max_concurrent_tasks=4,
    )
    proven = _proven_listing(db, host, CLASS, ask=200, capacity=4)

    summary = refill_open_bids(db, capability_class=CLASS)

    assert summary == [{"bid_id": str(bid["id"]), "tasks_granted": 3}]
    by_listing = {
        str(m["listing_id"]): int(m["tasks_assigned"])
        for m in _matches(db, bid["id"])
    }
    assert str(second["id"]) not in by_listing
    assert by_listing[str(proven["id"])] == 3
    assert _assigned(db, bid["id"]) == 4


def test_a_spilled_jobs_refill_budget_is_the_job_want_not_the_sum_of_its_bids(
    db, _clean_books
):
    """The allowance is the JOB's, and a spilled job's bids do not add up to
    the job.

    `plan_pool_routing` asks each class for the REMAINDER, so the bids a walk
    leaves behind are nested rather than disjoint: an eight-task job that
    fills three in its first class posts `tasks_wanted` 8 there and 5 in the
    second, and the same three tasks are inside both numbers. Their sum is 13,
    and `unproven_task_budget(13)` is 3 — half again the 2 the submit path
    computed from the single total, granted for nothing but the accident of
    having spilled. Summing is also monotonic in how MANY books the job
    touched, which is the exact amplification `plan_pool_routing` exists to
    remove, arriving one path over.

    One of the job's two allowed newcomer tasks is already spent (in the
    second class), a cheap unproven listing then appears in the first, and the
    refill may place exactly ONE more task on it. The total granted is five
    either way; WHERE the five land is what separates a correct budget from a
    summed one.
    """
    assert mk.unproven_task_budget(8) == 2   # the job's true allowance
    assert mk.unproven_task_budget(13) == 3  # what summing the bids would buy

    buyer, host = make_user(db), make_user(db)
    job_id = f"job-spill-{uuid.uuid4().hex[:10]}"

    # The shape a two-class walk leaves behind: the first class was asked for
    # the whole job and could fill three of it, the second for the five it
    # could not — both bids under one `job_id`, as `route_submitted_job` posts
    # them.
    held = _proven_listing(db, host, CLASS, ask=100, capacity=3)
    first = _bid_for(db, buyer, CLASS, cap=250, tasks=8, job_id=job_id)
    _grant_from_the_live_book(db, first, tasks=8)
    assert _assigned(db, first["id"]) == 3
    assert _bid_row(db, first["id"])["state"] == "partial"

    spill = _bid_for(db, buyer, SECOND, cap=250, tasks=5, job_id=job_id)
    newcomer = make_machine(db, host, capabilities={"gpus": [A100]})
    spent_on = mk.create_listing(
        db, machine_id=newcomer, owner_id=host, ask_zc_per_hour=100,
        max_concurrent_tasks=4,
    )
    assert spent_on["capability_class"] == SECOND
    # `unproven_task_budget(5)` is 1, so one of the job's two lands here.
    _grant_from_the_live_book(db, spill, tasks=5, klass=SECOND)
    assert [m["unproven_host"] for m in _matches(db, spill["id"])] == [True]
    assert _assigned(db, spill["id"]) == 1

    # New supply in the first class. The newcomer is the cheapest thing in the
    # book, so it takes everything the remaining allowance permits and the
    # proven machine takes the rest.
    fresh_machine = make_machine(db, host, capabilities={"gpus": [H100]})
    cheap_newcomer = mk.create_listing(
        db, machine_id=fresh_machine, owner_id=host, ask_zc_per_hour=50,
        max_concurrent_tasks=8,
    )
    assert cheap_newcomer["capability_class"] == CLASS
    proven = _proven_listing(db, host, CLASS, ask=200, capacity=8)

    summary = refill_open_bids(db, capability_class=CLASS)

    assert summary == [{"bid_id": str(first["id"]), "tasks_granted": 5}]
    by_listing = {
        str(m["listing_id"]): int(m["tasks_assigned"])
        for m in _matches(db, first["id"])
        if m["state"] != "expired"
    }
    assert by_listing == {
        str(held["id"]): 3,
        # budget(8) == 2 and the spill already spent one: exactly one left.
        # A summed budget would put two here and three on `proven`.
        str(cheap_newcomer["id"]): 1,
        str(proven["id"]): 4,
    }
    assert _assigned(db, first["id"]) == 8 == int(first["tasks_wanted"])
    # The job's whole newcomer exposure, across every bid it posted, is the
    # number the submit path would have allowed a single-class job of the same
    # size — which is the entire point of the cap.
    assert routing._unproven_tasks_spent(
        db, [str(first["id"]), str(spill["id"])]
    ) == mk.unproven_task_budget(8)


def test_a_filled_bid_is_never_refilled(db, _clean_books):
    """`open_bids` selects `open`/`partial` and `grant_matches` refuses
    anything else, so this is belt and braces — but it is the invariant a
    reader of this function most needs to be sure of: a bid that got
    everything it asked for must not keep accruing entitlements every time a
    host lists a machine."""
    buyer, host = make_user(db), make_user(db)
    _proven_listing(db, host, CLASS, ask=100, capacity=4)
    bid = _bid_for(db, buyer, CLASS, cap=250, tasks=1)
    _grant_from_the_live_book(db, bid, tasks=1)
    assert _bid_row(db, bid["id"])["state"] == "filled"

    _proven_listing(db, host, CLASS, ask=50, capacity=10)

    assert refill_open_bids(db, capability_class=CLASS) == []
    assert len(_matches(db, bid["id"])) == 1


def test_a_withdrawn_listing_refills_nothing(db, _clean_books):
    """A listing out of the book is not supply. `open_asks` already joins on
    `state = 'open'`; this pins that the refill reads the LIVE book rather
    than anything it was handed when the hook fired."""
    buyer, host = make_user(db), make_user(db)
    bid = _bid_for(db, buyer, CLASS, cap=250, tasks=3)
    listing = _proven_listing(db, host, CLASS, ask=100, capacity=10)
    mk.withdraw_listing(db, listing_id=str(listing["id"]), owner_id=host)

    assert refill_open_bids(db, capability_class=CLASS) == []
    assert _matches(db, bid["id"]) == []
    assert _bid_row(db, bid["id"])["state"] == "open"


def test_a_refill_withholds_the_buyers_own_workspace_machines(db, _clean_books):
    """C1/M12, unchanged by the refill: a machine bound to a pool the BID'S
    OWNER belongs to is free to them already, so entitling them to it at a
    price would charge for capacity they have for nothing.

    The exclusion is derived from the bid's `owner_id` — the job's actual
    submitter, which is what `route_submitted_job` passed as `user_id` when it
    posted this row — through the same `workspace_machine_ids_for` the submit
    path uses. A second, hand-rolled notion of "whose workspace is this" is
    exactly the drift that helper exists to prevent.
    """
    buyer, teammate = make_user(db), make_user(db)
    pool = dbmod.create_pool(db, name="refill-team", owner_id=buyer)
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pool_members (pool_id, user_id)"
            " values (%s::uuid, %s::uuid)",
            (str(pool["id"]), teammate),
        )
    bid = _bid_for(db, buyer, CLASS, cap=250, tasks=2)

    free = _proven_listing(db, teammate, CLASS, ask=10, capacity=10)
    dbmod.bind_machine_pool(
        db, machine_id=str(free["machine_id"]), pool_id=str(pool["id"])
    )

    assert refill_open_bids(db, capability_class=CLASS) == []
    assert _matches(db, bid["id"]) == []


def test_a_refill_ranks_the_book_by_the_objective_the_bid_stored(db, _clean_books):
    """Migration 0032 is what makes a refill honest.

    The bid was submitted `fastest`; the cheap machine is slow and the dear
    one is quick. A refill that fell back to the engine's `cheapest` — the
    only thing it COULD have done before the column existed — would quietly
    buy this buyer the opposite machine to the one their submission chose, at
    the same cap, with nothing anywhere to say the objective had changed.
    """
    buyer, host = make_user(db), make_user(db)
    slow = _proven_timed_listing(db, host, CLASS, ask=100, capacity=4, seconds=90)
    fast = _proven_timed_listing(db, host, CLASS, ask=200, capacity=4, seconds=5)
    bid = _bid_for(db, buyer, CLASS, cap=250, tasks=2, objective="fastest")

    summary = refill_open_bids(db, capability_class=CLASS)

    assert summary == [{"bid_id": str(bid["id"]), "tasks_granted": 2}]
    assert [str(m["listing_id"]) for m in _matches(db, bid["id"])] == [
        str(fast["id"])
    ]
    assert str(slow["id"]) not in {
        str(m["listing_id"]) for m in _matches(db, bid["id"])
    }


def test_a_refill_walks_every_open_bid_in_the_class_highest_first(db, _clean_books):
    """The order is `marketplace.open_bids`' — highest cap first — and it is
    left exactly as written rather than re-derived here: it is the fairness
    rule the matching engine already states ("live demand in one book, highest
    bid first — the order matching walks"), and a refill that walked its own
    order would be a second, silent auction rule.

    One listing, two competing bids, capacity for only one of them: the
    higher cap gets it.
    """
    low_buyer, high_buyer, host = make_user(db), make_user(db), make_user(db)
    low = _bid_for(db, low_buyer, CLASS, cap=150, tasks=1)
    high = _bid_for(db, high_buyer, CLASS, cap=250, tasks=1)
    listing = _proven_listing(db, host, CLASS, ask=100, capacity=1)

    summary = refill_open_bids(db, capability_class=CLASS)

    assert [entry["bid_id"] for entry in summary] == [
        str(high["id"]), str(low["id"]),
    ]
    # Both are entitled: `max_concurrent_tasks` is a concurrency hint, not a
    # decrementing quota, and over-entitling is how volunteer supply finishes
    # a job when a third of the hosts never show up (0018's header). What the
    # order decides is who is asked FIRST, which is what this pins.
    assert [str(m["listing_id"]) for m in _matches(db, high["id"])] == [
        str(listing["id"])
    ]


# ---------------------------------------------------------------------------
# Concurrency: two refills over one bid, and one bid's failure over a walk.
#
# The refill hook fires from `create_market_listing`, so two hosts listing into
# the same class in the same second run two of these walks at once over the
# same open bids. Everything `refill_open_bids` recomputes per bid — the
# remainder, the held listings, the job's unproven spend — is a READ, and a
# read cannot see a write the other connection has not committed yet.
# ---------------------------------------------------------------------------


def _second_connection(dsn):
    conn = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    return conn


def _wait_until_someone_blocks_on_a_lock(db, *, timeout=5.0) -> bool:
    """True once another backend is waiting on a lock.

    Polled rather than slept for: with the bid lock in place the second walk
    blocks within milliseconds, so the passing run costs nothing. A run where
    nobody ever blocks (the unlocked shape this test exists to refuse) falls
    through after `timeout` and the assertions below do the failing — the
    outcome is what is under test, not the timing.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with db.cursor() as cur:
            cur.execute(
                "select count(*) as waiting from pg_stat_activity"
                " where wait_event_type = 'Lock' and state = 'active'"
            )
            if int(cur.fetchone()["waiting"]) > 0:
                return True
        time.sleep(0.02)
    return False


def test_two_concurrent_refills_entitle_one_bid_exactly_once(
    db, postgres_dsn, _clean_books, monkeypatch
):
    """The race the bid's row lock exists for.

    One bid wanting four tasks, one listing offering two, and two refill walks
    on two connections. Unlocked, both walks read "this bid still wants four
    and holds nothing", both plan the same listing and both grant it: two
    entitlements on ONE machine for ONE bid, four tasks' worth of promises
    against two tasks of capacity. That is the single shape of over-entitlement
    0018's "over-entitling is how volunteer supply finishes a job" argument
    does not cover, and the shape `exclude_listings` exists to prevent — which
    it cannot, because it is built from a read of matches the other walk has
    not committed.

    The first walk is held between its reads and its grant; the second is
    started and observed to BLOCK (on the bid's row lock) before the first is
    released. Afterwards the second re-reads a remainder and a held-set that
    include the first's grant, finds the only listing in the book excluded,
    and grants nothing.
    """
    buyer, host = make_user(db), make_user(db)
    bid = _bid_for(db, buyer, CLASS, cap=250, tasks=4)
    listing = _proven_listing(db, host, CLASS, ask=100, capacity=2)

    first = "refill-first"
    real_plan = routing._plan_one_class
    reached = threading.Event()
    release = threading.Event()

    def paused(*args, **kwargs):
        if threading.current_thread().name == first:
            reached.set()
            assert release.wait(timeout=20), "the first walk was never released"
        return real_plan(*args, **kwargs)

    monkeypatch.setattr(routing, "_plan_one_class", paused)

    summaries: dict[str, list] = {}
    errors: list[BaseException] = []

    def walk(name):
        conn = _second_connection(postgres_dsn)
        try:
            summaries[name] = refill_open_bids(conn, capability_class=CLASS)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the test body
            errors.append(exc)
        finally:
            conn.close()

    a = threading.Thread(target=walk, args=(first,), name=first)
    b = threading.Thread(target=walk, args=("refill-second",), name="refill-second")
    a.start()
    try:
        assert reached.wait(timeout=20), "the first walk never reached its plan"
        b.start()
        # The second walk must be WAITING, not planning. If this ever returns
        # False the lock is gone and the assertions below report the damage.
        blocked = _wait_until_someone_blocks_on_a_lock(db)
    finally:
        release.set()
        a.join(timeout=20)
        b.join(timeout=20)

    assert not errors, errors
    assert blocked, "the second refill planned against the first's stale state"
    assert summaries[first] == [{"bid_id": str(bid["id"]), "tasks_granted": 2}]
    assert summaries["refill-second"] == []

    granted = _matches(db, bid["id"])
    assert [str(m["listing_id"]) for m in granted] == [str(listing["id"])]
    assert sum(int(m["tasks_assigned"]) for m in granted) == 2
    assert _bid_row(db, bid["id"])["state"] == "partial"


def test_one_bids_lost_race_does_not_discard_the_walks_earlier_grants(
    db, _clean_books, monkeypatch
):
    """The walk is one transaction, and it used to be one savepoint too.

    Two bids, the second of which fails its grant the way a racer can make it
    fail — `IllegalTransition`, because somebody filled or cancelled it between
    this walk's two reads. Without a per-bid savepoint that exception unwinds
    the WHOLE transaction: the first bid's perfectly good entitlement is rolled
    back with it, and `create_market_listing`'s fail-open reports
    `"refilled": []` about a refill that really did match somebody.

    The stub grants for real before raising, so what is asserted below is that
    the savepoint undid writes that existed — the match row and the bid-state
    move `grant_matches` had already made — rather than that the writes never
    happened.
    """
    buyer_a, buyer_b, host = make_user(db), make_user(db), make_user(db)
    high = _bid_for(db, buyer_a, CLASS, cap=250, tasks=1)
    low = _bid_for(db, buyer_b, CLASS, cap=150, tasks=1)
    listing = _proven_listing(db, host, CLASS, ask=100, capacity=4)

    real_grant = mk.grant_matches

    def grant_then_lose_the_race(conn, *, bid_id, plan):
        rows = real_grant(conn, bid_id=bid_id, plan=plan)
        if bid_id == str(low["id"]):
            raise mk.IllegalTransition("a filled bid cannot be matched")
        return rows

    monkeypatch.setattr(mk, "grant_matches", grant_then_lose_the_race)

    summary = refill_open_bids(db, capability_class=CLASS)

    # The walk finished, and it reports exactly the bid that kept its grant.
    assert summary == [{"bid_id": str(high["id"]), "tasks_granted": 1}]
    assert [str(m["listing_id"]) for m in _matches(db, high["id"])] == [
        str(listing["id"])
    ]
    assert _bid_row(db, high["id"])["state"] == "filled"

    # And the loser is exactly as it was: no match, no state move.
    assert _matches(db, low["id"]) == []
    assert _bid_row(db, low["id"])["state"] == "open"


def _clone_match(db, match_id) -> None:
    """A second row with the same `(bid_id, listing_id)` — the shape a lost
    race leaves behind — written straight at the table, past every guard in
    `marketplace` and `routing`."""
    with db.cursor() as cur:
        cur.execute(
            "insert into public.matches"
            " (bid_id, listing_id, machine_id, buyer_id, host_id,"
            "  capability_class, agreed_zc_per_hour, tasks_assigned,"
            "  est_task_seconds, unproven_host, state)"
            " select bid_id, listing_id, machine_id, buyer_id, host_id,"
            "        capability_class, agreed_zc_per_hour, tasks_assigned,"
            "        est_task_seconds, unproven_host, 'granted'"
            "   from public.matches where id = %s::uuid",
            (str(match_id),),
        )


def test_the_table_refuses_a_second_live_match_on_one_listing_for_one_bid(
    db, _clean_books
):
    """Migration 0033, checked against the applied schema.

    The lock is the fix; this is the backstop, and it is the reason a third
    caller that re-matches a live bid one day inherits the guarantee without
    having to rediscover the argument.
    """
    buyer, host = make_user(db), make_user(db)
    listing = _proven_listing(db, host, CLASS, ask=100, capacity=4)
    bid = _bid_for(db, buyer, CLASS, cap=250, tasks=4)
    granted = _grant_from_the_live_book(db, bid, tasks=1)
    assert [str(m["listing_id"]) for m in granted] == [str(listing["id"])]

    with pytest.raises(psycopg.errors.UniqueViolation):
        with db.transaction():
            _clone_match(db, granted[0]["id"])


def test_an_expired_match_leaves_its_listing_matchable_by_the_same_bid_again(
    db, _clean_books
):
    """Why 0033 is PARTIAL and not a bare unique index.

    An expired entitlement holds nothing — 0018's "no work, no charge, not an
    error" — and `_live_tasks_assigned`, `grant_matches` and the refill's own
    held-set all agree by filtering `state <> 'expired'`. A bare unique index
    would disagree with all three and permanently burn a listing for a bid
    because one host went offline once.
    """
    buyer, host = make_user(db), make_user(db)
    _proven_listing(db, host, CLASS, ask=100, capacity=4)
    bid = _bid_for(db, buyer, CLASS, cap=250, tasks=4)
    granted = _grant_from_the_live_book(db, bid, tasks=1)

    with db.cursor() as cur:
        cur.execute(
            "update public.matches set state = 'expired' where id = %s::uuid",
            (str(granted[0]["id"]),),
        )

    # No violation: the row that held this listing no longer holds anything.
    _clone_match(db, granted[0]["id"])
    assert _assigned(db, bid["id"]) == 1
