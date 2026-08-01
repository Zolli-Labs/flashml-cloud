"""Device-code enrolment: the security properties, pinned against a real
Postgres database.

A device-flow test against a mock proves very little — the properties
that matter (exactly-once redemption, revocation taking effect
immediately, node_id impersonation being refused) are properties of the
database's transactional behaviour as much as of the Python. So every
test in this file that touches state runs against the real Supabase
project (`yualksqjjvlfscbbsygq`), never a mock or an in-memory stand-in.

Wiring: set TEST_DATABASE_URL (or DATABASE_URL) to a Postgres connection
string for that project. If neither is set, every test that needs the
database is individually skipped via the `db` fixture below, with an
explicit reason — not silently faked. (See
docs/superpowers/plans/.task-3-report.md for why this repo's sandbox
could not wire that connection up automatically, and for the equivalent
verification performed directly against the real project instead.)

Isolation: every row created here uses a node_id/email namespaced with a
per-run random marker, so concurrent runs and the rest of the project's
data never collide. The `test_user`/`test_user2` fixtures create a real
auth.users row (profiles.id is a foreign key to it) and delete everything
they created — auth.users, profiles, machines, device_codes — on
teardown, in FK-safe order.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from flashml_cloud_api import enrolment
from flashml_cloud_api.enrolment import (
    DeviceCodeExpired,
    DeviceCodeNotFound,
    NodeAlreadyEnrolled,
)

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
RUN_MARKER = uuid.uuid4().hex[:12]

_SKIP_REASON = (
    "No TEST_DATABASE_URL/DATABASE_URL configured in this environment — "
    "this test needs a real Postgres connection to Supabase project "
    "yualksqjjvlfscbbsygq. Skipped, not mocked; see "
    "docs/superpowers/plans/.task-3-report.md."
)


def _node_id(name: str) -> str:
    return f"test-{RUN_MARKER}-{name}"


@pytest.fixture(scope="module")
def db():
    if not DATABASE_URL:
        pytest.skip(_SKIP_REASON)
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def _make_test_user(db, tag: str) -> str:
    """Insert a real auth.users row (the FK profiles.id requires) plus
    its profiles row, and return the new user id."""
    user_id = str(uuid.uuid4())
    email = f"test-{RUN_MARKER}-{tag}@example.invalid"
    with db.cursor() as cur:
        cur.execute(
            """
            insert into auth.users
                (id, instance_id, aud, role, email, encrypted_password,
                 email_confirmed_at, created_at, updated_at,
                 raw_app_meta_data, raw_user_meta_data, is_sso_user, is_anonymous)
            values
                (%s, '00000000-0000-0000-0000-000000000000', 'authenticated',
                 'authenticated', %s, '', now(), now(), now(), '{}'::jsonb,
                 '{}'::jsonb, false, false)
            """,
            (user_id, email),
        )
        cur.execute(
            "insert into public.profiles (id, display_name) values (%s, %s)",
            (user_id, f"test-{RUN_MARKER}-{tag}"),
        )
    return user_id


def _delete_test_user(db, user_id: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "delete from public.device_codes where node_id like %s",
            (f"test-{RUN_MARKER}-%",),
        )
        cur.execute("delete from public.machines where owner_id = %s", (user_id,))
        cur.execute("delete from public.profiles where id = %s", (user_id,))
        cur.execute("delete from auth.users where id = %s", (user_id,))


@pytest.fixture(scope="module")
def owner(db):
    user_id = _make_test_user(db, "owner")
    yield user_id
    _delete_test_user(db, user_id)


@pytest.fixture(scope="module")
def other_owner(db):
    user_id = _make_test_user(db, "other")
    yield user_id
    _delete_test_user(db, user_id)


# ---------------------------------------------------------------------------
# Pure property — needs no database, always runs.
# ---------------------------------------------------------------------------

def test_user_code_alphabet_excludes_ambiguous_characters():
    """No O/0 or I/1 — someone reads this off a terminal and types it
    into a phone."""
    forbidden = set("O0I1")
    assert forbidden.isdisjoint(set(enrolment.USER_CODE_ALPHABET))
    assert enrolment.USER_CODE_LENGTH == 8


# ---------------------------------------------------------------------------
# Against the real database.
# ---------------------------------------------------------------------------

def test_user_code_is_short_and_unambiguous(db, owner):
    result = enrolment.start_device_code(db, _node_id("code-shape"), "host", "linux")
    assert len(result["user_code"]) == 8
    assert set(result["user_code"]) <= set(enrolment.USER_CODE_ALPHABET)
    assert not (set(result["user_code"]) & set("O0I1"))
    assert result["interval"] > 0
    assert result["expires_at"] > datetime.now(timezone.utc)


def test_code_expires_and_cannot_be_approved(db, owner):
    result = enrolment.start_device_code(db, _node_id("expiry"), "host", "linux")
    with db.cursor() as cur:
        cur.execute(
            "update public.device_codes set expires_at = now() - interval '1 second'"
            " where user_code = %s",
            (result["user_code"],),
        )
    with pytest.raises(DeviceCodeExpired):
        enrolment.approve_device_code(db, result["user_code"], owner)


def test_approve_unknown_code_is_refused(db, owner):
    with pytest.raises(DeviceCodeNotFound):
        enrolment.approve_device_code(db, "NOSUCH99", owner)


def test_redeem_before_approval_returns_none(db, owner):
    result = enrolment.start_device_code(db, _node_id("preapproval"), "host", "linux")
    assert enrolment.redeem_device_code(db, result["device_code"]) is None


def test_redeem_returns_token_exactly_once_and_raw_token_is_never_stored(db, owner):
    result = enrolment.start_device_code(db, _node_id("redeem-once"), "host", "linux")
    enrolment.approve_device_code(db, result["user_code"], owner)

    token = enrolment.redeem_device_code(db, result["device_code"])
    assert token is not None
    assert token.startswith("fmk_")

    second = enrolment.redeem_device_code(db, result["device_code"])
    assert second is None

    machine = enrolment.authenticate_machine(db, token)
    assert machine is not None
    assert machine.node_id == _node_id("redeem-once")

    with db.cursor() as cur:
        cur.execute(
            "select token_hash from public.machines where id = %s", (machine.id,)
        )
        row = cur.fetchone()
    assert row["token_hash"] != token
    from flashml_cloud_api.auth import hash_machine_token

    assert row["token_hash"] == hash_machine_token(token)


def test_approving_the_same_code_twice_does_not_mint_a_second_machine(db, owner):
    result = enrolment.start_device_code(db, _node_id("double-approve"), "host", "linux")
    first_id = enrolment.approve_device_code(db, result["user_code"], owner)
    second_id = enrolment.approve_device_code(db, result["user_code"], owner)
    assert first_id == second_id

    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.machines where node_id = %s",
            (_node_id("double-approve"),),
        )
        assert cur.fetchone()["n"] == 1


def test_authenticate_machine_returns_none_for_revoked(db, owner):
    result = enrolment.start_device_code(db, _node_id("revoke-auth"), "host", "linux")
    machine_id = enrolment.approve_device_code(db, result["user_code"], owner)
    token = enrolment.redeem_device_code(db, result["device_code"])

    assert enrolment.authenticate_machine(db, token) is not None
    assert enrolment.revoke_machine(db, machine_id, owner) is True
    assert enrolment.authenticate_machine(db, token) is None


def test_enrolling_a_node_id_already_bound_to_another_machine_is_refused(db, owner):
    node_id = _node_id("impersonate")
    first = enrolment.start_device_code(db, node_id, "host-a", "linux")
    enrolment.approve_device_code(db, first["user_code"], owner)

    second = enrolment.start_device_code(db, node_id, "host-b", "linux")
    with pytest.raises(NodeAlreadyEnrolled):
        enrolment.approve_device_code(db, second["user_code"], owner)

    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.machines where node_id = %s",
            (node_id,),
        )
        assert cur.fetchone()["n"] == 1


def test_revoke_refuses_when_user_does_not_own_the_machine(db, owner, other_owner):
    result = enrolment.start_device_code(db, _node_id("wrong-owner"), "host", "linux")
    machine_id = enrolment.approve_device_code(db, result["user_code"], owner)
    token = enrolment.redeem_device_code(db, result["device_code"])

    assert enrolment.revoke_machine(db, machine_id, other_owner) is False
    # Untouched: the legitimate token still authenticates.
    assert enrolment.authenticate_machine(db, token) is not None
