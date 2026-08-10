"""Connecting a GitHub App installation, and spending it at submit time.

The security property this file exists for is
`test_a_state_minted_by_someone_else_is_refused`. An `installation_id` is not
a secret — it sits in GitHub's own URLs and in the redirect back to us — so
the only thing standing between "an attacker learned an id" and "an attacker
reads that organisation's private source" is the user-bound single-use state.
Everything else here is ordinary plumbing by comparison.

No test reaches GitHub: the App's HTTP hop is an `httpx.MockTransport` and
the tarball fetch is the same injected callable `from-repo` already uses.
"""
from __future__ import annotations

import uuid

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.github_app import GitHubApp
from flashml_cloud_api.settings import Settings

from test_jobs_from_repo import (  # noqa: E402  (shared harness, same dir)
    COORDINATOR_URL,
    JWT_SECRET,
    OPERATOR_TOKEN,
    CLEAN_REPO,
    FakeCoordinatorTransport,
    RecordingFetch,
    _jwt,
    make_tarball,
)

APP_ID = "123456"
SLUG = "flashml"
INSTALLATION_ID = 4242


# A throwaway key: these tests never verify the JWT, only that the App can
# sign one at all. `test_github_app.py` is where the signature is checked.
def _private_pem() -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    return (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        .decode()
    )


PRIVATE_PEM = _private_pem()


