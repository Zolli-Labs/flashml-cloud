"""Mirroring accepted artifacts to OSS.

Four properties carry this module, and each one has a way of failing that
looks fine in production until the day it matters:

- **unconfigured OSS changes nothing** — the deployment default, and the one
  regression that would break every existing install at once;
- **accepted work only** — the difference between publishing a result and
  publishing somebody's abandoned attempt (repo hard rule 4);
- **manifest last** — an interrupted mirror must read as incomplete, because
  the alternative is a job that reports a model nobody can fetch;
- **idempotent** — a job page polls every two seconds, so "runs twice" is the
  normal case, not the edge case.

The fakes below stand in for OSS and the coordinator so all of that is
pinned without a network. One live test at the bottom, marked ``network``
(deselected by default, like the GHCR pull check), proves the same path
against the real bucket.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import pytest

from flashml_cloud_api.alibaba_oss import OSSUnavailable, StoredObject
from flashml_cloud_api.artifact_mirror import (
    ACCEPTED_TASK,
    ALREADY_MIRRORED,
    CONTROL_PLANE,
    DIAGNOSTICS_DIR,
    DIAGNOSTICS_MANIFEST_SCHEMA,
    FOREIGN,
    MANIFEST_SCHEMA,
    MIRROR_DIR,
    MIRRORED,
    NO_DIAGNOSTICS,
    NOT_CONFIGURED,
    RESERVED,
    UNACCEPTED_TASK,
    MirrorError,
    build_diagnostics_manifest,
    build_manifest,
    classify_key,
    diagnostics_manifest_key,
    etag_matches,
    failed_task_ids,
    manifest_covers,
    manifest_key,
    mirror_diagnostics,
    mirror_job,
    mirror_jobs,
    parse_diagnostics_manifest,
    parse_manifest,
    presign_job_artifacts,
    presign_mirrored_artifact,
    reserved_collision,
    select_accepted_keys,
    select_failed_keys,
    unmirror_job,
    valid_job_id,
    verify_job,
)

JOB = "job-abc"


# -- fakes -------------------------------------------------------------------


@dataclass
class FakeSettings:
    oss_configured: bool = True


class FakeOSS:
    """An in-memory bucket with the four methods this module uses.

    ``put_bytes`` returns the real MD5 as its ETag, exactly as OSS does for a
    single-part put, so the transfer check in ``mirror_job`` is exercised for
    real rather than stubbed to pass.
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.fail_on: set[str] = set()
        self.etag_override: str | None = None
        self.dispositions: list[str | None] = []

    def put_bytes(self, key: str, data: bytes) -> StoredObject:
        if key in self.fail_on:
            raise OSSUnavailable(f"refusing {key}")
        self.puts.append(key)
        self.objects[key] = data
        etag = self.etag_override or hashlib.md5(data).hexdigest()  # noqa: S324
        return StoredObject(key=key, size_bytes=len(data), sha256=None,
                            etag=etag, last_modified=None)

    def get_bytes(self, key: str) -> bytes:
        if key not in self.objects:
            raise OSSUnavailable(f"cannot read {key}")
        return self.objects[key]

    def head(self, key: str):
        if key not in self.objects:
            return None
        return StoredObject(key=key, size_bytes=len(self.objects[key]),
                            sha256=None, etag=None, last_modified=None)

    def sign_get(self, key: str, *, ttl_s: int = 900,
                 content_disposition: str | None = None) -> str:
        #: Every disposition this fake was asked to fold into a signature,
        #: including the Nones. The real `sign_url` puts it INSIDE the
        #: signature, so a test cannot check for it by inspecting the returned
        #: string the way it can check for a key — it has to observe the call.
        self.dispositions.append(content_disposition)
        return f"https://example.invalid/{key}?Expires={ttl_s}"


class FakeSource:
    """The coordinator, as a dictionary."""

    def __init__(self, tasks: dict[str, str], blobs: dict[str, bytes]) -> None:
        self.tasks = tasks
        self.blobs = blobs
        self.reads: list[str] = []
        self.raise_on_read: str | None = None

    async def task_states(self, job_id: str) -> dict[str, str]:
        return dict(self.tasks)

    async def list_artifacts(self, job_id: str) -> list[dict]:
        return [{"uri": f"artifact://{k}", "key": k, "size_bytes": len(v)}
                for k, v in sorted(self.blobs.items())]

    async def read(self, key: str) -> bytes:
        if key == self.raise_on_read:
            raise MirrorError(f"coordinator answered 500 for {key}")
        self.reads.append(key)
        return self.blobs[key]


def a_job(**overrides):
    """One completed task, one failed task, and the driver's weights."""
    tasks = {"shard-000": "COMPLETED", "shard-001": "FAILED"}
    blobs = {
        f"jobs/{JOB}/shard-000/metrics.json": b'{"loss": 0.1}',
        f"jobs/{JOB}/shard-000/model.bin": b"\x00\x01weights",
        f"jobs/{JOB}/shard-000/logs/stdout.txt": b"training\n",
        f"jobs/{JOB}/shard-001/logs/stderr.txt": b"traceback\n",
        f"jobs/{JOB}/round-000/weights.json": b'{"w": [1, 2]}',
    }
    tasks.update(overrides.pop("tasks", {}))
    blobs.update(overrides.pop("blobs", {}))
    return FakeSource(tasks, blobs)


ACCEPTED_KEYS = [
    f"jobs/{JOB}/round-000/weights.json",
    f"jobs/{JOB}/shard-000/logs/stdout.txt",
    f"jobs/{JOB}/shard-000/metrics.json",
    f"jobs/{JOB}/shard-000/model.bin",
]


# -- unconfigured changes nothing --------------------------------------------


