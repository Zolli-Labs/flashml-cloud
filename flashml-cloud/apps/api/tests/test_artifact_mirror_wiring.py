"""The mirror, wired into the deployed product.

``test_artifact_mirror.py`` proves the module: what counts as accepted, that
the manifest is written last, that a second call copies nothing. None of that
was reachable from a request — the module had zero callers — so this file
covers the other half, which is the half that can be wrong while every test
next door stays green:

- the terminal-job path runs the mirror **once**, guarded by
  ``jobs.artifacts_mirrored_at`` (migration 0016), not once per two-second
  poll for as long as a tab is open;
- a mirror that FAILS logs and leaves that marker null, so the next
  observation retries — the failure mode 0016 exists to prevent is a job
  permanently believing its bytes are in OSS when they are not;
- deleting a job's artifacts deletes the OSS copy too. Without that, the
  release valve frees the coordinator's disk and leaves a presignable copy
  behind after the owner deleted it;
- the integration panel reports the mirror from ``Settings`` and never from
  the two environment variables nothing in this system consumes.

OSS is faked at ``OSSArtifacts.from_settings`` rather than at ``mirror_job``,
deliberately: the real selection, transfer-verification and manifest logic
runs against these routes, so a wiring test cannot pass by calling a stub
that agrees with it. The coordinator is the same kind of in-memory fake
``test_jobs.py`` uses, extended with the task-state view the mirror needs —
that view is what decides which tasks were ACCEPTED (repo hard rule 4), so a
fake without it could not tell an accepted commit from an abandoned one.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import artifact_mirror as mirrormod
from flashml_cloud_api import db as dbmod
from flashml_cloud_api.alibaba_oss import OSSUnavailable, StoredObject
from flashml_cloud_api.app import create_app, create_cloud_app
from flashml_cloud_api.settings import Settings

JWT_SECRET = "test-jwt-secret-long-enough-for-hs256-abcdef"
OPERATOR_TOKEN = "op-secret-do-not-leak-3f9c1b"
COORDINATOR_URL = "http://coordinator.internal:8100"

OSS_BUCKET = "flashml-artifacts-test"


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class FakeOSS:
    """An in-memory bucket with the five methods the mirror uses.

    ``put_bytes`` returns the real MD5 as its ETag, exactly as OSS does for a
    single-part put, so ``mirror_job``'s transfer check runs for real instead
    of being stubbed to agree.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.deleted_prefixes: list[str] = []
        #: Keys whose put fails. The mirror's contract is that ANY failure
        #: leaves no manifest, so failing one object is enough to exercise it.
        self.fail_put_on: set[str] = set()
        #: When true, deleting a prefix raises — the case the delete route
        #: must not report as success.
        self.deletes_broken = False

    def put_bytes(self, key: str, data: bytes) -> StoredObject:
        if key in self.fail_put_on:
            raise OSSUnavailable(f"refusing {key}")
        self.puts.append(key)
        self.objects[key] = data
        return StoredObject(
            key=key, size_bytes=len(data), sha256=None,
            etag=hashlib.md5(data).hexdigest(),  # noqa: S324 - transport check
            last_modified=None,
        )

    def get_bytes(self, key: str) -> bytes:
        if key not in self.objects:
            raise OSSUnavailable(f"cannot read {key}")
        return self.objects[key]

    def head(self, key: str):
        if key not in self.objects:
            return None
        return StoredObject(key=key, size_bytes=len(self.objects[key]),
                            sha256=None, etag=None, last_modified=None)

    def sign_get(self, key: str, *, ttl_s: int = 900) -> str:
        return f"https://example.invalid/{key}?Expires={ttl_s}"

    def delete_prefix(self, prefix: str) -> int:
        if self.deletes_broken:
            raise OSSUnavailable("bucket refused the delete")
        self.deleted_prefixes.append(prefix)
        keys = [k for k in self.objects if k.startswith(prefix)]
        for key in keys:
            del self.objects[key]
        return len(keys)

    # -- assertions the tests read ------------------------------------------

    def mirrored_keys(self, job_id: str) -> list[str]:
        """Everything under this job's prefix EXCEPT the manifest."""
        prefix = f"jobs/{job_id}/"
        return sorted(
            k for k in self.objects
            if k.startswith(prefix) and not k.startswith(f"{prefix}_mirror/")
        )

    def manifest(self, job_id: str) -> dict | None:
        raw = self.objects.get(mirrormod.manifest_key(job_id))
        return None if raw is None else json.loads(raw)


