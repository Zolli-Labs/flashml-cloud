"""The connection pool, against a real Postgres.

``db.connect`` opened a NEW connection per request, and both deployed
DATABASE_URLs cross a continent to reach Supabase's transaction pooler. Every
authenticated route therefore paid a TCP + TLS + auth handshake before its
first query — 3.0s on ``/v1alpha1/me``, 5.5s on ``/v1alpha1/jobs``, against a
60ms route that touches no database at all.

``db.ConnectionPool`` is the fix, and every property worth pinning here is a
property about what happens when something goes WRONG, because that is where a
pool differs from a factory: a connection returned twice, a route that raises
mid-transaction, a backend the server terminated while the connection sat
idle. A pool that only works on the happy path is slower to notice and worse
than no pool at all.

Real connections throughout. The failure modes being pinned (a session left
``INERROR``, a socket the server has closed) do not exist on a fake.
"""
from __future__ import annotations

import select
import threading
import time

import psycopg
import pytest

from flashml_cloud_api import db as dbmod


class _Settings:
    def __init__(self, dsn: str) -> None:
        self.database_url = dsn


@pytest.fixture()
def pool(postgres_dsn):
    p = dbmod.ConnectionPool(_Settings(postgres_dsn), max_size=3)
    try:
        yield p
    finally:
        p.close()