@pytest.mark.asyncio
async def test_unconfigured_oss_mirrors_nothing_and_asks_nobody():
    source = a_job()
    result = await mirror_job(JOB, source, FakeSettings(oss_configured=False))
    assert result.state == NOT_CONFIGURED
    assert result.objects == ()
    assert not result.mirrored
    # The gate is BEFORE the coordinator, not after it. A deployment with no
    # OSS must not pay a listing round trip on every finished job to learn
    # that it has nothing to do.
    assert source.reads == []


# -- what counts as accepted --------------------------------------------------


def test_a_completed_tasks_output_is_accepted():
    tasks = {"shard-000": "COMPLETED"}
    assert classify_key(JOB, f"jobs/{JOB}/shard-000/model.bin", tasks) == ACCEPTED_TASK


@pytest.mark.parametrize("state", ["FAILED", "PENDING", "LEASED", "CANCELLED"])
def test_no_other_task_state_is_accepted(state):
    tasks = {"shard-000": state}
    assert classify_key(JOB, f"jobs/{JOB}/shard-000/model.bin", tasks) == UNACCEPTED_TASK


def test_an_unknown_task_state_is_not_accepted():
    # Fail closed on a state this code has never heard of: a future runtime
    # adding one must not have its work published by default.
    tasks = {"shard-000": "QUARANTINED"}
    assert classify_key(JOB, f"jobs/{JOB}/shard-000/x", tasks) == UNACCEPTED_TASK


def test_the_drivers_aggregated_weights_are_control_plane_output():
    # The whole reason `classify_key` needs every task state and not just the
    # accepted ones. `round-000` is no task, so nothing but an operator
    # credential could have written it — and for a federated run it is the
    # model. Dropping it would mirror the shards and lose the answer.
    tasks = {"shard-000": "COMPLETED"}
    assert classify_key(
        JOB, f"jobs/{JOB}/round-000/weights.json", tasks
    ) == CONTROL_PLANE


def test_the_reducers_output_is_control_plane_output():
    assert classify_key(JOB, f"jobs/{JOB}/reduced/all.csv", {}) == CONTROL_PLANE


def test_the_mirror_directory_is_never_a_source():
    assert classify_key(JOB, manifest_key(JOB), {}) == RESERVED


def test_another_jobs_key_is_foreign():
    assert classify_key(JOB, "jobs/other/shard-000/x", {}) == FOREIGN


def test_selection_keeps_accepted_and_control_plane_and_drops_the_rest():
    source = a_job()
    listing = [{"key": k, "size_bytes": len(v)} for k, v in source.blobs.items()]
    keys = select_accepted_keys(JOB, listing, source.tasks)
    assert keys == ACCEPTED_KEYS
    assert all("shard-001" not in k for k in keys)


def test_selection_survives_a_listing_entry_with_no_key():
    keys = select_accepted_keys(
        JOB,
        [{"size_bytes": 3}, {"key": None}, {"key": f"jobs/{JOB}/t/x"}],
        {"t": "COMPLETED"},
    )
    assert keys == [f"jobs/{JOB}/t/x"]


def test_a_repeated_listing_entry_is_copied_once():
    key = f"jobs/{JOB}/t/x"
    assert select_accepted_keys(
        JOB, [{"key": key}, {"key": key}], {"t": "COMPLETED"}
    ) == [key]


# -- the copy -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_mirroring_copies_exactly_the_accepted_keys():
    source, oss = a_job(), FakeOSS()
    result = await mirror_job(JOB, source, FakeSettings(), oss=oss)

    assert result.state == MIRRORED
    assert result.mirrored
    assert sorted(o.key for o in result.objects) == ACCEPTED_KEYS
    assert result.accepted_tasks == ("shard-000",)
    # The failed task's log is on the coordinator's disk and stays there.
    assert f"jobs/{JOB}/shard-001/logs/stderr.txt" not in oss.objects


@pytest.mark.asyncio
async def test_the_mirrored_bytes_are_the_source_bytes():
    source, oss = a_job(), FakeOSS()
    await mirror_job(JOB, source, FakeSettings(), oss=oss)
    for key in ACCEPTED_KEYS:
        assert oss.objects[key] == source.blobs[key]


@pytest.mark.asyncio
async def test_the_oss_key_is_the_coordinator_key():
    # Not a cosmetic choice: an `artifact://jobs/…` URI already in an event,
    # a manifest, or a console link resolves in the bucket unchanged.
    source, oss = a_job(), FakeOSS()
    await mirror_job(JOB, source, FakeSettings(), oss=oss)
    assert f"jobs/{JOB}/shard-000/metrics.json" in oss.objects


@pytest.mark.asyncio
async def test_every_object_records_its_sha256():
    source, oss = a_job(), FakeOSS()
    result = await mirror_job(JOB, source, FakeSettings(), oss=oss)
    for obj in result.objects:
        assert obj.sha256 == hashlib.sha256(source.blobs[obj.key]).hexdigest()
        assert obj.size_bytes == len(source.blobs[obj.key])


# -- manifest last ------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_manifest_is_written_after_every_object():
    source, oss = a_job(), FakeOSS()
    await mirror_job(JOB, source, FakeSettings(), oss=oss)
    assert oss.puts[-1] == manifest_key(JOB)
    assert oss.puts[:-1] == ACCEPTED_KEYS


@pytest.mark.asyncio
async def test_the_manifest_describes_what_was_copied():
    source, oss = a_job(), FakeOSS()
    await mirror_job(JOB, source, FakeSettings(), oss=oss)
    payload = json.loads(oss.objects[manifest_key(JOB)])
    assert payload["schema"] == MANIFEST_SCHEMA
    assert payload["complete"] is True
    assert payload["job_id"] == JOB
    assert payload["object_count"] == len(ACCEPTED_KEYS)
    assert [o["key"] for o in payload["objects"]] == ACCEPTED_KEYS
    assert payload["total_bytes"] == sum(
        len(source.blobs[k]) for k in ACCEPTED_KEYS
    )


