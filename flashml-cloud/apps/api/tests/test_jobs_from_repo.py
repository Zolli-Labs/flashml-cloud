"""``POST /v1alpha1/jobs/from-repo``: paste a repo, get a job — or get told
everything that is wrong with it, at once, before anything is queued.

The two assertions that matter most are negative ones. A repo with an error
finding must leave **no** trace: nothing on the recording transport (no
artifact staged, no job submitted) and no row in the real ``jobs`` table.
Both are checked, because either one alone would pass while the other leaked
— a job submitted but unrecorded is an orphan nobody can cancel, and a row
written for a job that was never submitted is a permanent phantom in the
owner's list.

The coordinator is the in-memory fake from ``test_jobs.py``, extended to
accept artifact PUTs. The database is the real, freshly migrated ephemeral
Postgres from ``conftest.py``. GitHub is never contacted: every repo here is
a tarball built in-process and handed to the app through the injected
``fetch_repo``.

No skips in this file. A test that asserts a job was *not* submitted is
worthless if it silently doesn't run.
"""
from __future__ import annotations

import io
import tarfile
import textwrap
import time
import uuid

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import app as app_module
from flashml_cloud_api.app import create_cloud_app
from flashml_cloud_api.settings import Settings

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"
OPERATOR_TOKEN = "op-secret-do-not-leak-3f9c1b"
COORDINATOR_URL = "http://coordinator.internal:8100"

TOP = "acme-trainer-abc1234"

CLEAN_TRAIN_PY = """
    import json
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--lr", type=float, default=0.1)
    args = parser.parse_args()

    with open("/work/out/metrics.json", "w") as fh:
        json.dump({"accuracy": 0.9, "lr": args.lr}, fh)
"""

CLEAN_YAML = """
    version: 1
    name: acme-trainer
    image: python-slim
    entrypoint: train.py
    args: ["--epochs", "20"]
"""


# ---------------------------------------------------------------------------
# fixture repos
# ---------------------------------------------------------------------------


