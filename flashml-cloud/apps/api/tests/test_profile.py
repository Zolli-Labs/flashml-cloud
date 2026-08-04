"""GET/PATCH /v1alpha1/me — the account's own profile and access state.

PATCH accepts the profile fields a user owns: display_name, first_name,
last_name, company_name, role, team_size. Email and avatar belong to the
identity provider, ``github_login`` is written by enrolment, and the role
flags (is_host, is_developer, is_admin, admitted_at) are roles rather than
preferences — the route reads only the fields it knows and takes the owner
from the verified JWT rather than the body.

GET must stay reachable on ``current_user`` in every access state: it is
how the console learns which screen to show instead of the product.

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


# -- access state -----------------------------------------------------------

def test_me_reports_needs_onboarding_for_a_fresh_account(make_client, db):
    client = make_client()
    user = _new_user(db, admitted=False)
    assert client.get("/v1alpha1/me", headers=_auth(user)).json()["access"] == (
        "needs_onboarding"
    )


def test_me_keeps_the_admitted_boolean_alongside_access(make_client, db):
    """Additive: `admitted` predates this and other readers rely on it."""
    client = make_client()
    body = client.get("/v1alpha1/me", headers=_auth(_new_user(db))).json()
    assert body["admitted"] is True
    assert body["access"] == "admitted"


def test_me_is_readable_in_every_access_state(make_client, db):
    """The one route an un-admitted account MUST reach — it is how the
    console learns which screen to show instead of the product."""
    client = make_client()
    assert client.get(
        "/v1alpha1/me", headers=_auth(_new_user(db, admitted=False))
    ).status_code == 200


# -- widened PATCH ----------------------------------------------------------

def test_patch_writes_the_profile_fields(make_client, db):
    client = make_client()
    user = _new_user(db)
    r = client.patch(
        "/v1alpha1/me",
        json={
            "first_name": "Ha",
            "last_name": "Nguyen",
            "company_name": "VinAI",
            "role": "researcher",
            "team_size": "2_5",
        },
        headers=_auth(user),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["first_name"] == "Ha"
    assert body["company_name"] == "VinAI"
    assert body["role"] == "researcher"


def test_patch_rejects_an_unknown_role(make_client, db):
    client = make_client()
    r = client.patch(
        "/v1alpha1/me", json={"role": "wizard"}, headers=_auth(_new_user(db))
    )
    assert r.status_code == 400


def test_patch_refuses_to_grant_admin(make_client, db):
    """The escalation this route's shape exists to prevent."""
    client = make_client()
    user = _new_user(db)
    client.patch("/v1alpha1/me", json={"is_admin": True}, headers=_auth(user))
    with db.cursor() as cur:
        cur.execute("select is_admin from public.profiles where id = %s", (user,))
        assert cur.fetchone()["is_admin"] is False


def test_patch_refuses_to_grant_admission(make_client, db):
    client = make_client()
    user = _new_user(db, admitted=False)
    client.patch(
        "/v1alpha1/me", json={"admitted_at": "2020-01-01T00:00:00Z"}, headers=_auth(user)
    )
    assert client.get("/v1alpha1/me", headers=_auth(user)).json()["admitted"] is False


def test_patch_refuses_the_role_flags(make_client, db):
    client = make_client()
    user = _new_user(db)
    client.patch(
        "/v1alpha1/me",
        json={"is_host": True, "is_developer": True, "github_login": "spoofed"},
        headers=_auth(user),
    )
    body = client.get("/v1alpha1/me", headers=_auth(user)).json()
    assert body["is_host"] is False
    assert body["is_developer"] is False
    assert body["github_login"] is None


def test_display_name_still_works_unchanged(make_client, db):
    client = make_client()
    user = _new_user(db)
    r = client.patch(
        "/v1alpha1/me", json={"display_name": "Ada"}, headers=_auth(user)
    )
    assert r.json()["display_name"] == "Ada"
