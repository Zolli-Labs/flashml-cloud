"""`POST /v1alpha1/jobs/preview-plans` — the comparison view.

The single highest-value marketplace surface, and the one that must never
touch money: it shows a submitter what their job would cost and how long it
would take in each venue, *before* they have decided anything. Read-only is
therefore a property to test, not a convention to state.

What is pinned here:

- **Three plans, each carrying its basis and its n.** A plan is never better
  founded than the thinnest evidence behind any machine it allocates to.
- **ZC and USD settlement totals stay separate.** The fixed 1 ZC = 1 USD
  policy adds a normalized USD value for comparison without rewriting either
  source total; the budget verdict remains per settlement currency.
- **Gates before price.** The predicate is the runtime's own, injected; a
  machine the gates refuse is excluded however free it is.
- **Degrading beats erroring.** With routing unconfigured, or no machine
  available, or no evidence for the workload, the route answers what it can
  and says which question it could not answer. A comparison view that 500s
  is worse than one that says "not observed".

The eligibility predicate and the task expansion are injected from
`flashruntime` HERE, in a test, because a test may import it and
`flashml_cloud_api` may not — `test_import_boundary` confines the package to
`flashruntime.protocol`. That is the whole reason the seam exists; see
`create_cloud_app`.
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
from flashml_cloud_api import marketplace as mk
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

#: Four independent trials. `hyperparameter_search` is the workload M8 names
#: and the one a market can actually allocate.
SWEEP = {
    "apiVersion": "flashml.dev/v1alpha1",
    "kind": "Job",
    "metadata": {"name": "preview-sweep"},
    "spec": {
        "image": {"repository": "ghcr.io/zolli/flashml", "tag": "0.4.4"},
        "workload": {
            "type": "hyperparameter_search",
            "parameters": {"grid": {"alpha": [0.1, 0.2, 0.3, 0.4]}},
        },
    },
}


class DeadCoordinator(httpx.AsyncBaseTransport):
    """This route never contacts the coordinator, and this is how that is
    checked rather than asserted: every plan below is computed from Postgres,
    so any hop at all fails the request."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"preview must not reach the coordinator: {request.url}")


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
    """A process where the runtime is not importable at all.

    REWRITTEN 2026-08-11. This used to mean "nothing injected", because the
    repo could not reach `flashruntime.scheduler` and an un-injected app was
    therefore always unrouted. That is no longer true: `placement.py` is now a
    sanctioned one-door import (see `test_import_boundary.SANCTIONED_
    EXCEPTIONS`), so an un-injected app resolves the REAL seven gates — which
    is the entire point of the change and is what a deployment now gets.

    So the degraded path has to be forced rather than assumed. Both resolvers
    are stubbed to None, which is exactly what `placement.py` returns when the
    import fails. The behaviour under test is unchanged and still worth
    pinning: when routing genuinely cannot run, the route says so and quotes
    nothing, rather than falling back to a permissive predicate that would
    show a fleet the scheduler will refuse.
    """
    monkeypatch.setattr(placementmod, "placement_predicate", lambda: None)
    monkeypatch.setattr(placementmod, "task_expander", lambda: None)
    app = create_cloud_app(
        settings, connect=_connector(postgres_dsn), transport=DeadCoordinator()
    )
    with TestClient(app) as c:
        yield c


def test_an_uninjected_app_resolves_the_real_gates(settings, postgres_dsn):
    """The regression this change exists to prevent.

    Before `placement.py`, a production app built without explicit injection
    silently degraded to "no plans" — indistinguishable from an empty fleet,
    and wrong in the direction that looks like a working product with nothing
    to offer. A deployment must get the real predicate without anyone
    remembering to wire it.
    """
    app = create_cloud_app(
        settings, connect=_connector(postgres_dsn), transport=DeadCoordinator()
    )
    assert app is not None
    assert placementmod.placement_predicate() is not None
    assert placementmod.task_expander() is not None
    assert placementmod.available() is True


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