def make_tarball(files: dict[str, str], top: str = TOP) -> bytes:
    """A GitHub-shaped tarball: everything under one top-level directory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=top + "/")
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
        for name, content in files.items():
            payload = textwrap.dedent(content).encode()
            member = tarfile.TarInfo(name=f"{top}/{name}")
            member.size = len(payload)
            member.type = tarfile.REGTYPE
            tar.addfile(member, io.BytesIO(payload))
    return buf.getvalue()


CLEAN_REPO = {"flashml.yaml": CLEAN_YAML, "train.py": CLEAN_TRAIN_PY}


class RecordingFetch:
    """Stands in for codeload.github.com. Records what was asked for so a
    test can assert the ref actually travelled, and never touches the
    network."""

    def __init__(self, tar_bytes: bytes):
        self.tar_bytes = tar_bytes
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, owner: str, name: str, ref: str) -> bytes:
        self.calls.append((owner, name, ref))
        return self.tar_bytes


# ---------------------------------------------------------------------------
# the fake coordinator
# ---------------------------------------------------------------------------


class FakeCoordinatorTransport(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.artifacts: dict[str, bytes] = {}
        self.submitted: list[dict] = []
        self._prefix = uuid.uuid4().hex[:10]
        self._next_id = 1
        self.submit_status = 201
        # How many artifact PUTs answer 502 before one is allowed through.
        # A Render free-plan coordinator does exactly this while it cold
        # starts; `None` means every attempt fails.
        self.artifact_gateway_failures: int | None = 0
        self.artifact_attempts = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        method, path = request.method, request.url.path

        if method == "PUT" and path.startswith("/v1alpha1/artifacts/"):
            self.artifact_attempts += 1
            failures = self.artifact_gateway_failures
            if failures is None or self.artifact_attempts <= failures:
                return httpx.Response(502, text="<html>Bad Gateway</html>")
            key = path[len("/v1alpha1/artifacts/"):]
            self.artifacts[key] = request.content
            return httpx.Response(200, json={"uri": f"artifact://{key}"})

        if method == "POST" and path == "/v1alpha1/jobs":
            if self.submit_status >= 300:
                return httpx.Response(self.submit_status, json={"detail": "refused"})
            import json as _json

            body = _json.loads(request.content or b"{}")
            self.submitted.append(body)
            job_id = f"job-{self._prefix}-{self._next_id:04d}"
            self._next_id += 1
            return httpx.Response(
                201, json={"job_id": job_id, "spec": body, "state": "RUNNING"}
            )

        return httpx.Response(404, json={"detail": f"unhandled: {method} {path}"})

    @property
    def job_submissions(self) -> list[httpx.Request]:
        return [
            r for r in self.requests
            if r.method == "POST" and r.url.path == "/v1alpha1/jobs"
        ]


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def settings(postgres_dsn) -> Settings:
    return Settings(
        supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
        supabase_jwt_secret=JWT_SECRET,
        supabase_service_key="service-key-not-used-here",
        coordinator_url=COORDINATOR_URL,
        coordinator_operator_token=OPERATOR_TOKEN,
        require_auth=True,
        database_url=postgres_dsn,
        console_url="https://console.example",
    )


@pytest.fixture
def transport() -> FakeCoordinatorTransport:
    return FakeCoordinatorTransport()


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def make_client(settings, postgres_dsn, transport):
    clients = []

    def build(files: dict[str, str] | None = None, tar_bytes: bytes | None = None):
        fetch = RecordingFetch(
            tar_bytes if tar_bytes is not None else make_tarball(files or CLEAN_REPO)
        )

        def connect() -> psycopg.Connection:
            conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
            conn.autocommit = True
            return conn

        app = create_cloud_app(
            settings, connect=connect, transport=transport, fetch_repo=fetch
        )
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        client.fetch = fetch  # type: ignore[attr-defined]
        return client

    yield build
    for client in clients:
        client.__exit__(None, None, None)


def _new_user(db, *, admitted: bool = True) -> str:
    """A real ``auth.users`` + ``public.profiles`` pair.

    Admitted by default: this file (and everything that imports this
    helper — ``test_profile.py``, ``test_contributions.py``,
    ``test_federated.py``, ``test_verification.py``, ``test_db_pools.py``)
    submits jobs and otherwise exercises routes gated by the invite-only
    admission dependency (Task 10), and pre-alpha-gate accounts are exactly
    what most fixtures here are meant to model. ``admitted=False`` is the
    one deliberate exception, for the handful of tests whose point IS the
    gate (see ``test_pools_api.py`` and
    ``test_db_pools.test_consume_pool_invite_no_longer_admits_the_profile``
    — renamed and inverted when 0009 took admission away from invites).
    """
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        if admitted:
            cur.execute(
                "insert into public.profiles (id, admitted_at) values (%s, now())",
                (user_id,),
            )
        else:
            cur.execute("insert into public.profiles (id) values (%s)", (user_id,))
    return user_id


def _jwt(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": time.time() + 3600},
        JWT_SECRET,
        algorithm="HS256",
    )


def _post(client, token: str, **body):
    payload = {"repo": "https://github.com/acme/trainer", "ref": "main"}
    payload.update(body)
    return client.post(
        "/v1alpha1/jobs/from-repo",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


def _job_rows(db, owner_id: str) -> list[dict]:
    with db.cursor() as cur:
        cur.execute("select * from public.jobs where owner_id = %s", (owner_id,))
        return list(cur.fetchall())


# ---------------------------------------------------------------------------
# 1. auth
# ---------------------------------------------------------------------------


def test_without_a_jwt_nothing_is_fetched_or_submitted(make_client, transport):
    client = make_client()
    r = client.post("/v1alpha1/jobs/from-repo", json={"repo": "acme/trainer"})
    assert r.status_code == 401
    assert transport.requests == []
    assert client.fetch.calls == []


def test_a_machine_token_cannot_submit_from_a_repo(make_client, db, transport):
    from flashml_cloud_api import enrolment

    client = make_client()
    owner = _new_user(db)
    started = enrolment.start_device_code(db, f"n-{uuid.uuid4().hex[:8]}", "h", "linux")
    enrolment.approve_device_code(db, started["user_code"], owner)
    token = enrolment.redeem_device_code(db, started["device_code"])
    r = _post(client, token)
    assert r.status_code == 401
    assert transport.requests == []


# ---------------------------------------------------------------------------
# 2. the happy path
# ---------------------------------------------------------------------------


def test_a_clean_repo_is_submitted_and_recorded(make_client, db, transport):
    client = make_client()
    alice = _new_user(db)
    r = _post(client, _jwt(alice))
    assert r.status_code == 201, r.text

    job_id = r.json()["job_id"]
    rows = _job_rows(db, alice)
    assert [row["id"] for row in rows] == [job_id]
    assert len(transport.job_submissions) == 1


def test_the_submitted_argv_is_exactly_right(make_client, db, transport):
    client = make_client()
    _post(client, _jwt(_new_user(db)))
    params = transport.submitted[0]["spec"]["workload"]["parameters"]
    assert params["command"] == [
        "python",
        "/work/inputs/code/train.py",
        "--epochs",
        "20",
    ]
    assert "task_params" not in params


def test_a_sweep_repo_submits_one_task_per_combination(make_client, db, transport):
    files = dict(CLEAN_REPO)
    # Kept at CLEAN_YAML's indentation: make_tarball dedents, and a block
    # appended flush-left would make the common prefix empty and leave the
    # rest of the document indented.
    files["flashml.yaml"] = CLEAN_YAML + "    sweep:\n      lr: [0.001, 0.01, 0.1]\n"
    client = make_client(files)
    r = _post(client, _jwt(_new_user(db)))
    assert r.status_code == 201, r.text

    params = transport.submitted[0]["spec"]["workload"]["parameters"]
    assert params["command"] == [
        "python",
        "/work/inputs/code/train.py",
        "--epochs",
        "20",
        "--lr",
        "{lr}",
    ]
    assert params["task_params"] == [
        {"lr": "0.001"}, {"lr": "0.01"}, {"lr": "0.1"}
    ]


def test_the_submitted_spec_is_sandboxed(make_client, db, transport):
    client = make_client()
    _post(client, _jwt(_new_user(db)))
    isolation = transport.submitted[0]["spec"]["isolation"]
    assert isolation == {"tier": "sandboxed", "allowFallback": False}


def test_the_repo_tarball_is_staged_as_the_code_input(make_client, db, transport):
    client = make_client()
    _post(client, _jwt(_new_user(db)))
    inputs = transport.submitted[0]["spec"]["workload"]["parameters"]["inputs"]
    key = inputs["code"].removeprefix("artifact://")
    assert inputs["code"].startswith("artifact://")
    assert transport.artifacts[key] == client.fetch.tar_bytes


def test_the_ref_the_caller_asked_for_is_the_one_fetched(make_client, db):
    client = make_client()
    _post(client, _jwt(_new_user(db)), ref="v1.2.3")
    assert client.fetch.calls == [("acme", "trainer", "v1.2.3")]


def test_a_bare_owner_slash_name_is_accepted(make_client, db):
    client = make_client()
    r = _post(client, _jwt(_new_user(db)), repo="acme/trainer")
    assert r.status_code == 201
    assert client.fetch.calls[0][:2] == ("acme", "trainer")


def test_the_source_row_records_where_the_code_came_from(make_client, db):
    client = make_client()
    alice = _new_user(db)
    _post(client, _jwt(alice), ref="dev")
    source = _job_rows(db, alice)[0]["source"]
    assert source["owner"] == "acme"
    assert source["repo"] == "trainer"
    assert source["ref"] == "dev"


# ---------------------------------------------------------------------------
# 3. ownership comes from the JWT, never the body
# ---------------------------------------------------------------------------


def test_owner_id_is_the_jwt_sub_even_when_the_body_claims_another(make_client, db):
    client = make_client()
    alice = _new_user(db)
    mallory = _new_user(db)
    r = _post(client, _jwt(alice), owner_id=mallory, owner=mallory, user_id=mallory)
    assert r.status_code == 201

    job_id = r.json()["job_id"]
    with db.cursor() as cur:
        cur.execute("select owner_id from public.jobs where id = %s", (job_id,))
        row = cur.fetchone()
    assert str(row["owner_id"]) == alice
    assert _job_rows(db, mallory) == []


def test_another_user_cannot_see_the_job_created_from_a_repo(make_client, db):
    client = make_client()
    alice = _new_user(db)
    bob = _new_user(db)
    job_id = _post(client, _jwt(alice)).json()["job_id"]

    r = client.get(
        f"/v1alpha1/jobs/{job_id}", headers={"Authorization": f"Bearer {_jwt(bob)}"}
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 4. preflight errors block — nothing submitted, nothing written
# ---------------------------------------------------------------------------


BAD_TRAIN_PY = """
    import torch
    import requests

    def go():
        data = requests.get("http://example.com/data").content
        open("/tmp/model.pt", "wb").write(data)
