"""The FC Sandbox orchestrator, reachable over HTTP.

``test_sandbox_orchestrator.py`` proves the reducer: who wins a race, what is
observed rather than assumed, that cleanup happens on every path. None of that
was reachable from a request — the module had zero callers — so this file
covers the half that can be wrong while every test next door stays green.

Three properties carry most of the weight here:

- **Owner scoping is the only thing between two accounts.** ``on_model_ready``
  and ``cleanup_session`` take a BARE session id, deliberately, because the
  reconciler acts for the deployment rather than for a person. So every
  authenticated route must call ``fetch_session_for_owner`` and 404 first, and
  the tests below check that the orchestrator was **not reached** rather than
  only that the status code was 404 — a route that refused after killing
  somebody's sandbox would pass the weaker assertion.
- **The public evidence page is the submission's disqualifier insurance**, and
  it is the only route in this API that reads the database with no credential.
  It is checked for what it does NOT contain, from the row up: the full session
  id, the share token, the evaluation spec, the sandbox id, the owner, and the
  raw error message must appear nowhere in the response bytes.
- **``model-ready`` returns before the evaluation does.** It awaits an accepted
  result bounded by fifteen minutes; a route that ran it inline would be a
  route no browser can call. The test makes the orchestrator hang and asserts
  the 202 comes back anyway.

The orchestrator's four entry points are substituted here rather than driven
end to end. That is the honest split: what these routes own is authorisation,
error mapping, response shape and *when* work runs — and a test that also
booted a simulated sandbox would report a wiring failure as a bootstrap
failure. The one place the real thing runs is the evaluation driver, which is
code this file's module owns.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import sandbox_orchestrator as orchmod
from flashml_cloud_api import sandbox_sessions as ss
from flashml_cloud_api.alibaba_oss import OSSUnavailable
from flashml_cloud_api.app import (
    CoordinatorEvaluationDriver,
    CoordinatorClient,
    EvaluationSpecError,
    build_evaluation_jobspec,
    create_cloud_app,
    evaluation_job_name,
)
from flashml_cloud_api.settings import Settings

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"
OPERATOR_TOKEN = "op-secret-do-not-leak-3f9c1b"
COORDINATOR_URL = "http://coordinator.internal:8100"
REGION = "ap-southeast-1"

EVAL_SPEC = {
    "image": "ghcr.io/zolli-labs/flashml-eval:0.1.0",
    "command": ["python", "evaluate.py"],
}


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class FakeCoordinator(httpx.AsyncBaseTransport):
    """Enough coordinator to submit a job, list jobs and read one back.

    Job listing returns each record with the spec it was submitted with,
    exactly as the real one does — which is what the driver's idempotency
    lookup reads, so a fake that dropped the spec could not exercise it.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.jobs: dict[str, dict] = {}
        self.events: dict[str, list[dict]] = {}
        self._prefix = uuid.uuid4().hex[:8]
        self._next = 1
        #: When set, POST /jobs answers this status AFTER recording the job —
        #: the transport failure whose call actually succeeded.
        self.submit_status_after_accept: int | None = None
        self.list_broken = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        method, path = request.method, request.url.path

        if method == "POST" and path == "/v1alpha1/jobs":
            spec = json.loads(request.content or b"{}")
            job_id = f"job-{self._prefix}-{self._next:04d}"
            self._next += 1
            self.jobs[job_id] = {
                "job_id": job_id, "spec": spec, "state": "RUNNING",
                "backend": "leases",
            }
            if self.submit_status_after_accept is not None:
                return httpx.Response(
                    self.submit_status_after_accept, json={"detail": "lost"}
                )
            return httpx.Response(201, json=self.jobs[job_id])

        if method == "GET" and path == "/v1alpha1/jobs":
            if self.list_broken:
                return httpx.Response(503, json={"detail": "unavailable"})
            return httpx.Response(200, json=list(self.jobs.values()))

        if method == "GET" and path.endswith("/events"):
            job_id = path.split("/")[-2]
            return httpx.Response(200, json=self.events.get(job_id, []))

        if method == "GET" and path.startswith("/v1alpha1/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            record = self.jobs.get(job_id)
            if record is None:
                return httpx.Response(404, json={"detail": "no such job"})
            return httpx.Response(200, json=record)

        return httpx.Response(404, json={"detail": f"unhandled {method} {path}"})

    @property
    def submissions(self) -> list[httpx.Request]:
        return [
            r for r in self.requests
            if r.method == "POST" and r.url.path == "/v1alpha1/jobs"
        ]


class FakeGateway:
    """A ``SandboxGateway`` that is never actually driven here.

    Every orchestrator entry point is substituted in these tests, so the
    gateway's only job is to exist and to be the object the routes hand over —
    which is itself worth asserting: a route that built its own would not be
    injectable and could not be kept away from the network in a test.
    """

    region = REGION
    default_template = "code-interpreter-v1"


class Calls:
    """What the routes asked the orchestrator to do, in order."""

    def __init__(self) -> None:
        self.start: list[dict] = []
        self.model_ready: list[dict] = []
        self.cleanup: list[dict] = []
        self.reconcile: int = 0


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _settings(postgres_dsn: str, *, sandbox: bool = True, oss: bool = True,
              region: str = REGION) -> Settings:
    extra: dict = {}
    if sandbox:
        extra.update(
            fc_sandbox_api_key="not-a-real-key",
            fc_sandbox_api_url=f"https://api.{region}.e2b.fc.aliyuncs.com",
            fc_sandbox_domain=f"{region}.e2b.fc.aliyuncs.com",
            fc_sandbox_region=region,
            fc_sandbox_template="code-interpreter-v1",
            fc_sandbox_pool_id=str(uuid.uuid4()),
            fc_sandbox_timeout_ms=3_600_000,
        )
    if oss:
        extra.update(
            oss_bucket="flashml-artifacts-test",
            oss_endpoint=f"oss-{region}.aliyuncs.com",
            oss_access_key_id="key-id",
            oss_access_key_secret="key-secret",
        )
    return Settings(
        supabase_url="https://project.supabase.co",
        supabase_jwt_secret=JWT_SECRET,
        supabase_service_key="service-key-not-used-here",
        coordinator_url=COORDINATOR_URL,
        coordinator_operator_token=OPERATOR_TOKEN,
        require_auth=True,
        database_url=postgres_dsn,
        console_url="https://console.example",
        **extra,
    )


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    conn.execute("delete from public.sandbox_sessions")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def transport() -> FakeCoordinator:
    return FakeCoordinator()


@pytest.fixture
def calls() -> Calls:
    return Calls()


@pytest.fixture(autouse=True)
def _no_reconcile_loop(monkeypatch):
    """One sweep at startup and no timer, so no test leaves a task ticking."""
    monkeypatch.setenv("FLASHML_SANDBOX_RECONCILE_S", "0")


def _make_client(settings, postgres_dsn, transport, calls, monkeypatch):
    """A client whose orchestrator entry points are recorded, not run.

    Substituted on the MODULE, not on the app: ``app.py`` calls
    ``orchmod.start_session`` by attribute at call time, so this is the same
    binding the deployed code resolves — a fake injected any other way would
    prove nothing about the real path.
    """
    def connect() -> psycopg.Connection:
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        return conn

    async def fake_start_session(db, gateway, s, *, owner_id, training_job_id,
                                 evaluation_spec, **kwargs):
        calls.start.append({
            "owner_id": owner_id, "training_job_id": training_job_id,
            "evaluation_spec": evaluation_spec, "gateway": gateway,
            "kwargs": kwargs,
        })
        # The real function authorises the training job first and creates
        # nothing before it does; reproduced here so the CREATE route's own
        # owner scoping is genuinely exercised rather than assumed.
        job = dbmod.fetch_job_for_owner(db, training_job_id, owner_id)
        if job is None:
            raise orchmod.TrainingJobNotAuthorised(
                f"training job {training_job_id} is not available"
            )
        # A real isolation pool, through the real constructor: the session row
        # carries a foreign key onto `pools`, and a fabricated id would make
        # every read below run against a shape the database cannot hold.
        pool = dbmod.create_pool(db, name=f"fc-{uuid.uuid4().hex[:8]}",
                                 owner_id=owner_id)
        row = ss.create_session(
            db, owner_id=owner_id, pool_id=str(pool["id"]),
            training_job_id=training_job_id, region=REGION,
            template="code-interpreter-v1", evaluation_spec=evaluation_spec,
        )
        return str(row["id"])

    async def fake_on_model_ready(db, gateway, s, *, session_id, **kwargs):
        calls.model_ready.append({"session_id": session_id, "kwargs": kwargs})

    async def fake_cleanup(db, gateway, s, *, session_id, **kwargs):
        calls.cleanup.append({"session_id": session_id})
        ss.transition(db, session_id, ss.fetch_session(db, session_id)["state"],
                      "TERMINATED")

    async def fake_reconcile(db, gateway, s, **kwargs):
        calls.reconcile += 1
        return []

    monkeypatch.setattr(orchmod, "start_session", fake_start_session)
    monkeypatch.setattr(orchmod, "on_model_ready", fake_on_model_ready)
    monkeypatch.setattr(orchmod, "cleanup_session", fake_cleanup)
    monkeypatch.setattr(orchmod, "reconcile", fake_reconcile)

    app = create_cloud_app(
        settings, connect=connect, transport=transport,
        sandbox_gateway=FakeGateway(),
    )
    return TestClient(app)


@pytest.fixture
def client(postgres_dsn, transport, calls, monkeypatch):
    with _make_client(
        _settings(postgres_dsn), postgres_dsn, transport, calls, monkeypatch
    ) as c:
        yield c


@pytest.fixture
def unconfigured_client(postgres_dsn, transport, calls, monkeypatch):
    with _make_client(
        _settings(postgres_dsn, sandbox=False), postgres_dsn, transport,
        calls, monkeypatch,
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _user(db) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id, email) values (%s, %s)",
                    (user_id, f"{user_id[:8]}@example.com"))
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, now())",
            (user_id,),
        )
    return user_id


