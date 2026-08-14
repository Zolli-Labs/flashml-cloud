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
* a priced GPU job ROUTES, against the GPU books, in reference-price order.
  This file used to pin the opposite — a 400 naming ``gpuPerTask`` — because
  the pinned runtime dropped ``ResourcesSpec.gpuPerTask`` and a GPU bid would
  have been priced for hardware the coordinator could not reserve. That gap
  is closed in the pin this suite resolves (``gpuPerTask`` is declared and
  the command recipe stamps ``payload["gpus"]``), so the refusal went with
  it. A malformed ``resources`` value is still a validation 400;
* a priced FEDERATED job submits exactly as it does without Task 4 (no
  refusal, no bid — federated rounds are a named follow-up, not routed
  today) but the plan's explainability rule still holds: the response
  names the gap (``routing: {"state": "skipped", "reason":
  "federated-unsupported"}``) rather than silently dropping the price
  block on the floor.

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
from typing import Any, NamedTuple

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod
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
#: A cap low enough that only THIS file's own seeded listings clear it. The
#: session-scoped Postgres carries every earlier file's open listings
#: (`test_market_routes.py` leaves cpu-small asks at 900 and 1200,
#: `test_marketplace_class_board.py` leaves cpu-large asks at 1000 and 2000),
#: and a spill test that those could fill is not testing the spill.
CHEAP_PRICE_BLOCK = "    price:\n      max_per_hour: 0.20\n"
#: `price.objective` pinned to the engine's own default.
#:
#: The yaml default is `balanced` (flashml_yaml.DEFAULT_PRICE_OBJECTIVE), and
#: most of this file exercises it. The two inspection tests below compare the
#: submit response's book against `GET /jobs/{id}/routing`'s recomputed one
#: BYTE FOR BYTE, and that route has no objective to recompute with — bids do
#: not store one (v1) — so it re-ranks `cheapest`. Pinning the submit side to
#: the same objective is what makes the two comparable; leaving it on
#: `balanced` would compare two different questions and call the difference a
#: bug the first time a machine in the cpu books has a measured median.
CHEAPEST_PRICE_BLOCK = (
    "    price:\n      max_per_hour: 5.00\n      objective: cheapest\n"
)
#: Two tasks, so a one-task class has a remainder to spill.
SWEEP_BLOCK = "    sweep:\n      lr:\n        - 0.1\n        - 0.2\n"
GPU_BLOCK = "    resources:\n      gpus: 1\n"
#: I1, final review: a non-numeric `gpus` must 400 (`routing.UnroutableResources`),
#: never reach `int()`/`float()` unguarded and 500.
MALFORMED_RESOURCES_BLOCK = "    resources:\n      gpus: one\n"

CLEAN_YAML = BASE_YAML
PRICED_YAML = BASE_YAML + PRICE_BLOCK
CHEAPEST_PRICED_YAML = BASE_YAML + CHEAPEST_PRICE_BLOCK
GPU_PRICED_YAML = BASE_YAML + PRICE_BLOCK + GPU_BLOCK
SPILL_PRICED_YAML = BASE_YAML + CHEAP_PRICE_BLOCK + SWEEP_BLOCK
#: The spill repo, with the same objective pin and for the same reason.
CHEAPEST_SPILL_PRICED_YAML = (
    BASE_YAML
    + CHEAP_PRICE_BLOCK.rstrip("\n")
    + "\n      objective: cheapest\n"
    + SWEEP_BLOCK
)
FASTEST_PRICED_YAML = (
    BASE_YAML + PRICE_BLOCK.rstrip("\n") + "\n      objective: fastest\n"
)
MALFORMED_RESOURCES_PRICED_YAML = BASE_YAML + PRICE_BLOCK + MALFORMED_RESOURCES_BLOCK

CLEAN_REPO = {"flashml.yaml": CLEAN_YAML, "train.py": CLEAN_TRAIN_PY}
PRICED_REPO = {"flashml.yaml": PRICED_YAML, "train.py": CLEAN_TRAIN_PY}
CHEAPEST_PRICED_REPO = {
    "flashml.yaml": CHEAPEST_PRICED_YAML, "train.py": CLEAN_TRAIN_PY,
}
CHEAPEST_SPILL_PRICED_REPO = {
    "flashml.yaml": CHEAPEST_SPILL_PRICED_YAML, "train.py": CLEAN_TRAIN_PY,
}
FASTEST_PRICED_REPO = {
    "flashml.yaml": FASTEST_PRICED_YAML, "train.py": CLEAN_TRAIN_PY,
}
GPU_PRICED_REPO = {"flashml.yaml": GPU_PRICED_YAML, "train.py": CLEAN_TRAIN_PY}
SPILL_PRICED_REPO = {"flashml.yaml": SPILL_PRICED_YAML, "train.py": CLEAN_TRAIN_PY}
MALFORMED_RESOURCES_PRICED_REPO = {
    "flashml.yaml": MALFORMED_RESOURCES_PRICED_YAML, "train.py": CLEAN_TRAIN_PY,
}