"""


def test_an_error_finding_blocks_the_submission_entirely(make_client, db, transport):
    files = dict(CLEAN_REPO)
    files["train.py"] = BAD_TRAIN_PY
    client = make_client(files)
    alice = _new_user(db)

    r = _post(client, _jwt(alice))
    assert r.status_code == 400, r.text
    # Nothing reached the coordinator: no artifact staged, no job submitted.
    assert transport.requests == []
    # And nothing was written to the database.
    assert _job_rows(db, alice) == []


def test_all_findings_come_back_at_once_not_one_per_round_trip(make_client, db):
    files = dict(CLEAN_REPO)
    files["train.py"] = BAD_TRAIN_PY
    client = make_client(files)
    body = _post(client, _jwt(_new_user(db))).json()

    codes = {f["code"] for f in body["findings"]}
    assert {"unknown-import", "network-use"} <= codes
    assert "writes-outside-out" in codes  # warnings are reported too
    assert len(body["findings"]) >= 3


def test_a_missing_entrypoint_blocks_the_submission(make_client, db, transport):
    files = {"flashml.yaml": CLEAN_YAML}
    client = make_client(files)
    alice = _new_user(db)
    r = _post(client, _jwt(alice))
    assert r.status_code == 400
    assert {f["code"] for f in r.json()["findings"]} == {"entrypoint-missing"}
    assert transport.requests == []
    assert _job_rows(db, alice) == []


def test_an_unparseable_entrypoint_blocks_the_submission(make_client, db, transport):
    files = dict(CLEAN_REPO)
    files["train.py"] = "def broken(:\n"
    client = make_client(files)
    alice = _new_user(db)
    assert _post(client, _jwt(alice)).status_code == 400
    assert transport.requests == []
    assert _job_rows(db, alice) == []


# ---------------------------------------------------------------------------
# 5. warnings alone do not block
# ---------------------------------------------------------------------------


def test_warnings_alone_do_not_block_and_are_returned(make_client, db, transport):
    files = dict(CLEAN_REPO)
    # No metrics.json mention, and a write outside /work/out: two warnings,
    # zero errors.
    files["train.py"] = 'open("/tmp/out.txt", "w").close()\n'
    client = make_client(files)
    alice = _new_user(db)

    r = _post(client, _jwt(alice))
    assert r.status_code == 201, r.text
    findings = r.json()["findings"]
    assert findings
    assert {f["level"] for f in findings} == {"warning"}
    assert {f["code"] for f in findings} == {"no-metrics-json", "writes-outside-out"}
    assert len(_job_rows(db, alice)) == 1


def test_a_guarded_import_does_not_block(make_client, db):
    files = dict(CLEAN_REPO)
    files["train.py"] = textwrap.dedent(
        """
        try:
            import torch
        except ImportError:
            torch = None
        open("/work/out/metrics.json", "w").close()
        """
    )
    client = make_client(files)
    r = _post(client, _jwt(_new_user(db)))
    assert r.status_code == 201, r.text
    assert {f["level"] for f in r.json()["findings"]} == {"warning"}


# ---------------------------------------------------------------------------
# 6. config and repo errors
# ---------------------------------------------------------------------------


def test_a_repo_without_a_flashml_yaml_is_a_clean_400(make_client, db, transport):
    client = make_client({"train.py": CLEAN_TRAIN_PY})
    r = _post(client, _jwt(_new_user(db)))
    assert r.status_code == 400
    assert "flashml.yaml" in r.json()["detail"]
    assert transport.requests == []


def test_an_invalid_config_is_a_clean_400(make_client, db, transport):
    files = dict(CLEAN_REPO)
    # A version this parser does not read. `2` was the example here until
    # `mode: federated` took it (see flashml_yaml.SUPPORTED_VERSIONS).
    files["flashml.yaml"] = "version: 9\nname: x\nimage: python-slim\nentrypoint: t.py\n"
    client = make_client(files)
    r = _post(client, _jwt(_new_user(db)))
    assert r.status_code == 400
    assert "version" in r.json()["detail"]
    assert transport.requests == []


def test_an_unknown_image_alias_lists_the_real_ones(make_client, db):
    files = dict(CLEAN_REPO)
    files["flashml.yaml"] = CLEAN_YAML.replace("python-slim", "tensorflow-gpu")
    client = make_client(files)
    r = _post(client, _jwt(_new_user(db)))
    assert r.status_code == 400
    assert "pytorch-cpu" in r.json()["detail"]


@pytest.mark.parametrize(
    "repo",
    [
        "",
        "not-a-repo",
        "acme/trainer/extra",
        "https://evil.example.com/acme/trainer",
        "acme/../../etc",
        "acme/trainer\r\nX-Evil: 1",
    ],
)
def test_a_malformed_repo_reference_is_refused_before_any_fetch(
    make_client, db, transport, repo
):
    client = make_client()
    r = _post(client, _jwt(_new_user(db)), repo=repo)
    assert r.status_code == 400, r.text
    assert client.fetch.calls == []
    assert transport.requests == []


@pytest.mark.parametrize("ref", ["../../etc", "main\r\nEvil: 1", "-oProxyCommand=x", ""])
def test_a_malformed_ref_is_refused_before_any_fetch(make_client, db, ref):
    client = make_client()
    r = _post(client, _jwt(_new_user(db)), ref=ref)
    assert r.status_code == 400
    assert client.fetch.calls == []


def test_a_malicious_tarball_is_refused_without_a_stack_trace(
    make_client, db, transport
):
    """A zip-slip member: refused by extract_safely, surfaced as a clean
    400 whose message cannot carry a line break out of the member name."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        payload = b"pwned"
        member = tarfile.TarInfo(name="../evil\r\nX-Evil: 1.txt")
        member.size = len(payload)
        member.type = tarfile.REGTYPE
        tar.addfile(member, io.BytesIO(payload))

    client = make_client(tar_bytes=buf.getvalue())
    alice = _new_user(db)
    r = _post(client, _jwt(alice))
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "Traceback" not in detail
    assert "\n" not in detail and "\r" not in detail
    assert transport.requests == []
    assert _job_rows(db, alice) == []


