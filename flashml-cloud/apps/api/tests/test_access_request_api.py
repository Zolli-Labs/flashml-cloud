"""POST /v1alpha1/access-request — the onboarding form.

Sits on `current_user`, never `admitted_user`: an account that has not been
admitted is exactly who submits this. Gating it behind admission would make
the only route into the product require having already passed it.
"""
from __future__ import annotations

from flashml_cloud_api import db as dbmod
from flashml_cloud_api.access import parse_submission
from test_jobs_from_repo import (  # noqa: F401 - fixtures
    _jwt, _new_user, db, make_client, settings, transport,
)

VALID = {
    "first_name": "Ha",
    "last_name": "Nguyen",
    "company_name": "VinAI",
    "role": "researcher",
    "team_size": "2_5",
    "use_case": "Fine-tune a 7B model across the lab's machines.",
    "compute_sources": ["own_machines", "colab"],
    "heard_from": "github",
    "linkedin_url": "linkedin.com/in/hanguyen",
}


def _auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_jwt(user_id)}"}


def _access_requests_row(db, user_id: str) -> dict | None:
    with db.cursor() as cur:
        cur.execute(
            "select * from public.access_requests where user_id = %s", (user_id,)
        )
        return cur.fetchone()


def test_submitting_moves_the_account_to_pending(make_client, db):
    client = make_client()
    user = _new_user(db, admitted=False)
    r = client.post("/v1alpha1/access-request", json=VALID, headers=_auth(user))
    assert r.status_code == 200, r.text
    assert client.get("/v1alpha1/me", headers=_auth(user)).json()["access"] == "pending"


def test_submitting_does_not_admit(make_client, db):
    client = make_client()
    user = _new_user(db, admitted=False)
    client.post("/v1alpha1/access-request", json=VALID, headers=_auth(user))
    assert client.get("/v1alpha1/me", headers=_auth(user)).json()["admitted"] is False


def test_email_domain_is_derived_server_side(make_client, db):
    client = make_client()
    user = _new_user(db, admitted=False)
    with db.cursor() as cur:
        cur.execute(
            "update auth.users set email = %s where id = %s", ("ha@vinai.io", user)
        )
    client.post("/v1alpha1/access-request", json=VALID, headers=_auth(user))
    with db.cursor() as cur:
        cur.execute(
            "select email_domain, is_personal_email from public.profiles where id = %s",
            (user,),
        )
        row = cur.fetchone()
    assert row["email_domain"] == "vinai.io"
    assert row["is_personal_email"] is False


def test_a_client_supplied_email_domain_is_ignored(make_client, db):
    """The domain is a derived fact, not a claim. Accepting it from the body
    would let anyone label themselves as any company."""
    client = make_client()
    user = _new_user(db, admitted=False)
    with db.cursor() as cur:
        cur.execute(
            "update auth.users set email = %s where id = %s",
            ("minh@gmail.com", user),
        )
    client.post(
        "/v1alpha1/access-request",
        json={**VALID, "email_domain": "openai.com", "is_personal_email": False},
        headers=_auth(user),
    )
    with db.cursor() as cur:
        cur.execute("select email_domain from public.profiles where id = %s", (user,))
        assert cur.fetchone()["email_domain"] == "gmail.com"


def test_validation_failure_is_a_400_naming_the_field(make_client, db):
    client = make_client()
    r = client.post(
        "/v1alpha1/access-request",
        json={**VALID, "role": "wizard"},
        headers=_auth(_new_user(db, admitted=False)),
    )
    assert r.status_code == 400
    assert "role" in r.json()["detail"]


def test_resubmitting_while_pending_is_allowed(make_client, db):
    client = make_client()
    user = _new_user(db, admitted=False)
    client.post("/v1alpha1/access-request", json=VALID, headers=_auth(user))
    r = client.post(
        "/v1alpha1/access-request",
        json={**VALID, "company_name": "VinAI Research"},
        headers=_auth(user),
    )
    assert r.status_code == 200


def test_submitting_after_a_decision_is_a_409(make_client, db):
    """An admitted account edits its profile through PATCH /me. Letting it
    re-submit here would silently reset a decided request to pending.

    `_new_user(db)` (admitted=True) is precisely the state a product owner
    creates by hand-setting `admitted_at` (the 0009 flow): admitted, but
    with NO access_requests row at all. `access_state_for` still reports
    "admitted" for it via the admitted_at fallback, and the route must
    refuse the submission outright — not just answer 409, but leave no
    fresh access_requests row behind for this account to be found "pending"
    under later.
    """
    client = make_client()
    user = _new_user(db)  # admitted, so backfilled/decided — no request row
    r = client.post("/v1alpha1/access-request", json=VALID, headers=_auth(user))
    assert r.status_code == 409
    assert _access_requests_row(db, user) is None


def test_submitting_to_a_declined_account_is_a_409(make_client, db):
    """A declined account must not be able to re-open its own request by
    resubmitting the form."""
    client = make_client()
    user = _new_user(db, admitted=False)
    submission = parse_submission(VALID)
    dbmod.submit_access_request(
        db, user, submission, email_domain=None, is_personal_email=None
    )
    dbmod.decline_access_request(db, user, decided_by=user)
    assert dbmod.access_state_for(db, user) == "declined"

    r = client.post("/v1alpha1/access-request", json=VALID, headers=_auth(user))
    assert r.status_code == 409

    row = _access_requests_row(db, user)
    assert row is not None
    assert row["status"] == "declined"


def test_requires_a_session(make_client, db):
    assert make_client().post("/v1alpha1/access-request", json=VALID).status_code == 401