def _job(db, owner_id: str) -> str:
    job_id = f"job-train-{uuid.uuid4().hex[:12]}"
    dbmod.insert_job(db, job_id=job_id, owner_id=owner_id, name="train",
                     source=None, spec=None, status="COMPLETED")
    return job_id


def _jwt(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": time.time() + 3600},
        JWT_SECRET, algorithm="HS256",
    )


def _headers(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_jwt(user_id)}"}


def _open_session(client, db, user_id: str) -> dict:
    job_id = _job(db, user_id)
    r = client.post(
        "/v1alpha1/sandbox-sessions",
        json={"training_job_id": job_id, "evaluation_spec": EVAL_SPEC},
        headers=_headers(user_id),
    )
    assert r.status_code == 201, r.text
    return {**r.json(), "training_job_id": job_id}


#: Every authenticated route that takes a session id, as (method, template).
ID_ROUTES = [
    ("POST", "/v1alpha1/sandbox-sessions/{id}/model-ready"),
    ("POST", "/v1alpha1/sandbox-sessions/{id}/cleanup"),
    ("GET", "/v1alpha1/sandbox-sessions/{id}"),
    ("GET", "/v1alpha1/sandbox-sessions/{id}/events"),
]


# ---------------------------------------------------------------------------
# 1. create
# ---------------------------------------------------------------------------