@pytest.mark.asyncio
async def test_an_interrupted_mirror_leaves_no_manifest():
    """The property the whole ordering exists for.

    A copy that dies halfway leaves objects in the bucket. Without a manifest
    those are inert: nothing lists them, nothing signs them, and
    `verify_job` calls the job unmirrored. The failure mode this rules out is
    the opposite ordering's — a manifest promising a model that is not there.
    """
    source, oss = a_job(), FakeOSS()
    oss.fail_on = {f"jobs/{JOB}/shard-000/model.bin"}
    with pytest.raises(MirrorError):
        await mirror_job(JOB, source, FakeSettings(), oss=oss)
    assert manifest_key(JOB) not in oss.objects
    report = await verify_job(JOB, oss)
    assert not report.ok
    assert report.problems == ("no readable manifest",)


@pytest.mark.asyncio
async def test_a_failed_manifest_write_is_raised_not_swallowed():
    source, oss = a_job(), FakeOSS()
    oss.fail_on = {manifest_key(JOB)}
    with pytest.raises(MirrorError):
        await mirror_job(JOB, source, FakeSettings(), oss=oss)


# -- idempotence --------------------------------------------------------------


@pytest.mark.asyncio
async def test_mirroring_twice_copies_nothing_the_second_time():
    source, oss = a_job(), FakeOSS()
    first = await mirror_job(JOB, source, FakeSettings(), oss=oss)
    oss.puts.clear()
    second = await mirror_job(JOB, source, FakeSettings(), oss=oss)

    assert second.state == ALREADY_MIRRORED
    assert oss.puts == []
    # Same answer either way — a caller must not have to know which call did
    # the work.
    assert second.objects == first.objects
    assert second.total_bytes == first.total_bytes


@pytest.mark.asyncio
async def test_mirroring_twice_reads_nothing_from_the_coordinator_twice():
    source, oss = a_job(), FakeOSS()
    await mirror_job(JOB, source, FakeSettings(), oss=oss)
    source.reads.clear()
    await mirror_job(JOB, source, FakeSettings(), oss=oss)
    assert source.reads == []


@pytest.mark.asyncio
async def test_a_new_accepted_task_re_mirrors():
    source, oss = a_job(), FakeOSS()
    await mirror_job(JOB, source, FakeSettings(), oss=oss)
    # The failed shard was retried somewhere else and committed.
    source.tasks["shard-001"] = "COMPLETED"
    result = await mirror_job(JOB, source, FakeSettings(), oss=oss)
    assert result.state == MIRRORED
    assert f"jobs/{JOB}/shard-001/logs/stderr.txt" in oss.objects


@pytest.mark.asyncio
async def test_an_unreadable_manifest_is_re_mirrored_not_trusted():
    source, oss = a_job(), FakeOSS()
    await mirror_job(JOB, source, FakeSettings(), oss=oss)
    oss.objects[manifest_key(JOB)] = b"{ this is not json"
    result = await mirror_job(JOB, source, FakeSettings(), oss=oss)
    assert result.state == MIRRORED


def test_a_manifest_covering_a_different_key_set_does_not_count():
    payload = json.loads(build_manifest(JOB, [], ("t",)))
    assert manifest_covers(payload, [])
    assert not manifest_covers(payload, [f"jobs/{JOB}/t/x"])


# -- diagnostics: a FAILED task's debris, mirrored SEPARATELY -----------------
#
# Build #12 (owner-approved, 2026-08-13-architectural-pieces-design.md §4a):
# `mirror_job` is accepted-work-only by hard rule 4, so a FAILED task's
# stderr/diagnostics never reach OSS and vanish the moment the coordinator
# forgets the job. `mirror_diagnostics` is the parallel, clearly-labelled
# record that saves them anyway — under its own `_diagnostics/` prefix, with
# its own manifest schema, never touching `_mirror/manifest.json`. The
# accepted manifest feeds billing/goodput accounting, so every test below
# that mirrors both halves re-checks that the accepted one stayed untouched.


FAILED_KEYS = [f"jobs/{JOB}/shard-001/logs/stderr.txt"]


def test_a_failed_tasks_output_is_selected_for_diagnostics():
    source = a_job()
    listing = [{"key": k, "size_bytes": len(v)} for k, v in source.blobs.items()]
    assert select_failed_keys(JOB, listing, source.tasks) == FAILED_KEYS


@pytest.mark.parametrize("state", ["COMPLETED", "PENDING", "LEASED", "CANCELLED"])
def test_no_other_task_state_is_selected_for_diagnostics(state):
    # Deliberately narrower than "not accepted": a PENDING/LEASED task may
    # still be writing, and CANCELLED is an operator decision, not a failure
    # anyone is investigating. Only FAILED, exactly, is diagnosable.
    tasks = {"shard-000": state}
    listing = [{"key": f"jobs/{JOB}/shard-000/x", "size_bytes": 1}]
    assert select_failed_keys(JOB, listing, tasks) == []


def test_control_plane_output_is_never_selected_for_diagnostics():
    # `round-000/weights.json` belongs to no task at all — it cannot be "why
    # did THIS task fail" no matter what any task's state is.
    tasks = {"shard-000": "FAILED"}
    listing = [{"key": f"jobs/{JOB}/round-000/weights.json", "size_bytes": 3}]
    assert select_failed_keys(JOB, listing, tasks) == []


@pytest.mark.asyncio
async def test_diagnostics_mirror_copies_the_failed_tasks_artifacts():
    source, oss = a_job(), FakeOSS()
    result = await mirror_diagnostics(JOB, source, FakeSettings(), oss=oss)

    assert result.state == MIRRORED
    assert result.mirrored
    assert result.failed_tasks == ("shard-001",)
    assert [o.key for o in result.objects] == FAILED_KEYS
    for key in FAILED_KEYS:
        assert oss.objects[key] == source.blobs[key]