def make_machine(db, owner_id, *, capabilities=None, module_capable=True) -> str:
    """An active, registered machine.

    ``module_capable`` is written the way the register proxy writes it, from
    what the agent said about itself. It is the gate the sweep below meets.
    """
    machine_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into public.machines"
            " (id, owner_id, node_id, capabilities, status, module_capable)"
            " values (%s::uuid, %s::uuid, %s, %s, 'active', %s)",
            (
                machine_id,
                owner_id,
                f"node-{machine_id}",
                Json(capabilities if capabilities is not None else {"gpus": [RTX_4090]}),
                module_capable,
            ),
        )
    return machine_id


def make_job(db, owner_id, *, spec=None) -> str:
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    dbmod.insert_job(
        db,
        job_id=job_id,
        owner_id=owner_id,
        name="preview-sweep",
        source=None,
        spec=spec if spec is not None else SWEEP,
        status="PENDING",
    )
    return job_id


def seed_evidence(db, job_id: str, *, samples: int = 5, seconds: float = 10.0) -> None:
    """`samples` recorded durations on this job, from machines that are NOT
    candidates for the caller. Rung 1: the same code, the same inputs, the
    same round — the strongest evidence the estimator has."""
    stranger = make_user(db)
    for index in range(samples):
        machine = make_machine(db, stranger, capabilities={"gpus": [RTX_4090]})
        with db.cursor() as cur:
            cur.execute(
                "insert into public.contributions"
                " (machine_id, job_id, task_id, duration_s)"
                " values (%s::uuid, %s, %s, %s)",
                (machine, job_id, f"trial-{index:03d}", seconds),
            )


def token(user_id: str) -> dict[str, str]:
    claims = {"sub": user_id, "aud": "authenticated", "exp": time.time() + 3600}
    return {"Authorization": f"Bearer {jwt.encode(claims, JWT_SECRET, algorithm='HS256')}"}


def preview(client, user_id, **body) -> dict:
    r = client.post(
        "/v1alpha1/jobs/preview-plans", json=body, headers=token(user_id)
    )
    assert r.status_code == 200, r.text
    return r.json()


def candidate(body: dict, machine_id: str) -> dict:
    found = [c for c in body["candidates"] if c["machine_id"] == machine_id]
    assert found, f"{machine_id} is not among the candidates"
    return found[0]


# ---------------------------------------------------------------------------
# the three plans
# ---------------------------------------------------------------------------


def test_three_plans_each_carry_a_basis_and_an_n(db, client):
    """The comparison view. Balanced exists only because a deadline was given
    — with nothing to balance against it would be "cheapest" shown twice, which
    is the UI convenience these plans must never be."""
    owner = make_user(db)
    mine = [make_machine(db, owner) for _ in range(2)]
    job = make_job(db, owner)
    seed_evidence(db, job, samples=5)

    body = preview(client, owner, job_id=job, deadline=3600)

    assert [plan["name"] for plan in body["plans"]] == [
        "cheapest",
        "balanced",
        "fastest",
    ]
    assert body["tasks"] == 4
    for plan in body["plans"]:
        # Five accepted runs on THIS job: measured, and the n says how many.
        assert plan["basis"] == "measured"
        assert plan["n"] == 5
        for allocation in plan["allocations"]:
            assert allocation["basis"] == "measured"
            assert allocation["n"] == 5

    placed = {
        allocation["machine_id"]
        for plan in body["plans"]
        for allocation in plan["allocations"]
    }
    assert placed & set(mine)
    for machine in mine:
        assert candidate(body, machine)["eligible"] is True
        assert candidate(body, machine)["capability_class"] == "gpu-24gb"