# A federated config, priced. `mode: federated` requires `version: 2` and
# `epochs` (flashml_yaml._validate_mode); the price block is layered on top
# exactly as PRICED_YAML layers it onto BASE_YAML above.
FEDERATED_YAML = """
    version: 2
    name: routed-federated
    image: python-slim
    entrypoint: train.py
    mode: federated
    epochs: 1
"""

FEDERATED_PRICED_YAML = FEDERATED_YAML + PRICE_BLOCK

#: An entrypoint that speaks the delta protocol preflight requires for a
#: federated job (verbatim from tests/test_federated.py's FEDERATED_TRAIN_PY,
#: copied here so this file stays self-contained rather than importing
#: across test modules for one script). This is a STATIC scan — the
#: preflight "federated-contract" finding greps for the literal substrings
#: below (``/work/inputs/weights.json``, ``--shard``/``--num-shards``,
#: ``/work/out/delta.json``, ``chunks_done``), so trimming this down to only
#: the parts that "matter" at runtime (as an earlier draft of this fixture
#: did) is refused before submission ever reaches the routing hook this test
#: is about — CLEAN_TRAIN_PY does not satisfy it either.
FEDERATED_TRAIN_PY = """
    import argparse
    import json
    import os

    parser = argparse.ArgumentParser()
    parser.add_argument("--round", type=int, default=0)
    parser.add_argument("--shard", type=int, default=0)
    parser.add_argument("--num-shards", type=int, default=1)
    args = parser.parse_args()

    weights_path = "/work/inputs/weights.json"
    weights = None
    if os.path.exists(weights_path):
        with open(weights_path) as fh:
            weights = json.load(fh)

    delta = {"w": {"shape": [1], "data": [0.1]}}
    with open("/work/out/delta.json", "w") as fh:
        json.dump(delta, fh)
    with open("/work/out/metrics.json", "w") as fh:
        json.dump({"samples": 128, "loss": 0.5, "delta_file": "delta.json",
                   "chunks_done": [args.shard]}, fh)
"""

FEDERATED_PRICED_REPO = {
    "flashml.yaml": FEDERATED_PRICED_YAML, "train.py": FEDERATED_TRAIN_PY,
}


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


class _RecordingStarter:
    """Stands in for `start_federated_job` — records the run without
    launching a background driver thread. Same pattern
    tests/test_federated.py's own `RecordingStarter`/`federated_client`
    fixture uses for its from-repo submission tests: the driver's own
    behavior is out of scope here, only whether the submit route reports
    routing correctly for a federated + priced job."""

    def __init__(self) -> None:
        self.runs: list[Any] = []

    def __call__(self, run, **kwargs):
        self.runs.append(run)
        return None