class FakeCoordinatorTransport(httpx.AsyncBaseTransport):
    """The coordinator as a dictionary, including its TASK view.

    The task view is not decoration: ``jobs/{job}/{task}/…`` is mirrored only
    when the coordinator reports that task COMPLETED, and everything else
    under the prefix is control-plane output that no lease could have
    written. A fake that answered only the artifact listing could not tell
    those apart, which is the exact distinction hard rule 4 turns on.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._jobs: dict[str, dict] = {}
        self._prefix = uuid.uuid4().hex[:10]
        self._next_id = 1
        self.artifacts: dict[str, bytes] = {}
        #: ``{job_id: {task_id: state}}``.
        self.tasks: dict[str, dict[str, str]] = {}

    # -- seeding -------------------------------------------------------------

    def seed_artifact(self, key: str, content: bytes) -> None:
        self.artifacts[key] = content

    def seed_task(self, job_id: str, task_id: str, state: str) -> None:
        self.tasks.setdefault(job_id, {})[task_id] = state

    def finish(self, job_id: str, state: str = "SUCCEEDED") -> None:
        self._jobs[job_id] = dict(self._jobs[job_id], state=state)

    # -- observation ---------------------------------------------------------

    def artifact_reads(self) -> list[str]:
        return [r.url.path[len("/v1alpha1/artifacts/"):]
                for r in self.requests
                if r.method == "GET"
                and r.url.path.startswith("/v1alpha1/artifacts/")]

    def task_listings(self) -> list[str]:
        return [r.url.path.split("/")[-2] for r in self.requests
                if r.method == "GET" and r.url.path.endswith("/tasks")]

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        await request.aread()
        self.requests.append(request)
        method, path = request.method, request.url.path

        if method == "POST" and path == "/v1alpha1/jobs":
            body = json.loads(request.content or b"{}")
            job_id = f"job-{self._prefix}-{self._next_id:04d}"
            self._next_id += 1
            record = {"job_id": job_id, "spec": body, "state": "RUNNING",
                      "backend": "leases"}
            self._jobs[job_id] = record
            return httpx.Response(201, json=record)

        if method == "GET" and path == "/v1alpha1/jobs":
            return httpx.Response(200, json=list(self._jobs.values()))

        if method == "GET" and path.endswith("/tasks") and path.count("/") == 4:
            job_id = path.split("/")[-2]
            return httpx.Response(200, json=[
                {"task_id": t, "state": s}
                for t, s in sorted(self.tasks.get(job_id, {}).items())
            ])

        if (method == "GET" and path.startswith("/v1alpha1/jobs/")
                and path.count("/") == 3):
            record = self._jobs.get(path.rsplit("/", 1)[-1])
            if record is None:
                return httpx.Response(404, json={"detail": "no such job"})
            return httpx.Response(200, json=record)

        if method == "GET" and path.endswith("/artifacts") and path.count("/") == 4:
            job_id = path.split("/")[-2]
            prefix = f"jobs/{job_id}/"
            return httpx.Response(200, json=[
                {"uri": f"artifact://{k}", "key": k, "size_bytes": len(v)}
                for k, v in sorted(self.artifacts.items())
                if k.startswith(prefix)
            ])

        if method == "DELETE" and path.endswith("/artifacts") and path.count("/") == 4:
            job_id = path.split("/")[-2]
            prefix = f"jobs/{job_id}/"
            keys = [k for k in self.artifacts if k.startswith(prefix)]
            if not keys:
                return httpx.Response(404, json={"detail": "no artifacts"})
            freed = sum(len(self.artifacts.pop(k)) for k in keys)
            return httpx.Response(
                200, json={"deleted_files": len(keys), "freed_bytes": freed}
            )

        if method == "GET" and path.startswith("/v1alpha1/artifacts/"):
            content = self.artifacts.get(path[len("/v1alpha1/artifacts/"):])
            if content is None:
                return httpx.Response(404, json={"detail": "no such artifact"})
            return httpx.Response(200, content=content,
                                  headers={"content-type": "application/octet-stream"})

        return httpx.Response(
            404, json={"detail": f"unhandled fake coordinator route: {method} {path}"}
        )


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _settings(postgres_dsn: str, *, oss: bool) -> Settings:
    return Settings(
        supabase_url="https://yualksqjjvlfscbbsygq.supabase.co",
        supabase_jwt_secret=JWT_SECRET,
        supabase_service_key="service-key-not-used-here",
        coordinator_url=COORDINATOR_URL,
        coordinator_operator_token=OPERATOR_TOKEN,
        require_auth=True,
        database_url=postgres_dsn,
        console_url="https://console.example",
        oss_bucket=OSS_BUCKET if oss else "",
        oss_endpoint="oss-ap-southeast-1.aliyuncs.com" if oss else "",
        oss_access_key_id="AK-test" if oss else "",
        oss_access_key_secret="SK-test" if oss else "",
    )


@pytest.fixture
def transport() -> FakeCoordinatorTransport:
    return FakeCoordinatorTransport()


@pytest.fixture
def oss(monkeypatch) -> FakeOSS:
    """Stand in for the bucket at the one place the mirror builds a client.

    Patched on ``artifact_mirror`` rather than on ``alibaba_oss``: the module
    imports the name, so binding it there is what the mirror actually
    resolves. Everything above the client — selection, ETag verification,
    manifest-last ordering, idempotence — is the real code.
    """
    bucket = FakeOSS()
    monkeypatch.setattr(
        mirrormod, "OSSArtifacts",
        type("OSSArtifactsStub", (), {"from_settings": staticmethod(lambda s: bucket)}),
    )
    return bucket


def _client(settings: Settings, postgres_dsn: str, transport) -> TestClient:
    def connect() -> psycopg.Connection:
        conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
        conn.autocommit = True
        return conn

    return TestClient(create_cloud_app(settings, connect=connect, transport=transport))


@pytest.fixture
def client(postgres_dsn, transport):
    with _client(_settings(postgres_dsn, oss=True), postgres_dsn, transport) as c:
        yield c


@pytest.fixture
def unconfigured_client(postgres_dsn, transport):
    """The same app with no OSS — the deployment default, which must be
    byte-for-byte unaffected by any of this."""
    with _client(_settings(postgres_dsn, oss=False), postgres_dsn, transport) as c:
        yield c


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    try:
        yield conn
    finally:
        conn.close()


def _new_user(db) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id) values (%s)", (user_id,))
        cur.execute(
            "insert into public.profiles (id, admitted_at) values (%s, now())",
            (user_id,),
        )
    return user_id


def _browser_jwt(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "aud": "authenticated", "exp": time.time() + 3600},
        JWT_SECRET, algorithm="HS256",
    )


def _submit(client, token: str, name: str) -> str:
    r = client.post(
        "/v1alpha1/jobs",
        json={"apiVersion": "flashml.dev/v1alpha1", "kind": "Job",
              "metadata": {"name": name}, "spec": {}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["job_id"]


def _mirrored_at(db, job_id: str):
    with db.cursor() as cur:
        cur.execute(
            "select artifacts_mirrored_at from public.jobs where id = %s", (job_id,)
        )
        row = cur.fetchone()
    assert row is not None, "no such job row"
    return row["artifacts_mirrored_at"]


def _finished_job(client, db, transport, token: str, name: str) -> str:
    """A terminal job with one accepted task, one failed one, and the
    control-plane output that belongs to neither."""
    job_id = _submit(client, token, name)
    transport.seed_task(job_id, "shard-000", "COMPLETED")
    transport.seed_task(job_id, "shard-001", "FAILED")
    transport.seed_artifact(f"jobs/{job_id}/shard-000/model.bin", b"\x00\x01weights")
    transport.seed_artifact(f"jobs/{job_id}/shard-000/metrics.json", b'{"loss":0.1}')
    transport.seed_artifact(f"jobs/{job_id}/shard-001/stderr.txt", b"traceback\n")
    transport.seed_artifact(f"jobs/{job_id}/reduced/best.json", b'{"best":"000"}')
    transport.finish(job_id)
    return job_id


def _open(client, token: str, job_id: str):
    """One page load — the poll that observes a job terminal."""
    return client.get(f"/v1alpha1/jobs/{job_id}",
                      headers={"Authorization": f"Bearer {token}"})


# ---------------------------------------------------------------------------
# 1. the guard: the mirror runs exactly once
# ---------------------------------------------------------------------------


def test_a_finished_job_is_mirrored_when_its_page_is_first_opened(
    client, db, transport, oss
):
    """The wiring, end to end. Accepted work only: the failed shard's bytes
    stay on the coordinator's disk, and the reducer's output — which no lease
    could have written — goes with the accepted ones."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "mirror-me")

    assert _open(client, token, job_id).status_code == 200

    assert oss.mirrored_keys(job_id) == [
        f"jobs/{job_id}/reduced/best.json",
        f"jobs/{job_id}/shard-000/metrics.json",
        f"jobs/{job_id}/shard-000/model.bin",
    ]
    assert f"jobs/{job_id}/shard-001/stderr.txt" not in oss.objects
    manifest = oss.manifest(job_id)
    assert manifest is not None and manifest["complete"] is True
    assert manifest["accepted_tasks"] == ["shard-000"]
    assert _mirrored_at(db, job_id) is not None