def test_a_decompression_bomb_is_refused(make_client, db, transport):
    class _Zeros:
        def __init__(self, total: int):
            self._left = total

        def read(self, n: int = -1) -> bytes:
            if n < 0 or n > self._left:
                n = self._left
            self._left -= n
            return b"\x00" * n

    buf = io.BytesIO()
    size = 300 * 1024 * 1024
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        member = tarfile.TarInfo(name=f"{TOP}/bomb.bin")
        member.size = size
        member.type = tarfile.REGTYPE
        tar.addfile(member, _Zeros(size))

    client = make_client(tar_bytes=buf.getvalue())
    alice = _new_user(db)
    assert _post(client, _jwt(alice)).status_code == 400
    assert transport.requests == []
    assert _job_rows(db, alice) == []


# ---------------------------------------------------------------------------
# 7. coordinator failures never leave a phantom row
# ---------------------------------------------------------------------------


def test_a_refused_submission_writes_no_jobs_row(make_client, db, transport):
    transport.submit_status = 422
    client = make_client()
    alice = _new_user(db)
    r = _post(client, _jwt(alice))
    assert r.status_code == 422
    assert _job_rows(db, alice) == []


# ---------------------------------------------------------------------------
# 8. pool-scoped submission — the waiver exists iff the pool does
# ---------------------------------------------------------------------------