@pytest.fixture
def federated_client(settings, postgres_dsn, transport):
    clients = []

    def build(files: dict[str, str] | None = None):
        fetch = RecordingFetch(make_tarball(files or FEDERATED_PRICED_REPO))

        def connect() -> psycopg.Connection:
            conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
            conn.autocommit = True
            return conn

        starter = _RecordingStarter()
        app = create_cloud_app(
            settings, connect=connect, transport=transport, fetch_repo=fetch,
            start_federated_job=starter,
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        client.starter = starter  # type: ignore[attr-defined]
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
CPU_LARGE = {"cpu_cores": 16}  # >= CPU_LARGE_MIN_CORES (8): lands in cpu-large
#: A GPU capabilities snapshot in the shape `marketplace.capability_class`
#: reads, copied from `tests/test_marketplace.py`'s own `RTX_3070`. 8GB is
#: deliberately the BOTTOM of the GPU ladder: it is the first class a derived
#: GPU accept walks (cheapest reference price first), so a job that fills
#: here never walks the other five, and this file leaves the rest of the GPU
#: books untouched.
RTX_3070 = {"index": 0, "name": "NVIDIA GeForce RTX 3070", "memory_total_mb": 8192,
            "compute_capability": "8.6"}


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


@pytest.fixture
def _forget_observations(db):
    """Class names whose price-observation rows this test may not leave behind.

    ``_withdraw_after`` (above) closes a listing and re-records the book, so
    the LIVE state it restores is correct. What it cannot undo is the
    ``price_observations`` history: a listing, a bid and a match each append
    a row, and `class_board`'s ``history`` is that table verbatim. Two other
    files assert a class's history is EXACTLY empty
    (``test_marketplace_class_board.py`` for ``cpu-large``,
    ``test_marketplace.py`` for ``gpu-48gb``), which a routing test bidding
    into a new class would break the moment anything reordered the run.

    So the classes a test reaches into for the FIRST time in this file name
    themselves here, and every row they appended is deleted on teardown.
    Precise, not a truncate: the high-water ``id`` is read at setup
    (``bigserial``, so strictly increasing) and only rows above it in the
    named classes go. Rows any earlier file wrote are untouched.

    Request this fixture BEFORE ``_withdraw_after`` in a test's parameter
    list. Same-scope fixtures tear down in reverse setup order, so being set
    up first means being torn down last — after the withdrawal has written
    its own observation row, which is one of the rows that must go.
    """
    with db.cursor() as cur:
        cur.execute(
            "select coalesce(max(id), 0) as high from public.price_observations"
        )
        high = int(cur.fetchone()["high"])
    classes: list[str] = []
    yield classes
    if classes:
        with db.cursor() as cur:
            cur.execute(
                "delete from public.price_observations"
                " where id > %s and capability_class = any(%s)",
                (high, classes),
            )


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
    # The derived accept for a job that named no resources: both CPU books,
    # smaller first. Only the first is WALKED here, because it fills.
    assert routing_block["accept"] == ["cpu-small", "cpu-large"]
    assert routing_block["tasks_wanted"] == 1
    assert routing_block["tasks_filled"] == 1
    assert routing_block["tasks_unfilled"] == 0

    bids = _bids_for_job(db, job_id)
    assert len(bids) == 1
    bid = bids[0]
    assert bid["state"] in ("partial", "filled")
    assert bid["est_task_seconds"] > 0
    assert bid["capability_class"] == "cpu-small"
    assert routing_block["bids"] == [
        {"capability_class": "cpu-small", "bid_id": str(bid["id"])}
    ]

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
# 1b. the objective travels from the yaml to the response, and the response
#     publishes the arithmetic it used.
# ---------------------------------------------------------------------------


def _routing_of(client, db, *, ask=100):
    """Submit through `client` against one seeded cpu-small listing and
    return `(routing_block, cleanup)`."""
    host = _new_user(db)
    machine = _machine(db, host, capabilities=CPU_SMALL)
    listing = marketmod.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=ask,
    )
    r = _post(client, _jwt(_new_user(db)))
    assert r.status_code == 201, r.text
    return r.json()["routing"], (str(listing["id"]), host)


def test_a_priced_job_that_names_no_objective_is_routed_balanced(
    make_client, db, _withdraw_after
):
    """The owner-approved default (2026-08-13) arrives all the way at the
    response, so a submitter who wrote only `max_per_hour` can still read
    which of the three orders their book was ranked in."""
    routing_block, cleanup = _routing_of(make_client(PRICED_REPO), db)
    _withdraw_after.append(cleanup)

    assert routing_block["state"] == "routed"
    assert routing_block["objective"] == "balanced"
    assert routing_block["formula"] == marketmod.OBJECTIVE_FORMULAS["balanced"]


def test_a_declared_objective_reaches_the_response_with_its_formula(
    make_client, db, _withdraw_after
):
    """`price.objective: fastest` in the yaml, `"fastest"` in the response,
    and the published formula is the engine's own constant rather than a
    string the response builder made up — the two cannot drift."""
    routing_block, cleanup = _routing_of(make_client(FASTEST_PRICED_REPO), db)
    _withdraw_after.append(cleanup)

    assert routing_block["objective"] == "fastest"
    assert routing_block["formula"] == marketmod.OBJECTIVE_FORMULAS["fastest"]
    assert "median_seconds" in routing_block["formula"]


