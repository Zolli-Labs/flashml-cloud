from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Json

from flashml_cloud_api import marketplace as mk
from flashml_cloud_api import routing
from flashml_cloud_api.routing import (
    GpuRoutingUnavailable,
    UnroutableResources,
    job_capability_class,
    plan_pool_routing,
)


def test_no_resources_is_the_small_cpu_class():
    assert job_capability_class(None) == "cpu-small"
    assert job_capability_class({}) == "cpu-small"


def test_the_cpu_split_mirrors_the_marketplace_threshold():
    from flashml_cloud_api.marketplace import CPU_LARGE_MIN_CORES
    assert job_capability_class({"cpus": CPU_LARGE_MIN_CORES}) == "cpu-large"
    assert job_capability_class({"cpus": CPU_LARGE_MIN_CORES - 1}) == "cpu-small"


def test_gpu_jobs_are_refused_with_the_pin_gap_named():
    with pytest.raises(GpuRoutingUnavailable, match="gpuPerTask"):
        job_capability_class({"gpus": 1})


def test_the_result_is_always_a_ladder_class():
    from flashml_cloud_api.marketplace import CAPABILITY_CLASSES
    assert job_capability_class({"cpus": 2}) in CAPABILITY_CLASSES


def test_a_boolean_gpu_flag_is_checked_before_any_coercion():
    """Carried cleanup from Task 2's review: the old `isinstance(gpus, bool)`
    guard ran AFTER `res.get("gpus") or 0`, which collapses `False` to `0`
    before the guard ever sees it — dead for that branch, and only silently
    correct because `0` also fails the `int(gpus) > 0` check beside it.
    `True` survived the same coercion, so the guard caught one bool value and
    not the other. The fix reads the raw value first."""
    with pytest.raises(GpuRoutingUnavailable, match="gpuPerTask"):
        job_capability_class({"gpus": True})
    assert job_capability_class({"gpus": False}) == "cpu-small"


def test_a_non_numeric_gpus_is_a_typed_refusal_not_a_bare_typeerror():
    """I1, final review: a string `int()` cannot parse used to escape as a
    raw `ValueError` — 500 territory for the submit hook. `UnroutableResources`
    is the sibling of `GpuRoutingUnavailable`, named so the submit hook's
    validation-time except clause can catch it and answer 400."""
    with pytest.raises(UnroutableResources, match="gpus"):
        job_capability_class({"gpus": "one"})


def test_a_list_or_dict_gpus_is_also_refused_not_a_bare_typeerror():
    with pytest.raises(UnroutableResources, match="gpus"):
        job_capability_class({"gpus": [1]})
    with pytest.raises(UnroutableResources, match="gpus"):
        job_capability_class({"gpus": {"count": 1}})


def test_a_non_numeric_cpus_is_a_typed_refusal_not_a_bare_valueerror():
    with pytest.raises(UnroutableResources, match="cpus"):
        job_capability_class({"cpus": "many"})


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
# across the whole session. `gpu-80gb-hopper` is the class: grepping every
# other test file for it (and for the only GPU spec that classifies into it,
# an H100-shaped `compute_capability: "9.0"` card) turns up nothing that ever
# lists a machine there, so it starts clean. To stay a good citizen of that
# same discipline for whatever runs after this file, `_clean_hopper_book`
# withdraws every listing these tests create — on teardown, so it runs even
# if an assertion above it fails — leaving the class exactly as empty as it
# was found.
# ---------------------------------------------------------------------------

CLASS = "gpu-80gb-hopper"
H100 = {
    "index": 0,
    "name": "NVIDIA H100 PCIe",
    "memory_total_mb": 81559,
    "compute_capability": "9.0",
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
def _clean_hopper_book(db):
    yield
    with db.cursor() as cur:
        cur.execute(
            "update public.listings set state = 'withdrawn'"
            " where capability_class = %s and state = 'open'",
            (CLASS,),
        )


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


def resolve_attempts(db, machine_id, *, accepted: int, failed: int) -> None:
    """`accepted` + `failed` RESOLVED attempts for one machine, written the
    way the three real writers leave them (mirrors
    tests/test_router_evidence.py's `resolve_attempt`) — enough for
    `metrics.acceptance_rates` (via `db.acceptance_rate_rows`) to report a
    real rate once the total reaches `metrics.MIN_EVIDENCE`."""
    with db.cursor() as cur:
        for outcome, count in (("accepted", accepted), ("failed", failed)):
            for _ in range(count):
                lease = f"lease-{uuid.uuid4().hex[:12]}"
                claimed = datetime.now(timezone.utc) - timedelta(seconds=12)
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
                        claimed,
                        datetime.now(timezone.utc) if outcome == "accepted" else None,
                        datetime.now(timezone.utc),
                        outcome,
                    ),
                )


def test_the_book_is_ranked_and_matched_at_asks(db, _clean_hopper_book):
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
        db, capability_class=CLASS, max_zc_per_hour=250, tasks_wanted=4,
    )

    assert result["capability_class"] == CLASS
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

    assert result["plan"].tasks_filled == 4
    assert {(f.listing_id, f.tasks) for f in result["plan"].fills} == {
        (str(listing_half["id"]), 2), (str(listing_perfect["id"]), 2),
    }


def test_a_starved_bid_reports_the_nearest_miss(db, _clean_hopper_book):
    host = make_user(db)
    machine = make_machine(db, host)
    listing = mk.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=500,
    )

    result = plan_pool_routing(
        db, capability_class=CLASS, max_zc_per_hour=250, tasks_wanted=3,
    )

    assert result["tasks_filled"] == 0
    assert result["tasks_unfilled"] == 3
    assert result["nearest_miss"] == {
        "ask_zc_per_hour": 500, "listing_id": str(listing["id"]),
    }
    assert len(result["book"]) == 1
    assert result["book"][0]["excluded"] == "ask-above-cap"
    assert result["book"][0]["tasks_assigned"] == 0
    assert result["plan"].fills == ()


def test_an_empty_book_explains_itself(db, _clean_hopper_book):
    result = plan_pool_routing(
        db, capability_class=CLASS, max_zc_per_hour=250, tasks_wanted=5,
    )
    assert result["capability_class"] == CLASS
    assert result["book"] == []
    assert result["nearest_miss"] is None
    assert result["tasks_wanted"] == 5
    assert result["tasks_filled"] == 0
    assert result["tasks_unfilled"] == 5
    assert result["plan"].fills == ()


# ---------------------------------------------------------------------------
# I4 (final review): the rest of the `excluded` vocabulary.
# `test_the_book_is_ranked_and_matched_at_asks` above only ever exercises
# "ask-above-cap"; these two pin the other two named reasons — an unproven
# host past the 1/4 share cap, and a clearing listing the bid ran out of
# tasks before reaching.
# ---------------------------------------------------------------------------


def test_an_unproven_host_past_the_share_cap_is_excluded(db, _clean_hopper_book):
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
        db, capability_class=CLASS, max_zc_per_hour=250, tasks_wanted=8,
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
    db, _clean_hopper_book
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
        db, capability_class=CLASS, max_zc_per_hour=250, tasks_wanted=2,
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
