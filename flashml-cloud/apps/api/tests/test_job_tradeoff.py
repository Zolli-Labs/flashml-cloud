"""`GET /v1alpha1/jobs/{job_id}/tradeoff` — what one more machine buys.

The product's central claim made checkable: for each additional rented
machine, what happens to finish time and to cost — **and when nothing
happens**. The honest half is the half under test here, because it is the
half that costs a user real money if it is wrong.

What is pinned:

- **Five advice codes, and the two that refuse are not decoration.**
  `no_marginal_gain` marks a fleet that costs more and finishes no sooner;
  `no_parallelism` marks a job no fleet can speed up at any price;
  `beyond_task_count` marks buying past the work that exists. A curve that
  always slopes downward is a sales tool rather than a planner.
- **A public job is told renting cannot help it, with the reason.** A rented
  host registers `sandbox_capable: false` and a sandboxed job with no pool
  waiver requires true. The stored spec's `placement.pool` is never rewritten
  to make the answer prettier.
- **`null` is not observed and never 0.** A job with no duration evidence
  still gets real fleet sizes and real advice; what it does not get is
  invented seconds or an invented price.
- **Read-only.** The coordinator is unreachable in this module, nothing is
  acquired, and no row is written.

The eligibility predicate and the task expansion are injected from
`flashruntime` HERE, in a test, because a test may import it and
`flashml_cloud_api` may not — `test_import_boundary` confines the package to
`flashruntime.protocol`.
"""
from __future__ import annotations

import time
import uuid

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from flashruntime.scheduler import IsolationAwarePlacement
from flashruntime.service.modea import expand_tasks
from psycopg.rows import dict_row
from psycopg.types.json import Json

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import placement as placementmod
from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.settings import Settings

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"
OPERATOR_TOKEN = "op-secret-do-not-leak-3f9c1b"
COORDINATOR_URL = "http://coordinator.internal:8100"

RTX_4090 = {
    "index": 0,
    "name": "NVIDIA GeForce RTX 4090",
    "memory_total_mb": 24564,
    "compute_capability": "8.9",
}


def sweep_spec(trials: int, *, pool: str | None = None) -> dict:
    """A hyperparameter sweep of exactly ``trials`` independent tasks.

    Built at call time rather than held as a module constant so the task
    count — the denominator of every advice code below — is stated by the
    test that depends on it, right where the expectation is written.

    ``pool`` produces the pool-scoped shape `compile.py` emits: the
    `allowFallback` waiver exists if and only if a pool does, which is the
    coupling that decides whether a rented machine may run the job at all.
    """
    spec: dict = {
        "apiVersion": "flashml.dev/v1alpha1",
        "kind": "Job",
        "metadata": {"name": "tradeoff-sweep"},
        "spec": {
            "image": {"repository": "ghcr.io/zolli/flashml", "tag": "0.6.0"},
            "workload": {
                "type": "hyperparameter_search",
                "parameters": {
                    "grid": {"alpha": [round(i / 100, 4) for i in range(trials)]}
                },
            },
        },
    }
    if pool is not None:
        spec["spec"]["isolation"] = {"tier": "sandboxed", "allowFallback": True}
        spec["spec"]["placement"] = {"pool": pool}
    return spec


def public_sandboxed_spec(trials: int) -> dict:
    """What `compile.py` emits for a job nobody scoped to a workspace:
    sandboxed isolation, no waiver, `placement.pool` = "any"."""
    spec = sweep_spec(trials)
    spec["spec"]["isolation"] = {"tier": "sandboxed", "allowFallback": False}
    spec["spec"]["placement"] = {"pool": "any"}
    return spec