def test_zc_and_usd_keep_settlement_totals_and_add_normalized_value(db, client):
    """Source totals remain visible while 1 ZC = 1 USD makes them comparable."""
    owner = make_user(db)
    make_machine(db, owner)
    job = make_job(db, owner)
    seed_evidence(db, job)

    body = preview(client, owner, job_id=job, deadline=3600, budget_zc=1000)

    for plan in body["plans"]:
        assert set(plan["cost"]) == {"zc", "usd", "total_usd_value"}
        assert plan["cost"]["total_usd_value"] == pytest.approx(
            plan["cost"]["zc"] + plan["cost"]["usd"]
        )
        assert set(plan["within_budget"]) == {"zc", "usd"}
        # A budget given in one currency answers in that currency only. None
        # is "nobody asked", never "it fits".
        assert plan["within_budget"]["zc"] is True
        assert plan["within_budget"]["usd"] is None
        for allocation in plan["allocations"]:
            assert set(allocation["cost"]) == {
                "zc", "usd", "total_usd_value"
            }
            assert allocation["cost"]["total_usd_value"] == pytest.approx(
                allocation["cost"]["zc"] + allocation["cost"]["usd"]
            )


def test_workspace_capacity_is_free_and_the_open_market_is_priced(db, client):
    """M1 and M2 on one page, which is the whole point of the surface. The
    cheapest plan spends nothing because the fleet it consumes is the
    caller's own; the fastest reaches for priced capacity and says what it
    costs."""
    owner, host = make_user(db), make_user(db)
    ours = make_machine(db, owner)
    theirs = make_machine(db, host)
    mk.create_listing(db, machine_id=theirs, owner_id=host, ask_zc_per_hour=2_000)
    job = make_job(db, owner)
    seed_evidence(db, job)

    body = preview(client, owner, job_id=job, deadline=3600)
    plans = {plan["name"]: plan for plan in body["plans"]}

    assert plans["cheapest"]["cost"]["zc"] == 0.0
    assert plans["fastest"]["cost"]["zc"] > 0.0
    # No external vendor is in this fixture, so the USD settlement total is 0.
    assert plans["fastest"]["cost"]["usd"] == 0.0

    assert candidate(body, ours)["venue"] == "workspace"
    assert candidate(body, ours)["price_zc_per_hour"] == 0
    market = candidate(body, theirs)
    assert market["venue"] == "market"
    # Millicredits in the ledger, credits in a quote.
    assert market["price_zc_per_hour"] == 2.0
    assert market["price_per_hour"] == 2.0
    assert market["price_usd_per_hour"] == 2.0
    assert market["price_label"] == "2.00 ZC/hour"
    assert market["usd_equivalent_label"] == "$2.00/hour equivalent"


def test_a_teammates_machine_is_free_and_a_strangers_is_not(db, client):
    """M1 again, from the side that makes it a workspace: members consume each
    other's machines at no charge, so a teammate's rig is workspace capacity
    and not a listing to be priced."""
    owner, teammate = make_user(db), make_user(db)
    pool = dbmod.create_pool(db, name="lab", owner_id=teammate)
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pool_members (pool_id, user_id)"
            " values (%s::uuid, %s::uuid)",
            (pool["id"], owner),
        )
    theirs = make_machine(db, teammate)
    dbmod.bind_machine_pool(db, machine_id=theirs, pool_id=str(pool["id"]))
    job = make_job(db, owner)
    seed_evidence(db, job)

    body = preview(client, owner, job_id=job)
    assert candidate(body, theirs)["venue"] == "workspace"
    assert candidate(body, theirs)["price_zc_per_hour"] == 0


def test_a_gate_refuses_a_machine_that_costs_nothing(db, client):
    """Gates before price, always. An argv-only volunteer cannot run the
    module tier, and being free does not buy it past that — a price never
    buys past a safety or availability property, which is what lets the
    marketplace be wrong about money without being wrong about placement."""
    owner = make_user(db)
    capable = make_machine(db, owner, module_capable=True)
    argv_only = make_machine(db, owner, module_capable=False)
    job = make_job(db, owner)
    seed_evidence(db, job)

    body = preview(client, owner, job_id=job, deadline=3600)

    assert candidate(body, capable)["eligible"] is True
    assert candidate(body, argv_only)["eligible"] is False
    for plan in body["plans"]:
        assert argv_only not in {a["machine_id"] for a in plan["allocations"]}


