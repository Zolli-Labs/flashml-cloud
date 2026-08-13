"""``POST /v1alpha1/jobs/cost-quote`` — a rough, class-level price (AG-5).

``preview-plans`` prices the exact machines in its fleet at their own asks.
This route answers a different, coarser question: what does a CLASS of
capacity cost at the market's live going rate right now, independent of any
one listing. It shares the exact fleet-building call ``preview-plans`` makes
(``_route_plan`` -> ``router.plan_job``) so the two surfaces can never
disagree about which venues and classes are in play — see
``test_plan_preview.py`` for the pinned properties of that shared planner;
this file pins only what ``cost-quote`` adds on top of it.

What is pinned here:

- **Correct arithmetic**: ``price_per_hour x (seconds / 3600) x tasks``,
  reported in an explicit unit.
- **Null is never zero.** A capability class the market has never priced
  answers ``null`` — the load-bearing honesty rule, same as everywhere else
  in this codebase's cost surfaces.
- **This is an estimate, never a bill** (``kind: "estimate"``).
- **A total range spans the priced rows and is null, not zero, when nothing
  is priced.**
- **ZC and USD are never silently combined** — ``total`` keeps one range per
  currency rather than summing them, and every priced row today settles in
  ZC (``db.router_candidates_for_owner`` documents that ``rented`` "has no
  producer" in this deployment), so the USD range stays null by
  construction.
- **Auth matches ``preview-plans``** — ``admitted_user``, refused the same
  way.
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

#: Four independent trials — the same fixture `test_plan_preview.py` uses, so
#: the fleet the two routes see is directly comparable.
SWEEP = {
    "apiVersion": "flashml.dev/v1alpha1",
    "kind": "Job",
    "metadata": {"name": "cost-quote-sweep"},
    "spec": {
        "image": {"repository": "ghcr.io/zolli/flashml", "tag": "0.4.4"},
        "workload": {
            "type": "hyperparameter_search",
            "parameters": {"grid": {"alpha": [0.1, 0.2, 0.3, 0.4]}},
        },
    },
}


class DeadCoordinator(httpx.AsyncBaseTransport):
    """This route never contacts the coordinator — everything it needs comes
    from Postgres and the market board, same discipline as `preview-plans`."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"cost-quote must not reach the coordinator: {request.url}")


# ---------------------------------------------------------------------------
# fixtures — mirrors test_plan_preview.py's shape
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
        name="cost-quote-sweep",
        source=None,
        spec=spec if spec is not None else SWEEP,
        status="PENDING",
    )
    return job_id


def seed_evidence(db, job_id: str, *, samples: int = 5, seconds: float = 10.0) -> None:
    """`samples` recorded durations on this job, from machines that are NOT
    candidates for the caller — same rung-1 fixture as `test_plan_preview.py`.
    Identical seconds across every sample gives an exact, checkable
    `low == high == seconds` estimate rather than a band."""
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


def quote(client, user_id, **body) -> dict:
    r = client.post(
        "/v1alpha1/jobs/cost-quote", json=body, headers=token(user_id)
    )
    assert r.status_code == 200, r.text
    return r.json()


def row_for(body: dict, *, venue: str, capability_class: str) -> dict:
    found = [
        e
        for e in body["estimates"]
        if e["venue"] == venue and e["capability_class"] == capability_class
    ]
    assert found, (venue, capability_class, body["estimates"])
    return found[0]


def isolate_market(monkeypatch, priced: dict) -> None:
    """Pin `class_board`'s market price per class, deterministically.

    `postgres_dsn` is session-scoped (`conftest.py`) — every test file in a
    single `pytest` run shares ONE live Postgres — and
    `router_candidates_for_owner` surfaces every OPEN listing on the market
    globally, not just this account's own (see its docstring). Several other
    files in this suite list real machines into `gpu-24gb` specifically and
    leave the listings open, so an un-isolated median for that class (or a
    stray listing in some other class this test never mentions) would leak
    into `estimates`/`total` depending on what ran earlier in the session.

    Every class named in `priced` gets exactly that `median_ask_zc`; every
    other class is forced to `None` (unpriced), so a stray listing anywhere
    else in the shared book can only ever surface here as a `null` row,
    never as a surprise price that shifts an arithmetic assertion.
    """
    real_class_board = mk.class_board

    def patched(conn, capability_class_name, **kwargs):
        board = real_class_board(conn, capability_class_name, **kwargs)
        return {**board, "median_ask_zc": priced.get(capability_class_name)}

    monkeypatch.setattr(mk, "class_board", patched)


