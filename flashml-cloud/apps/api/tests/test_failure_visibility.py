"""What the API does when its dependencies are broken.

Both behaviours here were absent, and their absence hid a real outage.

DATABASE_URL pointed at a database that did not exist. The API returned 500
on every authenticated route, but:

  * /healthz returned 200 unconditionally, so Render marked the deploy live
    and kept serving it;
  * the 500 carried no CORS headers, because Starlette's ServerErrorMiddleware
    sits OUTSIDE CORSMiddleware, so the browser refused to read the response
    and reported `TypeError: Failed to fetch` — the same message it gives for
    a wrong hostname, a DNS failure, or being offline.

The result was a service that reported healthy while being unusable, failing
with a message that pointed at the network instead of the database.
"""
from __future__ import annotations

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.settings import Settings

WEB_ORIGIN = "https://flashml-web.onrender.com"


class _StubCoordinator(httpx.AsyncBaseTransport):
    """Never reached by these tests — the failures happen before the hop."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True}, request=request)


@pytest.fixture
def transport() -> _StubCoordinator:
    return _StubCoordinator()


@pytest.fixture
def settings(postgres_dsn) -> Settings:
    return Settings(
        supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
        supabase_jwt_secret="test-secret",
        supabase_service_key="service-key-not-used-here",
        coordinator_url="https://coordinator.invalid",
        coordinator_operator_token="operator-token",
        require_auth=True,
        database_url=postgres_dsn,
        console_url="https://console.example",
    )


@pytest.fixture
def broken_db_client(settings, transport):
    """An app whose database cannot be reached — the deployed failure."""

    def connect() -> psycopg.Connection:
        raise psycopg.OperationalError(
            'connection failed: FATAL: database "postgres" does not exist'
        )

    app = create_cloud_app(settings, connect=connect, transport=transport)
    # raise_server_exceptions=False so TestClient returns the 500 the way a
    # real client sees it, instead of re-raising it into the test.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def working_client(settings, postgres_dsn, transport):
    def connect() -> psycopg.Connection:
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        return conn

    app = create_cloud_app(settings, connect=connect, transport=transport)
    with TestClient(app) as c:
        yield c


def test_healthz_fails_when_the_database_is_unreachable(broken_db_client):
    """A health check that ignores the database is not a health check.

    Render keeps the PREVIOUS deploy serving while a new one's health check
    fails, so this is what stops a broken DATABASE_URL from replacing a
    working deployment.
    """
    res = broken_db_client.get("/healthz")
    assert res.status_code == 503
    assert "DATABASE_URL" in res.json()["detail"]


def test_healthz_reports_the_database_when_it_is_reachable(working_client):
    res = working_client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "database": "ok"}


def test_a_500_still_carries_cors_headers(broken_db_client):
    """Without this the browser cannot read the response at all.

    A 500 with no Access-Control-Allow-Origin is not shown to the page as a
    500 — fetch() rejects with `TypeError: Failed to fetch`, which is
    indistinguishable from the API being unreachable. The status and the body
    both have to survive the trip.
    """
    res = broken_db_client.get(
        "/v1alpha1/machines",
        headers={"Origin": WEB_ORIGIN, "Authorization": "Bearer irrelevant"},
    )

    assert res.status_code in (401, 500)
    assert res.headers.get("access-control-allow-origin") is not None, (
        "a browser cannot read this response, so the user sees "
        "'Failed to fetch' instead of the real error"
    )
