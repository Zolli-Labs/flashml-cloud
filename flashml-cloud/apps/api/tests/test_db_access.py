"""The access-request data layer.

Written against the real ephemeral Postgres, like every other db test
here — the transactional guarantee in `approve_access_request` is the
whole point of this module and cannot be shown against a mock.
"""
from __future__ import annotations

import contextlib
import uuid
from datetime import datetime, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod
from flashml_cloud_api.access import parse_submission

SUBMISSION = parse_submission(
    {
        "first_name": "Ha",
        "last_name": "Nguyen",
        "company_name": "VinAI",
        "role": "researcher",
        "team_size": "2_5",
        "use_case": "Fine-tune across the lab's machines.",
        "compute_sources": ["own_machines", "colab"],
        "heard_from": "github",
    }
)


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def _user(db, *, email: str | None = None, admitted: bool = False) -> str:
    """A real ``auth.users`` + ``public.profiles`` pair — profiles.id is an
    FK to auth.users, so both rows are required."""
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id, email) values (%s, %s)", (user_id, email)
        )
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, %s)",
            (user_id, datetime.now(timezone.utc) if admitted else None),
        )
    return user_id


@contextlib.contextmanager
def _pool_member_inserts_fail(db):
    """Make every INSERT into ``public.pool_members`` raise, database-side.

    A trigger rather than a monkeypatch: the property under test is the
    real Postgres transaction, and a Python-level stub would fail OUTSIDE
    it and prove nothing about what the database rolled back.

    Teardown is the delicate part, because ``postgres_dsn`` is
    SESSION-scoped — this trigger sits on a table shared with every other
    db test in the run, and a leak would fail unrelated files with an
    error naming nothing about this one. So it (a) runs in ``finally``,
    ahead of any assertion in the test body that could fail, (b) returns
    the session to a usable state first, since a DROP issued inside an
    aborted transaction raises instead of running, and (c) VERIFIES the
    trigger is gone rather than assuming the DROP took.
    """
    with db.cursor() as cur:
        cur.execute(
            "create or replace function public._boom() returns trigger as $$ "
            "begin raise exception 'boom'; end $$ language plpgsql"
        )
        cur.execute("drop trigger if exists _boom on public.pool_members")
        cur.execute(
            "create trigger _boom before insert on public.pool_members "
            "for each row execute function public._boom()"
        )
    try:
        yield
    finally:
        if db.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            db.rollback()
        with db.cursor() as cur:
            cur.execute("drop trigger if exists _boom on public.pool_members")
            cur.execute("drop function if exists public._boom()")
            cur.execute(
                "select 1 from pg_trigger "
                " where tgname = '_boom' "
                "   and tgrelid = 'public.pool_members'::regclass"
            )
            assert cur.fetchone() is None, (
                "the fault-injection trigger survived teardown — every later "
                "test in this session that joins a pool would fail with 'boom'"
            )


def _pool(db, owner_id: str) -> str:
    pool_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pools (id, name, owner_id) values (%s, %s, %s)",
            (pool_id, "Lab", owner_id),
        )
    return pool_id


# -- access_state_for -------------------------------------------------------

def test_no_row_is_needs_onboarding(db):
    assert dbmod.access_state_for(db, _user(db)) == "needs_onboarding"


def test_an_account_admitted_by_any_other_path_reads_as_admitted(db):
    """0009's backfill covers accounts that existed when it ran. An account
    admitted afterwards — the owner running one UPDATE — has no request row,
    and must not be shown the onboarding form."""
    assert dbmod.access_state_for(db, _user(db, admitted=True)) == "admitted"


def test_a_request_row_wins_over_the_flag(db):
    """A declined account that somehow carries admitted_at reports what the
    admin decided, not what the column says."""
    user = _user(db, admitted=True)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    dbmod.decline_access_request(db, user, decided_by=user)
    assert dbmod.access_state_for(db, user) == "declined"


def test_state_follows_the_row_status(db):
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain="vinai.io", is_personal_email=False
    )
    assert dbmod.access_state_for(db, user) == "pending"
    dbmod.approve_access_request(db, user, decided_by=user)
    assert dbmod.access_state_for(db, user) == "admitted"


def test_a_stub_from_a_banked_invite_reads_as_needs_onboarding(db):
    """A row is not a request. ``record_pending_invite`` stubs a ``pending``
    row for an account that redeemed a workspace invite before it ever saw
    the form; reporting that as ``pending`` parks a brand-new account on the
    "we'll get back to you" screen forever and never offers it the form.
    NULL ``use_case`` is the marker — Task 3's validation refuses an empty
    one, so a submitted row always has it.
    """
    owner = _user(db, admitted=True)
    pool_id = _pool(db, owner)
    user = _user(db)
    dbmod.record_pending_invite(db, user, pool_id=pool_id, invited_by=owner)

    assert dbmod.access_state_for(db, user) == "needs_onboarding"

    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    assert dbmod.access_state_for(db, user) == "pending"

    # Submitting must not drop the banked pool, or approval joins nothing.
    with db.cursor() as cur:
        cur.execute(
            "select pending_pool_id from public.access_requests where user_id = %s",
            (user,),
        )
        assert str(cur.fetchone()["pending_pool_id"]) == pool_id


