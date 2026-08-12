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
  the two environment variables nothing in this system consumes;
- and (section 5) the two browser routes that make any of it visible: the
  artifact LISTING, which comes from the coordinator whether or not a mirror
  exists because it is the only inventory that includes a failed shard's
  output, and the DOWNLOAD, which answers 307 to a presigned OSS URL when the
  job is mirrored and proxies exactly as it always did when it is not.

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
from datetime import datetime

import httpx
import jwt
import psycopg
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from flashml_cloud_api import app as appmod
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
        #: When true, reading anything raises. NOT the same as an empty
        #: bucket: this is a configured mirror that is unwell, and the
        #: download route's contract is that it costs a slower download
        #: rather than a failed one.
        self.reads_broken = False
        #: ``(key, content_disposition)`` for every URL signed — see
        #: ``sign_get``.
        self.signed: list[tuple[str, str | None]] = []

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
        if self.reads_broken:
            raise OSSUnavailable("bucket refused the read")
        if key not in self.objects:
            raise OSSUnavailable(f"cannot read {key}")
        return self.objects[key]

    def head(self, key: str):
        if self.reads_broken:
            raise OSSUnavailable("bucket refused the read")
        if key not in self.objects:
            return None
        return StoredObject(key=key, size_bytes=len(self.objects[key]),
                            sha256=None, etag=None, last_modified=None)

    def sign_get(self, key: str, *, ttl_s: int = 900,
                 content_disposition: str | None = None) -> str:
        # The real `sign_url` folds `response-content-disposition` INTO the
        # signature, so it cannot be appended to a returned string and cannot
        # be read back out of one either. This fake therefore records what it
        # was asked to sign with, and renders it into the URL only so a test
        # failure prints something legible.
        self.signed.append((key, content_disposition))
        suffix = "" if content_disposition is None else "&disposition=set"
        return f"https://example.invalid/{key}?Expires={ttl_s}{suffix}"

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
    """Stand in for the bucket at the two places a client is built.

    Patched on the importing modules rather than on ``alibaba_oss``: both
    import the name, so binding it there is what they actually resolve.
    ``artifact_mirror`` builds one to mirror a job; ``app`` builds one to sign
    a download's redirect. Both are patched to the SAME bucket, which is what
    makes "the mirror wrote it, so the download route can sign it" a property
    of these tests rather than of two agreeing stubs. Everything above the
    client — selection, ETag verification, manifest-last ordering,
    idempotence, and the manifest lookup behind a presign — is the real code.
    """
    bucket = FakeOSS()
    stub = type(
        "OSSArtifactsStub", (), {"from_settings": staticmethod(lambda s: bucket)}
    )
    monkeypatch.setattr(mirrormod, "OSSArtifacts", stub)
    monkeypatch.setattr(appmod, "OSSArtifacts", stub)
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


# ---------------------------------------------------------------------------
# 5. the two browser routes: listing a job's artifacts, and downloading one
#
# The mirror existed to make a model survive a redeploy. These two routes are
# what makes it survive being LOOKED FOR: before them the console read
# `job.artifacts`, a field whose only writer is on a code path this deployment
# never takes, so the Artifacts card was empty for every job this product has
# ever run — mirrored or not. The listing is the coordinator's, always,
# because it is the only inventory that includes a FAILED shard's output; the
# download prefers OSS, because a 500 MB model has no business travelling
# through the control plane twice once a copy exists that the browser can
# fetch directly.
# ---------------------------------------------------------------------------


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _list_artifacts(client, token: str, job_id: str):
    return client.get(f"/v1alpha1/jobs/{job_id}/artifacts", headers=_auth(token))


def _download(client, token: str, job_id: str, key: str):
    """One download click. ``follow_redirects=False`` is the whole point: the
    test has to see the 307 itself, not the bytes at the end of it."""
    return client.get(
        f"/v1alpha1/jobs/{job_id}/artifacts/{key}",
        headers=_auth(token), follow_redirects=False,
    )


#: What `_finished_job` leaves on the coordinator, keys relative to the job
#: prefix. The FAILED shard's stderr is in here deliberately: it is never
#: mirrored (hard rule 4) and it must still be listed and still be readable,
#: because "why did my job fail" is answered by exactly that file.
EXPECTED_KEYS = [
    "reduced/best.json",
    "shard-000/metrics.json",
    "shard-000/model.bin",
    "shard-001/stderr.txt",
]


