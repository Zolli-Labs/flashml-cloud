"""What an `fmu_` token can and cannot do over HTTP.

The point of this file is the claim in the design: extending
``current_user`` makes every route already tagged `browser` reachable from
a CLI, and grants nothing beyond what the owner already had. Both halves
need pinning — the second more than the first.

Fixture wiring follows ``test_pools_api.py``: the shared client/db/JWT
helpers live in ``test_jobs_from_repo`` and are imported from there. The
plan named fixtures (`client`, `admitted_owner`, `jwt_headers`,
`machine_token`) that this suite does not actually define anywhere; the
equivalents here are ``make_client()``, ``_new_user(db)``,
``_jwt(user_id)`` and an enrolled machine's redeemed token.
"""
from __future__ import annotations

import uuid

from flashml_cloud_api import cli_auth, db as dbmod

from test_jobs_from_repo import (  # noqa: F401 - fixtures
    _jwt,
    _new_user,
    db,
    make_client,
    settings,
    transport,
)


def _cli_token(db, owner: str) -> str:
    """An `fmu_` token belonging to ``owner``, through the real flow."""
    started = cli_auth.start_cli_code(db, "test-laptop")
    cli_auth.approve_cli_code(db, started["user_code"], owner)
    token = cli_auth.redeem_cli_code(db, started["device_code"])
    assert token is not None
    return token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_an_fmu_token_reaches_a_browser_tagged_route(make_client, db):
    client = make_client()
    owner = _new_user(db)
    r = client.get("/v1alpha1/me", headers=_bearer(_cli_token(db, owner)))
    assert r.status_code == 200


def test_an_fmu_token_resolves_to_its_owner_not_someone_else(make_client, db):
    client = make_client()
    owner = _new_user(db)
    # A second account exists concurrently, so "resolves to its owner" is a
    # real claim rather than "resolves to the only user in the database".
    _new_user(db)
    r = client.get("/v1alpha1/me", headers=_bearer(_cli_token(db, owner)))
    assert r.json()["id"] == owner


def test_a_revoked_token_stops_working_on_the_next_request(make_client, db):
    client = make_client()
    owner = _new_user(db)
    token = _cli_token(db, owner)

    assert client.get("/v1alpha1/me", headers=_bearer(token)).status_code == 200

    rows = dbmod.list_cli_credentials_for_owner(db, owner)
    assert dbmod.revoke_cli_credential_row(db, str(rows[0]["id"]), owner)

    after = client.get("/v1alpha1/me", headers=_bearer(token))
    assert after.status_code == 401


def test_an_unknown_fmu_token_is_401_not_500(make_client, db):
    client = make_client()
    r = client.get("/v1alpha1/me", headers=_bearer("fmu_" + "x" * 43))
    assert r.status_code == 401


def test_an_un_admitted_owners_token_still_hits_the_admission_gate(make_client, db):
    """The credential grants its owner's access and not one step more."""
    client = make_client()
    owner = _new_user(db, admitted=False)
    token = _cli_token(db, owner)

    # /me is open to un-admitted accounts, by design.
    assert client.get("/v1alpha1/me", headers=_bearer(token)).status_code == 200
    # Anything that creates state is not.
    r = client.post("/v1alpha1/pools", headers=_bearer(token), json={"name": "nope"})
    assert r.status_code == 403


def test_a_machine_token_still_cannot_reach_a_browser_route(make_client, db):
    from flashml_cloud_api import enrolment

    client = make_client()
    owner = _new_user(db)
    started = enrolment.start_device_code(db, f"n-{uuid.uuid4().hex[:8]}", "h", "linux")
    enrolment.approve_device_code(db, started["user_code"], owner)
    machine_token = enrolment.redeem_device_code(db, started["device_code"])

    r = client.get("/v1alpha1/me", headers=_bearer(machine_token))
    assert r.status_code == 401


def test_a_supabase_jwt_still_works_unchanged(make_client, db):
    client = make_client()
    owner = _new_user(db)
    r = client.get("/v1alpha1/me", headers=_bearer(_jwt(owner)))
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# the HTTP device-code surface, routed by kind
# ---------------------------------------------------------------------------


