"""The API as the only public door.

Every test here pins something that would be a *vulnerability* if it were
wrong, not merely a bug. The shape of the thing under test is: an agent
presents a machine token to the cloud API; the API resolves that token to
exactly one machine, and forwards to the (private) coordinator using its
operator credential plus ``X-FlashML-On-Behalf-Of: <that machine's
node_id>``. Everything an agent puts in its own request body about *who it
is* is discarded.

Two deliberate choices about how these run:

* The coordinator is a **recording fake transport**, not a live server.
  What matters is the exact bytes and headers that leave this process, and
  a live coordinator would only let us observe its *reaction* to them.
  ``RecordingTransport`` captures the outbound ``httpx.Request`` so the
  assertions can be about the request itself.
* The database is the real, freshly migrated ephemeral Postgres from
  ``conftest.py``. Revocation taking effect *immediately* is a property of
  a real query against a real row, and a mocked machine lookup would prove
  nothing about it.

There are no skips in this file, deliberately: a security test that skips
reads as green in CI and is worse than no test at all.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import enrolment
from flashml_cloud_api.app import DELEGATION_HEADER, _scrub_identity, create_cloud_app
from flashml_cloud_api.settings import Settings

RUN_MARKER = uuid.uuid4().hex[:12]

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"
OPERATOR_TOKEN = "op-secret-do-not-leak-3f9c1b"
COORDINATOR_URL = "http://coordinator.internal:8100"


def _node_id(name: str) -> str:
    return f"test-{RUN_MARKER}-{name}"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


class RecordingTransport(httpx.AsyncBaseTransport):
    """A stand-in coordinator that answers 200 and remembers what it was
    asked. Assertions are on ``self.requests[-1]`` — the real outbound
    request object, headers and body included — so a proxy that returns the
    right thing to the agent while forwarding the wrong thing upstream
    cannot pass."""

    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.requests: list[httpx.Request] = []
        self.status_code = status_code
        self.payload = payload if payload is not None else {"ok": True}
        self.raise_connect_error: str | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        if self.raise_connect_error is not None:
            raise httpx.ConnectError(self.raise_connect_error, request=request)
        return httpx.Response(self.status_code, json=self.payload)

    @property
    def last(self) -> httpx.Request:
        assert self.requests, "nothing was forwarded to the coordinator"
        return self.requests[-1]


@pytest.fixture(scope="module")
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        with conn.cursor() as cur:
            cur.execute(
                "delete from public.device_codes where node_id like %s",
                (f"test-{RUN_MARKER}-%",),
            )
            cur.execute(
                "delete from public.machines where node_id like %s",
                (f"test-{RUN_MARKER}-%",),
            )
        conn.close()


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
def transport() -> RecordingTransport:
    return RecordingTransport()


@pytest.fixture
def client(settings, postgres_dsn, transport):
    def connect() -> psycopg.Connection:
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        return conn

    app = create_cloud_app(settings, connect=connect, transport=transport)
    with TestClient(app) as c:
        yield c


def _new_user(db) -> str:
    """A real ``auth.users`` + ``public.profiles`` pair. profiles.id is an
    FK to auth.users, and machines.owner_id an FK to profiles, so a machine
    cannot exist without both.

    Admitted at creation: this file's device-approve tests go through the
    invite-gated ``POST /v1alpha1/device/approve`` route (Task 10), and this
    fixture models an ordinary, already-onboarded account rather than the
    admission gate itself."""
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, now())",
            (user_id,),
        )
    return user_id


def _enrol(db, owner_id: str, node_id: str) -> tuple[str, str]:
    """Run the real device flow end to end. Returns (machine_id, token)."""
    started = enrolment.start_device_code(db, node_id, "host-" + node_id, "linux")
    machine_id = enrolment.approve_device_code(db, started["user_code"], owner_id)
    token = enrolment.redeem_device_code(db, started["device_code"])
    assert token is not None
    return str(machine_id), token


def _browser_jwt(user_id: str, **over) -> str:
    claims = {"sub": user_id, "aud": "authenticated", "exp": time.time() + 3600}
    claims.update(over)
    return jwt.encode(claims, JWT_SECRET, algorithm="HS256")


@pytest.fixture(scope="module")
def machine(db):
    """One long-lived, never-revoked machine shared by the tests that only
    need *a* valid agent. Module-scoped because ``machines.node_id`` is
    unique: re-enrolling the same node_id per test would (correctly) be
    refused as impersonation. Tests that revoke enrol their own."""
    owner = _new_user(db)
    machine_id, token = _enrol(db, owner, _node_id("agent-a"))
    return {"owner": owner, "id": str(machine_id), "token": token,
            "node_id": _node_id("agent-a")}


# ---------------------------------------------------------------------------
# 1. no agent route reaches the coordinator without a machine token
# ---------------------------------------------------------------------------


def _agent_routes(app) -> list[tuple[str, str]]:
    """Every route the app itself declares as agent-facing. Enumerated from
    the app rather than hardcoded on purpose: a route added later without
    auth must fail this test, and a hardcoded list would simply not know
    about it."""
    out = []
    for route in app.routes:
        if "agent" not in (getattr(route, "tags", None) or []):
            continue
        path = re.sub(r"\{[^}]+\}", "x", route.path)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            out.append((method, path))
    return out


def test_every_agent_route_is_401_without_a_token(client, transport):
    routes = _agent_routes(client.app)
    assert len(routes) >= 10, f"expected the full proxy surface, got {routes}"
    for method, path in routes:
        r = client.request(method, path, content=b"{}")
        assert r.status_code == 401, f"{method} {path} answered {r.status_code}"
    assert transport.requests == [], (
        "an unauthenticated request reached the coordinator: "
        f"{[str(r.url) for r in transport.requests]}"
    )


def test_every_agent_route_is_401_with_a_junk_token(client, transport):
    for method, path in _agent_routes(client.app):
        r = client.request(method, path, content=b"{}",
                           headers={"Authorization": "Bearer fmk_not-a-real-token"})
        assert r.status_code == 401, f"{method} {path} answered {r.status_code}"
    assert transport.requests == []


def test_anonymous_traffic_costs_no_database_connection(settings, postgres_dsn,
                                                        transport):
    """The agent routes are the public door: anyone on the internet can hit
    them. If resolving the request opened a Postgres connection *before*
    checking for a credential, an unauthenticated flood would exhaust the
    connection pool for free."""
    opened = []

    def counting_connect():
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        opened.append(conn)
        return conn

    app = create_cloud_app(settings, connect=counting_connect, transport=transport)
    with TestClient(app) as c:
        for method, path in _agent_routes(app):
            c.request(method, path, content=b"{}")
        c.get("/v1alpha1/machines")
        c.get("/v1alpha1/me")
        c.post("/v1alpha1/device/approve", json={"user_code": "AAAAAAAA"})
    assert opened == [], f"{len(opened)} connection(s) opened for anonymous traffic"


def test_bearer_prefix_is_required(client, machine, transport):
    """A raw token in the Authorization header without ``Bearer `` must not
    work: accepting both spellings doubles the parsing surface for nothing."""
    r = client.post("/v1alpha1/leases/claim", content=b"{}",
                    headers={"Authorization": machine["token"]})
    assert r.status_code == 401
    assert transport.requests == []


# ---------------------------------------------------------------------------
# 2. revocation is immediate
# ---------------------------------------------------------------------------


def test_revoked_machine_token_is_rejected_immediately(client, db, transport):
    owner = _new_user(db)
    machine_id, token = _enrol(db, owner, _node_id("revoke-me"))
    auth = {"Authorization": f"Bearer {token}"}

    assert client.post("/v1alpha1/leases/claim", content=b"{}",
                       headers=auth).status_code == 200
    forwarded_before = len(transport.requests)

    assert enrolment.revoke_machine(db, machine_id, owner) is True

    # No refresh, no cache expiry, no restart: the very next call fails.
    r = client.post("/v1alpha1/leases/claim", content=b"{}", headers=auth)
    assert r.status_code == 401
    assert len(transport.requests) == forwarded_before, (
        "a revoked machine's request still reached the coordinator"
    )


def test_revoking_one_machine_does_not_disable_another(client, db):
    owner = _new_user(db)
    keep_id, keep_token = _enrol(db, owner, _node_id("keep"))
    drop_id, drop_token = _enrol(db, owner, _node_id("drop"))

    assert enrolment.revoke_machine(db, drop_id, owner) is True

    assert client.post("/v1alpha1/leases/claim", content=b"{}",
                       headers={"Authorization": f"Bearer {drop_token}"}).status_code == 401
    assert client.post("/v1alpha1/leases/claim", content=b"{}",
                       headers={"Authorization": f"Bearer {keep_token}"}).status_code == 200


# ---------------------------------------------------------------------------
# 3. what actually leaves the process
# ---------------------------------------------------------------------------


def test_forwarded_request_carries_operator_token_and_on_behalf_of(
    client, machine, transport
):
    r = client.post("/v1alpha1/leases/claim", json={"job_id": "j1"},
                    headers={"Authorization": f"Bearer {machine['token']}"})
    assert r.status_code == 200

    out = transport.last
    assert str(out.url).startswith(COORDINATOR_URL)
    assert out.url.path == "/v1alpha1/leases/claim"
    assert out.headers["Authorization"] == f"Bearer {OPERATOR_TOKEN}"
    assert out.headers[DELEGATION_HEADER] == machine["node_id"]


def test_the_delegation_header_is_spelled_exactly_as_the_coordinator_reads_it():
    """Task 4 reads this literal string. A typo here would silently demote
    every forwarded call to an unscoped operator write."""
    assert DELEGATION_HEADER == "X-FlashML-On-Behalf-Of"


def test_the_agents_own_authorization_header_is_not_forwarded(
    client, machine, transport
):
    client.post("/v1alpha1/leases/claim", json={"job_id": "j1"},
                headers={"Authorization": f"Bearer {machine['token']}"})
    out = transport.last
    assert machine["token"] not in out.headers["Authorization"]
    assert machine["token"] not in out.headers.get("X-Forwarded-Authorization", "")


def test_exactly_one_on_behalf_of_header_is_sent(client, machine, transport):
    """The coordinator answers 400 on a duplicated delegation header rather
    than picking a winner (an ordering the agent would control). So the
    client must be structurally incapable of emitting two — including when
    the agent helpfully supplies its own."""
    r = client.post(
        "/v1alpha1/leases/claim",
        json={"job_id": "j1"},
        headers={
            "Authorization": f"Bearer {machine['token']}",
            DELEGATION_HEADER: "fn-somebody-else",
        },
    )
    assert r.status_code == 200
    values = transport.last.headers.get_list(DELEGATION_HEADER)
    assert len(values) == 1, f"sent {len(values)} delegation headers: {values}"
    assert values[0] == machine["node_id"]


def test_a_lowercase_spoofed_delegation_header_is_also_dropped(
    client, machine, transport
):
    """HTTP header names are case-insensitive; a case-varied copy must not
    survive as a second value."""
    client.post(
        "/v1alpha1/leases/claim",
        json={"job_id": "j1"},
        headers={
            "Authorization": f"Bearer {machine['token']}",
            "x-flashml-on-behalf-of": "fn-somebody-else",
        },
    )
    values = transport.last.headers.get_list(DELEGATION_HEADER)
    assert len(values) == 1
    assert values[0] == machine["node_id"]


def test_body_node_id_disagreeing_with_the_token_is_overwritten(
    client, machine, transport
):
    """The rule Plan 2 learned the hard way on ``claim``: the body is not
    authoritative about identity."""
    r = client.post(
        "/v1alpha1/leases/claim",
        json={"node_id": "fn-victim", "job_id": "j1"},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    assert r.status_code == 200
    body = transport.last.read().decode()
    assert '"node_id"' in body
    assert machine["node_id"] in body
    assert "fn-victim" not in body
    assert transport.last.headers[DELEGATION_HEADER] == machine["node_id"]


def test_register_body_node_id_is_overwritten(client, machine, transport):
    client.post(
        "/v1alpha1/nodes/register",
        json={"schema_version": "v1alpha1", "node_id": "fn-victim",
              "hostname": "h"},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    body = transport.last.read().decode()
    assert "fn-victim" not in body
    assert machine["node_id"] in body


def test_heartbeat_path_node_id_is_taken_from_the_token_not_the_url(
    client, machine, transport
):
    """A machine may only heartbeat itself. Keeping another node marked
    online falsifies the pool view, so the URL segment is replaced rather
    than trusted."""
    r = client.post(
        "/v1alpha1/nodes/fn-victim/heartbeat",
        json={"schema_version": "v1alpha1", "node_id": "fn-victim"},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    assert r.status_code == 200
    assert transport.last.url.path == f"/v1alpha1/nodes/{machine['node_id']}/heartbeat"
    assert "fn-victim" not in transport.last.read().decode()


# ---------------------------------------------------------------------------
# 2b. pool membership is stamped onto register/heartbeat — never trusted
# from the agent's own body (Task 11)
# ---------------------------------------------------------------------------


def test_scrub_identity_stamps_pools_on_the_empty_body_force_branch():
    """``force=True`` on an empty body already synthesizes ``{"node_id":
    ...}`` out of nothing; the pools stamp must land in that same
    synthesized body rather than only firing when the agent sent *some*
    body. Both placements exercised — the heartbeat (top-level) and
    register (nested under ``capabilities``) shapes respectively — and the
    empty-membership case must still emit ``[]``, not omit the key."""
    top, media_type = _scrub_identity(
        b"", "node-x", force=True, pools=["p1", "p2"], pools_where="top"
    )
    assert media_type == "application/json"
    assert json.loads(top) == {"node_id": "node-x", "pools": ["p1", "p2"]}

    nested, _ = _scrub_identity(
        b"", "node-x", force=True, pools=[], pools_where="capabilities"
    )
    assert json.loads(nested) == {"node_id": "node-x", "capabilities": {"pools": []}}


def test_register_body_pools_is_stamped_from_membership(client, db, transport):
    """A forged ``capabilities.pools`` in the register body must be
    overwritten with the caller's real membership, never merged with it."""
    owner = _new_user(db)
    node_id = _node_id("pool-register")
    _, token = _enrol(db, owner, node_id)
    pool = dbmod.create_pool(db, name=f"team-{RUN_MARKER}", owner_id=owner)

    client.post(
        "/v1alpha1/nodes/register",
        json={"schema_version": "v1alpha1", "node_id": node_id,
              "hostname": "h",
              "capabilities": {"cpu_cores": 4, "pools": ["forged-pool"]}},
        headers={"Authorization": f"Bearer {token}"},
    )
    body = json.loads(transport.last.read())
    assert body["capabilities"]["pools"] == [str(pool["id"])]
    assert "forged-pool" not in json.dumps(body)