def test_every_book_row_publishes_its_median_and_its_rank_score(
    make_client, db, _withdraw_after
):
    """Both fields on every row, whatever the objective and whether or not
    the machine has been measured. A field that disappears when it has
    nothing to say arrives at the console as `undefined`."""
    routing_block, cleanup = _routing_of(make_client(PRICED_REPO), db)
    _withdraw_after.append(cleanup)

    assert routing_block["book"]
    for row in routing_block["book"]:
        assert "median_seconds" in row
        assert "rank_score" in row
    ours = {row["listing_id"]: row for row in routing_block["book"]}[cleanup[0]]
    # A machine that has never resolved an attempt: unproven AND unmeasured,
    # so it ranks on its ask alone and its balanced factor is exactly 1.
    assert ours["acceptance_rate"] is None
    assert ours["median_seconds"] is None
    assert ours["rank_score"] == ours["effective_zc_per_hour"] == "100"


# ---------------------------------------------------------------------------
# C1 (final review): the book must respect pools/workspace.
#
# Part 1 — workspace exclusion applies to EVERY priced job, pooled or not:
# a machine bound to a pool the submitter belongs to is withheld from the
# bid entirely (`excluded: "workspace-free"`), never merely priced out of
# it, even when the job itself carries no `pool` scoping.
#
# Part 2 — a pool-scoped priced job creates NO bid at all: the coordinator's
# own pool gate already confines it to the pool's machines, which are free
# to every member, so a bid could only ever charge a pool-mate wrongly or
# sit open and inert against non-members who can never claim it. The explain
# still runs, read-only, so the submitter sees the same ranked book (with
# the pool's members labelled `workspace-free`) they would have gotten from
# a real bid.
# ---------------------------------------------------------------------------


def _add_pool_member(db, pool_id: str, user_id: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pool_members (pool_id, user_id)"
            " values (%s::uuid, %s::uuid)",
            (pool_id, user_id),
        )


def test_a_pool_scoped_priced_job_creates_no_bid(make_client, db, _withdraw_after):
    client = make_client(PRICED_REPO)
    alice = _new_user(db)
    pool = dbmod.create_pool(db, name="alices-team", owner_id=alice)
    pool_id = str(pool["id"])

    host = _new_user(db)
    _add_pool_member(db, pool_id, host)
    machine = _machine(db, host, capabilities=CPU_SMALL)
    listing = marketmod.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=100,
    )
    _withdraw_after.append((str(listing["id"]), host))
    dbmod.bind_machine_pool(db, machine_id=machine, pool_id=pool_id)

    r = _post(client, _jwt(alice), pool=pool_id)
    assert r.status_code == 201, r.text
    body = r.json()
    job_id = body["job_id"]

    routing_block = body["routing"]
    assert routing_block["state"] == "skipped"
    assert routing_block["reason"] == "pool-capacity-is-free"
    assert routing_block["accept"] == ["cpu-small", "cpu-large"]
    assert routing_block["tasks_wanted"] == 1

    book_by_machine = {row["machine_id"]: row for row in routing_block["book"]}
    assert book_by_machine[machine]["excluded"] == "workspace-free"

    # The whole point: no bid, ever, for a pool-scoped priced job.
    assert _bids_for_job(db, job_id) == []
    # The job itself still exists — this is a routing skip, not a refusal.
    assert _job_rows(db, alice) != []


def test_workspace_machines_are_excluded_from_an_unpooled_priced_bid(
    make_client, db, _withdraw_after
):
    client = make_client(PRICED_REPO)
    alice = _new_user(db)
    pool = dbmod.create_pool(db, name="alices-team", owner_id=alice)
    pool_id = str(pool["id"])

    teammate = _new_user(db)
    _add_pool_member(db, pool_id, teammate)
    # Cheaper ask than the stranger below — if workspace exclusion did not
    # apply, this listing would rank FIRST and be the one matched.
    workspace_machine = _machine(db, teammate, capabilities=CPU_SMALL)
    workspace_listing = marketmod.create_listing(
        db, machine_id=workspace_machine, owner_id=teammate, ask_zc_per_hour=50,
    )
    _withdraw_after.append((str(workspace_listing["id"]), teammate))
    dbmod.bind_machine_pool(db, machine_id=workspace_machine, pool_id=pool_id)

    stranger = _new_user(db)
    market_machine = _machine(db, stranger, capabilities=CPU_SMALL)
    market_listing = marketmod.create_listing(
        db, machine_id=market_machine, owner_id=stranger, ask_zc_per_hour=100,
    )
    _withdraw_after.append((str(market_listing["id"]), stranger))

    # Unpooled submit — no `pool=` on the request at all.
    r = _post(client, _jwt(alice))
    assert r.status_code == 201, r.text
    body = r.json()
    job_id = body["job_id"]
    routing_block = body["routing"]
    assert routing_block["state"] == "routed"

    book_by_listing = {row["listing_id"]: row for row in routing_block["book"]}
    ws_row = book_by_listing[str(workspace_listing["id"])]
    assert ws_row["excluded"] == "workspace-free"
    assert ws_row["tasks_assigned"] == 0

    market_row = book_by_listing[str(market_listing["id"])]
    assert market_row["excluded"] is None
    assert market_row["tasks_assigned"] == 1

    bids = _bids_for_job(db, job_id)
    assert len(bids) == 1
    matches = _matches_for_bid(db, bids[0]["id"])
    assert len(matches) == 1
    assert str(matches[0]["listing_id"]) == str(market_listing["id"])


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