@pytest.mark.asyncio
async def test_diagnostics_land_under_their_own_reserved_prefix():
    source, oss = a_job(), FakeOSS()
    await mirror_diagnostics(JOB, source, FakeSettings(), oss=oss)

    dkey = diagnostics_manifest_key(JOB)
    assert dkey == f"jobs/{JOB}/{DIAGNOSTICS_DIR}/manifest.json"
    assert dkey in oss.objects
    # Never under the accepted mirror's own reserved directory.
    assert not dkey.startswith(f"jobs/{JOB}/{MIRROR_DIR}/")


@pytest.mark.asyncio
async def test_the_accepted_manifest_stays_accepted_only_after_diagnostics_mirror():
    """The invariant this whole feature is built around not breaking. Both
    halves run against the same job; the accepted record must come out
    identical to what it would be with no diagnostics mirror at all."""
    source, oss = a_job(), FakeOSS()

    await mirror_job(JOB, source, FakeSettings(), oss=oss)
    await mirror_diagnostics(JOB, source, FakeSettings(), oss=oss)

    accepted = json.loads(oss.objects[manifest_key(JOB)])
    accepted_keys = {o["key"] for o in accepted["objects"]}
    assert accepted_keys == set(ACCEPTED_KEYS)
    assert accepted_keys.isdisjoint(FAILED_KEYS)
    assert all(k not in accepted_keys for k in FAILED_KEYS)
    # And the failed debris really did land somewhere — this is not a test
    # that passes because nothing was mirrored at all.
    assert diagnostics_manifest_key(JOB) in oss.objects


@pytest.mark.asyncio
async def test_diagnostics_mirroring_leaves_the_accepted_manifest_untouched_even_run_first():
    """Order independence: run diagnostics before the accepted mirror and the
    accepted manifest — once it exists — still names only accepted keys."""
    source, oss = a_job(), FakeOSS()

    await mirror_diagnostics(JOB, source, FakeSettings(), oss=oss)
    await mirror_job(JOB, source, FakeSettings(), oss=oss)

    accepted = json.loads(oss.objects[manifest_key(JOB)])
    assert {o["key"] for o in accepted["objects"]} == set(ACCEPTED_KEYS)


@pytest.mark.asyncio
async def test_a_job_with_no_failed_tasks_writes_no_diagnostics_manifest():
    source = FakeSource(
        {"shard-000": "COMPLETED"},
        {f"jobs/{JOB}/shard-000/metrics.json": b"{}"},
    )
    oss = FakeOSS()

    result = await mirror_diagnostics(JOB, source, FakeSettings(), oss=oss)

    assert result.state == NO_DIAGNOSTICS
    assert result.objects == ()
    assert not result.mirrored
    # Absent, not an empty-but-present record.
    assert diagnostics_manifest_key(JOB) not in oss.objects
    assert oss.puts == []


@pytest.mark.asyncio
async def test_a_failed_task_with_no_artifacts_still_writes_no_manifest():
    """The other way a diagnostics mirror can have nothing to certify: the
    task is FAILED, but its lease died before writing a single byte."""
    source = FakeSource({"shard-000": "COMPLETED", "shard-001": "FAILED"}, {
        f"jobs/{JOB}/shard-000/metrics.json": b"{}",
    })
    oss = FakeOSS()

    result = await mirror_diagnostics(JOB, source, FakeSettings(), oss=oss)

    assert result.state == NO_DIAGNOSTICS
    assert result.failed_tasks == ("shard-001",)
    assert diagnostics_manifest_key(JOB) not in oss.objects


def test_the_diagnostics_manifest_carries_its_own_schema_and_a_non_accepted_marker():
    payload = json.loads(build_diagnostics_manifest(JOB, [], ("shard-001",)))
    assert payload["schema"] == DIAGNOSTICS_MANIFEST_SCHEMA
    assert payload["schema"] != MANIFEST_SCHEMA
    assert payload["accepted"] is False
    assert payload["failed_tasks"] == ["shard-001"]


def test_an_accepted_manifest_does_not_parse_as_a_diagnostics_manifest():
    accepted = build_manifest(JOB, [], ("shard-000",))
    assert parse_diagnostics_manifest(accepted) is None


def test_a_diagnostics_manifest_does_not_parse_as_an_accepted_manifest():
    diagnostics = build_diagnostics_manifest(JOB, [], ("shard-001",))
    assert parse_manifest(diagnostics) is None


@pytest.mark.asyncio
async def test_mirroring_diagnostics_twice_copies_nothing_the_second_time():
    source, oss = a_job(), FakeOSS()
    first = await mirror_diagnostics(JOB, source, FakeSettings(), oss=oss)
    oss.puts.clear()
    second = await mirror_diagnostics(JOB, source, FakeSettings(), oss=oss)

    assert second.state == ALREADY_MIRRORED
    assert oss.puts == []
    assert second.objects == first.objects
    assert second.failed_tasks == first.failed_tasks


@pytest.mark.asyncio
async def test_mirroring_diagnostics_twice_reads_nothing_from_the_coordinator_twice():
    source, oss = a_job(), FakeOSS()
    await mirror_diagnostics(JOB, source, FakeSettings(), oss=oss)
    source.reads.clear()
    await mirror_diagnostics(JOB, source, FakeSettings(), oss=oss)
    assert source.reads == []


@pytest.mark.asyncio
async def test_a_newly_failed_task_re_mirrors_its_diagnostics():
    source, oss = a_job(), FakeOSS()
    await mirror_diagnostics(JOB, source, FakeSettings(), oss=oss)
    source.tasks["shard-000"] = "FAILED"
    source.blobs[f"jobs/{JOB}/shard-000/logs/stderr.txt"] = b"second failure\n"

    result = await mirror_diagnostics(JOB, source, FakeSettings(), oss=oss)

    assert result.state == MIRRORED
    assert result.failed_tasks == ("shard-000", "shard-001")
    assert f"jobs/{JOB}/shard-000/logs/stderr.txt" in oss.objects