def test_polling_a_mirrored_job_never_copies_anything_again(
    client, db, transport, oss
):
    """A job page polls every two seconds and keeps polling after the job has
    finished. Without the 0016 marker each of those polls would re-enter the
    mirror — a task listing, an artifact listing and a manifest HEAD, for
    ever — so "runs once" is the property, not "is eventually consistent"."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "polled-hard")

    _open(client, token, job_id)
    puts_after_first = list(oss.puts)
    reads_after_first = transport.artifact_reads()
    stamped = _mirrored_at(db, job_id)

    for _ in range(5):
        assert _open(client, token, job_id).status_code == 200

    assert oss.puts == puts_after_first
    # Not one further byte read from the coordinator, and not even the task
    # listing: the guard is checked before the mirror is entered at all.
    assert transport.artifact_reads() == reads_after_first
    assert transport.task_listings() == [job_id]
    assert _mirrored_at(db, job_id) == stamped


def test_a_job_that_is_still_running_is_not_mirrored(client, db, transport, oss):
    """``COMPLETED`` is a per-TASK fact. A job with one finished shard and
    three still running would mirror the one and then write a manifest saying
    that is all there is — permanently. The terminal check is the caller's,
    and this is it."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _submit(client, token, "still-going")
    transport.seed_task(job_id, "shard-000", "COMPLETED")
    transport.seed_artifact(f"jobs/{job_id}/shard-000/model.bin", b"early")

    assert _open(client, token, job_id).status_code == 200

    assert oss.objects == {}
    assert _mirrored_at(db, job_id) is None