def test_a_grant_matches_failure_leaves_no_orphaned_bid(
    make_client, db, monkeypatch, _withdraw_after
):
    """The atomicity proof: `test_routing_failure_fails_open` (above) patches
    `routing.plan_pool_routing`, which fails BEFORE `create_bid` ever runs,
    so it says nothing about whether a bid that WAS written gets cleaned up.
    This monkeypatches
    the failure to land right after `create_bid` would have committed —
    seeding an open, cheap listing first so the plan actually has fills and
    `route_submitted_job` actually reaches `grant_matches` — and pins that
    under autocommit (this deployment's mode) a caller's `db.rollback()`
    cannot undo an already-committed `create_bid`, so the only thing that
    can keep the response's "skipped" honest is `route_submitted_job`
    wrapping both writes in one `db.transaction()` itself."""
    client = make_client(PRICED_REPO)
    host = _new_user(db)
    machine = _machine(db, host, capabilities=CPU_SMALL)
    listing = marketmod.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=100,
    )
    _withdraw_after.append((str(listing["id"]), host))

    def _boom(*args, **kwargs):
        raise RuntimeError("grant_matches exploded")

    monkeypatch.setattr(marketmod, "grant_matches", _boom)

    alice = _new_user(db)
    r = _post(client, _jwt(alice))
    assert r.status_code == 201, r.text
    body = r.json()
    job_id = body["job_id"]

    assert body["routing"] == {"state": "skipped", "reason": "routing-error"}
    # The atomicity proof: create_bid ran (the plan had a fill to grant),
    # and would have committed a durable, open bid under autocommit if
    # route_submitted_job did not wrap it with grant_matches in one
    # transaction. Zero rows, not one orphaned "open" row the response
    # claims does not exist.
    assert _bids_for_job(db, job_id) == []
    assert _job_rows(db, alice) != []


# ---------------------------------------------------------------------------
# 4. a priced GPU job routes against a GPU book
# ---------------------------------------------------------------------------


def test_a_gpu_priced_job_routes_against_a_gpu_book(
    make_client, db, transport, _forget_observations, _withdraw_after
):
    """The inverse of what this file used to assert. The refusal named a
    runtime gap — the pin dropped `ResourcesSpec.gpuPerTask`, so a GPU bid
    would have been priced for capacity nobody could reserve — and that gap
    is closed in the pin this venv resolves. So a `gpus: 1` job now reaches
    the coordinator (a real job row, a real submission) and bids in the GPU
    books, cheapest reference class first.

    The seeded machine is a `gpu-8gb` one because that is the FIRST class a
    derived GPU accept walks: it fills there, the walk stops, and the other
    five GPU books are neither read nor bid in."""
    _forget_observations.append("gpu-8gb")
    client = make_client(GPU_PRICED_REPO)
    host = _new_user(db)
    machine = _machine(db, host, capabilities={"gpus": [RTX_3070]})
    listing = marketmod.create_listing(
        db, machine_id=machine, owner_id=host, ask_zc_per_hour=100,
    )
    assert listing["capability_class"] == "gpu-8gb"
    _withdraw_after.append((str(listing["id"]), host))

    alice = _new_user(db)
    r = _post(client, _jwt(alice))

    assert r.status_code == 201, r.text
    body = r.json()
    job_id = body["job_id"]
    # A real submission reached the coordinator, carrying the GPU count.
    assert len(transport.job_submissions) == 1
    assert transport.submitted[-1]["spec"]["resources"]["gpuPerTask"] == 1
    assert _job_rows(db, alice) != []

    routing_block = body["routing"]
    assert routing_block["state"] == "routed"
    assert routing_block["accept"][0] == "gpu-8gb"
    assert all(k.startswith("gpu-") for k in routing_block["accept"])
    assert routing_block["tasks_filled"] == 1
    assert routing_block["tasks_unfilled"] == 0

    # One bid, in the one class the walk needed.
    bids = _bids_for_job(db, job_id)
    assert len(bids) == 1
    assert bids[0]["capability_class"] == "gpu-8gb"
    assert routing_block["bids"] == [
        {"capability_class": "gpu-8gb", "bid_id": str(bids[0]["id"])}
    ]

    matches = _matches_for_bid(db, bids[0]["id"])
    assert [str(m["listing_id"]) for m in matches] == [str(listing["id"])]


