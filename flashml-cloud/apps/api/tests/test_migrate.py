"""The migration runner.

These tests must run against a database the runner has never touched, so
they cannot use the `postgres_dsn` database itself — `conftest.py` has
already applied every migration to it by the time any test sees it. What
they borrow from that fixture is the *server*: each test creates its own
database on it with `create database`, and drops it afterwards.

That is also why nothing here needs a mock. The runner's whole job is
transactional behaviour against PostgreSQL — a fake connection would pin
the shape of the code and none of the behaviour that matters.
"""
from __future__ import annotations

import contextlib
import uuid
from pathlib import Path
from typing import Iterator

import psycopg
import pytest

from flashml_cloud_api import migrate

REAL_MIGRATIONS = migrate.MIGRATIONS_DIR

#: The auth schema Supabase provides and a bare local server does not.
#: `0001_initial.sql` has a foreign key into it. Same stub conftest uses.
AUTH_STUB = "create schema auth; create table auth.users (id uuid primary key);"


@contextlib.contextmanager
def scratch_database(server_dsn: str, *, auth_stub: bool = False) -> Iterator[str]:
    """Create an empty database on the same server as `server_dsn`, yield
    its DSN, and drop it afterwards."""
    name = f"migrate_test_{uuid.uuid4().hex[:12]}"
    admin = psycopg.connect(server_dsn, autocommit=True, connect_timeout=5)
    try:
        admin.execute(f'create database "{name}"')
        dsn = server_dsn.rsplit("/", 1)[0] + "/" + name
        if auth_stub:
            with psycopg.connect(dsn, autocommit=True, connect_timeout=5) as conn:
                conn.execute(AUTH_STUB)
        try:
            yield dsn
        finally:
            # Anything still connected would block the drop; nothing in
            # these tests should be, but a failed test can leave a session
            # behind and that must not cascade into the next test.
            admin.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity "
                "where datname = %s and pid <> pg_backend_pid()",
                (name,),
            )
            admin.execute(f'drop database if exists "{name}"')
    finally:
        admin.close()


@contextlib.contextmanager
def connected(dsn: str) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(dsn, autocommit=True, connect_timeout=5)
    try:
        yield conn
    finally:
        conn.close()


def write_migrations(directory: Path, files: dict[str, str]) -> None:
    for name, sql in files.items():
        (directory / name).write_text(sql)


def table_exists(conn: psycopg.Connection, qualified: str) -> bool:
    return conn.execute("select to_regclass(%s) is not null", (qualified,)).fetchone()[0]


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def test_discover_finds_the_four_real_migrations_in_order():
    versions = [m.version for m in migrate.discover(REAL_MIGRATIONS)]
    assert versions == [
        "0001_initial",
        "0002_job_rounds",
        "0003_contributions_unique",
        "0004_attempts",
    ]


# ---------------------------------------------------------------------------
# applying
# ---------------------------------------------------------------------------


def test_first_apply_creates_the_tracking_table(postgres_dsn, tmp_path):
    write_migrations(tmp_path, {"001_a.sql": "create table public.t_a (id int primary key);"})

    with scratch_database(postgres_dsn) as dsn, connected(dsn) as conn:
        assert not table_exists(conn, "public.schema_migrations")

        assert migrate.apply(conn, tmp_path) == ["001_a"]

        assert table_exists(conn, "public.schema_migrations")
        assert migrate.applied(conn) == {
            "001_a": migrate.discover(tmp_path)[0].checksum
        }


def test_all_four_real_migrations_apply_to_an_empty_database(postgres_dsn):
    with scratch_database(postgres_dsn, auth_stub=True) as dsn, connected(dsn) as conn:
        applied = migrate.apply(conn, REAL_MIGRATIONS)

        assert applied == [
            "0001_initial",
            "0002_job_rounds",
            "0003_contributions_unique",
            "0004_attempts",
        ]
        for table in ("profiles", "machines", "device_codes", "jobs",
                      "contributions", "job_rounds", "attempts"):
            assert table_exists(conn, f"public.{table}"), table


def test_a_second_run_against_an_up_to_date_database_is_a_no_op(postgres_dsn):
    with scratch_database(postgres_dsn, auth_stub=True) as dsn, connected(dsn) as conn:
        migrate.apply(conn, REAL_MIGRATIONS)
        before = migrate.applied(conn)

        assert migrate.apply(conn, REAL_MIGRATIONS) == []
        assert migrate.pending(conn, REAL_MIGRATIONS) == []
        assert migrate.applied(conn) == before


def test_each_migration_commits_on_its_own(postgres_dsn, tmp_path):
    """A later migration failing must not undo an earlier one that
    succeeded — otherwise a half-applied deploy is unrecoverable by
    re-running, because the runner would keep retrying from the top."""
    write_migrations(tmp_path, {
        "001_ok.sql": "create table public.t_ok (id int primary key);",
        "002_bad.sql": "create table public.t_bad (id int primary key); "
                       "this is not sql;",
    })

    with scratch_database(postgres_dsn) as dsn, connected(dsn) as conn:
        with pytest.raises(psycopg.Error):
            migrate.apply(conn, tmp_path)

        assert table_exists(conn, "public.t_ok")
        assert not table_exists(conn, "public.t_bad")
        assert list(migrate.applied(conn)) == ["001_ok"]


# ---------------------------------------------------------------------------
# drift
# ---------------------------------------------------------------------------


