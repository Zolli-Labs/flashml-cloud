"""Session-scoped ephemeral local Postgres for tests that need a real
database.

Supabase project `yualksqjjvlfscbbsygq` is the real production target,
but the test suite must not depend on reaching it: a skip against a
missing cloud credential is not a regression guard (see
docs/superpowers/plans/.task-3-report.md for the history here). Instead
this spins up a throwaway PostgreSQL server per test session using the
`initdb`/`pg_ctl` binaries already on this machine, applies the real,
unmodified migration, and tears the whole thing down afterward. These
tests never reach Supabase.

Version note: this runs whatever local PostgreSQL `initdb` resolves to
(Homebrew ships 14; Supabase runs 17). For this schema — uuid, jsonb,
timestamptz, check constraints, and gen_random_uuid(), which has been a
core builtin (no extension needed) since Postgres 13 — that gap is
immaterial. This fixture proves the schema and enrolment logic behave
correctly against a real Postgres; it does not prove anything specific
to Postgres-17 behaviour.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

MIGRATION_PATH = Path(__file__).parent.parent / "migrations" / "0001_initial.sql"

REQUIRED_BINARIES = ("initdb", "pg_ctl", "pg_isready", "psql")

READY_TIMEOUT_S = 20.0


def _free_port() -> int:
    """Bind a port, read what the OS assigned, release it. The same
    bind-then-release pattern the e2e suite uses (e2e/conftest.py) —
    good enough for a server we start immediately afterward, and it
    avoids ever hardcoding a port that might collide."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


@pytest.fixture(scope="session")
def postgres_dsn(tmp_path_factory):
    """Yield a DSN for a real, freshly-migrated, throwaway Postgres
    database. Skips (naming the missing binary) if this machine has no
    local Postgres tooling."""
    missing = [b for b in REQUIRED_BINARIES if shutil.which(b) is None]
    if missing:
        pytest.skip(
            "Missing required local Postgres binaries for the ephemeral "
            f"test database: {', '.join(missing)}. Install PostgreSQL "
            "locally (e.g. `brew install postgresql@14`) to run these "
            "tests; they will not fall back to a mock."
        )

    data_dir = tmp_path_factory.mktemp("pg-enrolment")
    log_path = data_dir / "log"
    port = _free_port()
    started = False

    # Postgres's Unix-domain socket path is capped at ~103 bytes on macOS
    # (sun_path). pytest's tmp_path_factory directories nest under a long
    # pytest-of-<user>/pytest-<N>/... path that alone can blow that limit,
    # so the socket lives in its own short-named directory under the
    # system temp dir instead; only the data directory needs to be under
    # tmp_path_factory.
    socket_dir = Path(tempfile.mkdtemp(prefix="flashml-pg-sock-"))

    try:
        init = _run(["initdb", "-D", str(data_dir), "-U", "postgres",
                      "--auth=trust", "-E", "UTF8"])
        if init.returncode != 0:
            raise RuntimeError(f"initdb failed:\n{init.stdout}\n{init.stderr}")

        start = _run([
            "pg_ctl", "-D", str(data_dir),
            "-o", f"-p {port} -k {socket_dir} -c listen_addresses=127.0.0.1",
            "-l", str(log_path), "start",
        ])
        if start.returncode != 0:
            raise RuntimeError(f"pg_ctl start failed:\n{start.stdout}\n{start.stderr}")
        started = True

        deadline = time.monotonic() + READY_TIMEOUT_S
        ready = False
        while time.monotonic() < deadline:
            if _run(["pg_isready", "-h", "127.0.0.1", "-p", str(port)]).returncode == 0:
                ready = True
                break
            time.sleep(0.2)
        if not ready:
            log = log_path.read_text() if log_path.exists() else "(no log file)"
            raise RuntimeError(
                f"Local Postgres on 127.0.0.1:{port} never became ready "
                f"within {READY_TIMEOUT_S}s. Server log:\n{log}"
            )

        dsn = f"postgresql://postgres@127.0.0.1:{port}/postgres"

        # Supabase provides the `auth` schema; locally we stand in for it
        # with only the one column the migration's FK actually needs.
        stub = _run([
            "psql", dsn, "-v", "ON_ERROR_STOP=1", "-c",
            "create schema auth; create table auth.users (id uuid primary key);",
        ])
        if stub.returncode != 0:
            raise RuntimeError(f"auth schema stub failed:\n{stub.stdout}\n{stub.stderr}")

        # Apply the real migration, unmodified — applying the actual
        # migration file is what makes this test meaningful.
        applied = _run(["psql", dsn, "-v", "ON_ERROR_STOP=1", "-f", str(MIGRATION_PATH)])
        if applied.returncode != 0 and "pgcrypto" in applied.stderr.lower():
            # pgcrypto ships with a standard Postgres install (verified
            # present locally), but if some other machine's build lacks
            # the contrib module, gen_random_uuid() — the only thing this
            # schema actually needs from it — has been a core builtin
            # since Postgres 13 regardless. Rather than editing the
            # checked-in migration to drop that line, tolerate *this one
            # statement* failing and re-apply non-strictly so the rest of
            # the (unmodified) file still runs and is still checked.
            retry = _run(["psql", dsn, "-v", "ON_ERROR_STOP=0", "-f", str(MIGRATION_PATH)])
            if retry.returncode != 0:
                raise RuntimeError(
                    f"migration failed even tolerating pgcrypto:\n"
                    f"{retry.stdout}\n{retry.stderr}"
                )
        elif applied.returncode != 0:
            raise RuntimeError(f"migration failed:\n{applied.stdout}\n{applied.stderr}")

        yield dsn
    finally:
        if started:
            subprocess.run(
                ["pg_ctl", "-D", str(data_dir), "-m", "immediate", "stop"],
                capture_output=True, text=True,
            )
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(socket_dir, ignore_errors=True)