# ---------------------------------------------------------------------------
# 4b. the remainder spills into the next accepted class
# ---------------------------------------------------------------------------


def test_a_cpu_small_job_spills_into_the_cpu_large_book(
    make_client, db, _forget_observations, _withdraw_after
):
    """Two tasks, a one-task `cpu-small` listing and a roomy `cpu-large` one:
    the job bids in BOTH classes, and the second bid wants only what the
    first could not fill.

    Two mechanisms point the same way here and the test does not have to
    choose between them: the `cpu-small` listing has capacity for one task,
    and both machines are unproven, so `unproven_task_budget(2) == 1` caps
    that class at one task regardless. Either way the walk must ask the
    second class for exactly one task, and that is what is asserted."""
    _forget_observations.append("cpu-large")
    client = make_client(SPILL_PRICED_REPO)
    host = _new_user(db)

    small_machine = _machine(db, host, capabilities=CPU_SMALL)
    small = marketmod.create_listing(
        db, machine_id=small_machine, owner_id=host, ask_zc_per_hour=50,
        max_concurrent_tasks=1,
    )
    _withdraw_after.append((str(small["id"]), host))

    large_machine = _machine(db, host, capabilities=CPU_LARGE)
    large = marketmod.create_listing(
        db, machine_id=large_machine, owner_id=host, ask_zc_per_hour=100,
        max_concurrent_tasks=4,
    )
    assert large["capability_class"] == "cpu-large"
    _withdraw_after.append((str(large["id"]), host))

    alice = _new_user(db)
    r = _post(client, _jwt(alice))
    assert r.status_code == 201, r.text
    body = r.json()
    job_id = body["job_id"]

    routing_block = body["routing"]
    assert routing_block["state"] == "routed"
    assert routing_block["accept"] == ["cpu-small", "cpu-large"]
    assert routing_block["tasks_wanted"] == 2
    assert routing_block["tasks_filled"] == 2
    assert routing_block["tasks_unfilled"] == 0

    rows = {row["listing_id"]: row for row in routing_block["book"]}
    assert rows[str(small["id"])]["tasks_assigned"] == 1
    assert rows[str(small["id"])]["capability_class"] == "cpu-small"
    assert rows[str(large["id"])]["tasks_assigned"] == 1
    assert rows[str(large["id"])]["capability_class"] == "cpu-large"

    # One bid per WALKED class, in walk order, and the second wants only the
    # remainder — a second bid for the full two tasks would entitle four
    # machines to a two-task job.
    bids = sorted(_bids_for_job(db, job_id), key=lambda b: b["created_at"])
    assert [b["capability_class"] for b in bids] == ["cpu-small", "cpu-large"]
    assert [b["tasks_wanted"] for b in bids] == [2, 1]
    assert routing_block["bids"] == [
        {"capability_class": b["capability_class"], "bid_id": str(b["id"])}
        for b in bids
    ]

    for bid, expected in zip(bids, (str(small["id"]), str(large["id"]))):
        matches = _matches_for_bid(db, bid["id"])
        assert [str(m["listing_id"]) for m in matches] == [expected]


def test_a_priced_job_with_non_numeric_gpus_400s_not_500s(make_client, db, transport):
    """I1, final review: `job_accept_classes` coerces `resources.gpus` with
    `int()`; a non-numeric value used to escape as a raw, unhandled
    `ValueError` — a 500 with no job row visible to anyone but the log.
    `routing.UnroutableResources` names it, and the validation-time except
    clause catches its base `routing.RoutingValidationError` before the
    coordinator is ever asked. This is the refusal that SURVIVED the GPU
    one: a malformed count is the submitter's typo, where `gpus: 1` is now
    a perfectly routable request."""
    client = make_client(MALFORMED_RESOURCES_PRICED_REPO)
    alice = _new_user(db)

    r = _post(client, _jwt(alice))

    assert r.status_code == 400, r.text
    assert "gpus" in r.json()["detail"]
    assert _job_rows(db, alice) == []
    assert transport.job_submissions == []


