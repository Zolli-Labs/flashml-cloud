"""The CLI credential lifecycle, pinned against a real Postgres.

The properties that matter here — exactly-once redemption, revocation
taking effect immediately, an unapproved code never yielding a token — are
properties of the database's transactional behaviour as much as of the
Python, so nothing in this file is mocked. Wiring matches
tests/test_enrolment.py: the session-scoped ephemeral Postgres from
conftest.py, with every row namespaced by a per-run marker.

``_make_test_user`` is copied from tests/test_enrolment.py rather than
imported. `tests/` has no `__init__.py`, so it is not an importable
package, and adding one would change how the whole suite is collected —
a much larger change than a duplicated fixture helper.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from flashml_cloud_api import cli_auth, db as dbmod

RUN_MARKER = uuid.uuid4().hex[:12]

_FULL_AUTH_USER_COLUMNS = (
    "instance_id", "aud", "role", "email", "encrypted_password",
    "email_confirmed_at", "created_at", "updated_at",
    "raw_app_meta_data", "raw_user_meta_data", "is_sso_user", "is_anonymous",
)


def _make_test_user(db, tag: str) -> str:
    """Insert a real auth.users row (the FK profiles.id requires) plus
    its profiles row, and return the new user id.

    Against real Supabase, auth.users has the full set of NOT-NULL
    columns below; against the local ephemeral fixture (conftest.py's
    `postgres_dsn`) it is a one-column stand-in (`id uuid primary key`),
    since only the FK target matters for this schema. Introspect which
    columns actually exist rather than hardcoding one shape."""
    user_id = str(uuid.uuid4())
    email = f"test-{RUN_MARKER}-{tag}@example.invalid"
    with db.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns"
            " where table_schema = 'auth' and table_name = 'users'"
        )
        existing = {row["column_name"] for row in cur.fetchall()}
        extra = [c for c in _FULL_AUTH_USER_COLUMNS if c in existing]

        if not extra:
            cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        else:
            values_sql = {
                "instance_id": "'00000000-0000-0000-0000-000000000000'",
                "aud": "'authenticated'",
                "role": "'authenticated'",
                "email": "%s",
                "encrypted_password": "''",
                "email_confirmed_at": "now()",
                "created_at": "now()",
                "updated_at": "now()",
                "raw_app_meta_data": "'{}'::jsonb",
                "raw_user_meta_data": "'{}'::jsonb",
                "is_sso_user": "false",
                "is_anonymous": "false",
            }
            columns_sql = ", ".join(["id", *extra])
            placeholders_sql = ", ".join(["%s", *(values_sql[c] for c in extra)])
            params = [user_id] + ([email] if "email" in extra else [])
            cur.execute(
                f"insert into auth.users ({columns_sql}) values ({placeholders_sql})",
                params,
            )
        cur.execute(
            "insert into public.profiles (id, display_name) values (%s, %s)",
            (user_id, f"test-{RUN_MARKER}-{tag}"),
        )
    return user_id


@pytest.fixture(scope="module")
def db(postgres_dsn):
    url = (
        os.environ.get("TEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or postgres_dsn
    )
    conn = psycopg.connect(url, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def owner(db):
    """A real profiles row. profiles.id is a FK to auth.users, so the user
    must exist first — the same two-step tests/test_enrolment.py does."""
    user_id = _make_test_user(db, f"cli-{RUN_MARKER}")
    yield user_id
    with db.cursor() as cur:
        cur.execute("delete from public.cli_credentials where owner_id = %s", (user_id,))
        cur.execute("delete from public.profiles where id = %s", (user_id,))
        cur.execute("delete from auth.users where id = %s", (user_id,))


def test_a_started_code_yields_nothing_until_someone_approves_it(db):
    started = cli_auth.start_cli_code(db, "laptop")
    assert cli_auth.redeem_cli_code(db, started["device_code"]) is None


def test_the_full_flow_returns_a_usable_fmu_token(db, owner):
    started = cli_auth.start_cli_code(db, "laptop")
    credential_id = cli_auth.approve_cli_code(db, started["user_code"], owner)

    token = cli_auth.redeem_cli_code(db, started["device_code"])
    assert token is not None
    assert token.startswith("fmu_")

    resolved = cli_auth.authenticate_cli(db, token)
    assert resolved is not None
    assert resolved.owner_id == owner
    assert resolved.id == credential_id
    assert resolved.label == "laptop"


def test_a_code_redeems_exactly_once(db, owner):
    started = cli_auth.start_cli_code(db, "laptop")
    cli_auth.approve_cli_code(db, started["user_code"], owner)

    assert cli_auth.redeem_cli_code(db, started["device_code"]) is not None
    assert cli_auth.redeem_cli_code(db, started["device_code"]) is None


def test_an_expired_code_is_refused_at_approval(db, owner):
    started = cli_auth.start_cli_code(db, "laptop")
    with db.cursor() as cur:
        cur.execute(
            "update public.device_codes set expires_at = %s where device_code = %s",
            (datetime.now(timezone.utc) - timedelta(seconds=1), started["device_code"]),
        )
    with pytest.raises(cli_auth.CliCodeExpired):
        cli_auth.approve_cli_code(db, started["user_code"], owner)


def test_an_unknown_user_code_is_refused(db, owner):
    with pytest.raises(cli_auth.CliCodeNotFound):
        cli_auth.approve_cli_code(db, "ZZZZZZZZ", owner)


def test_approving_twice_does_not_mint_a_second_credential(db, owner):
    started = cli_auth.start_cli_code(db, "laptop")
    first = cli_auth.approve_cli_code(db, started["user_code"], owner)
    second = cli_auth.approve_cli_code(db, started["user_code"], owner)
    assert first == second


def test_a_revoked_credential_stops_authenticating_immediately(db, owner):
    started = cli_auth.start_cli_code(db, "laptop")
    credential_id = cli_auth.approve_cli_code(db, started["user_code"], owner)
    token = cli_auth.redeem_cli_code(db, started["device_code"])

    assert dbmod.revoke_cli_credential_row(db, credential_id, owner) is True
    assert cli_auth.authenticate_cli(db, token) is None


def test_revoking_someone_elses_credential_reports_nothing(db, owner):
    started = cli_auth.start_cli_code(db, "laptop")
    credential_id = cli_auth.approve_cli_code(db, started["user_code"], owner)
    stranger = str(uuid.uuid4())
    assert dbmod.revoke_cli_credential_row(db, credential_id, stranger) is False
    # And a garbage id is the same answer, not a 500.
    assert dbmod.revoke_cli_credential_row(db, "not-a-uuid", owner) is False


def test_an_unknown_token_and_no_token_both_resolve_to_none(db):
    assert cli_auth.authenticate_cli(db, None) is None
    assert cli_auth.authenticate_cli(db, "") is None
    assert cli_auth.authenticate_cli(db, "fmu_nope") is None


def test_a_machine_code_is_not_approvable_through_the_cli_path(db, owner):
    """The two flows share a table. They must not share a code."""
    from flashml_cloud_api import enrolment

    started = enrolment.start_device_code(
        db, f"node-{RUN_MARKER}", "host", "linux"
    )
    with pytest.raises(cli_auth.CliCodeNotFound):
        cli_auth.approve_cli_code(db, started["user_code"], owner)