def test_create_returns_the_id_state_and_share_token(client, db):
    user_id = _user(db)
    body = _open_session(client, db, user_id)
    assert set(body) >= {"session_id", "state", "share_token"}
    assert body["state"] == ss.INITIAL_STATE
    assert body["share_token"]

    row = ss.fetch_session(db, body["session_id"])
    assert str(row["owner_id"]) == user_id
    assert row["evaluation_spec"] == EVAL_SPEC


def test_create_hands_the_orchestrator_the_injected_gateway(client, db, calls):
    _open_session(client, db, _user(db))
    assert isinstance(calls.start[0]["gateway"], FakeGateway)


def test_create_passes_an_enrolment_url_for_the_sandbox(client, db, calls):
    """The sandbox's flashnode enrols against THIS API with a machine token —
    the coordinator has no idea what one is. The route must therefore pass the
    URL explicitly rather than let the orchestrator default it."""
    _open_session(client, db, _user(db))
    assert calls.start[0]["kwargs"]["coordinator_url"]


def test_create_refuses_a_spec_that_cannot_compile(client, db, calls):
    user_id = _user(db)
    job_id = _job(db, user_id)
    r = client.post(
        "/v1alpha1/sandbox-sessions",
        json={"training_job_id": job_id, "evaluation_spec": {"command": ["x"]}},
        headers=_headers(user_id),
    )
    assert r.status_code == 400
    # Nothing was provisioned to find that out.
    assert calls.start == []
    assert ss.list_sessions_for_owner(db, user_id) == []


def test_create_refuses_a_latest_tag_before_anything_is_created(client, db, calls):
    user_id = _user(db)
    job_id = _job(db, user_id)
    r = client.post(
        "/v1alpha1/sandbox-sessions",
        json={"training_job_id": job_id,
              "evaluation_spec": {"image": "repo:latest", "command": ["x"]}},
        headers=_headers(user_id),
    )
    assert r.status_code == 400
    assert calls.start == []


def test_create_requires_a_training_job_id(client, db):
    user_id = _user(db)
    r = client.post(
        "/v1alpha1/sandbox-sessions",
        json={"evaluation_spec": EVAL_SPEC},
        headers=_headers(user_id),
    )
    assert r.status_code == 400


def test_create_for_someone_elses_training_job_is_404(client, db, calls):
    owner, stranger = _user(db), _user(db)
    job_id = _job(db, owner)
    r = client.post(
        "/v1alpha1/sandbox-sessions",
        json={"training_job_id": job_id, "evaluation_spec": EVAL_SPEC},
        headers=_headers(stranger),
    )
    assert r.status_code == 404
    assert ss.list_sessions_for_owner(db, stranger) == []


# ---------------------------------------------------------------------------
# 2. error mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("exc, status", [
    (orchmod.TrainingJobNotAuthorised("nope"), 404),
    (orchmod.SessionNotFound("nope"), 404),
    (orchmod.SandboxUnconfigured("nope"), 404),
    (OSSUnavailable("nope"), 503),
    (orchmod.EvaluationUnavailable("nope"), 500),
])
def test_create_maps_orchestrator_errors(client, db, monkeypatch, exc, status):
    user_id = _user(db)
    job_id = _job(db, user_id)

    async def boom(*a, **kw):
        raise exc

    monkeypatch.setattr(orchmod, "start_session", boom)
    r = client.post(
        "/v1alpha1/sandbox-sessions",
        json={"training_job_id": job_id, "evaluation_spec": EVAL_SPEC},
        headers=_headers(user_id),
    )
    assert r.status_code == status


