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
#: `0001_initial.sql` has a foreign key into it. Same stub conftest uses —
#: must stay byte-identical to the one in conftest.py, or this file's own
#: drift-detection tests fail in a way that looks unrelated to the change.
AUTH_STUB = (
    "create schema auth; "
    "create table auth.users (id uuid primary key, email text);"
)


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


def test_discover_finds_every_real_migration_in_order():
    """Ordering and completeness, NOT a fixed list.

    This asserted a hardcoded list of four until 2026-08-03, when adding
    `0005_job_rounds_clipped` broke it — along with four other tests here.
    A guard that fails on every new migration gets "fixed" by pasting the
    new name in, which is editing the expectation to match the code and
    proves nothing. What actually matters is that discovery finds ALL of
    them and hands them back in the order they must be applied.
    """
    versions = [m.version for m in migrate.discover(REAL_MIGRATIONS)]
    on_disk = sorted(p.stem for p in REAL_MIGRATIONS.glob("*.sql"))

    assert versions == on_disk, "discover() must find every migration on disk"
    assert versions == sorted(versions), "and hand them back in applied order"
    # The first is load-bearing: everything else has a foreign key into it.
    assert versions[0] == "0001_initial"


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

        assert applied == sorted(p.stem for p in REAL_MIGRATIONS.glob("*.sql"))
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
    """The one-time prod operation: the migrations are already in
    `flashml-poc`, and `0001` would fail against live tables."""
    with scratch_database(postgres_dsn) as dsn, connected(dsn) as conn:
        recorded = migrate.apply(conn, REAL_MIGRATIONS, baseline=True)

        assert recorded == sorted(p.stem for p in REAL_MIGRATIONS.glob("*.sql"))
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
        # Everything AFTER the baselined prefix stays pending — expressed
        # against the directory so a new migration does not break this.
        expected_pending = [v for v in
                            sorted(p.stem for p in REAL_MIGRATIONS.glob("*.sql"))
                            if v > BASELINE_TO]
        assert [m.version for m in
                migrate.pending(conn, REAL_MIGRATIONS)] == expected_pending


def test_baseline_through_then_apply_runs_only_the_remainder(postgres_dsn):
    """The real production sequence, end to end: baseline what is already
    there by hand, then apply the rest for real."""
    with scratch_database(postgres_dsn, auth_stub=True) as dsn, connected(dsn) as conn:
        migrate.apply(
            conn, REAL_MIGRATIONS, baseline=True, baseline_through=BASELINE_TO
        )
        # Baselining RECORDS without EXECUTING, so nothing the skipped
        # migrations create actually exists. Every later migration that
        # references one of those objects needs a stand-in here.
        #
        # This is not test scaffolding for its own sake — it is the real
        # hazard of the baseline flow in miniature. Baseline too far and the
        # next migration fails on a missing object, which is the *good*
        # outcome; the bad one is `--baseline` over a migration that creates
        # nothing later code references, where the gap stays invisible.
        conn.execute("create table public.profiles (id uuid primary key)") # 0001, for 0007
        # 0028 widens the `status` CHECK and comments the column, so the
        # stand-in needs `status` to exist for either to have something to
        # bind to — the same reason `device_codes` below carries `node_id`.
        conn.execute(                                                        # 0001, for 0004 + 0028
            "create table public.machines ("
            "  id uuid primary key,"
            "  status text not null default 'pending')"
        )
        conn.execute("create table public.jobs (id text primary key)")       # 0001, for 0007
        conn.execute("create table public.job_rounds (id uuid primary key)") # 0002, for 0005
        # 0012 adds `kind`/`credential_id` and relaxes `node_id`, so the
        # stand-in needs node_id to exist for the DROP NOT NULL and the
        # machine-needs-node check constraint to have something to bind to.
        conn.execute(                                                        # 0001, for 0012
            "create table public.device_codes ("
            "  device_code text primary key,"
            "  node_id text not null)"
        )

        done = migrate.apply(conn, REAL_MIGRATIONS)

        assert done == [v for v in
                        sorted(p.stem for p in REAL_MIGRATIONS.glob("*.sql"))
                        if v > BASELINE_TO]
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