@pytest.mark.asyncio
async def test_unconfigured_oss_mirrors_no_diagnostics_and_asks_nobody():
    source = a_job()
    result = await mirror_diagnostics(JOB, source, FakeSettings(oss_configured=False))
    assert result.state == NOT_CONFIGURED
    assert result.objects == ()
    assert not result.mirrored
    assert source.reads == []


@pytest.mark.asyncio
async def test_diagnostics_refuse_a_job_the_coordinator_does_not_know():
    source, oss = FakeSource({}, {}), FakeOSS()
    with pytest.raises(MirrorError, match="no tasks"):
        await mirror_diagnostics(JOB, source, FakeSettings(), oss=oss)
    assert oss.objects == {}


def test_reserved_collision_now_also_guards_the_diagnostics_directory():
    assert reserved_collision({DIAGNOSTICS_DIR: "FAILED"}) == DIAGNOSTICS_DIR
    assert reserved_collision({MIRROR_DIR: "COMPLETED"}) == MIRROR_DIR
    assert reserved_collision({"shard-000": "FAILED"}) is None


@pytest.mark.asyncio
async def test_a_task_named_like_the_diagnostics_directory_refuses_the_whole_job():
    source, oss = a_job(tasks={DIAGNOSTICS_DIR: "FAILED"}), FakeOSS()
    with pytest.raises(MirrorError, match="collide"):
        await mirror_diagnostics(JOB, source, FakeSettings(), oss=oss)
    with pytest.raises(MirrorError, match="collide"):
        await mirror_job(JOB, source, FakeSettings(), oss=oss)


def test_a_key_under_the_diagnostics_directory_is_reserved_not_control_plane():
    """The hardening this feature adds to `classify_key`: without it, a key
    under `_diagnostics/` would have no matching task id and would be
    classified CONTROL_PLANE — which is mirrored into the ACCEPTED manifest.
    That would be exactly the leak this feature must not create."""
    assert classify_key(JOB, diagnostics_manifest_key(JOB), {}) == RESERVED


@pytest.mark.asyncio
async def test_a_malformed_job_id_never_reaches_oss_for_diagnostics():
    oss = FakeOSS()
    with pytest.raises(MirrorError, match="malformed"):
        await mirror_diagnostics("../escape", a_job(), FakeSettings(), oss=oss)
    assert oss.objects == {}


@pytest.mark.asyncio
async def test_an_unreadable_diagnostics_manifest_is_re_mirrored_not_trusted():
    source, oss = a_job(), FakeOSS()
    await mirror_diagnostics(JOB, source, FakeSettings(), oss=oss)
    oss.objects[diagnostics_manifest_key(JOB)] = b"{ this is not json"
    result = await mirror_diagnostics(JOB, source, FakeSettings(), oss=oss)
    assert result.state == MIRRORED


@pytest.mark.asyncio
async def test_an_interrupted_diagnostics_mirror_leaves_no_manifest():
    source, oss = a_job(), FakeOSS()
    oss.fail_on = {f"jobs/{JOB}/shard-001/logs/stderr.txt"}
    with pytest.raises(MirrorError):
        await mirror_diagnostics(JOB, source, FakeSettings(), oss=oss)
    assert diagnostics_manifest_key(JOB) not in oss.objects


def test_failed_task_ids_matches_the_exact_failed_state():
    assert failed_task_ids(
        {"a": "FAILED", "b": "COMPLETED", "c": "CANCELLED"}
    ) == ("a",)


# -- integrity ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_oss_storing_different_bytes_fails_the_mirror():
    source, oss = a_job(), FakeOSS()
    oss.etag_override = "0" * 32  # a valid-looking MD5 that is not ours
    with pytest.raises(MirrorError, match="different bytes"):
        await mirror_job(JOB, source, FakeSettings(), oss=oss)
    assert manifest_key(JOB) not in oss.objects


def test_an_absent_or_multipart_etag_is_no_evidence_rather_than_a_failure():
    assert etag_matches(None, b"x") is None
    assert etag_matches('"deadbeef-2"', b"x") is None
    assert etag_matches("not-hex" * 5, b"x") is None


def test_a_single_part_etag_is_checked_both_ways():
    data = b"hello"
    assert etag_matches(hashlib.md5(data).hexdigest().upper(), data) is True
    assert etag_matches('"' + hashlib.md5(data).hexdigest() + '"', data) is True
    assert etag_matches(hashlib.md5(b"other").hexdigest(), data) is False


@pytest.mark.asyncio
async def test_verify_reads_every_object_back_and_re_hashes_it():
    source, oss = a_job(), FakeOSS()
    await mirror_job(JOB, source, FakeSettings(), oss=oss)
    report = await verify_job(JOB, oss)
    assert report.ok
    assert report.checked == len(ACCEPTED_KEYS)


@pytest.mark.asyncio
async def test_verify_catches_an_object_changed_after_the_fact():
    source, oss = a_job(), FakeOSS()
    await mirror_job(JOB, source, FakeSettings(), oss=oss)
    oss.objects[f"jobs/{JOB}/shard-000/model.bin"] = b"\x00\x01tampered!"
    report = await verify_job(JOB, oss)
    assert not report.ok
    assert any("model.bin" in p for p in report.problems)


@pytest.mark.asyncio
async def test_verify_catches_an_object_that_vanished():
    source, oss = a_job(), FakeOSS()
    await mirror_job(JOB, source, FakeSettings(), oss=oss)
    del oss.objects[f"jobs/{JOB}/shard-000/metrics.json"]
    report = await verify_job(JOB, oss)
    assert not report.ok
    assert any("unreadable" in p for p in report.problems)


