"""The orchestrator, against a simulated sandbox and a real database.

The properties pinned here are the ones whose failure costs money, lies to a
judge, or runs one session twice:

- **Cleanup is total and it happens on every path.** A sandbox left running
  bills by the second and a machine token left alive is a live credential for
  a machine nobody is watching. So every failure injected below is checked for
  both halves, not one — and the failures are injected at every state, because
  "we handled the error" is a claim about the path it happened on.
- **Compare-and-set decides who drives.** Two controllers is the ordinary
  shape of this system after a restart during a long hibernation. Exactly one
  must wake the sandbox and exactly one evaluation job may exist.
- **The wake path never re-bootstraps.** The worker survives hibernation
  (measured 5/5, lease claims 13 → 19 across a pause); reinstalling the agent
  would be a second worker under one node id.
- **Restartable from the row plus ``external_sandbox_id``.** The resume test
  hands ``on_model_ready`` a session id and a fresh connection and nothing
  else, which is exactly what a redeployed API has.
- **Absent is a fact.** ``head`` returning ``None`` means the model is not
  there yet. It must not transition, must not wake anything, and must not
  raise.
- **Nothing that looks like a credential reaches the ledger.** The machine
  token and the presigned URLs both pass through this module; neither may
  appear in any event payload.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shlex
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

from flashml_cloud_api import db as dbmod
from flashml_cloud_api import sandbox_orchestrator as orch
from flashml_cloud_api import sandbox_sessions as ss
from flashml_cloud_api.alibaba_oss import StoredObject
from flashml_cloud_api.alibaba_sandbox import (
    CommandEvidence,
    FakeSandboxGateway,
    SandboxState,
)
from flashml_cloud_api.artifact_mirror import (
    MirroredObject,
    build_manifest,
    manifest_key,
)
from flashml_cloud_api.sandbox_bootstrap import (
    DEFAULT_FLASHNODE_VERSION,
    DEFAULT_PATHS,
)
from flashml_cloud_api.settings import Settings

def _model_key(job_id: str) -> str:
    return f"jobs/{job_id}/it00-shard-000/model.pt"


#: Zeroed everywhere so the suite never sleeps. The production defaults exist
#: to outlast a ~4 s registration crash window that the fake reproduces
#: instantaneously.
FAST_BOOTSTRAP = {
    "register_settle_s": 0.0,
    "poll_interval_s": 0.0,
    "claim_window_s": 0.0,
}


# ---------------------------------------------------------------------------
# A sandbox with a very small shell in it.
#
# Same shape as `test_sandbox_bootstrap`'s: answers are computed from the
# fake's own file state wherever possible, so a test that broke the module's
# file handling fails here rather than sailing past a canned string.
# ---------------------------------------------------------------------------


class _Sandbox(FakeSandboxGateway):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.commands: list[str] = []
        self.background_commands: list[str] = []
        #: (path, bytes, mode) for every file placed. Kept so a test can read
        #: what was delivered after the sandbox has been killed — which is
        #: where the interesting assertions live, since cleanup runs on the
        #: happy path too.
        self.written: list[tuple[str, bytes, int]] = []
        self.live_pids: set[int] = set()
        self.launches = 0
        self.install_exit = 0
        self.installed_version = DEFAULT_FLASHNODE_VERSION
        self.coordinator_http = "200"
        self.unreachable_hits = 0
        self.worker_active = True
        self.marker_override: bytes | None = None
        #: Leave `artifacts.json` at the default mode, as a failed second
        #: `chmod` round trip would. `write_file` has no mode parameter in the
        #: SDK, so that is a real failure the ordering check must catch.
        self.break_artifacts_mode = False
        self.capabilities = {
            "sandbox_capable": False,
            "unsandboxed_argv_capable": True,
            "module_capable": False,
            "can_install_dependencies": True,
            "agent_version": DEFAULT_FLASHNODE_VERSION,
        }

    async def run(self, sandbox_id, command, *, timeout_s=300, background=False):
        self.commands.append(command)
        if background:
            self.background_commands.append(command)
        else:
            self.command_results[command] = self._answer(sandbox_id, command)
        evidence: CommandEvidence = await super().run(
            sandbox_id, command, timeout_s=timeout_s, background=background
        )
        if background and evidence.background_pid:
            self.launches += 1
            self.live_pids.add(evidence.background_pid)
        return evidence

    async def write_file(self, sandbox_id, path, data, *, mode=0o600):
        self.written.append((path, bytes(data), mode))
        await super().write_file(sandbox_id, path, data, mode=mode)
        if self.break_artifacts_mode and path == orch.evaluation_artifacts_path():
            self.sandboxes[sandbox_id].modes[path] = 0o644

    # -- the shell -----------------------------------------------------------

    def _answer(self, sandbox_id: str, command: str) -> tuple[int, str, str]:
        files = self.sandboxes[sandbox_id].files
        modes = self.sandboxes[sandbox_id].modes

        if command.startswith("python3 -m pip install"):
            return (self.install_exit, "Successfully installed flashnode", "")
        if "importlib.metadata" in command:
            return (0, self.installed_version + "\n", "")
        if command.startswith("mkdir -p"):
            return (0, "", "")
        if command.startswith("cat "):
            path = shlex.split(command)[1]
            return (0, (files.get(path) or b"").decode(), "")
        if command.startswith("sha256sum"):
            path = shlex.split(command)[1]
            data = files.get(path)
            return (0, hashlib.sha256(data).hexdigest() + "\n" if data else "", "")
        if command.startswith("stat -c %a"):
            path = shlex.split(command)[3]
            return (0, f"{modes.get(path, 0o644):o}\n", "")
        if command.startswith('echo "coordinator_http='):
            return (0, f"coordinator_http={self.coordinator_http}\n", "")
        if "flashnode.inventory.capabilities" in command:
            return (0, json.dumps(self.capabilities) + "\n", "")
        if command.startswith("rm -f "):
            path = shlex.split(command.split(";", 1)[0])[2]
            files.pop(path, None)
            return (0, "credential_present=no\n", "")
        if command.startswith("kill "):
            self.live_pids.discard(int(command.split()[1]))
            return (0, "", "")
        if command.startswith("P="):
            return (0, self._probe_output(command, files), "")
        raise AssertionError(f"the fake sandbox was asked something new: {command!r}")

    def _probe_output(self, command: str, files: dict[str, bytes]) -> str:
        pid = int(re.match(r"P=(\d*)", command).group(1) or 0)
        alive = pid in self.live_pids
        marker = self.marker_override if self.marker_override is not None \
            else files.get(DEFAULT_PATHS.marker)
        io_before = 100_000
        io_after = io_before + (2_048 if (alive and self.worker_active) else 0)
        return "\n".join([
            f"io_before={io_before}",
            "cpu_before=42",
            f"unreachable_before={self.unreachable_hits}",
            f"alive={'yes' if alive else 'no'}",
            f"io_after={io_after}",
            "cpu_after=43",
            f"unreachable_after={self.unreachable_hits}",
            "marker_sha256="
            + (hashlib.sha256(marker).hexdigest() if marker else ""),
            "credential_present="
            + ("yes" if DEFAULT_PATHS.credentials in files else "no"),
            "log_bytes=480",
            "fatal=0",
            "---flashml-log-tail---",
            "worker log tail",
        ]) + "\n"


# ---------------------------------------------------------------------------
# OSS and the evaluation driver, faked.
# ---------------------------------------------------------------------------


class _OSS:
    """Only the three methods this module and `artifact_mirror` reach for.

    ``head`` keeps ``alibaba_oss``'s contract exactly: ``None`` is *absent*
    and a raise is *broken*. Conflating them is the mistake the wake trigger
    cannot survive.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.head_raises: Exception | None = None
        self.signed: list[str] = []
        self.mint = 0

    def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def head(self, key: str) -> StoredObject | None:
        if self.head_raises is not None:
            raise self.head_raises
        data = self.objects.get(key)
        if data is None:
            return None
        return StoredObject(key=key, size_bytes=len(data), sha256=None,
                            etag=None, last_modified=None)

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    def sign_get(self, key: str, *, ttl_s: int = 900) -> str:
        self.mint += 1
        url = f"https://oss.test/{key}?Expires=1&Signature=sig{self.mint:04d}"
        self.signed.append(url)
        return url


