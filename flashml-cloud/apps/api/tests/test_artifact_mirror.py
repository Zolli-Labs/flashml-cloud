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
    FOREIGN,
    MANIFEST_SCHEMA,
    MIRRORED,
    NOT_CONFIGURED,
    RESERVED,
    UNACCEPTED_TASK,
    MirrorError,
    build_manifest,
    classify_key,
    etag_matches,
    manifest_covers,
    manifest_key,
    mirror_job,
    mirror_jobs,
    parse_manifest,
    presign_job_artifacts,
    reserved_collision,
    select_accepted_keys,
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

    def sign_get(self, key: str, *, ttl_s: int = 900) -> str:
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
