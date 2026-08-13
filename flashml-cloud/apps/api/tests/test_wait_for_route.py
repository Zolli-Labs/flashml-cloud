"""``GET /v1alpha1/jobs/{job_id}/wait`` — AG-5's bounded long-poll.

Before this route existed, a caller that wanted to know "has this job
finished yet" had exactly one option: spin-poll ``.../events`` (or
``.../{job_id}``) every couple of seconds for as long as it cared, burning a
request — and, if that request used the ``Depends(db_conn)`` idiom every
sibling job route uses, a pooled Postgres connection — on every single tick
even while the answer was still "no". This route answers the same question
with one request that blocks, server-side, until the answer changes or a
bounded timeout elapses, and it does so with the SAME visibility rule every
other job route uses (``fetch_job_for_viewer``: 404 for a job that exists
and the caller cannot see, identical to a job that does not exist).

The property this file cares about most, because it is the one a poll-loop
route can get wrong silently: **the polling loop never holds a database
connection across its own sleep.** ``WaitConnectTracker`` below counts opens
against closes so a test can prove it directly rather than trust the
docstring — see ``test_the_db_connection_is_opened_once_and_never_held_open_
across_a_poll_sleep``.

The coordinator is faked the same way ``test_artifact_mirror_wiring.py``'s
``FakeCoordinatorTransport`` and ``test_job_verifications_route.py``'s
``SilentTransport`` are: an ``httpx.AsyncBaseTransport`` this app's own
``CoordinatorClient`` is built against, so nothing here needs a live
coordinator. ``WaitCoordinatorTransport`` additionally supports queuing a
SEQUENCE of answers per job id (repeating the last one once exhausted), which
is what proves a state transition observed mid-wait, and a "forgotten" /
"unreachable" sentinel, which is what proves a coordinator blip mid-wait
returns 200 with the last known state rather than a hang or a 500.

``WAIT_POLL_INTERVAL_S`` and ``WAIT_TIMEOUT_CAP_S`` are monkeypatched down to
near-zero in every test that needs more than one poll, per the module-level
constants ``flashml_cloud_api.app`` defines specifically so a test can do
this — see the comment beside them.
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

from flashml_cloud_api import app as appmod
from flashml_cloud_api import db as dbmod
from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.settings import Settings

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"
OPERATOR_TOKEN = "op-secret-do-not-leak-3f9c1b"
COORDINATOR_URL = "http://coordinator.internal:8100"


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class WaitCoordinatorTransport(httpx.AsyncBaseTransport):
    """``GET /v1alpha1/jobs/{job_id}`` as a queue of canned answers.

    ``seed(job_id, "RUNNING", "SUCCEEDED")`` makes the first poll answer
    ``RUNNING`` and every poll after that answer ``SUCCEEDED`` — the queue
    is popped down to its last element and then repeats it forever, so a
    test only has to spell out the transitions it cares about.

    ``FORGOTTEN`` and ``UNREACHABLE`` are sentinels standing in for the two
    ways a coordinator round trip can fail to hand back a usable state: a
    404 (a free-tier restart forgot the job, or — unreachable from this
    route in practice, since a federated id never gets this far in a real
    deployment — an id the coordinator never heard of), and the coordinator
    not answering at all.
    """

    FORGOTTEN = object()
    UNREACHABLE = object()

    def __init__(self) -> None:
        self.queues: dict[str, list[object]] = {}
        #: Every job id a poll was actually made for, in order — proves how
        #: many round trips a wait made, and that it stops making them the
        #: moment it has an answer.
        self.calls: list[str] = []

    def seed(self, job_id: str, *states: object) -> None:
        assert states, "seed at least one state"
        self.queues[job_id] = list(states)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        method, path = request.method, request.url.path
        if (method == "GET" and path.startswith("/v1alpha1/jobs/")
                and path.count("/") == 3):
            job_id = path.rsplit("/", 1)[-1]
            self.calls.append(job_id)
            queue = self.queues.get(job_id)
            if not queue:
                return httpx.Response(404, json={"detail": "unknown job"})
            item = queue[0]
            if len(queue) > 1:
                queue.pop(0)
            if item is self.FORGOTTEN:
                return httpx.Response(404, json={"detail": "unregistered"})
            if item is self.UNREACHABLE:
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(200, json={"job_id": job_id, "state": item})
        return httpx.Response(
            404, json={"detail": f"unhandled fake coordinator route: {method} {path}"}
        )


class WaitConnectTracker:
    """Wraps the real ``connect`` factory to count opens against closes.

    ``calls`` is the total number of connections opened for the life of the
    app (including whatever the background reconcile loops open at
    startup — see ``client`` below, which resets this counter after startup
    has already happened, so a test's own assertion is about the request it
    just made and nothing else). ``max_concurrent`` is the largest number of
    connections open AT THE SAME TIME across the tracker's whole life; for
    this route to be honest about never holding a connection across its own
    ``asyncio.sleep``, it must never exceed the concurrency the rest of the
    app legitimately needs (1 is enough to prove the wait loop itself never
    doubles up with the one-shot visibility check, since that check is
    always closed, via ``finally``, before the loop's first sleep).
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self.calls = 0
        self._open = 0
        self.max_concurrent = 0

    def connect(self) -> psycopg.Connection:
        conn = psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        self.calls += 1
        self._open += 1
        self.max_concurrent = max(self.max_concurrent, self._open)
        real_close = conn.close

        def _tracked_close(*a: object, **kw: object) -> None:
            self._open -= 1
            real_close(*a, **kw)

        conn.close = _tracked_close  # type: ignore[method-assign]
        return conn

    @property
    def open_now(self) -> int:
        return self._open

    def reset(self) -> None:
        """Zero the counters. Callers should only do this once ``open_now``
        is back to 0 — i.e. once anything that opened before the reset has
        also finished closing — so nothing already in flight gets counted
        against the test that follows."""
        self.calls = 0
        self.max_concurrent = self._open


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
def transport() -> WaitCoordinatorTransport:
    return WaitCoordinatorTransport()


@pytest.fixture
def tracker(postgres_dsn) -> WaitConnectTracker:
    return WaitConnectTracker(postgres_dsn)


@pytest.fixture
def client(settings, transport, tracker):
    app = create_cloud_app(settings, connect=tracker.connect, transport=transport)
    with TestClient(app) as c:
        # `_rented_capacity_loop` (app.py) runs its first sweep on the
        # startup edge, unconditionally, opening one connection of its own —
        # see `test_anonymous_traffic_costs_no_database_connection` in
        # test_agent_proxy.py, which hit exactly this and fixed it the same
        # way: the sweep is a background task, so "after startup" is not a
        # moment TestClient hands back, and resetting the tracker before that
        # one connection lands would make ``tracker.calls``/``max_concurrent``
        # flaky in the direction that hides a real regression. Wait for it
        # rather than race it, then reset so every test's assertions are
        # about the request IT made.
        deadline = time.monotonic() + 10.0
        while (
            (tracker.calls == 0 or tracker.open_now > 0)
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        tracker.reset()
        yield c


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def fast_poll(monkeypatch):
    """The module constants ``app.py`` defines specifically so a test does
    not have to sit through the real 1s poll interval. Applied to every test
    in this file — a test that wants the real interval simply does not
    exist here, since AG-5's whole point is that nobody should be waiting on
    wall-clock polling."""
    monkeypatch.setattr(appmod, "WAIT_POLL_INTERVAL_S", 0.01)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


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


def _job_id() -> str:
    return f"cjob-{uuid.uuid4().hex[:12]}"


def _seed_job(db, owner_id: str, job_id: str, *, status: str = "PENDING") -> None:
    dbmod.insert_job(
        db, job_id=job_id, owner_id=owner_id, name="job-under-test",
        source=None, spec=None, status=status,
    )


def _wait(client, user_id: str, job_id: str, **params):
    return client.get(
        f"/v1alpha1/jobs/{job_id}/wait", headers=_auth(user_id), params=params
    )


# ---------------------------------------------------------------------------
# 1. immediate answers — the condition is already true at the first look
# ---------------------------------------------------------------------------


def test_a_job_already_in_the_target_state_returns_immediately(
    client, db, transport
):
    owner = _new_user(db)
    job_id = _job_id()
    _seed_job(db, owner, job_id)
    transport.seed(job_id, "SUCCEEDED")

    r = _wait(client, owner, job_id, for_state="SUCCEEDED", timeout_s=25)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job_id"] == job_id
    assert body["state"] == "SUCCEEDED"
    assert body["reached"] is True
    assert body["timed_out"] is False
    assert body["waited_s"] < 1.0
    # One look was enough — the whole point of returning immediately.
    assert transport.calls == [job_id]


def test_a_job_already_terminal_with_no_for_state_reaches_immediately(
    client, db, transport
):
    """No ``for_state`` means "any terminal state" — a job already
    FAILED, CANCELLED, whatever, must not make the caller wait out the
    timeout just because nobody named the exact state."""
    owner = _new_user(db)
    job_id = _job_id()
    _seed_job(db, owner, job_id)
    transport.seed(job_id, "FAILED")

    r = _wait(client, owner, job_id, timeout_s=25)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "FAILED"
    assert body["reached"] is True
    assert body["timed_out"] is False


def test_a_non_terminal_job_with_no_for_state_does_not_reach(client, db, transport):
    """The inverse of the above: RUNNING is not terminal, so with no
    ``for_state`` the wait must NOT report reached — it must time out."""
    owner = _new_user(db)
    job_id = _job_id()
    _seed_job(db, owner, job_id)
    transport.seed(job_id, "RUNNING")

    r = _wait(client, owner, job_id, timeout_s=0)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reached"] is False
    assert body["timed_out"] is True
    assert body["state"] == "RUNNING"


# ---------------------------------------------------------------------------
# 2. the timeout path — never an error, always 200 with the current state
# ---------------------------------------------------------------------------


def test_a_job_that_never_reaches_the_state_times_out_with_200(
    client, db, transport
):
    owner = _new_user(db)
    job_id = _job_id()
    _seed_job(db, owner, job_id)
    transport.seed(job_id, "RUNNING")

    r = _wait(client, owner, job_id, for_state="SUCCEEDED", timeout_s=0)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job_id"] == job_id
    assert body["state"] == "RUNNING"
    assert body["reached"] is False
    assert body["timed_out"] is True
    assert isinstance(body["waited_s"], (int, float))


def test_timeout_s_above_the_cap_is_clamped(client, db, transport, monkeypatch):
    """A caller asking for far longer than the hard cap still gets an
    answer inside (about) the cap, never the value it asked for."""
    monkeypatch.setattr(appmod, "WAIT_TIMEOUT_CAP_S", 0.2)
    owner = _new_user(db)
    job_id = _job_id()
    _seed_job(db, owner, job_id)
    transport.seed(job_id, "RUNNING")

    started = time.monotonic()
    r = _wait(client, owner, job_id, for_state="SUCCEEDED", timeout_s=600)
    elapsed = time.monotonic() - started

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["timed_out"] is True
    assert body["reached"] is False
    # Comfortable margin over the (patched) 0.2s cap, nowhere near the
    # 600s asked for — proves the request was actually clamped and not
    # merely reported as clamped.
    assert elapsed < 5.0
    assert body["waited_s"] < 5.0


# ---------------------------------------------------------------------------
# 3. a real mid-wait transition, observed across multiple polls
# ---------------------------------------------------------------------------


def test_a_state_transition_mid_wait_is_observed_and_reached(
    client, db, transport
):
    owner = _new_user(db)
    job_id = _job_id()
    _seed_job(db, owner, job_id)
    transport.seed(job_id, "RUNNING", "RUNNING", "SUCCEEDED")

    r = _wait(client, owner, job_id, for_state="SUCCEEDED", timeout_s=25)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "SUCCEEDED"
    assert body["reached"] is True
    assert body["timed_out"] is False
    # More than one poll happened — this is the "later poll" the test name
    # promises, not a first-look match.
    assert transport.calls.count(job_id) >= 2


# ---------------------------------------------------------------------------
# 4. a coordinator blip mid-wait — 200, never a hang, never a 500
# ---------------------------------------------------------------------------


def test_a_coordinator_blip_mid_wait_ends_the_wait_with_the_last_known_state(
    client, db, transport
):
    owner = _new_user(db)
    job_id = _job_id()
    _seed_job(db, owner, job_id)
    # A healthy first poll, then the coordinator forgets the job entirely
    # (the free-tier-restart shape ``FakeCoordinatorTransport.forget`` in
    # the artifact-mirror tests models the same way, with a 404).
    transport.seed(job_id, "RUNNING", WaitCoordinatorTransport.FORGOTTEN)

    r = _wait(client, owner, job_id, for_state="SUCCEEDED", timeout_s=25)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reached"] is False
    assert body["timed_out"] is False
    assert body["state"] == "RUNNING"  # the last state actually observed
    assert "note" in body


def test_a_coordinator_that_is_unreachable_from_the_first_poll_still_answers_200(
    client, db, transport
):
    """``CoordinatorClient.forward`` turns a connection failure into an
    ``HTTPException(502)`` for every other route — that is the one thing
    this route must swallow rather than let propagate, or a single flaky
    coordinator hop would turn a long-poll into a 502 instead of the
    "cannot tell you right now" answer the caller can actually act on."""
    owner = _new_user(db)
    job_id = _job_id()
    _seed_job(db, owner, job_id, status="PENDING")
    transport.seed(job_id, WaitCoordinatorTransport.UNREACHABLE)

    r = _wait(client, owner, job_id, for_state="SUCCEEDED", timeout_s=25)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reached"] is False
    assert body["timed_out"] is False
    # Nothing was ever observed from the coordinator, so the last known
    # state falls back to the row's own status at the time of the check.
    assert body["state"] == "PENDING"
    assert "note" in body


# ---------------------------------------------------------------------------
# 5. visibility — a stranger and an unknown id both answer 404
# ---------------------------------------------------------------------------


def test_a_job_owned_by_someone_else_answers_404(client, db, transport):
    owner = _new_user(db)
    stranger = _new_user(db)
    job_id = _job_id()
    _seed_job(db, owner, job_id)
    transport.seed(job_id, "RUNNING")

    r = _wait(client, stranger, job_id, timeout_s=1)
    assert r.status_code == 404, r.text
    # And the coordinator was never even asked — visibility is checked
    # before the first poll, not after a wasted round trip.
    assert transport.calls == []


def test_an_unknown_job_id_answers_404(client, db):
    owner = _new_user(db)
    r = _wait(client, owner, f"cjob-{uuid.uuid4().hex[:12]}", timeout_s=1)
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# 6. the connection discipline the whole route exists to enforce
# ---------------------------------------------------------------------------


def test_the_db_connection_is_opened_once_and_never_held_open_across_a_poll_sleep(
    client, db, transport, tracker
):
    """The property CLAUDE.md-for-this-task calls CRITICAL: a long-poll must
    not pin a pooled connection for the length of the wait. This route's
    design opens exactly one connection, for the one-time visibility check,
    and closes it (via ``finally``) before the poll loop's first sleep — the
    loop itself talks to the coordinator, never the database. A multi-poll
    wait is the case that would catch a regression back to
    ``Depends(db_conn)`` (which holds a connection for the whole request,
    sleeps and all): ``calls`` would jump well above 1, or
    ``max_concurrent`` would show more than one connection open at once.
    """
    owner = _new_user(db)
    job_id = _job_id()
    _seed_job(db, owner, job_id)
    # Three polls before reaching the target — several sleeps for a held
    # connection to have been sitting through, if one were held.
    transport.seed(job_id, "RUNNING", "RUNNING", "RUNNING", "SUCCEEDED")

    r = _wait(client, owner, job_id, for_state="SUCCEEDED", timeout_s=25)
    assert r.status_code == 200, r.text
    assert r.json()["reached"] is True
    assert transport.calls.count(job_id) >= 3

    assert tracker.calls == 1  # the one visibility check — never per poll
    assert tracker.max_concurrent <= 1  # never two connections open at once