def test_tracking_table_has_rls_enabled(postgres_dsn):
    """`schema_migrations` must be locked down like every other table.

    0001_initial.sql enables RLS with no policies on all five of its tables,
    deliberately: that denies every role except the owner and BYPASSRLS
    roles, which is what makes the database API-only. The runner creates a
    table in the same schema, so it has to follow the same rule.

    Supabase flagged this as critical on flashml-dev the first time the
    runner ran there: with RLS off, anyone holding the ANON key — which
    ships inside the browser bundle — could read or rewrite the migration
    ledger, and a forged row makes the runner skip or re-apply a migration.
    """
    with scratch_database(postgres_dsn, auth_stub=True) as dsn, connected(dsn) as conn:
        migrate.apply(conn, REAL_MIGRATIONS)
        row = conn.execute(
            "select relrowsecurity from pg_class"
            " where oid = 'public.schema_migrations'::regclass"
        ).fetchone()
        assert row[0] is True, "schema_migrations is exposed to anon"


# ---------------------------------------------------------------------------
# drift must be visible to --dry-run, and reconcilable when it is benign
#
# Found in CI 2026-08-02: scrubbing project refs out of migration COMMENTS
# changed their checksums, so `apply` refused (correctly). But `--dry-run`
# against the same database had said "Up to date; nothing pending" — it only
# ever compared the SET of versions, never their checksums. A drifted database
# reported healthy, which is a false green on the exact question this tool
# exists to answer.
# ---------------------------------------------------------------------------


def _drift_one(directory: Path) -> None:
    """Append a comment to an applied migration — changes the checksum, not
    the SQL."""
    p = directory / "0001_first.sql"
    p.write_text(p.read_text() + "\n-- an edit after the fact\n")


def test_dry_run_reports_drift_instead_of_reporting_healthy(postgres_dsn, tmp_path):
    write_migrations(tmp_path, {"0001_first.sql": "create table public.a (id int);"})
    with scratch_database(postgres_dsn) as dsn:
        with connected(dsn) as conn:
            migrate.apply(conn, tmp_path)
        _drift_one(tmp_path)

        # The whole point: nothing is PENDING, so the old implementation
        # returned 0 and said the database was fine.
        with connected(dsn) as conn:
            assert migrate.pending(conn, tmp_path) == []

        rc = migrate.main(["--dry-run", "--database-url", dsn, "--dir", str(tmp_path)])
        assert rc != 0, "a drifted database must not report healthy"


def test_reconcile_checksums_makes_a_benign_edit_applyable_again(
    postgres_dsn, tmp_path
):
    write_migrations(tmp_path, {"0001_first.sql": "create table public.a (id int);"})
    with scratch_database(postgres_dsn) as dsn:
        with connected(dsn) as conn:
            migrate.apply(conn, tmp_path)
        _drift_one(tmp_path)

        with connected(dsn) as conn:
            with pytest.raises(migrate.DriftError):
                migrate.apply(conn, tmp_path)

            changed = migrate.reconcile_checksums(conn, tmp_path)
            assert changed == ["0001_first"]

            # Now it is quiet again, and a NEW migration can still be applied.
            assert migrate.apply(conn, tmp_path) == []
        assert migrate.main(["--dry-run", "--database-url", dsn, "--dir", str(tmp_path)]) == 0


def test_reconcile_checksums_is_a_no_op_when_nothing_drifted(postgres_dsn, tmp_path):
    write_migrations(tmp_path, {"0001_first.sql": "create table public.a (id int);"})
    with scratch_database(postgres_dsn) as dsn, connected(dsn) as conn:
        migrate.apply(conn, tmp_path)
        assert migrate.reconcile_checksums(conn, tmp_path) == []
