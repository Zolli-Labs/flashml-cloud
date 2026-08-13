"""The settle path, the registry behind it, and the loop that outlives both.

``test_capacity_reconcile.py`` proves the sweep: which rentals it selects, what
it does to them, and — the newer half — which ones it must never touch. None of
that was reachable from a running deployment. ``release_capacity`` had **zero
callers outside tests**, no code built the ``providers`` dict the sweep takes,
and nothing started the sweep at all. A rental that booted successfully
therefore billed for ever, because ``flashnode`` keeps heartbeating after its
job ends and every liveness input said it was fine.

So this file covers the half that can be wrong while every test next door stays
green:

- the production registry is EMPTY and honest about it — a ``FakeProvider``
  wired in "for now" would answer ``destroyed=True`` about machines at a venue
  that is really billing, and the sweep would close those rows for ever;
- a rental is released when the API observes its job finish, from the two
  routes where that observation actually happens;
- both the settle hook and the sweep ship **log-only**, and one flag arms
  both — a deployment armed on one path and disarmed on the other is not a
  safer half;
- the loop exists in the deployed app, disarmed, and destroys nothing.

The coordinator is an in-memory fake in the shape ``test_artifact_mirror_
wiring.py`` uses. Everything below the routes — selection, teardown, the
credential half — is the real code.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod
from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.capacity import settle as settlemod
from flashml_cloud_api.capacity.provider import FakeProvider, ReleaseOutcome
from flashml_cloud_api.capacity.registry import providers_for
from flashml_cloud_api.capacity.settle import settle_finished_jobs
from flashml_cloud_api.settings import Settings

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"
OPERATOR_TOKEN = "op-secret-do-not-leak-3f9c1b"
COORDINATOR_URL = "http://coordinator.internal:8100"
VENUE = "fake"


# ---------------------------------------------------------------------------
# fakes and fixtures
# ---------------------------------------------------------------------------


@dataclass
class _Venue(FakeProvider):
    """The shipped fake, with a record of what teardown was asked to do.

    A release the settle path skipped and a release that happened look
    identical on the row once the row is gone; they do not look identical
    here, which is the whole assertion in the log-only tests.
    """

    release_calls: list[str] = field(default_factory=list)

    def rent(self) -> str:
        handle = f"{self.venue_id}-{uuid.uuid4().hex[:12]}"
        self._live.add(handle)
        return handle

    async def release(self, *, handle: str) -> ReleaseOutcome:
        self.release_calls.append(handle)
        return await super().release(handle=handle)


class FakeCoordinatorTransport(httpx.AsyncBaseTransport):
    """Enough coordinator to submit a job, list jobs, and finish one."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._jobs: dict[str, dict] = {}
        self._prefix = uuid.uuid4().hex[:10]
        self._next_id = 1

    def finish(self, job_id: str, state: str = "SUCCEEDED") -> None:
        self._jobs[job_id] = dict(self._jobs[job_id], state=state)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        method, path = request.method, request.url.path

        if method == "POST" and path == "/v1alpha1/jobs":
            body = json.loads(request.content or b"{}")
            job_id = f"job-{self._prefix}-{self._next_id:04d}"
            self._next_id += 1
            record = {"job_id": job_id, "spec": body, "state": "RUNNING",
                      "backend": "leases"}
            self._jobs[job_id] = record
            return httpx.Response(201, json=record)

        if method == "GET" and path == "/v1alpha1/jobs":
            return httpx.Response(200, json=list(self._jobs.values()))

        if (method == "GET" and path.startswith("/v1alpha1/jobs/")
                and path.count("/") == 3):
            record = self._jobs.get(path.rsplit("/", 1)[-1])
            if record is None:
                return httpx.Response(404, json={"detail": "no such job"})
            return httpx.Response(200, json=record)

        return httpx.Response(
            404, json={"detail": f"unhandled fake route: {method} {path}"}
        )


def _settings(postgres_dsn: str, *, destroy: bool) -> Settings:
    return Settings(
        supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
        supabase_jwt_secret=JWT_SECRET,
        supabase_service_key="service-key-not-used-here",
        coordinator_url=COORDINATOR_URL,
        coordinator_operator_token=OPERATOR_TOKEN,
        require_auth=True,
        database_url=postgres_dsn,
        console_url="https://console.example",
        rented_capacity_destroy=destroy,
    )


@pytest.fixture
def transport() -> FakeCoordinatorTransport:
    return FakeCoordinatorTransport()


