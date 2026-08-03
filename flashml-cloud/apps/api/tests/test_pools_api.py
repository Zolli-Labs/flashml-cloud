"""Pools, invites, and the invite-only admission gate — the browser-facing
routes built on top of Task 9's db layer.

``POST /v1alpha1/pools`` creates a team and seats its creator; ``GET
/v1alpha1/pools`` and ``GET /v1alpha1/pools/{id}`` are member-scoped reads
(404, never 403, for a pool that exists but is not yours — same doctrine as
every other resource in this API); ``POST /v1alpha1/pools/{id}/invites``
mints a one-time link only the pool's OWNER may create; ``POST
/v1alpha1/invites/accept`` is the admission bootstrap itself, so it runs on
``current_user`` rather than ``admitted_user`` — the whole point is that an
un-admitted account can use it.

Runs against the same migrated Postgres as the rest of the suite, reusing
``test_jobs_from_repo``'s fixtures exactly as ``test_profile.py`` does.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

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


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_jwt(user_id)}"}


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
    # Close to now + 1h — and, just as importantly, nowhere near the 168h
    # (7 day) default, which is what would come back if expires_hours were
    # silently ignored.
    assert now + timedelta(minutes=50) < expires_at < now + timedelta(minutes=70)


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
# accept: the admission bootstrap
# ---------------------------------------------------------------------------


def test_accepting_a_valid_invite_admits_and_joins_and_me_reflects_it(
    make_client, db
):
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

    after = client.get("/v1alpha1/me", headers=_auth(joiner))
    assert after.json()["admitted"] is True

    got = client.get(f"/v1alpha1/pools/{pool['id']}", headers=_auth(joiner))
    assert got.status_code == 200
    assert joiner in [m["user_id"] for m in got.json()["members"]]


def test_accepting_an_already_consumed_token_is_404(make_client, db):
    client = make_client()
    owner = _new_user(db)
    pool = _create_pool(client, owner).json()
    token = _invite(client, owner, pool["id"]).json()["token"]

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