def test_the_cli_device_flow_end_to_end_over_http(make_client, db):
    client = make_client()
    approver = _bearer(_jwt(_new_user(db)))

    start = client.post(
        "/v1alpha1/device/code", json={"kind": "cli", "label": "test-laptop"}
    )
    assert start.status_code == 200
    body = start.json()
    assert body["user_code"] and body["device_code"]
    assert body["verification_uri"].endswith("/activate")

    pending = client.post(
        "/v1alpha1/device/token", json={"device_code": body["device_code"]}
    )
    assert pending.status_code == 400
    assert pending.json()["error"] == "authorization_pending"

    approved = client.post(
        "/v1alpha1/device/approve",
        headers=approver,
        json={"user_code": body["user_code"]},
    )
    assert approved.status_code == 200
    assert approved.json()["kind"] == "cli"
    assert approved.json()["credential_id"]

    redeemed = client.post(
        "/v1alpha1/device/token", json={"device_code": body["device_code"]}
    )
    assert redeemed.status_code == 200
    assert redeemed.json()["token"].startswith("fmu_")
    assert redeemed.json()["token_type"] == "cli"


def test_a_cli_start_does_not_require_a_node_id(make_client):
    """The machine route demands one. A CLI has no node."""
    client = make_client()
    r = client.post("/v1alpha1/device/code", json={"kind": "cli"})
    assert r.status_code == 200


def test_a_machine_start_still_demands_a_valid_node_id(make_client):
    client = make_client()
    assert client.post("/v1alpha1/device/code", json={}).status_code == 400
    assert client.post(
        "/v1alpha1/device/code", json={"node_id": "bad id!"}
    ).status_code == 400


def test_an_unknown_kind_is_refused_rather_than_guessed(make_client):
    client = make_client()
    r = client.post("/v1alpha1/device/code", json={"kind": "printer"})
    assert r.status_code == 400


def test_a_machine_approval_still_reports_its_machine_id(make_client, db):
    """The machine response gains `kind` but keeps `machine_id`, so no agent
    in the field has to be updated in lockstep."""
    client = make_client()
    approver_id = _new_user(db)
    node = f"n-{uuid.uuid4().hex[:8]}"
    start = client.post(
        "/v1alpha1/device/code", json={"node_id": node}
    ).json()
    r = client.post(
        "/v1alpha1/device/approve",
        headers=_bearer(_jwt(approver_id)),
        json={"user_code": start["user_code"]},
    )
    assert r.status_code == 200
    assert r.json()["machine_id"]
    assert r.json()["kind"] == "machine"


def test_a_machine_device_code_cannot_be_redeemed_as_a_user_token(make_client, db):
    """Which flow a code belongs to is read off the stored row, never off
    the request — otherwise a machine's device_code buys an fmu_ token."""
    client = make_client()
    approver_id = _new_user(db)
    node = f"n-{uuid.uuid4().hex[:8]}"
    start = client.post("/v1alpha1/device/code", json={"node_id": node}).json()
    client.post(
        "/v1alpha1/device/approve",
        headers=_bearer(_jwt(approver_id)),
        json={"user_code": start["user_code"]},
    )
    redeemed = client.post(
        "/v1alpha1/device/token", json={"device_code": start["device_code"]}
    )
    assert redeemed.json()["token_type"] == "machine"
    assert redeemed.json()["token"].startswith("fmk_")


def test_approving_an_expired_cli_code_is_410(make_client, db):
    from datetime import datetime, timedelta, timezone

    client = make_client()
    approver = _bearer(_jwt(_new_user(db)))
    start = client.post("/v1alpha1/device/code", json={"kind": "cli"}).json()
    with db.cursor() as cur:
        cur.execute(
            "update public.device_codes set expires_at = %s where device_code = %s",
            (
                datetime.now(timezone.utc) - timedelta(seconds=1),
                start["device_code"],
            ),
        )
    r = client.post(
        "/v1alpha1/device/approve",
        headers=approver,
        json={"user_code": start["user_code"]},
    )
    assert r.status_code == 410


def test_a_pool_id_on_a_cli_approval_is_refused(make_client, db):
    """pool_id binds a MACHINE to a pool. A credential is not placed on, so
    silently ignoring it would accept a request that did not do what it
    said."""
    client = make_client()
    approver = _bearer(_jwt(_new_user(db)))
    start = client.post("/v1alpha1/device/code", json={"kind": "cli"}).json()
    r = client.post(
        "/v1alpha1/device/approve",
        headers=approver,
        json={"user_code": start["user_code"], "pool_id": str(uuid.uuid4())},
    )
    assert r.status_code == 400