# ---------------------------------------------------------------------------
# 5. a priced FEDERATED job submits unchanged, but says so
# ---------------------------------------------------------------------------
#
# Ruling (coordinator, post-review): federated + price is not a validation
# refusal — the run above already started by the time routing.py could ever
# see it — and it is not silence either. The plan's explainability
# constraint (every routing decision visible in the response) means a
# submitter who priced a federated job must be told routing did not apply,
# in the same shape a routing-error skip already uses, rather than the price
# block being dropped on the floor with no trace.


def test_a_federated_priced_job_reports_skipped_not_routed(federated_client, db):
    client = federated_client(FEDERATED_PRICED_REPO)
    alice = _new_user(db)

    r = _post(client, _jwt(alice))
    assert r.status_code == 201, r.text
    body = r.json()

    assert body["mode"] == "federated"
    assert body["routing"] == {
        "state": "skipped", "reason": "federated-unsupported",
    }
    assert _bids_for_job(db, body["job_id"]) == []
    # The run itself is untouched by the routing gap: it still started,
    # exactly as it would have before this task existed.
    assert len(client.starter.runs) == 1
    assert client.starter.runs[0].job_id == body["job_id"]


# ---------------------------------------------------------------------------
# Task 5: GET /v1alpha1/jobs/{job_id}/routing — the routing inspection route
# ---------------------------------------------------------------------------
#
# The route is read-only: it never plans a bid into existence, it re-explains
# one that submission already created (or reports there is none). Three
# invariants:
#
# * a routed job returns EVERY bid it posted (one per walked class, in
#   creation order) with that bid's granted matches, and a freshly
#   recomputed "live book" that agrees with the "routing" block the submit
#   response itself returned moments earlier — the listing set has not
#   moved between the two calls, so the recomputation must not either;
# * an unrouted job (no price block, so no bid was ever created) answers
#   ``{"bids": [], "live_book": None}``, not a 404 — the job exists and is
#   visible, it simply never entered the book;
# * a job that exists but belongs to somebody else 404s, with the exact
#   same body an unknown job id gets — the not-found doctrine every other
#   job GET route in this file already follows.


def _get_routing(client, token: str, job_id: str):
    return client.get(
        f"/v1alpha1/jobs/{job_id}/routing",
        headers={"Authorization": f"Bearer {token}"},
    )


def test_routing_inspection_for_a_routed_job(make_client, db, _withdraw_after):
    client = make_client(CHEAPEST_PRICED_REPO)
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

    bids = _bids_for_job(db, job_id)
    assert len(bids) == 1
    bid_row = bids[0]
    matches = _matches_for_bid(db, bid_row["id"])
    assert matches, "the fixture listing is cheap enough to have filled"

    resp = _get_routing(client, _jwt(alice), job_id)
    assert resp.status_code == 200, resp.text
    out = resp.json()

    # bids: the same rows a fresh DB query finds, JSON-rendered, each
    # carrying its own matches.
    assert len(out["bids"]) == 1
    got = out["bids"][0]
    assert got["id"] == str(bid_row["id"])
    assert got["job_id"] == job_id
    assert got["owner_id"] == alice
    assert got["capability_class"] == "cpu-small"
    assert got["tasks_wanted"] == routing_block["tasks_wanted"]
    assert got["state"] == bid_row["state"]

    # matches: every granted entitlement for that bid, nothing else.
    assert len(got["matches"]) == len(matches)
    assert {m["id"] for m in got["matches"]} == {str(m["id"]) for m in matches}
    assert {m["listing_id"] for m in got["matches"]} == {
        str(m["listing_id"]) for m in matches
    }

    # live_book: recomputed against a book that has not moved since submit,
    # so it must agree with the "routing" block the submit response
    # returned — same shape as the plan (minus "class_plans", which carries
    # MatchPlan dataclasses and is not JSON-safe), not a subset or an
    # approximation of it.
    live_book = out["live_book"]
    assert live_book is not None
    assert "class_plans" not in live_book
    assert set(live_book.keys()) == {
        "accept", "objective", "formula", "tasks_wanted", "tasks_filled",
        "tasks_unfilled", "book", "nearest_miss",
    }
    # The live re-explain has no stored objective to recompute with — a bid
    # records its class, cap and task count, not what the submitter asked the
    # book to be ranked by — so it says `cheapest` and means it. This job
    # pinned `objective: cheapest` in its yaml precisely so the two sides
    # are the same question; see CHEAPEST_PRICE_BLOCK.
    assert live_book["objective"] == "cheapest"
    assert live_book["objective"] == routing_block["objective"]
    assert live_book["formula"] == routing_block["formula"]
    # Re-derived from the bids that exist, so it names the classes actually
    # WALKED — not the full accept list the submit response reported.
    assert live_book["accept"] == ["cpu-small"]
    assert live_book["tasks_wanted"] == routing_block["tasks_wanted"]
    assert live_book["tasks_filled"] == routing_block["tasks_filled"]
    assert live_book["tasks_unfilled"] == routing_block["tasks_unfilled"]
    assert live_book["nearest_miss"] == routing_block["nearest_miss"]
    assert live_book["book"] == routing_block["book"]