def test_an_unconfigured_deployment_mirrors_nothing_and_stays_retryable(
    unconfigured_client, db, transport, oss
):
    """The deployment default. Nothing is copied, no OSS client is built —
    and the marker stays NULL, which is what lets these jobs be mirrored the
    day OSS is configured instead of being excluded for ever."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(unconfigured_client, db, transport, token, "no-oss-here")

    assert _open(unconfigured_client, token, job_id).status_code == 200

    assert oss.objects == {}
    assert oss.puts == []
    # The gate is before the coordinator, not after it: an unconfigured
    # deployment does not pay a task listing per finished job to learn it has
    # nothing to do.
    assert transport.task_listings() == []
    assert _mirrored_at(db, job_id) is None


def test_another_account_cannot_trigger_a_mirror_it_cannot_see(
    client, db, transport, oss
):
    """The hook hangs off a route whose first act is a visibility check."""
    alice = _new_user(db)
    bob = _new_user(db)
    job_id = _finished_job(client, db, transport, _browser_jwt(alice), "not-bobs")

    assert _open(client, _browser_jwt(bob), job_id).status_code == 404

    assert oss.objects == {}
    assert _mirrored_at(db, job_id) is None


# ---------------------------------------------------------------------------
# 2. a failed mirror is not recorded, so it retries
# ---------------------------------------------------------------------------


def test_a_failed_mirror_leaves_no_manifest_and_no_marker(
    client, db, transport, oss
):
    """The failure this whole marker exists for. The request still succeeds —
    a job page that 500s because a bucket was slow is strictly worse than a
    mirror that lands one poll later — and nothing is written down, so
    nothing believes the copy happened."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "bucket-refuses")
    oss.fail_put_on = {f"jobs/{job_id}/shard-000/model.bin"}

    r = _open(client, token, job_id)

    assert r.status_code == 200, r.text
    assert r.json()["state"] == "SUCCEEDED"
    # Manifest LAST: objects copied before the failure may be in the bucket,
    # but with no manifest the mirror reads as incomplete rather than as a
    # promise of objects that are not there.
    assert oss.manifest(job_id) is None
    assert _mirrored_at(db, job_id) is None


