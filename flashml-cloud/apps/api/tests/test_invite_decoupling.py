"""A workspace invite joins a workspace. It does not grant the product.

Until 0009 these were one act — `pool_invites`' own comment said
"Consuming an invite both ADMITS the account through the alpha signup gate
and joins it to the pool". Separating them is the point of this design, so
it gets tests that pin the two apart rather than trusting a code read.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod
from flashml_cloud_api.access import parse_submission

# `consume_pool_invite` takes a token_hash directly and never hashes
# anything itself, so these tests pass an opaque digest exactly as
# test_db_pools.py already does. There is no `hash_token` helper to import.
def _digest(label: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, label).hex

SUBMISSION = parse_submission(
    {
        "first_name": "Minh", "last_name": "Tran", "company_name": "VinAI",
        "role": "ml_engineer", "team_size": "2_5",
        "use_case": "Join my team's pool.", "compute_sources": ["own_machines"],
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


def _user(db, *, admitted: bool = False) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, %s)",
            (user_id, datetime.now(timezone.utc) if admitted else None),
        )
    return user_id


def _pool_with_invite(db, owner_id: str, *, token: str, uses: int = 3) -> str:
    pool_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pools (id, name, owner_id) values (%s, %s, %s)",
            (pool_id, "Lab", owner_id),
        )
        cur.execute(
            "insert into public.pool_members (pool_id, user_id) values (%s, %s)",
            (pool_id, owner_id),
        )
    dbmod.create_pool_invite(
        db,
        pool_id=pool_id,
        created_by=owner_id,
        token_hash=_digest(token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        uses=uses,
    )
    return pool_id


def test_redeeming_while_unadmitted_does_not_set_admitted_at(db):
    """THE regression this file exists for."""
    owner = _user(db, admitted=True)
    _pool_with_invite(db, owner, token="fmi_abc")
    newcomer = _user(db)

    dbmod.consume_pool_invite(db, token_hash=_digest("fmi_abc"), user_id=newcomer)

    with db.cursor() as cur:
        cur.execute("select admitted_at from public.profiles where id = %s", (newcomer,))
        assert cur.fetchone()["admitted_at"] is None


def test_redeeming_while_unadmitted_banks_the_pool_instead_of_joining(db):
    owner = _user(db, admitted=True)
    pool_id = _pool_with_invite(db, owner, token="fmi_bank")
    newcomer = _user(db)

    result = dbmod.consume_pool_invite(
        db, token_hash=_digest("fmi_bank"), user_id=newcomer
    )
    assert result is not None
    assert result["admitted"] is False

    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.pool_members "
            " where pool_id = %s and user_id = %s",
            (pool_id, newcomer),
        )
        assert cur.fetchone()["n"] == 0
        cur.execute(
            "select pending_pool_id from public.access_requests where user_id = %s",
            (newcomer,),
        )
        assert str(cur.fetchone()["pending_pool_id"]) == pool_id


def test_an_admitted_account_still_joins_immediately(db):
    """The path that already worked must be untouched."""
    owner = _user(db, admitted=True)
    pool_id = _pool_with_invite(db, owner, token="fmi_now")
    member = _user(db, admitted=True)

    result = dbmod.consume_pool_invite(
        db, token_hash=_digest("fmi_now"), user_id=member
    )
    assert result["admitted"] is True
    with db.cursor() as cur:
        cur.execute(
            "select 1 from public.pool_members where pool_id = %s and user_id = %s",
            (pool_id, member),
        )
        assert cur.fetchone() is not None


def test_approval_after_banking_lands_the_join(db):
    """End to end: invite before approval, then approval, then membership."""
    owner = _user(db, admitted=True)
    pool_id = _pool_with_invite(db, owner, token="fmi_e2e")
    newcomer = _user(db)

    dbmod.submit_access_request(
        db, newcomer, SUBMISSION, email_domain=None, is_personal_email=None
    )
    dbmod.consume_pool_invite(db, token_hash=_digest("fmi_e2e"), user_id=newcomer)
    dbmod.approve_access_request(db, newcomer, decided_by=owner)

    with db.cursor() as cur:
        cur.execute(
            "select 1 from public.pool_members where pool_id = %s and user_id = %s",
            (pool_id, newcomer),
        )
        assert cur.fetchone() is not None


def test_a_use_is_consumed_even_when_the_join_is_only_banked(db):
    """Accepted cost, recorded in the spec: declining that person later
    burns the use. Holding it instead would let one link be claimed by
    unlimited pending accounts."""
    owner = _user(db, admitted=True)
    _pool_with_invite(db, owner, token="fmi_use", uses=1)
    newcomer = _user(db)

    assert dbmod.consume_pool_invite(
        db, token_hash=_digest("fmi_use"), user_id=newcomer
    ) is not None
    # Exhausted now, for everybody.
    assert dbmod.consume_pool_invite(
        db, token_hash=_digest("fmi_use"), user_id=_user(db)
    ) is None
