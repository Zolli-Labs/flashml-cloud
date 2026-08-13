"""One connect seam, and why that is a rule and not a preference.

``db.connect`` passes ``prepare_threshold=None`` because both deployed
DATABASE_URLs go through Supabase's transaction pooler (:6543), where many
logical sessions share few server connections and a server-side prepared
statement's name outlives the session that made it. psycopg auto-prepares
any statement it has executed five times, so a handler that runs one query
per capability class crosses the threshold inside a single request and
``PREPARE _pg3_0`` collides with another session's leftover
(``DuplicatePreparedStatement`` — the 2026-08-13 dev outage on
``GET /v1alpha1/prices``).

The outage did not come from ``db.connect`` — that seam already carried the
guard. It came from a second, bare ``psycopg.connect`` in ``create_app``'s
factory, which is the connection every real deployment actually uses. The
guard is only real if there is exactly one place that opens app
connections, so these tests pin the *absence of the second site*, not just
the kwarg.

CLOSING IS A SEAM TOO, AND IT IS THE ONE THAT SEGFAULTED CI
-----------------------------------------------------------
``db.close_when_idle`` is ``connect``'s pair for any connection held across
an ``await``. ``asyncio.to_thread`` cannot be cancelled, so a background
loop's ``finally`` closes its connection on one thread while the query it
could not withdraw is still running on another — and
``psycopg.Connection.close`` takes no lock and calls ``PQfinish``. The tests
below pin the BEHAVIOUR (close waits for an in-flight execute), not the
``conn.lock`` attribute it currently uses, so a psycopg version that moves
the lock fails here rather than silently restoring a SIGSEGV.
"""

import inspect
import threading
import time

import psycopg

from flashml_cloud_api import app as appmod
from flashml_cloud_api import db as dbmod


def test_db_connect_disables_auto_prepare():
    source = inspect.getsource(dbmod.connect)
    assert "prepare_threshold=None" in source


def test_app_opens_connections_only_through_the_db_seam():
    # Source-level on purpose: the factory only exists once the cloud env
    # variables are set, and constructing the whole app to inspect a
    # closure would test the harness more than the invariant. The invariant
    # IS textual — no bare psycopg.connect in app.py — and a structural
    # test is how this suite already pins import boundaries.
    source = inspect.getsource(appmod)
    assert "psycopg.connect(" not in source, (
        "app.py opens a raw psycopg connection; route it through "
        "db.connect so the transaction-pooler guard (prepare_threshold="
        "None) cannot drift away from any deployed connection"
    )


def test_the_guard_actually_reaches_psycopg(monkeypatch):
    captured: dict = {}

    def fake_connect(url, **kwargs):
        captured.update(kwargs, url=url)

        class _Conn:
            autocommit = False

        return _Conn()

    monkeypatch.setattr(psycopg, "connect", fake_connect)

    class _Settings:
        database_url = "postgresql://example/db"

    conn = dbmod.connect(_Settings())
    assert captured["prepare_threshold"] is None
    assert conn.autocommit is True


# ---------------------------------------------------------------------------
# closing: db.close_when_idle
# ---------------------------------------------------------------------------


def test_close_waits_for_a_query_running_on_another_thread(postgres_dsn):
    """The property the whole fix is: closing waits out work it cannot cancel.

    A real connection and a real slow query, because the thing being pinned is
    what libpq does when one thread frees the PGconn under another. A fake
    would only pin our own arithmetic.
    """
    conn = psycopg.connect(postgres_dsn, connect_timeout=5)
    conn.autocommit = True
    failure: list[BaseException] = []
    started = threading.Event()

    def query() -> None:
        try:
            with conn.cursor() as cur:
                started.set()
                cur.execute("select pg_sleep(0.5)")
                cur.fetchall()
        except BaseException as exc:  # noqa: BLE001 - the point of the test
            failure.append(exc)

    worker = threading.Thread(target=query, name="slow-query")
    worker.start()
    assert started.wait(timeout=5)
    time.sleep(0.05)  # let the thread get into libpq, not just up to it

    began = time.monotonic()
    assert dbmod.close_when_idle(conn) is True
    waited = time.monotonic() - began
    worker.join(timeout=5)

    assert not failure, (
        "close_when_idle closed the connection under a running query "
        f"({failure[0]!r}). On CI that is a SIGSEGV, not an exception."
    )
    assert waited > 0.2, (
        "close_when_idle returned without waiting for the in-flight query; "
        "psycopg's connection lock is no longer serialising execute() against "
        "this close, so `db.close_when_idle` needs a new mechanism"
    )
    assert conn.closed


def test_a_connection_that_never_goes_idle_is_abandoned_not_closed(postgres_dsn):
    """Leak, do not close. A connection abandoned open costs one session until
    the process exits; closing it under the thread using it risks the process
    itself. psycopg's ``__del__`` only warns, so an abandoned one cannot
    re-enter the race on the garbage collector's thread either."""
    conn = psycopg.connect(postgres_dsn, connect_timeout=5)
    conn.autocommit = True
    try:
        with conn.lock:
            assert dbmod.close_when_idle(conn, timeout_s=0.05) is False
            assert not conn.closed
    finally:
        conn.close()


def test_background_connections_are_never_closed_across_a_thread(postgres_dsn):
    """Structural, and deliberately so: this is the shape of the bug, and the
    next background loop will be written by copying an existing one.

    ``run_in_threadpool(conn.close)`` reads as symmetric with the
    ``run_in_threadpool(app.state.connect)`` above it, which is exactly why it
    was written five times. It is not symmetric — opening happens before any
    ``await`` that can be cancelled, closing happens after one.
    """
    source = inspect.getsource(appmod)
    assert "run_in_threadpool(conn.close)" not in source, (
        "app.py closes a background connection with a bare `conn.close` on a "
        "threadpool thread; use `db.close_when_idle`, which waits for the "
        "`asyncio.to_thread` query that cancellation could not withdraw"
    )
