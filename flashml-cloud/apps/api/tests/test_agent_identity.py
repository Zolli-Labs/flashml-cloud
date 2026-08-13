"""Agent principals: an agent's own scoped, revocable identity (AG-6).

The properties pinned here are the ones whose failure hands an agent access
it should not have, or leaves a revoked credential able to act:

- **The raw token is returned once, at creation, and stored nowhere.** Not
  "stored encrypted" — absent. Checked against the one column that
  legitimately holds a digest of it, and against a scan of the whole schema,
  the same discipline ``test_sandbox_identity.py`` applies to machine tokens.
- **Scopes are validated before any row is written.** An unknown scope name
  is rejected outright; ``'submit'`` without a ``pool_id`` and ``'spend'``
  without a positive ``allowance_zc`` are both rejected — the schema itself
  (``migrations/0027_agent_principals.sql``, exercised in ``test_schema.py``)
  enforces the same two rules a second time, but this file is what proves
  ``agent_identity``/``db.py`` refuse them BEFORE that constraint is ever
  reached.
- **``has_scope`` is an AND, not an OR.** Active-but-wrong-scope and
  right-scope-but-revoked must both read as "no."
- **Revocation is total.** The token stops authenticating immediately, and
  the credential material (``token_hash``/``token_prefix``) is destroyed in
  the same statement — "the revoked token must stay dead."
- **Owner-scoping holds in both directions.** A stranger's revoke and a
  stranger's read both return the same nothing an unknown id returns.
"""
from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from flashml_cloud_api import agent_identity as ai
from flashml_cloud_api import db as dbmod
from flashml_cloud_api.auth import hash_machine_token

# Session Postgres fixture, shared across the suite (see test_schema.py's
# own import of it, and its docstring in test_jobs_from_repo.py).
from test_jobs_from_repo import db  # noqa: F401 - fixture


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _user(db) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute(
            "insert into auth.users (id, email) values (%s, %s)",
            (user_id, f"{user_id[:8]}@example.com"),
        )
        cur.execute("insert into public.profiles (id) values (%s)", (user_id,))
    return user_id


def _pool(db, owner_id: str) -> str:
    return str(dbmod.create_pool(db, name="agent-pool", owner_id=owner_id)["id"])


def _create(
    db,
    owner_id: str,
    *,
    label: str = "test agent",
    scopes=("read",),
    pool_id: str | None = None,
    allowance_zc: int = 0,
):
    return dbmod.create_agent_principal(
        db,
        owner_id=owner_id,
        label=label,
        scopes=list(scopes),
        pool_id=pool_id,
        allowance_zc=allowance_zc,
    )


def _principal_row(db, principal_id: str) -> dict:
    with db.cursor() as cur:
        cur.execute(
            "select * from public.agent_principals where id = %s", (principal_id,)
        )
        row = cur.fetchone()
    assert row is not None
    return row


# ---------------------------------------------------------------------------
# agent_identity.normalise_scopes / AgentPrincipal.has_scope
# ---------------------------------------------------------------------------


def test_normalise_scopes_accepts_a_known_subset_in_canonical_order():
    assert ai.normalise_scopes(["spend", "read"]) == ("read", "spend")
    assert ai.normalise_scopes(["read"]) == ("read",)
    assert ai.normalise_scopes(("submit", "read", "spend")) == (
        "read", "submit", "spend",
    )


def test_normalise_scopes_deduplicates():
    assert ai.normalise_scopes(["read", "read", "submit"]) == ("read", "submit")


def test_normalise_scopes_rejects_an_unknown_scope():
    with pytest.raises(ai.InvalidScope):
        ai.normalise_scopes(["read", "admin"])
    with pytest.raises(ai.InvalidScope):
        ai.normalise_scopes(["Read"])  # exact case only


def test_normalise_scopes_rejects_empty():
    with pytest.raises(ai.InvalidScope):
        ai.normalise_scopes([])


def test_normalise_scopes_rejects_a_bare_string():
    """`"read"` must never silently iterate into `('r', 'e', 'a', 'd')`."""
    with pytest.raises(ai.InvalidScope):
        ai.normalise_scopes("read")


def test_normalise_scopes_rejects_non_string_elements():
    with pytest.raises(ai.InvalidScope):
        ai.normalise_scopes(["read", 1])
    with pytest.raises(ai.InvalidScope):
        ai.normalise_scopes([None])


def test_has_scope_is_active_and_granted():
    active_reader = ai.AgentPrincipal(
        id="p1", owner_id="o1", label="x", scopes=("read",),
        pool_id=None, allowance_zc=0, status="active",
    )
    assert active_reader.has_scope("read") is True
    assert active_reader.has_scope("submit") is False
    assert active_reader.has_scope("spend") is False

    revoked_reader = ai.AgentPrincipal(
        id="p2", owner_id="o1", label="x", scopes=("read", "submit", "spend"),
        pool_id="pool-1", allowance_zc=100, status="revoked",
    )
    # Every scope was granted, and none of them count once revoked.
    for scope in ("read", "submit", "spend"):
        assert revoked_reader.has_scope(scope) is False