def test_the_listing_is_the_coordinators_keys_relative_to_the_job_prefix(
    client, db, transport, oss
):
    """The shape the console consumes, and the reason the keys are relative:
    each one concatenates onto this route's own path to give the download
    link, so the browser never has to know the store's layout."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "list-me")

    r = _list_artifacts(client, token, job_id)

    assert r.status_code == 200, r.text
    body = r.json()
    assert [a["key"] for a in body["artifacts"]] == EXPECTED_KEYS
    assert body["artifacts"][2] == {
        "key": "shard-000/model.bin", "size_bytes": len(b"\x00\x01weights"),
    }
    # Never opened, so never mirrored: the two fields that describe where a
    # download will come from say the coordinator, and they are not guesses.
    assert body["storage"] == "coordinator"
    assert body["mirrored_at"] is None


def test_the_listing_reports_oss_only_once_the_job_is_really_mirrored(
    client, db, transport, oss
):
    """`storage` is a claim about the NEXT download, so it may only flip when
    the download route would really redirect. The inventory itself does not
    change: the failed shard's output is still listed and still on the
    coordinator's disk, which is where a mirror is not allowed to put it."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "listed-after-mirror")
    assert _list_artifacts(client, token, job_id).json()["storage"] == "coordinator"

    _open(client, token, job_id)

    body = _list_artifacts(client, token, job_id).json()
    assert body["storage"] == "oss"
    assert body["mirrored_at"] is not None
    # ISO 8601, parseable — `str(datetime)` would give a space instead of a T
    # and put the console in the implementation-defined corner of `Date`.
    assert datetime.fromisoformat(body["mirrored_at"]).tzinfo is not None
    assert [a["key"] for a in body["artifacts"]] == EXPECTED_KEYS


def test_an_unconfigured_deployment_lists_and_downloads_exactly_as_before(
    unconfigured_client, db, transport, oss
):
    """THE DEPLOYMENT DEFAULT, and the one this whole feature is gated on. No
    OSS plan has been bought; uploading to an unconfigured account is a real
    cost and a real failure. So: the listing works, it says `coordinator`, the
    download proxies bytes, and not one byte goes anywhere near a bucket."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(unconfigured_client, db, transport, token, "no-oss-listing")

    _open(unconfigured_client, token, job_id)
    listed = _list_artifacts(unconfigured_client, token, job_id)
    got = _download(unconfigured_client, token, job_id, "shard-000/model.bin")

    assert listed.status_code == 200, listed.text
    assert [a["key"] for a in listed.json()["artifacts"]] == EXPECTED_KEYS
    assert listed.json()["storage"] == "coordinator"
    assert listed.json()["mirrored_at"] is None
    assert got.status_code == 200
    assert got.content == b"\x00\x01weights"
    assert oss.objects == {} and oss.puts == []


def test_a_mirrored_artifact_is_a_307_to_oss_and_is_not_proxied(
    client, db, transport, oss
):
    """The egress move. A redirect means the bytes go from OSS to the browser
    and never through this process — so the assertion that matters as much as
    the 307 is that the coordinator was not asked for the object either."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "redirect-me")
    _open(client, token, job_id)
    reads_before = transport.artifact_reads()

    r = _download(client, token, job_id, "shard-000/model.bin")

    assert r.status_code == 307, r.text
    # The presigned URL names the OSS key, which IS the coordinator key —
    # `job_prefix` produces the coordinator's own layout precisely so that
    # nothing has to translate between the two key spaces.
    assert r.headers["location"].startswith(
        f"https://example.invalid/jobs/{job_id}/shard-000/model.bin"
    )
    assert transport.artifact_reads() == reads_before


def test_an_unaccepted_tasks_output_still_downloads_from_the_coordinator(
    client, db, transport, oss
):
    """A FAILED shard's stderr is never mirrored, and must not become
    unreachable because of it. Not being in the manifest is a fact about that
    key, not a failure, and the answer to it is the old one: proxy."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "failed-shard-download")
    _open(client, token, job_id)
    assert f"jobs/{job_id}/shard-001/stderr.txt" not in oss.objects

    r = _download(client, token, job_id, "shard-001/stderr.txt")

    assert r.status_code == 200, r.text
    assert r.content == b"traceback\n"


def test_a_job_with_no_mirror_downloads_byte_for_byte_as_it_always_did(
    client, db, transport, oss
):
    """Configured OSS, but this job was never mirrored — nobody has opened it.
    The marker is the guard, and it is read off the row the visibility check
    already fetched, so an unmirrored job costs no OSS call at all."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "not-mirrored-yet")

    r = _download(client, token, job_id, "shard-000/model.bin")

    assert r.status_code == 200, r.text
    assert r.content == b"\x00\x01weights"
    assert oss.objects == {}


