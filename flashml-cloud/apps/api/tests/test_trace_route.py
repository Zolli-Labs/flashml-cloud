"""``GET /v1alpha1/trace/{correlation_id}`` — AG-5's cloud-side trace surface.

One call that returns the whole D-4 chain: every job, sandbox session and
attempt sharing one correlation id, for a caller authorized to see it. The
chain itself is `migrations/0026_correlation_id.sql`; `test_correlation_id.py`
proves the WRITE side (inherit, never mint); this file proves the READ side
that AG-5 asks for.

**Visibility is narrower than the sibling `/contributions` and
`/verifications` routes, on purpose.** Those widen to a job's pool via
`fetch_job_for_viewer`. A correlation id is minted once, at exactly one
submission (`observability.new_correlation_id`), so it names ONE owner's
work by construction — `dbmod.trace_by_correlation_id` checks
`owner_id = user_id` directly against `jobs`/`sandbox_sessions`, never pool
membership. A stranger, an unknown id, and a garbage id all come back 404,
indistinguishably — the same doctrine `test_job_verifications_route.py` pins
for its own route, tightened one notch further here.

Self-contained the same way that file is: a `SilentTransport` that raises if
contacted, plus rows seeded directly via `dbmod`/`sandbox_sessions`, proves
this route never round-trips to the coordinator for something this local.
"""
from __future__ import annotations

import time
import uuid

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import sandbox_sessions as ssmod
from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.settings import Settings

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"


@pytest.fixture(scope="module")
def settings(postgres_dsn) -> Settings:
    return Settings(
        supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
        supabase_jwt_secret=JWT_SECRET,
        supabase_service_key="service-key-not-used-here",
        coordinator_url="http://coordinator.internal:8100",
        coordinator_operator_token="op-secret-do-not-leak-3f9c1b",
        require_auth=True,
        database_url=postgres_dsn,
        console_url="https://console.example",
    )


