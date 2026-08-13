"""AG-6's HTTP surface: mint, list, revoke, and `whoami` for an agent
principal — an agent's own scoped, revocable identity, separate from the
human's Supabase sign-in (see agent_identity.py and migration 0027).

THE LOAD-BEARING TEST IN THIS FILE is
``test_minting_submit_scope_for_a_foreign_pool_is_404``. An opus review
flagged, HIGH, that `dbmod.create_agent_principal` performs no pool
authorization of its own — it only enforces that a `pool_id` is present at
all when `submit` is requested — so the mint route is the ONLY place that
can refuse an admitted account minting a `submit`-scoped token against a
pool it merely guessed the id of. Without the route-level membership check,
any admitted account could mint an agent that submits work into an
arbitrary team's queue. This file pins that check the same way
test_market_routes.py pins escrow-on-claim: through the HTTP surface, not
the repository function alone, because the vulnerability was a MISSING
route-level check, and only an HTTP test can prove the route makes it.

Everything is built at runtime — users, pools, memberships — for the same
reason test_market_routes.py's own header gives: the owner has rejected
fixture-shaped literals, and a credential-shaped constant in a test is
still one in the repo.
"""
from __future__ import annotations

import time
import uuid

import psycopg
import pytest
import jwt
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod
from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.settings import Settings

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def settings(postgres_dsn) -> Settings:
    return Settings(
        supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
        supabase_jwt_secret=JWT_SECRET,
        supabase_service_key="service-key-not-used-here",
        coordinator_url="http://coordinator.internal:8100",
        coordinator_operator_token="op-secret-do-not-leak-3f9c1b",
        require_auth=True,
        database_url=postgres_dsn,
        console_url="https://console.example",
    )


@pytest.fixture
def client(settings, postgres_dsn):
    def connect() -> psycopg.Connection:
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        return conn

    app = create_cloud_app(settings, connect=connect)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def _new_user(db, *, admitted: bool = True) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, %s)",
            (user_id, "now()" if admitted else None),
        )
    return user_id


def _browser_jwt(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": time.time() + 3600},
        JWT_SECRET,
        algorithm="HS256",
    )


def _auth(user_id: str) -> dict:
    return {"Authorization": f"Bearer {_browser_jwt(user_id)}"}


def _pool(db, owner_id: str, name: str = "Ada's Team") -> str:
    """A pool ``owner_id`` is a MEMBER of — ``create_pool`` seats its owner
    atomically, so this is also the "owner IS a member" fixture."""
    return str(dbmod.create_pool(db, name=name, owner_id=owner_id)["id"])


# ---------------------------------------------------------------------------
# POST /v1alpha1/agent-principals — mint
# ---------------------------------------------------------------------------


def test_mint_requires_sign_in(client):
    r = client.post("/v1alpha1/agent-principals", json={"label": "x", "scopes": ["read"]})
    assert r.status_code == 401


def test_mint_requires_admission(client, db):
    user = _new_user(db, admitted=False)
    r = client.post(
        "/v1alpha1/agent-principals",
        headers=_auth(user),
        json={"label": "x", "scopes": ["read"]},
    )
    assert r.status_code == 403


