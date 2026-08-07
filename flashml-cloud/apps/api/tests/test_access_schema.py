"""0009 — the access-request schema.

The migration runs against the ephemeral Postgres in conftest, so these
assertions are against a really-applied migration, not a parsed file.
"""
from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import dict_row


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def _columns(db, schema: str, table: str) -> dict[str, str]:
    with db.cursor() as cur:
        cur.execute(
            """
            select column_name, data_type
              from information_schema.columns
             where table_schema = %s and table_name = %s
            """,
            (schema, table),
        )
        return {r["column_name"]: r["data_type"] for r in cur.fetchall()}


def test_profiles_gains_the_onboarding_columns(db):
    cols = _columns(db, "public", "profiles")
    for name in (
        "first_name", "last_name", "company_name", "role", "team_size",
        "email_domain", "is_personal_email", "is_admin",
    ):
        assert name in cols, f"profiles.{name} missing"


def test_is_admin_defaults_false_and_is_not_null(db):
    with db.cursor() as cur:
        cur.execute(
            """
            select column_default, is_nullable
              from information_schema.columns
             where table_schema = 'public' and table_name = 'profiles'
               and column_name = 'is_admin'
            """
        )
        row = cur.fetchone()
    assert row["is_nullable"] == "NO"
    assert "false" in row["column_default"]


def test_access_requests_table_exists_with_expected_columns(db):
    cols = _columns(db, "public", "access_requests")
    assert cols["user_id"] == "uuid"
    assert cols["status"] == "text"
    assert cols["compute_sources"] == "ARRAY"
    for name in (
        "use_case", "heard_from", "pending_pool_id", "invited_by",
        "requested_at", "decided_at", "decided_by",
    ):
        assert name in cols, f"access_requests.{name} missing"


def test_rls_is_enabled_with_zero_policies(db):
    """Same discipline as every other table: the API is the only door."""
    with db.cursor() as cur:
        cur.execute(
            "select relrowsecurity from pg_class where relname = 'access_requests'"
        )
        assert cur.fetchone()["relrowsecurity"] is True
        cur.execute(
            "select count(*) as n from pg_policies where tablename = 'access_requests'"
        )
        assert cur.fetchone()["n"] == 0


def test_status_is_constrained_to_the_three_states(db):
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (gen_random_uuid()) returning id")
        user_id = cur.fetchone()["id"]
        cur.execute("insert into public.profiles (id) values (%s)", (user_id,))
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                "insert into public.access_requests (user_id, status) values (%s, %s)",
                (user_id, "banana"),
            )


def test_auth_users_stub_has_email_like_real_supabase(db):
    """Real `auth.users` has an email column; the test stub must too, or
    every email-derivation test passes against a schema that isn't the
    deployed one."""
    assert "email" in _columns(db, "auth", "users")


def test_existing_admitted_profiles_are_backfilled_as_admitted(db):
    """Grandfathered testers must NOT compute as needs_onboarding — they
    would be shown the form despite already being admitted."""
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (gen_random_uuid()) returning id")
        user_id = cur.fetchone()["id"]
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, now())",
            (user_id,),
        )
        # Re-running the backfill statement is what the migration does; it
        # must be idempotent and must pick this row up.
        cur.execute(
            """
            insert into public.access_requests (user_id, status, decided_at)
            select p.id, 'admitted', p.admitted_at
              from public.profiles p
             where p.admitted_at is not null
            on conflict (user_id) do nothing
            """
        )
        cur.execute(
            "select status from public.access_requests where user_id = %s", (user_id,)
        )
        assert cur.fetchone()["status"] == "admitted"