def _one(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("select 1 as n")
        return cur.fetchone()["n"]


# ---------------------------------------------------------------------------
# the point of the whole change
# ---------------------------------------------------------------------------


def test_a_closed_connection_is_reused_not_reopened(pool):
    first = pool()
    assert _one(first) == 1
    first.close()

    second = pool()
    assert second is first, "the second request opened a second connection"
    assert _one(second) == 1
    second.close()
    assert pool.stats()["opened"] == 1
    assert pool.stats()["reused"] == 1


def test_the_pooled_connection_keeps_the_transaction_pooler_guard(pool):
    """``prepare_threshold=None`` and ``dict_row`` are not optional through
    :6543 — see ``test_db_connect_seam``. A pooled connection is opened by the
    same ``db.connect``, so it carries them; this pins that it still does."""
    conn = pool()
    try:
        assert conn.prepare_threshold is None
        assert conn.autocommit is True
        with conn.cursor() as cur:
            cur.execute("select 2 as n")
            assert cur.fetchone() == {"n": 2}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# the failure modes
# ---------------------------------------------------------------------------


def test_a_route_that_raises_still_returns_its_connection(pool):
    """Every call site closes in a ``finally``; that is what makes the
    exception path safe. If it ever stops being true this fails."""
    with pytest.raises(ZeroDivisionError):
        conn = pool()
        try:
            _one(conn)
            raise ZeroDivisionError("as a route would")
        finally:
            conn.close()

    assert pool.stats()["idle"] == 1
    assert pool.stats()["live"] == 1


def test_a_connection_left_mid_transaction_is_cleaned_before_reuse(pool):
    """The failure that matters: a session handed to the next request while
    it is still ``INERROR`` answers ``current transaction is aborted`` to
    every statement, and the request that suffers it did nothing wrong."""
    conn = pool()
    tx = conn.transaction()
    tx.__enter__()
    with pytest.raises(psycopg.errors.DivisionByZero), conn.cursor() as cur:
        cur.execute("select 1 / 0")
    assert conn.info.transaction_status == psycopg.pq.TransactionStatus.INERROR
    conn.close()  # the unwind never ran; it goes back dirty

    reused = pool()
    try:
        # Rolled back and handed on, or discarded and replaced — the pool
        # decides which by whether the rollback works (an ABANDONED
        # ``transaction()`` block, as here, cannot be rolled back through
        # psycopg's own API and is therefore thrown away). What is not
        # negotiable is the session the next request gets.
        assert reused.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
        assert _one(reused) == 1, "a dirty connection reached the next request"
    finally:
        reused.close()


def test_a_route_raising_inside_a_transaction_leaves_a_reusable_connection(pool):
    """The ORDINARY dirty case, and the one worth reusing rather than
    discarding: a route raises inside ``with db.transaction()``. The context
    manager unwinds, the session is IDLE again, and the same connection serves
    the next request."""
    conn = pool()
    with pytest.raises(psycopg.errors.DivisionByZero):
        with conn.transaction(), conn.cursor() as cur:
            cur.execute("create temporary table t (n int)")
            cur.execute("select 1 / 0")
    conn.close()

    reused = pool()
    try:
        assert reused is conn, "a recoverable connection was thrown away"
        assert _one(reused) == 1
    finally:
        reused.close()


def test_a_connection_the_server_killed_is_discarded_not_handed_out(
    pool, postgres_dsn
):
    """An idle pooled connection can die without us: a pooler recycle, a
    failover, an admin terminating the backend. Detected without a round trip
    (see ``db._socket_still_quiet``) and replaced silently."""
    conn = pool()
    with conn.cursor() as cur:
        cur.execute("select pg_backend_pid() as pid")
        pid = cur.fetchone()["pid"]
    conn.close()

    with psycopg.connect(postgres_dsn, autocommit=True) as killer:
        killer.execute("select pg_terminate_backend(%s)", (pid,))
    # The FIN travels asynchronously; in production the connection has been
    # idle for minutes by the time anyone asks for it again.
    deadline = time.monotonic() + 5
    while not select.select([conn.fileno()], [], [], 0)[0]:
        assert time.monotonic() < deadline, "the backend never actually died"
        time.sleep(0.05)

    replacement = pool()
    try:
        assert replacement is not conn
        assert _one(replacement) == 1
    finally:
        replacement.close()
    assert pool.stats()["discarded"] == 1


def test_closing_twice_does_not_close_a_connection_somebody_else_holds(pool):
    """``contextlib.closing`` around code that also closes, or a dependency
    generator finalised twice. The second close must be a no-op."""
    conn = pool()
    conn.close()
    conn.close()
    assert pool.stats()["idle"] == 1, "a double close filed the connection twice"

    again = pool()
    try:
        assert again is conn
        assert not again.closed
        assert _one(again) == 1
    finally:
        again.close()


def test_close_when_idle_returns_a_pooled_connection(pool):
    """The background loops close through ``close_when_idle``, which holds
    ``conn.lock`` while it does. Returning to the pool must not need that lock
    for anything — a rollback there would deadlock every background sweep."""
    conn = pool()
    assert dbmod.close_when_idle(conn) is True
    assert pool.stats()["idle"] == 1
    assert not conn.closed


# ---------------------------------------------------------------------------
# the ceiling
# ---------------------------------------------------------------------------


def test_past_the_ceiling_it_falls_back_instead_of_blocking(postgres_dsn):
    """Exhaustion is worse than a slow page. Past ``max_size`` the pool hands
    out an ordinary unpooled connection — the exact behaviour that shipped
    before it existed — rather than queueing the request."""
    pool = dbmod.ConnectionPool(_Settings(postgres_dsn), max_size=1)
    try:
        held = pool()
        overflow = pool()
        assert overflow is not held
        assert _one(overflow) == 1
        overflow.close()
        assert overflow.closed, "an overflow connection must really close"
        assert pool.stats()["overflowed"] == 1

        held.close()
        assert pool.stats()["idle"] == 1
        assert pool.stats()["live"] == 1
    finally:
        pool.close()


def test_max_size_zero_is_the_kill_switch(postgres_dsn):
    """``FLASHML_DB_POOL_MAX_SIZE=0`` restores connection-per-request without
    a deploy."""
    pool = dbmod.ConnectionPool(_Settings(postgres_dsn), max_size=0)
    try:
        first = pool()
        first.close()
        assert first.closed
        second = pool()
        try:
            assert second is not first
        finally:
            second.close()
        assert pool.stats()["opened"] == 0
    finally:
        pool.close()


def test_the_deployed_app_actually_gets_the_pool(monkeypatch):
    """The regression that would make every line above decorative.

    ``create_app`` used to build its own ``connect`` closure and inject it,
    which is the one thing that opts a deployment OUT of pooling — and it did
    so invisibly, because the app still worked. It just opened a connection
    per request across a continent. Pin the wiring, not only the pool.
    """
    from flashml_cloud_api.app import create_app

    monkeypatch.setenv("SUPABASE_URL", "https://yualksqjjvlfscbbsygq.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
    monkeypatch.setenv("COORDINATOR_URL", "http://coordinator.internal:8100")
    monkeypatch.setenv("COORDINATOR_OPERATOR_TOKEN", "op")

    app = create_app()
    assert isinstance(app.state.db_pool, dbmod.ConnectionPool)
    assert app.state.connect is app.state.db_pool
    # Constructing it must not have opened anything: an API whose database is
    # unreachable still has to boot far enough to answer 503 from /healthz.
    assert app.state.db_pool.stats()["live"] == 0


@pytest.mark.parametrize(
    "dsn, expected",
    [
        ("postgresql://u:p@aws-0-us-east-1.pooler.supabase.com:5432/postgres", 3),
        ("postgresql://u:p@aws-0-us-east-1.pooler.supabase.com:6543/postgres", 10),
        ("postgresql://postgres@127.0.0.1:5432/postgres", 10),
        (None, 10),
    ],
)
def test_the_session_pooler_gets_a_smaller_ceiling(dsn, expected):
    """Fifteen clients for the whole project is the cap that took dev down on
    2026-08-13. A pool holds its slots through the quiet periods too, so ten
    of them against :5432 would be that outage made permanent. Only the
    Supabase pooler host on :5432 is clamped — a local Postgres on 5432 is
    not the thing with the cap."""
    pool = dbmod.ConnectionPool(_Settings(dsn), max_size=10)
    try:
        assert pool.stats()["max_size"] == expected
    finally:
        pool.close()


def test_closing_the_pool_closes_its_idle_connections(postgres_dsn):
    pool = dbmod.ConnectionPool(_Settings(postgres_dsn), max_size=2)
    conn = pool()
    conn.close()
    pool.close()
    assert conn.closed
    pool.close()  # idempotent


def test_a_connection_checked_out_across_a_pool_close_is_not_pooled(postgres_dsn):
    pool = dbmod.ConnectionPool(_Settings(postgres_dsn), max_size=2)
    conn = pool()
    pool.close()
    conn.close()
    assert conn.closed
    assert pool.stats()["idle"] == 0


def test_concurrent_checkout_never_exceeds_the_ceiling(postgres_dsn):
    """Two dozen threads, three slots. The pool may hand out unpooled
    connections past the ceiling but must never OWN more than ``max_size``."""
    pool = dbmod.ConnectionPool(_Settings(postgres_dsn), max_size=3)
    seen: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(5):
            conn = pool()
            try:
                assert _one(conn) == 1
                with lock:
                    seen.append(pool.stats()["live"])
            finally:
                conn.close()

    threads = [threading.Thread(target=worker) for _ in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    try:
        assert max(seen) <= 3
        assert pool.stats()["live"] <= 3
    finally:
        pool.close()
