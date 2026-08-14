"""Task 4: routing wired into the pool submit path.

``POST /v1alpha1/jobs/from-repo`` (and, through the shared
``_stage_compile_and_submit``, ``/jobs/from-upload``) is where a priced
``flashml.yaml`` turns into a real bid against the open book. This file pins
the four invariants task-4-brief.md names:

* a priced job that clears the book gets a bid, granted matches, and a
  ``"routing"`` block in the response that agrees with the ``bids``/
  ``matches`` rows a fresh query finds;
* an unpriced job is untouched — no bid row, no ``"routing"`` key, byte-
  identical to the pre-Task-4 shape;
* routing is FAIL-OPEN: any exception inside ``routing.route_submitted_job``
  still returns 201, with ``routing: {"state": "skipped", "reason":
  "routing-error"}`` and no bid row left behind;
* a priced GPU job is refused with 400 naming ``gpuPerTask`` before a single
  byte reaches the coordinator — a VALIDATION failure
  (``routing.GpuRoutingUnavailable``), not a routing one, so no job row
  exists either.

Fixture and coordinator-stub pattern copied from ``tests/test_jobs_from_repo.py``
(module docstring there: GitHub is never contacted, every repo is a tarball
built in-process). Listing/machine helpers copied from
``tests/test_market_routes.py``. The ephemeral Postgres is session-scoped and
shared with every other file in the run (``conftest.py``), so — exactly as
``tests/test_routing.py``'s module docstring documents for its own book —
every assertion below is scoped to THIS test's own job/bid/listing ids rather
than assuming exclusive control of the ``cpu-small`` class.
"""
from __future__ import annotations

import io
import json
import tarfile
import textwrap
import time
import uuid
from typing import NamedTuple

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import marketplace as marketmod
from flashml_cloud_api import routing as routingmod
from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.settings import Settings

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"
OPERATOR_TOKEN = "op-secret-do-not-leak-3f9c1b"
COORDINATOR_URL = "http://coordinator.internal:8100"

TOP = "acme-trainer-abc1234"

CLEAN_TRAIN_PY = """
    import json

    with open("/work/out/metrics.json", "w") as fh:
        json.dump({"accuracy": 0.9}, fh)
"""

# Kept at a uniform 4-space indent throughout, matching
# tests/test_jobs_from_repo.py's CLEAN_YAML: make_tarball dedents every
# file's content, and a block appended flush-left would make the common
# prefix empty and leave the rest of the document mis-indented.
BASE_YAML = """
    version: 1
    name: routed-trainer
    image: python-slim
    entrypoint: train.py
"""

PRICE_BLOCK = "    price:\n      max_per_hour: 5.00\n"
GPU_BLOCK = "    resources:\n      gpus: 1\n"

CLEAN_YAML = BASE_YAML
PRICED_YAML = BASE_YAML + PRICE_BLOCK
GPU_PRICED_YAML = BASE_YAML + PRICE_BLOCK + GPU_BLOCK

CLEAN_REPO = {"flashml.yaml": CLEAN_YAML, "train.py": CLEAN_TRAIN_PY}
PRICED_REPO = {"flashml.yaml": PRICED_YAML, "train.py": CLEAN_TRAIN_PY}
GPU_PRICED_REPO = {"flashml.yaml": GPU_PRICED_YAML, "train.py": CLEAN_TRAIN_PY}


# ---------------------------------------------------------------------------
# fixture repos + the fake coordinator (copied from test_jobs_from_repo.py)
# ---------------------------------------------------------------------------