def test_a_bucket_that_cannot_be_read_costs_a_slower_download_not_a_failed_one(
    client, db, transport, oss
):
    """The mirror is an ADDITIONAL copy, never a migration, so the
    coordinator still holds every byte this route can be asked for. A 502 for
    an artifact sitting on a disk we can reach would make configuring OSS
    strictly worse than not configuring it."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "sick-bucket")
    _open(client, token, job_id)
    oss.reads_broken = True

    r = _download(client, token, job_id, "shard-000/model.bin")

    assert r.status_code == 200, r.text
    assert r.content == b"\x00\x01weights"


def test_a_failed_mirror_leaves_the_listing_and_the_download_on_the_coordinator(
    client, db, transport, oss
):
    """The user-visible half of "nothing is recorded on failure". A mirror
    that failed must not leave the console claiming OSS storage, and must not
    leave a download route signing URLs for objects that were never
    certified — the retry is what fixes both."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "half-copied")
    oss.fail_put_on = {f"jobs/{job_id}/shard-000/model.bin"}
    _open(client, token, job_id)

    body = _list_artifacts(client, token, job_id).json()
    got = _download(client, token, job_id, "reduced/best.json")

    assert body["storage"] == "coordinator"
    assert body["mirrored_at"] is None
    # `reduced/best.json` may well be IN the bucket — the manifest is written
    # last, so a failure leaves objects behind it. Without a manifest none of
    # them is signable, which is the readable, retryable failure that ordering
    # buys.
    assert got.status_code == 200
    assert got.content == b'{"best":"000"}'


def test_the_listing_drops_foreign_entries_and_never_guesses_a_size():
    """``_relative_artifacts`` on its own, because the awkward entries do not
    come up against a healthy fake coordinator and are exactly what another
    service's payload is made of.

    The two directions are asymmetric on purpose. A key that is not under this
    job's prefix is DROPPED — a listing that disagrees with the job it was
    asked about is not evidence about this job, and filing it here would
    attribute someone else's output. A size that cannot be read is KEPT at 0:
    the file demonstrably exists, hiding it would be the worse error, and a
    guessed size is a number nobody measured. ``bool`` is excluded explicitly
    because ``True`` is an ``int`` in Python and would otherwise report a
    one-byte model — the same trap ``storage.sum_artifact_sizes`` documents
    about this very payload.
    """
    listing = [
        {"key": "jobs/j1/shard-001/b.txt", "size_bytes": 12},
        {"key": "jobs/j1/shard-000/a.bin", "size_bytes": 7},
        {"key": "jobs/OTHER/shard-000/a.bin", "size_bytes": 9},
        {"key": "jobs/j1/", "size_bytes": 4},
        {"key": "jobs/j1/no-size.txt"},
        {"key": "jobs/j1/bool-size.txt", "size_bytes": True},
        {"key": "jobs/j1/negative.txt", "size_bytes": -5},
        {"size_bytes": 3},
        "not-a-dict",
    ]

    assert appmod._relative_artifacts("j1", listing) == [
        {"key": "bool-size.txt", "size_bytes": 0},
        {"key": "negative.txt", "size_bytes": 0},
        {"key": "no-size.txt", "size_bytes": 0},
        {"key": "shard-000/a.bin", "size_bytes": 7},
        {"key": "shard-001/b.txt", "size_bytes": 12},
    ]


