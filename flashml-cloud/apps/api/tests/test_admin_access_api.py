"""The access-request queue.

The 403 tests here are the ones that matter: this is the only surface that
grants product access, so "a non-admin cannot reach it" is the assertion
that stops the feature becoming a privilege-escalation bug.

Each 403 test asserts the *effect*, not only the status code. A route that
wrote first and refused afterwards would return 403 and still have admitted
the caller, and a status-only assertion would pass. So every refusal is
checked against the database (and /me) as well.

Note also what the plain user in these tests is: ``_new_user(db)`` is an
ADMITTED account. Admission is not admin. An ordinary customer of the
product, fully through the gate, still cannot open the gate for anyone else.
"""
from __future__ import annotations

from test_jobs_from_repo import (  # noqa: F401 - fixtures
    _jwt, _new_user, db, make_client, settings, transport,
)

VALID = {
    "first_name": "Ha", "last_name": "Nguyen", "company_name": "VinAI",
    "role": "researcher", "team_size": "2_5",
    "use_case": "Fine-tune across the lab.", "compute_sources": ["own_machines"],
}


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_jwt(user_id)}"}


def _admin(db) -> str:
    """An admin account. `is_admin` is set with a direct UPDATE because
    there is no route that grants it — deliberately, and the absence is
    asserted below in `test_no_route_grants_admin`."""
    user = _new_user(db)
    with db.cursor() as cur:
        cur.execute("update public.profiles set is_admin = true where id = %s", (user,))
    return user


def _pending(client, db) -> str:
    user = _new_user(db, admitted=False)
    client.post("/v1alpha1/access-request", json=VALID, headers=_auth(user))
    return user


def _request_row(db, user_id: str) -> dict | None:
    with db.cursor() as cur:
        cur.execute(
            "select status, decided_by from public.access_requests where user_id = %s",
            (user_id,),
        )
        return cur.fetchone()


def _admitted_at(db, user_id: str):
    with db.cursor() as cur:
        cur.execute("select admitted_at from public.profiles where id = %s", (user_id,))
        return cur.fetchone()["admitted_at"]


# -- authorization ----------------------------------------------------------

def test_a_plain_user_cannot_list_the_queue(make_client, db):
    """403, and no queue data in the body: a refusal that still returned the
    rows would leak every applicant's email and company."""
    client = make_client()
    waiting = _pending(client, db)
    r = client.get("/v1alpha1/admin/access-requests", headers=_auth(_new_user(db)))
    assert r.status_code == 403
    assert waiting not in r.text


def test_a_plain_user_cannot_approve(make_client, db):
    """The escalation path: approving yourself.

    The status code alone is not the assertion. A route that admitted first
    and refused afterwards would answer 403 with the account already through
    the gate, so this checks /me AND the two rows the write would have
    touched.
    """
    client = make_client()
    attacker = _new_user(db, admitted=False)
    client.post("/v1alpha1/access-request", json=VALID, headers=_auth(attacker))
    r = client.post(
        f"/v1alpha1/admin/access-requests/{attacker}/approve", headers=_auth(attacker)
    )
    assert r.status_code == 403
    body = client.get("/v1alpha1/me", headers=_auth(attacker)).json()
    assert body["admitted"] is False
    assert body["access"] == "pending"
    assert _request_row(db, attacker)["status"] == "pending"
    assert _admitted_at(db, attacker) is None


def test_an_admitted_non_admin_cannot_approve_someone_else(make_client, db):
    """Being through the gate is not being the gatekeeper."""
    client = make_client()
    customer = _new_user(db)  # admitted, not admin
    victim = _pending(client, db)
    r = client.post(
        f"/v1alpha1/admin/access-requests/{victim}/approve", headers=_auth(customer)
    )
    assert r.status_code == 403
    assert _request_row(db, victim)["status"] == "pending"
    assert _admitted_at(db, victim) is None


def test_a_plain_user_cannot_decline(make_client, db):
    client = make_client()
    victim = _pending(client, db)
    r = client.post(
        f"/v1alpha1/admin/access-requests/{victim}/decline",
        headers=_auth(_new_user(db)),
    )
    assert r.status_code == 403
    assert _request_row(db, victim)["status"] == "pending"
    assert client.get("/v1alpha1/me", headers=_auth(victim)).json()["access"] == "pending"


def test_the_queue_requires_a_session(make_client, db):
    assert make_client().get("/v1alpha1/admin/access-requests").status_code == 401


def test_deciding_requires_a_session(make_client, db):
    """Unauthenticated, on both write routes, with the effect checked: an
    anonymous caller must not be able to decide anything."""
    client = make_client()
    user = _pending(client, db)
    assert client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve"
    ).status_code == 401
    assert client.post(
        f"/v1alpha1/admin/access-requests/{user}/decline"
    ).status_code == 401
    assert _request_row(db, user)["status"] == "pending"
    assert _admitted_at(db, user) is None