class DeadCoordinator(httpx.AsyncBaseTransport):
    """This route never contacts the coordinator, and this is how that is
    checked rather than asserted: every number below is computed from
    Postgres, so any hop at all fails the request."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"tradeoff must not reach the coordinator: {request.url}")


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def settings(postgres_dsn) -> Settings:
    return Settings(
        supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
        supabase_jwt_secret=JWT_SECRET,
        supabase_service_key="service-key-not-used-here",
        coordinator_url=COORDINATOR_URL,
        coordinator_operator_token=OPERATOR_TOKEN,
        require_auth=True,
        database_url=postgres_dsn,
        console_url="https://console.example",
    )


def _connector(postgres_dsn):
    def connect() -> psycopg.Connection:
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        return conn

    return connect


@pytest.fixture
def client(settings, postgres_dsn):
    app = create_cloud_app(
        settings,
        connect=_connector(postgres_dsn),
        transport=DeadCoordinator(),
        placement_eligible=IsolationAwarePlacement().eligible,
        expand_tasks=expand_tasks,
    )
    with TestClient(app) as c:
        yield c


@pytest.fixture
def unrouted_client(settings, postgres_dsn, monkeypatch):
    """A process where the runtime's gates cannot be resolved at all."""
    monkeypatch.setattr(placementmod, "placement_predicate", lambda: None)
    monkeypatch.setattr(placementmod, "task_expander", lambda: None)
    app = create_cloud_app(
        settings, connect=_connector(postgres_dsn), transport=DeadCoordinator()
    )
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def make_user(db, *, admitted: bool = True) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id, email) values (%s::uuid, %s)",
            (user_id, f"{user_id}@example.test"),
        )
        cur.execute(
            "insert into public.profiles (id, admitted_at) values"
            " (%s::uuid, case when %s then now() else null end)",
            (user_id, admitted),
        )
    return user_id


def make_machine(db, owner_id, *, sandbox_capable: bool = True) -> str:
    machine_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into public.machines"
            " (id, owner_id, node_id, capabilities, status, module_capable,"
            "  sandbox_capable)"
            " values (%s::uuid, %s::uuid, %s, %s, 'active', true, %s)",
            (
                machine_id,
                owner_id,
                f"node-{machine_id}",
                Json({"gpus": [RTX_4090]}),
                sandbox_capable,
            ),
        )
    return machine_id


def bind_machine(db, machine_id: str, pool_id: str) -> None:
    """Bind a machine to a workspace, which is what makes it eligible for a
    workspace-scoped job — and what makes the fleet in these tests
    DETERMINISTIC. The suite shares one Postgres, and any open marketplace
    listing another module leaves behind is reachable by every account; a
    workspace-scoped spec is the only fleet nothing else in the suite can
    join."""
    with db.cursor() as cur:
        cur.execute(
            "insert into public.machine_pools (machine_id, pool_id)"
            " values (%s::uuid, %s::uuid)",
            (machine_id, pool_id),
        )


def make_pool(db, owner_id: str) -> str:
    pool_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pools (id, name, owner_id)"
            " values (%s::uuid, %s, %s::uuid)",
            (pool_id, f"pool-{pool_id[:8]}", owner_id),
        )
        cur.execute(
            "insert into public.pool_members (pool_id, user_id)"
            " values (%s::uuid, %s::uuid)",
            (pool_id, owner_id),
        )
    return pool_id


def join_pool(db, pool_id: str, user_id: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pool_members (pool_id, user_id)"
            " values (%s::uuid, %s::uuid)",
            (pool_id, user_id),
        )


def make_job(db, owner_id, *, spec: dict, pool_id: str | None = None) -> str:
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    dbmod.insert_job(
        db,
        job_id=job_id,
        owner_id=owner_id,
        name="tradeoff-sweep",
        source=None,
        spec=spec,
        status="PENDING",
        pool_id=pool_id,
    )
    return job_id


def seed_evidence(db, job_id: str, *, samples: int = 5, seconds: float = 60.0) -> None:
    """Rung 1: recorded durations on THIS job, from machines that are not
    candidates for the caller. The strongest evidence the estimator has, and
    the only rung this schema can produce."""
    stranger = make_user(db)
    for index in range(samples):
        machine = make_machine(db, stranger)
        with db.cursor() as cur:
            cur.execute(
                "insert into public.contributions"
                " (machine_id, job_id, task_id, duration_s)"
                " values (%s::uuid, %s, %s, %s)",
                (machine, job_id, f"trial-{index:03d}", seconds),
            )


def token(user_id: str) -> dict[str, str]:
    claims = {"sub": user_id, "aud": "authenticated", "exp": time.time() + 3600}
    return {
        "Authorization": f"Bearer {jwt.encode(claims, JWT_SECRET, algorithm='HS256')}"
    }


def tradeoff(client, user_id: str, job_id: str) -> dict:
    r = client.get(
        f"/v1alpha1/jobs/{job_id}/tradeoff", headers=token(user_id)
    )
    assert r.status_code == 200, r.text
    return r.json()


def codes(body: dict) -> list[str]:
    return [point["advice_code"] for point in body["points"]]