def test_a_pool_member_submits_with_pool_and_the_row_carries_it(
    make_client, db, transport
):
    from flashml_cloud_api import db as dbmod

    client = make_client()
    alice = _new_user(db)
    pool = dbmod.create_pool(db, name="Ada's Team", owner_id=alice)
    pool_id = str(pool["id"])

    r = _post(client, _jwt(alice), pool=pool_id)
    assert r.status_code == 201, r.text

    row = _job_rows(db, alice)[0]
    assert str(row["pool_id"]) == pool_id
    assert row["source"]["pool"] == pool_id

    spec = transport.submitted[0]["spec"]
    assert spec["isolation"] == {"tier": "sandboxed", "allowFallback": True}
    assert spec["placement"]["pool"] == pool_id


def test_a_pool_id_submitted_uppercase_is_normalized_to_canonical(
    make_client, db, transport
):
    """Postgres accepts an uppercase uuid, so the membership check
    (`fetch_pool_for_member`) passes it just fine — but the scheduler's gate
    compares exact strings against the canonical-lowercase ids
    `pool_ids_for_machine` returns. Submitting the caller's spelling
    verbatim would pass this check and then never match that gate, leaving
    the job PENDING forever. The route must rebind `pool` to the database's
    canonical id before it reaches compile/insert_job/source."""
    from flashml_cloud_api import db as dbmod

    client = make_client()
    alice = _new_user(db)
    pool = dbmod.create_pool(db, name="Ada's Team", owner_id=alice)
    pool_id = str(pool["id"])
    assert pool_id == pool_id.lower(), "sanity: a fresh uuid is already lowercase"

    r = _post(client, _jwt(alice), pool=pool_id.upper())
    assert r.status_code == 201, r.text

    row = _job_rows(db, alice)[0]
    assert str(row["pool_id"]) == pool_id
    assert row["source"]["pool"] == pool_id

    spec = transport.submitted[0]["spec"]
    assert spec["placement"]["pool"] == pool_id