# ---------------------------------------------------------------------------
# labelled as an estimate, never a bill
# ---------------------------------------------------------------------------


def test_the_response_is_labelled_an_estimate_not_a_bill(db, client):
    owner = make_user(db)
    make_machine(db, owner)
    job = make_job(db, owner)
    seed_evidence(db, job)

    body = quote(client, owner, job_id=job)
    assert body["kind"] == "estimate"


# ---------------------------------------------------------------------------
# correct arithmetic, explicit unit
# ---------------------------------------------------------------------------


def test_a_priced_market_venue_gets_correct_arithmetic_and_an_explicit_unit(db, client, monkeypatch):
    """A market reference pinned at exactly 1.0 ZC/hour (via `isolate_market`
    — see its docstring for why a real, un-pinned median is not safe to
    assert exact arithmetic against in this suite), 10 measured seconds/task
    (identical samples -> an exact estimate, not a band), 4 tasks:
    1.0 * (10/3600) * 4 = 0.011111... ZC, rounded to 4 places."""
    owner, host = make_user(db), make_user(db)
    theirs = make_machine(db, host)
    mk.create_listing(db, machine_id=theirs, owner_id=host, ask_zc_per_hour=1_000)
    isolate_market(monkeypatch, {"gpu-24gb": 1_000})
    job = make_job(db, owner)
    seed_evidence(db, job, samples=5, seconds=10.0)

    body = quote(client, owner, job_id=job)
    assert body["tasks"] == 4

    row = row_for(body, venue="market", capability_class="gpu-24gb")
    assert row["price_per_hour"] == pytest.approx(1.0)
    assert row["unit"] == "ZC/hour"
    assert row["usd_equivalent_per_hour"] == pytest.approx(1.0)
    assert row["estimated_task_seconds"] == pytest.approx(10.0)
    assert row["duration_basis"] == "measured"
    assert row["estimated_tasks"] == 4
    assert row["estimated_cost"] == pytest.approx(round(1.0 * (10.0 / 3600.0) * 4, 4))
    # >= rather than ==: the shared market book may hold other gpu-24gb
    # listings from earlier tests in this session; this machine is
    # guaranteed to be counted among them, not to be the only one.
    assert row["eligible_machines"] >= 1
    assert isinstance(row["basis"], str) and row["basis"]


def test_workspace_capacity_is_a_real_known_zero_not_an_unknown(db, client):
    """Workspace capacity is free by construction (M1) — a genuine zero, not
    the "no observation" null the market-priced row gets when unpriced. The
    two must never read the same on this surface."""
    owner = make_user(db)
    make_machine(db, owner)
    job = make_job(db, owner)
    seed_evidence(db, job, samples=5, seconds=10.0)

    body = quote(client, owner, job_id=job)
    row = row_for(body, venue="workspace", capability_class="gpu-24gb")
    assert row["price_per_hour"] == 0.0
    assert row["unit"] == "ZC/hour"
    assert row["estimated_cost"] == 0.0
    assert "free" in row["basis"]


# ---------------------------------------------------------------------------
# null is never zero — the load-bearing honesty test
# ---------------------------------------------------------------------------