def test_session_failed_is_502_carrying_the_session_id_and_code(
    client, db, monkeypatch
):
    """The console links straight to the evidence page, so the id has to
    travel. The free-text detail deliberately does not."""
    user_id = _user(db)
    job_id = _job(db, user_id)
    session_id = str(uuid.uuid4())

    async def boom(*a, **kw):
        raise orchmod.SessionFailed(session_id, "SandboxTerminalError",
                                    "a secret-looking detail")

    monkeypatch.setattr(orchmod, "start_session", boom)
    r = client.post(
        "/v1alpha1/sandbox-sessions",
        json={"training_job_id": job_id, "evaluation_spec": EVAL_SPEC},
        headers=_headers(user_id),
    )
    assert r.status_code == 502
    assert r.json()["detail"] == {
        "session_id": session_id, "code": "SandboxTerminalError",
    }
    assert "secret-looking" not in r.text


def test_unconfigured_is_the_github_app_shape(client, db, monkeypatch):
    user_id = _user(db)
    job_id = _job(db, user_id)

    async def boom(*a, **kw):
        raise orchmod.SandboxUnconfigured("nope")

    monkeypatch.setattr(orchmod, "start_session", boom)
    r = client.post(
        "/v1alpha1/sandbox-sessions",
        json={"training_job_id": job_id, "evaluation_spec": EVAL_SPEC},
        headers=_headers(user_id),
    )
    assert r.status_code == 404
    assert "not configured on this deployment" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 3. model-ready does not run inline
# ---------------------------------------------------------------------------


def test_model_ready_is_202_and_does_not_wait_for_the_evaluation(
    client, db, monkeypatch
):
    """`on_model_ready` waits for an ACCEPTED result, bounded by 900 s. A
    route that ran it inline is a route no browser can call."""
    async def slow(dbc, gateway, s, *, session_id, **kwargs):
        # A bounded wait rather than an event: a route that ran this inline
        # must FAIL this test rather than hang it, and a test that hangs on
        # regression is a test nobody trusts enough to leave in CI.
        await asyncio.sleep(3)

    session = _open_session(client, db, (user_id := _user(db)))
    monkeypatch.setattr(orchmod, "on_model_ready", slow)

    began = time.monotonic()
    r = client.post(
        f"/v1alpha1/sandbox-sessions/{session['session_id']}/model-ready",
        headers=_headers(user_id),
    )
    elapsed = time.monotonic() - began

    assert r.status_code == 202
    assert r.json()["session_id"] == session["session_id"]
    assert r.json()["state"]
    assert elapsed < 2.0, f"the route blocked for {elapsed:.1f}s"


def test_model_ready_actually_starts_the_evaluation(client, db, calls):
    session = _open_session(client, db, (user_id := _user(db)))
    r = client.post(
        f"/v1alpha1/sandbox-sessions/{session['session_id']}/model-ready",
        headers=_headers(user_id),
    )
    assert r.status_code == 202
    deadline = time.monotonic() + 5
    while not calls.model_ready and time.monotonic() < deadline:
        time.sleep(0.02)
    assert [c["session_id"] for c in calls.model_ready] == [session["session_id"]]
    assert calls.model_ready[0]["kwargs"]["driver"] is not None


def test_model_ready_is_503_when_the_artifact_store_is_unconfigured(
    postgres_dsn, transport, calls, monkeypatch, db
):
    with _make_client(
        _settings(postgres_dsn, oss=False), postgres_dsn, transport, calls,
        monkeypatch,
    ) as client:
        session = _open_session(client, db, (user_id := _user(db)))
        r = client.post(
            f"/v1alpha1/sandbox-sessions/{session['session_id']}/model-ready",
            headers=_headers(user_id),
        )
    assert r.status_code == 503
    assert calls.model_ready == []


# ---------------------------------------------------------------------------
# 4. cleanup, read, events, by-job
# ---------------------------------------------------------------------------


def test_cleanup_reports_the_state_read_back_from_the_row(client, db, calls):
    session = _open_session(client, db, (user_id := _user(db)))
    r = client.post(
        f"/v1alpha1/sandbox-sessions/{session['session_id']}/cleanup",
        headers=_headers(user_id),
    )
    assert r.status_code == 200
    assert r.json() == {"session_id": session["session_id"], "state": "TERMINATED"}
    assert calls.cleanup == [{"session_id": session["session_id"]}]


def test_get_session_returns_the_owner_view(client, db):
    session = _open_session(client, db, (user_id := _user(db)))
    r = client.get(
        f"/v1alpha1/sandbox-sessions/{session['session_id']}",
        headers=_headers(user_id),
    )
    assert r.status_code == 200
    body = r.json()
    # The owner sees the whole row — the narrowing is the PUBLIC view's job.
    assert set(body) == set(ss.SESSION_COLUMNS)
    assert body["id"] == session["session_id"]


def test_events_are_returned_in_order(client, db):
    session = _open_session(client, db, (user_id := _user(db)))
    r = client.get(
        f"/v1alpha1/sandbox-sessions/{session['session_id']}/events",
        headers=_headers(user_id),
    )
    assert r.status_code == 200
    events = r.json()
    assert events, "create_session writes its first event in the same transaction"
    assert [e["sequence"] for e in events] == sorted(e["sequence"] for e in events)