def test_a_reliability_record_ranks_and_never_removes(db, client):
    """The record a machine has is reported beside it, with the n behind it,
    and `None` below MIN_EVIDENCE — never a number somebody made up, and never
    a reason to drop a candidate."""
    owner = make_user(db)
    machine = make_machine(db, owner)
    job = make_job(db, owner)
    seed_evidence(db, job)

    body = preview(client, owner, job_id=job)
    row = candidate(body, machine)
    assert row["acceptance_rate"] is None
    assert row["reliability_tier"] == "unproven"
    assert row["eligible"] is True


# ---------------------------------------------------------------------------
# nothing moves
# ---------------------------------------------------------------------------


def test_the_preview_matches_nothing_and_moves_no_credit(db, client):
    """Read-only, stated as a test. A comparison view that quietly consumed a
    listing or held a credit would make "just show me the options" a
    transaction."""
    owner, host = make_user(db), make_user(db)
    mk.grant_starting_credits(db, owner)
    theirs = make_machine(db, host)
    listing = mk.create_listing(
        db, machine_id=theirs, owner_id=host, ask_zc_per_hour=2_000
    )
    make_machine(db, owner)
    job = make_job(db, owner)
    seed_evidence(db, job)

    def counts():
        with db.cursor() as cur:
            cur.execute("select count(*) as n from public.matches")
            matches = int(cur.fetchone()["n"])
            cur.execute("select count(*) as n from public.bids")
            bids = int(cur.fetchone()["n"])
            cur.execute("select count(*) as n from public.credit_entries")
            entries = int(cur.fetchone()["n"])
        return matches, bids, entries

    before = counts()
    preview(client, owner, job_id=job, deadline=3600)
    assert counts() == before
    assert mk.balances(db, owner) == {
        "spendable": mk.STARTING_GRANT_ZC,
        "escrow": 0,
    }

    with db.cursor() as cur:
        cur.execute(
            "select state from public.listings where id = %s", (listing["id"],)
        )
        assert cur.fetchone()["state"] == "open"


# ---------------------------------------------------------------------------
# degrading beats erroring
# ---------------------------------------------------------------------------


def test_unconfigured_routing_says_so_and_quotes_nothing(db, unrouted_client):
    """The shipped deployment. The placement gates and the task expansion both
    live in the runtime, and this repo imports only its protocol package — so
    with nothing injected the route reports that rather than falling back to a
    permissive predicate, which would show a fleet the scheduler will refuse."""
    owner = make_user(db)
    make_machine(db, owner)
    job = make_job(db, owner)
    seed_evidence(db, job)

    body = preview(unrouted_client, owner, job_id=job, deadline=3600)
    assert body["plans"] == []
    assert body["candidates"] == []
    assert body["canary"] is None
    assert body["duration"] is None
    assert any("routing is not configured" in note for note in body["notes"])


def test_no_available_machine_is_an_answer_and_not_an_error(db, client, monkeypatch):
    """An account with nothing enrolled, nothing shared and nothing listed. A
    500 here would read as a broken product; "there is no capacity available to
    you" is the truth and is actionable."""
    owner = make_user(db)
    job = make_job(db, owner)
    monkeypatch.setattr(dbmod, "router_candidates_for_owner", lambda *a, **k: [])

    body = preview(client, owner, job_id=job)
    assert body["plans"] == []
    assert body["candidates"] == []
    assert any("no active machine" in note for note in body["notes"])


