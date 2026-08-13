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

import json
from dataclasses import replace

import httpx

from flashml_cloud_api.mailer import Mailer

from test_jobs_from_repo import (  # noqa: F401 - fixtures
    _jwt, _new_user, db, make_client, settings, transport,
)

VALID = {
    "first_name": "Ha", "last_name": "Nguyen", "company_name": "VinAI",
    "role": "researcher", "team_size": "2_5",
    "use_case": "Fine-tune across the lab.", "compute_sources": ["own_machines"],
    "linkedin_url": "linkedin.com/in/hanguyen",
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


def _pending(client, db, *, email: str | None = None) -> str:
    user = _new_user(db, admitted=False)
    if email is not None:
        # Seeded HERE rather than in `_new_user`: that helper is shared by
        # this file, test_profile, test_contributions, test_federated,
        # test_verification and test_db_pools, and giving every fixture
        # account an address could perturb suites this task never looks at
        # — `derive_email_facts` runs off exactly this column.
        with db.cursor() as cur:
            cur.execute(
                "update auth.users set email = %s where id = %s", (email, user)
            )
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
    """403, and no queue data in the body.

    The asset that must not leak from this route is the EMAIL ADDRESS.
    `list_access_requests` left-joins `auth.users` for `u.email` and returns
    it in every row, and holding the service-role key that can read that
    table is the entire reason this is a server route rather than a browser
    query. So the waiting account is seeded with a canary address and BOTH
    halves are asserted: absent from the refusal, and present in the admin's
    listing. Without the second half this would be asserting the absence of
    something that was never there — `_new_user` inserts `auth.users` rows
    with only an id, so `u.email` is NULL for every unseeded fixture and the
    negative assertion would pass against a route that leaked everything.
    """
    client = make_client()
    canary = "leaked-canary@example.com"
    waiting = _pending(client, db, email=canary)

    refused = client.get(
        "/v1alpha1/admin/access-requests", headers=_auth(_new_user(db))
    )
    assert refused.status_code == 403
    assert canary not in refused.text
    assert waiting not in refused.text

    # The canary is real: an admin does see it, so its absence above is the
    # gate working and not an empty column.
    allowed = client.get("/v1alpha1/admin/access-requests", headers=_auth(_admin(db)))
    assert allowed.status_code == 200
    assert canary in allowed.text


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
    """401, and nothing serialised into the body on the way out. An
    implementation that built the queue and then raised would pass a
    status-only assertion, so the same canary the sibling above uses is
    checked here too."""
    client = make_client()
    canary = "anonymous-canary@example.com"
    waiting = _pending(client, db, email=canary)

    r = client.get("/v1alpha1/admin/access-requests")
    assert r.status_code == 401
    assert canary not in r.text
    assert waiting not in r.text


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


def _machine_token(db) -> str:
    """A real, enrolled host-agent credential."""
    from flashml_cloud_api import enrolment
    import uuid as _uuid

    owner = _new_user(db)
    started = enrolment.start_device_code(db, f"n-{_uuid.uuid4().hex[:8]}", "h", "linux")
    enrolment.approve_device_code(db, started["user_code"], owner)
    return enrolment.redeem_device_code(db, started["device_code"])


def test_a_machine_token_cannot_reach_the_queue(make_client, db):
    """An enrolled host agent is a credential, not an admin. It must not
    even reach the JWT decoder, let alone the queue — and that has to hold
    on the two routes that WRITE, not only on the read.

    Not a live bug today: `current_user` rejects anything shaped like a
    machine token before the JWT decoder runs, so the two credential kinds
    are disjoint. This is the regression guard for the day somebody adds an
    `or current_machine(request)` fallback, a shared "any valid credential"
    resolver, or an `admin_machine` for an ops script — at which point the
    second credential kind would reach the only two routes that grant
    product access.
    """
    client = make_client()
    token = _machine_token(db)
    headers = {"Authorization": f"Bearer {token}"}
    victim = _pending(client, db)

    assert client.get(
        "/v1alpha1/admin/access-requests", headers=headers
    ).status_code == 401
    assert client.post(
        f"/v1alpha1/admin/access-requests/{victim}/approve", headers=headers
    ).status_code == 401
    assert client.post(
        f"/v1alpha1/admin/access-requests/{victim}/decline", headers=headers
    ).status_code == 401

    # The effect, not only the status. A route that wrote and then refused
    # would satisfy all three assertions above — the mutation-7 shape.
    assert _request_row(db, victim)["status"] == "pending"
    assert _admitted_at(db, victim) is None
    assert client.get("/v1alpha1/me", headers=_auth(victim)).json()["admitted"] is False


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


# ---------------------------------------------------------------------------
# email on decision
#
# Exactly-once is structural here, not bookkept: `approve_access_request`
# and `decline_access_request` both filter on `status = 'pending'`, so a
# second call matches no row and the route 404s before the mailer is
# reached. That is why there is no sent-log table and no migration.
# ---------------------------------------------------------------------------


class FakeResend(httpx.AsyncBaseTransport):
    def __init__(self, status: int = 200):
        self.requests: list[httpx.Request] = []
        self._status = status

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        return httpx.Response(self._status, json={"id": "msg_1"})

    @property
    def sent(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


def _mail_client(make_client, settings, status: int = 200):
    """A client whose mailer is configured and pointed at a fake Resend."""
    resend = FakeResend(status=status)
    configured = replace(
        settings,
        resend_api_key="re_test_key",
        email_from="FlashML <no-reply@mail.example>",
    )
    client = make_client(mailer=Mailer(configured, transport=resend))
    return client, resend


def test_approving_emails_the_account(make_client, settings, db):
    client, resend = _mail_client(make_client, settings)
    admin = _admin(db)
    user = _pending(client, db, email="her@example.com")

    r = client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    )
    assert r.status_code == 200
    assert r.json()["status"] == "admitted"
    assert r.json()["emailed"] is True

    assert len(resend.sent) == 1
    assert resend.sent[0]["to"] == ["her@example.com"]
    assert "You're in" in resend.sent[0]["subject"]


def test_approving_twice_sends_exactly_one_email(make_client, settings, db):
    client, resend = _mail_client(make_client, settings)
    admin = _admin(db)
    user = _pending(client, db, email="her@example.com")

    first = client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    )
    second = client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    )
    assert first.status_code == 200
    assert second.status_code == 404
    assert len(resend.sent) == 1