def test_deleting_the_artifacts_takes_the_listing_with_them(
    client, db, transport, oss
):
    """The release valve, seen from the card. Both copies are gone, so the
    listing is empty rather than a set of links that 404 one click later."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "listed-then-deleted")
    _open(client, token, job_id)

    client.delete(f"/v1alpha1/jobs/{job_id}/artifacts", headers=_auth(token))

    body = _list_artifacts(client, token, job_id).json()
    assert body["artifacts"] == []
    # The row still remembers that a mirror once existed; the bucket is empty,
    # so the download route finds no manifest and falls back — which for a
    # deleted job is an honest 404 from the coordinator rather than a signed
    # URL to nothing.
    assert _download(client, token, job_id, "shard-000/model.bin").status_code == 404


# ---------------------------------------------------------------------------
# 6. the split that removes the 401
#
# The console was changed to plain `<a href download>` anchors so a browser
# would follow the 307 above into OSS. It does follow it — and it sends no
# `Authorization` header on the way, because a NAVIGATION cannot. Every
# download answered 401. Reverting to an authenticated fetch() only moves the
# failure: the fetch follows the same 307 to a third origin that grants this
# console no CORS, and pulls whole model weights through browser memory.
#
# So the two facts are separated. `GET .../artifact-url/{key}` is an ordinary
# authenticated JSON call that answers with a presigned URL — a credential of
# OSS's, scoped to one object and expiring on its own, which a navigation may
# therefore carry. Coordinator-only bytes keep coming through the download
# route, with the header, as a fetch: there is nowhere else those bytes live,
# and the coordinator's disk is 5 GB, so that transfer is bounded by the disk
# rather than by hope.
#
# These tests walk the sequence the console actually walks, header and all,
# for both halves.
# ---------------------------------------------------------------------------


def _artifact_url(client, token: str, job_id: str, key: str):
    """The console's first call on a click, with the bearer header it can
    always send — because it is a fetch, not a navigation."""
    return client.get(
        f"/v1alpha1/jobs/{job_id}/artifact-url/{key}", headers=_auth(token)
    )


def _navigate(client, job_id: str, key: str):
    """What an `<a href download>` click actually sends: no header at all.

    This is the bug in one function. Kept as its own helper so a test can
    state the fact rather than describe it.
    """
    return client.get(
        f"/v1alpha1/jobs/{job_id}/artifacts/{key}", follow_redirects=False
    )


def test_a_navigation_to_the_download_route_still_401s_which_is_the_bug(
    client, db, transport, oss
):
    """The premise, pinned. `current_user` reads a bearer token from the
    `Authorization` header and from nothing else, and that is correct — this
    test exists so nobody "fixes" the download by weakening it, and so the two
    tests below are visibly about a real failure rather than a hypothetical
    one. The route is unchanged; what changed is that the console no longer
    navigates to it."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "navigation-401")
    _open(client, token, job_id)

    assert _navigate(client, job_id, "shard-000/model.bin").status_code == 401
    # And the fix is not "make this one open either": the URL route is
    # authenticated too, because it mints a grant to somebody's model.
    assert client.get(
        f"/v1alpha1/jobs/{job_id}/artifact-url/shard-000/model.bin"
    ).status_code == 401


def test_a_mirrored_key_downloads_by_asking_for_a_url_and_navigating_to_oss(
    client, db, transport, oss
):
    """THE 401 IS GONE, mirrored half. The whole click, in the order the
    console performs it: one authenticated JSON call that succeeds, and a URL
    that carries its own grant so the navigation after it needs no header.
    The bytes never touch this process — the coordinator is not asked for the
    object at any point."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "url-then-navigate")
    _open(client, token, job_id)
    reads_before = transport.artifact_reads()

    r = _artifact_url(client, token, job_id, "shard-000/model.bin")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["storage"] == "oss"
    assert body["url"].startswith(
        f"https://example.invalid/jobs/{job_id}/shard-000/model.bin"
    )
    # Nothing that could only come from this API's own auth is in the string:
    # the grant is OSS's, scoped to one object and expiring on its own.
    assert token not in body["url"]
    assert transport.artifact_reads() == reads_before


def test_an_unmirrored_key_downloads_through_the_api_with_the_bearer_header(
    client, db, transport, oss
):
    """THE 401 IS GONE, coordinator half — and this is the per-key fallback,
    not a differently-configured deployment. The job IS mirrored; the FAILED
    shard's stderr is not, because hard rule 4 mirrors accepted work only. A
    console that took the job's `storage` as the answer for every key would
    fail on exactly the file somebody opens after a failure.

    The answer is a null url, which is a fact and not an error, and the bytes
    then come through the download route — with the header, as a fetch, which
    is a thing a fetch can send and a navigation cannot."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "per-key-fallback")
    _open(client, token, job_id)
    # The job as a whole says OSS. The key does not, and both are true.
    assert _list_artifacts(client, token, job_id).json()["storage"] == "oss"

    told = _artifact_url(client, token, job_id, "shard-001/stderr.txt")
    got = _download(client, token, job_id, "shard-001/stderr.txt")

    assert told.status_code == 200, told.text
    assert told.json() == {"storage": "coordinator", "url": None}
    assert got.status_code == 200
    assert got.content == b"traceback\n"