def test_no_evidence_answers_with_a_canary_rather_than_a_number(db, client):
    """Cold start is the steady state — roughly 29 credited tasks exist in the
    whole ledger — so a novel workload genuinely has no history. The honest
    answer is *not observed*, and the canary is what converts it into a
    measurement: one trial calibrates the other three."""
    owner = make_user(db)
    machine = make_machine(db, owner)
    job = make_job(db, owner)

    body = preview(client, owner, job_id=job, deadline=3600)
    assert body["plans"] == []
    assert body["duration"] is None
    assert body["canary"] is not None
    assert body["canary"]["machine_id"] == machine
    assert body["canary"]["tasks_to_calibrate"] == 3
    assert body["unplannable_machines"] >= 1


def test_a_spec_in_the_body_needs_no_job_at_all(db, client):
    """The other half of the contract: a submitter comparing venues before
    submitting anything. There is no rung-1 evidence for a job that does not
    exist, so this is the canary case by construction."""
    owner = make_user(db)
    make_machine(db, owner)

    body = preview(client, owner, spec=SWEEP)
    assert body["job_id"] is None
    assert body["tasks"] == 4
    assert body["canary"] is not None


def test_somebody_elses_job_is_404_and_not_403(db, client):
    """Unlike a gate, a job id is a secret: a 403 would confirm to a guesser
    that the id is real."""
    owner, stranger = make_user(db), make_user(db)
    job = make_job(db, owner)

    r = client.post(
        "/v1alpha1/jobs/preview-plans",
        json={"job_id": job},
        headers=token(stranger),
    )
    assert r.status_code == 404


def test_an_un_admitted_account_cannot_preview(db, client):
    """`admitted_user`, like every other surface that is the product rather
    than the door to it."""
    waiting = make_user(db, admitted=False)
    r = client.post(
        "/v1alpha1/jobs/preview-plans",
        json={"spec": SWEEP},
        headers=token(waiting),
    )
    assert r.status_code == 403


def test_a_malformed_deadline_is_refused_rather_than_clamped(db, client):
    """Answering a different question than the one asked produces a page whose
    label and contents disagree."""
    owner = make_user(db)
    for bad in ({"deadline": -5}, {"deadline": "soon"}, {"budget_zc": -1}):
        r = client.post(
            "/v1alpha1/jobs/preview-plans",
            json={"spec": SWEEP, **bad},
            headers=token(owner),
        )
        assert r.status_code == 400, (bad, r.text)


def test_a_forty_trial_sweep_over_a_real_fleet_comes_back_promptly(db, client):
    """This sits behind a button, so the bound is a product requirement rather
    than a nicety. There is no LP solver here: homogeneous tasks over machines
    with a rate and a price make cheapest a scan and fastest a water-fill,
    O(M log M + N log min(N, S)). The budget below is twenty times the observed
    cost — it catches an accidental per-task query, not a slow laptop."""
    owner = make_user(db)
    for _ in range(20):
        make_machine(db, owner)
    spec = {
        **SWEEP,
        "spec": {
            **SWEEP["spec"],
            "workload": {
                "type": "hyperparameter_search",
                "parameters": {"grid": {"alpha": [i / 100 for i in range(40)]}},
            },
        },
    }
    job = make_job(db, owner, spec=spec)
    seed_evidence(db, job, samples=8)

    started = time.monotonic()
    body = preview(client, owner, job_id=job, deadline=1800)
    elapsed = time.monotonic() - started

    assert body["tasks"] == 40
    assert body["plans"]
    assert elapsed < 1.0, f"preview took {elapsed:.3f}s"


def test_nothing_to_plan_against_is_a_400_not_a_crash(db, client):
    owner = make_user(db)
    r = client.post(
        "/v1alpha1/jobs/preview-plans", json={}, headers=token(owner)
    )
    assert r.status_code == 400

    r = client.post(
        "/v1alpha1/jobs/preview-plans",
        json={"spec": {"not": "a job spec"}},
        headers=token(owner),
    )
    assert r.status_code == 400