def test_agent_principal_carries_no_credential_field():
    """The dataclass shape itself is the guarantee: there is no field a
    token or its digest could ever be assigned to, so no future line of code
    inside this type can leak one."""
    import dataclasses

    fields = {f.name for f in dataclasses.fields(ai.AgentPrincipal)}
    assert fields == {
        "id", "owner_id", "label", "scopes", "pool_id", "allowance_zc",
        "status",
    }


# ---------------------------------------------------------------------------
# db.create_agent_principal
# ---------------------------------------------------------------------------


def test_create_returns_the_raw_token_exactly_once_and_the_row_stores_a_hash(db):
    owner = _user(db)
    principal, token = _create(db, owner, scopes=["read"])

    assert token.startswith("fmk_")
    assert isinstance(principal, ai.AgentPrincipal)
    assert principal.owner_id == owner
    assert principal.scopes == ("read",)
    assert principal.status == "active"

    row = _principal_row(db, principal.id)
    assert row["token_hash"] == hash_machine_token(token)
    assert row["token_hash"] != token
    assert row["token_prefix"] == token[: dbmod.AGENT_TOKEN_PREFIX_LENGTH]
    assert token not in repr(row)


def test_no_raw_token_is_stored_anywhere_in_the_schema(db):
    """The whole database, every table, every column — not only the one we
    thought to check, the same scan `test_sandbox_identity.py` runs for
    machine tokens."""
    owner = _user(db)
    _, token = _create(db, owner, scopes=["read"])

    with db.cursor() as cur:
        cur.execute(
            """
            select table_name from information_schema.tables
             where table_schema = 'public' and table_type = 'BASE TABLE'
            """
        )
        tables = [r["table_name"] for r in cur.fetchall()]
    assert "agent_principals" in tables

    for table in tables:
        with db.cursor() as cur:
            cur.execute(
                sql.SQL("select * from public.{}").format(sql.Identifier(table))
            )
            dumped = "\n".join(repr(row) for row in cur.fetchall())
        assert token not in dumped, f"raw token found in public.{table}"


def test_each_create_mints_a_fresh_token(db):
    owner = _user(db)
    _, first_token = _create(db, owner, scopes=["read"])
    _, second_token = _create(db, owner, scopes=["read"])
    assert first_token != second_token


def test_create_rejects_an_unknown_scope_and_writes_nothing(db):
    owner = _user(db)
    with pytest.raises(ai.InvalidScope):
        _create(db, owner, scopes=["read", "admin"])
    assert dbmod.list_agent_principals(db, owner) == []


def test_create_rejects_submit_scope_without_a_pool_id(db):
    owner = _user(db)
    with pytest.raises(ai.InvalidScope):
        _create(db, owner, scopes=["submit"], pool_id=None)
    assert dbmod.list_agent_principals(db, owner) == []

    # The same request, with a pool, succeeds.
    pool = _pool(db, owner)
    principal, _ = _create(db, owner, scopes=["submit"], pool_id=pool)
    assert principal.pool_id == pool


def test_create_rejects_spend_scope_with_a_non_positive_allowance(db):
    owner = _user(db)
    for bad_allowance in (0, -1, -1000):
        with pytest.raises(ai.InvalidScope):
            _create(db, owner, scopes=["spend"], allowance_zc=bad_allowance)
    assert dbmod.list_agent_principals(db, owner) == []

    principal, _ = _create(db, owner, scopes=["spend"], allowance_zc=500)
    assert principal.allowance_zc == 500


def test_create_rejects_a_blank_label(db):
    owner = _user(db)
    with pytest.raises(ValueError):
        _create(db, owner, label="   ", scopes=["read"])


# ---------------------------------------------------------------------------
# db.authenticate_agent_token
# ---------------------------------------------------------------------------


def test_authenticate_finds_an_active_principal(db):
    owner = _user(db)
    principal, token = _create(db, owner, scopes=["read"])

    found = dbmod.authenticate_agent_token(db, token)
    assert found is not None
    assert found.id == principal.id
    assert found.owner_id == owner
    assert found.status == "active"


def test_authenticate_returns_none_for_a_revoked_principal(db):
    owner = _user(db)
    principal, token = _create(db, owner, scopes=["read"])
    assert dbmod.authenticate_agent_token(db, token) is not None

    assert dbmod.revoke_agent_principal(
        db, principal_id=principal.id, owner_id=owner
    )

    assert dbmod.authenticate_agent_token(db, token) is None


def test_authenticate_returns_none_for_an_unknown_or_garbage_token(db):
    owner = _user(db)
    _, token = _create(db, owner, scopes=["read"])

    assert dbmod.authenticate_agent_token(db, token + "x") is None
    assert dbmod.authenticate_agent_token(db, "fmk_" + "0" * 40) is None
    assert dbmod.authenticate_agent_token(db, "not-even-the-right-shape") is None


def test_authenticate_returns_none_for_an_empty_or_missing_token(db):
    assert dbmod.authenticate_agent_token(db, None) is None
    assert dbmod.authenticate_agent_token(db, "") is None


# ---------------------------------------------------------------------------
# Scopes, end to end
# ---------------------------------------------------------------------------