def test_a_non_member_submitting_to_a_pool_is_404(make_client, db, transport):
    from flashml_cloud_api import db as dbmod

    client = make_client()
    owner = _new_user(db)
    outsider = _new_user(db)
    pool_id = str(dbmod.create_pool(db, name="Ada's Team", owner_id=owner)["id"])

    r = _post(client, _jwt(outsider), pool=pool_id)
    assert r.status_code == 404
    assert transport.requests == []
    assert _job_rows(db, outsider) == []


def test_an_unknown_pool_id_is_404(make_client, db, transport):
    client = make_client()
    alice = _new_user(db)
    r = _post(client, _jwt(alice), pool=str(uuid.uuid4()))
    assert r.status_code == 404
    assert transport.requests == []
    assert _job_rows(db, alice) == []


def test_a_pool_that_is_not_even_a_uuid_is_404_not_500(make_client, db, transport):
    """Same answer as 'not found' — three sibling routes already guard this
    exact case (revoke, get_pool_route, create_pool_invite_route) so a
    malformed pool id must not become a distinguishable third answer that
    breaks the 404 doctrine every member-scoped lookup here relies on."""
    client = make_client()
    alice = _new_user(db)
    r = _post(client, _jwt(alice), pool="not-a-uuid")
    assert r.status_code == 404
    assert transport.requests == []
    assert _job_rows(db, alice) == []


def test_an_unadmitted_user_submitting_with_a_pool_is_403_not_404(make_client, db):
    """Admission is checked before the pool lookup — same ordering as every
    other route here: an un-admitted account never learns whether the pool
    id it guessed is even real."""
    from flashml_cloud_api import db as dbmod

    client = make_client()
    owner = _new_user(db)
    pool_id = str(dbmod.create_pool(db, name="Ada's Team", owner_id=owner)["id"])
    stranger = _new_user(db, admitted=False)

    r = _post(client, _jwt(stranger), pool=pool_id)
    assert r.status_code == 403


def test_no_pool_field_is_unchanged(make_client, db, transport):
    """The regression guard: a plain from-repo submission with no `pool`
    key carries no pool_id and no placement waiver, exactly as before."""
    client = make_client()
    alice = _new_user(db)
    r = _post(client, _jwt(alice))
    assert r.status_code == 201, r.text

    row = _job_rows(db, alice)[0]
    assert row["pool_id"] is None
    assert "pool" not in row["source"]

    spec = transport.submitted[0]["spec"]
    assert spec["isolation"] == {"tier": "sandboxed", "allowFallback": False}


def test_raw_job_submission_refuses_an_allow_fallback_spec(make_client, db, transport):
    client = make_client()
    alice = _new_user(db)
    r = client.post(
        "/v1alpha1/jobs",
        json={"spec": {"isolation": {"allowFallback": True}}},
        headers={"Authorization": f"Bearer {_jwt(alice)}"},
    )
    assert r.status_code == 400
    assert "from-repo" in r.json()["detail"]
    assert transport.requests == []


