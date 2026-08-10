"""The GitHub-installation data layer, against the real ephemeral Postgres.

Two properties here would be bugs of very different kinds if wrong, and both
get a test that says so in its name:

- **Two colleagues in one organisation share one `installation_id`.** GitHub
  installs an App on an *account*, not on a person. A single-column primary
  key on `installation_id` would let whoever connects first win and lock
  every teammate out — the exact opposite of what an org-level integration
  is for, and invisible until the second person tries.
- **A state can be claimed once, by the user it was minted for.** That is the
  binding that stops an attacker attaching someone else's organisation to
  their own account. See the spec's §3.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def _user(db) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id, email) values (%s, %s)",
                    (user_id, f"{user_id[:8]}@example.com"))
        cur.execute("insert into public.profiles (id) values (%s)", (user_id,))
    return user_id


def _future() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=10)


# ---------------------------------------------------------------------------
# Installations
# ---------------------------------------------------------------------------


def test_an_installation_is_found_by_its_repo_owner(db):
    user_id = _user(db)
    dbmod.insert_github_installation(
        db, installation_id=100, user_id=user_id,
        account_login="acme", account_type="Organization",
        repository_selection="selected",
    )

    row = dbmod.fetch_github_installation_for_owner(db, user_id, "acme")

    assert row is not None
    assert row["installation_id"] == 100


def test_the_owner_lookup_ignores_case(db):
    """GitHub logins preserve case and compare case-insensitively, and a repo
    URL may be typed either way. A case-sensitive lookup would silently fall
    back to an anonymous fetch and 404 on a private repo — reported to the
    submitter as "repo not found", which is the wrong problem entirely."""
    user_id = _user(db)
    dbmod.insert_github_installation(
        db, installation_id=100, user_id=user_id,
        account_login="AcmeCorp", account_type="Organization",
        repository_selection="all",
    )

    assert dbmod.fetch_github_installation_for_owner(db, user_id, "acmecorp")
    assert dbmod.fetch_github_installation_for_owner(db, user_id, "ACMECORP")


def test_another_users_installation_is_not_visible(db):
    """The whole point of the binding. If this leaks, any admitted user
    reads any connected organisation's private code."""
    owner, stranger = _user(db), _user(db)
    dbmod.insert_github_installation(
        db, installation_id=100, user_id=owner,
        account_login="acme", account_type="Organization",
        repository_selection="all",
    )

    assert dbmod.fetch_github_installation_for_owner(db, stranger, "acme") is None


def test_two_users_can_share_one_installation_id(db):
    """One org, one installation, two colleagues who both connect.

    GitHub hands them the SAME installation_id. This test fails loudly
    against a single-column primary key, which is why it exists.
    """
    alice, bob = _user(db), _user(db)

    dbmod.insert_github_installation(
        db, installation_id=100, user_id=alice,
        account_login="acme", account_type="Organization",
        repository_selection="all",
    )
    dbmod.insert_github_installation(
        db, installation_id=100, user_id=bob,
        account_login="acme", account_type="Organization",
        repository_selection="all",
    )

    assert dbmod.fetch_github_installation_for_owner(db, alice, "acme")
    assert dbmod.fetch_github_installation_for_owner(db, bob, "acme")


def test_reconnecting_the_same_installation_is_idempotent(db):
    """Someone who clicks Connect twice must not hit a primary-key error."""
    user_id = _user(db)
    for _ in range(2):
        dbmod.insert_github_installation(
            db, installation_id=100, user_id=user_id,
            account_login="acme", account_type="Organization",
            repository_selection="all",
        )

    assert len(dbmod.list_github_installations(db, user_id)) == 1


def test_disconnecting_removes_only_the_callers_row(db):
    alice, bob = _user(db), _user(db)
    for user_id in (alice, bob):
        dbmod.insert_github_installation(
            db, installation_id=100, user_id=user_id,
            account_login="acme", account_type="Organization",
            repository_selection="all",
        )

    assert dbmod.delete_github_installation(db, alice, 100) is True

    assert dbmod.list_github_installations(db, alice) == []
    assert len(dbmod.list_github_installations(db, bob)) == 1


def test_disconnecting_something_you_do_not_have_reports_false(db):
    """So the route can answer 404 rather than a cheerful 204 for an id the
    caller never had."""
    user_id = _user(db)
    assert dbmod.delete_github_installation(db, user_id, 999) is False


def test_list_returns_only_the_callers_installations(db):
    alice, bob = _user(db), _user(db)
    dbmod.insert_github_installation(
        db, installation_id=100, user_id=alice,
        account_login="acme", account_type="Organization",
        repository_selection="all",
    )
    dbmod.insert_github_installation(
        db, installation_id=200, user_id=bob,
        account_login="other", account_type="User",
        repository_selection="selected",
    )

    rows = dbmod.list_github_installations(db, alice)

    assert [r["installation_id"] for r in rows] == [100]


# ---------------------------------------------------------------------------
# Install state — the user binding
# ---------------------------------------------------------------------------


def test_a_state_is_claimable_once_by_the_user_it_was_minted_for(db):
    user_id = _user(db)
    dbmod.insert_github_install_state(db, "st_abc", user_id, _future())

    assert dbmod.claim_github_install_state(db, "st_abc", user_id) is True
    # Replay: a captured redirect must not bind a second time.
    assert dbmod.claim_github_install_state(db, "st_abc", user_id) is False


def test_a_state_minted_for_someone_else_cannot_be_claimed(db):
    """THE security property.

    An attacker mints a state as themselves and phishes a victim into
    installing the App with it. The victim's browser posts it as the victim.
    The user ids differ, the claim fails, and nothing is bound. Without this,
    the attacker attaches the victim's organisation to their own account and
    reads its private code.
    """
    attacker, victim = _user(db), _user(db)
    dbmod.insert_github_install_state(db, "st_abc", attacker, _future())

    assert dbmod.claim_github_install_state(db, "st_abc", victim) is False
    # And it is still there for its rightful owner — a failed attempt must
    # not consume someone else's pending flow.
    assert dbmod.claim_github_install_state(db, "st_abc", attacker) is True


def test_an_expired_state_cannot_be_claimed(db):
    user_id = _user(db)
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    dbmod.insert_github_install_state(db, "st_old", user_id, past)

    assert dbmod.claim_github_install_state(db, "st_old", user_id) is False


def test_an_unknown_state_cannot_be_claimed(db):
    user_id = _user(db)
    assert dbmod.claim_github_install_state(db, "st_nope", user_id) is False