# -- refusals -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_job_the_coordinator_does_not_know_is_refused():
    """`[]` is the coordinator's answer for a job it has never heard of AND
    for one with no tasks. Certifying an empty mirror off that would be a
    permanent claim built on a question that was never answered."""
    source, oss = FakeSource({}, {}), FakeOSS()
    with pytest.raises(MirrorError, match="no tasks"):
        await mirror_job(JOB, source, FakeSettings(), oss=oss)
    assert oss.objects == {}


@pytest.mark.asyncio
async def test_a_task_named_like_the_mirror_directory_refuses_the_whole_job():
    source, oss = a_job(tasks={"_mirror": "COMPLETED"}), FakeOSS()
    with pytest.raises(MirrorError, match="collide"):
        await mirror_job(JOB, source, FakeSettings(), oss=oss)


def test_reserved_collision_looks_at_every_task_not_just_accepted_ones():
    assert reserved_collision({"_mirror": "FAILED"}) == "_mirror"
    assert reserved_collision({"shard-000": "COMPLETED"}) is None


@pytest.mark.asyncio
async def test_a_coordinator_read_failure_is_raised_and_writes_no_manifest():
    source, oss = a_job(), FakeOSS()
    source.raise_on_read = f"jobs/{JOB}/shard-000/model.bin"
    with pytest.raises(MirrorError):
        await mirror_job(JOB, source, FakeSettings(), oss=oss)
    assert manifest_key(JOB) not in oss.objects


@pytest.mark.parametrize("bad", ["", "a/b", "..", "_x", " j", "j "])
def test_a_job_id_that_could_escape_its_prefix_is_refused(bad):
    assert not valid_job_id(bad)


@pytest.mark.parametrize("good", ["job-abc", "fed-0123456789ab", "a1b2c3"])
def test_ordinary_job_ids_pass(good):
    assert valid_job_id(good)


@pytest.mark.asyncio
async def test_a_malformed_job_id_never_reaches_oss():
    oss = FakeOSS()
    with pytest.raises(MirrorError, match="malformed"):
        await mirror_job("../escape", a_job(), FakeSettings(), oss=oss)
    assert oss.objects == {}


# -- several rounds, and taking it all away ----------------------------------


@pytest.mark.asyncio
async def test_a_federated_runs_rounds_are_mirrored_one_job_at_a_time():
    rounds = ["fedjob-0", "fedjob-1"]
    blobs, tasks = {}, {"shard-000": "COMPLETED"}
    for rid in rounds:
        blobs[f"jobs/{rid}/shard-000/metrics.json"] = b"{}"
        blobs[f"jobs/{rid}/round-000/weights.json"] = f'{{"r":"{rid}"}}'.encode()

    class MultiSource(FakeSource):
        async def list_artifacts(self, job_id):
            return [{"key": k, "size_bytes": len(v)}
                    for k, v in sorted(self.blobs.items())
                    if k.startswith(f"jobs/{job_id}/")]

    oss = FakeOSS()
    results = await mirror_jobs(rounds, MultiSource(tasks, blobs),
                                FakeSettings(), oss=oss)
    assert [r.state for r in results] == [MIRRORED, MIRRORED]
    # Every round's own aggregated weights, under that round's own prefix.
    for rid in rounds:
        assert oss.objects[f"jobs/{rid}/round-000/weights.json"] == \
            f'{{"r":"{rid}"}}'.encode()
        assert manifest_key(rid) in oss.objects


@pytest.mark.asyncio
async def test_deleting_a_job_takes_its_mirror_with_it():
    source, oss = a_job(), FakeOSS()
    await mirror_job(JOB, source, FakeSettings(), oss=oss)

    class DeletableOSS(FakeOSS):
        def delete_prefix(self, prefix):
            keys = [k for k in self.objects if k.startswith(prefix)]
            for k in keys:
                del self.objects[k]
            return len(keys)

    deletable = DeletableOSS()
    deletable.objects = dict(oss.objects)
    gone = await unmirror_job(JOB, FakeSettings(), oss=deletable)
    assert gone == len(ACCEPTED_KEYS) + 1  # the manifest goes too
    assert deletable.objects == {}
    # And the second click is zero, not an error.
    assert await unmirror_job(JOB, FakeSettings(), oss=deletable) == 0


@pytest.mark.asyncio
async def test_unmirroring_an_unconfigured_deployment_is_zero_not_an_error():
    assert await unmirror_job(JOB, FakeSettings(oss_configured=False)) == 0


@pytest.mark.asyncio
async def test_an_oss_refusal_on_delete_is_raised_so_nobody_is_told_it_is_gone():
    class RefusingOSS(FakeOSS):
        def delete_prefix(self, prefix):
            raise OSSUnavailable("no")

    with pytest.raises(MirrorError):
        await unmirror_job(JOB, FakeSettings(), oss=RefusingOSS())


# -- manifest parsing ---------------------------------------------------------


def _manifest(**overrides) -> bytes:
    payload = json.loads(build_manifest(JOB, [], ("t",)))
    payload.update(overrides)
    return json.dumps(payload).encode()


@pytest.mark.parametrize("payload", [
    b"not json at all",
    b"[]",
])
def test_unparseable_manifests_are_rejected(payload):
    assert parse_manifest(payload) is None


def test_a_manifest_from_another_schema_is_rejected():
    assert parse_manifest(_manifest(schema="something/v9")) is None


def test_a_manifest_that_does_not_claim_completeness_is_rejected():
    assert parse_manifest(_manifest(complete=False)) is None
    assert parse_manifest(_manifest(complete="true")) is None


def test_a_manifest_with_a_malformed_entry_is_rejected():
    assert parse_manifest(_manifest(objects=[{"key": "k"}])) is None
    assert parse_manifest(
        _manifest(objects=[{"key": "k", "sha256": "a", "size_bytes": -1}])
    ) is None
    assert parse_manifest(
        _manifest(objects=[{"key": "k", "sha256": "a", "size_bytes": True}])
    ) is None


