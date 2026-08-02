"""Apply `migrations/*.sql` to a Postgres database, once each, and record
what was applied in `public.schema_migrations`.

Before this existed, migrations were applied by hand through the Supabase
MCP tool and the answer to "what schema does that database have?" lived in
someone's memory. The tracking table makes it a query, and `--dry-run`
makes it a exit code.

Three properties this file exists to guarantee:

**Each migration commits on its own.** The connection is opened in
autocommit mode and every migration runs inside an explicit
``with conn.transaction():`` block together with its bookkeeping row. A
migration either applies and is recorded, or neither. A failure halfway
down the list leaves the migrations before it applied and recorded, so
re-running resumes rather than starting over — which for a file that is
not idempotent is the difference between a fixable deploy and a restore.

**Drift is checked across the whole set before anything is applied.** If a
file whose version is already recorded no longer hashes to the recorded
checksum, the databases have silently diverged: the schema in front of you
is not the schema the file describes, and nothing that reads either can be
trusted. The runner names the file and applies nothing at all — not even
the unrelated pending migrations, which would only deepen the divergence.

**Recorded is not the same as executed.** ``--baseline`` records versions
without running their SQL. The four migrations here are already live in
`flashml-poc`, applied by hand before this runner existed; `0001` against
those live tables would fail. Baselining is how an existing database joins
the scheme, and it is a deliberate one-time human operation.

Which is why ``--baseline`` alone is dangerous on a database that is
BEHIND, and ``--baseline-through VERSION`` exists. `flashml-poc` has
0001-0003 but not `0004_attempts`; a bare baseline there would record 0004
as applied without creating `public.attempts`, so the contribution ledger
would credit nobody while ``--dry-run`` reported the database up to date.
Baseline the prefix that is really there, then apply the rest for real.

The database URL contains a password. It is read from ``--database-url``
or ``$DATABASE_URL`` and is never printed, not even on failure.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg

#: The checked-in migrations, `apps/api/migrations`.
MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

TABLE = "public.schema_migrations"

CREATE_TABLE = f"""
create table if not exists {TABLE} (
    version     text primary key,
    checksum    text not null,
    applied_at  timestamptz not null default now()
)
"""

# RLS on, with NO policies — the same rule 0001_initial.sql applies to every
# table it creates. In Postgres that denies all access except the table owner
# and BYPASSRLS roles, which is exactly the "database is API-only" design.
#
# This is not decoration. Supabase reported it as critical the first time the
# runner ran against flashml-dev: a table in `public` with RLS off is readable
# AND writable by the `anon` role, whose key ships inside the browser bundle.
# A forged row here makes the runner skip a migration that was never applied,
# or re-apply one that was — the ledger is the thing everything else trusts.
#
# Separate statement rather than part of CREATE TABLE because `create table if
# not exists` is a no-op on an existing table, which would leave a ledger
# created by an earlier version of this file permanently unprotected.
ENABLE_RLS = f"alter table {TABLE} enable row level security"


class DriftError(Exception):
    """An already-applied migration file has changed since it was applied."""


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    sql: str
    checksum: str


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Every `*.sql` in `directory`, sorted by filename.

    Sorted by name, which for the `NNNN_name.sql` convention is the order
    they must be applied in. Globbed rather than listed so a new migration
    is picked up the moment it is written: a checked-in migration nothing
    ever applies is a migration nobody has run.
    """
    return [
        Migration(
            version=path.stem,
            path=path,
            sql=path.read_text(),
            # Bytes, not text. A line-ending change is a change: if two
            # databases were fed different bytes, saying so is the point.
            checksum=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(directory.glob("*.sql"))
    ]


def _table_exists(conn: psycopg.Connection) -> bool:
    row = conn.execute("select to_regclass(%s) is not null", (TABLE,)).fetchone()
    return bool(row and row[0])


def applied(conn: psycopg.Connection) -> dict[str, str]:
    """Version → checksum for everything already applied.

    Empty if the tracking table does not exist yet, so that reading the
    state of a database — which is what ``--dry-run`` does — never writes
    to it.
    """
    if not _table_exists(conn):
        return {}
    rows = conn.execute(f"select version, checksum from {TABLE}").fetchall()
    return {version: checksum for version, checksum in rows}


def pending(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Discovered migrations with no row in the tracking table, in order."""
    already = applied(conn)
    return [m for m in discover(directory) if m.version not in already]


def _drifted(migrations: list[Migration], already: dict[str, str]) -> list[Migration]:
    return [
        m for m in migrations
        if m.version in already and already[m.version] != m.checksum
    ]


def apply(
    conn: psycopg.Connection,
    directory: Path = MIGRATIONS_DIR,
    *,
    baseline: bool = False,
    baseline_through: str | None = None,
) -> list[str]:
    """Apply (or, with `baseline`, merely record) every pending migration.

    Returns the versions it applied, in order — empty when the database is
    already up to date. Raises `DriftError`, having applied nothing, if any
    already-applied migration's file has changed.

    `baseline_through` stops the baseline after the named version, leaving
    everything later genuinely pending. **This is the only safe way to
    baseline a database that is behind.** `flashml-poc` has 0001-0003 applied
    by hand but not `0004_attempts`; a bare `baseline=True` there would record
    0004 as applied without ever creating `public.attempts`, so the
    contribution ledger would credit nobody while `--dry-run` cheerfully
    reported the database up to date. Recording a migration you did not run is
    indistinguishable, later, from having run it.

    `conn` must be in autocommit mode; each migration takes its own
    transaction explicitly.
    """
    if baseline_through is not None and not baseline:
        raise ValueError(
            "baseline_through only means anything with baseline=True — "
            "refusing to guess whether you wanted to apply or to record"
        )

    migrations = discover(directory)

    if baseline_through is not None and baseline_through not in {
        m.version for m in migrations
    }:
        # A typo must not silently fall through to "baseline everything",
        # which is the outcome this argument exists to prevent.
        raise ValueError(
            f"baseline_through names no known migration: {baseline_through!r}. "
            f"Known: {', '.join(m.version for m in migrations)}"
        )

    conn.execute(CREATE_TABLE)
    conn.execute(ENABLE_RLS)
    already = applied(conn)

    # Every applied migration, before any pending one. Drift is a property
    # of the set, not of the next file in the list.
    drifted = _drifted(migrations, already)
    if drifted:
        raise DriftError(
            "migration files changed after they were applied: "
            + ", ".join(m.version for m in drifted)
            + ". The database no longer matches the migrations that "
            "describe it; nothing was applied. Reconcile by hand — do not "
            "edit the checksum."
        )

    done: list[str] = []
    for migration in migrations:
        if migration.version in already:
            continue
        # Before any work, not after: `discover` is sorted, so once we pass
        # the named version everything remaining must stay pending.
        if baseline_through is not None and migration.version > baseline_through:
            break
        with conn.transaction():
            if not baseline:
                conn.execute(migration.sql)
            conn.execute(
                f"insert into {TABLE} (version, checksum) values (%s, %s)",
                (migration.version, migration.checksum),
            )
        done.append(migration.version)
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m flashml_cloud_api.migrate",
        description="Apply pending SQL migrations and record them in "
                    "public.schema_migrations.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres connection URI. Defaults to $DATABASE_URL. Never "
             "logged.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be applied and change nothing. Exits 1 if "
             "anything is pending, 0 if the database is up to date.",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Record pending migrations as applied WITHOUT executing their "
             "SQL. For a database that already has the schema.",
    )
    parser.add_argument(
        "--baseline-through",
        metavar="VERSION",
        help="Stop the baseline after VERSION (e.g. 0003_contributions_unique); "
             "everything later stays genuinely pending. Use this whenever the "
             "database is BEHIND the migration directory — a bare --baseline "
             "would mark those later migrations as done without running them.",
    )
    args = parser.parse_args(argv)

    if not args.database_url:
        # Deliberately says nothing about the value it did find.
        print("No database URL: pass --database-url or set $DATABASE_URL.",
              file=sys.stderr)
        return 2

    with psycopg.connect(args.database_url, autocommit=True) as conn:
        if args.dry_run:
            outstanding = pending(conn, MIGRATIONS_DIR)
            if not outstanding:
                print("Up to date; nothing pending.")
                return 0
            print(f"{len(outstanding)} migration(s) pending:")
            for migration in outstanding:
                print(f"  {migration.version}")
            return 1

        try:
            done = apply(
                conn,
                MIGRATIONS_DIR,
                baseline=args.baseline,
                baseline_through=args.baseline_through,
            )
        except DriftError as exc:
            print(f"Refusing to migrate: {exc}", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(f"Refusing to migrate: {exc}", file=sys.stderr)
            return 2

    verb = "Baselined" if args.baseline else "Applied"
    if not done:
        print("Up to date; nothing to apply.")
    else:
        for version in done:
            print(f"{verb} {version}")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