def test_declining_sends_the_declined_email(make_client, settings, db):
    client, resend = _mail_client(make_client, settings)
    admin = _admin(db)
    user = _pending(client, db, email="them@example.com")

    r = client.post(
        f"/v1alpha1/admin/access-requests/{user}/decline", headers=_auth(admin)
    )
    assert r.status_code == 200
    assert r.json()["emailed"] is True
    assert len(resend.sent) == 1
    assert "About your FlashML request" in resend.sent[0]["subject"]
    assert "You're in" not in resend.sent[0]["subject"]


def test_a_provider_failure_still_admits_the_user(make_client, settings, db):
    """The admission is committed before the send. A 500 from Resend must
    cost the user their email, never their access."""
    client, resend = _mail_client(make_client, settings, status=500)
    admin = _admin(db)
    user = _pending(client, db, email="her@example.com")

    r = client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    )
    assert r.status_code == 200
    assert r.json()["emailed"] is False
    assert _request_row(db, user)["status"] == "admitted"
    assert _admitted_at(db, user) is not None


def test_an_account_with_no_address_is_still_admitted(make_client, settings, db):
    client, resend = _mail_client(make_client, settings)
    admin = _admin(db)
    user = _pending(client, db)  # no email seeded in auth.users

    r = client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    )
    assert r.status_code == 200
    assert r.json()["emailed"] is False
    assert resend.sent == []
    assert _admitted_at(db, user) is not None


def test_an_unconfigured_deploy_sends_nothing_and_still_works(make_client, db):
    """The default client has no mail configured — the shape every other
    test file in this suite runs under."""
    client = make_client()
    admin = _admin(db)
    user = _pending(client, db, email="her@example.com")

    r = client.post(
        f"/v1alpha1/admin/access-requests/{user}/approve", headers=_auth(admin)
    )
    assert r.status_code == 200
    assert r.json()["emailed"] is False
    assert _admitted_at(db, user) is not None
