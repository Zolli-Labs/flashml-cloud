"""PATCH /v1alpha1/me — the one profile field a user owns.

Display name is the only writable field. Email and avatar belong to the
identity provider, ``github_login`` is written by enrolment, and the role
flags are roles rather than preferences, so the route must accept exactly
one key and must take the owner from the verified JWT rather than the body.

Runs against the same migrated Postgres as the rest of the suite.
"""

from __future__ import annotations

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


def test_sets_the_display_name_and_get_reflects_it(make_client, db):
    client = make_client()
    user = _new_user(db)

    r = client.patch(
        "/v1alpha1/me", json={"display_name": "Ada Lovelace"}, headers=_auth(user)
    )
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "Ada Lovelace"

    # The write is durable, not just echoed back.
    assert client.get("/v1alpha1/me", headers=_auth(user)).json()[
        "display_name"
    ] == "Ada Lovelace"


def test_trims_surrounding_whitespace(make_client, db):
    client = make_client()
    user = _new_user(db)
    r = client.patch(
        "/v1alpha1/me", json={"display_name": "  Grace Hopper  "}, headers=_auth(user)
    )
    assert r.status_code == 200
    assert r.json()["display_name"] == "Grace Hopper"


# `upsert_profile` coalesces NULL to "keep the existing value", so an empty
# submission would silently do nothing the user can see. Clearing a field and
# not touching it are different intentions; the empty one has to be refused
# rather than quietly ignored.
def test_empty_name_is_refused_not_silently_ignored(make_client, db):
    client = make_client()
    user = _new_user(db)
    client.patch("/v1alpha1/me", json={"display_name": "Set"}, headers=_auth(user))

    for value in ("", "   "):
        r = client.patch(
            "/v1alpha1/me", json={"display_name": value}, headers=_auth(user)
        )
        assert r.status_code == 400, f"{value!r} -> {r.status_code}"

    # And the earlier value survived the refusals.
    assert client.get("/v1alpha1/me", headers=_auth(user)).json()["display_name"] == "Set"


def test_rejects_an_over_long_name(make_client, db):
    client = make_client()
    user = _new_user(db)
    r = client.patch(
        "/v1alpha1/me", json={"display_name": "x" * 81}, headers=_auth(user)
    )
    assert r.status_code == 400
    # The boundary itself is allowed.
    assert (
        client.patch(
            "/v1alpha1/me", json={"display_name": "y" * 80}, headers=_auth(user)
        ).status_code
        == 200
    )


def test_rejects_a_non_string(make_client, db):
    client = make_client()
    user = _new_user(db)
    r = client.patch("/v1alpha1/me", json={"display_name": 42}, headers=_auth(user))
    assert r.status_code == 400


def test_requires_authentication(make_client):
    client = make_client()
    assert client.patch("/v1alpha1/me", json={"display_name": "nobody"}).status_code == 401


# The body names a field, never an owner. Two users writing concurrently must
# each only be able to change their own row.
def test_one_user_cannot_write_anothers_profile(make_client, db):
    client = make_client()
    alice, bob = _new_user(db), _new_user(db)

    client.patch("/v1alpha1/me", json={"display_name": "Alice"}, headers=_auth(alice))
    client.patch(
        "/v1alpha1/me",
        # An id in the body must be ignored entirely.
        json={"display_name": "Bob", "id": alice},
        headers=_auth(bob),
    )

    assert client.get("/v1alpha1/me", headers=_auth(alice)).json()["display_name"] == "Alice"
    assert client.get("/v1alpha1/me", headers=_auth(bob)).json()["display_name"] == "Bob"


# Roles are not preferences. A client that sends them must not get them.
def test_role_flags_are_not_writable(make_client, db):
    client = make_client()
    user = _new_user(db)
    r = client.patch(
        "/v1alpha1/me",
        json={"display_name": "Chancer", "is_host": True, "is_developer": True},
        headers=_auth(user),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["is_host"] is False
    assert body["is_developer"] is False
