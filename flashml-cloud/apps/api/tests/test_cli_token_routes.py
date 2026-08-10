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