def workspace(db, *, machines: int) -> tuple[str, str]:
    """An account, its workspace, and exactly ``machines`` bound to it.

    Returns ``(user_id, pool_id)``. Every test that asserts an exact fleet
    size uses this and a workspace-scoped spec, because the suite shares one
    Postgres session: an open marketplace listing another module leaves
    behind is a candidate for EVERY account, and a job scoped to a workspace
    is the only fleet nothing else in the suite can join. It is also the
    shape `compile.py` actually emits for a team job.
    """
    user = make_user(db)
    pool = make_pool(db, user)
    for _ in range(machines):
        bind_machine(db, make_machine(db, user), pool)
    return user, pool


# ---------------------------------------------------------------------------
# the curve
# ---------------------------------------------------------------------------


def test_the_curve_starts_at_the_hardware_you_already_have(db, client):
    """`baseline` is the buyer's own fleet at zero cost, and it is the row
    every other row is measured against. It is never `helps`: nothing was
    rented, so nothing was bought."""
    owner, pool = workspace(db, machines=1)
    job = make_job(db, owner, spec=sweep_spec(4, pool=pool), pool_id=pool)
    seed_evidence(db, job)

    body = tradeoff(client, owner, job)

    assert body["tasks"] == 4
    assert body["owned"]["machines"] == 1
    assert body["owned"]["slots"] == 1
    first = body["points"][0]
    assert first["advice_code"] == "baseline"
    assert first["rented_slots"] == 0
    assert first["total_slots"] == 1
    assert first["usd_cost"] == 0.0
    assert first["zc_cost"] == 0.0


def test_one_curve_carries_helps_no_marginal_gain_and_beyond_task_count(db, client):
    """Five tasks on one owned machine. The fourth slot is the point of the
    whole module: `ceil(5 / 4)` is `ceil(5 / 3)`, so that machine costs money
    and buys no time, and the row says so instead of sloping downward."""
    owner, pool = workspace(db, machines=1)
    job = make_job(db, owner, spec=sweep_spec(5, pool=pool), pool_id=pool)
    seed_evidence(db, job)

    body = tradeoff(client, owner, job)

    assert body["tasks"] == 5
    assert codes(body) == [
        "baseline",
        "helps",
        "helps",
        "no_marginal_gain",
        "helps",
        "beyond_task_count",
    ]

    by_slots = {point["total_slots"]: point for point in body["points"]}
    # The verdict is arithmetic, not a label: the row marked
    # `no_marginal_gain` finishes at exactly the time of the row before it
    # and costs strictly more.
    assert by_slots[4]["finish_seconds"] == by_slots[3]["finish_seconds"]
    assert by_slots[4]["usd_cost"] > by_slots[3]["usd_cost"]


def test_a_single_task_job_is_told_no_fleet_is_faster(db, client):
    """The honest half, stated plainly. One task cannot be split, so every
    rented row is `no_parallelism` — not `beyond_task_count`, and never
    `helps`."""
    owner, pool = workspace(db, machines=1)
    job = make_job(db, owner, spec=sweep_spec(1, pool=pool), pool_id=pool)
    seed_evidence(db, job)

    body = tradeoff(client, owner, job)

    assert body["tasks"] == 1
    assert codes(body)[0] == "baseline"
    assert set(codes(body)[1:]) == {"no_parallelism"}
    # And renting is still *allowed* — the refusal here is about the shape of
    # the work, not about permission, and merging the two would tell a
    # submitter to go and get a pool for a job a pool cannot help.
    assert body["renting"]["suited"] is True


def test_a_fleet_bigger_than_the_task_count_is_marked_as_such(db, client):
    """Distinct from `no_marginal_gain`: this is past the work entirely."""
    owner, pool = workspace(db, machines=2)
    job = make_job(db, owner, spec=sweep_spec(2, pool=pool), pool_id=pool)
    seed_evidence(db, job)

    body = tradeoff(client, owner, job)

    assert body["owned"]["slots"] == 2
    beyond = [p for p in body["points"] if p["advice_code"] == "beyond_task_count"]
    assert beyond, codes(body)
    assert all(p["total_slots"] > body["tasks"] for p in beyond)


# ---------------------------------------------------------------------------
# the refusal that matters: a public job
# ---------------------------------------------------------------------------