class SilentTransport(httpx.AsyncBaseTransport):
    """This route is answered entirely from ``public.jobs``,
    ``public.sandbox_sessions`` and ``public.attempts``. A coordinator round
    trip here would mean the route stopped trusting its own ledger."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"the trace route contacted {request.url}")


@pytest.fixture
def client(settings, postgres_dsn):
    def connect() -> psycopg.Connection:
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        return conn

    app = create_cloud_app(settings, connect=connect, transport=SilentTransport())
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
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, now())",
            (user_id,),
        )
    return user_id


def _jwt(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": time.time() + 3600},
        JWT_SECRET,
        algorithm="HS256",
    )


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_jwt(user_id)}"}


def _node_id(tag: str) -> str:
    return f"trace-route-{tag}-{uuid.uuid4().hex[:10]}"


def _machine(db, owner: str, *, name: str = "laptop") -> str:
    return str(dbmod.insert_machine(
        db, owner_id=owner, node_id=_node_id("m"), name=name, platform="linux",
    ))


def _job_id() -> str:
    return f"cjob-{uuid.uuid4().hex[:12]}"


def _seed_job(
    db, owner_id: str, job_id: str, *, correlation_id: str | None = None,
) -> None:
    """A real ``jobs`` row, written the same production path
    ``list_job_contributions``'s and ``list_verifications_for_job``'s own
    route tests use — direct ``dbmod.insert_job``, no coordinator round trip
    needed for a route that never calls one."""
    dbmod.insert_job(
        db, job_id=job_id, owner_id=owner_id, name="job-under-test",
        source=None, spec=None, status="RUNNING", correlation_id=correlation_id,
    )


def _pool(db, owner_id: str) -> str:
    return dbmod.create_pool(db, name="Team", owner_id=owner_id)["id"]


def _get(client, user_id: str, correlation_id: str):
    return client.get(
        f"/v1alpha1/trace/{correlation_id}", headers=_auth(user_id)
    )


# ---------------------------------------------------------------------------
# 1. the chain: jobs, sandbox sessions and attempts on the same thread
# ---------------------------------------------------------------------------


def test_a_job_submitted_with_a_correlation_id_appears_in_its_own_trace(client, db):
    owner = _new_user(db)
    cid = str(uuid.uuid4())
    job_id = _job_id()
    _seed_job(db, owner, job_id, correlation_id=cid)

    r = _get(client, owner, cid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["correlation_id"] == cid
    assert [j["id"] for j in body["jobs"]] == [job_id]
    assert body["jobs"][0]["owner_id"] == owner


def test_an_attempt_sharing_the_correlation_id_appears_under_attempts(client, db):
    owner = _new_user(db)
    machine = _machine(db, owner)
    cid = str(uuid.uuid4())
    job_id = _job_id()
    _seed_job(db, owner, job_id, correlation_id=cid)
    lease_id = f"lease-{uuid.uuid4().hex[:8]}"
    dbmod.record_attempt(
        db, lease_id=lease_id, machine_id=machine, job_id=job_id,
        task_id="task-000",
    )

    body = _get(client, owner, cid).json()
    assert [a["lease_id"] for a in body["attempts"]] == [lease_id]
    assert body["attempts"][0]["task_id"] == "task-000"
    assert body["attempts"][0]["correlation_id"] == cid


def test_a_sandbox_session_alone_authorizes_and_appears_in_its_trace(client, db):
    """Ownership is "a job OR a sandbox session", not "a job". A session
    with no training job's row in this database (the coordinator-side job id
    case `test_correlation_id.py` covers on the write side) must still be
    reachable through its own correlation id."""
    owner = _new_user(db)
    cid = str(uuid.uuid4())
    session = ssmod.create_session(
        db, owner_id=owner, pool_id=_pool(db, owner),
        training_job_id="a-job-only-the-coordinator-knows",
        region="ap-southeast-1", template="t", correlation_id=cid,
    )

    body = _get(client, owner, cid).json()
    assert [s["id"] for s in body["sandbox_sessions"]] == [str(session["id"])]
    assert body["jobs"] == []


# ---------------------------------------------------------------------------
# 2. visibility: ownership, not viewer-scoping, and always 404
# ---------------------------------------------------------------------------


def test_a_stranger_requesting_another_owners_correlation_id_gets_404(client, db):
    owner = _new_user(db)
    stranger = _new_user(db)
    cid = str(uuid.uuid4())
    _seed_job(db, owner, _job_id(), correlation_id=cid)

    r = _get(client, stranger, cid)
    assert r.status_code == 404


def test_a_pool_mate_does_not_widen_visibility_here(client, db):
    """The one place this route deliberately disagrees with its siblings:
    `/contributions` and `/verifications` admit any pool member through
    `fetch_job_for_viewer`; a correlation id names one owner's submission and
    a teammate who does not themselves own a row on the thread is a stranger
    to it."""
    owner = _new_user(db)
    member = _new_user(db)
    pool_id = _pool(db, owner)
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pool_members (pool_id, user_id) values (%s, %s)",
            (pool_id, member),
        )
    cid = str(uuid.uuid4())
    job_id = _job_id()
    _seed_job(db, owner, job_id, correlation_id=cid)
    with db.cursor() as cur:
        cur.execute(
            "update public.jobs set pool_id = %s where id = %s", (pool_id, job_id)
        )

    assert _get(client, member, cid).status_code == 404
    assert _get(client, owner, cid).status_code == 200


def test_a_garbage_correlation_id_is_404_not_a_crash(client, db):
    owner = _new_user(db)
    r = _get(client, owner, "not-a-uuid-at-all")
    assert r.status_code == 404


def test_a_hostname_shaped_correlation_id_is_404_not_a_crash(client, db):
    """The exact shape D-2 exists to keep out of this column in the first
    place must not even reach the database as a query parameter."""
    owner = _new_user(db)
    r = _get(client, owner, "phongs-macbook.local")
    assert r.status_code == 404


def test_a_correlation_id_that_exists_for_nobody_is_404(client, db):
    owner = _new_user(db)
    r = _get(client, owner, str(uuid.uuid4()))
    assert r.status_code == 404


def test_the_route_requires_a_jwt(client, db):
    r = client.get(f"/v1alpha1/trace/{uuid.uuid4()}")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# 3. provenance (AS-16): ids/enums/timestamps only
# ---------------------------------------------------------------------------


def test_the_response_carries_no_token_hostname_or_stderr(client, db):
    owner = _new_user(db)
    machine = _machine(db, owner, name="a-hostname-nobody-should-see.local")
    cid = str(uuid.uuid4())
    job_id = _job_id()
    _seed_job(db, owner, job_id, correlation_id=cid)
    session = ssmod.create_session(
        db, owner_id=owner, pool_id=_pool(db, owner), training_job_id=job_id,
        region="ap-southeast-1", template="t", correlation_id=cid,
    )
    lease_id = f"lease-{uuid.uuid4().hex[:8]}"
    dbmod.record_attempt(
        db, lease_id=lease_id, machine_id=machine, job_id=job_id,
        task_id="task-000",
    )

    r = _get(client, owner, cid)
    assert r.status_code == 200, r.text
    body = r.json()

    # No bearer capability, ever — the whole reason 0014 forbids a token
    # column and mints share_token only in the application.
    assert session["share_token"] not in r.text
    # No machine self-reported hostname anywhere in the payload.
    assert "a-hostname-nobody-should-see.local" not in r.text

    job_fields = set(body["jobs"][0])
    assert job_fields <= {
        "id", "owner_id", "pool_id", "status", "correlation_id", "created_at",
    }
    assert "name" not in job_fields  # submitter-typed, AS-16

    session_fields = set(body["sandbox_sessions"][0])
    assert "share_token" not in session_fields
    assert "error_message" not in session_fields
    assert "marker_sha256" not in session_fields

    attempt_fields = set(body["attempts"][0])
    assert attempt_fields <= {
        "lease_id", "machine_id", "job_id", "task_id", "claimed_at",
        "accepted_at", "resolved_at", "outcome", "lease_deadline",
        "correlation_id",
    }


# ---------------------------------------------------------------------------
# 4. absent is null: a job with no correlation id joins no trace
# ---------------------------------------------------------------------------


def test_a_job_with_null_correlation_id_is_never_grouped_into_another_trace(
    client, db
):
    owner = _new_user(db)
    cid = str(uuid.uuid4())
    threaded_job = _job_id()
    _seed_job(db, owner, threaded_job, correlation_id=cid)
    # Same owner, same moment, no correlation id at all.
    _seed_job(db, owner, _job_id(), correlation_id=None)

    body = _get(client, owner, cid).json()
    assert [j["id"] for j in body["jobs"]] == [threaded_job]