def test_events_for_an_unknown_session_is_404_not_empty(client, db):
    r = client.get(
        f"/v1alpha1/sandbox-sessions/{uuid.uuid4()}/events",
        headers=_headers(_user(db)),
    )
    assert r.status_code == 404


def test_sessions_for_a_job_are_newest_first(client, db):
    user_id = _user(db)
    job_id = _job(db, user_id)
    ids = []
    for _ in range(2):
        r = client.post(
            "/v1alpha1/sandbox-sessions",
            json={"training_job_id": job_id, "evaluation_spec": EVAL_SPEC},
            headers=_headers(user_id),
        )
        assert r.status_code == 201
        ids.append(r.json()["session_id"])

    r = client.get(
        f"/v1alpha1/jobs/{job_id}/sandbox-sessions", headers=_headers(user_id)
    )
    assert r.status_code == 200
    returned = [row["id"] for row in r.json()]
    assert set(returned) == set(ids)
    stamps = [row["created_at"] for row in r.json()]
    assert stamps == sorted(stamps, reverse=True)


def test_a_job_with_no_session_is_an_empty_list_not_a_404(client, db):
    user_id = _user(db)
    job_id = _job(db, user_id)
    r = client.get(
        f"/v1alpha1/jobs/{job_id}/sandbox-sessions", headers=_headers(user_id)
    )
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# 5. owner scoping — the only thing between two users
# ---------------------------------------------------------------------------


def test_another_user_gets_404_on_every_id_scoped_route(client, db, calls):
    owner, stranger = _user(db), _user(db)
    session = _open_session(client, db, owner)
    sid = session["session_id"]

    for method, template in ID_ROUTES:
        r = client.request(
            method, template.format(id=sid), headers=_headers(stranger)
        )
        assert r.status_code == 404, f"{method} {template} -> {r.status_code}"

    # And nothing reached the orchestrator. A route that refused only AFTER
    # killing somebody's sandbox would pass a status-code-only assertion.
    assert calls.model_ready == []
    assert calls.cleanup == []
    assert ss.fetch_session(db, sid)["state"] != "TERMINATED"


def test_another_users_job_lists_no_sessions(client, db):
    owner, stranger = _user(db), _user(db)
    session = _open_session(client, db, owner)
    r = client.get(
        f"/v1alpha1/jobs/{session['training_job_id']}/sandbox-sessions",
        headers=_headers(stranger),
    )
    assert r.status_code == 200
    assert r.json() == []


def test_an_unknown_session_and_someone_elses_are_indistinguishable(client, db):
    owner, stranger = _user(db), _user(db)
    sid = _open_session(client, db, owner)["session_id"]
    theirs = client.get(
        f"/v1alpha1/sandbox-sessions/{sid}", headers=_headers(stranger)
    )
    missing = client.get(
        f"/v1alpha1/sandbox-sessions/{uuid.uuid4()}", headers=_headers(stranger)
    )
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json() == missing.json()


@pytest.mark.parametrize("method, template", ID_ROUTES + [
    ("POST", "/v1alpha1/sandbox-sessions"),
    ("GET", "/v1alpha1/jobs/{id}/sandbox-sessions"),
])
def test_authenticated_routes_refuse_a_signed_out_caller(
    client, db, calls, method, template
):
    r = client.request(method, template.format(id=uuid.uuid4()), json={})
    assert r.status_code == 401
    assert calls.start == calls.model_ready == calls.cleanup == []


# ---------------------------------------------------------------------------
# 6. the public evidence page
# ---------------------------------------------------------------------------


def _publish(client, db, user_id: str) -> tuple[str, str]:
    """A session with a marker hash, a sandbox id and an error on it — every
    field the public view has to decide about is populated, so a leak has
    something to leak."""
    session = _open_session(client, db, user_id)
    sid = session["session_id"]
    db.execute(
        "update public.sandbox_sessions set marker_sha256 = %s,"
        " external_sandbox_id = %s, error_code = %s, error_message = %s"
        " where id = %s::uuid",
        ("f" * 64, "i-secret-sandbox-id", "SandboxTerminalError",
         "the raw message nobody outside may read", sid),
    )
    return sid, session["share_token"]


def test_the_public_page_is_reachable_signed_out(client, db):
    """The competition's auto-disqualifier insurance: a live URL that opens
    without a login, on a product where every console route redirects to
    sign-in. Paired with the authenticated read of the SAME session in one
    test, because the property is the difference between the two."""
    sid, token = _publish(client, db, _user(db))

    public = client.get(f"/v1alpha1/public/sandbox-sessions/{token}")
    assert public.status_code == 200
    assert "authorization" not in {k.lower() for k in public.request.headers}

    # ...and the ordinary route onto the same session still refuses.
    assert client.get(f"/v1alpha1/sandbox-sessions/{sid}").status_code == 401


def test_the_public_envelope_is_exactly_session_and_events(client, db):
    _, token = _publish(client, db, _user(db))
    body = client.get(f"/v1alpha1/public/sandbox-sessions/{token}").json()
    # Fixed, so the console's defensive branch over two possible shapes can go.
    assert set(body) == {"session", "events"}
    assert isinstance(body["session"], dict)
    assert isinstance(body["events"], list)