def test_a_good_manifest_is_accepted():
    payload = parse_manifest(_manifest())
    assert payload is not None and payload["job_id"] == JOB


def test_the_same_inputs_serialise_to_the_same_bytes():
    from datetime import datetime, timezone
    when = datetime(2026, 8, 11, tzinfo=timezone.utc)
    assert build_manifest(JOB, [], ("t",), now=when) == \
        build_manifest(JOB, [], ("t",), now=when)


# -- grants -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_consumers_get_signed_urls_for_the_mirrored_objects():
    source, oss = a_job(), FakeOSS()
    await mirror_job(JOB, source, FakeSettings(), oss=oss)
    urls = await presign_job_artifacts(JOB, oss, ttl_s=60)
    assert sorted(urls) == ACCEPTED_KEYS
    assert all(u.startswith("https://") for u in urls.values())
    # No credential in what a consumer receives — the point of signing.
    assert all("AccessKeySecret" not in u for u in urls.values())


@pytest.mark.asyncio
async def test_nothing_is_signed_for_a_job_with_no_complete_mirror():
    with pytest.raises(MirrorError):
        await presign_job_artifacts(JOB, FakeOSS())


@pytest.mark.asyncio
async def test_a_half_mirrored_job_signs_nothing():
    """The consumer-facing half of manifest-last: objects in the bucket with
    no manifest are not reachable through the only door that hands them out."""
    source, oss = a_job(), FakeOSS()
    oss.fail_on = {f"jobs/{JOB}/shard-000/model.bin"}
    with pytest.raises(MirrorError):
        await mirror_job(JOB, source, FakeSettings(), oss=oss)
    assert oss.objects  # something was copied
    with pytest.raises(MirrorError):
        await presign_job_artifacts(JOB, oss)


@pytest.mark.asyncio
async def test_one_mirrored_object_can_be_signed_on_its_own():
    """The browser download route's door. It signs ONE key because that is
    what a click is: minting every object's signature to use one of them is a
    signature per shard on a run that may hold thousands."""
    source, oss = a_job(), FakeOSS()
    await mirror_job(JOB, source, FakeSettings(), oss=oss)

    url = await presign_mirrored_artifact(
        JOB, f"jobs/{JOB}/shard-000/model.bin", oss, ttl_s=60
    )

    assert url is not None and url.startswith("https://")
    assert f"jobs/{JOB}/shard-000/model.bin" in url
    assert "AccessKeySecret" not in url


@pytest.mark.asyncio
async def test_a_disposition_is_folded_into_the_signature_not_appended_to_it():
    """``response-content-disposition`` reaches ``sign_get`` as an argument,
    which is the only place it can go: OSS signs the query string, so a
    parameter bolted onto the returned URL would invalidate the URL it was
    bolted onto. The browser needs it because a navigation to a mirrored
    ``metrics.json`` with no disposition RENDERS it — replacing the console
    with a wall of JSON instead of saving a file.

    ``None`` stays the default and stays meaningful: the sandbox path reads
    these with an HTTP client and has no use for one.
    """
    source, oss = a_job(), FakeOSS()
    await mirror_job(JOB, source, FakeSettings(), oss=oss)

    await presign_mirrored_artifact(
        JOB, f"jobs/{JOB}/shard-000/model.bin", oss,
        content_disposition='attachment; filename="shard-000__model.bin"',
    )
    assert oss.dispositions == ['attachment; filename="shard-000__model.bin"']

    await presign_job_artifacts(JOB, oss)
    assert oss.dispositions[1:] == [None] * (len(oss.dispositions) - 1)


@pytest.mark.asyncio
async def test_an_unaccepted_tasks_key_signs_to_none_rather_than_raising():
    """The distinction the optional return exists for. A FAILED shard's
    stderr is deliberately never mirrored (hard rule 4) — it is a key that
    legitimately exists, is legitimately readable from the coordinator, and
    legitimately has no OSS copy. That is a fact about the key, not a failure,
    and a caller told it by exception would have to catch its way back to the
    normal path."""
    source, oss = a_job(), FakeOSS()
    await mirror_job(JOB, source, FakeSettings(), oss=oss)

    signed = await presign_mirrored_artifact(
        JOB, f"jobs/{JOB}/shard-001/logs/stderr.txt", oss
    )

    assert signed is None


@pytest.mark.asyncio
async def test_a_job_with_no_manifest_signs_nothing_for_any_key():
    assert await presign_mirrored_artifact(
        JOB, f"jobs/{JOB}/shard-000/model.bin", FakeOSS()
    ) is None


@pytest.mark.asyncio
async def test_an_object_in_the_bucket_but_not_the_manifest_is_not_signed():
    """Set membership against the MANIFEST, never a bucket listing — the same
    rule ``presign_job_artifacts`` states. An object under the prefix that no
    complete manifest names belongs to an interrupted mirror, and signing it
    hands out bytes nothing has certified."""
    source, oss = a_job(), FakeOSS()
    await mirror_job(JOB, source, FakeSettings(), oss=oss)
    stray = f"jobs/{JOB}/shard-001/logs/stderr.txt"
    oss.objects[stray] = b"traceback\n"

    assert await presign_mirrored_artifact(JOB, stray, oss) is None


# -- what the log lines may say ----------------------------------------------
#
# This module's two log sites were `json.dumps({"text": ...})` written by hand.
# They now go through `observability.log_event`, which takes the ids explicitly
# and refuses anything else — and the migration was not cosmetic: one of them
# was logging an OSS **key**, and everything in a key past
# `jobs/{job_id}/{task_id}/` is a path the task's own code chose. A log is a
# publication (`PROGRESS.md` Rule 7), so the key came out and the task id —
# which the coordinator assigned and `select_accepted_keys` has already matched
# against `task_states` — went in.


def _log_lines(caplog):
    import json

    return [
        json.loads(r.getMessage())
        for r in caplog.records
        if r.name == "flashml_cloud_api.artifact_mirror"
        and r.getMessage().startswith("{")
    ]