def test_a_backfilled_admitted_row_is_not_mistaken_for_a_stub(db):
    """0009's backfill writes ``admitted`` rows with no ``use_case`` on
    purpose. The stub rule is scoped to ``pending`` so it cannot drag a
    grandfathered tester back to the onboarding form."""
    user = _user(db, admitted=True)
    with db.cursor() as cur:
        cur.execute(
            "insert into public.access_requests (user_id, status) values (%s, %s)",
            (user, "admitted"),
        )
    assert dbmod.access_state_for(db, user) == "admitted"


def test_declined_is_its_own_state(db):
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    dbmod.decline_access_request(db, user, decided_by=user)
    assert dbmod.access_state_for(db, user) == "declined"


# -- submit -----------------------------------------------------------------

def test_submit_writes_profile_columns_and_seeds_display_name(db):
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain="vinai.io", is_personal_email=False
    )
    with db.cursor() as cur:
        cur.execute(
            "select first_name, last_name, company_name, role, team_size, "
            "       email_domain, is_personal_email, display_name "
            "  from public.profiles where id = %s",
            (user,),
        )
        row = cur.fetchone()
    # Every column the insert binds, checked. The nine parameters are
    # positional, and `role` and `team_size` are adjacent, both text, and
    # both free-form — transposing them is the exact mistake a
    # column-round-trip test exists to catch, so neither may go unasserted.
    assert row["first_name"] == "Ha"
    assert row["last_name"] == "Nguyen"
    assert row["company_name"] == "VinAI"
    assert row["role"] == "researcher"
    assert row["team_size"] == "2_5"
    assert row["email_domain"] == "vinai.io"
    assert row["is_personal_email"] is False
    assert row["display_name"] == "Ha Nguyen"


def test_submit_does_not_overwrite_a_display_name_the_user_chose(db):
    user = _user(db)
    dbmod.upsert_profile(db, user, display_name="hanguyen")
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    with db.cursor() as cur:
        cur.execute("select display_name from public.profiles where id = %s", (user,))
        assert cur.fetchone()["display_name"] == "hanguyen"


def test_submit_does_not_admit(db):
    """Submitting is asking, not being let in."""
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    with db.cursor() as cur:
        cur.execute("select admitted_at from public.profiles where id = %s", (user,))
        assert cur.fetchone()["admitted_at"] is None


def test_resubmitting_while_pending_updates_in_place(db):
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    second = parse_submission(
        {
            "first_name": "Ha",
            "last_name": "Nguyen",
            "company_name": "VinAI Research",
            "role": "founder",
            "team_size": "6_20",
            "use_case": "Changed my mind.",
            "compute_sources": ["runpod"],
        }
    )
    dbmod.submit_access_request(
        db, user, second, email_domain=None, is_personal_email=None
    )
    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.access_requests where user_id = %s",
            (user,),
        )
        assert cur.fetchone()["n"] == 1
        cur.execute(
            "select use_case, compute_sources from public.access_requests "
            " where user_id = %s",
            (user,),
        )
        row = cur.fetchone()
    assert row["use_case"] == "Changed my mind."
    assert row["compute_sources"] == ["runpod"]


# -- record_pending_invite --------------------------------------------------

def test_banking_reports_whether_it_actually_banked(db):
    """The upsert's ``where status = 'pending'`` refuses to touch a DECIDED
    request — correct, a declined account must not re-queue itself by
    clicking a link — but it refuses silently. The outcome is returned so
    ``consume_pool_invite`` can refund the invite use it already spent
    instead of burning a pool owner's link on a join nobody can ever
    materialise.
    """
    owner = _user(db, admitted=True)
    pool_id = _pool(db, owner)

    fresh = _user(db)
    assert dbmod.record_pending_invite(
        db, fresh, pool_id=pool_id, invited_by=owner
    ) is True

    outcast = _user(db)
    dbmod.submit_access_request(
        db, outcast, SUBMISSION, email_domain=None, is_personal_email=None
    )
    dbmod.decline_access_request(db, outcast, decided_by=owner)
    assert dbmod.record_pending_invite(
        db, outcast, pool_id=pool_id, invited_by=owner
    ) is False

    # And the decided row is untouched — no pool smuggled onto it.
    with db.cursor() as cur:
        cur.execute(
            "select status, pending_pool_id from public.access_requests "
            " where user_id = %s",
            (outcast,),
        )
        row = cur.fetchone()
    assert row["status"] == "declined"
    assert row["pending_pool_id"] is None


# -- approve ----------------------------------------------------------------