def test_the_next_poll_retries_a_failed_mirror_and_completes_it(
    client, db, transport, oss
):
    """Retryability is the whole reason the marker is separate from
    ``artifact_bytes_recorded_at``: the footprint measurement beside this one
    SUCCEEDS on the failing poll, so a shared marker would record the job as
    done and this retry would never happen."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "second-time-lucky")
    oss.fail_put_on = {f"jobs/{job_id}/shard-000/model.bin"}

    _open(client, token, job_id)
    assert _mirrored_at(db, job_id) is None
    # The footprint hook ran on that same poll and DID succeed, which is
    # exactly the situation a shared marker would misread.
    with db.cursor() as cur:
        cur.execute(
            "select artifact_bytes_recorded_at from public.jobs where id = %s",
            (job_id,),
        )
        assert cur.fetchone()["artifact_bytes_recorded_at"] is not None

    oss.fail_put_on = set()
    assert _open(client, token, job_id).status_code == 200

    assert oss.manifest(job_id) is not None
    assert oss.mirrored_keys(job_id) == [
        f"jobs/{job_id}/reduced/best.json",
        f"jobs/{job_id}/shard-000/metrics.json",
        f"jobs/{job_id}/shard-000/model.bin",
    ]
    assert _mirrored_at(db, job_id) is not None


# ---------------------------------------------------------------------------
# 3. deleting a job's artifacts deletes the mirror
# ---------------------------------------------------------------------------


def test_deleting_a_jobs_artifacts_deletes_the_oss_copy(client, db, transport, oss):
    """The release valve's second half. Without it an owner is told their
    artifacts are gone while a complete, manifest-certified copy sits in a
    bucket — still presignable, still billed, counted by nobody."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "delete-both-copies")
    _open(client, token, job_id)
    assert oss.mirrored_keys(job_id)

    r = client.delete(f"/v1alpha1/jobs/{job_id}/artifacts",
                      headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200, r.text
    assert oss.objects == {}
    assert oss.manifest(job_id) is None
    assert oss.deleted_prefixes == [f"jobs/{job_id}/"]
    assert dbmod.storage_usage_for_owner(db, alice) == 0


def test_a_never_mirrored_job_still_deletes_cleanly(client, db, transport, oss):
    """Deleting a mirror that was never written is 0 objects, not an error —
    the same "already gone is not a failure" rule the coordinator's own
    delete follows. This is also the second click and the retry."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "never-opened")

    first = client.delete(f"/v1alpha1/jobs/{job_id}/artifacts",
                          headers={"Authorization": f"Bearer {token}"})
    second = client.delete(f"/v1alpha1/jobs/{job_id}/artifacts",
                           headers={"Authorization": f"Bearer {token}"})

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json() == {"deleted_files": 0, "freed_bytes": 0}
    # Attempted for every target on every call, including the one whose
    # coordinator side answered 404 — that 404 is precisely the case where
    # the OSS copy is the one still standing.
    assert oss.deleted_prefixes == [f"jobs/{job_id}/", f"jobs/{job_id}/"]


def test_a_bucket_that_refuses_the_delete_is_502_and_credits_nothing(
    client, db, transport, oss
):
    """Reporting success here would tell somebody their data is deleted while
    a readable copy of it remains. Nothing is credited, so the usage
    over-states the disk — wrong in the safe direction — and the call is
    retryable."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "bucket-wont-delete")
    _open(client, token, job_id)
    assert dbmod.storage_usage_for_owner(db, alice) > 0
    charged = dbmod.storage_usage_for_owner(db, alice)
    oss.deletes_broken = True

    r = client.delete(f"/v1alpha1/jobs/{job_id}/artifacts",
                      headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 502, r.text
    assert dbmod.storage_usage_for_owner(db, alice) == charged
    assert oss.manifest(job_id) is not None


def test_deleting_artifacts_needs_no_oss_to_work(
    unconfigured_client, db, transport, oss
):
    """An unconfigured deployment's delete route behaves exactly as it did
    before the mirror existed: there is no mirror, so there is nothing to
    delete, and ``unmirror_job`` says 0 rather than raising."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(unconfigured_client, db, transport, token, "plain-delete")

    r = unconfigured_client.delete(f"/v1alpha1/jobs/{job_id}/artifacts",
                                   headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200, r.text
    assert r.json()["deleted_files"] == 4
    assert oss.deleted_prefixes == []


# ---------------------------------------------------------------------------
# 4. the integration panel tells the truth
#
# Two bugs lived here. `artifact_store` read FLASHML_ARTIFACT_BACKEND, which
# nothing in the managed path consumes — `store_from_env()`'s only caller in
# the runtime is the KubeRay ingest, disabled on both deployed coordinators —
# so the panel would report "oss" for a deployment writing every byte to a
# local disk. `oss_bucket` read FLASHML_OSS_BUCKET, a different name from the
# OSS_BUCKET settings.py actually reads, so a fully configured deployment
# reported an empty bucket and an unconfigured one could be made to look
# configured by exporting one variable.
# ---------------------------------------------------------------------------


DEAD_VARS = ("FLASHML_ARTIFACT_BACKEND", "FLASHML_OSS_BUCKET")

PANEL_ENV = (
    "SUPABASE_URL", "COORDINATOR_URL", "COORDINATOR_OPERATOR_TOKEN",
    "FLASHML_PROFILE", "OSS_BUCKET", "OSS_ENDPOINT",
    "OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET",
) + DEAD_VARS


@pytest.fixture
def panel(tmp_path, monkeypatch):
    """The legacy app's panel, in a clean environment.

    ``FLASHML_REQUIRE_AUTH`` is off because ``Settings.from_env`` refuses to
    build without the Supabase/coordinator secrets while it is on — and this
    app is only ever returned when those are absent. That refusal is itself
    reported (``not observed``); the case below with auth off is the one
    where the panel can actually see the mirror's configuration.
    """
    monkeypatch.setenv("FLASHML_CLOUD_DB", str(tmp_path / "cloud.db"))
    monkeypatch.setenv("FLASHML_REQUIRE_AUTH", "false")
    for var in PANEL_ENV:
        monkeypatch.delenv(var, raising=False)

    def read() -> dict:
        with TestClient(create_app()) as c:
            r = c.get("/v1alpha1/integration")
            assert r.status_code == 200, r.text
            return r.json()

    return read


def test_the_panel_reports_where_the_coordinator_actually_writes(panel, monkeypatch):
    """Even with the dead variable set to something that used to change this
    line. Nothing reads it, so it may not move the answer."""
    monkeypatch.setenv("FLASHML_ARTIFACT_BACKEND", "oss")

    status = panel()

    assert status["artifact_store"] == "coordinator-local-disk"


def test_the_panel_reports_no_mirror_when_none_is_configured(panel):
    status = panel()
    assert status["artifact_mirror"] == "not configured"
    assert status["oss_bucket"] is None


def test_the_dead_env_var_cannot_fabricate_a_configured_mirror(panel, monkeypatch):
    """The dangerous direction: a dashboard telling somebody their artifacts
    are being copied off a disk that a redeploy erases, because one variable
    nothing consumes was exported."""
    monkeypatch.setenv("FLASHML_OSS_BUCKET", "looks-real-but-is-not")

    status = panel()

    assert status["oss_bucket"] is None
    assert status["artifact_mirror"] == "not configured"
    assert "looks-real-but-is-not" not in json.dumps(status)


def test_the_panel_reports_the_bucket_settings_actually_reads(panel, monkeypatch):
    """The other direction, and the one a real deployment hits: OSS_BUCKET is
    set, everything works, and the panel used to report nothing at all."""
    monkeypatch.setenv("OSS_BUCKET", OSS_BUCKET)
    monkeypatch.setenv("OSS_ENDPOINT", "oss-ap-southeast-1.aliyuncs.com")
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "AK-test")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "SK-test")

    status = panel()

    assert status["oss_bucket"] == OSS_BUCKET
    assert status["artifact_mirror"] == "configured"
    # The mirror is an ADDITIONAL copy. Configuring it must not change what
    # the coordinator is reported to write to, which is the belief the old
    # panel encouraged.
    assert status["artifact_store"] == "coordinator-local-disk"


def test_a_half_configured_mirror_is_not_reported_as_configured(panel, monkeypatch):
    """All four or none, like the GitHub App. A bucket with no credentials
    cannot copy anything, and rounding it up to "configured" is how somebody
    stops worrying about a disk that still holds the only copy."""
    monkeypatch.setenv("OSS_BUCKET", OSS_BUCKET)

    status = panel()

    assert status["artifact_mirror"] == "not configured"
    assert status["oss_bucket"] is None


def test_the_panel_says_not_observed_rather_than_guessing(tmp_path, monkeypatch):
    """When ``Settings`` cannot be built at all, the panel does not know and
    says so. "not configured" would be a claim about the mirror inferred from
    a fact about Supabase — the house rule from ``metrics.py``: a value that
    was never derived is never reported as a plausible default."""
    monkeypatch.setenv("FLASHML_CLOUD_DB", str(tmp_path / "cloud.db"))
    for var in PANEL_ENV + ("FLASHML_REQUIRE_AUTH",):
        monkeypatch.delenv(var, raising=False)

    with TestClient(create_app()) as c:
        status = c.get("/v1alpha1/integration").json()

    assert status["artifact_mirror"] == "not observed"
    assert status["oss_bucket"] is None