def test_a_venue_with_no_market_observation_is_null_never_zero(db, client, monkeypatch):
    """A capability class the market has never priced must answer `null`,
    never `0` — `0` reads as free compute and only workspace capacity
    genuinely is. Forced via a patched `class_board` rather than an
    unreachable fixture: any machine that shows up as `venue == "market"`
    via `router_candidates_for_owner` has, by that route's own contract, an
    open listing for its own class — so the book can never be honestly empty
    for a class with a live market candidate in it. This test exercises the
    row-builder's null-handling directly, the same way the router's own
    `class_board` tests inject book states no ordinary fixture reaches."""
    owner, host = make_user(db), make_user(db)
    theirs = make_machine(db, host)
    mk.create_listing(db, machine_id=theirs, owner_id=host, ask_zc_per_hour=2_000)
    isolate_market(monkeypatch, {"gpu-24gb": None})
    job = make_job(db, owner)
    seed_evidence(db, job, samples=5, seconds=10.0)

    body = quote(client, owner, job_id=job)
    row = row_for(body, venue="market", capability_class="gpu-24gb")
    assert row["price_per_hour"] is None
    assert row["unit"] is None
    assert row["usd_equivalent_per_hour"] is None
    assert row["estimated_cost"] is None
    assert row["price_per_hour"] != 0
    assert row["estimated_cost"] != 0


def test_an_unobserved_duration_is_null_not_assumed(db, client, monkeypatch):
    """No recorded evidence and no caller-supplied estimate: the price may
    be known, but the cost that needs a duration must not invent one."""
    owner, host = make_user(db), make_user(db)
    theirs = make_machine(db, host)
    mk.create_listing(db, machine_id=theirs, owner_id=host, ask_zc_per_hour=1_000)
    isolate_market(monkeypatch, {"gpu-24gb": 1_000})
    job = make_job(db, owner)
    # No seed_evidence: nothing on rung 1 for this job.

    body = quote(client, owner, job_id=job)
    row = row_for(body, venue="market", capability_class="gpu-24gb")
    assert row["price_per_hour"] == pytest.approx(1.0)
    assert row["estimated_task_seconds"] is None
    assert row["duration_basis"] is None
    assert row["estimated_cost"] is None


def test_all_unpriced_yields_a_null_total_range_never_zero(db, client, monkeypatch):
    owner, host = make_user(db), make_user(db)
    theirs = make_machine(db, host)
    mk.create_listing(db, machine_id=theirs, owner_id=host, ask_zc_per_hour=2_000)
    # No class named -> every class, including any stray one left open by an
    # earlier test in this shared session, is forced unpriced.
    isolate_market(monkeypatch, {})
    job = make_job(db, owner)
    seed_evidence(db, job, samples=5, seconds=10.0)

    body = quote(client, owner, job_id=job)
    assert body["total"]["zc"] == {"min": None, "max": None}
    assert body["total"]["usd"] == {"min": None, "max": None}


# ---------------------------------------------------------------------------
# total range, and ZC/USD kept apart
# ---------------------------------------------------------------------------


def test_the_total_range_spans_the_priced_venues(db, client, monkeypatch):
    """A free workspace machine and a priced market machine in the same
    class: the range must run from the workspace's real 0 to the market
    row's real, computed cost — not collapse to one row or ignore either."""
    owner, host = make_user(db), make_user(db)
    make_machine(db, owner)
    theirs = make_machine(db, host)
    mk.create_listing(db, machine_id=theirs, owner_id=host, ask_zc_per_hour=1_000)
    isolate_market(monkeypatch, {"gpu-24gb": 1_000})
    job = make_job(db, owner)
    seed_evidence(db, job, samples=5, seconds=10.0)

    body = quote(client, owner, job_id=job)
    market_cost = row_for(body, venue="market", capability_class="gpu-24gb")["estimated_cost"]
    workspace_cost = row_for(body, venue="workspace", capability_class="gpu-24gb")["estimated_cost"]
    assert workspace_cost == 0.0
    assert market_cost > 0.0
    assert body["total"]["zc"]["min"] == pytest.approx(min(workspace_cost, market_cost))
    assert body["total"]["zc"]["max"] == pytest.approx(max(workspace_cost, market_cost))