def make_tarball(files: dict[str, str], top: str = TOP) -> bytes:
    """A GitHub-shaped tarball: everything under one top-level directory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=top + "/")
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
        for name, content in files.items():
            payload = textwrap.dedent(content).encode()
            member = tarfile.TarInfo(name=f"{top}/{name}")
            member.size = len(payload)
            member.type = tarfile.REGTYPE
            tar.addfile(member, io.BytesIO(payload))
    return buf.getvalue()


class FetchCall(NamedTuple):
    owner: str
    name: str
    ref: str
    token: str | None


class RecordingFetch:
    """Stands in for the GitHub tarball fetch. Never touches the network."""

    def __init__(self, tar_bytes: bytes):
        self.tar_bytes = tar_bytes
        self.calls: list[FetchCall] = []

    def __call__(
        self, owner: str, name: str, ref: str, token: str | None = None
    ) -> bytes:
        self.calls.append(FetchCall(owner, name, ref, token))
        return self.tar_bytes


class FakeCoordinatorTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.artifacts: dict[str, bytes] = {}
        self.submitted: list[dict] = []
        self._prefix = uuid.uuid4().hex[:10]
        self._next_id = 1
        self.submit_status = 201

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        method, path = request.method, request.url.path

        if method == "PUT" and path.startswith("/v1alpha1/artifacts/"):
            key = path[len("/v1alpha1/artifacts/"):]
            self.artifacts[key] = request.content
            return httpx.Response(200, json={"uri": f"artifact://{key}"})

        if method == "POST" and path == "/v1alpha1/jobs":
            if self.submit_status >= 300:
                return httpx.Response(self.submit_status, json={"detail": "refused"})

            body = json.loads(request.content or b"{}")
            self.submitted.append(body)
            job_id = f"job-{self._prefix}-{self._next_id:04d}"
            self._next_id += 1
            return httpx.Response(
                201, json={"job_id": job_id, "spec": body, "state": "RUNNING"}
            )

        return httpx.Response(404, json={"detail": f"unhandled: {method} {path}"})

    @property
    def job_submissions(self) -> list[httpx.Request]:
        return [
            r for r in self.requests
            if r.method == "POST" and r.url.path == "/v1alpha1/jobs"
        ]


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


@pytest.fixture
def transport() -> FakeCoordinatorTransport:
    return FakeCoordinatorTransport()


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def make_client(settings, postgres_dsn, transport):
    clients = []

    def build(files: dict[str, str] | None = None, tar_bytes: bytes | None = None):
        fetch = RecordingFetch(
            tar_bytes if tar_bytes is not None else make_tarball(files or CLEAN_REPO)
        )

        def connect() -> psycopg.Connection:
            conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
            conn.autocommit = True
            return conn

        app = create_cloud_app(
            settings, connect=connect, transport=transport, fetch_repo=fetch,
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        client.fetch = fetch  # type: ignore[attr-defined]
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def _new_user(db, *, admitted: bool = True) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        if admitted:
            cur.execute(
                "insert into public.profiles (id, admitted_at) values (%s, now())",
                (user_id,),
            )
        else:
            cur.execute("insert into public.profiles (id) values (%s)", (user_id,))
    return user_id


def _jwt(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": time.time() + 3600},
        JWT_SECRET,
        algorithm="HS256",
    )


def _post(client, token: str, **body):
    payload = {"repo": "https://github.com/acme/trainer", "ref": "main"}
    payload.update(body)
    return client.post(
        "/v1alpha1/jobs/from-repo",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


def _job_rows(db, owner_id: str) -> list[dict]:
    with db.cursor() as cur:
        cur.execute("select * from public.jobs where owner_id = %s", (owner_id,))
        return list(cur.fetchall())


def _bids_for_job(db, job_id: str) -> list[dict]:
    with db.cursor() as cur:
        cur.execute("select * from public.bids where job_id = %s", (job_id,))
        return list(cur.fetchall())


def _matches_for_bid(db, bid_id) -> list[dict]:
    with db.cursor() as cur:
        cur.execute("select * from public.matches where bid_id = %s", (bid_id,))
        return list(cur.fetchall())


def _machine(db, owner: str, *, capabilities: dict | None = None) -> str:
    """A machine row with the capabilities snapshot the ladder reads —
    active by construction, matching `tests/test_market_routes.py`."""
    with db.cursor() as cur:
        cur.execute(
            "insert into public.machines (owner_id, node_id, name, status,"
            " capabilities)"
            " values (%s, %s, %s, 'active', %s) returning id",
            (
                owner,
                f"rtng-{uuid.uuid4().hex[:10]}",
                "laptop",
                json.dumps(capabilities or {}),
            ),
        )
        return str(cur.fetchone()["id"])


CPU_SMALL = {"cpu_cores": 4}  # < CPU_LARGE_MIN_CORES (8): lands in cpu-small


@pytest.fixture
def _withdraw_after(db):
    """``(listing_id, owner_id)`` pairs to withdraw once the test is done.

    ``cpu-small`` is the one class an unpriced-resources job always routes
    to, so it is NOT this file's own exclusive book the way
    ``tests/test_routing.py``'s ``gpu-80gb-hopper`` is — ``test_market_routes.py``
    lists there too and asserts an exact ``best_ask_zc``, assuming nothing
    else has undercut it. The ephemeral Postgres is session-scoped
    (``conftest.py``) and never rolled back between tests, so a listing this
    file creates and forgets to remove would still be open (or, worse, still
    the LAST RECORDED price observation) when that assertion runs, whichever
    file happens to run second.

    This calls ``marketplace.withdraw_listing`` — not a raw SQL update —
    because withdrawing through it re-records a price observation
    (``set_listing_state`` -> ``_record_observation_locked``) reflecting the
    book with this listing gone. A raw ``update ... set state = 'withdrawn'``
    moves the row but leaves the LAST observed price frozen at this test's
    ask forever, which is a subtler version of the exact pollution this
    fixture exists to prevent — `class_board`'s `last_zc` (what `/prices`
    reports as `best_ask_zc`) is read from that observation, not from a live
    scan of open listings.
    """
    created: list[tuple[str, str]] = []
    yield created
    for listing_id, owner_id in created:
        marketmod.withdraw_listing(db, listing_id=listing_id, owner_id=owner_id)


# ---------------------------------------------------------------------------
# 1. a priced job that clears the book
# ---------------------------------------------------------------------------


def test_a_priced_pool_job_creates_a_bid_and_matches(make_client, db, _withdraw_after):
    client = make_client(PRICED_REPO)
    host = _new_user(db)
    machine = _machine(db, host, capabilities=CPU_SMALL)
    listing = marketmod.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=100,
    )
    _withdraw_after.append((str(listing["id"]), host))

    alice = _new_user(db)
    r = _post(client, _jwt(alice))
    assert r.status_code == 201, r.text
    body = r.json()
    job_id = body["job_id"]

    routing_block = body["routing"]
    assert routing_block["state"] == "routed"
    assert routing_block["capability_class"] == "cpu-small"
    assert routing_block["tasks_wanted"] == 1
    assert routing_block["tasks_filled"] == 1
    assert routing_block["tasks_unfilled"] == 0

    bids = _bids_for_job(db, job_id)
    assert len(bids) == 1
    bid = bids[0]
    assert bid["state"] in ("partial", "filled")
    assert bid["est_task_seconds"] > 0
    assert routing_block["bid_id"] == str(bid["id"])

    matches = _matches_for_bid(db, bid["id"])
    filled_book_rows = [
        row for row in routing_block["book"] if row["tasks_assigned"] > 0
    ]
    assert len(matches) == len(filled_book_rows)
    assert {str(m["listing_id"]) for m in matches} == {
        row["listing_id"] for row in filled_book_rows
    }
    assert sum(m["tasks_assigned"] for m in matches) == routing_block["tasks_filled"]
    # Our own listing — cheap ask, generous cap — is among the filled rows.
    assert str(listing["id"]) in {str(m["listing_id"]) for m in matches}


# ---------------------------------------------------------------------------
# 2. no price, no routing — byte-identical to today
# ---------------------------------------------------------------------------


def test_a_job_without_price_routes_nothing(make_client, db):
    client = make_client(CLEAN_REPO)
    alice = _new_user(db)

    r = _post(client, _jwt(alice))
    assert r.status_code == 201, r.text
    body = r.json()
    job_id = body["job_id"]

    assert "routing" not in body
    assert _bids_for_job(db, job_id) == []


# ---------------------------------------------------------------------------
# 3. routing fails open
# ---------------------------------------------------------------------------


def test_routing_failure_fails_open(make_client, db, monkeypatch):
    client = make_client(PRICED_REPO)
    alice = _new_user(db)

    def _boom(*args, **kwargs):
        raise RuntimeError("plan_pool_routing exploded")

    monkeypatch.setattr(routingmod, "plan_pool_routing", _boom)

    r = _post(client, _jwt(alice))
    assert r.status_code == 201, r.text
    body = r.json()
    job_id = body["job_id"]

    assert body["routing"] == {"state": "skipped", "reason": "routing-error"}
    assert _bids_for_job(db, job_id) == []
    # The job itself still exists — routing failing open must not have
    # rolled back the job row written moments before it ran.
    assert _job_rows(db, alice) != []


# ---------------------------------------------------------------------------
# 4. a priced GPU job is refused before the coordinator is ever asked
# ---------------------------------------------------------------------------


def test_a_gpu_priced_job_is_refused_before_the_coordinator(make_client, db, transport):
    client = make_client(GPU_PRICED_REPO)
    alice = _new_user(db)

    r = _post(client, _jwt(alice))

    assert r.status_code == 400, r.text
    assert "gpuPerTask" in r.json()["detail"]
    assert _job_rows(db, alice) == []
    assert transport.job_submissions == []