def test_a_public_job_is_told_renting_cannot_help_it_and_why(db, client):
    """The one answer this surface must never soften. A rented host
    registers `sandbox_capable: false`; a sandboxed job with no pool waiver
    is placed only on a host advertising true. So no fleet money can buy
    makes this job finish sooner, and the reason names both facts."""
    # A sandbox-capable machine of the owner's own, so the baseline row
    # below is the fleet they have rather than an empty one.
    owner = make_user(db)
    make_machine(db, owner, sandbox_capable=True)
    job = make_job(db, owner, spec=public_sandboxed_spec(4))
    seed_evidence(db, job)

    body = tradeoff(client, owner, job)

    assert body["renting"]["suited"] is False
    assert body["renting"]["usable"] is False
    assert body["renting"]["slots"] == 0
    reason = body["renting"]["reason"]
    assert "sandbox_capable" in reason
    assert "placement.pool" in reason
    # Only what the account already has. No rented row is offered, because
    # there is no rented row that would be true.
    assert codes(body) == ["baseline"]


def test_a_public_jobs_stored_pool_is_never_rewritten(db, client):
    """Reading the trade-off must not change who is allowed to run somebody
    else's code. The row is compared before and after."""
    owner = make_user(db)
    make_machine(db, owner)
    spec = public_sandboxed_spec(4)
    job = make_job(db, owner, spec=spec)

    before = dbmod.fetch_job_for_viewer(db, job, owner)["spec"]
    tradeoff(client, owner, job)
    after = dbmod.fetch_job_for_viewer(db, job, owner)["spec"]

    assert before == after
    assert after["spec"]["placement"]["pool"] == "any"
    assert after["spec"]["isolation"]["allowFallback"] is False


def test_a_workspace_scoped_job_may_rent_and_says_why(db, client):
    """The other side of the same coupling: `allowFallback` exists if and
    only if a pool does, and it is what lets a machine rented into that
    workspace run the work."""
    owner, pool = workspace(db, machines=1)
    job = make_job(db, owner, spec=sweep_spec(4, pool=pool), pool_id=pool)
    seed_evidence(db, job)

    body = tradeoff(client, owner, job)

    assert body["renting"]["suited"] is True
    assert "allowFallback" in body["renting"]["reason"]
    assert body["renting"]["slots"] >= 1


def test_suited_and_acquirable_are_reported_separately(db, client):
    """The same distinction `preview-plans` keeps: "this job may not use a
    rented machine" and "we could not price one" are different sentences,
    and neither is ever produced from the other."""
    owner = make_user(db)
    make_machine(db, owner)
    job = make_job(db, owner, spec=public_sandboxed_spec(4))

    renting = tradeoff(client, owner, job)["renting"]

    assert renting["suited"] is False
    # A price exists for a rentable venue; the refusal above is not it.
    assert renting["acquirable"] is True
    assert renting["usable"] is False
    assert renting["reason"] != renting["price_reason"]


# ---------------------------------------------------------------------------
# not observed, never zero
# ---------------------------------------------------------------------------


def test_a_job_with_no_duration_evidence_still_gets_real_advice(db, client):
    """`None` from the estimator means *not observed* and travels as `null`.
    The fleet sizes and the advice codes are facts about tasks and slots, so
    they are as true without a second as with one."""
    owner, pool = workspace(db, machines=1)
    job = make_job(db, owner, spec=sweep_spec(5, pool=pool), pool_id=pool)

    body = tradeoff(client, owner, job)

    assert body["duration"] is None
    assert body["task_seconds"] is None
    assert codes(body) == [
        "baseline",
        "helps",
        "helps",
        "no_marginal_gain",
        "helps",
        "beyond_task_count",
    ]
    for point in body["points"]:
        assert point["finish_seconds"] is None
        assert point["usd_cost"] is None
        assert point["total_usd_value"] is None


def test_the_advice_is_identical_with_and_without_a_measured_duration(db, client):
    """Which fleet sizes help does not depend on how long a task takes:
    every comparison inside the curve scales both sides by the same positive
    number. This pins that the unobserved path takes no shortcut that could
    change an answer."""
    owner, pool = workspace(db, machines=1)
    measured = make_job(
        db, owner, spec=sweep_spec(5, pool=pool), pool_id=pool
    )
    unmeasured = make_job(
        db, owner, spec=sweep_spec(5, pool=pool), pool_id=pool
    )
    seed_evidence(db, measured, samples=6, seconds=42.0)

    with_evidence = tradeoff(client, owner, measured)
    without = tradeoff(client, owner, unmeasured)

    assert with_evidence["task_seconds"] is not None
    assert without["task_seconds"] is None
    assert codes(with_evidence) == codes(without)
    assert [p["total_slots"] for p in with_evidence["points"]] == [
        p["total_slots"] for p in without["points"]
    ]


