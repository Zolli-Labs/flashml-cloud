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
"""

import inspect

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