@pytest.fixture
def venue() -> _Venue:
    return _Venue()


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def _make_client(postgres_dsn, transport, providers, *, destroy: bool):
    def connect() -> psycopg.Connection:
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        return conn

    return TestClient(create_cloud_app(
        _settings(postgres_dsn, destroy=destroy), connect=connect,
        transport=transport, capacity_providers=providers,
    ))


@pytest.fixture
def armed_client(postgres_dsn, transport, venue):
    """A deployment an operator has deliberately armed."""
    with _make_client(
        postgres_dsn, transport, {VENUE: venue}, destroy=True
    ) as c:
        yield c


@pytest.fixture
def client(postgres_dsn, transport, venue):
    """The DEFAULT deployment: a venue it can reach, and no permission to
    destroy anything at it."""
    with _make_client(
        postgres_dsn, transport, {VENUE: venue}, destroy=False
    ) as c:
        yield c


@pytest.fixture
def an_owner(db):
    """An admitted account, and a promise to clean up after it.

    ``budget.window_spend_usd`` has no owner or venue filter — it is one global
    ceiling — so a `rented_capacity` row left behind here is a budget refusal
    in a file that has no idea why. Deleting the ``auth.users`` row cascades
    the rest; the explicit deletes make a leak fail HERE.
    """
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id, email) values (%s, %s)",
            (user_id, f"{user_id[:8]}@example.com"),
        )
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, now())",
            (user_id,),
        )
    try:
        yield user_id
    finally:
        with db.cursor() as cur:
            cur.execute(
                "delete from public.rented_capacity where owner_id = %s",
                (user_id,),
            )
            cur.execute(
                "delete from public.machines where owner_id = %s", (user_id,)
            )
            cur.execute("delete from public.pools where owner_id = %s", (user_id,))
            cur.execute("delete from auth.users where id = %s", (user_id,))


@pytest.fixture
def a_pool(db, an_owner):
    return str(
        dbmod.create_pool(db, name="rented-capacity-settle", owner_id=an_owner)["id"]
    )


def _token(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": time.time() + 3600},
        JWT_SECRET, algorithm="HS256",
    )