def test_register_with_no_membership_stamps_empty(client, db, transport):
    """``[]`` not absent: an agent-supplied value must be OVERWRITTEN even
    when the truthful answer is 'no pools'. A fresh owner/machine, not the
    module's shared ``machine`` fixture — relying on "no other test ever
    gives the shared fixture a pool" is a latent order dependency, not a
    guarantee."""
    owner = _new_user(db)
    node_id = _node_id("pool-register-empty")
    _, token = _enrol(db, owner, node_id)

    client.post(
        "/v1alpha1/nodes/register",
        json={"schema_version": "v1alpha1", "node_id": node_id,
              "hostname": "h", "capabilities": {"pools": ["forged-pool"]}},
        headers={"Authorization": f"Bearer {token}"},
    )
    body = json.loads(transport.last.read())
    assert body["capabilities"]["pools"] == []


def test_heartbeat_carries_the_membership_refresh(client, db, transport):
    owner = _new_user(db)
    node_id = _node_id("pool-heartbeat")
    _, token = _enrol(db, owner, node_id)
    pool = dbmod.create_pool(db, name=f"team-{RUN_MARKER}", owner_id=owner)

    client.post(
        f"/v1alpha1/nodes/{node_id}/heartbeat",
        json={"schema_version": "v1alpha1", "node_id": node_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    body = json.loads(transport.last.read())
    assert body["pools"] == [str(pool["id"])]


def test_heartbeat_scrubs_a_forged_nested_capabilities_pools_too(client, db, transport):
    """``NodeHeartbeat`` has no ``capabilities.pools`` field today, but an
    agent that includes one anyway must not get it forwarded unscrubbed —
    today's schema is the only thing standing between that and a real
    forgery landing wherever the coordinator adds the field next."""
    owner = _new_user(db)
    node_id = _node_id("pool-heartbeat-nested")
    _, token = _enrol(db, owner, node_id)
    pool = dbmod.create_pool(db, name=f"team-{RUN_MARKER}", owner_id=owner)

    client.post(
        f"/v1alpha1/nodes/{node_id}/heartbeat",
        json={"schema_version": "v1alpha1", "node_id": node_id,
              "capabilities": {"pools": ["forged-pool"]}},
        headers={"Authorization": f"Bearer {token}"},
    )
    body = json.loads(transport.last.read())
    assert body["pools"] == [str(pool["id"])]
    assert body["capabilities"]["pools"] == [str(pool["id"])]
    assert "forged-pool" not in json.dumps(body)


def test_register_fails_closed_when_pools_lookup_raises(
    client, machine, transport, monkeypatch
):
    """Pins the except branch as a STAMP, not a skip. `dbmod` is imported by
    name in `app.py` and the attribute is resolved at call time, so
    monkeypatching it here reaches the same lookup the route calls —
    without this, a refactor that turned `except: pools = []` into
    `except: pass` would forward the agent's forged pools untouched and
    every other test in this file would stay green."""
    def raiser(*_a, **_kw):
        raise RuntimeError("simulated pool lookup failure")

    monkeypatch.setattr(dbmod, "pool_ids_for_machine_owner", raiser)

    client.post(
        "/v1alpha1/nodes/register",
        json={"schema_version": "v1alpha1", "node_id": machine["node_id"],
              "hostname": "h", "capabilities": {"pools": ["forged-pool"]}},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    body = json.loads(transport.last.read())
    assert body["capabilities"]["pools"] == []
    assert "forged-pool" not in json.dumps(body)


def test_heartbeat_fails_closed_when_pools_lookup_raises(
    client, machine, transport, monkeypatch
):
    def raiser(*_a, **_kw):
        raise RuntimeError("simulated pool lookup failure")

    monkeypatch.setattr(dbmod, "pool_ids_for_machine_owner", raiser)

    client.post(
        f"/v1alpha1/nodes/{machine['node_id']}/heartbeat",
        json={"schema_version": "v1alpha1", "node_id": machine["node_id"],
              "pools": ["forged-pool"]},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    body = json.loads(transport.last.read())
    assert body["pools"] == []
    assert "forged-pool" not in json.dumps(body)


def test_artifact_put_forwards_raw_bytes_with_delegation(client, machine, transport):
    payload = b"\x00\x01binary-not-json\xff"
    r = client.put(
        "/v1alpha1/artifacts/jobs/j1/task-1/out.bin",
        content=payload,
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    assert r.status_code == 200
    out = transport.last
    assert out.method == "PUT"
    assert out.url.path == "/v1alpha1/artifacts/jobs/j1/task-1/out.bin"
    assert out.read() == payload
    assert out.headers[DELEGATION_HEADER] == machine["node_id"]
    assert out.headers["Authorization"] == f"Bearer {OPERATOR_TOKEN}"


def test_an_artifact_key_cannot_escape_the_artifacts_route(client, machine, transport):
    """Found by attacking this proxy, not by design: Starlette
    percent-decodes a ``{key:path}`` parameter and httpx then resolves dot
    segments when it builds the URL. So ``..%2F..%2Fleases%2Fclaim`` as an
    artifact key would have become a call to a *different* coordinator
    route, carrying the operator credential. The key is validated before it
    is interpolated."""
    auth = {"Authorization": f"Bearer {machine['token']}"}
    for evil in ("..%2F..%2Fleases%2Fclaim", "jobs%2F..%2F..%2Fnodes",
                 "%2Fetc%2Fpasswd", "jobs%2Fj1%3Fnode_id=fn-victim",
                 "jobs%2F%2Ej%2E%2E%2Fx"):
        r = client.put(f"/v1alpha1/artifacts/{evil}", content=b"x", headers=auth)
        assert r.status_code == 400, f"accepted artifact key {evil!r}"
    assert transport.requests == [], (
        "a path-traversal artifact key reached the coordinator: "
        f"{[str(r.url) for r in transport.requests]}"
    )


def test_a_lease_id_cannot_retarget_the_forwarded_request(client, machine, transport):
    auth = {"Authorization": f"Bearer {machine['token']}"}
    r = client.post("/v1alpha1/attempts/..%2F..%2Fleases%2Fclaim/complete",
                    json={}, headers=auth)
    assert r.status_code in (400, 404)
    for out in transport.requests:
        assert out.url.path.startswith("/v1alpha1/attempts/")


def test_two_authorization_headers_are_no_credential_at_all(client, machine,
                                                            transport):
    """Duplicated credentials are an ambiguity the caller controls. Picking
    the first is how a proxy chain ends up disagreeing with its backend
    about who is calling."""
    req = client.build_request("POST", "/v1alpha1/leases/claim", content=b"{}")
    raw_headers = list(req.headers.raw)
    raw_headers.append((b"authorization", f"Bearer {machine['token']}".encode()))
    raw_headers.append((b"authorization", b"Bearer fmk_second-token"))
    req.headers = httpx.Headers(raw_headers)
    r = client.send(req)
    assert r.status_code == 401
    assert transport.requests == []


def test_an_oversized_body_is_refused_before_it_is_forwarded(client, machine,
                                                             transport):
    """Every proxied body is buffered in this process before it is
    forwarded. Without a ceiling, one authenticated volunteer could exhaust
    the API's memory — and the coordinator's own 413 would not help, because
    by then the bytes are already here."""
    auth = {"Authorization": f"Bearer {machine['token']}"}
    r = client.post("/v1alpha1/leases/claim", content=b"x" * (1024 * 1024 + 1),
                    headers=auth)
    assert r.status_code == 413
    assert transport.requests == []


def test_an_exotic_content_type_is_not_repeated_to_the_coordinator(
    client, machine, transport
):
    client.put("/v1alpha1/artifacts/jobs/j1/out.bin", content=b"x",
               headers={"Authorization": f"Bearer {machine['token']}",
                        "Content-Type": "application/octet-stream; boundary=\"a\"b"})
    assert transport.last.headers["Content-Type"] == "application/octet-stream"


def test_checkpoint_routes_are_proxied_with_delegation(client, machine, transport):
    auth = {"Authorization": f"Bearer {machine['token']}"}
    r = client.post("/v1alpha1/jobs/j1/tasks/t1/checkpoints/parts",
                    json={"step": 1}, headers=auth)
    assert r.status_code == 200
    assert transport.last.url.path == "/v1alpha1/jobs/j1/tasks/t1/checkpoints/parts"
    assert transport.last.headers[DELEGATION_HEADER] == machine["node_id"]

    r = client.post("/v1alpha1/jobs/j1/tasks/t1/checkpoints/commit",
                    json={"step": 1}, headers=auth)
    assert r.status_code == 200
    assert transport.last.url.path == "/v1alpha1/jobs/j1/tasks/t1/checkpoints/commit"

    r = client.get("/v1alpha1/jobs/j1/tasks/t1/checkpoints/latest?world_size=2",
                   headers=auth)
    assert r.status_code == 200
    assert transport.last.url.query == b"world_size=2"


def test_coordinator_status_and_body_are_passed_through(client, machine, settings,
                                                        postgres_dsn):
    """A 204 from ``claim`` means "nothing claimable right now" — turning it
    into a 200 would make an idle agent think it had work."""
    def connect():
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        return conn

    class NoContent(RecordingTransport):
        async def handle_async_request(self, request):
            await request.aread()
            self.requests.append(request)
            return httpx.Response(204)

    t = NoContent()
    app = create_cloud_app(settings, connect=connect, transport=t)
    with TestClient(app) as c:
        r = c.post("/v1alpha1/leases/claim", json={},
                   headers={"Authorization": f"Bearer {machine['token']}"})
    assert r.status_code == 204


# ---------------------------------------------------------------------------
# 4. the two credential kinds do not cross over
# ---------------------------------------------------------------------------


def test_a_machine_token_is_not_a_browser_jwt(client, machine, transport):
    auth = {"Authorization": f"Bearer {machine['token']}"}
    for method, path in (("GET", "/v1alpha1/me"),
                         ("GET", "/v1alpha1/machines"),
                         ("POST", "/v1alpha1/device/approve"),
                         ("POST", f"/v1alpha1/machines/{machine['id']}/revoke")):
        r = client.request(method, path, json={"user_code": "AAAAAAAA"}, headers=auth)
        assert r.status_code == 401, f"{method} {path} answered {r.status_code}"
    assert transport.requests == []


def test_a_browser_jwt_is_not_a_machine_token(client, machine, transport):
    token = _browser_jwt(machine["owner"])
    auth = {"Authorization": f"Bearer {token}"}
    for method, path in _agent_routes(client.app):
        r = client.request(method, path, content=b"{}", headers=auth)
        assert r.status_code == 401, f"{method} {path} answered {r.status_code}"
    assert transport.requests == []


def test_an_expired_browser_jwt_is_rejected(client, machine):
    token = _browser_jwt(machine["owner"], exp=time.time() - 1)
    r = client.get("/v1alpha1/machines", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_a_forged_browser_jwt_is_rejected(client, machine):
    forged = jwt.encode({"sub": machine["owner"], "aud": "authenticated",
                         "exp": time.time() + 60}, "wrong-secret", algorithm="HS256")
    r = client.get("/v1alpha1/machines", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# 5. browser routes are owner-scoped
# ---------------------------------------------------------------------------


def test_machines_lists_only_the_callers_machines(client, db):
    alice = _new_user(db)
    bob = _new_user(db)
    alice_id, _ = _enrol(db, alice, _node_id("alice-1"))
    bob_id, _ = _enrol(db, bob, _node_id("bob-1"))

    r = client.get("/v1alpha1/machines",
                   headers={"Authorization": f"Bearer {_browser_jwt(alice)}"})
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()]
    assert alice_id in ids
    assert bob_id not in ids
    assert _node_id("bob-1") not in r.text


def test_machines_never_exposes_a_token_hash(client, db):
    owner = _new_user(db)
    _machine_id, token = _enrol(db, owner, _node_id("hash-check"))
    r = client.get("/v1alpha1/machines",
                   headers={"Authorization": f"Bearer {_browser_jwt(owner)}"})
    assert r.status_code == 200
    assert "token_hash" not in r.text
    assert token not in r.text
    from flashml_cloud_api.auth import hash_machine_token
    assert hash_machine_token(token) not in r.text


def test_revoking_another_users_machine_is_refused_and_has_no_effect(client, db):
    alice = _new_user(db)
    bob = _new_user(db)
    bob_machine, bob_token = _enrol(db, bob, _node_id("bob-2"))

    r = client.post(f"/v1alpha1/machines/{bob_machine}/revoke",
                    headers={"Authorization": f"Bearer {_browser_jwt(alice)}"})
    assert r.status_code == 404  # not 403: 403 would confirm the id exists

    # And Bob's machine still works.
    assert client.post("/v1alpha1/leases/claim", content=b"{}",
                       headers={"Authorization": f"Bearer {bob_token}"}).status_code == 200


def test_owner_can_revoke_and_the_token_dies_at_once(client, db):
    owner = _new_user(db)
    machine_id, token = _enrol(db, owner, _node_id("self-revoke"))
    r = client.post(f"/v1alpha1/machines/{machine_id}/revoke",
                    headers={"Authorization": f"Bearer {_browser_jwt(owner)}"})
    assert r.status_code == 200
    assert client.post("/v1alpha1/leases/claim", content=b"{}",
                       headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_me_upserts_the_profile_on_first_sight(client, db):
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))

    r = client.get("/v1alpha1/me",
                   headers={"Authorization": f"Bearer {_browser_jwt(user_id)}"})
    assert r.status_code == 200
    assert r.json()["id"] == user_id

    with db.cursor() as cur:
        cur.execute("select count(*) as n from public.profiles where id = %s", (user_id,))
        assert cur.fetchone()["n"] == 1

    # Idempotent: a second call must not fail or duplicate.
    assert client.get("/v1alpha1/me",
                      headers={"Authorization": f"Bearer {_browser_jwt(user_id)}"}
                      ).status_code == 200


# ---------------------------------------------------------------------------
# 6. the device flow, over HTTP
# ---------------------------------------------------------------------------


def test_device_flow_end_to_end_over_http(client, db, transport):
    owner = _new_user(db)
    node_id = _node_id("device-flow")

    start = client.post("/v1alpha1/device/code",
                        json={"node_id": node_id, "hostname": "h", "platform": "linux"})
    assert start.status_code == 200
    body = start.json()
    assert set(body) >= {"device_code", "user_code", "verification_uri",
                         "interval", "expires_at"}

    # Before approval, polling must not hand out a token.
    pending = client.post("/v1alpha1/device/token",
                          json={"device_code": body["device_code"]})
    assert pending.status_code == 400
    assert pending.json()["error"] == "authorization_pending"
    assert "token" not in pending.text

    approve = client.post("/v1alpha1/device/approve",
                          json={"user_code": body["user_code"]},
                          headers={"Authorization": f"Bearer {_browser_jwt(owner)}"})
    assert approve.status_code == 200

    redeemed = client.post("/v1alpha1/device/token",
                           json={"device_code": body["device_code"]})
    assert redeemed.status_code == 200
    token = redeemed.json()["token"]
    assert token.startswith("fmk_")

    # Exactly once: a replay gets nothing.
    again = client.post("/v1alpha1/device/token",
                        json={"device_code": body["device_code"]})
    assert again.status_code == 400
    assert token not in again.text

    # And the token now works as a machine credential, as itself.
    r = client.post("/v1alpha1/leases/claim", json={"node_id": "fn-lies"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert transport.last.headers[DELEGATION_HEADER] == node_id


def test_device_code_rejects_a_node_id_that_could_forge_a_header(client):
    """node_id is attacker-supplied at this point and later becomes an HTTP
    header *value*. CR/LF in it would be request splitting, so it is
    refused at the door rather than sanitised later."""
    for bad in ("fn-a\r\nX-FlashML-On-Behalf-Of: fn-victim", "fn a", "", "x" * 300,
                "fn-a\nfoo"):
        r = client.post("/v1alpha1/device/code", json={"node_id": bad})
        assert r.status_code == 400, f"accepted node_id {bad!r}"


def test_device_approve_requires_a_browser_jwt(client):
    assert client.post("/v1alpha1/device/approve",
                       json={"user_code": "AAAAAAAA"}).status_code == 401


def test_approving_an_unknown_user_code_does_not_confirm_anything(client, db):
    owner = _new_user(db)
    r = client.post("/v1alpha1/device/approve", json={"user_code": "ZZZZZZZZ"},
                    headers={"Authorization": f"Bearer {_browser_jwt(owner)}"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 7. the operator credential never escapes
# ---------------------------------------------------------------------------


def test_coordinator_unreachable_leaks_neither_token_nor_internal_url(
    client, machine, transport, caplog
):
    transport.raise_connect_error = (
        f"[Errno 61] Connection refused connecting to {COORDINATOR_URL} "
        f"with Authorization: Bearer {OPERATOR_TOKEN}"
    )
    with caplog.at_level(logging.DEBUG):
        r = client.post("/v1alpha1/leases/claim", content=b"{}",
                        headers={"Authorization": f"Bearer {machine['token']}"})
    assert r.status_code == 502
    assert OPERATOR_TOKEN not in r.text
    assert "coordinator.internal" not in r.text
    assert "Errno 61" not in r.text  # the exception string is not echoed at all

    logged = "\n".join(rec.getMessage() for rec in caplog.records)
    assert OPERATOR_TOKEN not in logged
    assert machine["token"] not in logged


def test_no_response_on_any_route_contains_the_operator_token(client, machine, db):
    owner_jwt = _browser_jwt(machine["owner"])
    responses = [
        client.get("/healthz"),
        client.get("/v1alpha1/me", headers={"Authorization": f"Bearer {owner_jwt}"}),
        client.get("/v1alpha1/machines", headers={"Authorization": f"Bearer {owner_jwt}"}),
        client.post("/v1alpha1/leases/claim", content=b"{}",
                    headers={"Authorization": f"Bearer {machine['token']}"}),
        client.post("/v1alpha1/leases/claim", content=b"{}"),
        client.post("/v1alpha1/device/code", json={"node_id": _node_id("leak")}),
        client.get("/openapi.json"),
    ]
    for r in responses:
        assert OPERATOR_TOKEN not in r.text
        assert JWT_SECRET not in r.text


def test_create_app_selects_the_cloud_app_when_the_env_describes_one(monkeypatch):
    """The wiring itself, not just the factory: a deploy that sets the cloud
    env must not silently keep serving the legacy, unauthenticated app."""
    from flashml_cloud_api.app import create_app

    monkeypatch.setenv("SUPABASE_URL", "https://yualksqjjvlfscbbsygq.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
    monkeypatch.setenv("COORDINATOR_URL", COORDINATOR_URL)
    monkeypatch.setenv("COORDINATOR_OPERATOR_TOKEN", OPERATOR_TOKEN)

    app = create_app()
    paths = {r.path for r in app.routes}
    assert "/v1alpha1/me" in paths
    assert "/v1alpha1/machines" in paths
    # The legacy open node registry is gone in this mode.
    assert "/v1alpha1/nodes" not in paths
    assert any("agent" in (getattr(r, "tags", None) or []) for r in app.routes)


def test_create_app_refuses_to_start_with_half_an_environment(monkeypatch):
    from flashml_cloud_api.app import create_app

    monkeypatch.setenv("SUPABASE_URL", "https://yualksqjjvlfscbbsygq.supabase.co")
    monkeypatch.setenv("COORDINATOR_URL", COORDINATOR_URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.delenv("COORDINATOR_OPERATOR_TOKEN", raising=False)

    with pytest.raises(RuntimeError) as exc:
        create_app()
    assert "COORDINATOR_OPERATOR_TOKEN" in str(exc.value)
    # ...but NOT the JWT secret: it is legacy and optional now that the
    # project signs ES256 and tokens are verified against its JWKS.
    assert "SUPABASE_JWT_SECRET" not in str(exc.value)
    # ...and NOT the service-role key, which no code path reads. See the
    # comment in settings.from_env: requiring it would push the one
    # credential that bypasses every RLS policy into a deploy that has no
    # use for it.
    assert "SUPABASE_SERVICE_KEY" not in str(exc.value)


def test_create_app_starts_without_a_service_role_key(monkeypatch):
    """The service-role key is not a boot requirement.

    Nothing in the API reads it: Postgres is reached over libpq and tokens
    are verified against the JWKS. A deploy should not have to hold a
    full-bypass credential it never uses.
    """
    from flashml_cloud_api.app import create_app

    monkeypatch.setenv("SUPABASE_URL", "https://yualksqjjvlfscbbsygq.supabase.co")
    monkeypatch.setenv("COORDINATOR_URL", COORDINATOR_URL)
    monkeypatch.setenv("COORDINATOR_OPERATOR_TOKEN", OPERATOR_TOKEN)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)

    app = create_app()
    assert "/v1alpha1/me" in {r.path for r in app.routes}


def test_create_app_starts_without_a_jwt_secret(monkeypatch):
    """A Supabase project migrated to asymmetric keys has no shared secret
    to configure at all — requiring one would keep the API from booting."""
    from flashml_cloud_api.app import create_app

    monkeypatch.setenv("SUPABASE_URL", "https://yualksqjjvlfscbbsygq.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
    monkeypatch.setenv("COORDINATOR_URL", COORDINATOR_URL)
    monkeypatch.setenv("COORDINATOR_OPERATOR_TOKEN", OPERATOR_TOKEN)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)

    app = create_app()
    assert "/v1alpha1/me" in {r.path for r in app.routes}


def test_a_jwt_secret_alone_does_not_bring_up_the_cloud_app(monkeypatch):
    """SUPABASE_URL has replaced the secret as the mandatory Supabase input —
    the JWKS is derived from it. A deploy that sets everything EXCEPT the URL
    must not serve authenticated routes it cannot actually verify tokens for;
    `create_app` falls back to the legacy app instead, and `Settings.from_env`
    raises outright (see tests/test_settings.py)."""
    from flashml_cloud_api.app import create_app
    from flashml_cloud_api.settings import Settings

    monkeypatch.setenv("SUPABASE_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
    monkeypatch.setenv("COORDINATOR_URL", COORDINATOR_URL)
    monkeypatch.setenv("COORDINATOR_OPERATOR_TOKEN", OPERATOR_TOKEN)
    monkeypatch.delenv("SUPABASE_URL", raising=False)

    assert "/v1alpha1/me" not in {r.path for r in create_app().routes}
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        Settings.from_env()


def test_machine_token_is_never_logged(client, machine, caplog):
    with caplog.at_level(logging.DEBUG):
        client.post("/v1alpha1/leases/claim", content=b"{}",
                    headers={"Authorization": f"Bearer {machine['token']}"})
    logged = "\n".join(rec.getMessage() for rec in caplog.records)
    assert machine["token"] not in logged
    assert OPERATOR_TOKEN not in logged


def test_heartbeat_records_last_seen_so_the_console_can_show_online(client, machine, db):
    """`machines.last_seen_at` is what the console renders Online/Offline from.

    Nothing ever wrote it. The column existed, the UI read it, and every
    machine therefore showed "Offline / Last seen never" no matter how
    healthily it was heartbeating — while the coordinator, which tracks
    liveness separately for scheduling, saw the same machine as perfectly
    alive. Two liveness views, only one of them written.

    A host who has just enrolled and started their agent sees a dead-looking
    dashboard, which reads as "my machine isn't working" at exactly the moment
    they are deciding whether this thing is worth running.
    """
    # Establish the precondition rather than assume it: the machine fixture is
    # shared, so an earlier test in this module may already have heartbeated.
    with db.cursor() as cur:
        cur.execute(
            "update public.machines set last_seen_at = null where id = %s",
            (machine["id"],),
        )
    assert _fetch_machine(db, machine["id"])["last_seen_at"] is None

    res = client.post(
        f"/v1alpha1/nodes/{machine['node_id']}/heartbeat",
        json={"node_id": machine["node_id"]},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    assert res.status_code == 200

    after = _fetch_machine(db, machine["id"])["last_seen_at"]
    assert after is not None, "the console can never show this machine as online"


def _fetch_machine(db, machine_id):
    with db.cursor() as cur:
        cur.execute("select * from public.machines where id = %s", (machine_id,))
        return cur.fetchone()


# ---------------------------------------------------------------------------
# the attempt ledger: which work the API remembers, and who it credits
#
# Until these landed, `db.record_contributions` had exactly one caller —
# inside `fedavg.on_round` — so a machine was paid for FEDERATED rounds and
# for nothing else. A sweep, which is the workload donated laptops are
# actually good at, credited nobody.
#
# The two hops are tested separately because they fail separately: `claim` is
# where the API learns what a lease covers, and `complete` is where it learns
# the work was accepted. Neither carries both facts.
# ---------------------------------------------------------------------------


def _attempt_rows(db, lease_id: str) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "select machine_id, job_id, task_id, accepted_at"
            "  from public.attempts where lease_id = %s",
            (lease_id,),
        )
        return list(cur.fetchall())


def _contribution_rows(db, job_id: str) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "select machine_id, task_id, duration_s"
            "  from public.contributions where job_id = %s",
            (job_id,),
        )
        return list(cur.fetchall())


def _lease_payload(lease_id: str, job_id: str, task_id: str = "task-000") -> dict:
    return {
        "schema_version": "v1alpha1",
        "lease_id": lease_id,
        "task_id": task_id,
        "job_id": job_id,
        "node_id": "whatever-the-agent-says",
        "attempt_number": 1,
        "deadline": "2026-08-02T00:00:00Z",
        "payload": {},
    }


def _new_lease_id() -> str:
    return f"lease-{uuid.uuid4().hex[:12]}"


def _new_job_id() -> str:
    return f"cjob-{uuid.uuid4().hex[:10]}"


def test_claim_records_the_attempt(client, transport, machine, db):
    lease_id, job_id = _new_lease_id(), _new_job_id()
    transport.status_code = 200
    transport.payload = _lease_payload(lease_id, job_id, "task-007")

    r = client.post(
        "/v1alpha1/leases/claim",
        json={},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    assert r.status_code == 200

    rows = _attempt_rows(db, lease_id)
    assert len(rows) == 1
    assert rows[0]["job_id"] == job_id
    assert rows[0]["task_id"] == "task-007"
    assert str(rows[0]["machine_id"]) == machine["id"]
    # Claiming is not doing. accepted_at is set only when work is credited.
    assert rows[0]["accepted_at"] is None


def test_claim_204_records_nothing(client, transport, machine, db):
    """204 is "no work right now". There is no attempt to remember.

    Asserted as a DELTA over this machine's rows, not an absolute count.
    `machine` is module-scoped and shared with every other test in this
    file, so an absolute count is just whatever the tests that happened to
    run first left behind.
    """
    def _count() -> int:
        with db.cursor() as cur:
            cur.execute(
                "select count(*) as n from public.attempts where machine_id = %s",
                (machine["id"],),
            )
            return cur.fetchone()["n"]

    before = _count()
    transport.status_code = 204
    transport.payload = None

    r = client.post(
        "/v1alpha1/leases/claim",
        json={},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    assert r.status_code == 204
    assert _count() == before


def test_claim_with_unparseable_body_does_not_fail_the_claim(
    client, transport, machine
):
    """The ledger is never allowed to break scheduling.

    A 200 whose body is not a lease — a coordinator change, something in the
    middle rewriting it — must cost the agent nothing. It still gets its
    response; we simply record no attempt.
    """
    transport.status_code = 200
    transport.payload = {"not": "a lease"}

    r = client.post(
        "/v1alpha1/leases/claim",
        json={},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    assert r.status_code == 200


def _claim_one(client, transport, machine, *, lease_id, job_id):
    """Drive a real claim through the proxy so the attempts row exists."""
    transport.status_code = 200
    transport.payload = _lease_payload(lease_id, job_id)
    r = client.post(
        "/v1alpha1/leases/claim",
        json={},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    assert r.status_code == 200


def _complete(client, transport, machine, *, lease_id, body, status=200):
    transport.status_code = status
    transport.payload = body
    return client.post(
        f"/v1alpha1/attempts/{lease_id}/complete",
        json={"output_sha256": "0" * 64},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )


def test_accepted_completion_credits_the_machine(client, transport, machine, db):
    lease_id, job_id = _new_lease_id(), _new_job_id()
    _claim_one(client, transport, machine, lease_id=lease_id, job_id=job_id)

    _complete(client, transport, machine,
              lease_id=lease_id, body={"accepted": True})

    rows = _contribution_rows(db, job_id)
    assert len(rows) == 1
    assert str(rows[0]["machine_id"]) == machine["id"]
    assert rows[0]["task_id"] == "task-000"


def test_rejected_completion_credits_nobody(client, transport, machine, db):
    """200 + accepted:false — the output's sha256 did not match, so the
    attempt was requeued elsewhere.

    This is the case that makes "credit on 2xx" wrong: the status code says
    the HOP succeeded, the body says the WORK did not. Hard rule 4 —
    attempted work is not accepted work.
    """
    lease_id, job_id = _new_lease_id(), _new_job_id()
    _claim_one(client, transport, machine, lease_id=lease_id, job_id=job_id)

    _complete(
        client, transport, machine,
        lease_id=lease_id,
        body={"accepted": False,
              "detail": "output validation failed; attempt requeued"},
    )

    assert _contribution_rows(db, job_id) == []


def test_late_commit_credits_nobody(client, transport, machine, db):
    """200 + a bare accepted:false — another attempt already won this task.

    Two machines can each finish the same task; only one commit wins. Paying
    both would double-count the same unit of work.
    """
    lease_id, job_id = _new_lease_id(), _new_job_id()
    _claim_one(client, transport, machine, lease_id=lease_id, job_id=job_id)

    _complete(client, transport, machine,
              lease_id=lease_id, body={"accepted": False})

    assert _contribution_rows(db, job_id) == []


def test_error_completion_credits_nobody(client, transport, machine, db):
    lease_id, job_id = _new_lease_id(), _new_job_id()
    _claim_one(client, transport, machine, lease_id=lease_id, job_id=job_id)

    _complete(client, transport, machine, lease_id=lease_id,
              body={"detail": "unknown lease"}, status=404)

    assert _contribution_rows(db, job_id) == []


def test_completing_twice_credits_once(client, transport, machine, db):
    """A retried commit is one piece of work, not two."""
    lease_id, job_id = _new_lease_id(), _new_job_id()
    _claim_one(client, transport, machine, lease_id=lease_id, job_id=job_id)

    for _ in range(2):
        _complete(client, transport, machine,
                  lease_id=lease_id, body={"accepted": True})

    assert len(_contribution_rows(db, job_id)) == 1


def test_completion_without_a_claim_credits_nobody(client, transport, machine, db):
    """No attempts row => nothing is known about this lease => no credit."""
    lease_id = _new_lease_id()
    r = _complete(client, transport, machine,
                  lease_id=lease_id, body={"accepted": True})
    assert r.status_code == 200
    assert _attempt_rows(db, lease_id) == []


# ---------------------------------------------------------------------------
# the verification layer, slice 1: timing anomalies on the credit path
#
# ADVISORY ONLY. Every test below asserts twice: what verdict was recorded,
# and that the agent's completion was unaffected by it. The second assertion
# is the one that matters — a verifier that can refuse work takes the fleet
# down on a false positive, so a flag must withhold no credit, change no
# response body and fail no commit.
#
# Design: docs/superpowers/specs/2026-08-03-result-verification-design.md §2.
# ---------------------------------------------------------------------------


def _verification_rows(db, job_id: str) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "select machine_id, task_id, slice, verdict, detail"
            "  from public.verifications where job_id = %s",
            (job_id,),
        )
        return list(cur.fetchall())


def _seed_peer_credits(db, job_id: str, durations: list[float]) -> None:
    """Credit `len(durations)` OTHER machines on `job_id`, so the job has a
    peer baseline this test's machine can be measured against."""
    for i, seconds in enumerate(durations):
        owner = _new_user(db)
        peer = dbmod.insert_machine(
            db, owner_id=owner, node_id=_node_id(f"peer-{uuid.uuid4().hex[:6]}"),
            name="peer", platform="linux",
        )
        with db.cursor() as cur:
            cur.execute(
                "insert into public.contributions"
                "            (machine_id, job_id, task_id, duration_s)"
                "     values (%s, %s, %s, %s)",
                (peer, job_id, f"peer-task-{i:03d}", seconds),
            )


def _backdate_claim(db, lease_id: str, seconds: int) -> None:
    """Make the lease look as though it was claimed `seconds` ago.

    `claim_attempt_credit` computes `now() - claimed_at`, so this is the only
    way to give a test an observation of realistic length — a completion
    driven through the client takes milliseconds.
    """
    with db.cursor() as cur:
        cur.execute(
            "update public.attempts set claimed_at = now() - %s * interval '1 second'"
            " where lease_id = %s",
            (seconds, lease_id),
        )


def test_a_first_of_its_kind_task_is_unknown_never_pass(
    client, transport, machine, db
):
    """No peers, so no distribution, so no verdict — and `unknown` is what
    that gets written as (§8.8). A cold-start fleet verifies nothing by
    timing, and recording `pass` here would turn every brand-new job into a
    clean bill of health for whoever happened to run it first."""
    lease_id, job_id = _new_lease_id(), _new_job_id()
    _claim_one(client, transport, machine, lease_id=lease_id, job_id=job_id)

    _complete(client, transport, machine,
              lease_id=lease_id, body={"accepted": True})

    rows = _verification_rows(db, job_id)
    assert len(rows) == 1
    assert rows[0]["slice"] == "timing"
    assert rows[0]["verdict"] == "unknown"
    assert rows[0]["verdict"] != "pass"
    assert rows[0]["detail"]["reason"] == "too_few_peers"
    assert str(rows[0]["machine_id"]) == machine["id"]
    assert rows[0]["task_id"] == "task-000"


def test_an_instant_return_against_a_real_baseline_is_flagged_and_still_paid(
    client, transport, machine, db
):
    """The whole point of the slice, and the whole point of it being advisory.

    Three peers took ~9s on this job; this completion lands in milliseconds.
    That is flagged — and the machine is credited anyway, because the API
    cannot tell "cheated" from "input was cached and the machine is fast",
    and refusing on that guess would cost a volunteer their machine.
    """
    lease_id, job_id = _new_lease_id(), _new_job_id()
    _seed_peer_credits(db, job_id, [8.0, 9.0, 10.0])
    _claim_one(client, transport, machine, lease_id=lease_id, job_id=job_id)

    r = _complete(client, transport, machine,
                  lease_id=lease_id, body={"accepted": True})

    verdicts = _verification_rows(db, job_id)
    assert [v["verdict"] for v in verdicts] == ["flag"]
    assert verdicts[0]["detail"]["peer_median_s"] == 9.0

    # Nothing enforced: the response is the coordinator's, unaltered...
    assert r.status_code == 200
    assert r.json() == {"accepted": True}
    # ...and the credit was written exactly as it would have been.
    credits = [row for row in _contribution_rows(db, job_id)
               if str(row["machine_id"]) == machine["id"]]
    assert len(credits) == 1


def test_an_ordinary_duration_against_a_real_baseline_passes(
    client, transport, machine, db
):
    lease_id, job_id = _new_lease_id(), _new_job_id()
    _seed_peer_credits(db, job_id, [8.0, 9.0, 10.0])
    _claim_one(client, transport, machine, lease_id=lease_id, job_id=job_id)
    _backdate_claim(db, lease_id, 10)

    _complete(client, transport, machine,
              lease_id=lease_id, body={"accepted": True})

    rows = _verification_rows(db, job_id)
    assert [r["verdict"] for r in rows] == ["pass"]
    assert rows[0]["detail"]["observed_s"] >= 10.0


def test_a_machines_own_earlier_work_is_not_its_own_baseline(
    client, transport, machine, db
):
    """Self-as-peer, end to end, because getting this wrong is invisible.

    This machine has three prior credits on the job at 0.3s each — the
    profile of a node that has been returning instantly all along. If its own
    history counted as peers, the median would be 0.3s, the floor 0.06s, and
    the next instant return would be certified `pass`: a consistently fast
    liar becomes its own baseline. With self excluded there are no peers at
    all, and the honest answer is `unknown`.
    """
    lease_id, job_id = _new_lease_id(), _new_job_id()
    with db.cursor() as cur:
        for i in range(3):
            cur.execute(
                "insert into public.contributions"
                "            (machine_id, job_id, task_id, duration_s)"
                "     values (%s, %s, %s, %s)",
                (machine["id"], job_id, f"earlier-{i:03d}", 0.3),
            )
    _claim_one(client, transport, machine, lease_id=lease_id, job_id=job_id)

    _complete(client, transport, machine,
              lease_id=lease_id, body={"accepted": True})

    rows = _verification_rows(db, job_id)
    assert [r["verdict"] for r in rows] == ["unknown"]
    assert rows[0]["detail"]["peers"] == 0


def test_a_verification_write_that_fails_never_fails_the_completion(
    client, transport, machine, db, monkeypatch
):
    """Best-effort, exactly like `last_seen_at` and the attempt ledger.

    Verification must never break the thing it observes: if the ledger is
    unreachable the agent still gets its answer, and — because the verdict is
    written after the credit — the credit still lands too.
    """
    lease_id, job_id = _new_lease_id(), _new_job_id()
    _seed_peer_credits(db, job_id, [8.0, 9.0, 10.0])
    _claim_one(client, transport, machine, lease_id=lease_id, job_id=job_id)

    def explode(*args, **kwargs):
        raise RuntimeError("verification ledger unavailable")

    monkeypatch.setattr(dbmod, "record_verification", explode)

    r = _complete(client, transport, machine,
                  lease_id=lease_id, body={"accepted": True})

    assert r.status_code == 200
    assert r.json() == {"accepted": True}
    assert _verification_rows(db, job_id) == []
    credits = [row for row in _contribution_rows(db, job_id)
               if str(row["machine_id"]) == machine["id"]]
    assert len(credits) == 1, "a failed verdict must not cost the machine credit"


def test_work_that_was_not_accepted_is_not_verified(client, transport, machine, db):
    """`accepted: false` means the output failed its hash check or another
    attempt already won the task. There is no accepted work to have an
    opinion about, and a row here would put a verdict against a machine for
    work the system threw away."""
    lease_id, job_id = _new_lease_id(), _new_job_id()
    _seed_peer_credits(db, job_id, [8.0, 9.0, 10.0])
    _claim_one(client, transport, machine, lease_id=lease_id, job_id=job_id)

    _complete(client, transport, machine,
              lease_id=lease_id, body={"accepted": False})

    assert _verification_rows(db, job_id) == []


def test_completing_twice_records_one_verdict(client, transport, machine, db):
    """The second completion of a lease credits nothing, so it also judges
    nothing. `verifications` has no unique index, so this rests entirely on
    `claim_attempt_credit` handing out the right to record a lease once —
    which is why that dependency is written down in the migration header."""
    lease_id, job_id = _new_lease_id(), _new_job_id()
    _claim_one(client, transport, machine, lease_id=lease_id, job_id=job_id)

    for _ in range(2):
        _complete(client, transport, machine,
                  lease_id=lease_id, body={"accepted": True})

    assert len(_verification_rows(db, job_id)) == 1