def test_raw_job_submission_with_a_non_dict_isolation_or_placement_does_not_500(
    make_client, db, transport
):
    """A malformed (non-dict) `isolation`/`placement` value must not crash
    the refusal guard with an AttributeError from `.get` on a string — it is
    treated as absent, the same `isinstance(..., dict) else {}` guard the
    line above already applies to `spec` itself. Neither condition can then
    be true, so the request is not refused; the point of this test is the
    absence of a 500, not any particular status code."""
    client = make_client()
    alice = _new_user(db)
    r = client.post(
        "/v1alpha1/jobs",
        json={
            "metadata": {"name": "demo", "labels": {}},
            "spec": {"isolation": "x", "placement": "y"},
        },
        headers={"Authorization": f"Bearer {_jwt(alice)}"},
    )
    assert r.status_code == 201, r.text


def test_raw_job_submission_refuses_a_pool_placement(make_client, db, transport):
    client = make_client()
    alice = _new_user(db)
    r = client.post(
        "/v1alpha1/jobs",
        json={"spec": {"placement": {"pool": "some-pool"}}},
        headers={"Authorization": f"Bearer {_jwt(alice)}"},
    )
    assert r.status_code == 400
    assert "from-repo" in r.json()["detail"]
    assert transport.requests == []


def test_raw_job_submission_with_placement_pool_any_is_still_allowed(
    make_client, db, transport
):
    """`placement.pool: "any"` is the same as omitting it — the explicit
    default, not a waiver request — so the raw route must not refuse it."""
    client = make_client()
    alice = _new_user(db)
    r = client.post(
        "/v1alpha1/jobs",
        json={
            "metadata": {"name": "demo", "labels": {}},
            "spec": {"placement": {"pool": "any"}},
        },
        headers={"Authorization": f"Bearer {_jwt(alice)}"},
    )
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# a coordinator that is still waking up
#
# Regression: ISSUE-006 — the dev coordinator runs on a Render free plan and
# spins down when idle, so the FIRST submission of any session got a 502 on
# the artifact PUT and the user was told "could not stage the repo". A cold
# start measured 21.3s; the retry below outlasts it. The PUT is safe to
# repeat: `code_key` is a freshly minted uuid, so a retry cannot overwrite
# anyone's staged code, including an earlier attempt of this same request.
# Found by hands-on QA on 2026-08-04.
# Report: .gstack/qa-reports/qa-report-flashml-console-2026-08-04.md
# ---------------------------------------------------------------------------


def test_a_cold_coordinator_is_retried_and_the_job_still_lands(
    make_client, db, transport, monkeypatch
):
    monkeypatch.setattr(app_module, "GATEWAY_RETRY_DELAYS", (0.0, 0.0, 0.0))
    transport.artifact_gateway_failures = 2

    r = _post(make_client(), _jwt(_new_user(db)))

    assert r.status_code == 201, r.text
    assert transport.artifact_attempts == 3
    assert len(transport.job_submissions) == 1


def test_a_coordinator_that_never_wakes_says_so(
    make_client, db, transport, monkeypatch
):
    monkeypatch.setattr(app_module, "GATEWAY_RETRY_DELAYS", (0.0, 0.0, 0.0))
    transport.artifact_gateway_failures = None

    r = _post(make_client(), _jwt(_new_user(db)))

    assert r.status_code == 502
    # Every allowed attempt was actually made.
    assert transport.artifact_attempts == 4
    # The old copy was "could not stage the repo", which reads as "your repo
    # is broken" for what is really "come back in thirty seconds".
    detail = r.json()["detail"].lower()
    assert "starting up" in detail or "try again" in detail
    assert "repo" not in detail.replace("repository", "")


def test_a_non_gateway_upload_failure_is_not_retried(
    make_client, db, transport, monkeypatch
):
    """A 4xx from the artifact store is our bug, not a cold start. Retrying
    it just multiplies a request that will never succeed."""
    monkeypatch.setattr(app_module, "GATEWAY_RETRY_DELAYS", (0.0, 0.0, 0.0))

    async def handle(request: httpx.Request) -> httpx.Response:
        await request.aread()
        transport.requests.append(request)
        if request.method == "PUT" and "/artifacts/" in request.url.path:
            transport.artifact_attempts += 1
            return httpx.Response(413, text="too large")
        return httpx.Response(404)

    monkeypatch.setattr(transport, "handle_async_request", handle)

    r = _post(make_client(), _jwt(_new_user(db)))

    assert r.status_code == 502
    assert transport.artifact_attempts == 1