def test_mint_read_scope_needs_no_pool(client, db):
    user = _new_user(db)
    r = client.post(
        "/v1alpha1/agent-principals",
        headers=_auth(user),
        json={"label": "monitor", "scopes": ["read"]},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["label"] == "monitor"
    assert body["scopes"] == ["read"]
    assert body["pool_id"] is None
    assert body["allowance_zc"] == 0
    assert body["status"] == "active"
    assert isinstance(body["token"], str) and body["token"]


def test_mint_returns_a_raw_token_that_authenticates_and_echoes_scopes(client, db):
    user = _new_user(db)
    minted = client.post(
        "/v1alpha1/agent-principals",
        headers=_auth(user),
        json={"label": "watcher", "scopes": ["read"]},
    ).json()

    who = client.get(
        "/v1alpha1/agent/whoami",
        headers={"Authorization": f"Bearer {minted['token']}"},
    )
    assert who.status_code == 200, who.text
    body = who.json()
    assert body["id"] == minted["id"]
    assert body["label"] == "watcher"
    assert body["scopes"] == ["read"]
    assert body["status"] == "active"
    assert "token" not in body


def test_minting_submit_scope_for_a_foreign_pool_is_404(client, db):
    """THE SECURITY FIX. ``owner`` is admitted but a member of no pool at
    all; ``someone_else`` owns a real, existing pool. Minting a
    submit-scoped principal against that pool must be refused — not merely
    "not recommended", refused — with the same 404 an unknown pool id
    gets, because ``create_agent_principal`` performs no authorization of
    its own and the route is the only thing standing in the way."""
    owner = _new_user(db)
    someone_else = _new_user(db)
    foreign_pool = _pool(db, someone_else)

    r = client.post(
        "/v1alpha1/agent-principals",
        headers=_auth(owner),
        json={"label": "submitter", "scopes": ["submit"], "pool_id": foreign_pool},
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "unknown pool"

    # And no row was written: the caller's own list is still empty.
    listed = client.get("/v1alpha1/agent-principals", headers=_auth(owner)).json()
    assert listed == []


def test_minting_submit_scope_for_an_unknown_pool_id_is_the_same_404(client, db):
    owner = _new_user(db)
    r = client.post(
        "/v1alpha1/agent-principals",
        headers=_auth(owner),
        json={
            "label": "submitter",
            "scopes": ["submit"],
            "pool_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "unknown pool"


def test_minting_submit_scope_with_no_pool_id_at_all_is_also_404(client, db):
    owner = _new_user(db)
    r = client.post(
        "/v1alpha1/agent-principals",
        headers=_auth(owner),
        json={"label": "submitter", "scopes": ["submit"]},
    )
    assert r.status_code == 404


def test_minting_submit_scope_for_a_pool_the_owner_belongs_to_is_201(client, db):
    owner = _new_user(db)
    own_pool = _pool(db, owner)

    r = client.post(
        "/v1alpha1/agent-principals",
        headers=_auth(owner),
        json={"label": "submitter", "scopes": ["submit"], "pool_id": own_pool},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["scopes"] == ["submit"]
    assert body["pool_id"] == own_pool


def test_mint_rejects_an_unknown_scope(client, db):
    user = _new_user(db)
    r = client.post(
        "/v1alpha1/agent-principals",
        headers=_auth(user),
        json={"label": "x", "scopes": ["fly"]},
    )
    assert r.status_code == 400


def test_mint_rejects_spend_scope_with_zero_allowance(client, db):
    user = _new_user(db)
    r = client.post(
        "/v1alpha1/agent-principals",
        headers=_auth(user),
        json={"label": "spender", "scopes": ["spend"], "allowance_zc": 0},
    )
    assert r.status_code == 400


def test_mint_accepts_spend_scope_with_a_positive_allowance(client, db):
    user = _new_user(db)
    r = client.post(
        "/v1alpha1/agent-principals",
        headers=_auth(user),
        json={"label": "spender", "scopes": ["spend"], "allowance_zc": 5000},
    )
    assert r.status_code == 201, r.text
    assert r.json()["allowance_zc"] == 5000


def test_mint_rejects_a_missing_label(client, db):
    user = _new_user(db)
    r = client.post(
        "/v1alpha1/agent-principals",
        headers=_auth(user),
        json={"scopes": ["read"]},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# GET /v1alpha1/agent-principals — list
# ---------------------------------------------------------------------------


def test_list_requires_sign_in(client):
    assert client.get("/v1alpha1/agent-principals").status_code == 401


def test_list_is_empty_not_absent_for_a_new_account(client, db):
    got = client.get("/v1alpha1/agent-principals", headers=_auth(_new_user(db)))
    assert got.status_code == 200
    assert got.json() == []


def test_list_shows_the_principal_with_no_token_or_hash_field(client, db):
    user = _new_user(db)
    minted = client.post(
        "/v1alpha1/agent-principals",
        headers=_auth(user),
        json={"label": "watcher", "scopes": ["read"]},
    ).json()

    listed = client.get("/v1alpha1/agent-principals", headers=_auth(user)).json()
    assert len(listed) == 1
    row = listed[0]
    assert row["id"] == minted["id"]
    assert row["label"] == "watcher"
    assert "token" not in row
    assert "token_hash" not in row
    assert "token_prefix" not in row


def test_list_is_owner_scoped(client, db):
    owner, stranger = _new_user(db), _new_user(db)
    client.post(
        "/v1alpha1/agent-principals",
        headers=_auth(owner),
        json={"label": "mine", "scopes": ["read"]},
    )
    assert client.get("/v1alpha1/agent-principals", headers=_auth(stranger)).json() == []


# ---------------------------------------------------------------------------
# POST /v1alpha1/agent-principals/{id}/revoke
# ---------------------------------------------------------------------------


def test_revoke_requires_sign_in(client):
    r = client.post(f"/v1alpha1/agent-principals/{uuid.uuid4()}/revoke")
    assert r.status_code == 401


def test_revoke_then_the_same_token_is_401_on_whoami(client, db):
    user = _new_user(db)
    minted = client.post(
        "/v1alpha1/agent-principals",
        headers=_auth(user),
        json={"label": "watcher", "scopes": ["read"]},
    ).json()
    token_headers = {"Authorization": f"Bearer {minted['token']}"}

    assert client.get("/v1alpha1/agent/whoami", headers=token_headers).status_code == 200

    revoked = client.post(
        f"/v1alpha1/agent-principals/{minted['id']}/revoke", headers=_auth(user)
    )
    assert revoked.status_code == 200
    assert revoked.json() == {"revoked": True}

    assert client.get("/v1alpha1/agent/whoami", headers=token_headers).status_code == 401


def test_a_second_revoke_of_the_same_id_is_404(client, db):
    user = _new_user(db)
    minted = client.post(
        "/v1alpha1/agent-principals",
        headers=_auth(user),
        json={"label": "watcher", "scopes": ["read"]},
    ).json()

    url = f"/v1alpha1/agent-principals/{minted['id']}/revoke"
    assert client.post(url, headers=_auth(user)).status_code == 200
    assert client.post(url, headers=_auth(user)).status_code == 404


def test_a_stranger_revoking_another_owners_principal_is_404(client, db):
    owner, stranger = _new_user(db), _new_user(db)
    minted = client.post(
        "/v1alpha1/agent-principals",
        headers=_auth(owner),
        json={"label": "watcher", "scopes": ["read"]},
    ).json()

    r = client.post(
        f"/v1alpha1/agent-principals/{minted['id']}/revoke", headers=_auth(stranger)
    )
    assert r.status_code == 404

    # And it is still live for its real owner.
    who = client.get(
        "/v1alpha1/agent/whoami",
        headers={"Authorization": f"Bearer {minted['token']}"},
    )
    assert who.status_code == 200


def test_revoking_an_unknown_id_is_404(client, db):
    user = _new_user(db)
    r = client.post(
        f"/v1alpha1/agent-principals/{uuid.uuid4()}/revoke", headers=_auth(user)
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /v1alpha1/agent/whoami
# ---------------------------------------------------------------------------


def test_whoami_with_no_token_is_401(client):
    assert client.get("/v1alpha1/agent/whoami").status_code == 401


def test_whoami_with_a_garbage_token_is_401(client):
    r = client.get(
        "/v1alpha1/agent/whoami",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert r.status_code == 401


def test_whoami_with_a_browser_jwt_is_401(client, db):
    """A browser credential is a different kind entirely — it must not open
    this route just because it is a valid signed-in session."""
    user = _new_user(db)
    r = client.get("/v1alpha1/agent/whoami", headers=_auth(user))
    assert r.status_code == 401


def test_whoami_never_carries_the_token(client, db):
    user = _new_user(db)
    minted = client.post(
        "/v1alpha1/agent-principals",
        headers=_auth(user),
        json={"label": "watcher", "scopes": ["read", "submit"], "pool_id": _pool(db, user)},
    ).json()

    body = client.get(
        "/v1alpha1/agent/whoami",
        headers={"Authorization": f"Bearer {minted['token']}"},
    ).json()

    assert body["scopes"] == ["read", "submit"]
    assert body["pool_id"] == minted["pool_id"]
    assert "token" not in body
    assert "token_hash" not in body