def test_with_no_oss_every_key_answers_coordinator_and_no_bucket_is_touched(
    unconfigured_client, db, transport, oss
):
    """The deployment default. `Settings.oss_configured` gates the whole
    feature, so the URL route answers the same thing for every key, no client
    is built and no socket is opened — and the console takes the blob path for
    everything, exactly as it did before any of this existed."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(unconfigured_client, db, transport, token, "no-oss-url")
    _open(unconfigured_client, token, job_id)

    for key in EXPECTED_KEYS:
        r = _artifact_url(unconfigured_client, token, job_id, key)
        assert r.status_code == 200, r.text
        assert r.json() == {"storage": "coordinator", "url": None}
    assert oss.objects == {} and oss.signed == []

    got = _download(unconfigured_client, token, job_id, "shard-000/model.bin")
    assert got.status_code == 200
    assert got.content == b"\x00\x01weights"


def test_a_sick_bucket_answers_coordinator_rather_than_failing_the_click(
    client, db, transport, oss
):
    """Same rule the download route already follows, at the new door: the
    mirror is an ADDITIONAL copy, so a bucket having a bad minute must cost a
    slower download and not a broken one. The console reads the null url as
    "fetch it through the API" and the person gets their file."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "url-sick-bucket")
    _open(client, token, job_id)
    oss.reads_broken = True

    told = _artifact_url(client, token, job_id, "shard-000/model.bin")
    got = _download(client, token, job_id, "shard-000/model.bin")

    assert told.json() == {"storage": "coordinator", "url": None}
    assert got.status_code == 200
    assert got.content == b"\x00\x01weights"


def test_the_url_route_is_viewer_scoped_like_every_other_job_route(
    client, db, transport, oss
):
    """It mints a grant to somebody's model, so the visibility check runs
    BEFORE anything is signed — and answers 404 rather than 403, the same
    rule the rest of this block follows so a guesser cannot learn which id is
    real. A presigned URL carries no identity of its own; handing one to a
    stranger would be access the check exists to refuse, in a form that keeps
    working after they are removed from the pool."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "not-yours")
    _open(client, token, job_id)
    signed_before = list(oss.signed)

    mallory = _browser_jwt(_new_user(db))
    r = _artifact_url(client, mallory, job_id, "shard-000/model.bin")

    assert r.status_code == 404
    assert oss.signed == signed_before, "a refused caller must cost no signature"


# -- Content-Disposition ------------------------------------------------------


def test_a_mirrored_download_is_signed_as_an_attachment_named_for_its_key(
    client, db, transport, oss
):
    """WITHOUT THIS A DOWNLOAD NAVIGATES THE CONSOLE AWAY. The browser goes to
    OSS itself, and a `metrics.json` served as JSON with no disposition renders
    in the tab the console was in. The anchor's own `download` attribute cannot
    save it — OSS is a third origin, so the browser ignores the attribute — so
    naming the file is the header's job or nobody's.

    The name is the KEY with its separators flattened, not the last segment:
    every task of a job writes `stdout.txt`, and twenty shards saving as
    `stdout (1).txt` … erases the one fact — which task — the person opened
    them to learn."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "disposition-oss")
    _open(client, token, job_id)

    _artifact_url(client, token, job_id, "shard-000/metrics.json")

    assert oss.signed[-1] == (
        f"jobs/{job_id}/shard-000/metrics.json",
        'attachment; filename="shard-000__metrics.json"',
    )


def test_the_proxied_download_sends_the_same_disposition(
    client, db, transport, oss
):
    """The coordinator serves a store, not a browser, so it has no reason to
    send this and does not. Setting it here means a file saves under the same
    name whichever path it came down — which is the point of having two paths
    at all."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "disposition-proxy")
    _open(client, token, job_id)

    r = _download(client, token, job_id, "shard-001/stderr.txt")

    assert r.status_code == 200
    assert r.headers["content-disposition"] == (
        'attachment; filename="shard-001__stderr.txt"'
    )


def test_two_tasks_writing_the_same_filename_do_not_collide(
    client, db, transport, oss
):
    """The reason the whole key is flattened rather than the last segment
    taken. Both of these are `stderr.txt`; the disposition is what keeps them
    apart once they are in a downloads folder, because the anchor's `download`
    attribute is ignored for a cross-origin url and the API is a different
    origin from the console in every deployment."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _submit(client, token, "same-filename")
    transport.seed_artifact(f"jobs/{job_id}/shard-000/stderr.txt", b"one\n")
    transport.seed_artifact(f"jobs/{job_id}/shard-001/stderr.txt", b"two\n")
    transport.finish(job_id)

    first = _download(client, token, job_id, "shard-000/stderr.txt")
    second = _download(client, token, job_id, "shard-001/stderr.txt")

    assert first.headers["content-disposition"] != second.headers["content-disposition"]
    assert first.headers["content-disposition"] == (
        'attachment; filename="shard-000__stderr.txt"'
    )