def test_approve_sets_admitted_at_and_records_the_decider(db):
    admin = _user(db, admitted=True)
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    assert dbmod.approve_access_request(db, user, decided_by=admin) is True
    with db.cursor() as cur:
        cur.execute("select admitted_at from public.profiles where id = %s", (user,))
        assert cur.fetchone()["admitted_at"] is not None
        cur.execute(
            "select status, decided_by, decided_at from public.access_requests "
            " where user_id = %s",
            (user,),
        )
        row = cur.fetchone()
    assert row["status"] == "admitted"
    assert str(row["decided_by"]) == admin
    assert row["decided_at"] is not None


def test_approve_materialises_a_banked_workspace_invite(db):
    """The invite redeemed before approval has to actually land, or the
    person is admitted into a console with no pool — which looks exactly
    like the invite never worked."""
    owner = _user(db, admitted=True)
    pool_id = _pool(db, owner)
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    dbmod.record_pending_invite(db, user, pool_id=pool_id, invited_by=owner)

    dbmod.approve_access_request(db, user, decided_by=owner)

    with db.cursor() as cur:
        cur.execute(
            "select 1 from public.pool_members where pool_id = %s and user_id = %s",
            (pool_id, user),
        )
        assert cur.fetchone() is not None


def test_approve_without_a_banked_invite_joins_nothing(db):
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    dbmod.approve_access_request(db, user, decided_by=user)
    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.pool_members where user_id = %s", (user,)
        )
        assert cur.fetchone()["n"] == 0


def test_approving_twice_is_idempotent_not_an_error(db):
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    assert dbmod.approve_access_request(db, user, decided_by=user) is True
    assert dbmod.approve_access_request(db, user, decided_by=user) is False


def test_approve_is_false_for_an_account_that_never_asked(db):
    assert dbmod.approve_access_request(db, _user(db), decided_by=_user(db)) is False


def test_approve_rolls_back_the_admission_when_the_pool_join_fails(db):
    """The three effects are one transaction, or the approval is a lie.

    Every other approve test above observes the happy path only — delete
    `with db.transaction():` from `approve_access_request` and all of them
    still pass, while the module docstring goes on claiming atomicity is
    the whole point. This one breaks the THIRD effect and insists the
    first two never happened: without the transaction the status flip and
    `admitted_at` commit on their own, and the person is admitted into a
    console with no pool — the exact failure the one transaction exists to
    prevent.
    """
    owner = _user(db, admitted=True)
    pool_id = _pool(db, owner)
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    dbmod.record_pending_invite(db, user, pool_id=pool_id, invited_by=owner)

    with _pool_member_inserts_fail(db):
        with pytest.raises(psycopg.Error):
            dbmod.approve_access_request(db, user, decided_by=owner)

    assert dbmod.access_state_for(db, user) == "pending"
    with db.cursor() as cur:
        cur.execute("select admitted_at from public.profiles where id = %s", (user,))
        assert cur.fetchone()["admitted_at"] is None


# -- list -------------------------------------------------------------------

def test_list_returns_pending_with_the_email_and_profile_facts(db):
    user = _user(db, email="ha@vinai.io")
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain="vinai.io", is_personal_email=False
    )
    rows = dbmod.list_access_requests(db, status="pending")
    row = next(r for r in rows if str(r["user_id"]) == user)
    assert row["email"] == "ha@vinai.io"
    assert row["first_name"] == "Ha"
    assert row["company_name"] == "VinAI"
    assert row["use_case"] == "Fine-tune across the lab's machines."


def test_list_excludes_decided_requests(db):
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    dbmod.approve_access_request(db, user, decided_by=user)
    assert all(
        str(r["user_id"]) != user for r in dbmod.list_access_requests(db, status="pending")
    )


def test_list_names_the_inviting_pool_when_one_was_banked(db):
    owner = _user(db, admitted=True)
    pool_id = _pool(db, owner)
    user = _user(db)
    dbmod.submit_access_request(
        db, user, SUBMISSION, email_domain=None, is_personal_email=None
    )
    dbmod.record_pending_invite(db, user, pool_id=pool_id, invited_by=owner)
    row = next(
        r for r in dbmod.list_access_requests(db, status="pending")
        if str(r["user_id"]) == user
    )
    assert row["pending_pool_name"] == "Lab"


# -- helpers ----------------------------------------------------------------

def test_email_for_user_reads_auth_users(db):
    assert dbmod.email_for_user(db, _user(db, email="ha@vinai.io")) == "ha@vinai.io"


def test_email_for_user_is_none_when_absent(db):
    assert dbmod.email_for_user(db, _user(db)) is None


def test_profile_is_admin_defaults_false(db):
    assert dbmod.profile_is_admin(db, _user(db)) is False


def test_profile_is_admin_reads_the_column(db):
    user = _user(db)
    with db.cursor() as cur:
        cur.execute("update public.profiles set is_admin = true where id = %s", (user,))
    assert dbmod.profile_is_admin(db, user) is True