def test_the_public_page_leaks_no_full_identifier(client, db):
    user_id = _user(db)
    sid, token = _publish(client, db, user_id)
    row = ss.fetch_session(db, sid)
    raw = client.get(f"/v1alpha1/public/sandbox-sessions/{token}").text

    for secret in (
        sid,                              # the session id, whole
        token,                            # the capability itself
        user_id,                          # who owns it
        str(row["pool_id"]),              # which team
        str(row["machine_id"] or "x" * 40),
        "i-secret-sandbox-id",            # live infrastructure
        "the raw message nobody outside may read",
        EVAL_SPEC["image"],               # what was being measured
        EVAL_SPEC["command"][1],
    ):
        assert secret not in raw, f"the public page leaked {secret!r}"


def test_the_public_view_serves_only_the_share_columns(client, db):
    _, token = _publish(client, db, _user(db))
    session = client.get(
        f"/v1alpha1/public/sandbox-sessions/{token}"
    ).json()["session"]

    withheld = set(ss.SESSION_COLUMNS) - set(ss.SESSION_SHARE_COLUMNS)
    assert withheld == {"owner_id", "pool_id", "machine_id",
                        "external_sandbox_id", "evaluation_spec", "share_token",
                        # 0026. Our value, not a submitter's, so it passes the
                        # provenance half of Rule 7 — and it is withheld on the
                        # second half: it is the one column that joins this
                        # session to its owner's other work, which is exactly
                        # what a page anybody with a link can open must not do.
                        "correlation_id"}
    assert not (withheld & set(session)), "a withheld column reached the page"
    # `id` is kept in SQL only so the route can read the events; it is rendered
    # as a suffix, and there is deliberately no key called `id`.
    assert "id" not in session
    assert "error_message" not in session
    assert session["error_code"] == "SandboxTerminalError"


def test_the_marker_hash_is_a_twelve_character_prefix(client, db):
    _, token = _publish(client, db, _user(db))
    session = client.get(
        f"/v1alpha1/public/sandbox-sessions/{token}"
    ).json()["session"]
    assert session["marker_sha256_prefix"] == "f" * 12
    assert "marker_sha256" not in session


def test_the_public_events_carry_no_session_id(client, db):
    _, token = _publish(client, db, _user(db))
    events = client.get(
        f"/v1alpha1/public/sandbox-sessions/{token}"
    ).json()["events"]
    assert events
    for event in events:
        assert "session_id" not in event
        assert "id" not in event
        assert set(event) == {"sequence", "type", "source", "observed_at",
                              "latency_ms", "data"}


def test_an_unknown_token_is_404(client, db):
    r = client.get(f"/v1alpha1/public/sandbox-sessions/shr_{uuid.uuid4().hex}")
    assert r.status_code == 404


def test_a_withdrawn_token_is_indistinguishable_from_a_wrong_one(client, db):
    sid, token = _publish(client, db, _user(db))
    db.execute(
        "update public.sandbox_sessions set share_token = null where id = %s::uuid",
        (sid,),
    )
    withdrawn = client.get(f"/v1alpha1/public/sandbox-sessions/{token}")
    wrong = client.get(f"/v1alpha1/public/sandbox-sessions/shr_nope")
    assert withdrawn.status_code == wrong.status_code == 404
    assert withdrawn.json() == wrong.json()


def test_an_empty_token_matches_nothing(client, db):
    _publish(client, db, _user(db))
    # A null share_token must not be handed to a caller who sent none.
    assert client.get("/v1alpha1/public/sandbox-sessions/").status_code in (404, 405)


def test_the_public_route_is_rate_limited(postgres_dsn, transport, calls,
                                          monkeypatch, db):
    monkeypatch.setenv("FLASHML_PUBLIC_RATE_LIMIT", "3")
    monkeypatch.setenv("FLASHML_PUBLIC_RATE_WINDOW_S", "60")
    with _make_client(
        _settings(postgres_dsn), postgres_dsn, transport, calls, monkeypatch
    ) as client:
        _, token = _publish(client, db, _user(db))
        codes = [
            client.get(f"/v1alpha1/public/sandbox-sessions/{token}").status_code
            for _ in range(5)
        ]
    assert codes[:3] == [200, 200, 200]
    assert codes[3:] == [429, 429]


# ---------------------------------------------------------------------------
# 7. unconfigured changes nothing
# ---------------------------------------------------------------------------


