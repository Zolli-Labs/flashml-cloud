"""Device-code enrolment: the security properties, pinned against a real
Postgres database.

A device-flow test against a mock proves very little — the properties
that matter (exactly-once redemption, revocation taking effect
immediately, node_id impersonation being refused) are properties of the
database's transactional behaviour as much as of the Python. So every
test in this file that touches state runs against a real, freshly
migrated Postgres — never a mock or an in-memory stand-in.

Wiring: by default these run against a session-scoped ephemeral local
Postgres (see the `postgres_dsn` fixture in conftest.py) that this
process starts, migrates, and tears down itself — no cloud credentials
needed, and the real Supabase project is never touched by this file. Set
TEST_DATABASE_URL (or DATABASE_URL) to point these at a different
Postgres instead (e.g. to deliberately test against Supabase). If this
machine has no local `initdb`/`pg_ctl`, the fixture skips naming the
missing binary rather than silently mocking anything.

Isolation: every row created here uses a node_id/email namespaced with a
per-run random marker, so concurrent runs and the rest of the database's
data never collide. The `owner`/`other_owner` fixtures create a real
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

RUN_MARKER = uuid.uuid4().hex[:12]


def _node_id(name: str) -> str:
    return f"test-{RUN_MARKER}-{name}"


@pytest.fixture(scope="module")
def db(postgres_dsn):
    database_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or postgres_dsn
    conn = psycopg.connect(database_url, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


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


# --- the URL we print on a volunteer's terminal ----------------------------
#
# /v1alpha1/device/code returns `verification_uri`, flashnode prints it, and
# the volunteer types it into a browser. It said `{console}/enrol` while the
# console has only ever served `/activate`, so that URL 404'd at the exact
# moment a first-time host is most likely to give up.
#
# Nothing caught it because both sides were internally consistent: the API
# had a route name, the web app had a page, and no test compared the two.
# This does — against the filesystem, so renaming or moving the page fails
# here rather than in someone's terminal.


def _console_routes() -> set[str]:
    """Route paths the Next.js app actually serves, read from disk."""
    from pathlib import Path

    web = Path(__file__).resolve().parents[4] / "flashml-cloud" / "apps" / "web" / "app"
    assert web.is_dir(), f"expected the web app at {web}"
    routes = set()
    for page in web.rglob("page.tsx"):
        rel = page.relative_to(web).parent
        # Route groups like (auth) are organisational and not in the URL.
        parts = [p for p in rel.parts if not (p.startswith("(") and p.endswith(")"))]
        routes.add("/" + "/".join(parts) if parts else "/")
    return routes


def test_verification_uri_points_at_a_page_that_exists():
    from flashml_cloud_api.images import CURATED  # noqa: F401  (import sanity)

    routes = _console_routes()
    # Sanity: the reader found real pages, so an empty set cannot make this
    # pass vacuously.
    assert "/machines" in routes, f"route reader looks broken: {sorted(routes)}"

    import re
    from pathlib import Path

    app_src = (
        Path(__file__).resolve().parents[1]
        / "flashml_cloud_api"
        / "app.py"
    ).read_text()
    printed = set(re.findall(r'"verification_uri": f?"\{base\}(/[a-z0-9-]+)"', app_src))
    assert printed, "could not find the verification_uri literal in app.py"
    for path in printed:
        assert path in routes, (
            f"device-code enrolment sends volunteers to {path!r}, which the "
            f"console does not serve. Real routes: {sorted(routes)}"
        )


def test_a_revoked_machine_can_be_re_enrolled_by_its_owner(db, owner):
    """Revoke must not be a one-way door.

    machines.node_id is globally unique and revoking only sets
    status='revoked' — the row, and therefore the node_id, stays. So a plain
    INSERT on re-enrolment raised UniqueViolation and the owner was told
    "this machine is already enrolled", with no way back except deleting the
    agent's identity file. Revoking a machine you own and enrolling it again
    is an ordinary thing to do; item 7 of the M1 acceptance bar even requires
    revocation to work, which makes a permanently unusable node_id a poor
    reward for using the feature.

    The old token must NOT survive: re-enrolment issues a fresh one, and
    anything holding the revoked token stays locked out.
    """
    node_id = _node_id("re-enrol")
    first = enrolment.start_device_code(db, node_id, "host", "linux")
    machine_id = enrolment.approve_device_code(db, first["user_code"], owner)
    old_token = enrolment.redeem_device_code(db, first["device_code"])
    assert enrolment.revoke_machine(db, machine_id, owner) is True
    assert enrolment.authenticate_machine(db, old_token) is None

    second = enrolment.start_device_code(db, node_id, "host", "linux")
    again_id = enrolment.approve_device_code(db, second["user_code"], owner)
    new_token = enrolment.redeem_device_code(db, second["device_code"])

    assert enrolment.authenticate_machine(db, new_token) is not None
    assert enrolment.authenticate_machine(db, old_token) is None, (
        "the revoked token must not come back to life"
    )

    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.machines where node_id = %s",
            (node_id,),
        )
        assert cur.fetchone()["n"] == 1, "re-enrolment must reuse the row, not duplicate it"
    assert again_id == machine_id


def test_a_revoked_machine_cannot_be_claimed_by_a_different_account(db, owner, other_owner):
    """Re-enrolment is owner-scoped. A revoked node_id must not become a way
    for a second account to adopt someone else's machine id — that is the
    impersonation the unique constraint exists to prevent, and revoking must
    not open it."""
    node_id = _node_id("re-enrol-other")
    first = enrolment.start_device_code(db, node_id, "host", "linux")
    machine_id = enrolment.approve_device_code(db, first["user_code"], owner)
    assert enrolment.revoke_machine(db, machine_id, owner) is True

    second = enrolment.start_device_code(db, node_id, "host", "linux")
    with pytest.raises(NodeAlreadyEnrolled):
        enrolment.approve_device_code(db, second["user_code"], other_owner)