@pytest.mark.asyncio
async def test_the_completion_line_is_json_carrying_the_job_and_our_own_counts(
    caplog,
):
    import logging

    source, oss = a_job(), FakeOSS()
    with caplog.at_level(logging.INFO, logger="flashml_cloud_api.artifact_mirror"):
        await mirror_job(JOB, source, FakeSettings(), oss=oss,
                         correlation_id="7f1a4a26-0d1f-4a1e-9a8e-1b2c3d4e5f60")

    done = [line for line in _log_lines(caplog) if line["event"] == "mirror.completed"]
    assert done == [{
        "event": "mirror.completed",
        "job_id": JOB,
        "correlation_id": "7f1a4a26-0d1f-4a1e-9a8e-1b2c3d4e5f60",
        "objects": len(ACCEPTED_KEYS),
        "bytes": sum(len(source.blobs[k]) for k in ACCEPTED_KEYS),
    }]


@pytest.mark.asyncio
async def test_an_unverifiable_etag_is_logged_without_the_key_the_task_named(
    caplog,
):
    """The regression this rewrite exists to prevent.

    `.../shard-000/logs/stdout.txt` is a filename the submitted code chose. The
    line that reports an unusable ETag must still be findable — so it carries
    the job id, the task id, and the size WE measured on the bytes WE sent —
    and it must not carry the path.
    """
    import logging

    source, oss = a_job(), FakeOSS()
    oss.etag_override = "not-a-digest"
    with caplog.at_level(
        logging.WARNING, logger="flashml_cloud_api.artifact_mirror"
    ):
        await mirror_job(JOB, source, FakeSettings(), oss=oss)

    lines = [line for line in _log_lines(caplog)
             if line["event"] == "mirror.etag_unverifiable"]
    assert len(lines) == len(ACCEPTED_KEYS)
    for line in lines:
        assert line["job_id"] == JOB
        assert isinstance(line["bytes"], int)
        # Present for a volunteer's upload; ABSENT for control-plane output,
        # which belongs to no task — `jobs/{job}/round-000/weights.json` is the
        # aggregated model and no lease could ever have scoped it. Absent is
        # the honest answer there, and inventing a task id to fill the slot is
        # the same mistake as minting a correlation id on a miss.
        assert line.get("task_id") in (None, "shard-000")
    assert sum("task_id" in line for line in lines) == 3
    # Asserted on the serialized bytes, not on the absence of a `key` field:
    # a refactor that reintroduced the path under `object`, `name` or `text`
    # would pass the field-name version of this check.
    serialized = "".join(r.getMessage() for r in caplog.records)
    for key in ACCEPTED_KEYS:
        assert key not in serialized
    assert "stdout.txt" not in serialized
    assert "logs/" not in serialized


@pytest.mark.asyncio
async def test_no_log_line_in_this_module_is_hand_rolled_json():
    """`log_event` is the only way a line here carries ids, so a second
    `json.dumps({...})` in this module is a line nothing checks."""
    import pathlib

    import flashml_cloud_api.artifact_mirror as mirror

    source = pathlib.Path(mirror.__file__).read_text()
    assert 'log.info(\n        json.dumps' not in source
    assert "json.dumps({\"text\"" not in source
    assert 'json.dumps({"text"' not in source


# -- the real bucket ----------------------------------------------------------


@pytest.mark.network
@pytest.mark.asyncio
async def test_the_whole_path_against_the_real_bucket():
    """Everything above, once, against `zolli-flashml-artifacts`.

    Deselected by default (`-m network`) and skipped when OSS is not
    configured, so this file still runs offline and in a CI job with no
    Alibaba credentials. Kilobytes, and it deletes what it wrote.
    """
    import os
    import uuid

    from flashml_cloud_api.alibaba_oss import OSSArtifacts

    required = ("OSS_BUCKET", "OSS_ENDPOINT", "OSS_ACCESS_KEY_ID",
                "OSS_ACCESS_KEY_SECRET")
    if not all(os.environ.get(name) for name in required):
        pytest.skip("OSS is not configured in this environment")

    oss = OSSArtifacts(
        endpoint=os.environ["OSS_ENDPOINT"],
        bucket=os.environ["OSS_BUCKET"],
        access_key_id=os.environ["OSS_ACCESS_KEY_ID"],
        access_key_secret=os.environ["OSS_ACCESS_KEY_SECRET"],
    )
    job_id = f"mirrortest-{uuid.uuid4().hex[:10]}"
    tasks = {"shard-000": "COMPLETED", "shard-001": "FAILED"}
    blobs = {
        f"jobs/{job_id}/shard-000/metrics.json": b'{"loss": 0.125}',
        f"jobs/{job_id}/shard-000/model.bin": b"\x00\x01" * 512,
        f"jobs/{job_id}/shard-001/logs/stderr.txt": b"this attempt failed\n",
        f"jobs/{job_id}/round-000/weights.json": b'{"w": [1, 2, 3]}',
    }
    source = FakeSource(tasks, blobs)
    try:
        first = await mirror_job(job_id, source, FakeSettings(), oss=oss)
        assert first.state == MIRRORED
        assert f"jobs/{job_id}/shard-001/logs/stderr.txt" not in {
            o.key for o in first.objects
        }

        report = await verify_job(job_id, oss)
        assert report.ok, report.problems

        second = await mirror_job(job_id, source, FakeSettings(), oss=oss)
        assert second.state == ALREADY_MIRRORED

        urls = await presign_job_artifacts(job_id, oss, ttl_s=120)
        import urllib.request
        key = f"jobs/{job_id}/shard-000/metrics.json"
        with urllib.request.urlopen(urls[key], timeout=30) as r:
            assert r.read() == blobs[key]
    finally:
        oss.delete_prefix(f"jobs/{job_id}/")