def test_every_new_route_is_404_when_the_sandbox_is_unconfigured(
    unconfigured_client, db, calls
):
    user_id = _user(db)
    sid = str(uuid.uuid4())
    job_id = _job(db, user_id)
    requests = [
        ("POST", "/v1alpha1/sandbox-sessions"),
        ("POST", f"/v1alpha1/sandbox-sessions/{sid}/model-ready"),
        ("POST", f"/v1alpha1/sandbox-sessions/{sid}/cleanup"),
        ("GET", f"/v1alpha1/sandbox-sessions/{sid}"),
        ("GET", f"/v1alpha1/sandbox-sessions/{sid}/events"),
        ("GET", f"/v1alpha1/jobs/{job_id}/sandbox-sessions"),
        ("GET", f"/v1alpha1/public/sandbox-sessions/shr_{uuid.uuid4().hex}"),
    ]
    for method, path in requests:
        r = unconfigured_client.request(
            method, path,
            headers=None if "/public/" in path else _headers(user_id),
            json={"training_job_id": job_id, "evaluation_spec": EVAL_SPEC},
        )
        assert r.status_code == 404, f"{method} {path} -> {r.status_code}"
    assert calls.start == calls.model_ready == calls.cleanup == []


def test_unconfigured_starts_no_reconciler(unconfigured_client, calls):
    assert calls.reconcile == 0
    assert getattr(unconfigured_client.app.state, "sandbox_reconciler", None) is None


def test_existing_routes_are_untouched_when_unconfigured(unconfigured_client, db):
    user_id = _user(db)
    r = unconfigured_client.get("/v1alpha1/me", headers=_headers(user_id))
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 8. reconcile runs on startup
# ---------------------------------------------------------------------------


def test_reconcile_sweeps_at_startup(client, calls):
    """The only backstop against a sandbox billing after a crashed controller,
    so it runs on the edge that causes the crash: a redeploy."""
    deadline = time.monotonic() + 5
    while calls.reconcile == 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert calls.reconcile >= 1


def test_a_failing_sweep_does_not_take_the_app_down(
    postgres_dsn, transport, calls, monkeypatch, db
):
    async def boom(*a, **kw):
        raise RuntimeError("the provider had a bad minute")

    monkeypatch.setattr(orchmod, "reconcile", boom)
    with _make_client(
        _settings(postgres_dsn), postgres_dsn, transport, calls, monkeypatch
    ) as client:
        assert client.get("/healthz").status_code == 200


# ---------------------------------------------------------------------------
# 9. the evaluation driver
# ---------------------------------------------------------------------------


def _driver(transport, settings) -> CoordinatorEvaluationDriver:
    return CoordinatorEvaluationDriver(
        CoordinatorClient(settings, transport=transport)
    )


def _request(session_id: str, **over) -> orchmod.EvaluationRequest:
    fields = {
        "session_id": session_id,
        "owner_id": str(uuid.uuid4()),
        "pool_id": str(uuid.uuid4()),
        "training_job_id": "job-train-abc",
        "node_id": orchmod.node_id_for(session_id),
        "spec": EVAL_SPEC,
    }
    fields.update(over)
    return orchmod.EvaluationRequest(**fields)


def test_the_driver_satisfies_the_protocol(postgres_dsn, transport):
    assert isinstance(
        _driver(transport, _settings(postgres_dsn)), orchmod.EvaluationDriver
    )


def test_two_submits_for_one_session_place_one_job(postgres_dsn, transport):
    """The hard requirement the orchestrator explicitly cannot enforce."""
    driver = _driver(transport, _settings(postgres_dsn))
    session_id = str(uuid.uuid4())

    first = asyncio.run(driver.submit(_request(session_id)))
    second = asyncio.run(driver.submit(_request(session_id)))

    assert first == second
    assert len(transport.submissions) == 1
    assert len(transport.jobs) == 1


def test_two_sessions_place_two_jobs(postgres_dsn, transport):
    """The idempotency must key on the session and nothing coarser."""
    driver = _driver(transport, _settings(postgres_dsn))
    a = asyncio.run(driver.submit(_request(str(uuid.uuid4()))))
    b = asyncio.run(driver.submit(_request(str(uuid.uuid4()))))
    assert a != b
    assert len(transport.submissions) == 2


def test_a_lost_response_does_not_place_a_second_job(postgres_dsn, transport):
    """A 5xx from a coordinator that had already recorded the job is the
    transport failure whose call succeeded. Resubmitting on it is how one
    session ends up with two evaluations on a one-machine pool."""
    driver = _driver(transport, _settings(postgres_dsn))
    transport.submit_status_after_accept = 503
    session_id = str(uuid.uuid4())

    recovered = asyncio.run(driver.submit(_request(session_id)))
    assert recovered in transport.jobs
    assert len(transport.jobs) == 1


def test_the_submitted_spec_is_lease_mode_pooled_and_names_the_model(
    postgres_dsn, transport
):
    driver = _driver(transport, _settings(postgres_dsn))
    request = _request(str(uuid.uuid4()))
    asyncio.run(driver.submit(request))

    spec = json.loads(transport.submissions[0].content)
    assert spec["spec"]["execution"]["backend"] == "leases"
    assert spec["spec"]["placement"]["pool"] == request.pool_id
    # allowFallback iff pool — the invariant CommandRecipe.expand enforces.
    assert spec["spec"]["isolation"]["allowFallback"] is True
    assert spec["spec"]["workload"]["parameters"]["inputs"]["model"] == (
        f"artifact://jobs/{request.training_job_id}/"
    )
    assert spec["metadata"]["name"] == evaluation_job_name(request.session_id)