def test_routing_inspection_returns_every_bid_a_spilled_job_posted(
    make_client, db, _forget_observations, _withdraw_after
):
    """A job that spilled across two classes has TWO bids, and the route
    returns both in creation order with their own matches. Returning only
    the newest (the pre-spill behaviour) would hide the class that actually
    filled most of the job."""
    _forget_observations.append("cpu-large")
    client = make_client(CHEAPEST_SPILL_PRICED_REPO)
    host = _new_user(db)

    small_machine = _machine(db, host, capabilities=CPU_SMALL)
    small = marketmod.create_listing(
        db, machine_id=small_machine, owner_id=host, ask_zc_per_hour=50,
        max_concurrent_tasks=1,
    )
    _withdraw_after.append((str(small["id"]), host))
    large_machine = _machine(db, host, capabilities=CPU_LARGE)
    large = marketmod.create_listing(
        db, machine_id=large_machine, owner_id=host, ask_zc_per_hour=100,
        max_concurrent_tasks=4,
    )
    _withdraw_after.append((str(large["id"]), host))

    alice = _new_user(db)
    r = _post(client, _jwt(alice))
    assert r.status_code == 201, r.text
    body = r.json()
    job_id = body["job_id"]
    assert body["routing"]["state"] == "routed"

    resp = _get_routing(client, _jwt(alice), job_id)
    assert resp.status_code == 200, resp.text
    out = resp.json()

    assert [b["capability_class"] for b in out["bids"]] == ["cpu-small", "cpu-large"]
    assert [b["tasks_wanted"] for b in out["bids"]] == [2, 1]
    assert [
        [m["listing_id"] for m in b["matches"]] for b in out["bids"]
    ] == [[str(small["id"])], [str(large["id"])]]

    # The live book walks the bids' own classes, in the order they were
    # created — the same walk, re-run against the book as it stands now.
    assert out["live_book"]["accept"] == ["cpu-small", "cpu-large"]
    assert out["live_book"]["book"] == body["routing"]["book"]


def test_routing_inspection_for_an_unrouted_job(make_client, db):
    client = make_client(CLEAN_REPO)
    alice = _new_user(db)

    r = _post(client, _jwt(alice))
    assert r.status_code == 201, r.text
    job_id = r.json()["job_id"]
    assert "routing" not in r.json()
    assert _bids_for_job(db, job_id) == []

    resp = _get_routing(client, _jwt(alice), job_id)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"bids": [], "live_book": None}


def test_routing_inspection_for_someone_elses_job_404s(make_client, db):
    client = make_client(CLEAN_REPO)
    alice = _new_user(db)
    r = _post(client, _jwt(alice))
    assert r.status_code == 201, r.text
    job_id = r.json()["job_id"]

    mallory = _new_user(db)
    not_yours = _get_routing(client, _jwt(mallory), job_id)
    assert not_yours.status_code == 404
    assert not_yours.json() == {"detail": "unknown job"}

    # Not-yours and does-not-exist must be indistinguishable — the same
    # doctrine every sibling job GET route in this API follows.
    unknown = _get_routing(client, _jwt(mallory), "no-such-job-at-all")
    assert unknown.status_code == 404
    assert unknown.json() == not_yours.json()