def test_a_machine_token_cannot_reach_the_queue(make_client, db):
    """An enrolled host agent is a credential, not an admin. It must not
    even reach the JWT decoder, let alone the queue."""
    from flashml_cloud_api import enrolment
    import uuid as _uuid

    client = make_client()
    owner = _new_user(db)
    started = enrolment.start_device_code(db, f"n-{_uuid.uuid4().hex[:8]}", "h", "linux")
    enrolment.approve_device_code(db, started["user_code"], owner)
    token = enrolment.redeem_device_code(db, started["device_code"])
    r = client.get(
        "/v1alpha1/admin/access-requests",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 401


def test_no_route_grants_admin(make_client, db):
    """`is_admin` is granted by one manual UPDATE and by nothing else. A
    route that could set it from a request body would make every other 403
    in this file decorative, so both body-accepting profile routes are
    pinned: neither may write the flag, and neither may report it back as
    though it had."""
    client = make_client()
    user = _new_user(db, admitted=False)
    client.post(
        "/v1alpha1/access-request",
        json={**VALID, "is_admin": True},
        headers=_auth(user),
    )
    r = client.patch("/v1alpha1/me", json={"is_admin": True}, headers=_auth(user))
    assert r.json().get("is_admin") is not True
    with db.cursor() as cur:
        cur.execute("select is_admin from public.profiles where id = %s", (user,))
        assert cur.fetchone()["is_admin"] is False

    # And the flag it never granted is still the flag the gate reads.
    assert client.get(
        "/v1alpha1/admin/access-requests", headers=_auth(user)
    ).status_code == 403


# -- the queue --------------------------------------------------------------

def test_an_admin_sees_pending_requests(make_client, db):
    client = make_client()
    admin = _admin(db)
    user = _pending(client, db)
    rows = client.get(
        "/v1alpha1/admin/access-requests", headers=_auth(admin)
    ).json()
    assert any(r["user_id"] == user for r in rows)


def test_approve_admits_and_removes_from_the_queue(make_client, db):
    client = make_client()
    admin = _admin(db)
    user = _pending(client, db)

    r = client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    )
    assert r.status_code == 200, r.text

    assert client.get("/v1alpha1/me", headers=_auth(user)).json()["access"] == "admitted"
    rows = client.get("/v1alpha1/admin/access-requests", headers=_auth(admin)).json()
    assert all(row["user_id"] != user for row in rows)


def test_the_decision_records_who_made_it(make_client, db):
    client = make_client()
    admin = _admin(db)
    user = _pending(client, db)
    client.post(f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin))
    assert str(_request_row(db, user)["decided_by"]) == admin


def test_decline_sets_declined_without_admitting(make_client, db):
    client = make_client()
    admin = _admin(db)
    user = _pending(client, db)

    client.post(f"/v1alpha1/admin/access-requests/{user}/decline", headers=_auth(admin))

    body = client.get("/v1alpha1/me", headers=_auth(user)).json()
    assert body["access"] == "declined"
    assert body["admitted"] is False


def test_a_decided_request_is_listable_by_status(make_client, db):
    client = make_client()
    admin = _admin(db)
    user = _pending(client, db)
    client.post(f"/v1alpha1/admin/access-requests/{user}/decline", headers=_auth(admin))

    rows = client.get(
        "/v1alpha1/admin/access-requests?status=declined", headers=_auth(admin)
    ).json()
    assert any(r["user_id"] == user for r in rows)


def test_an_unknown_status_is_a_400(make_client, db):
    """`list_access_requests` does not validate its argument against the
    CHECK constraint — an unknown string silently returns []. An empty list
    reads as "nobody is waiting", which is the wrong answer to a typo."""
    r = make_client().get(
        "/v1alpha1/admin/access-requests?status=approved", headers=_auth(_admin(db))
    )
    assert r.status_code == 400


def test_approving_an_unknown_user_is_a_404(make_client, db):
    client = make_client()
    r = client.post(
        "/v1alpha1/admin/access-requests/"
        "00000000-0000-0000-0000-000000000000/approve",
        headers=_auth(_admin(db)),
    )
    assert r.status_code == 404


def test_approving_twice_is_a_404_the_second_time(make_client, db):
    """Reports honestly that nothing changed rather than a success that
    did nothing."""
    client = make_client()
    admin = _admin(db)
    user = _pending(client, db)
    assert client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    ).status_code == 200
    assert client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    ).status_code == 404


def test_declining_an_already_approved_account_is_a_404(make_client, db):
    """Declining is for pending requests. Using it to revoke an admitted
    account would be a different, unaudited action — so it must not
    half-happen and report 200."""
    client = make_client()
    admin = _admin(db)
    user = _pending(client, db)
    client.post(f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin))
    r = client.post(
        f"/v1alpha1/admin/access-requests/{user}/decline", headers=_auth(admin)
    )
    assert r.status_code == 404
    assert _request_row(db, user)["status"] == "admitted"
    assert client.get("/v1alpha1/me", headers=_auth(user)).json()["admitted"] is True


def test_a_malformed_user_id_is_rejected(make_client, db):
    client = make_client()
    r = client.post(
        "/v1alpha1/admin/access-requests/not-a-uuid/approve", headers=_auth(_admin(db))
    )
    assert r.status_code in (400, 422)


def test_a_malformed_user_id_is_rejected_on_decline(make_client, db):
    """The same path segment reaches the same WHERE clause on both routes;
    a 500 from psycopg's DataError on one of them is still a 500."""
    client = make_client()
    r = client.post(
        "/v1alpha1/admin/access-requests/not-a-uuid/decline", headers=_auth(_admin(db))
    )
    assert r.status_code in (400, 422)