@dataclass
class _Driver:
    """An `EvaluationDriver` that is idempotent on the session id, as the
    protocol requires — and that records enough for a test to prove exactly
    one job was ever placed."""

    accept: bool = True
    detail: str = ""
    never_settles: bool = False
    submissions: list[orch.EvaluationRequest] = field(default_factory=list)
    by_session: dict[str, str] = field(default_factory=dict)
    polls: int = 0
    submit_raises: Exception | None = None
    poll_raises: Exception | None = None

    async def submit(self, request: orch.EvaluationRequest) -> str:
        if self.submit_raises is not None:
            raise self.submit_raises
        existing = self.by_session.get(request.session_id)
        if existing:
            return existing
        self.submissions.append(request)
        job_id = f"eval-{len(self.submissions):04d}"
        self.by_session[request.session_id] = job_id
        return job_id

    async def poll(self, evaluation_job_id: str):
        self.polls += 1
        if self.poll_raises is not None:
            raise self.poll_raises
        if self.never_settles:
            return None
        return orch.EvaluationOutcome(
            evaluation_job_id=evaluation_job_id,
            accepted=self.accept,
            detail=self.detail,
            data={"accuracy": 0.91},
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    conn.autocommit = True
    conn.execute("delete from public.sandbox_sessions")
    try:
        yield conn
    finally:
        conn.close()


def _user(db) -> str:
    user_id = str(uuid.uuid4())
    with db.cursor() as cur:
        cur.execute("insert into auth.users (id, email) values (%s, %s)",
                    (user_id, f"{user_id[:8]}@example.com"))
        cur.execute("insert into public.profiles (id) values (%s)", (user_id,))
    return user_id


def _pool(db, owner_id: str) -> str:
    """Through the real constructor, which seats its owner as a member —
    ``pool_ids_for_machine`` intersects bindings with that membership, so a
    machine minted into a raw-inserted pool is stamped with no pools at all."""
    return str(dbmod.create_pool(db, name="fc-sandbox", owner_id=owner_id)["id"])


def _job(db, owner_id: str) -> str:
    """A unique id per test: `public.jobs` is not truncated between them, and
    two sessions naming one job id is not the thing under test here."""
    job_id = f"job-train-{uuid.uuid4().hex[:12]}"
    dbmod.insert_job(db, job_id=job_id, owner_id=owner_id, name="train",
                     source=None, spec=None, status="COMPLETED")
    return job_id


def _settings(pool_id: str, *, region: str = "ap-southeast-1") -> Settings:
    return Settings(
        supabase_url="https://project.supabase.co",
        supabase_service_key="service-key",
        coordinator_url="https://coordinator.test",
        coordinator_operator_token="operator-token",
        require_auth=True,
        fc_sandbox_api_key="not-a-real-key",
        fc_sandbox_api_url=f"https://api.{region}.e2b.fc.aliyuncs.com",
        fc_sandbox_domain=f"{region}.e2b.fc.aliyuncs.com",
        fc_sandbox_region=region,
        fc_sandbox_template="code-interpreter-v1",
        fc_sandbox_pool_id=pool_id,
        fc_sandbox_timeout_ms=3_600_000,
        oss_bucket="flashml-artifacts",
        oss_endpoint="oss-ap-southeast-1.aliyuncs.com",
        oss_access_key_id="key-id",
        oss_access_key_secret="key-secret",
    )


def _oss_with_model(job_id: str) -> _OSS:
    """OSS holding a complete mirror of the training job — the manifest is
    what ``head`` looks for and what ``presign_job_artifacts`` signs from."""
    oss = _OSS()
    payload = build_manifest(
        job_id,
        [MirroredObject(key=_model_key(job_id), size_bytes=1024, sha256="a" * 64)],
        ("it00-shard-000",),
    )
    oss.put(manifest_key(job_id), payload)
    oss.put(_model_key(job_id), b"weights")
    return oss


@dataclass
class _World:
    db: Any
    gateway: _Sandbox
    settings: Settings
    owner: str
    pool: str
    oss: _OSS
    driver: _Driver
    job: str

    @property
    def model_key(self) -> str:
        return _model_key(self.job)


def _world(db, *, region: str = "ap-southeast-1") -> _World:
    owner = _user(db)
    pool = _pool(db, owner)
    job = _job(db, owner)
    return _World(
        db=db,
        gateway=_Sandbox(region=region),
        settings=_settings(pool, region=region),
        owner=owner,
        pool=pool,
        oss=_oss_with_model(job),
        driver=_Driver(),
        job=job,
    )


async def _start(world: _World, **overrides) -> str:
    kwargs: dict[str, Any] = dict(
        owner_id=world.owner,
        training_job_id=world.job,
        evaluation_spec={"kind": "eval", "metric": "accuracy"},
    )
    kwargs.update(FAST_BOOTSTRAP)
    kwargs.update(overrides)
    return await orch.start_session(
        world.db, world.gateway, world.settings, **kwargs
    )


async def _wake(world: _World, session_id: str, **overrides) -> None:
    kwargs: dict[str, Any] = dict(
        session_id=session_id,
        driver=world.driver,
        oss=world.oss,
        poll_interval_s=0.0,
    )
    kwargs.update(overrides)
    await orch.on_model_ready(
        world.db, world.gateway, world.settings, **kwargs
    )


# ---------------------------------------------------------------------------
# Assertions shared by every failure test
# ---------------------------------------------------------------------------


def _machine_status(db, machine_id: str) -> str:
    row = db.execute("select status from public.machines where id = %s",
                     (machine_id,)).fetchone()
    return row["status"] if row else "missing"


def _assert_cleaned_up(world: _World, session_id: str) -> None:
    """Nothing is billing and nothing can still authenticate.

    Both halves, always. ``cleanup_session`` runs the revocation in a
    ``finally`` precisely so a kill that fails cannot take the credential down
    with it, and a test that checked only the sandbox would pass against an
    implementation that had quietly stopped revoking.
    """
    row = ss.fetch_session(world.db, session_id)
    assert row["state"] == "TERMINATED", row["state"]
    assert row["terminated_at"] is not None

    sandbox_id = row["external_sandbox_id"]
    if sandbox_id:
        assert sandbox_id not in world.gateway.sandboxes, "a sandbox was leaked"

    machine_id = row["machine_id"]
    if machine_id:
        assert _machine_status(world.db, str(machine_id)) == "revoked"
        assert dbmod.pool_ids_bound_to_machine(world.db, str(machine_id)) == []
    # ...and the isolation pool is free for the next session.
    assert dbmod.machine_ids_bound_to_pool(world.db, world.pool) == []


def _event_blob(db, session_id: str) -> str:
    return json.dumps(
        [dict(e["data"] or {}) for e in ss.events_for_session(db, session_id)],
        default=str,
    )


# ---------------------------------------------------------------------------
# Pure policy — no database, no gateway.
# ---------------------------------------------------------------------------


def test_the_node_id_and_the_marker_nonce_are_derived_not_random():
    """A retried bootstrap must reproduce the same identity and the same
    marker bytes. A random nonce would make a resumed preparation look, to
    `verify_worker`, exactly like a sandbox swapped underneath us."""
    session = "8b1f0f5c-0000-4000-8000-000000000001"
    assert orch.node_id_for(session) == orch.node_id_for(session)
    assert orch.marker_nonce_for(session) == orch.marker_nonce_for(session)
    assert orch.marker_nonce_for(session) != orch.marker_nonce_for(session[:-1] + "2")
    # It becomes an HTTP delegation header, so it must match `app.NODE_ID_RE`.
    assert re.match(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
                    orch.node_id_for(session))


def test_the_artifacts_document_is_a_file_and_serialises_stably():
    first = orch.artifacts_document(
        session_id="s", training_job_id="j",
        urls={"b": "https://x/b?Signature=1", "a": "https://x/a?Signature=2"},
        ttl_s=900, issued_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
    )
    second = orch.artifacts_document(
        session_id="s", training_job_id="j",
        urls={"a": "https://x/a?Signature=2", "b": "https://x/b?Signature=1"},
        ttl_s=900, issued_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
    )
    assert first == second
    assert json.loads(first)["schema"] == orch.ARTIFACT_MANIFEST_SCHEMA


# ---------------------------------------------------------------------------
# Refusals that cost nothing, because nothing was created.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_another_persons_training_job_is_simply_not_there(db):
    """404-not-403: a session for a job that is not yours must not confirm
    that the id is real, and must not create a sandbox to find out."""
    world = _world(db)
    stranger = _user(db)
    with pytest.raises(orch.TrainingJobNotAuthorised):
        await _start(world, owner_id=stranger)
    assert world.gateway.calls == []
    assert ss.unfinished_sessions(db) == []


@pytest.mark.asyncio
async def test_a_region_that_cannot_hibernate_is_refused_before_anything_exists(db):
    """`pause()` answers 403 PauseSessionForbidden outside ap-southeast-1,
    forever. A sandbox created for a session that can never hibernate is a
    sandbox billing for nothing, so this is decided from settings."""
    world = _world(db, region="us-west-1")
    with pytest.raises(orch.SandboxUnconfigured, match="hibernation"):
        await _start(world)
    assert world.gateway.calls == []


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_full_session_prepares_hibernates_wakes_evaluates_and_terminates(db):
    world = _world(db)

    session_id = await _start(world)

    row = ss.fetch_session(db, session_id)
    assert row["state"] == "HIBERNATED"
    assert row["external_sandbox_id"]
    assert row["marker_sha256"]
    assert world.gateway.sandboxes[row["external_sandbox_id"]].state \
        is SandboxState.PAUSED

    # The budget was asserted BEFORE the pause, never after: a sandbox that
    # would expire mid-hibernation takes the prepared environment with it.
    types = [e["type"] for e in ss.events_for_session(db, session_id)]
    assert types.index("sandbox.budget_checked") < types.index("sandbox.hibernated")

    await _wake(world, session_id)

    row = ss.fetch_session(db, session_id)
    assert row["state"] == "TERMINATED"
    assert row["evaluation_job_id"] == "eval-0001"
    types = [e["type"] for e in ss.events_for_session(db, session_id)]
    # Wake, verify, deliver, THEN submit. The woken worker claims every two
    # seconds, so a task queued any earlier can be picked up by a process that
    # finds no artifacts.json.
    assert (types.index("sandbox.woken")
            < types.index("worker.verified")
            < types.index("artifacts.presigned")
            < types.index(orch.EVALUATION_SUBMITTED)), types
    assert "evaluation.accepted" in types
    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_the_wake_and_the_time_to_productive_are_two_separate_numbers(db):
    """One blended figure would overstate the wake and hide everything folded
    into it. `sandbox.woken` is the provider's wake and nothing else;
    `ready_ms` is the controller's own cost of making the woken sandbox
    productive — health probe, presign and delivery."""
    world = _world(db)
    session_id = await _start(world)
    await _wake(world, session_id)

    events = {e["type"]: e for e in ss.events_for_session(db, session_id)}
    woken = events["sandbox.woken"]
    submitted = events[orch.EVALUATION_SUBMITTED]
    resumed = events["worker.resumed"]

    # The wake is attributed to the provider, and its latency is the connect.
    assert woken["source"] == "fc"
    assert woken["latency_ms"] == pytest.approx(
        world.gateway.simulated_latency_ms["connect"], abs=200
    )
    # The health check carries its own cost, not the wake's.
    assert resumed["latency_ms"] is not None
    assert resumed["data"]["wake_ms"] == woken["latency_ms"]
    # ...and time-to-productive is measured from the wake to the submission.
    assert submitted["data"]["ready_ms"] >= 0
    assert submitted["latency_ms"] is not None


@pytest.mark.asyncio
async def test_the_wake_verifies_the_worker_and_never_reinstalls_it(db):
    """Measured 5/5: the worker survives hibernation and keeps claiming
    (13 → 19 across a pause). A wake that reinstalled would be a second agent
    under one node id, failing half its own leases."""
    world = _world(db)
    session_id = await _start(world)
    installs_after_bootstrap = [
        c for c in world.gateway.commands if c.startswith("python3 -m pip install")
    ]

    await _wake(world, session_id)

    installs = [
        c for c in world.gateway.commands if c.startswith("python3 -m pip install")
    ]
    assert installs == installs_after_bootstrap == installs[:1]
    assert world.gateway.launches == 1, "the worker was relaunched on the wake path"


@pytest.mark.asyncio
async def test_the_presigned_urls_are_re_minted_on_the_wake_and_never_logged(db):
    """A URL signed before a hibernation is expired when the sandbox wakes, so
    it is minted after the health check — and it is a bearer credential with
    its signature in the query string, so it goes into the sandbox by
    `write_file` and appears in no event, no command and no error."""
    world = _world(db)
    session_id = await _start(world)
    assert world.oss.mint == 0, "nothing may be signed before the hibernation"

    await _wake(world, session_id)

    assert world.oss.mint >= 1

    # It reached the sandbox, as a FILE at 0600 — never as a command string,
    # which would put it in `CommandEvidence.command` and from there into the
    # ledger this module spends its redaction budget keeping clean.
    delivered = [
        (path, data, mode) for path, data, mode in world.gateway.written
        if path == orch.evaluation_artifacts_path()
    ]
    assert len(delivered) == 1
    path, data, mode = delivered[0]
    assert mode == 0o600
    document = json.loads(data)
    assert set(document["objects"]) == {world.model_key}
    assert document["objects"][world.model_key] in world.oss.signed

    blob = _event_blob(db, session_id)
    for url in world.oss.signed:
        assert url not in blob
    assert "Signature=" not in blob
    for command in world.gateway.commands:
        assert "Signature=" not in command
    # The keys ARE recorded — that is the evidence the re-mint happened.
    assert world.model_key in blob


@pytest.mark.asyncio
async def test_the_machine_token_never_reaches_the_ledger(db):
    """`sandbox_identity` hands the raw token out exactly once, in memory.
    Everything downstream of that — bootstrap observations included — is
    scrubbed, and this asserts it end to end rather than per module."""
    world = _world(db)
    session_id = await _start(world)
    blob = _event_blob(db, session_id)
    assert "fmk_" not in blob
    assert "raw_token" not in blob


def test_the_evaluation_spec_column_is_nullable_jsonb_and_not_shared(db):
    """Migration 0017, checked against the applied schema.

    Nullable with no default: NULL is the truth for every pre-0017 session and
    stays a legitimate value, and `'{}'::jsonb` as a default would assert an
    empty specification about rows that never had one. Absent from the public
    share view because a spec names datasets, repositories and thresholds
    belonging to whoever wrote it, and the evidence page proves a hibernation
    without publishing any of that.
    """
    with db.cursor() as cur:
        cur.execute(
            "select is_nullable, column_default, data_type"
            "  from information_schema.columns"
            " where table_schema = 'public' and table_name = 'sandbox_sessions'"
            "   and column_name = 'evaluation_spec'"
        )
        row = cur.fetchone()
    assert row is not None, "migration 0017 did not apply"
    assert row["is_nullable"] == "YES"
    assert row["column_default"] is None
    assert row["data_type"] == "jsonb"

    assert "evaluation_spec" in ss.SESSION_COLUMNS
    assert "evaluation_spec" not in ss.SESSION_SHARE_COLUMNS


@pytest.mark.asyncio
async def test_a_spec_field_named_like_a_secret_survives_the_hibernation(db):
    """The reason 0017 exists. `redact_data` matches KEY NAMES as substrings —
    correctly, for an SDK exception that echoes the request that caused it —
    so a spec carried in an event payload comes back with `api_key_name` and
    `token_budget` replaced by `[redacted]`, silently, on the one code path
    that only runs after a hibernation. A column is not an event.
    """
    world = _world(db)
    spec = {
        "metric": "accuracy",
        # Every one of these is an ordinary field name that the event
        # scrubber would have eaten.
        "api_key_name": "OPENAI_API_KEY",
        "token_budget": 4096,
        "secret_share_count": 3,
    }
    session_id = await _start(world, evaluation_spec=spec)

    # ...on the row, verbatim.
    assert ss.fetch_session(db, session_id)["evaluation_spec"] == spec
    # ...and that is what reaches the coordinator on the far side of the wake.
    await _wake(world, session_id)
    assert world.driver.submissions[0].spec == spec

    # This is what the old path did to it, and the reason the column exists.
    # `redact_data` is untouched — 0017 removes a USE of it, not the
    # mechanism — so the mangling is still exactly one function call away.
    mangled = ss.redact_data({"spec": spec})["spec"]
    assert mangled["api_key_name"] == "[redacted]"
    assert mangled["token_budget"] == "[redacted]"
    assert mangled["secret_share_count"] == "[redacted]"
    assert mangled["metric"] == "accuracy"

    # What an event carries now is the SHAPE of the spec, which nothing reads
    # back, so a scrubbed value there costs nobody anything.
    opened = [e for e in ss.events_for_session(db, session_id)
              if e["type"] == "session.opened"][0]
    assert opened["data"]["spec_keys"] == sorted(spec)


@pytest.mark.asyncio
async def test_the_evaluation_task_carries_the_isolation_pool(db):
    """The coordinator's seventh gate refuses a pool-scoped task to any node
    not stamped with that pool, and this pool holds exactly one machine. A
    submission that dropped it would place the evaluation on any volunteer
    laptop that happened to be online."""
    world = _world(db)
    session_id = await _start(world)
    await _wake(world, session_id)

    assert len(world.driver.submissions) == 1
    request = world.driver.submissions[0]
    assert request.pool_id == world.pool
    assert request.node_id == orch.node_id_for(session_id)
    assert request.training_job_id == world.job
    assert request.spec["metric"] == "accuracy"


# ---------------------------------------------------------------------------
# "Not ready yet" is a fact, not a failure.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_absent_model_moves_nothing_and_wakes_nothing(db):
    """The evaluator waits precisely because the model does not exist yet.
    `head` returning None is that fact; treating it as an error would fail
    every session whose training run had not finished."""
    world = _world(db)
    world.oss = _OSS()  # empty bucket
    session_id = await _start(world)
    calls_before = len(world.gateway.calls)

    await _wake(world, session_id)

    assert ss.fetch_session(db, session_id)["state"] == "HIBERNATED"
    assert world.driver.submissions == []
    assert len(world.gateway.calls) == calls_before, (
        "a paused sandbox was touched; run() and write_file() wake one at ~1s "
        "and put it back on billed time"
    )


@pytest.mark.asyncio
async def test_a_broken_bucket_is_not_an_absent_model(db):
    """`alibaba_oss.head` raises for a transport or auth failure and returns
    None for absence, and the two must never be confused: one means wait, the
    other means something is wrong."""
    world = _world(db)
    world.oss.head_raises = RuntimeError("oss is having a bad minute")
    session_id = await _start(world)

    with pytest.raises(RuntimeError):
        await _wake(world, session_id)
    # ...and the session is untouched: a bad minute at OSS is not a reason to
    # throw away a prepared, hibernated sandbox.
    assert ss.fetch_session(db, session_id)["state"] == "HIBERNATED"


# ---------------------------------------------------------------------------
# A failure at every state, with cleanup verified at each one.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_create_leaves_no_machine_and_no_pool_binding(db):
    world = _world(db)
    world.gateway.fail_next("create", kind="terminal")

    with pytest.raises(orch.SessionFailed) as raised:
        await _start(world)

    _assert_cleaned_up(world, raised.value.session_id)
    assert world.gateway.sandboxes == {}


@pytest.mark.asyncio
async def test_a_create_that_may_have_applied_is_reconciled_not_retried(db):
    """`asyncio.wait_for` abandons the await, not the thread: a timed-out
    create may have succeeded server-side. Retrying it makes a second sandbox
    nobody has the id of, so the answer is `inspect()` and record."""
    world = _world(db)
    world.gateway.fail_next("create", kind="transport", applied=True)

    session_id = await _start(world)

    row = ss.fetch_session(db, session_id)
    assert row["state"] == "HIBERNATED"
    assert len(world.gateway.sandboxes) == 1, "a second sandbox was created"
    assert [c for c, _ in world.gateway.calls].count("create") == 1
    types = [e["type"] for e in ss.events_for_session(db, session_id)]
    assert "create.reconciled" in types


@pytest.mark.asyncio
async def test_a_failed_bootstrap_kills_the_sandbox_and_revokes_the_credential(db):
    world = _world(db)
    world.gateway.install_exit = 1

    with pytest.raises(orch.SessionFailed) as raised:
        await _start(world)

    _assert_cleaned_up(world, raised.value.session_id)


@pytest.mark.asyncio
async def test_a_sandbox_with_no_budget_left_is_never_hibernated(db):
    """Two clocks may be running and only one has been measured. The
    assertion assumes the pessimistic reading, and it happens BEFORE the 2.6 s
    pause — a sandbox that expires mid-hibernation is gone and takes the
    prepared environment with it."""
    world = _world(db)

    with pytest.raises(orch.SessionFailed) as raised:
        await _start(world, expected_wait_s=86_400.0)

    session_id = raised.value.session_id
    assert [c for c, _ in world.gateway.calls].count("pause") == 0
    types = [e["type"] for e in ss.events_for_session(db, session_id)]
    assert "sandbox.budget_checked" in types
    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_the_providers_own_deadline_beats_the_configured_one(db):
    """`end_at` is what the sandbox says about itself; the configured lifetime
    is only the fallback for a provider that reports none."""
    world = _world(db)
    created: list[str] = []
    real_create = world.gateway.create

    async def create(**kwargs):
        observed = await real_create(**kwargs)
        created.append(observed.sandbox_id)
        world.gateway.set_expiry(
            observed.sandbox_id, datetime.now(timezone.utc) + timedelta(seconds=30)
        )
        return observed

    world.gateway.create = create

    with pytest.raises(orch.SessionFailed) as raised:
        await _start(world, expected_wait_s=60.0, resume_reserve_s=10.0)

    events = ss.events_for_session(db, raised.value.session_id)
    checked = [e for e in events if e["type"] == "sandbox.budget_checked"][0]
    assert checked["data"]["budget_source"] == "provider"
    _assert_cleaned_up(world, raised.value.session_id)


@pytest.mark.asyncio
async def test_a_pause_that_is_not_allowed_is_not_retried(db):
    """The allowlist 403 is permanent until Alibaba changes it. A retry loop
    around it burns budget on a call that cannot ever succeed — and reports
    "hibernation failed" when the truth is "hibernation was never available
    here"."""
    world = _world(db)
    world.gateway.fail_next("pause", kind="allowlist")

    with pytest.raises(orch.SessionFailed) as raised:
        await _start(world)

    assert [c for c, _ in world.gateway.calls].count("pause") == 1
    _assert_cleaned_up(world, raised.value.session_id)


@pytest.mark.asyncio
async def test_a_pause_that_actually_applied_is_believed_only_after_inspect(db):
    """The transport failed; the pause did not. The correct response is to go
    and look, and the ledger then records HIBERNATED because the API said
    PAUSED — not because we asked for a pause."""
    world = _world(db)
    world.gateway.fail_next("pause", kind="transport", applied=True)

    session_id = await _start(world)

    row = ss.fetch_session(db, session_id)
    assert row["state"] == "HIBERNATED"
    types = [e["type"] for e in ss.events_for_session(db, session_id)]
    assert "pause.reconciled" in types
    assert [c for c, _ in world.gateway.calls].count("pause") == 1


@pytest.mark.asyncio
async def test_a_sandbox_that_will_not_wake_leaks_nothing(db):
    """The one that must not leave a sandbox behind: the session is asleep,
    the model has arrived, and the sandbox refuses to come back."""
    world = _world(db)
    session_id = await _start(world)
    sandbox_id = ss.fetch_session(db, session_id)["external_sandbox_id"]
    world.gateway.break_wake(sandbox_id)

    with pytest.raises(orch.SessionFailed):
        await _wake(world, session_id)

    _assert_cleaned_up(world, session_id)
    assert sandbox_id not in world.gateway.sandboxes


@pytest.mark.asyncio
async def test_a_wake_that_comes_back_wrong_is_refused(db):
    """The marker hash is the evidence for the only continuity claim this
    design may make. A sandbox whose filesystem is not the one we prepared is
    not the right sandbox, however healthy the worker looks."""
    world = _world(db)
    session_id = await _start(world)
    world.gateway.marker_override = b"a different filesystem entirely"

    with pytest.raises(orch.SessionFailed, match="hibernation"):
        await _wake(world, session_id)

    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_a_dead_worker_is_reported_rather_than_repaired(db):
    world = _world(db)
    session_id = await _start(world)
    world.gateway.live_pids.clear()  # the worker did not survive

    with pytest.raises(orch.SessionFailed):
        await _wake(world, session_id)

    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_an_evaluation_that_refuses_is_a_result_not_an_exception(db):
    """Attempted work is not accepted work (repo hard rule 4). A job that ran
    and said no fails the session — and still kills the sandbox."""
    world = _world(db)
    world.driver.accept = False
    world.driver.detail = "accuracy below the threshold"
    session_id = await _start(world)

    with pytest.raises(orch.SessionFailed, match="EvaluationRejected"):
        await _wake(world, session_id)

    row = ss.fetch_session(db, session_id)
    assert row["error_code"] == "EvaluationRejected"
    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_an_evaluation_that_never_settles_is_bounded(db):
    """An unbounded poll against a coordinator that has stopped answering is a
    sandbox held open for ever."""
    world = _world(db)
    world.driver.never_settles = True
    session_id = await _start(world)

    with pytest.raises(orch.SessionFailed, match="EvaluationTimeout"):
        await _wake(world, session_id, evaluation_timeout_s=0.0)

    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_a_succeeded_session_still_owns_a_sandbox_until_cleanup_runs(db):
    """SUCCEEDED is not terminal and that is the expensive mistake this
    guards. The happy path ends in TERMINATED because `_await_and_settle`
    calls cleanup itself — not because the run finished."""
    world = _world(db)
    session_id = await _start(world)
    await _wake(world, session_id)

    row = ss.fetch_session(db, session_id)
    assert row["state"] == "TERMINATED"
    types = [e["type"] for e in ss.events_for_session(db, session_id)]
    assert types.index("evaluation.accepted") < types.index("session.terminated")
    assert ss.unfinished_sessions(db) == []


# ---------------------------------------------------------------------------
# Idempotence, races, and restarts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calling_on_model_ready_twice_produces_one_job_and_one_resume(db):
    world = _world(db)
    session_id = await _start(world)

    await _wake(world, session_id)
    await _wake(world, session_id)  # the poller fires again

    assert len(world.driver.submissions) == 1
    assert [c for c, _ in world.gateway.calls].count("connect") == 1
    resumes = [e for e in ss.events_for_session(db, session_id)
               if e["type"] == "session.resuming"]
    assert len(resumes) == 1


@pytest.mark.asyncio
async def test_two_controllers_race_and_exactly_one_drives(postgres_dsn, db):
    """A restart during a long hibernation is the ordinary way to get two
    controllers. The compare-and-set is the only thing that stops both of them
    waking one sandbox, and losing it is a normal outcome — not an error."""
    world = _world(db)
    session_id = await _start(world)

    other = psycopg.connect(postgres_dsn, row_factory=dict_row, connect_timeout=5)
    other.autocommit = True
    try:
        rival = _World(db=other, gateway=world.gateway, settings=world.settings,
                       owner=world.owner, pool=world.pool, oss=world.oss,
                       driver=world.driver, job=world.job)
        results = await asyncio.gather(
            _wake(world, session_id),
            _wake(rival, session_id),
            return_exceptions=True,
        )
    finally:
        other.close()

    assert [r for r in results if isinstance(r, BaseException)] == [], results
    assert len(world.driver.submissions) == 1
    assert [c for c, _ in world.gateway.calls].count("connect") == 1
    resumes = [e for e in ss.events_for_session(db, session_id)
               if e["type"] == "session.resuming"]
    assert len(resumes) == 1
    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_a_restart_mid_hibernation_resumes_from_the_persisted_row(
    postgres_dsn, db
):
    """Everything the wake path needs is the session row plus
    `external_sandbox_id`: the sandbox id, the marker hash, the worker pid,
    the evaluation spec and the pool. This hands a *different connection* a
    session id and nothing else, which is exactly what a redeployed API has."""
    world = _world(db)
    session_id = await _start(world)

    restarted = psycopg.connect(postgres_dsn, row_factory=dict_row,
                                connect_timeout=5)
    restarted.autocommit = True
    try:
        await orch.on_model_ready(
            restarted, world.gateway, world.settings,
            session_id=session_id, driver=world.driver, oss=world.oss,
            poll_interval_s=0.0,
        )
    finally:
        restarted.close()

    row = ss.fetch_session(db, session_id)
    assert row["state"] == "TERMINATED"
    assert row["evaluation_job_id"] == "eval-0001"
    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_a_controller_that_died_after_submitting_recorded_the_job_id(db):
    """The window between the coordinator accepting the job and the row
    learning its id is closed by the ledger event, which is appended
    immediately and in its own statement. Here the controller dies at the
    very next step and the id has already survived."""
    world = _world(db)
    session_id = await _start(world)
    world.driver.poll_raises = RuntimeError("the coordinator stopped answering")

    with pytest.raises(orch.SessionFailed):
        await _wake(world, session_id)

    assert len(world.driver.submissions) == 1
    recorded = [e for e in ss.events_for_session(db, session_id)
                if e["type"] == orch.EVALUATION_SUBMITTED]
    assert len(recorded) == 1
    assert recorded[0]["data"]["evaluation_job_id"] == "eval-0001"
    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_nothing_is_queued_until_the_artifacts_are_confirmed_on_disk(db):
    """The ordering rule, enforced by the code rather than by a comment. A
    task placed before `artifacts.json` is really there would be claimed by a
    worker that finds nothing — a failure that presents as a broken evaluation
    script, hours in, and reproduces roughly never."""
    world = _world(db)
    session_id = await _start(world)
    world.gateway.break_artifacts_mode = True

    with pytest.raises(orch.SessionFailed):
        await _wake(world, session_id)

    assert world.driver.submissions == [], (
        "work was queued for a sandbox whose artifacts could not be confirmed"
    )
    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_a_controller_that_died_after_the_health_check_finishes_the_job(db):
    """The ACTIVE recovery. There is no ACTIVE -> ACTIVE edge and asking for
    one raises, correctly — re-observing a state is an event, not a move — so
    the resumed call must skip that step and carry on from the presign."""
    world = _world(db)
    session_id = await _start(world)
    # The shape a controller that died between `verify_worker` and the
    # presign leaves behind: woken, verified, ACTIVE, nothing signed.
    ss.transition(db, session_id, "HIBERNATED", "RESUMING")
    ss.transition(db, session_id, "RESUMING", "ACTIVE",
                  evaluation_job_id="eval-0001")
    world.driver.by_session[session_id] = "eval-0001"

    await _wake(world, session_id)

    row = ss.fetch_session(db, session_id)
    assert row["state"] == "TERMINATED"
    assert row["evaluation_job_id"] == "eval-0001"
    assert world.driver.submissions == [], "the recorded job was resubmitted"
    assert world.oss.mint >= 1, "the presign never happened"
    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_a_controller_that_died_mid_wake_is_resumed_not_restarted(db):
    """RESUMING is the shape a controller that died between winning the CAS
    and finishing the wake leaves behind. The submitted job id is in the
    ledger; the resumed call must adopt it rather than place a second one."""
    world = _world(db)
    session_id = await _start(world)
    ss.transition(db, session_id, "HIBERNATED", "RESUMING")
    ss.append_event(db, session_id, ss.Observation(
        type=orch.EVALUATION_SUBMITTED, source="controller",
        data={"evaluation_job_id": "eval-earlier"},
    ))

    await _wake(world, session_id)

    row = ss.fetch_session(db, session_id)
    assert row["state"] == "TERMINATED"
    assert row["evaluation_job_id"] == "eval-earlier"
    assert world.driver.submissions == []
    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_a_session_start_is_still_building_is_not_woken_by_a_poller(db):
    """ACTIVE is two different situations: mid-`start_session`, and a resumed
    wake. The marker hash is what tells them apart, and confusing them would
    put two controllers on one session."""
    world = _world(db)
    session_id = await _start(world)
    db.execute(
        "update public.sandbox_sessions set state = 'ACTIVE',"
        " marker_sha256 = null where id = %s::uuid", (session_id,)
    )
    calls_before = len(world.gateway.calls)

    await _wake(world, session_id)

    assert ss.fetch_session(db, session_id)["state"] == "ACTIVE"
    assert world.driver.submissions == []
    assert len(world.gateway.calls) == calls_before


@pytest.mark.asyncio
async def test_waking_a_sandbox_with_nothing_to_give_it_is_refused(db):
    """Waking a sandbox to hand it no work is exactly the outcome the
    submit-before-connect ordering exists to prevent, so a missing driver is
    a caller bug and is refused before anything is touched."""
    world = _world(db)
    session_id = await _start(world)
    calls_before = len(world.gateway.calls)

    with pytest.raises(orch.EvaluationUnavailable):
        await orch.on_model_ready(db, world.gateway, world.settings,
                                  session_id=session_id, oss=world.oss)

    assert ss.fetch_session(db, session_id)["state"] == "HIBERNATED"
    assert len(world.gateway.calls) == calls_before


@pytest.mark.asyncio
async def test_cleanup_is_idempotent_and_makes_no_second_round_trip(db):
    world = _world(db)
    session_id = await _start(world)
    await orch.cleanup_session(db, world.gateway, world.settings,
                               session_id=session_id)
    calls = list(world.gateway.calls)

    await orch.cleanup_session(db, world.gateway, world.settings,
                               session_id=session_id)

    assert world.gateway.calls == calls
    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_the_credential_is_revoked_even_when_the_kill_fails(db):
    """The two halves are independent: the obvious implementation — kill, then
    revoke — silently stops revoking the moment Alibaba has a bad minute."""
    world = _world(db)
    session_id = await _start(world)
    machine_id = str(ss.fetch_session(db, session_id)["machine_id"])
    world.gateway.fail_next("kill", kind="terminal")

    await orch.cleanup_session(db, world.gateway, world.settings,
                               session_id=session_id)

    assert _machine_status(db, machine_id) == "revoked"
    assert dbmod.pool_ids_bound_to_machine(db, machine_id) == []
    # ...and TERMINATED was NOT recorded, because the API never confirmed it.
    # A TERMINATED row is a promise that nothing is running.
    assert ss.fetch_session(db, session_id)["state"] != "TERMINATED"
    assert [str(r["id"]) for r in ss.unfinished_sessions(db)] == [session_id], (
        "the reconciler must keep being handed this session until a kill "
        "actually succeeds"
    )


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_kills_the_sandbox_of_a_session_that_already_finished(db):
    """`unfinished_sessions` returns SUCCEEDED and FAILED on purpose: a run
    being over is not the same as the money having stopped. This is the leak
    that costs the most, because it is the sessions that went well."""
    world = _world(db)
    session_id = await _start(world)
    sandbox_id = ss.fetch_session(db, session_id)["external_sandbox_id"]
    # Force the shape a crashed controller leaves: the verdict recorded, the
    # sandbox still alive.
    ss.transition(db, session_id, "HIBERNATED", "RESUMING")
    ss.transition(db, session_id, "RESUMING", "ACTIVE")
    ss.transition(db, session_id, "ACTIVE", "EVALUATING")
    ss.transition(db, session_id, "EVALUATING", "SUCCEEDED")
    assert sandbox_id in world.gateway.sandboxes

    touched = await orch.reconcile(db, world.gateway, world.settings)

    assert touched == [session_id]
    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_reconcile_leaves_a_hibernated_sandbox_strictly_alone(db):
    """`run()` and `write_file()` implicitly wake a paused sandbox at ~1 s and
    put it back on billed time. A bookkeeping sweep that reached for either
    would cost money every time it ran."""
    world = _world(db)
    session_id = await _start(world)
    before = [c for c, _ in world.gateway.calls]

    touched = await orch.reconcile(db, world.gateway, world.settings)

    assert touched == []
    assert ss.fetch_session(db, session_id)["state"] == "HIBERNATED"
    after = [c for c, _ in world.gateway.calls]
    assert after == before + ["inspect"], after[len(before):]


@pytest.mark.asyncio
async def test_reconcile_records_a_pause_whose_response_was_lost(db):
    """The pause applied; the answer never arrived. The ledger says PREPARED,
    the API says PAUSED, and the API is what is true."""
    world = _world(db)
    session_id = await _start(world)
    # Rewind the ledger to the state a controller that died mid-pause leaves.
    db.execute("update public.sandbox_sessions set state = 'PREPARED'"
               " where id = %s::uuid", (session_id,))

    touched = await orch.reconcile(db, world.gateway, world.settings)

    assert touched == [session_id]
    assert ss.fetch_session(db, session_id)["state"] == "HIBERNATED"


@pytest.mark.asyncio
async def test_reconcile_terminates_a_session_whose_sandbox_vanished(db):
    """A session that thought work was in progress and has no sandbox to do it
    in. Saying so is the difference between a run that ended and one that
    vanished."""
    world = _world(db)
    session_id = await _start(world)
    sandbox_id = ss.fetch_session(db, session_id)["external_sandbox_id"]
    world.gateway.sandboxes.pop(sandbox_id)  # its TTL ran out while asleep

    touched = await orch.reconcile(db, world.gateway, world.settings)

    assert touched == [session_id]
    row = ss.fetch_session(db, session_id)
    assert row["state"] == "TERMINATED"
    assert row["error_code"] == "SandboxGone"
    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_reconcile_fails_a_session_no_controller_has_moved(db):
    """A forgotten sandbox bills indefinitely. A session stuck in a transient
    state past the staleness window has no controller left to finish it."""
    world = _world(db)
    session_id = await _start(world)
    db.execute("update public.sandbox_sessions set state = 'RESUMING'"
               " where id = %s::uuid", (session_id,))

    touched = await orch.reconcile(db, world.gateway, world.settings,
                                   stale_after_s=0.0)

    assert touched == [session_id]
    row = ss.fetch_session(db, session_id)
    assert row["error_code"] == "SessionAbandoned"
    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_reconcile_ends_a_hibernation_that_outlived_its_own_ttl(db):
    """A HIBERNATED session is exempt from the staleness rule — a hibernation
    is *meant* to last — so the deadline is the only thing that stops one
    billing for ever. The sandbox cannot outlive the TTL it was created with,
    whichever of the two clocks is actually running."""
    world = _world(db)
    session_id = await _start(world)
    sandbox_id = ss.fetch_session(db, session_id)["external_sandbox_id"]
    world.gateway.set_expiry(sandbox_id,
                             datetime.now(timezone.utc) - timedelta(seconds=1))

    touched = await orch.reconcile(db, world.gateway, world.settings)

    assert touched == [session_id]
    assert ss.fetch_session(db, session_id)["error_code"] == "SandboxExpired"
    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_reconcile_uses_the_configured_lifetime_when_none_is_reported(db):
    """The fallback deadline. Without it a provider that reports no `end_at`
    leaves a forgotten sandbox with nothing to sweep it."""
    world = _world(db)
    session_id = await _start(world)
    assert ss.fetch_session(db, session_id)["state"] == "HIBERNATED"
    world.settings.fc_sandbox_timeout_ms = 0

    touched = await orch.reconcile(db, world.gateway, world.settings)

    assert touched == [session_id]
    assert ss.fetch_session(db, session_id)["error_code"] == "SandboxExpired"
    _assert_cleaned_up(world, session_id)


@pytest.mark.asyncio
async def test_reconcile_survives_one_unreadable_sandbox(db):
    """One bad row must not stop the sweep — the next row along may be the
    expensive one."""
    world = _world(db)
    first = await _start(world)
    # A second session needs a free pool, so retire the first machine's
    # binding the way a finished cleanup would.
    machine_id = str(ss.fetch_session(db, first)["machine_id"])
    dbmod.unbind_machine_pool(db, machine_id=machine_id, pool_id=world.pool)
    second = await _start(world)
    ss.transition(db, second, "HIBERNATED", "RESUMING")
    ss.transition(db, second, "RESUMING", "ACTIVE")
    ss.transition(db, second, "ACTIVE", "EVALUATING")
    ss.transition(db, second, "EVALUATING", "SUCCEEDED")
    world.gateway.fail_next("inspect", kind="transport")

    touched = await orch.reconcile(db, world.gateway, world.settings)

    assert second in touched
    assert ss.fetch_session(db, second)["state"] == "TERMINATED"