@pytest.fixture(scope="module")
def settings(postgres_dsn) -> Settings:
    return Settings(
        supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
        supabase_jwt_secret=JWT_SECRET,
        supabase_service_key="",
        coordinator_url=COORDINATOR_URL,
        coordinator_operator_token=OPERATOR_TOKEN,
        require_auth=True,
        database_url=postgres_dsn,
        console_url="https://console.example",
        github_app_id=APP_ID,
        github_app_slug=SLUG,
        github_app_private_key=PRIVATE_PEM,
    )


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def _github_transport(
    *,
    account_login: str = "acme",
    token: str = "ghs_installation",
    status: int = 200,
) -> httpx.MockTransport:
    """Answers both App hops: read the installation, mint a token."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(
                201,
                json={
                    "token": token,
                    "expires_at": "2099-01-01T00:00:00Z",
                },
            )
        if status != 200:
            return httpx.Response(status, json={"message": "Not Found"})
        return httpx.Response(
            200,
            json={
                "id": INSTALLATION_ID,
                "account": {"login": account_login, "type": "Organization"},
                "repository_selection": "selected",
            },
        )

    return httpx.MockTransport(handler)


@pytest.fixture
def make_client(settings, postgres_dsn):
    clients = []

    def build(github_transport: httpx.MockTransport | None = None,
              app_settings: Settings | None = None):
        fetch = RecordingFetch(make_tarball(CLEAN_REPO))

        def connect() -> psycopg.Connection:
            conn = psycopg.connect(
                postgres_dsn, row_factory=dict_row, connect_timeout=5
            )
            conn.autocommit = True
            return conn

        used = app_settings or settings
        github_app = GitHubApp(
            used, transport=github_transport or _github_transport()
        )
        app = create_cloud_app(
            used,
            connect=connect,
            transport=FakeCoordinatorTransport(),
            fetch_repo=fetch,
            github_app=github_app,
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        client.fetch = fetch  # type: ignore[attr-defined]
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def _new_user(db) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, now())",
            (user_id,),
        )
    return user_id


def _auth(user_id: str) -> dict:
    return {"Authorization": f"Bearer {_jwt(user_id)}"}


def _state_for(client, user_id: str) -> str:
    response = client.post("/v1alpha1/github/install-url", headers=_auth(user_id))
    assert response.status_code == 200, response.text
    return httpx.URL(response.json()["url"]).params["state"]


# ---------------------------------------------------------------------------
# Starting the flow
# ---------------------------------------------------------------------------


def test_install_url_names_the_app_and_carries_a_state(make_client, db):
    client = make_client()
    user_id = _new_user(db)

    body = client.post("/v1alpha1/github/install-url", headers=_auth(user_id)).json()

    assert body["url"].startswith(f"https://github.com/apps/{SLUG}/installations/new")
    assert httpx.URL(body["url"]).params["state"]


def test_install_url_requires_a_signed_in_user(make_client, db):
    client = make_client()
    assert client.post("/v1alpha1/github/install-url").status_code == 401


# ---------------------------------------------------------------------------
# Finishing it — the binding
# ---------------------------------------------------------------------------


def test_a_good_state_connects_the_installation(make_client, db):
    client = make_client()
    user_id = _new_user(db)
    state = _state_for(client, user_id)

    response = client.post(
        "/v1alpha1/github/installations",
        headers=_auth(user_id),
        json={"installation_id": INSTALLATION_ID, "state": state},
    )

    assert response.status_code == 201, response.text
    listed = client.get(
        "/v1alpha1/github/installations", headers=_auth(user_id)
    ).json()
    assert [i["account_login"] for i in listed["installations"]] == ["acme"]


def test_a_state_minted_by_someone_else_is_refused(make_client, db):
    """THE test in this file.

    An attacker starts the flow as themselves and phishes a victim into
    completing the install. GitHub redirects the VICTIM's browser, so the
    callback arrives on the victim's session carrying the attacker's state.
    The user ids differ and nothing is bound.

    Without this, the attacker's account ends up holding an installation on
    the victim's organisation and can read all of its private source.
    """
    client = make_client()
    attacker, victim = _new_user(db), _new_user(db)
    attacker_state = _state_for(client, attacker)

    response = client.post(
        "/v1alpha1/github/installations",
        headers=_auth(victim),
        json={"installation_id": INSTALLATION_ID, "state": attacker_state},
    )

    assert response.status_code == 403
    for user_id in (attacker, victim):
        listed = client.get(
            "/v1alpha1/github/installations", headers=_auth(user_id)
        ).json()
        assert listed["installations"] == []


def test_a_state_cannot_be_replayed(make_client, db):
    client = make_client()
    user_id = _new_user(db)
    state = _state_for(client, user_id)
    payload = {"installation_id": INSTALLATION_ID, "state": state}

    assert client.post(
        "/v1alpha1/github/installations", headers=_auth(user_id), json=payload
    ).status_code == 201
    assert client.post(
        "/v1alpha1/github/installations", headers=_auth(user_id), json=payload
    ).status_code == 403


def test_a_missing_state_is_refused_without_asking_github(make_client, db):
    client = make_client()
    user_id = _new_user(db)

    response = client.post(
        "/v1alpha1/github/installations",
        headers=_auth(user_id),
        json={"installation_id": INSTALLATION_ID},
    )

    assert response.status_code in (400, 403)


def test_an_installation_github_does_not_recognise_is_not_stored(make_client, db):
    """A fabricated id that survives a stolen state still has to exist."""
    client = make_client(github_transport=_github_transport(status=404))
    user_id = _new_user(db)
    state = _state_for(client, user_id)

    response = client.post(
        "/v1alpha1/github/installations",
        headers=_auth(user_id),
        json={"installation_id": 999, "state": state},
    )

    assert response.status_code == 400
    listed = client.get(
        "/v1alpha1/github/installations", headers=_auth(user_id)
    ).json()
    assert listed["installations"] == []


# ---------------------------------------------------------------------------
# Listing and disconnecting
# ---------------------------------------------------------------------------


def test_the_list_reports_whether_the_app_is_configured_at_all(make_client, db):
    """The console reads this to decide whether to render a Connect button.
    Offering one on a deploy with no App sends people to a dead end."""
    client = make_client()
    user_id = _new_user(db)
    assert client.get(
        "/v1alpha1/github/installations", headers=_auth(user_id)
    ).json()["configured"] is True


def test_a_deployment_without_an_app_says_so_and_refuses_to_start_a_flow(
    make_client, db, postgres_dsn
):
    bare = Settings(
        supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
        supabase_jwt_secret=JWT_SECRET,
        supabase_service_key="",
        coordinator_url=COORDINATOR_URL,
        coordinator_operator_token=OPERATOR_TOKEN,
        require_auth=True,
        database_url=postgres_dsn,
        console_url="https://console.example",
    )
    client = make_client(app_settings=bare)
    user_id = _new_user(db)

    listed = client.get(
        "/v1alpha1/github/installations", headers=_auth(user_id)
    ).json()
    assert listed["configured"] is False
    assert client.post(
        "/v1alpha1/github/install-url", headers=_auth(user_id)
    ).status_code == 404


def test_disconnect_removes_it(make_client, db):
    client = make_client()
    user_id = _new_user(db)
    state = _state_for(client, user_id)
    client.post(
        "/v1alpha1/github/installations",
        headers=_auth(user_id),
        json={"installation_id": INSTALLATION_ID, "state": state},
    )

    response = client.delete(
        f"/v1alpha1/github/installations/{INSTALLATION_ID}", headers=_auth(user_id)
    )

    assert response.status_code == 204
    listed = client.get(
        "/v1alpha1/github/installations", headers=_auth(user_id)
    ).json()
    assert listed["installations"] == []


def test_disconnecting_someone_elses_installation_is_a_404(make_client, db):
    client = make_client()
    owner, stranger = _new_user(db), _new_user(db)
    state = _state_for(client, owner)
    client.post(
        "/v1alpha1/github/installations",
        headers=_auth(owner),
        json={"installation_id": INSTALLATION_ID, "state": state},
    )

    response = client.delete(
        f"/v1alpha1/github/installations/{INSTALLATION_ID}", headers=_auth(stranger)
    )

    assert response.status_code == 404
    # And the owner still has it — a stranger's DELETE must not take it.
    listed = client.get(
        "/v1alpha1/github/installations", headers=_auth(owner)
    ).json()
    assert len(listed["installations"]) == 1


# ---------------------------------------------------------------------------
# Spending it at submit time
# ---------------------------------------------------------------------------


def _connect(client, db, user_id: str, login: str = "acme") -> None:
    state = _state_for(client, user_id)
    response = client.post(
        "/v1alpha1/github/installations",
        headers=_auth(user_id),
        json={"installation_id": INSTALLATION_ID, "state": state},
    )
    assert response.status_code == 201, response.text


def test_a_connected_owner_gets_an_installation_token_on_the_fetch(
    make_client, db
):
    client = make_client(github_transport=_github_transport(account_login="acme"))
    user_id = _new_user(db)
    _connect(client, db, user_id)

    client.post(
        "/v1alpha1/jobs/from-repo",
        headers=_auth(user_id),
        json={"repo": "https://github.com/acme/trainer", "ref": "main"},
    )

    (call,) = client.fetch.calls
    assert call.owner == "acme"
    assert call.token == "ghs_installation"


def test_an_unconnected_owner_is_fetched_anonymously(make_client, db):
    """Today's exact behaviour for everyone who has not connected: a public
    repo works, a private one 404s with the message it already gives."""
    client = make_client()
    user_id = _new_user(db)

    client.post(
        "/v1alpha1/jobs/from-repo",
        headers=_auth(user_id),
        json={"repo": "https://github.com/acme/trainer", "ref": "main"},
    )

    (call,) = client.fetch.calls
    assert call.token is None


def test_a_token_is_not_minted_for_an_owner_the_user_has_no_installation_for(
    make_client, db
):
    """Connecting `acme` must not silently authenticate a fetch of
    `someone-else/...`. It would work — the token is scoped by GitHub — but
    it sends our App's credential at a repo the person never granted, and
    on a private repo it would either 404 confusingly or, worse, succeed."""
    client = make_client(github_transport=_github_transport(account_login="acme"))
    user_id = _new_user(db)
    _connect(client, db, user_id)

    client.post(
        "/v1alpha1/jobs/from-repo",
        headers=_auth(user_id),
        json={"repo": "https://github.com/other-org/trainer", "ref": "main"},
    )

    (call,) = client.fetch.calls
    assert call.owner == "other-org"
    assert call.token is None


def test_another_users_connection_does_not_authenticate_my_fetch(make_client, db):
    """Installations are per-user. Alice connecting `acme` must not let Bob
    read `acme`'s private repos through our App."""
    client = make_client(github_transport=_github_transport(account_login="acme"))
    alice, bob = _new_user(db), _new_user(db)
    _connect(client, db, alice)

    client.post(
        "/v1alpha1/jobs/from-repo",
        headers=_auth(bob),
        json={"repo": "https://github.com/acme/trainer", "ref": "main"},
    )

    (call,) = client.fetch.calls
    assert call.token is None
