"""Job ownership: the last residual the Task 5 report flagged.

``POST /v1alpha1/jobs`` requires a Supabase JWT and writes a ``jobs`` row
owned by the verified ``sub`` — never by anything the request body claims.
Every subsequent read, cancel, or artifact fetch for that job consults this
table *before* ever forwarding to the coordinator (which has no notion of
accounts and answers every job route unscoped behind the operator token),
and refuses with 404 — never 403, which would confirm the id exists — for
a job that either does not exist or belongs to someone else.

The coordinator here is a small in-memory fake (``FakeCoordinatorTransport``),
not a live server: what matters is the exact requests this API sends (or,
for the ownership tests, conspicuously does *not* send), and a live
coordinator would only let us observe its reaction to them. The database is
the real, freshly migrated ephemeral Postgres from ``conftest.py``.

No skips in this file, deliberately: a security test that skips reads as
green in CI and is worse than no test at all.
"""
from __future__ import annotations

import json
import time
import uuid

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import enrolment
from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.settings import Settings

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"
OPERATOR_TOKEN = "op-secret-do-not-leak-3f9c1b"
COORDINATOR_URL = "http://coordinator.internal:8100"


# ---------------------------------------------------------------------------
# the fake coordinator
# ---------------------------------------------------------------------------


class FakeCoordinatorTransport(httpx.AsyncBaseTransport):
    """A stand-in coordinator for the job routes: stores submitted jobs and
    served artifacts in memory, and remembers every request it received so
    a test can assert the coordinator was (or — for the ownership tests,
    critically — was NOT) contacted at all.

    Job listing and job-by-id are deliberately unscoped, exactly like the
    real coordinator: it has no accounts, so any scoping visible to a
    caller of this API must come from the API's own database, not from
    this fake pretending to enforce something the real coordinator does
    not.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._jobs: dict[str, dict] = {}
        # A fresh per-instance prefix, not just a counter reset to 1: the
        # ``jobs`` table is a real Postgres table shared by the whole test
        # session, so two tests both minting "job-0001" would collide on
        # the primary key even though each test's transport is otherwise
        # isolated.
        self._prefix = uuid.uuid4().hex[:10]
        self._next_id = 1
        self.artifacts: dict[str, bytes] = {}

    def seed_artifact(self, key: str, content: bytes) -> None:
        self.artifacts[key] = content

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        method, path = request.method, request.url.path

        if method == "POST" and path == "/v1alpha1/jobs":
            body = json.loads(request.content or b"{}")
            job_id = f"job-{self._prefix}-{self._next_id:04d}"
            self._next_id += 1
            record = {
                "job_id": job_id,
                "spec": body,
                "state": "RUNNING",
                "backend": "leases",
            }
            self._jobs[job_id] = record
            return httpx.Response(201, json=record)

        if method == "GET" and path == "/v1alpha1/jobs":
            return httpx.Response(200, json=list(self._jobs.values()))

        if method == "GET" and path.startswith("/v1alpha1/jobs/") and path.count("/") == 3:
            job_id = path.rsplit("/", 1)[-1]
            record = self._jobs.get(job_id)
            if record is None:
                return httpx.Response(404, json={"detail": "no such job"})
            return httpx.Response(200, json=record)

        if method == "POST" and path.endswith("/cancel"):
            job_id = path.split("/")[-2]
            record = self._jobs.get(job_id)
            if record is None:
                return httpx.Response(404, json={"detail": "no such job"})
            record = dict(record, state="CANCELLED")
            self._jobs[job_id] = record
            return httpx.Response(200, json=record)

        if method == "GET" and path.startswith("/v1alpha1/artifacts/"):
            key = path[len("/v1alpha1/artifacts/"):]
            content = self.artifacts.get(key)
            if content is None:
                return httpx.Response(404, json={"detail": "no such artifact"})
            return httpx.Response(
                200, content=content,
                headers={"content-type": "application/octet-stream"},
            )

        return httpx.Response(
            404, json={"detail": f"unhandled fake coordinator route: {method} {path}"}
        )

    @property
    def last(self) -> httpx.Request:
        assert self.requests, "nothing was forwarded to the coordinator"
        return self.requests[-1]


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
def client(settings, postgres_dsn, transport):
    def connect() -> psycopg.Connection:
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        return conn

    app = create_cloud_app(settings, connect=connect, transport=transport)
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


def _new_user(db) -> str:
    """A real ``auth.users`` + ``public.profiles`` pair, admitted at
    creation — every test in this file submits jobs through the
    invite-gated ``POST /v1alpha1/jobs`` route (Task 10), and this fixture
    is meant to model an ordinary, already-onboarded account, not the
    admission gate itself."""
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, now())",
            (user_id,),
        )
    return user_id


def _browser_jwt(user_id: str, **over) -> str:
    claims = {"sub": user_id, "aud": "authenticated", "exp": time.time() + 3600}
    claims.update(over)
    return jwt.encode(claims, JWT_SECRET, algorithm="HS256")


def _enrol(db, owner_id: str, node_id: str) -> tuple[str, str]:
    started = enrolment.start_device_code(db, node_id, "host-" + node_id, "linux")
    machine_id = enrolment.approve_device_code(db, started["user_code"], owner_id)
    token = enrolment.redeem_device_code(db, started["device_code"])
    assert token is not None
    return str(machine_id), token


def _submit(client, jwt_token: str, name: str) -> dict:
    r = client.post(
        "/v1alpha1/jobs",
        json={"apiVersion": "flashml.dev/v1alpha1", "kind": "Job",
              "metadata": {"name": name}, "spec": {}},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# 1. submission requires a JWT, and owner_id is never taken from the body
# ---------------------------------------------------------------------------


def test_submit_without_a_jwt_is_401(client, transport):
    r = client.post("/v1alpha1/jobs", json={"metadata": {"name": "no-auth"}})
    assert r.status_code == 401
    assert transport.requests == []


def test_a_machine_token_cannot_submit_a_job(client, db, transport):
    owner = _new_user(db)
    _mid, token = _enrol(db, owner, f"test-jobs-{uuid.uuid4().hex[:10]}")
    r = client.post("/v1alpha1/jobs", json={"metadata": {"name": "x"}},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert transport.requests == []


def test_owner_id_is_the_jwts_sub_even_when_the_body_claims_another(client, db):
    alice = _new_user(db)
    mallory = _new_user(db)
    r = client.post(
        "/v1alpha1/jobs",
        json={"metadata": {"name": "spoofed-owner"}, "owner_id": mallory,
              "owner": mallory},
        headers={"Authorization": f"Bearer {_browser_jwt(alice)}"},
    )
    assert r.status_code == 201
    job_id = r.json()["job_id"]

    with db.cursor() as cur:
        cur.execute("select owner_id from public.jobs where id = %s", (job_id,))
        row = cur.fetchone()
    assert row is not None
    assert str(row["owner_id"]) == alice
    assert str(row["owner_id"]) != mallory


# ---------------------------------------------------------------------------
# 2. list scoping
# ---------------------------------------------------------------------------


def test_list_jobs_never_includes_another_users_jobs(client, db):
    alice = _new_user(db)
    bob = _new_user(db)
    alice_jwt, bob_jwt = _browser_jwt(alice), _browser_jwt(bob)

    alice_job = _submit(client, alice_jwt, "alice-only-job")
    bob_job = _submit(client, bob_jwt, "bobs-private-job")

    r = client.get("/v1alpha1/jobs", headers={"Authorization": f"Bearer {alice_jwt}"})
    assert r.status_code == 200
    ids = [j["job_id"] for j in r.json()]
    assert alice_job["job_id"] in ids
    assert bob_job["job_id"] not in ids
    assert "bobs-private-job" not in r.text


def test_list_jobs_without_a_jwt_is_401(client):
    assert client.get("/v1alpha1/jobs").status_code == 401


# ---------------------------------------------------------------------------
# 3. get-by-id: 404, not 403, and indistinguishable from nonexistent
# ---------------------------------------------------------------------------


def test_getting_another_users_job_is_404(client, db):
    alice = _new_user(db)
    bob = _new_user(db)
    alice_job = _submit(client, _browser_jwt(alice), "alices-secret-job")

    r = client.get(f"/v1alpha1/jobs/{alice_job['job_id']}",
                   headers={"Authorization": f"Bearer {_browser_jwt(bob)}"})
    assert r.status_code == 404
    assert "alices-secret-job" not in r.text


def test_another_users_job_and_a_nonexistent_id_answer_identically(client, db):
    alice = _new_user(db)
    bob = _new_user(db)
    alice_job = _submit(client, _browser_jwt(alice), "job-that-exists")
    bob_jwt = _browser_jwt(bob)

    got_others = client.get(f"/v1alpha1/jobs/{alice_job['job_id']}",
                            headers={"Authorization": f"Bearer {bob_jwt}"})
    got_fake = client.get("/v1alpha1/jobs/totally-made-up-job-id",
                          headers={"Authorization": f"Bearer {bob_jwt}"})
    assert got_others.status_code == 404 == got_fake.status_code
    assert got_others.json() == got_fake.json()


def test_owner_can_get_their_own_job(client, db):
    alice = _new_user(db)
    alice_jwt = _browser_jwt(alice)
    job = _submit(client, alice_jwt, "alices-own-job")

    r = client.get(f"/v1alpha1/jobs/{job['job_id']}",
                   headers={"Authorization": f"Bearer {alice_jwt}"})
    assert r.status_code == 200
    assert r.json()["job_id"] == job["job_id"]


# ---------------------------------------------------------------------------
# 4. cancel: 404, and the coordinator is never contacted
# ---------------------------------------------------------------------------


def test_cancelling_another_users_job_is_404_and_never_reaches_the_coordinator(
    client, db, transport
):
    alice = _new_user(db)
    bob = _new_user(db)
    alice_job = _submit(client, _browser_jwt(alice), "dont-cancel-me")
    before = len(transport.requests)

    r = client.post(f"/v1alpha1/jobs/{alice_job['job_id']}/cancel",
                    headers={"Authorization": f"Bearer {_browser_jwt(bob)}"})
    assert r.status_code == 404
    assert len(transport.requests) == before, (
        "cancelling another user's job reached the coordinator: "
        f"{[str(r.url) for r in transport.requests[before:]]}"
    )


def test_owner_can_cancel_their_own_job(client, db, transport):
    alice = _new_user(db)
    alice_jwt = _browser_jwt(alice)
    job = _submit(client, alice_jwt, "alices-cancellable-job")

    r = client.post(f"/v1alpha1/jobs/{job['job_id']}/cancel",
                    headers={"Authorization": f"Bearer {alice_jwt}"})
    assert r.status_code == 200
    assert r.json()["state"] == "CANCELLED"
    assert transport.last.url.path == f"/v1alpha1/jobs/{job['job_id']}/cancel"


def test_cancel_without_a_jwt_is_401_and_never_reaches_the_coordinator(
    client, db, transport
):
    alice = _new_user(db)
    job = _submit(client, _browser_jwt(alice), "needs-auth-to-cancel")
    r = client.post(f"/v1alpha1/jobs/{job['job_id']}/cancel")
    assert r.status_code == 401
    assert all(req.method != "POST" or not req.url.path.endswith("/cancel")
              for req in transport.requests)


# ---------------------------------------------------------------------------
# 5. artifact reads are job-scoped for browsers
# ---------------------------------------------------------------------------


def test_owner_can_read_their_own_jobs_artifacts(client, db, transport):
    alice = _new_user(db)
    alice_jwt = _browser_jwt(alice)
    job = _submit(client, alice_jwt, "job-with-artifacts")
    job_id = job["job_id"]
    transport.seed_artifact(f"jobs/{job_id}/output.txt", b"alice's private output")

    r = client.get(f"/v1alpha1/jobs/{job_id}/artifacts/output.txt",
                   headers={"Authorization": f"Bearer {alice_jwt}"})
    assert r.status_code == 200
    assert r.content == b"alice's private output"


def test_another_users_job_artifacts_are_unreachable(client, db, transport):
    alice = _new_user(db)
    bob = _new_user(db)
    alice_jwt = _browser_jwt(alice)
    job = _submit(client, alice_jwt, "job-bob-must-not-read")
    job_id = job["job_id"]
    transport.seed_artifact(f"jobs/{job_id}/secret.bin", b"alice's confidential bytes")

    r = client.get(f"/v1alpha1/jobs/{job_id}/artifacts/secret.bin",
                   headers={"Authorization": f"Bearer {_browser_jwt(bob)}"})
    assert r.status_code in (403, 404)
    assert r.status_code == 404  # never 403: that would confirm the job exists
    assert b"alice's confidential bytes" not in r.content

    # And Alice herself still succeeds against the same key.
    own = client.get(f"/v1alpha1/jobs/{job_id}/artifacts/secret.bin",
                     headers={"Authorization": f"Bearer {alice_jwt}"})
    assert own.status_code == 200
    assert own.content == b"alice's confidential bytes"


def test_artifacts_under_a_nonexistent_job_are_404(client, db):
    alice = _new_user(db)
    r = client.get("/v1alpha1/jobs/no-such-job/artifacts/output.txt",
                   headers={"Authorization": f"Bearer {_browser_jwt(alice)}"})
    assert r.status_code == 404


def test_job_artifact_key_cannot_escape_via_dot_segments(client, db, transport):
    alice = _new_user(db)
    alice_jwt = _browser_jwt(alice)
    job = _submit(client, alice_jwt, "job-for-traversal-check")
    job_id = job["job_id"]
    before = len(transport.requests)

    r = client.get(f"/v1alpha1/jobs/{job_id}/artifacts/..%2F..%2Fjobs",
                   headers={"Authorization": f"Bearer {alice_jwt}"})
    assert r.status_code == 400
    assert len(transport.requests) == before


def test_a_machine_token_cannot_read_job_artifacts_via_the_browser_route(
    client, db, transport
):
    owner = _new_user(db)
    _mid, token = _enrol(db, owner, f"test-jobs-art-{uuid.uuid4().hex[:8]}")
    job = _submit(client, _browser_jwt(owner), "machine-should-not-use-this-route")
    r = client.get(f"/v1alpha1/jobs/{job['job_id']}/artifacts/output.txt",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