def test_an_edited_applied_migration_raises_drift_and_applies_nothing(
    postgres_dsn, tmp_path
):
    write_migrations(tmp_path, {
        "001_a.sql": "create table public.t_a (id int primary key);",
        "002_b.sql": "create table public.t_b (id int primary key);",
    })

    with scratch_database(postgres_dsn) as dsn, connected(dsn) as conn:
        migrate.apply(conn, tmp_path)
        before = migrate.applied(conn)

        # 001 is edited after the fact, and an unrelated 003 shows up
        # pending. Drift is a property of the whole set: 003 must not be
        # applied either, even though nothing is wrong with 003.
        (tmp_path / "001_a.sql").write_text(
            "create table public.t_a (id int primary key); -- edited\n"
        )
        write_migrations(tmp_path, {
            "003_c.sql": "create table public.t_c (id int primary key);",
        })

        with pytest.raises(migrate.DriftError) as excinfo:
            migrate.apply(conn, tmp_path)

        assert "001_a" in str(excinfo.value)
        assert not table_exists(conn, "public.t_c")
        assert migrate.applied(conn) == before


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------


def test_baseline_records_versions_without_executing_their_sql(postgres_dsn):
    """The one-time prod operation: the four migrations are already in
    `flashml-poc`, and `0001` would fail against live tables."""
    with scratch_database(postgres_dsn) as dsn, connected(dsn) as conn:
        recorded = migrate.apply(conn, REAL_MIGRATIONS, baseline=True)

        assert recorded == [
            "0001_initial",
            "0002_job_rounds",
            "0003_contributions_unique",
            "0004_attempts",
        ]
        assert set(migrate.applied(conn)) == set(recorded)
        # Not merely "no error": the tables the SQL would have created
        # must not exist. This database never had an `auth` schema, so
        # 0001 could not have succeeded even if it had been executed.
        assert not table_exists(conn, "public.machines")
        assert not table_exists(conn, "public.attempts")
        assert migrate.pending(conn, REAL_MIGRATIONS) == []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_dry_run_exits_non_zero_only_while_something_is_pending(postgres_dsn):
    with scratch_database(postgres_dsn, auth_stub=True) as dsn:
        assert migrate.main(["--dry-run", "--database-url", dsn]) == 1

        # ...and a dry run must not have created anything, not even the
        # tracking table.
        with connected(dsn) as conn:
            assert not table_exists(conn, "public.schema_migrations")

        assert migrate.main(["--database-url", dsn]) == 0
        assert migrate.main(["--dry-run", "--database-url", dsn]) == 0


# ---------------------------------------------------------------------------
# --baseline-through: baselining a PREFIX, not everything pending
#
# `flashml-poc` has 0001-0003 applied by hand but does NOT have 0004_attempts.
# A bare `--baseline` there would record 0004 as applied without ever creating
# public.attempts: the contribution ledger would credit nobody, forever, while
# `--dry-run` reported the database up to date. That is precisely the silent
# drift this runner exists to prevent, so the operation the production gate
# actually needs is "baseline up to and including X".
# ---------------------------------------------------------------------------

BASELINE_TO = "0003_contributions_unique"


def test_baseline_through_stops_after_the_named_version(postgres_dsn):
    with scratch_database(postgres_dsn) as dsn, connected(dsn) as conn:
        recorded = migrate.apply(
            conn, REAL_MIGRATIONS, baseline=True, baseline_through=BASELINE_TO
        )

        assert recorded == [
            "0001_initial",
            "0002_job_rounds",
            "0003_contributions_unique",
        ]
        # Recorded, never executed.
        assert not table_exists(conn, "public.machines")
        # 0004 is untouched and still pending — the whole point.
        assert [m.version for m in migrate.pending(conn, REAL_MIGRATIONS)] == [
            "0004_attempts"
        ]


def test_baseline_through_then_apply_runs_only_the_remainder(postgres_dsn):
    """The real production sequence, end to end: baseline what is already
    there by hand, then apply the rest for real."""
    with scratch_database(postgres_dsn, auth_stub=True) as dsn, connected(dsn) as conn:
        migrate.apply(
            conn, REAL_MIGRATIONS, baseline=True, baseline_through=BASELINE_TO
        )
        # Baselining did not run 0001, so give 0004 the table it references.
        conn.execute("create table public.machines (id uuid primary key)")

        done = migrate.apply(conn, REAL_MIGRATIONS)

        assert done == ["0004_attempts"]
        assert table_exists(conn, "public.attempts")
        assert migrate.pending(conn, REAL_MIGRATIONS) == []


def test_baseline_through_an_unknown_version_is_refused(postgres_dsn):
    """A typo must not silently fall back to baselining everything."""
    with scratch_database(postgres_dsn) as dsn, connected(dsn) as conn:
        with pytest.raises(ValueError, match="0009_nope"):
            migrate.apply(
                conn, REAL_MIGRATIONS, baseline=True, baseline_through="0009_nope"
            )
        assert migrate.applied(conn) == {}


def test_baseline_through_without_baseline_is_refused(postgres_dsn):
    """Misuse, not a silent full apply."""
    with scratch_database(postgres_dsn) as dsn, connected(dsn) as conn:
        with pytest.raises(ValueError, match="baseline"):
            migrate.apply(conn, REAL_MIGRATIONS, baseline_through=BASELINE_TO)


def test_cli_baseline_through_is_wired(postgres_dsn):
    with scratch_database(postgres_dsn) as dsn:
        rc = migrate.main(
            ["--database-url", dsn, "--baseline", "--baseline-through", BASELINE_TO]
        )
        assert rc == 0
        # Still pending afterwards => the CLI honoured the prefix.
        assert migrate.main(["--dry-run", "--database-url", dsn]) == 1
