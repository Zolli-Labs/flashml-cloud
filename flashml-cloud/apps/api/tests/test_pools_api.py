"""Pools, invites, and the invite-only admission gate — the browser-facing
routes built on top of Task 9's db layer.

``POST /v1alpha1/pools`` creates a team and seats its creator; ``GET
/v1alpha1/pools`` and ``GET /v1alpha1/pools/{id}`` are member-scoped reads
(404, never 403, for a pool that exists but is not yours — same doctrine as
every other resource in this API); ``POST /v1alpha1/pools/{id}/invites``
mints a one-time link only the pool's OWNER may create; ``POST
/v1alpha1/invites/accept`` redeems one, and runs on ``current_user`` rather
than ``admitted_user`` — a not-yet-admitted account is exactly who calls it.
Since 0009 it joins a WORKSPACE and nothing more: an un-admitted caller's
join is banked on their access request until an admin approves them.

Runs against the same migrated Postgres as the rest of the suite, reusing
``test_jobs_from_repo``'s fixtures exactly as ``test_profile.py`` does.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from flashml_cloud_api import db as dbmod
from flashml_cloud_api.auth import (
    hash_invite_token,
    looks_like_invite_token,
    new_invite_token,
)

from test_jobs_from_repo import (  # noqa: F401 - fixtures
    _jwt,
    _new_user,
    db,
    make_client,
    settings,
    transport,
)

RUN_MARKER = uuid.uuid4().hex[:8]


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_jwt(user_id)}"}


def _node_id(tag: str) -> str:
    """A node id no other test can collide with on ``machines.node_id``."""
    return f"pools-api-{RUN_MARKER}-{tag}-{uuid.uuid4().hex[:6]}"


def _enrol(db, owner_id: str, tag: str) -> str:
    """A machine belonging to ``owner_id``, for the binding routes — the
    same ``insert_machine`` helper ``test_db_pools.py``'s own ``_enrol``
    uses, since these tests need a real machine row and not the enrolment
    token dance."""
    node_id = _node_id(tag)
    return str(dbmod.insert_machine(
        db, owner_id=owner_id, node_id=node_id, name=f"machine-{node_id}",
        platform="linux",
    ))


def _new_unadmitted_user(db) -> str:
    """An account that looks exactly like a brand-new sign-in: only the
    ``auth.users`` row exists. No ``public.profiles`` row yet, so
    ``profile_is_admitted`` reads it the same as an admitted-then-refused
    account — not admitted — and the first authenticated call that touches
    ``upsert_profile`` (``GET /me``, or ``/invites/accept``) is what creates
    the profile row, with ``admitted_at`` left null.

    This is deliberately NOT ``_new_user`` (which now marks its profile
    admitted on creation, matching every pre-alpha-gate account and the
    overwhelming majority of callers across the suite) — this is the one
    shape of account the admission gate exists to catch.
    """
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
    return user_id


def _add_member(db, pool_id: str, user_id: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            "insert into public.pool_members (pool_id, user_id) values (%s, %s)",
            (pool_id, user_id),
        )


def _set_display_name(db, user_id: str, name: str) -> None:
    """``_new_user`` creates a profile with no display name, and the pool
    machines route renders one. No existing helper sets it, so set it
    directly."""
    with db.cursor() as cur:
        cur.execute(
            "update public.profiles set display_name = %s where id = %s",
            (name, user_id),
        )


def _create_pool(client, owner: str, name: str = "Ada's Team"):
    return client.post("/v1alpha1/pools", json={"name": name}, headers=_auth(owner))


def _invite(client, owner: str, pool_id: str, **body):
    return client.post(
        f"/v1alpha1/pools/{pool_id}/invites", json=body, headers=_auth(owner)
    )


def _invite_count(db, pool_id: str) -> int:
    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.pool_invites where pool_id = %s",
            (pool_id,),
        )
        return cur.fetchone()["n"]


def _invite_expires_at(db, token: str):
    with db.cursor() as cur:
        cur.execute(
            "select expires_at from public.pool_invites where token_hash = %s",
            (hash_invite_token(token),),
        )
        row = cur.fetchone()
        return row["expires_at"] if row else None


# ---------------------------------------------------------------------------
# the token trio
# ---------------------------------------------------------------------------


def test_invite_tokens_are_unique_prefixed_and_hash_stably():
    a, b = new_invite_token(), new_invite_token()
    assert a != b
    assert a.startswith("fmi_")
    assert b.startswith("fmi_")
    assert looks_like_invite_token(a)
    assert not looks_like_invite_token("fmk_not-an-invite")
    assert not looks_like_invite_token(None)
    assert not looks_like_invite_token("")

    assert hash_invite_token(a) == hash_invite_token(a)
    assert hash_invite_token(a) != hash_invite_token(b)
    assert a not in hash_invite_token(a)
    assert len(hash_invite_token(a)) == 64


# ---------------------------------------------------------------------------
# create -> list -> get
# ---------------------------------------------------------------------------


def test_create_list_get_round_trip(make_client, db):
    client = make_client()
    owner = _new_user(db)

    created = _create_pool(client, owner, name="Ada's Team")
    assert created.status_code == 201, created.text
    pool = created.json()
    assert pool["name"] == "Ada's Team"
    assert pool["owner_id"] == owner
    assert "id" in pool and isinstance(pool["id"], str)

    listed = client.get("/v1alpha1/pools", headers=_auth(owner))
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["id"] == pool["id"]
    assert rows[0]["member_count"] == 1
    assert rows[0]["machines_online"] == 0

    got = client.get(f"/v1alpha1/pools/{pool['id']}", headers=_auth(owner))
    assert got.status_code == 200
    body = got.json()
    assert body["id"] == pool["id"]
    assert body["name"] == "Ada's Team"
    assert [m["user_id"] for m in body["members"]] == [owner]


def test_pools_requires_authentication(make_client):
    client = make_client()
    assert client.get("/v1alpha1/pools").status_code == 401
    assert client.post("/v1alpha1/pools", json={"name": "x"}).status_code == 401


def test_create_pool_rejects_a_missing_or_blank_name(make_client, db):
    client = make_client()
    owner = _new_user(db)
    for body in ({}, {"name": ""}, {"name": "   "}, {"name": 42}):
        r = client.post("/v1alpha1/pools", json=body, headers=_auth(owner))
        assert r.status_code == 400, f"{body!r} -> {r.status_code}"


# ---------------------------------------------------------------------------
# member-scoped reads: 404, never 403
# ---------------------------------------------------------------------------


def test_a_non_member_gets_404_not_403_reading_a_real_pool(make_client, db):
    client = make_client()
    owner = _new_user(db)
    stranger = _new_user(db)
    pool = _create_pool(client, owner).json()

    r = client.get(f"/v1alpha1/pools/{pool['id']}", headers=_auth(stranger))
    assert r.status_code == 404

    # And the pool never showed up in the stranger's own list either.
    assert client.get("/v1alpha1/pools", headers=_auth(stranger)).json() == []


def test_unknown_pool_id_and_a_malformed_one_are_both_404(make_client, db):
    client = make_client()
    owner = _new_user(db)

    assert client.get(
        f"/v1alpha1/pools/{uuid.uuid4()}", headers=_auth(owner)
    ).status_code == 404
    assert client.get(
        "/v1alpha1/pools/not-a-uuid-at-all", headers=_auth(owner)
    ).status_code == 404


# ---------------------------------------------------------------------------
# machine <-> pool bindings (Task 4)
# ---------------------------------------------------------------------------


def test_a_member_binds_their_own_machine_to_their_own_pool(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    machine_id = _enrol(db, owner, "bind")

    r = client.put(
        f"/v1alpha1/pools/{pool['id']}/machines/{machine_id}", headers=_auth(owner)
    )
    assert r.status_code == 204
    assert dbmod.pool_ids_for_machine(db, machine_id) == [pool["id"]]


def test_a_non_owner_member_binds_their_own_machine_to_the_pool(make_client, db):
    """The feature's primary multi-user path: binding is scoped to pool
    MEMBERSHIP (``fetch_pool_for_member``), not pool ownership — any member
    may opt their own machine in, not just the pool's creator. Previously
    pinned only by inspection; this is that test."""
    client = make_client()
    owner = _new_user(db)
    member = _new_user(db)
    pool = _create_pool(client, owner).json()
    _add_member(db, pool["id"], member)
    machine_id = _enrol(db, member, "member-bind")

    r = client.put(
        f"/v1alpha1/pools/{pool['id']}/machines/{machine_id}", headers=_auth(member)
    )
    assert r.status_code == 204
    assert dbmod.pool_ids_for_machine(db, machine_id) == [pool["id"]]


def test_binding_requires_authentication(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    machine_id = _enrol(db, owner, "bind-auth")

    r = client.put(f"/v1alpha1/pools/{pool['id']}/machines/{machine_id}")
    assert r.status_code == 401
    assert dbmod.pool_ids_for_machine(db, machine_id) == []


def test_binding_to_a_pool_the_caller_is_not_a_member_of_is_404(make_client, db):
    client = make_client()
    owner = _new_user(db)
    stranger = _new_user(db)
    pool = _create_pool(client, owner).json()
    machine_id = _enrol(db, stranger, "bind-nonmember")

    r = client.put(
        f"/v1alpha1/pools/{pool['id']}/machines/{machine_id}", headers=_auth(stranger)
    )
    assert r.status_code == 404
    assert dbmod.pool_ids_for_machine(db, machine_id) == []


def test_binding_someone_elses_machine_is_404(make_client, db):
    client = make_client()
    owner = _new_user(db)
    stranger = _new_user(db)
    pool = _create_pool(client, owner).json()
    strangers_machine = _enrol(db, stranger, "bind-not-yours")

    r = client.put(
        f"/v1alpha1/pools/{pool['id']}/machines/{strangers_machine}",
        headers=_auth(owner),
    )
    assert r.status_code == 404
    assert dbmod.pool_ids_for_machine(db, strangers_machine) == []


def test_binding_a_malformed_pool_or_machine_id_is_404(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    machine_id = _enrol(db, owner, "bind-malformed")

    assert client.put(
        f"/v1alpha1/pools/not-a-uuid/machines/{machine_id}", headers=_auth(owner)
    ).status_code == 404
    assert client.put(
        f"/v1alpha1/pools/{pool['id']}/machines/not-a-uuid", headers=_auth(owner)
    ).status_code == 404
    # Neither malformed half wrote a binding.
    assert dbmod.pool_ids_for_machine(db, machine_id) == []


def test_binding_an_unknown_pool_or_machine_id_is_404(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    machine_id = _enrol(db, owner, "bind-unknown")

    assert client.put(
        f"/v1alpha1/pools/{uuid.uuid4()}/machines/{machine_id}", headers=_auth(owner)
    ).status_code == 404
    assert client.put(
        f"/v1alpha1/pools/{pool['id']}/machines/{uuid.uuid4()}", headers=_auth(owner)
    ).status_code == 404


def test_unbinding_returns_204_and_the_stamp_empties(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    machine_id = _enrol(db, owner, "unbind")
    assert client.put(
        f"/v1alpha1/pools/{pool['id']}/machines/{machine_id}", headers=_auth(owner)
    ).status_code == 204
    assert dbmod.pool_ids_for_machine(db, machine_id) == [pool["id"]]

    r = client.delete(
        f"/v1alpha1/pools/{pool['id']}/machines/{machine_id}", headers=_auth(owner)
    )
    assert r.status_code == 204
    assert dbmod.pool_ids_for_machine(db, machine_id) == []


def test_unbinding_a_non_member_pool_or_someone_elses_machine_is_404_and_has_no_effect(
    make_client, db
):
    client = make_client()
    owner = _new_user(db)
    stranger = _new_user(db)
    pool = _create_pool(client, owner).json()
    machine_id = _enrol(db, owner, "unbind-guard")
    assert client.put(
        f"/v1alpha1/pools/{pool['id']}/machines/{machine_id}", headers=_auth(owner)
    ).status_code == 204

    assert client.delete(
        f"/v1alpha1/pools/{pool['id']}/machines/{machine_id}", headers=_auth(stranger)
    ).status_code == 404
    # A stranger's refused unbind must not have touched the binding.
    assert dbmod.pool_ids_for_machine(db, machine_id) == [pool["id"]]


# ---------------------------------------------------------------------------
# machines listing: pool chips and capability booleans (Task 4)
# ---------------------------------------------------------------------------


def test_machines_listing_carries_the_pool_chip_and_capability_booleans(
    make_client, db
):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    machine_id = _enrol(db, owner, "chip")
    dbmod.set_machine_capabilities(
        db, machine_id=machine_id, sandbox_capable=True, argv_capable=True,
        unsandboxed_argv_capable=False, module_capable=True,
    )
    assert client.put(
        f"/v1alpha1/pools/{pool['id']}/machines/{machine_id}", headers=_auth(owner)
    ).status_code == 204

    r = client.get("/v1alpha1/machines", headers=_auth(owner))
    assert r.status_code == 200
    item = next(m for m in r.json() if m["id"] == machine_id)
    assert item["pools"] == [{"id": pool["id"], "name": pool["name"]}]
    assert item["sandbox_capable"] is True
    assert item["argv_capable"] is True
    assert item["unsandboxed_argv_capable"] is False
    assert item["module_capable"] is True


def test_machines_listing_defaults_pools_to_an_empty_list_when_unbound(
    make_client, db
):
    client = make_client()
    owner = _new_user(db)
    machine_id = _enrol(db, owner, "unbound-chip")

    r = client.get("/v1alpha1/machines", headers=_auth(owner))
    assert r.status_code == 200
    item = next(m for m in r.json() if m["id"] == machine_id)
    assert item["pools"] == []
    assert item["sandbox_capable"] is False
    assert item["argv_capable"] is False
    assert item["unsandboxed_argv_capable"] is False
    assert item["module_capable"] is False


# ---------------------------------------------------------------------------
# invite creation: owner only, 404 doctrine
# ---------------------------------------------------------------------------


def test_a_stranger_creating_an_invite_gets_404(make_client, db):
    client = make_client()
    owner = _new_user(db)
    stranger = _new_user(db)
    pool = _create_pool(client, owner).json()

    r = _invite(client, stranger, pool["id"])
    assert r.status_code == 404


def test_a_member_who_is_not_the_owner_cannot_create_an_invite(make_client, db):
    client = make_client()
    owner = _new_user(db)
    member = _new_user(db)
    pool = _create_pool(client, owner).json()
    _add_member(db, pool["id"], member)

    r = _invite(client, member, pool["id"])
    assert r.status_code == 404

    # The owner, meanwhile, can.
    assert _invite(client, owner, pool["id"]).status_code == 201


def test_owner_creating_an_invite_on_an_unknown_pool_is_404(make_client, db):
    client = make_client()
    owner = _new_user(db)
    r = _invite(client, owner, str(uuid.uuid4()))
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# invite creation: expires_hours boundaries
# ---------------------------------------------------------------------------


def test_a_custom_expires_hours_is_honored_in_the_stored_row(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()

    r = _invite(client, owner, pool["id"], expires_hours=1)
    assert r.status_code == 201, r.text
    token = r.json()["token"]

    expires_at = _invite_expires_at(db, token)
    assert expires_at is not None
    now = datetime.now(timezone.utc)
    # Close to now + 1h — and, just as importantly, nowhere near the 720h
    # (30 day) default, which is what would come back if expires_hours were
    # silently ignored.
    assert now + timedelta(minutes=50) < expires_at < now + timedelta(minutes=70)


def test_expires_hours_default_is_720_hours_30_days(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()

    r = _invite(client, owner, pool["id"])
    assert r.status_code == 201, r.text
    token = r.json()["token"]

    expires_at = _invite_expires_at(db, token)
    assert expires_at is not None
    now = datetime.now(timezone.utc)
    assert now + timedelta(hours=719) < expires_at < now + timedelta(hours=721)


def test_expires_hours_zero_is_rejected_and_creates_no_row(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    before = _invite_count(db, pool["id"])

    r = _invite(client, owner, pool["id"], expires_hours=0)
    assert r.status_code == 400
    assert _invite_count(db, pool["id"]) == before


def test_expires_hours_negative_is_rejected_and_creates_no_row(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    before = _invite_count(db, pool["id"])

    r = _invite(client, owner, pool["id"], expires_hours=-5)
    assert r.status_code == 400
    assert _invite_count(db, pool["id"]) == before


def test_expires_hours_non_numeric_is_rejected_and_creates_no_row(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    before = _invite_count(db, pool["id"])

    r = _invite(client, owner, pool["id"], expires_hours="soon")
    assert r.status_code == 400
    assert _invite_count(db, pool["id"]) == before


def test_expires_hours_boolean_is_rejected_and_creates_no_row(make_client, db):
    """``bool`` is an ``int`` subclass in Python — ``True`` must not sneak
    past the numeric check and silently become a 1-hour invite."""
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    before = _invite_count(db, pool["id"])

    r = _invite(client, owner, pool["id"], expires_hours=True)
    assert r.status_code == 400
    assert _invite_count(db, pool["id"]) == before


def test_expires_hours_over_the_cap_is_rejected_and_creates_no_row(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    before = _invite_count(db, pool["id"])

    r = _invite(client, owner, pool["id"], expires_hours=2161)
    assert r.status_code == 400
    assert _invite_count(db, pool["id"]) == before


def test_expires_hours_exactly_at_the_cap_is_accepted(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()

    r = _invite(client, owner, pool["id"], expires_hours=2160)
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# invite creation: uses boundaries (Task 4)
# ---------------------------------------------------------------------------


def _invite_uses_remaining(db, pool_id: str) -> int:
    with db.cursor() as cur:
        cur.execute(
            "select uses_remaining from public.pool_invites where pool_id = %s",
            (pool_id,),
        )
        return cur.fetchone()["uses_remaining"]


def test_a_custom_uses_is_honored_in_the_stored_row(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()

    r = _invite(client, owner, pool["id"], uses=3)
    assert r.status_code == 201, r.text
    assert _invite_uses_remaining(db, pool["id"]) == 3


def test_uses_defaults_to_ten(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()

    r = _invite(client, owner, pool["id"])
    assert r.status_code == 201, r.text
    assert _invite_uses_remaining(db, pool["id"]) == 10


def test_uses_zero_is_rejected_and_creates_no_row(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    before = _invite_count(db, pool["id"])

    r = _invite(client, owner, pool["id"], uses=0)
    assert r.status_code == 400
    assert _invite_count(db, pool["id"]) == before


def test_uses_negative_is_rejected_and_creates_no_row(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    before = _invite_count(db, pool["id"])

    r = _invite(client, owner, pool["id"], uses=-3)
    assert r.status_code == 400
    assert _invite_count(db, pool["id"]) == before


def test_uses_non_numeric_is_rejected_and_creates_no_row(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    before = _invite_count(db, pool["id"])

    r = _invite(client, owner, pool["id"], uses="lots")
    assert r.status_code == 400
    assert _invite_count(db, pool["id"]) == before


def test_uses_float_is_rejected_and_creates_no_row(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    before = _invite_count(db, pool["id"])

    r = _invite(client, owner, pool["id"], uses=3.5)
    assert r.status_code == 400
    assert _invite_count(db, pool["id"]) == before


def test_uses_boolean_is_rejected_and_creates_no_row(make_client, db):
    """``bool`` is an ``int`` subclass in Python — ``True`` must not sneak
    past the numeric check and silently become a valid ``uses=1``."""
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    before = _invite_count(db, pool["id"])

    r = _invite(client, owner, pool["id"], uses=True)
    assert r.status_code == 400
    assert _invite_count(db, pool["id"]) == before


def test_uses_over_the_cap_is_rejected_and_creates_no_row(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    before = _invite_count(db, pool["id"])

    r = _invite(client, owner, pool["id"], uses=101)
    assert r.status_code == 400
    assert _invite_count(db, pool["id"]) == before


def test_uses_exactly_at_the_cap_is_accepted(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()

    r = _invite(client, owner, pool["id"], uses=100)
    assert r.status_code == 201, r.text
    assert _invite_uses_remaining(db, pool["id"]) == 100


# ---------------------------------------------------------------------------
# invite state / revoke (Task 4)
# ---------------------------------------------------------------------------


def test_get_invite_state_is_empty_when_none_outstanding(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()

    r = client.get(f"/v1alpha1/pools/{pool['id']}/invites", headers=_auth(owner))
    assert r.status_code == 200
    assert r.json() == {}


def test_get_invite_state_reflects_the_outstanding_invite_with_no_token_material(
    make_client, db
):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    invite = _invite(client, owner, pool["id"], uses=5)
    token = invite.json()["token"]
    token_hash = hash_invite_token(token)

    r = client.get(f"/v1alpha1/pools/{pool['id']}/invites", headers=_auth(owner))
    assert r.status_code == 200
    body = r.json()
    assert body["uses_remaining"] == 5
    assert "expires_at" in body
    assert "token_hash" not in body
    assert "token" not in body
    assert token not in r.text
    assert token_hash not in r.text


def test_get_invite_state_is_owner_only_404_doctrine(make_client, db):
    client = make_client()
    owner = _new_user(db)
    member = _new_user(db)
    stranger = _new_user(db)
    pool = _create_pool(client, owner).json()
    _add_member(db, pool["id"], member)
    _invite(client, owner, pool["id"])

    assert client.get(
        f"/v1alpha1/pools/{pool['id']}/invites", headers=_auth(member)
    ).status_code == 404
    assert client.get(
        f"/v1alpha1/pools/{pool['id']}/invites", headers=_auth(stranger)
    ).status_code == 404
    assert client.get(
        f"/v1alpha1/pools/{uuid.uuid4()}/invites", headers=_auth(owner)
    ).status_code == 404


def test_invite_state_and_revoke_require_authentication(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()

    assert client.get(f"/v1alpha1/pools/{pool['id']}/invites").status_code == 401
    assert client.delete(f"/v1alpha1/pools/{pool['id']}/invites").status_code == 401


def test_delete_invites_revokes_and_returns_the_count(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    spent = _invite(client, owner, pool["id"], uses=1).json()["token"]
    valid = _invite(client, owner, pool["id"], uses=3).json()["token"]

    joiner = _new_unadmitted_user(db)
    consumed = client.post(
        "/v1alpha1/invites/accept", json={"token": spent}, headers=_auth(joiner)
    )
    assert consumed.status_code == 200

    r = client.delete(f"/v1alpha1/pools/{pool['id']}/invites", headers=_auth(owner))
    assert r.status_code == 200
    assert r.json() == {"revoked": 2}

    assert client.get(
        f"/v1alpha1/pools/{pool['id']}/invites", headers=_auth(owner)
    ).json() == {}

    # The still-valid invite is gone too — revoke deletes everything, spent
    # or not — so consuming it now falls into the same ``None`` fold as an
    # unknown token.
    second_joiner = _new_unadmitted_user(db)
    again = client.post(
        "/v1alpha1/invites/accept", json={"token": valid}, headers=_auth(second_joiner)
    )
    assert again.status_code == 404


def test_delete_invites_is_owner_only(make_client, db):
    client = make_client()
    owner = _new_user(db)
    member = _new_user(db)
    pool = _create_pool(client, owner).json()
    _add_member(db, pool["id"], member)
    _invite(client, owner, pool["id"])

    r = client.delete(f"/v1alpha1/pools/{pool['id']}/invites", headers=_auth(member))
    assert r.status_code == 404
    # Unaffected: the owner's invite is still outstanding.
    assert client.get(
        f"/v1alpha1/pools/{pool['id']}/invites", headers=_auth(owner)
    ).json() != {}


def test_regenerate_after_revoke_mints_a_working_link(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    _invite(client, owner, pool["id"])

    revoke = client.delete(f"/v1alpha1/pools/{pool['id']}/invites", headers=_auth(owner))
    assert revoke.status_code == 200
    assert revoke.json()["revoked"] == 1

    regenerated = _invite(client, owner, pool["id"])
    assert regenerated.status_code == 201
    token = regenerated.json()["token"]

    joiner = _new_unadmitted_user(db)
    accept = client.post(
        "/v1alpha1/invites/accept", json={"token": token}, headers=_auth(joiner)
    )
    assert accept.status_code == 200
    assert accept.json()["pool_id"] == pool["id"]


# ---------------------------------------------------------------------------
# accept: joining a workspace (which is no longer being admitted)
# ---------------------------------------------------------------------------


def test_accepting_a_valid_invite_banks_the_join_and_does_not_admit(
    make_client, db
):
    """INVERTED BY 0009, deliberately. This was
    ``test_accepting_a_valid_invite_admits_and_joins_and_me_reflects_it``
    and asserted that accepting flipped ``/me``'s ``admitted`` to True and
    seated the caller in the pool immediately. Accepting an invite is now
    joining a WORKSPACE, not being let into the product: an un-admitted
    caller's join is banked on their access request and materialises when
    an admin approves them, so ``joined`` comes back False and ``/me``
    still says not admitted. See docs/superpowers/specs/
    2026-08-04-signup-profile-and-access-requests-design.md.
    """
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    invite = _invite(client, owner, pool["id"])
    token = invite.json()["token"]

    joiner = _new_unadmitted_user(db)
    before = client.get("/v1alpha1/me", headers=_auth(joiner))
    assert before.status_code == 200
    assert before.json()["admitted"] is False

    accepted = client.post(
        "/v1alpha1/invites/accept", json={"token": token}, headers=_auth(joiner)
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["pool_id"] == pool["id"]
    assert body["name"] == pool["name"]
    assert body["joined"] is False

    after = client.get("/v1alpha1/me", headers=_auth(joiner))
    assert after.json()["admitted"] is False

    # Not a member yet — and the pool read is member-scoped, so it 404s the
    # same as any pool that is not yours.
    got = client.get(f"/v1alpha1/pools/{pool['id']}", headers=_auth(joiner))
    assert got.status_code == 404

    # The join is banked, waiting on an approval.
    with db.cursor() as cur:
        cur.execute(
            "select pending_pool_id from public.access_requests where user_id = %s",
            (joiner,),
        )
        assert str(cur.fetchone()["pending_pool_id"]) == pool["id"]


def test_accepting_a_valid_invite_seats_an_already_admitted_account(
    make_client, db
):
    """The other half of the split: admission is not this route's business,
    but an account that already has it joins outright, exactly as before."""
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    token = _invite(client, owner, pool["id"]).json()["token"]

    joiner = _new_user(db)  # admitted by default
    accepted = client.post(
        "/v1alpha1/invites/accept", json={"token": token}, headers=_auth(joiner)
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["joined"] is True

    got = client.get(f"/v1alpha1/pools/{pool['id']}", headers=_auth(joiner))
    assert got.status_code == 200
    assert joiner in [m["user_id"] for m in got.json()["members"]]


def test_accepting_an_already_consumed_token_is_404(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    # Explicit uses=1: the default is now 10 (Task 4), and this test's whole
    # point is a single-use invite's second redemption being refused.
    token = _invite(client, owner, pool["id"], uses=1).json()["token"]

    first_joiner = _new_unadmitted_user(db)
    ok = client.post(
        "/v1alpha1/invites/accept",
        json={"token": token},
        headers=_auth(first_joiner),
    )
    assert ok.status_code == 200

    second_joiner = _new_unadmitted_user(db)
    again = client.post(
        "/v1alpha1/invites/accept",
        json={"token": token},
        headers=_auth(second_joiner),
    )
    assert again.status_code == 404
    assert client.get("/v1alpha1/me", headers=_auth(second_joiner)).json()[
        "admitted"
    ] is False


def test_accepting_an_expired_invite_is_404(make_client, db):
    from datetime import datetime, timedelta, timezone

    from flashml_cloud_api import db as dbmod

    client = make_client()
    owner = _new_user(db)
    pool_row = dbmod.create_pool(db, name="Expiring", owner_id=owner)
    token = "fmi_" + uuid.uuid4().hex
    dbmod.create_pool_invite(
        db,
        pool_id=pool_row["id"],
        created_by=owner,
        token_hash=hash_invite_token(token),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        uses=5,
    )

    joiner = _new_unadmitted_user(db)
    r = client.post(
        "/v1alpha1/invites/accept", json={"token": token}, headers=_auth(joiner)
    )
    assert r.status_code == 404
    assert client.get("/v1alpha1/me", headers=_auth(joiner)).json()["admitted"] is False


def test_accepting_an_unknown_token_is_404(make_client, db):
    client = make_client()
    joiner = _new_unadmitted_user(db)
    r = client.post(
        "/v1alpha1/invites/accept",
        json={"token": "fmi_" + uuid.uuid4().hex},
        headers=_auth(joiner),
    )
    assert r.status_code == 404


def test_accept_requires_a_token_in_the_body(make_client, db):
    client = make_client()
    joiner = _new_unadmitted_user(db)
    for body in ({}, {"token": ""}, {"token": 5}):
        r = client.post("/v1alpha1/invites/accept", json=body, headers=_auth(joiner))
        assert r.status_code == 400, f"{body!r} -> {r.status_code}"


def test_accept_requires_authentication(make_client):
    client = make_client()
    r = client.post("/v1alpha1/invites/accept", json={"token": "fmi_x"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# the admission gate itself
# ---------------------------------------------------------------------------


def test_unadmitted_account_is_blocked_from_pool_create_but_not_reads(
    make_client, db
):
    client = make_client()
    user = _new_unadmitted_user(db)

    r = client.post("/v1alpha1/pools", json={"name": "Nope"}, headers=_auth(user))
    assert r.status_code == 403
    assert r.json() == {"detail": "invite required"}

    # Reads stay open: the console needs GET /me to know to show the
    # enter-invite screen, and job listing must not itself be gated.
    me = client.get("/v1alpha1/me", headers=_auth(user))
    assert me.status_code == 200
    assert me.json()["admitted"] is False

    jobs = client.get("/v1alpha1/jobs", headers=_auth(user))
    assert jobs.status_code == 200


def test_unadmitted_account_is_blocked_from_job_submission(make_client, db):
    client = make_client()
    user = _new_unadmitted_user(db)

    r = client.post(
        "/v1alpha1/jobs",
        json={"metadata": {"name": "x"}},
        headers=_auth(user),
    )
    assert r.status_code == 403
    assert r.json() == {"detail": "invite required"}


def test_unadmitted_account_is_blocked_from_job_submission_from_repo(
    make_client, db
):
    client = make_client()
    user = _new_unadmitted_user(db)

    r = client.post(
        "/v1alpha1/jobs/from-repo",
        json={"repo": "acme/trainer", "ref": "main"},
        headers=_auth(user),
    )
    assert r.status_code == 403
    assert r.json() == {"detail": "invite required"}
    assert client.fetch.calls == []


def test_unadmitted_account_is_blocked_from_device_approve(make_client, db):
    client = make_client()
    user = _new_unadmitted_user(db)

    r = client.post(
        "/v1alpha1/device/approve",
        json={"user_code": "AAAAAAAA"},
        headers=_auth(user),
    )
    assert r.status_code == 403
    assert r.json() == {"detail": "invite required"}


def test_admitted_account_is_not_blocked_from_pool_create(make_client, db):
    client = make_client()
    user = _new_user(db)
    r = client.post("/v1alpha1/pools", json={"name": "Fine"}, headers=_auth(user))
    assert r.status_code == 201


# ---------------------------------------------------------------------------
# secrets never leak
# ---------------------------------------------------------------------------


def test_raw_token_appears_exactly_once_and_token_hash_never_appears(
    make_client, db
):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()

    invite = _invite(client, owner, pool["id"])
    assert invite.status_code == 201
    raw_token = invite.json()["token"]
    assert raw_token.startswith("fmi_")
    assert invite.text.count(raw_token) == 1
    assert "token_hash" not in invite.text

    token_hash = hash_invite_token(raw_token)

    # Nowhere else the token (or its hash) could leak: pool list/get, /me,
    # and the accept response itself.
    joiner = _new_unadmitted_user(db)
    accepted = client.post(
        "/v1alpha1/invites/accept", json={"token": raw_token}, headers=_auth(joiner)
    )
    responses = [
        invite,
        accepted,
        client.get("/v1alpha1/pools", headers=_auth(owner)),
        client.get(f"/v1alpha1/pools/{pool['id']}", headers=_auth(owner)),
        client.get("/v1alpha1/me", headers=_auth(owner)),
    ]
    for r in responses:
        assert "token_hash" not in r.text
        assert token_hash not in r.text
    # The raw token appears exactly once across every response: the one
    # create-invite answer, and nowhere else — not even the accept response
    # that consumed it.
    total = sum(r.text.count(raw_token) for r in responses)
    assert total == 1


# ---------------------------------------------------------------------------
# pool machines: the workspace's fleet across all members (Task 2)
# ---------------------------------------------------------------------------


def test_pool_machines_lists_every_members_bound_machine(make_client, db):
    """The point of the tab: you see the workspace's compute, not just your
    own. `list_machines_for_owner` structurally cannot answer this."""
    owner = _new_user(db)
    _set_display_name(db, owner, "Ada")
    member = _new_user(db)
    _set_display_name(db, member, "Grace")
    client = make_client()
    pool = _create_pool(client, owner).json()
    _add_member(db, pool["id"], member)

    mine = _enrol(db, owner, "mine")
    theirs = _enrol(db, member, "theirs")
    unbound = _enrol(db, owner, "unbound")
    dbmod.bind_machine_pool(db, machine_id=mine, pool_id=pool["id"])
    dbmod.bind_machine_pool(db, machine_id=theirs, pool_id=pool["id"])

    rows = client.get(
        f"/v1alpha1/pools/{pool['id']}/machines", headers=_auth(owner)
    ).json()

    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {mine, theirs}, "unbound machines must not appear"
    assert unbound not in by_id
    assert by_id[theirs]["owner_display_name"] == "Grace"
    assert "token_hash" not in by_id[mine]


def test_pool_machines_omits_a_machine_whose_owner_left(make_client, db):
    """A binding left behind by someone who has left the pool is inert for
    placement (`pool_ids_for_machine`'s join). This view must agree, or the
    tab overstates the workspace's capacity."""
    owner = _new_user(db)
    leaver = _new_user(db)
    client = make_client()
    pool = _create_pool(client, owner).json()
    _add_member(db, pool["id"], leaver)

    abandoned = _enrol(db, leaver, "abandoned")
    dbmod.bind_machine_pool(db, machine_id=abandoned, pool_id=pool["id"])
    with db.cursor() as cur:
        cur.execute(
            "delete from public.pool_members where pool_id = %s and user_id = %s",
            (pool["id"], leaver),
        )

    rows = client.get(
        f"/v1alpha1/pools/{pool['id']}/machines", headers=_auth(owner)
    ).json()

    assert rows == []


def test_pool_machines_404s_for_non_member_and_for_a_malformed_id(make_client, db):
    """404 doctrine: a stranger and a garbage id get the same answer, so the
    route cannot be used to learn which pool ids are real."""
    owner = _new_user(db)
    stranger = _new_user(db)
    client = make_client()
    pool = _create_pool(client, owner).json()

    assert client.get(
        f"/v1alpha1/pools/{pool['id']}/machines", headers=_auth(stranger)
    ).status_code == 404
    assert client.get(
        "/v1alpha1/pools/not-a-uuid/machines", headers=_auth(owner)
    ).status_code == 404


# ---------------------------------------------------------------------------
# rename: owner-only, 404 doctrine, shared validation with create
# ---------------------------------------------------------------------------


def test_owner_can_rename_their_pool(make_client, db):
    owner = _new_user(db)
    client = make_client()
    pool = _create_pool(client, owner, name="Untitled").json()

    r = client.patch(
        f"/v1alpha1/pools/{pool['id']}",
        json={"name": "  Vision Lab  "},
        headers=_auth(owner),
    )

    assert r.status_code == 200
    assert r.json()["name"] == "Vision Lab", "the name is stored trimmed"
    # get_pool_route returns pool fields flat (see
    # test_create_list_get_round_trip), not nested under a "pool" key.
    assert client.get(
        f"/v1alpha1/pools/{pool['id']}", headers=_auth(owner)
    ).json()["name"] == "Vision Lab"


def test_a_member_who_is_not_the_owner_cannot_rename(make_client, db):
    """404, not 403 — the same answer a stranger gets. A 403 here would
    confirm the pool is real to someone outside it."""
    owner = _new_user(db)
    member = _new_user(db)
    stranger = _new_user(db)
    client = make_client()
    pool = _create_pool(client, owner, name="Vision Lab").json()
    _add_member(db, pool["id"], member)

    for who in (member, stranger):
        r = client.patch(
            f"/v1alpha1/pools/{pool['id']}",
            json={"name": "Hijacked"},
            headers=_auth(who),
        )
        assert r.status_code == 404
        assert r.json()["detail"] == "unknown pool"

    assert client.get(
        f"/v1alpha1/pools/{pool['id']}", headers=_auth(owner)
    ).json()["name"] == "Vision Lab"


def test_rename_rejects_empty_and_overlong_names(make_client, db):
    """Same validation as create — the two routes must agree on what a
    legal pool name is."""
    owner = _new_user(db)
    client = make_client()
    pool = _create_pool(client, owner).json()

    for bad in ("", "   ", 42, None):
        r = client.patch(
            f"/v1alpha1/pools/{pool['id']}", json={"name": bad},
            headers=_auth(owner),
        )
        assert r.status_code == 400, f"{bad!r} should be rejected"

    r = client.patch(
        f"/v1alpha1/pools/{pool['id']}", json={"name": "x" * 201},
        headers=_auth(owner),
    )
    assert r.status_code == 400
    assert "200 characters" in r.json()["detail"]


def test_rename_404s_on_a_malformed_pool_id(make_client, db):
    owner = _new_user(db)
    client = make_client()
    r = client.patch(
        "/v1alpha1/pools/not-a-uuid", json={"name": "x"}, headers=_auth(owner)
    )
    assert r.status_code == 404