def _submit(client, token: str) -> str:
    r = client.post(
        "/v1alpha1/jobs",
        json={"apiVersion": "flashml.dev/v1alpha1", "kind": "Job",
              "metadata": {"name": f"settle-{uuid.uuid4().hex[:8]}"},
              "spec": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["job_id"]


def _rent(db, *, owner_id, pool_id, job_id, handle, venue_id=VENUE):
    """A rental against a job, as ``acquire_for_job`` would leave it."""
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.rented_capacity
                (venue_id, state, owner_id, pool_id, job_id, provider_handle,
                 usd_per_hour, acquired_at)
            values (%s, 'ACTIVE', %s, %s, %s, %s, 0.5, now())
            returning id
            """,
            (venue_id, str(owner_id), str(pool_id), job_id, handle),
        )
        return str(cur.fetchone()["id"])


def _state(db, rid):
    with db.cursor() as cur:
        cur.execute(
            "select state, released_at from public.rented_capacity where id = %s",
            (rid,),
        )
        return cur.fetchone()


# ---------------------------------------------------------------------------
# 1. the registry
# ---------------------------------------------------------------------------


def test_the_production_registry_is_empty(postgres_dsn):
    """And that is the honest answer, not a gap to be filled in passing.

    ``FakeProvider`` is the only implementation in this repository, and wiring
    it in would report ``destroyed=True`` for machines at a venue that is
    really billing — closing rows the sweep then never looks at again, which
    is the exact "RELEASED row in front of a live machine" the whole module is
    written to prevent.
    """
    assert providers_for(_settings(postgres_dsn, destroy=False)) == {}


def test_an_uninjected_app_gets_that_empty_registry(postgres_dsn, transport):
    """The wiring, not just the function: a deployment must not acquire a
    provider by accident."""
    with _make_client(postgres_dsn, transport, None, destroy=False) as c:
        assert c.app.state.capacity_providers == {}


def test_a_row_at_an_unreachable_venue_is_left_exactly_where_it_is(
    db, an_owner, a_pool
):
    """What an empty registry does to a rental, and why it is safe. A venue
    with no adapter cannot be settled, so the row stays visible and sweepable
    rather than being closed on an assumption. A stuck row is a cheap, visible
    defect; a closed one is an invoice."""
    rid = _rent(db, owner_id=an_owner, pool_id=a_pool, job_id="job-nowhere",
                handle="h-unreachable")
    settled = asyncio.run(settle_finished_jobs(
        db, {}, job_ids=["job-nowhere"], dry_run=False,
    ))
    assert settled == []
    assert _state(db, rid)["state"] == "ACTIVE"


# ---------------------------------------------------------------------------
# 2. the settle path itself
# ---------------------------------------------------------------------------


def test_a_finished_job_gives_its_machine_back(db, an_owner, a_pool, venue):
    handle = venue.rent()
    rid = _rent(db, owner_id=an_owner, pool_id=a_pool, job_id="job-over",
                handle=handle)
    settled = asyncio.run(settle_finished_jobs(
        db, {VENUE: venue}, job_ids=["job-over"], dry_run=False,
    ))
    assert settled == [rid]
    assert venue.live_handles() == []
    row = _state(db, rid)
    assert row["state"] == "RELEASED"
    assert row["released_at"] is not None


def test_settling_twice_asks_the_venue_once(db, an_owner, a_pool, venue):
    """A page left polling a finished job re-enters this on every poll. The
    second pass must find nothing to do without a marker column of its own —
    a released rental has left the state the query selects."""
    handle = venue.rent()
    _rent(db, owner_id=an_owner, pool_id=a_pool, job_id="job-twice",
          handle=handle)
    for _ in range(3):
        asyncio.run(settle_finished_jobs(
            db, {VENUE: venue}, job_ids=["job-twice"], dry_run=False,
        ))
    assert venue.release_calls == [handle]


def test_one_query_covers_a_whole_page_of_finished_jobs(
    db, an_owner, a_pool, venue
):
    """The list route is polled every two seconds and observes every visible
    job at once. A per-job loop would turn one indexed statement into N at
    exactly the frequency that makes N expensive."""
    jobs = [f"job-page-{i}" for i in range(3)]
    rids = {
        job: _rent(db, owner_id=an_owner, pool_id=a_pool, job_id=job,
                   handle=venue.rent())
        for job in jobs
    }
    settled = asyncio.run(settle_finished_jobs(
        db, {VENUE: venue}, job_ids=jobs, dry_run=False,
    ))
    assert sorted(settled) == sorted(rids.values())
    assert venue.live_handles() == []


def test_settling_is_log_only_unless_told_otherwise(db, an_owner, a_pool, venue):
    """``dry_run`` defaults to True HERE and to False in ``reconcile_rented``,
    and the asymmetry is deliberate: this one is called from request handlers,
    where the cost of somebody forgetting the argument is a machine destroyed
    out of a page load."""
    handle = venue.rent()
    rid = _rent(db, owner_id=an_owner, pool_id=a_pool, job_id="job-quiet",
                handle=handle)
    assert asyncio.run(
        settle_finished_jobs(db, {VENUE: venue}, job_ids=["job-quiet"])
    ) == []
    assert venue.release_calls == []
    assert venue.live_handles() == [handle]
    assert _state(db, rid)["state"] == "ACTIVE"


def test_a_running_jobs_rental_is_never_touched(db, an_owner, a_pool, venue):
    """The caller decides what "finished" means; this asks nothing about
    liveness. So the one thing it must not do is act on a job nobody named."""
    handle = venue.rent()
    rid = _rent(db, owner_id=an_owner, pool_id=a_pool, job_id="job-running",
                handle=handle)
    asyncio.run(settle_finished_jobs(
        db, {VENUE: venue}, job_ids=["some-other-job"], dry_run=False,
    ))
    assert _state(db, rid)["state"] == "ACTIVE"
    assert venue.live_handles() == [handle]


def test_a_broken_settle_never_reaches_the_caller(
    db, an_owner, a_pool, venue, monkeypatch
):
    """It runs inside a route whose job is to answer a user's read. A
    rented-capacity failure that turned a finished job's page into a 500 would
    be strictly worse than one settled by the sweep four minutes later — and
    the sweep is the guarantee anyway."""
    _rent(db, owner_id=an_owner, pool_id=a_pool, job_id="job-boom",
          handle=venue.rent())

    async def _explode(*_a, **_kw):
        raise RuntimeError("the connection went away mid-release")

    monkeypatch.setattr(settlemod, "release_capacity", _explode)
    assert asyncio.run(settle_finished_jobs(
        db, {VENUE: venue}, job_ids=["job-boom"], dry_run=False,
    )) == []

    def _explode_sync(*_a, **_kw):
        raise RuntimeError("the connection went away mid-listing")

    monkeypatch.setattr(settlemod, "rentals_for_jobs", _explode_sync)
    assert asyncio.run(settle_finished_jobs(
        db, {VENUE: venue}, job_ids=["job-boom"], dry_run=False,
    )) == []


# ---------------------------------------------------------------------------
# 3. the hook, from the routes that actually observe a job ending
# ---------------------------------------------------------------------------


def test_opening_a_finished_jobs_page_gives_its_machine_back(
    armed_client, db, an_owner, a_pool, transport, venue
):
    """``GET /v1alpha1/jobs/{id}`` is where a non-federated job is observed to
    have stopped — the same observation the footprint and mirror hooks hang
    off. This is the fourth thing it now does, and the only one that costs
    money to skip."""
    token = _token(an_owner)
    job_id = _submit(armed_client, token)
    handle = venue.rent()
    rid = _rent(db, owner_id=an_owner, pool_id=a_pool, job_id=job_id,
                handle=handle)

    # Still running: the page load must not touch the rental.
    assert armed_client.get(
        f"/v1alpha1/jobs/{job_id}", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 200
    assert _state(db, rid)["state"] == "ACTIVE"

    transport.finish(job_id)
    assert armed_client.get(
        f"/v1alpha1/jobs/{job_id}", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 200
    assert _state(db, rid)["state"] == "RELEASED"
    assert venue.live_handles() == []


def test_the_jobs_list_settles_them_too(
    armed_client, db, an_owner, a_pool, transport, venue
):
    """It is on the list route as well as the detail route for the reason
    ``sync_observed_job_states`` is: a job's outcome — and now its bill — must
    not depend on somebody having opened its own page."""
    token = _token(an_owner)
    job_id = _submit(armed_client, token)
    rid = _rent(db, owner_id=an_owner, pool_id=a_pool, job_id=job_id,
                handle=venue.rent())
    transport.finish(job_id, "FAILED")

    assert armed_client.get(
        "/v1alpha1/jobs", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 200
    assert _state(db, rid)["state"] == "RELEASED"
    assert venue.live_handles() == []


def test_the_default_deployment_reports_and_destroys_nothing(
    client, db, an_owner, a_pool, transport, venue
):
    """**The shipped behaviour.** One flag governs both the hook and the
    sweep, and it is off: the failure mode here is silent and irreversible, so
    an operator arms it deliberately after reading what it would have done."""
    token = _token(an_owner)
    job_id = _submit(client, token)
    handle = venue.rent()
    rid = _rent(db, owner_id=an_owner, pool_id=a_pool, job_id=job_id,
                handle=handle)
    transport.finish(job_id)

    assert client.get(
        f"/v1alpha1/jobs/{job_id}", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 200
    assert _state(db, rid)["state"] == "ACTIVE"
    assert venue.release_calls == []
    assert venue.live_handles() == [handle]


def test_a_finished_job_with_no_rental_costs_one_indexed_lookup(
    armed_client, an_owner, transport
):
    """The common case by a wide margin: no job in this product rents anything
    today. Observing one finish must stay a query that finds nothing, never a
    venue call or an error."""
    token = _token(an_owner)
    job_id = _submit(armed_client, token)
    transport.finish(job_id)
    r = armed_client.get(
        f"/v1alpha1/jobs/{job_id}", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    assert r.json()["state"] == "SUCCEEDED"


# ---------------------------------------------------------------------------
# 4. the loop
# ---------------------------------------------------------------------------


def test_the_deployed_app_runs_a_rented_capacity_reconciler(client):
    """It is the only thing in this deployment that stops a rented machine
    billing when nobody has a page open. A task that is not started is a
    backstop that does not exist."""
    task = client.app.state.rented_capacity_reconciler
    assert isinstance(task, asyncio.Task)
    assert not task.done()


@pytest.mark.parametrize(
    "value, armed",
    [
        (None, False),   # the variable nobody set: the shipped default
        ("true", True),
        ("1", True),
        ("false", False),
        ("ture", False),  # a typo must not arm a destructive loop
        ("", False),
    ],
)
def test_the_destroy_flag_is_opt_in(monkeypatch, value, armed):
    """The setting at the level a deployment reads it. Opposite polarity to
    ``FLASHML_REQUIRE_AUTH`` and on purpose: a flag whose misspelling arms a
    loop that destroys machines is a flag pointed the wrong way."""
    monkeypatch.setenv("FLASHML_REQUIRE_AUTH", "false")
    monkeypatch.delenv("RENTED_CAPACITY_DESTROY", raising=False)
    if value is not None:
        monkeypatch.setenv("RENTED_CAPACITY_DESTROY", value)
    assert Settings.from_env().rented_capacity_destroy is armed
