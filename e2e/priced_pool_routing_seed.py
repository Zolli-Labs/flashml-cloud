"""One-shot DB seeding helper for ``test_priced_pool_routing.py``.

WHY THIS IS A SEPARATE SCRIPT, RUN UNDER A DIFFERENT INTERPRETER. The e2e
suite's own venv (``e2e/.venv``, built by ``make e2e-setup``) intentionally
carries only ``flashruntime``/``flashnode`` plus the sklearn stack — never
``flashml_cloud_api`` and its dependencies (``psycopg``, ``pyjwt``,
``fastapi``'s test tooling). ``test_repo_job_contract.py`` and
``test_archive_parity.py`` work around the same gap by importing a handful
of dependency-free ``flashml_cloud_api`` modules straight off ``sys.path``;
that trick does not extend to ``enrolment``/``db``/``migrate``, which need a
real ``psycopg`` connection. So this script runs under
``flashml-cloud/apps/api/.venv`` instead (already built by ``make setup``,
and confirmed present before this test collects), imports the REAL
``flashml_cloud_api.enrolment``/``migrate`` modules, and does the one-time
setup work a browser + a device-code exchange would otherwise do by hand:

1. Stub Supabase's ``auth`` schema (mirrors
   ``apps/api/tests/conftest.py``'s ``postgres_dsn`` fixture — the same
   ephemeral-Postgres contract, reimplemented here without a psycopg import
   in the caller).
2. Apply the real, unmodified migrations through the same runner that
   migrates dev and prod (``flashml_cloud_api.migrate``).
3. Create four admitted accounts (three hosts, one buyer) and enrol three
   machines for them through the REAL device-code flow
   (``enrolment.start_device_code`` / ``approve_device_code`` /
   ``redeem_device_code`` — the same functions ``apps/api/tests`` use for
   this, e.g. ``test_agent_proxy.py``'s ``_enrol`` helper) — never a
   hand-inserted machine row or a hand-rolled token hash.

Everything the e2e test actually exercises over HTTP — listings, job
submission, routing inspection, real task completion — happens in the test
file itself, in ``e2e/.venv``, against a live server. This script only gets
the world to the state a signed-in owner clicking through onboarding and
approving a device code would have left it in, and prints that state out as
JSON so the caller (a different Python, in a different venv) can use it
without ever importing anything from ``flashml_cloud_api``.

Usage: ``apps/api/.venv/bin/python priced_pool_routing_seed.py <database_url>``
Output: one JSON object on stdout; nothing else goes to stdout.
"""
from __future__ import annotations

import json
import sys
import uuid

import psycopg
from psycopg.rows import dict_row

from flashml_cloud_api import enrolment, migrate


def _new_user(conn: psycopg.Connection, tag: str) -> str:
    """A real ``auth.users`` + ``public.profiles`` (admitted) pair."""
    user_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        cur.execute(
            "insert into public.profiles (id, display_name, admitted_at)"
            " values (%s, %s, now())",
            (user_id, tag),
        )
    return user_id


def _enrol(conn: psycopg.Connection, owner_id: str, node_id: str) -> dict:
    """The real device-code flow, start to finish. Returns
    ``{"id": machine_id, "token": raw_machine_token}``."""
    started = enrolment.start_device_code(conn, node_id, f"host-{node_id}", "linux")
    machine_id = enrolment.approve_device_code(conn, started["user_code"], owner_id)
    token = enrolment.redeem_device_code(conn, started["device_code"])
    assert token is not None, f"redemption failed for {node_id}"
    return {"id": str(machine_id), "token": token}


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: priced_pool_routing_seed.py <database_url>", file=sys.stderr)
        raise SystemExit(2)
    database_url = sys.argv[1]
    run = uuid.uuid4().hex[:10]

    # Two connections, deliberately different row factories: `migrate.apply`
    # (and its internal `_table_exists` helper) index rows positionally, the
    # SAME contract `apps/api/tests/conftest.py`'s `postgres_dsn` fixture
    # gives it — a `dict_row` connection there raises `KeyError: 0`.
    # `enrolment`'s functions do the opposite (column-name access), so
    # everything after migration runs on its own `dict_row` connection —
    # the same split `apps/api/tests/conftest.py` and `test_agent_proxy.py`
    # keep between their `postgres_dsn` fixture and their `db` fixture.
    plain_conn = psycopg.connect(database_url, connect_timeout=5)
    plain_conn.autocommit = True
    try:
        # Same stub `apps/api/tests/conftest.py`'s `postgres_dsn` fixture
        # installs: real Supabase provides the `auth` schema, and locally we
        # stand in for it with only the column migrations actually need.
        with plain_conn.cursor() as cur:
            cur.execute(
                "select 1 from information_schema.schemata"
                " where schema_name = 'auth'"
            )
            if cur.fetchone() is None:
                cur.execute(
                    "create schema auth; "
                    "create table auth.users (id uuid primary key, email text)"
                )

        migrate.apply(plain_conn, migrate.MIGRATIONS_DIR)
    finally:
        plain_conn.close()

    conn = psycopg.connect(database_url, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        host_cheap = _new_user(conn, f"host-cheap-{run}")
        host_expensive = _new_user(conn, f"host-expensive-{run}")
        host_free = _new_user(conn, f"host-free-{run}")
        submitter = _new_user(conn, f"submitter-{run}")

        machine_cheap = _enrol(conn, host_cheap, f"routing-e2e-cheap-{run}")
        machine_expensive = _enrol(conn, host_expensive, f"routing-e2e-expensive-{run}")
        machine_free = _enrol(conn, host_free, f"routing-e2e-free-{run}")

        print(json.dumps({
            "run": run,
            "users": {
                "host_cheap": host_cheap,
                "host_expensive": host_expensive,
                "host_free": host_free,
                "submitter": submitter,
            },
            "machines": {
                "cheap": machine_cheap,
                "expensive": machine_expensive,
                "free": machine_free,
            },
        }))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