def test_a_read_only_principal_has_read_but_not_submit_or_spend(db):
    owner = _user(db)
    principal, _ = _create(db, owner, scopes=["read"])

    assert principal.has_scope("read") is True
    assert principal.has_scope("submit") is False
    assert principal.has_scope("spend") is False


def test_a_principal_with_every_scope_reports_all_three(db):
    owner = _user(db)
    pool = _pool(db, owner)
    principal, _ = _create(
        db, owner, scopes=["read", "submit", "spend"],
        pool_id=pool, allowance_zc=1000,
    )
    assert principal.has_scope("read") is True
    assert principal.has_scope("submit") is True
    assert principal.has_scope("spend") is True
    assert principal.pool_id == pool
    assert principal.allowance_zc == 1000


# ---------------------------------------------------------------------------
# db.revoke_agent_principal
# ---------------------------------------------------------------------------


def test_revoke_clears_the_token_hash_and_prefix(db):
    owner = _user(db)
    principal, _ = _create(db, owner, scopes=["read"])

    assert dbmod.revoke_agent_principal(
        db, principal_id=principal.id, owner_id=owner
    )

    row = _principal_row(db, principal.id)
    assert row["status"] == "revoked"
    assert row["token_hash"] is None
    assert row["token_prefix"] is None
    assert row["revoked_at"] is not None


def test_revoking_twice_is_a_no_op_not_an_error(db):
    owner = _user(db)
    principal, _ = _create(db, owner, scopes=["read"])

    assert dbmod.revoke_agent_principal(
        db, principal_id=principal.id, owner_id=owner
    ) is True
    assert dbmod.revoke_agent_principal(
        db, principal_id=principal.id, owner_id=owner
    ) is False
    assert dbmod.revoke_agent_principal(
        db, principal_id=principal.id, owner_id=owner
    ) is False

    row = _principal_row(db, principal.id)
    assert row["status"] == "revoked"
    assert row["token_hash"] is None


def test_revoking_an_unknown_principal_is_false_not_an_error(db):
    owner = _user(db)
    assert dbmod.revoke_agent_principal(
        db, principal_id=str(uuid.uuid4()), owner_id=owner
    ) is False


def test_revoking_with_a_garbage_id_is_false_not_a_raised_error(db):
    owner = _user(db)
    assert dbmod.revoke_agent_principal(
        db, principal_id="not-a-uuid", owner_id=owner
    ) is False


# ---------------------------------------------------------------------------
# Owner-scoping
# ---------------------------------------------------------------------------


def test_a_stranger_cannot_revoke_someone_elses_principal(db):
    owner = _user(db)
    stranger = _user(db)
    principal, token = _create(db, owner, scopes=["read"])

    assert dbmod.revoke_agent_principal(
        db, principal_id=principal.id, owner_id=stranger
    ) is False

    # Nothing moved: not the status, not the credential.
    row = _principal_row(db, principal.id)
    assert row["status"] == "active"
    assert row["token_hash"] is not None
    assert dbmod.authenticate_agent_token(db, token) is not None


def test_a_stranger_cannot_read_someone_elses_principal(db):
    owner = _user(db)
    stranger = _user(db)
    principal, _ = _create(db, owner, scopes=["read"])

    assert dbmod.get_agent_principal(db, principal.id, stranger) is None
    # The owner themselves can, so the refusal above is really ownership and
    # not merely "this read never works".
    found = dbmod.get_agent_principal(db, principal.id, owner)
    assert found is not None
    assert found.id == principal.id


def test_an_unknown_principal_id_and_someone_elses_are_refused_identically(db):
    owner = _user(db)
    theirs, _ = _create(db, _user(db), scopes=["read"])

    unknown = dbmod.get_agent_principal(db, str(uuid.uuid4()), owner)
    forbidden = dbmod.get_agent_principal(db, theirs.id, owner)
    assert unknown is None
    assert forbidden is None


def test_list_agent_principals_is_owner_scoped_and_never_carries_a_token(db):
    owner = _user(db)
    other = _user(db)
    mine, token = _create(db, owner, scopes=["read"], label="mine")
    _create(db, other, scopes=["read"], label="not mine")

    listed = dbmod.list_agent_principals(db, owner)
    assert [p.id for p in listed] == [mine.id]
    assert listed[0].label == "mine"
    for principal in listed:
        assert not hasattr(principal, "token_hash")
        assert not hasattr(principal, "token_prefix")
    assert token not in repr(listed)


def test_list_agent_principals_includes_revoked_rows(db):
    """Unlike `list_machines_for_owner` (which hides a revoked *leased*
    machine because that identity was never permanently the owner's), an
    agent principal's identity IS permanently the owner's — it is their own
    agent, not borrowed hardware — so a revoked principal stays visible as
    history, the same way a revoked *persistent* machine does."""
    owner = _user(db)
    principal, _ = _create(db, owner, scopes=["read"])
    dbmod.revoke_agent_principal(db, principal_id=principal.id, owner_id=owner)

    listed = dbmod.list_agent_principals(db, owner)
    assert [p.id for p in listed] == [principal.id]
    assert listed[0].status == "revoked"