def test_a_failed_download_is_not_labelled_as_a_file_to_save(
    client, db, transport, oss
):
    """The coordinator's 404 body is an error message, and offering to save it
    as `no-such__thing.bin` would turn a missing file into a downloaded one."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "disposition-404")

    r = _download(client, token, job_id, "no-such/thing.bin")

    assert r.status_code == 404
    assert "content-disposition" not in r.headers


def test_the_disposition_filename_cannot_carry_a_quote_or_a_header_break():
    """The invariant `_attachment_disposition` depends on, pinned to the thing
    that guarantees it.

    The header is built as a quoted-string with no escaping, which is only safe
    because `_artifact_key` has already refused every character that would need
    escaping — `PATH_SEGMENT_RE` admits no quote, backslash, semicolon, CR, LF
    or anything outside ASCII. That is a dependency between two functions, not
    a coincidence, and loosening the alphabet without revisiting the header
    would let an artifact key inject a header parameter. Assert the pair, not
    one side of it.
    """
    for bad in ['a"b', "a\\b", "a;b", "a\rb", "a\nb", "a b", "modèle.bin", "a,b"]:
        assert not appmod.PATH_SEGMENT_RE.match(bad), bad
        with pytest.raises(HTTPException) as caught:
            appmod._artifact_key(f"job-1/{bad}")
        assert caught.value.status_code == 400

    assert appmod._attachment_disposition("shard-000/ckpt/step-20.json") == (
        'attachment; filename="shard-000__ckpt__step-20.json"'
    )


# -- routing: the greedy `{key:path}` and the new segment ---------------------


def test_a_key_named_like_the_url_route_still_downloads_its_own_bytes(
    client, db, transport, oss
):
    """WHY THE URL ROUTE IS A SIBLING SEGMENT AND NOT A SUFFIX. The obvious
    shape — `.../artifacts/{key:path}/download-url` — is ambiguous by
    construction, because `{key:path}` is greedy and an artifact key is
    arbitrary: a job that wrote a file called `download-url` would find its own
    bytes unreachable, since that URL parses as a URL request for the parent
    directory. `artifacts` and `artifact-url` differ one component EARLIER than
    the greedy part, so no key can shadow the route and the route can shadow no
    key. These two files are named to prove exactly that."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _submit(client, token, "awkward-keys")
    transport.seed_artifact(f"jobs/{job_id}/shard-000/download-url", b"i am a file\n")
    transport.seed_artifact(f"jobs/{job_id}/artifact-url/x.bin", b"so am i\n")
    transport.finish(job_id)

    assert [a["key"] for a in _list_artifacts(client, token, job_id).json()["artifacts"]] == [
        "artifact-url/x.bin", "shard-000/download-url",
    ]
    named = _download(client, token, job_id, "shard-000/download-url")
    prefixed = _download(client, token, job_id, "artifact-url/x.bin")

    assert named.status_code == 200 and named.content == b"i am a file\n"
    assert prefixed.status_code == 200 and prefixed.content == b"so am i\n"
    # And asking the URL route about that same awkward key reaches the URL
    # route, with the key intact.
    told = _artifact_url(client, token, job_id, "artifact-url/x.bin")
    assert told.status_code == 200
    assert told.json() == {"storage": "coordinator", "url": None}


def test_the_url_route_refuses_a_key_the_download_route_would_refuse(
    client, db, transport, oss
):
    """Both doors run `_artifact_key`, so a traversal attempt is a 400 at
    either one. Two doors to the same object must not disagree about which
    keys exist."""
    alice = _new_user(db)
    token = _browser_jwt(alice)
    job_id = _finished_job(client, db, transport, token, "traversal")
    _open(client, token, job_id)

    assert _artifact_url(
        client, token, job_id, "..%2F..%2Fleases%2Fclaim"
    ).status_code == 400
    assert _download(
        client, token, job_id, "..%2F..%2Fleases%2Fclaim"
    ).status_code == 400