def test_both_units_travel_and_the_total_is_offered_not_assumed(db, client):
    """Owned capacity settles in ZC and rented capacity in USD. At the
    settled 1 ZC = $1 rate a total is permissible, and it is supplied rather
    than left for a reader to compute — but only where both halves were
    observed."""
    owner, pool = workspace(db, machines=1)
    job = make_job(db, owner, spec=sweep_spec(4, pool=pool), pool_id=pool)
    seed_evidence(db, job)

    body = tradeoff(client, owner, job)

    rented = [p for p in body["points"] if p["rented_slots"] > 0]
    assert rented
    for point in body["points"]:
        assert point["zc_cost"] == 0.0
        assert point["usd_cost"] is not None
        assert point["total_usd_value"] == round(
            point["zc_cost"] + point["usd_cost"], 4
        )
    assert all(point["usd_cost"] > 0 for point in rented)


def test_the_price_carries_its_provenance_and_its_own_staleness_verdict(db, client):
    """A scraped price shown as live is a lie with a delay. The USD column
    rests on one published SKU, and the row that produced it travels with
    it."""
    owner, pool = workspace(db, machines=1)
    job = make_job(db, owner, spec=sweep_spec(4, pool=pool), pool_id=pool)

    renting = tradeoff(client, owner, job)["renting"]

    assert renting["acquirable"] is True
    assert renting["venue_id"] == "runpod"
    price = renting["price"]
    assert price["provider"] == "runpod"
    assert price["unit"] == "gpu-hour"
    assert price["currency"] == "USD"
    assert price["captured_at"]
    assert price["source"]
    assert "stale" in price
    assert float(price["amount"]) == renting["usd_per_hour"]


# ---------------------------------------------------------------------------
# who may read it, and what happens when it cannot be answered
# ---------------------------------------------------------------------------


def test_somebody_elses_job_is_404_and_not_403(db, client):
    """A job id is a secret: a 403 would confirm to a guesser that it is
    real."""
    owner, stranger = make_user(db), make_user(db)
    job = make_job(db, owner, spec=sweep_spec(4))

    r = client.get(
        f"/v1alpha1/jobs/{job}/tradeoff", headers=token(stranger)
    )
    assert r.status_code == 404


def test_a_teammate_in_the_jobs_workspace_may_read_it(db, client):
    """Viewer-scoped like every sibling read. The fleet is the READER's own
    reachable capacity, so a teammate sees what it would cost them."""
    owner, mate = make_user(db), make_user(db)
    pool = make_pool(db, owner)
    join_pool(db, pool, mate)
    bind_machine(db, make_machine(db, mate), pool)
    job = make_job(db, owner, spec=sweep_spec(4, pool=pool), pool_id=pool)

    body = tradeoff(client, mate, job)

    assert body["job_id"] == job
    # The READER's fleet, not the owner's: the owner enrolled nothing, and
    # the only machine bound to this workspace is the teammate's own.
    assert body["owned"]["machines"] == 1
    assert body["owned"]["reachable_machines"] >= 1


def test_a_job_with_no_stored_spec_is_a_409_with_a_sentence(db, client):
    owner = make_user(db)
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    dbmod.insert_job(
        db,
        job_id=job_id,
        owner_id=owner,
        name=None,
        source=None,
        spec=None,
        status="PENDING",
    )

    r = client.get(
        f"/v1alpha1/jobs/{job_id}/tradeoff", headers=token(owner)
    )
    assert r.status_code == 409
    assert "spec" in r.json()["detail"]


def test_unconfigured_routing_degrades_rather_than_erroring(db, unrouted_client):
    """A surface that 500s is worse than one that says which question it
    could not answer. No points, a reason, and 200."""
    owner = make_user(db)
    job = make_job(db, owner, spec=sweep_spec(4))

    r = unrouted_client.get(
        f"/v1alpha1/jobs/{job}/tradeoff", headers=token(owner)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["points"] == []
    assert body["renting"] is None
    assert body["owned"] is None
    assert any("routing is not configured" in note for note in body["notes"])


def test_an_account_with_no_machines_still_gets_an_answer(db, client):
    """Zero owned slots is a measurement, not a failure — and it is exactly
    the account for whom renting is the whole question."""
    owner = make_user(db)
    pool = make_pool(db, owner)
    job = make_job(db, owner, spec=sweep_spec(4, pool=pool), pool_id=pool)

    body = tradeoff(client, owner, job)

    assert body["owned"]["slots"] == 0
    assert body["owned"]["machines"] == 0
    assert body["points"]
    assert all(point["rented_slots"] >= 1 for point in body["points"])