def test_zc_and_usd_are_never_silently_combined(db, client):
    """`total` carries one range per settlement currency, never a summed
    figure — the same discipline `router.Cost` enforces structurally.
    Every priced row settles in ZC today (`rented` "has no producer": see
    `db.router_candidates_for_owner`), so the USD range stays null rather
    than folding into, or being invented alongside, the ZC one."""
    owner, host = make_user(db), make_user(db)
    theirs = make_machine(db, host)
    mk.create_listing(db, machine_id=theirs, owner_id=host, ask_zc_per_hour=1_000)
    job = make_job(db, owner)
    seed_evidence(db, job, samples=5, seconds=10.0)

    body = quote(client, owner, job_id=job)
    assert set(body["total"]) == {"zc", "usd"}
    assert body["total"]["zc"]["min"] is not None
    assert body["total"]["usd"] == {"min": None, "max": None}
    for row in body["estimates"]:
        # Every row states its own unit; nothing here would let a ZC amount
        # and a USD amount land in the same bucket unlabelled.
        assert row["unit"] in (None, "ZC/hour")


# ---------------------------------------------------------------------------
# a caller-supplied duration is a coarse override, clearly labelled
# ---------------------------------------------------------------------------


def test_a_caller_supplied_duration_overrides_evidence_and_says_so(db, client, monkeypatch):
    owner, host = make_user(db), make_user(db)
    theirs = make_machine(db, host)
    mk.create_listing(db, machine_id=theirs, owner_id=host, ask_zc_per_hour=1_000)
    isolate_market(monkeypatch, {"gpu-24gb": 1_000})
    job = make_job(db, owner)
    seed_evidence(db, job, samples=5, seconds=10.0)

    body = quote(client, owner, job_id=job, estimated_task_seconds=3600)
    row = row_for(body, venue="market", capability_class="gpu-24gb")
    assert row["estimated_task_seconds"] == 3600.0
    assert row["duration_basis"] == "caller-provided"
    assert row["estimated_cost"] == pytest.approx(1.0 * 1.0 * 4)


def test_a_malformed_estimated_duration_is_refused_rather_than_clamped(db, client):
    owner = make_user(db)
    for bad in ({"estimated_task_seconds": -5}, {"estimated_task_seconds": "soon"}):
        r = client.post(
            "/v1alpha1/jobs/cost-quote",
            json={"spec": SWEEP, **bad},
            headers=token(owner),
        )
        assert r.status_code == 400, (bad, r.text)


# ---------------------------------------------------------------------------
# degrading beats erroring, exactly as preview-plans
# ---------------------------------------------------------------------------


def test_no_available_machine_is_an_answer_and_not_an_error(db, client, monkeypatch):
    """Forced empty via the same monkeypatch `test_plan_preview.py` uses,
    rather than relying on a truly empty market: `router_candidates_for_owner`
    surfaces the OPEN market globally, not just this owner's own listings, and
    the ephemeral Postgres is one shared session-scoped database — another
    test in this file may have listed a machine in `gpu-24gb` already."""
    owner = make_user(db)
    job = make_job(db, owner)
    monkeypatch.setattr(dbmod, "router_candidates_for_owner", lambda *a, **k: [])

    body = quote(client, owner, job_id=job)
    assert body["kind"] == "estimate"
    assert body["estimates"] == []
    assert body["total"]["zc"] == {"min": None, "max": None}
    assert any("no active machine" in note for note in body["notes"])


def test_nothing_to_plan_against_is_a_400_not_a_crash(db, client):
    owner = make_user(db)
    r = client.post("/v1alpha1/jobs/cost-quote", json={}, headers=token(owner))
    assert r.status_code == 400


def test_somebody_elses_job_is_404_and_not_403(db, client):
    owner, stranger = make_user(db), make_user(db)
    job = make_job(db, owner)

    r = client.post(
        "/v1alpha1/jobs/cost-quote", json={"job_id": job}, headers=token(stranger)
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# auth matches preview-plans
# ---------------------------------------------------------------------------


def test_an_un_admitted_account_is_refused_the_same_way_preview_plans_is(db, client):
    waiting = make_user(db, admitted=False)
    r = client.post(
        "/v1alpha1/jobs/cost-quote",
        json={"spec": SWEEP},
        headers=token(waiting),
    )
    assert r.status_code == 403