def test_an_unreadable_listing_still_submits(postgres_dsn, transport):
    """Treating a broken listing as "already submitted" would strand a session
    waiting on a job that was never placed."""
    driver = _driver(transport, _settings(postgres_dsn))
    transport.list_broken = True
    job_id = asyncio.run(driver.submit(_request(str(uuid.uuid4()))))
    assert job_id in transport.jobs


def test_poll_returns_none_while_the_job_is_running(postgres_dsn, transport):
    driver = _driver(transport, _settings(postgres_dsn))
    job_id = asyncio.run(driver.submit(_request(str(uuid.uuid4()))))
    assert asyncio.run(driver.poll(job_id)) is None


@pytest.mark.parametrize("state, accepted", [
    ("SUCCEEDED", True),
    ("PARTIAL", False),   # lost shards are not the verdict somebody asked for
    ("FAILED", False),
    ("CANCELLED", False),
])
def test_poll_settles_only_on_a_terminal_state(
    postgres_dsn, transport, state, accepted
):
    driver = _driver(transport, _settings(postgres_dsn))
    job_id = asyncio.run(driver.submit(_request(str(uuid.uuid4()))))
    transport.jobs[job_id]["state"] = state

    outcome = asyncio.run(driver.poll(job_id))
    assert outcome is not None
    assert outcome.accepted is accepted
    assert outcome.evaluation_job_id == job_id


def test_poll_reports_the_coordinators_own_submit_to_claim_interval(
    postgres_dsn, transport
):
    """Both endpoints are the coordinator's own timestamps, so the number is
    not contaminated by this API's clock or by the wake before it."""
    driver = _driver(transport, _settings(postgres_dsn))
    job_id = asyncio.run(driver.submit(_request(str(uuid.uuid4()))))
    transport.jobs[job_id]["state"] = "SUCCEEDED"
    transport.events[job_id] = [
        {"type": "JOB_ACCEPTED", "timestamp": "2026-08-11T10:00:00Z"},
        {"type": "LEASE_CLAIMED", "timestamp": "2026-08-11T10:00:02.500Z"},
        {"type": "LEASE_CLAIMED", "timestamp": "2026-08-11T10:00:09Z"},
    ]
    outcome = asyncio.run(driver.poll(job_id))
    assert outcome.data["submit_to_claim_ms"] == 2500.0


def test_the_interval_is_absent_rather_than_estimated(postgres_dsn, transport):
    driver = _driver(transport, _settings(postgres_dsn))
    job_id = asyncio.run(driver.submit(_request(str(uuid.uuid4()))))
    transport.jobs[job_id]["state"] = "SUCCEEDED"
    transport.events[job_id] = [
        {"type": "JOB_ACCEPTED", "timestamp": "2026-08-11T10:00:00Z"},
    ]
    outcome = asyncio.run(driver.poll(job_id))
    assert "submit_to_claim_ms" not in outcome.data


def test_an_unreadable_coordinator_is_not_a_verdict(postgres_dsn, transport):
    """`None` means *not yet*, never *failed*. Collapsing the two would settle
    a session on the strength of a network blip."""
    driver = _driver(transport, _settings(postgres_dsn))
    assert asyncio.run(driver.poll("job-that-does-not-exist")) is None


# ---------------------------------------------------------------------------
# 10. the compiled spec
# ---------------------------------------------------------------------------


def test_the_job_name_is_derived_from_the_session_id():
    session_id = str(uuid.uuid4())
    assert evaluation_job_name(session_id) == evaluation_job_name(session_id)
    assert session_id in evaluation_job_name(session_id)
    assert len(evaluation_job_name(session_id)) <= 63


@pytest.mark.parametrize("spec", [
    {},
    {"command": ["python"]},
    {"image": "repo:1.0"},
    {"image": "repo:1.0", "command": []},
    {"image": "repo:1.0", "command": "python eval.py"},
    {"image": "repo", "command": ["python"]},
    {"image": "repo:latest", "command": ["python"]},
    {"image": {"repository": "repo"}, "command": ["python"]},
    {"image": "repo:1.0", "command": ["python"], "env": ["not", "a", "map"]},
    {"image": "repo:1.0", "command": ["python"], "timeout_seconds": "soon"},
])
def test_an_unusable_evaluation_spec_is_refused(spec):
    with pytest.raises(EvaluationSpecError):
        build_evaluation_jobspec(
            session_id=str(uuid.uuid4()), pool_id="pool-1",
            training_job_id="job-1", spec=spec,
        )


def test_workload_parameters_are_forwarded_verbatim():
    spec = build_evaluation_jobspec(
        session_id=str(uuid.uuid4()), pool_id="pool-1", training_job_id="job-1",
        spec={**EVAL_SPEC, "env": {"SEED": 7}, "timeout_seconds": 300,
              "dependencies": ["torch==2.4.0"]},
    )
    parameters = spec["spec"]["workload"]["parameters"]
    assert parameters["env"] == {"SEED": "7"}
    assert parameters["timeout_seconds"] == 300
    assert parameters["dependencies"] == ["torch==2.4.0"]
