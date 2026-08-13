"""Mirroring a finished job's **accepted** artifacts from the coordinator to OSS.

WHAT THIS EXISTS FOR. Today a committed artifact lives in exactly one place:
the coordinator's own filesystem, under ``FLASHML_LOCAL_ARTIFACTS_DIR``. On
prod that is 5 GB of Render disk; on dev it is ``/tmp`` with **no disk
attached at all**, so every redeploy destroys every artifact the product has
ever produced. A model that survives an hour of hibernation and then does not
survive a deploy is not stored, it is cached.

WHY THE MIRROR IS IN THIS REPO AND NOT IN THE COORDINATOR. The obvious move
is to point the coordinator at OSS instead of its disk, and it is not
available. ``flashruntime.artifacts`` really does ship an ``OSSArtifactStore``
and a ``store_from_env()`` — but **the managed job path never calls either.**
Mode A (leases: every job this product runs) hosts artifacts itself, writing
``state.artifacts_dir / key`` directly in ``service/modea.py``'s
``put_artifact``; the only caller of ``store_from_env()`` in the whole runtime
is ``service/app.py``'s ``ingest_workload_artifacts``, which belongs to the
optional KubeRay path and only *lists* an already-populated store to fold
records into the ledger. Setting ``FLASHML_ARTIFACT_BACKEND=oss`` on the
coordinator therefore changes nothing about where a volunteer's upload lands.

Making it change something means editing ``modea.py`` — a file in the PUBLIC
``Zolli-Labs/flashml`` repo, consumed here as the exact pin
``flashruntime==0.6.0``. That is release-to-PyPI plus a synchronised bump
across four pin sites, for a week in which the pin has already moved once.
So the copy happens here, on the accepted-commit boundary this API already
observes, and the public runtime is not touched at all.

THE FOUR RULES THIS MODULE IS BUILT AROUND.

1. **Unconfigured OSS changes nothing.** ``settings.oss_configured`` false and
   this returns ``NOT_CONFIGURED`` having imported no client, opened no
   socket, and read nothing. Same all-or-nothing posture as
   ``github_app_configured``. The coordinator's disk stays the only store and
   every existing path behaves byte for byte as before.

2. **Accepted work only** (repo hard rule 4). Not "everything under the job's
   prefix" — that is what the *storage budget* sums, and over-charging an
   account for a failed attempt's debris is safe in a way that publishing it
   as a result is not. ``classify_key`` decides, and it splits the job's
   prefix in two:

   - ``jobs/{job_id}/{task_id}/…`` is a volunteer's upload. Mirrored only
     when the coordinator reports that task ``COMPLETED``, which in
     ``modea.py`` means one thing exactly: a commit whose ``metrics.json``
     hashed to what the worker claimed and passed the semantic check. Every
     ``PENDING``/``LEASED``/``FAILED``/``CANCELLED`` task's bytes stay put.
   - anything else under the prefix is **control-plane output** and is
     mirrored. That is not a loophole, it is the other half of the rule:
     ``modea._authorize_write`` confines a node to ``jobs/{job}/{task}/``
     for tasks it holds a *live lease* on, so a key whose first segment is
     not one of this job's task ids cannot have come from a volunteer at
     all. It came from an operator credential — the federated driver
     writing ``jobs/{job_id}/round-NNN/weights.json`` (the aggregated model
     itself, which belongs to no task and which no lease could ever scope),
     or the coordinator's own reducer writing ``jobs/{job_id}/reduced/…``
     from accepted task outputs. Excluding those would mirror a federated
     run's shards and drop its model.

3. **Manifest last.** Objects are copied first, each one verified against what
   OSS says it stored, and only then is
   ``jobs/{job_id}/_mirror/manifest.json`` written. The manifest's *presence*
   is the completeness claim — the same parts-first / manifest-last discipline
   ``flashruntime.checkpoint`` uses, and for the same reason: an interrupted
   mirror leaves objects with no manifest, which reads as incomplete, rather
   than a manifest that promises objects that are not there. There is no
   in-between state that looks finished.

4. **Idempotent.** The second call reads the manifest, sees the same accepted
   key set with the same digests, and copies nothing. Not an error, not a
   duplicate — ``ALREADY_MIRRORED`` and one HEAD's worth of work.

WHAT IS *NOT* CLAIMED, said plainly. The coordinator addresses a task's output
by key, not by attempt, so a retried task's winning attempt overwrites the
losing one at every key it writes. Anything the winner did *not* rewrite
(a stray file from a dead attempt, a checkpoint the winner resumed from) is
still under that task's prefix and is mirrored with it. Distinguishing those
would need per-attempt keys, which is a runtime change. What is guaranteed is
narrower and true: **nothing is mirrored from a task that never had a commit
accepted.**

NO CREDENTIAL EVER LEAVES THIS PROCESS. Consumers of a mirrored artifact get
``presign_job_artifacts`` — expiring, single-object GET grants signed here.
The OSS key pair is held by the control plane and by nothing else; a sandbox
running user ML code receives URLs (see ``alibaba_oss``'s module docstring on
why, and on re-minting them after a hibernation).

THE DIAGNOSTICS SIBLING (build #12, owner-approved 2026-08-13; see
``docs/superpowers/specs/2026-08-13-architectural-pieces-design.md`` §4).
Rule 2 above means a FAILED task's stderr — the "why did it fail" bytes —
is never in ``jobs/{job_id}/_mirror/manifest.json``, by design: that manifest
feeds billing and goodput accounting, and must stay provably accepted-only.
But that leaves those bytes reachable only while the coordinator remembers
the job, which is exactly the durability gap this module exists to close for
everything else. ``mirror_diagnostics`` is the resolution: a SEPARATE
function that copies a FAILED task's output under its OWN reserved prefix
(``jobs/{job_id}/_diagnostics/``), certified by its OWN manifest
(``DIAGNOSTICS_MANIFEST_SCHEMA``, carrying an explicit ``"accepted": false``)
— never written to, and never read from, ``_mirror/manifest.json``. Same
discipline throughout: parts first, manifest last, sha256-verified,
idempotent, and silent (no manifest at all, not an empty one) when there is
nothing FAILED to certify. Presenting these to a browser is a separate,
later change; this module only makes the bytes survive.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

from flashml_cloud_api.alibaba_oss import DEFAULT_TTL_S, OSSArtifacts, OSSUnavailable
from flashml_cloud_api.observability import Chain, log_event

if TYPE_CHECKING:  # pragma: no cover - typing only
    from flashml_cloud_api.settings import Settings

log = logging.getLogger(__name__)

#: Manifest format. Version it from the first byte: a consumer reading a
#: manifest it does not understand must be able to say so rather than guess,
#: and the alternative — inferring the shape from which keys happen to be
#: present — is how a format becomes unchangeable.
MANIFEST_SCHEMA = "flashml.artifact-mirror/v1"

#: Reserved directory under a job's prefix, holding the manifest and nothing
#: else. Underscore-prefixed because a task id never is (they are
#: ``trial-000`` / ``shard-000`` / ``it00-shard-000``), and
#: ``_reserved_collision`` refuses to run at all if that ever stops being
#: true — a task able to write ``…/_mirror/manifest.json`` could forge a
#: completeness claim, so the check is fail-closed rather than advisory.
MIRROR_DIR = "_mirror"
MANIFEST_NAME = "manifest.json"

#: Manifest format for the DIAGNOSTICS side — a FAILED task's stderr, logs,
#: whatever debris its attempt left behind. A different schema string from
#: ``MANIFEST_SCHEMA`` on purpose: the two are never supposed to parse as
#: each other, and ``parse_manifest``/``parse_diagnostics_manifest`` each
#: check theirs and reject the other's. See the module docstring's design
#: note (``2026-08-13-architectural-pieces-design.md`` §4) for why this is a
#: SEPARATE, non-accepted record rather than a fourth ``classify_key`` verdict
#: folded into the accepted manifest: the accepted manifest feeds
#: billing/goodput accounting and must stay provably accepted-only.
DIAGNOSTICS_MANIFEST_SCHEMA = "flashml.artifact-diagnostics/v1"

#: Reserved directory under a job's prefix, holding the diagnostics manifest
#: and nothing else. Same shape and same reason as ``MIRROR_DIR`` — a task id
#: never starts with ``_``, and ``reserved_collision``/``classify_key`` treat
#: this one exactly like the accepted mirror's directory: a task that could
#: write here could forge a "this is what failed" record.
DIAGNOSTICS_DIR = "_diagnostics"

#: Every underscore-prefixed directory this module reserves for itself.
#: Checked as a set, not two separate ``==`` comparisons, so adding a third
#: reserved directory later is a one-line change here rather than a hunt
#: through every place ``MIRROR_DIR`` was compared by name.
RESERVED_DIRS = frozenset({MIRROR_DIR, DIAGNOSTICS_DIR})

#: The only task state whose output has been accepted. Repo hard rule 4.
ACCEPTED_TASK_STATE = "COMPLETED"

#: The one task state whose output the diagnostics side mirrors. Deliberately
#: narrower than "anything but ACCEPTED_TASK_STATE": that would sweep in
#: ``PENDING``/``LEASED`` (an attempt that may still be writing — mirroring
#: its partial bytes as "why it failed" would be wrong on its face) and
#: ``CANCELLED`` (an operator or scheduler decision, not a failure anyone is
#: investigating). ``FAILED``, exactly, is the state whose bytes answer "why
#: did this task fail".
FAILED_TASK_STATE = "FAILED"


class MirrorError(RuntimeError):
    """The mirror could not be completed, and nothing may be recorded.

    Raised — never returned, never logged-and-swallowed — because every
    caller's correct response is the same: leave the job unmarked so the next
    observation retries it. A mirror that failed quietly is indistinguishable
    from one that never ran, and the difference between those two is the
    whole point of having a manifest.
    """


# -- results -----------------------------------------------------------------

#: OSS is not configured. The job's artifacts stay on the coordinator's disk
#: and every existing path is untouched. Not a failure.
NOT_CONFIGURED = "not-configured"
#: A current manifest was already there. Nothing was copied.
ALREADY_MIRRORED = "already-mirrored"
#: Objects were copied and the manifest was written.
MIRRORED = "mirrored"
#: ``mirror_diagnostics`` only: no diagnostics manifest exists and none was
#: written, because there was nothing to certify — either no task was
#: FAILED, or a FAILED task left no bytes under its prefix. Absent is the
#: honest record here, not an empty manifest a consumer would have to learn
#: to treat as "nothing failed" rather than "we didn't check".
NO_DIAGNOSTICS = "no-diagnostics"


@dataclass(frozen=True)
class MirroredObject:
    """One object as the manifest records it.

    ``sha256`` is computed over the bytes read from the coordinator, before
    they were sent. It is deliberately not OSS's ETag: that is an MD5 for a
    single-part put and something else entirely for a multipart one, so it
    can verify a transfer but can never be the artifact's identity.
    """

    key: str
    size_bytes: int
    sha256: str

    def as_json(self) -> dict[str, Any]:
        return {"key": self.key, "size_bytes": self.size_bytes, "sha256": self.sha256}


@dataclass(frozen=True)
class MirrorResult:
    """What one ``mirror_job`` call did.

    ``objects`` is populated for ``MIRRORED`` and for ``ALREADY_MIRRORED``
    (read back off the manifest, so a caller gets the same answer either way
    and does not have to care which call did the work). Empty for
    ``NOT_CONFIGURED``.
    """

    job_id: str
    state: str
    objects: tuple[MirroredObject, ...] = ()
    accepted_tasks: tuple[str, ...] = ()
    manifest_key: str | None = None

    @property
    def object_count(self) -> int:
        return len(self.objects)

    @property
    def total_bytes(self) -> int:
        return sum(o.size_bytes for o in self.objects)

    @property
    def mirrored(self) -> bool:
        """Whether the artifacts are in OSS *now* — however they got there."""
        return self.state in (MIRRORED, ALREADY_MIRRORED)


@dataclass(frozen=True)
class DiagnosticsMirrorResult:
    """What one ``mirror_diagnostics`` call did.

    A separate type from ``MirrorResult``, deliberately, so nothing can pass
    a FAILED task's debris to a function expecting accepted output by
    matching shape alone. ``failed_tasks`` must never be read as
    ``accepted_tasks`` — the fields have different names for that reason —
    and ``NO_DIAGNOSTICS`` has no analogue on the accepted side: a job with
    nothing accepted is refused (``mirror_job``'s empty-task-listing guard),
    but a job with nothing FAILED is the ordinary, hoped-for case and not an
    error.
    """

    job_id: str
    state: str
    objects: tuple[MirroredObject, ...] = ()
    failed_tasks: tuple[str, ...] = ()
    manifest_key: str | None = None

    @property
    def object_count(self) -> int:
        return len(self.objects)

    @property
    def total_bytes(self) -> int:
        return sum(o.size_bytes for o in self.objects)

    @property
    def mirrored(self) -> bool:
        """Whether diagnostics are in OSS *now* — however they got there."""
        return self.state in (MIRRORED, ALREADY_MIRRORED)


# -- the coordinator side ----------------------------------------------------


class ArtifactSource(Protocol):
    """Where the bytes come from.

    A protocol rather than a direct dependency on ``app.CoordinatorClient``
    for two reasons, one structural and one practical. Structural: ``app``
    imports this module, so this module importing ``app`` would close a
    cycle. Practical: every selection rule below is then testable with a
    dictionary instead of an HTTP server.
    """

    async def task_states(self, job_id: str) -> dict[str, str]: ...

    async def list_artifacts(self, job_id: str) -> list[dict[str, Any]]: ...

    async def read(self, key: str) -> bytes: ...


class CoordinatorArtifactSource:
    """``ArtifactSource`` over the coordinator's HTTP surface.

    Constructed from anything with ``CoordinatorClient``'s ``forward`` —
    which is what holds the operator credential. Nothing here builds a
    header; that stays in the one place the repo already keeps it.
    """

    def __init__(self, coordinator: Any) -> None:
        self._coordinator = coordinator

    async def _get(self, path: str) -> Any:
        try:
            r = await self._coordinator.forward("GET", path)
        except Exception as exc:  # noqa: BLE001 - normalised, never swallowed
            raise MirrorError(f"coordinator unreachable for {path}") from exc
        if r.status_code >= 300:
            raise MirrorError(f"coordinator answered {r.status_code} for {path}")
        return r

    async def task_states(self, job_id: str) -> dict[str, str]:
        r = await self._get(f"/v1alpha1/jobs/{job_id}/tasks")
        try:
            rows = r.json()
        except ValueError as exc:
            raise MirrorError(f"unreadable task listing for {job_id}") from exc
        if not isinstance(rows, list):
            raise MirrorError(f"unreadable task listing for {job_id}")
        states: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            task_id, state = row.get("task_id"), row.get("state")
            if isinstance(task_id, str) and isinstance(state, str):
                states[task_id] = state
        return states

    async def list_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        r = await self._get(f"/v1alpha1/jobs/{job_id}/artifacts")
        try:
            listing = r.json()
        except ValueError as exc:
            raise MirrorError(f"unreadable artifact listing for {job_id}") from exc
        if not isinstance(listing, list):
            raise MirrorError(f"unreadable artifact listing for {job_id}")
        return [entry for entry in listing if isinstance(entry, dict)]

    async def read(self, key: str) -> bytes:
        r = await self._get(f"/v1alpha1/artifacts/{key}")
        return r.content


# -- selection: what counts as accepted --------------------------------------


def valid_job_id(job_id: str) -> bool:
    """Whether a job id may be interpolated into a key at all.

    Every id this is called with comes from our own ``jobs`` table or from
    the coordinator, so this should never fire. It is checked anyway because
    the value ends up inside an object key, and the cost of being wrong is a
    write outside the job's prefix — the same reason ``modea._safe_key``
    exists on the other side of the wire, and the same reason ``app._seg``
    validates a path segment before forwarding it.
    """
    return bool(job_id) and "/" not in job_id and ".." not in job_id \
        and not job_id.startswith("_") and job_id.strip() == job_id


def job_prefix(job_id: str) -> str:
    """Where the coordinator keeps one job's artifacts.

    Hard-coded to the coordinator's own layout (``modea.py`` builds every
    task's ``output_prefix`` as ``jobs/{job_id}/{task_id}/`` and its
    ``GET /jobs/{id}/artifacts`` lists ``artifacts_dir/jobs/{id}``), and the
    same shape ``alibaba_oss.job_key`` produces. The two key spaces being
    identical is not a coincidence to preserve by accident: it means a
    mirrored key is the coordinator key, so nothing has to translate, and a
    consumer holding an ``artifact://`` URI can find the object.
    """
    return f"jobs/{job_id}/"


def manifest_key(job_id: str) -> str:
    return f"{job_prefix(job_id)}{MIRROR_DIR}/{MANIFEST_NAME}"


def diagnostics_manifest_key(job_id: str) -> str:
    """Where the diagnostics record lives — a sibling of ``manifest_key``,
    never mixed into it. ``jobs/{id}/_diagnostics/manifest.json``."""
    return f"{job_prefix(job_id)}{DIAGNOSTICS_DIR}/{MANIFEST_NAME}"


def accepted_task_ids(task_states: dict[str, str]) -> tuple[str, ...]:
    """The tasks whose commit the coordinator accepted, sorted.

    Exact string match on ``COMPLETED``. Not "not FAILED", which would sweep
    in ``PENDING`` and ``LEASED`` — work that may still be running — and not
    a truthiness test on some field, which would make an unknown future state
    default to accepted. An unrecognised state is not accepted; that is the
    direction that cannot publish somebody's abandoned attempt as a result.
    """
    return tuple(
        sorted(t for t, s in task_states.items() if s == ACCEPTED_TASK_STATE)
    )


def failed_task_ids(task_states: dict[str, str]) -> tuple[str, ...]:
    """The tasks whose attempt the coordinator reports FAILED, sorted.

    Exact string match on ``FAILED_TASK_STATE`` — the diagnostics-side
    sibling of ``accepted_task_ids``, and for the same reason: an unknown
    future state must not silently start being treated as diagnosable, any
    more than it should silently start being treated as accepted.
    """
    return tuple(
        sorted(t for t, s in task_states.items() if s == FAILED_TASK_STATE)
    )


def reserved_collision(task_states: dict[str, str]) -> str | None:
    """A task id that shares a reserved directory's name, or None.

    Never expected: task ids are minted by the coordinator's expanders as
    ``trial-000`` / ``shard-000`` / ``it00-shard-000``. And ``classify_key``
    already refuses to mirror anything under a reserved directory whatever
    wrote it, so this is the second of two guards rather than the only one.
    It is here because the failure it prevents — a task's own upload being
    mistaken for the object that certifies a mirror complete — is not one to
    leave resting on a naming convention in another repo, and because
    refusing the whole job is a legible outcome where silently skipping one
    key is not. Covers ``DIAGNOSTICS_DIR`` too: a task able to write
    ``…/_diagnostics/manifest.json`` could forge a diagnostics record the
    same way one writing ``…/_mirror/manifest.json`` could forge an accepted
    one.

    Checked against EVERY task, not just the accepted or failed ones: a task
    that failed still chose that name, and so did one still pending.
    """
    for name in sorted(RESERVED_DIRS):
        if name in task_states:
            return name
    return None


#: ``classify_key`` verdicts. Only ``ACCEPTED_TASK`` and ``CONTROL_PLANE`` are
#: mirrored; the module docstring's rule 2 argues why those two and no others.
ACCEPTED_TASK = "accepted-task"
UNACCEPTED_TASK = "unaccepted-task"
CONTROL_PLANE = "control-plane"
RESERVED = "reserved"
FOREIGN = "foreign"

MIRRORABLE = frozenset({ACCEPTED_TASK, CONTROL_PLANE})


def classify_key(job_id: str, key: str, task_states: dict[str, str]) -> str:
    """What one coordinator key is, for the purpose of mirroring it.

    Needs the FULL task-state map, not just the accepted ids, and that is the
    whole subtlety of this function. "First segment is not an accepted task"
    is two very different situations wearing one shape: a task that exists
    and did not commit (never mirror — that is somebody's failed attempt),
    and a segment that is no task at all (mirror — nothing but an operator
    credential can write there). With only the accepted set in hand the two
    are indistinguishable and the safe-looking choice, excluding both, drops
    every federated run's model.
    """
    prefix = job_prefix(job_id)
    if not key or not key.startswith(prefix):
        # The coordinator's listing is job-scoped, so this means we and it
        # disagree about which job was asked for. Filing those bytes here
        # would attribute another job's output to this one.
        return FOREIGN
    rest = key[len(prefix):]
    head, _, _tail = rest.partition("/")
    if not head or head in RESERVED_DIRS:
        return RESERVED
    state = task_states.get(head)
    if state is None:
        return CONTROL_PLANE
    return ACCEPTED_TASK if state == ACCEPTED_TASK_STATE else UNACCEPTED_TASK


def task_id_in_key(job_id: str, key: str, task_states: dict[str, str]) -> str | None:
    """The task id a mirrorable key belongs to, or ``None``.

    Exists so a log line about one object can be findable **without logging the
    object**. Everything in a key past ``jobs/{job_id}/{task_id}/`` is a path
    the submitted code chose — a filename, a directory layout, sometimes a
    checkpoint step it named itself — and a log is a publication
    (``PROGRESS.md`` Rule 7). The first segment is different in kind: it is
    only returned here when ``task_states`` (the coordinator's own task view)
    knows it, so what comes back is an id this system assigned and can join on,
    not a string somebody handed us.
    """
    if classify_key(job_id, key, task_states) != ACCEPTED_TASK:
        return None
    head, _, _tail = key[len(job_prefix(job_id)):].partition("/")
    return head or None


def select_accepted_keys(
    job_id: str,
    listing: list[dict[str, Any]],
    task_states: dict[str, str],
) -> list[str]:
    """The keys from ``listing`` that may be mirrored, sorted and de-duplicated.

    Sorted because the manifest is, and de-duplicated because a coordinator
    listing that ever repeated a key would otherwise copy it twice and record
    it twice.
    """
    keys: set[str] = set()
    for entry in listing:
        key = entry.get("key")
        if not isinstance(key, str):
            continue
        if classify_key(job_id, key, task_states) in MIRRORABLE:
            keys.add(key)
    return sorted(keys)


def select_failed_keys(
    job_id: str,
    listing: list[dict[str, Any]],
    task_states: dict[str, str],
) -> list[str]:
    """The keys from ``listing`` that belong to a FAILED task, sorted and
    de-duplicated — the debris ``select_accepted_keys`` deliberately drops.

    Deliberately narrower than "everything ``select_accepted_keys`` excludes":
    that set also holds ``PENDING``/``LEASED`` output (an attempt that may
    still be writing) and control-plane output (belongs to no task, so it
    cannot answer "why did THIS task fail"). Only a key whose task is FAILED,
    exactly, is diagnostics.
    """
    prefix = job_prefix(job_id)
    keys: set[str] = set()
    for entry in listing:
        key = entry.get("key")
        if not isinstance(key, str) or not key.startswith(prefix):
            continue
        head, _, _tail = key[len(prefix):].partition("/")
        if not head or head in RESERVED_DIRS:
            continue
        if task_states.get(head) == FAILED_TASK_STATE:
            keys.add(key)
    return sorted(keys)


# -- the manifest ------------------------------------------------------------


def build_manifest(
    job_id: str,
    objects: list[MirroredObject],
    accepted: tuple[str, ...],
    *,
    now: datetime | None = None,
) -> bytes:
    """The completeness claim, serialised.

    ``complete`` is written ``true`` unconditionally and that is not
    redundant: this function is only ever called after every object has been
    copied and verified, and the manifest is only ever written after this
    function returns. There is no code path that produces a manifest for a
    partial mirror, which is what makes the field's presence meaningful
    rather than decorative.

    Sorted keys and a stable separator so the same inputs serialise to the
    same bytes — a manifest that differed run to run would defeat the
    idempotence check that reads it back.
    """
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    payload = {
        "schema": MANIFEST_SCHEMA,
        "job_id": job_id,
        "source": "coordinator-local-disk",
        "mirrored_at": stamp.isoformat().replace("+00:00", "Z"),
        "complete": True,
        "accepted_tasks": list(accepted),
        "object_count": len(objects),
        "total_bytes": sum(o.size_bytes for o in objects),
        "objects": [o.as_json() for o in sorted(objects, key=lambda o: o.key)],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def build_diagnostics_manifest(
    job_id: str,
    objects: list[MirroredObject],
    failed: tuple[str, ...],
    *,
    now: datetime | None = None,
) -> bytes:
    """The diagnostics record, serialised. ``build_manifest``'s sibling, never
    its substitute.

    ``"accepted": False`` is written unconditionally, the same way
    ``build_manifest`` writes ``"complete": True`` unconditionally — not a
    field a caller could ever set the other way, so its presence is a marker
    a reader can trust rather than a claim someone made about this call.
    Combined with ``DIAGNOSTICS_MANIFEST_SCHEMA`` (never equal to
    ``MANIFEST_SCHEMA``), a consumer has two independent reasons to refuse to
    read this as accepted output — schema mismatch and an explicit
    ``accepted: false`` — rather than one that could be typo'd away.

    Sorted keys and a stable separator, same reason as ``build_manifest``:
    the same inputs must serialise to the same bytes for the idempotence
    check that reads this back to mean anything.
    """
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    payload = {
        "schema": DIAGNOSTICS_MANIFEST_SCHEMA,
        "accepted": False,
        "job_id": job_id,
        "source": "coordinator-local-disk",
        "mirrored_at": stamp.isoformat().replace("+00:00", "Z"),
        "complete": True,
        "failed_tasks": list(failed),
        "object_count": len(objects),
        "total_bytes": sum(o.size_bytes for o in objects),
        "objects": [o.as_json() for o in sorted(objects, key=lambda o: o.key)],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def parse_manifest(data: bytes) -> dict[str, Any] | None:
    """A manifest we are willing to trust, or None.

    None for anything unrecognised — wrong schema, not complete, malformed
    entries — and None is what makes the caller re-mirror. The rule is that
    an unreadable manifest must never be read as "already done": that is the
    one mistake here that loses data silently, because it would leave a job
    permanently believing bytes are in OSS that are not.
    """
    try:
        payload = json.loads(data)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != MANIFEST_SCHEMA:
        return None
    if payload.get("complete") is not True:
        return None
    objects = payload.get("objects")
    if not isinstance(objects, list):
        return None
    for entry in objects:
        if not isinstance(entry, dict):
            return None
        if not isinstance(entry.get("key"), str):
            return None
        if not isinstance(entry.get("sha256"), str):
            return None
        size = entry.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            return None
    return payload


def parse_diagnostics_manifest(data: bytes) -> dict[str, Any] | None:
    """A diagnostics manifest we are willing to trust, or None.

    ``parse_manifest``'s sibling, checking the diagnostics schema and the
    explicit ``accepted: false`` marker instead of ``MANIFEST_SCHEMA`` — so a
    manifest built by ``build_manifest`` fails this (wrong schema, no
    ``accepted`` key) and one built by ``build_diagnostics_manifest`` fails
    ``parse_manifest`` (wrong schema there too). Neither parser can be handed
    the other's manifest and mistake it for its own.
    """
    try:
        payload = json.loads(data)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != DIAGNOSTICS_MANIFEST_SCHEMA:
        return None
    if payload.get("accepted") is not False:
        return None
    if payload.get("complete") is not True:
        return None
    objects = payload.get("objects")
    if not isinstance(objects, list):
        return None
    for entry in objects:
        if not isinstance(entry, dict):
            return None
        if not isinstance(entry.get("key"), str):
            return None
        if not isinstance(entry.get("sha256"), str):
            return None
        size = entry.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            return None
    return payload


def manifest_objects(payload: dict[str, Any]) -> tuple[MirroredObject, ...]:
    return tuple(
        MirroredObject(
            key=e["key"], size_bytes=int(e["size_bytes"]), sha256=e["sha256"]
        )
        for e in sorted(payload["objects"], key=lambda e: e["key"])
    )


def manifest_covers(payload: dict[str, Any], keys: list[str]) -> bool:
    """Whether an existing manifest already describes exactly these keys.

    Set equality, not "is a superset". A manifest listing MORE keys than the
    coordinator now reports as accepted describes a different job state than
    the one in front of us, and re-mirroring is the cheap, correct answer;
    treating it as covered would leave the stale entries certified for ever.
    """
    return sorted({e["key"] for e in payload["objects"]}) == sorted(set(keys))


# -- integrity ---------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def etag_matches(etag: str | None, data: bytes) -> bool | None:
    """Whether OSS stored the bytes we sent, per its own ETag.

    Returns True/False when the ETag is a single-part MD5 (which every
    ``put_object`` produces, and ``alibaba_oss.put_bytes`` only ever does
    single-part puts), and **None** when there is no usable ETag — a
    multipart upload, or a client that did not surface one.

    Three-valued on purpose. Collapsing "no evidence" into False would fail
    a mirror that is fine; collapsing it into True would silently drop the
    only far-side check there is. The caller decides, and it decides
    differently for the two.
    """
    if not isinstance(etag, str):
        return None
    cleaned = etag.strip().strip('"').lower()
    if len(cleaned) != 32 or any(c not in "0123456789abcdef" for c in cleaned):
        return None
    return cleaned == hashlib.md5(data).hexdigest()  # noqa: S324 - transport check


# -- the operation ------------------------------------------------------------


async def mirror_job(
    job_id: str,
    source: ArtifactSource,
    settings: "Settings",
    *,
    oss: OSSArtifacts | None = None,
    now: datetime | None = None,
    correlation_id: str | None = None,
) -> MirrorResult:
    """Copy one finished job's accepted artifacts to OSS. Idempotent.

    **Call this only for a job the coordinator reports terminal.** Nothing
    here can tell whether a task is still writing — ``COMPLETED`` is a
    per-task fact and a job with one completed task and three running ones
    would mirror the one and then write a manifest saying that is all there
    is. The caller owns that check; ``app.is_terminal_state`` is the same
    predicate the footprint hook already gates on.

    **This must never be on the commit path.** A volunteer's commit is
    accepted by the coordinator with no knowledge of this API, which is the
    property that keeps running leases alive when this process dies. Mirroring
    happens after the fact, on an observation, and its failure raises to a
    caller whose job is to log it and try again — never to a caller that
    could turn it into a failed job.

    ``correlation_id`` is carried onto this module's log lines and nothing
    else. Passed in rather than looked up: this function has a coordinator and
    a bucket and deliberately no database, and giving it one so it could read
    ``jobs.correlation_id`` for itself would put a Postgres round trip on a
    path whose whole design is that it is off the commit path. ``None`` means
    the lines carry the job id alone, which is honest and still findable.

    Raises ``MirrorError`` on any failure, having written no manifest.
    """
    chain = Chain(correlation_id=correlation_id, job_id=job_id)
    if not settings.oss_configured:
        # The whole all-or-nothing gate. Note what has NOT happened above
        # this line: no coordinator call, no OSS import, no listing. An
        # unconfigured deployment does not pay for this feature in latency
        # either.
        return MirrorResult(job_id=job_id, state=NOT_CONFIGURED)

    if not valid_job_id(job_id):
        raise MirrorError(f"refusing to mirror under a malformed job id: {job_id!r}")

    if oss is None:
        try:
            oss = OSSArtifacts.from_settings(settings)
        except OSSUnavailable as exc:
            raise MirrorError(f"OSS is configured but unusable: {exc}") from exc

    task_states = await source.task_states(job_id)
    if not task_states:
        # An empty task listing is not "this job produced nothing" — the
        # coordinator answers `[]` for a job it has never heard of just as it
        # does for a job with no tasks. Writing a manifest here would certify
        # "nothing was accepted" on the strength of a question that was never
        # answered, and that claim is permanent. Refusing is retryable.
        raise MirrorError(
            f"coordinator reports no tasks for {job_id}; refusing to certify "
            f"an empty mirror"
        )

    accepted = accepted_task_ids(task_states)
    collision = reserved_collision(task_states)
    if collision is not None:
        raise MirrorError(
            f"job {job_id} has a task named {collision!r}, which would collide "
            f"with the mirror manifest"
        )

    listing = await source.list_artifacts(job_id)
    keys = select_accepted_keys(job_id, listing, task_states)

    mkey = manifest_key(job_id)
    existing = await _read_manifest(oss, mkey, chain=chain)
    if existing is not None and manifest_covers(existing, keys):
        return MirrorResult(
            job_id=job_id,
            state=ALREADY_MIRRORED,
            objects=manifest_objects(existing),
            accepted_tasks=accepted,
            manifest_key=mkey,
        )

    objects: list[MirroredObject] = []
    for key in keys:
        data = await source.read(key)
        digest = _sha256(data)
        try:
            stored = await asyncio.to_thread(oss.put_bytes, key, data)
        except Exception as exc:  # noqa: BLE001 - normalised, never swallowed
            # One error type out of this function, whatever failed inside it.
            # The caller's response is the same for every cause — log, do not
            # mark the job, let the next observation retry — and giving it
            # three exception types to catch is how one of them ends up
            # uncaught in a `finally` that then reports success.
            raise MirrorError(f"could not mirror {key}: {type(exc).__name__}") from exc
        verdict = etag_matches(stored.etag, data)
        if verdict is False:
            raise MirrorError(
                f"OSS stored different bytes than were sent for {key}"
            )
        if verdict is None:
            # Not fatal, but it is the loss of the only independent check on
            # this transfer, and it must not pass unremarked: a run of these
            # means the put path changed shape and `verify_job` is now the
            # only thing standing between a corrupt copy and a manifest.
            # The KEY is not on this line, and that omission is the point.
            # Past `jobs/{job_id}/{task_id}/` a key is a path the submitted
            # code chose, and a log is a publication (`PROGRESS.md` Rule 7).
            # What replaces it is the task id — assigned by the coordinator,
            # already matched against `task_states` — and the size WE measured
            # on the bytes WE sent, so the line is still findable and every
            # value on it is one this codebase assigned.
            log_event(
                log, "mirror.etag_unverifiable", level=logging.WARNING,
                chain=replace(
                    chain, task_id=task_id_in_key(job_id, key, task_states),
                ),
                bytes=len(data),
                reason="no_usable_etag",
            )
        if stored.size_bytes != len(data):
            raise MirrorError(
                f"OSS stored {stored.size_bytes} bytes for {key}, sent {len(data)}"
            )
        objects.append(
            MirroredObject(key=key, size_bytes=len(data), sha256=digest)
        )

    # LAST. Everything above has to have succeeded to get here; anything that
    # did not raised, leaving objects in the bucket with no manifest — which
    # is the readable, retryable failure this ordering buys.
    manifest = build_manifest(job_id, objects, accepted, now=now)
    try:
        await asyncio.to_thread(oss.put_bytes, mkey, manifest)
    except Exception as exc:  # noqa: BLE001 - normalised, never swallowed
        raise MirrorError(f"could not write the mirror manifest for {job_id}") from exc

    log_event(
        log, "mirror.completed", chain=chain,
        objects=len(objects),
        bytes=sum(o.size_bytes for o in objects),
    )
    return MirrorResult(
        job_id=job_id,
        state=MIRRORED,
        objects=tuple(objects),
        accepted_tasks=accepted,
        manifest_key=mkey,
    )


async def mirror_diagnostics(
    job_id: str,
    source: ArtifactSource,
    settings: "Settings",
    *,
    oss: OSSArtifacts | None = None,
    now: datetime | None = None,
    correlation_id: str | None = None,
) -> DiagnosticsMirrorResult:
    """Copy one finished job's FAILED-task artifacts to OSS. Idempotent.

    ``mirror_job``'s sibling for the debris hard rule 4 keeps off the accepted
    record — the "why did it fail" bytes (a shard's ``stderr.txt``, whatever
    else a dying attempt left under its own prefix) that would otherwise live
    only on the coordinator's disk and vanish the moment it forgets the job.
    See the module docstring and
    ``docs/superpowers/specs/2026-08-13-architectural-pieces-design.md`` §4
    for why this is a SEPARATE function writing a SEPARATE, clearly-marked
    record rather than a change to ``mirror_job``'s selection: the accepted
    manifest feeds billing and goodput accounting, and must stay provably
    accepted-only. Nothing in this function ever touches ``manifest_key``,
    ``MANIFEST_SCHEMA``, or the ``_mirror/`` directory — it writes under its
    own reserved prefix (``diagnostics_manifest_key``,
    ``DIAGNOSTICS_MANIFEST_SCHEMA``, ``_diagnostics/``) with its own
    explicit ``"accepted": false`` marker, so nothing downstream can mistake
    this record for accepted output even by accident.

    **Call this only for a job the coordinator reports terminal** — the same
    caller obligation ``mirror_job`` states, and for the same reason: nothing
    here can tell a FAILED task's bytes are final from a task still writing
    a fresh attempt at the same id.

    **No manifest is written when there is nothing to certify** — no task is
    FAILED, or every FAILED task left nothing under its own prefix. Absent is
    the honest answer there: an empty-but-present manifest would read as "we
    checked and nothing failed" when what actually happened is "there was
    nothing to record", and a consumer should not have to learn to treat
    those the same.

    Raises ``MirrorError`` on any failure, having written no manifest — same
    discipline as ``mirror_job``: log it, record nothing, let the next
    observation retry.
    """
    chain = Chain(correlation_id=correlation_id, job_id=job_id)
    if not settings.oss_configured:
        return DiagnosticsMirrorResult(job_id=job_id, state=NOT_CONFIGURED)

    if not valid_job_id(job_id):
        raise MirrorError(f"refusing to mirror under a malformed job id: {job_id!r}")

    if oss is None:
        try:
            oss = OSSArtifacts.from_settings(settings)
        except OSSUnavailable as exc:
            raise MirrorError(f"OSS is configured but unusable: {exc}") from exc

    task_states = await source.task_states(job_id)
    if not task_states:
        # Same refusal as `mirror_job`, and for the same reason: `[]` means
        # either "no tasks" or "never heard of this job", and certifying
        # either verdict off that answer is a permanent claim built on a
        # question that was never actually answered.
        raise MirrorError(
            f"coordinator reports no tasks for {job_id}; refusing to certify "
            f"an empty diagnostics mirror"
        )

    collision = reserved_collision(task_states)
    if collision is not None:
        raise MirrorError(
            f"job {job_id} has a task named {collision!r}, which would collide "
            f"with a reserved mirror directory"
        )

    failed = failed_task_ids(task_states)
    if not failed:
        return DiagnosticsMirrorResult(job_id=job_id, state=NO_DIAGNOSTICS)

    listing = await source.list_artifacts(job_id)
    keys = select_failed_keys(job_id, listing, task_states)

    mkey = diagnostics_manifest_key(job_id)
    existing = await _read_diagnostics_manifest(oss, mkey, chain=chain)
    if existing is not None and manifest_covers(existing, keys):
        return DiagnosticsMirrorResult(
            job_id=job_id,
            state=ALREADY_MIRRORED,
            objects=manifest_objects(existing),
            failed_tasks=failed,
            manifest_key=mkey,
        )

    if not keys:
        # Every FAILED task left nothing under its own prefix — a lease that
        # died before writing a single byte. There is still nothing to
        # certify, so the answer is the same as "no task failed": no
        # manifest, not an empty one.
        return DiagnosticsMirrorResult(
            job_id=job_id, state=NO_DIAGNOSTICS, failed_tasks=failed,
        )

    objects: list[MirroredObject] = []
    for key in keys:
        data = await source.read(key)
        digest = _sha256(data)
        try:
            stored = await asyncio.to_thread(oss.put_bytes, key, data)
        except Exception as exc:  # noqa: BLE001 - normalised, never swallowed
            raise MirrorError(
                f"could not mirror diagnostic {key}: {type(exc).__name__}"
            ) from exc
        verdict = etag_matches(stored.etag, data)
        if verdict is False:
            raise MirrorError(
                f"OSS stored different bytes than were sent for {key}"
            )
        if verdict is None:
            # Same omission as `mirror_job`'s equivalent line, and for the
            # same reason: past `jobs/{job_id}/{task_id}/` a key is a path
            # the submitted code chose, and a log is a publication
            # (`PROGRESS.md` Rule 7). The task id here is one this system
            # assigned and `select_failed_keys` already matched against
            # `task_states`, so the line stays findable without the key.
            task_id = key[len(job_prefix(job_id)):].partition("/")[0]
            log_event(
                log, "mirror.diagnostics.etag_unverifiable", level=logging.WARNING,
                chain=replace(chain, task_id=task_id),
                bytes=len(data),
                reason="no_usable_etag",
            )
        if stored.size_bytes != len(data):
            raise MirrorError(
                f"OSS stored {stored.size_bytes} bytes for {key}, sent {len(data)}"
            )
        objects.append(
            MirroredObject(key=key, size_bytes=len(data), sha256=digest)
        )

    # LAST, same discipline as `mirror_job`: everything above has to have
    # succeeded to get here, and anything that did not raised, leaving
    # objects in the bucket with no diagnostics manifest — readable as
    # incomplete, retried on the next observation.
    manifest = build_diagnostics_manifest(job_id, objects, failed, now=now)
    try:
        await asyncio.to_thread(oss.put_bytes, mkey, manifest)
    except Exception as exc:  # noqa: BLE001 - normalised, never swallowed
        raise MirrorError(
            f"could not write the diagnostics manifest for {job_id}"
        ) from exc

    log_event(
        log, "mirror.diagnostics.completed", chain=chain,
        objects=len(objects),
        bytes=sum(o.size_bytes for o in objects),
    )
    return DiagnosticsMirrorResult(
        job_id=job_id,
        state=MIRRORED,
        objects=tuple(objects),
        failed_tasks=failed,
        manifest_key=mkey,
    )


async def mirror_jobs(
    job_ids: list[str],
    source: ArtifactSource,
    settings: "Settings",
    *,
    oss: OSSArtifacts | None = None,
    correlation_id: str | None = None,
) -> list[MirrorResult]:
    """Mirror several coordinator jobs — a federated run's rounds.

    A federated run is N coordinator jobs under a parent id the coordinator
    has never heard of, and each round's aggregated weights live under that
    round's own job prefix. So a run is mirrored round by round, and this is
    the loop that does it.

    **Stops at the first failure and raises**, having mirrored the rounds
    before it. That is deliberate and it is not a partial-write hazard,
    because each round's manifest is written independently and completely:
    a retry re-reads them, finds them current, and resumes at the round that
    failed. Contrast ``delete_job_artifacts``, which must record nothing on a
    partial failure — deleting has no per-target record to resume from, so it
    has to be wrong in the safe direction instead.

    ``correlation_id`` is the PARENT run's, and it is right that every round
    shares it: the rounds are one piece of work under a parent id the
    coordinator has never heard of, which is precisely the situation a
    correlation id exists to make readable. The per-round coordinator job id is
    on each line as ``job_id``, so the rounds stay distinguishable within the
    thread.
    """
    results: list[MirrorResult] = []
    for job_id in job_ids:
        results.append(await mirror_job(
            job_id, source, settings, oss=oss, correlation_id=correlation_id,
        ))
    return results


async def unmirror_job(
    job_id: str,
    settings: "Settings",
    *,
    oss: OSSArtifacts | None = None,
) -> int:
    """Delete a job's mirror. Returns how many objects went.

    **The release valve's second half.** ``DELETE /jobs/{id}/artifacts``
    frees the coordinator's disk and, without this, would leave the OSS copy
    behind — an account told it had freed its space while its bytes sat in a
    bucket nobody was counting, and an artifact still fetchable by presigned
    URL after its owner deleted it. Deleting the mirror is part of deleting
    the artifacts, not a follow-up chore.

    Returns 0 for an unconfigured deployment, which is the truthful answer:
    there was no mirror. Deleting twice is 0 as well, matching the
    coordinator's own "already gone is not an error".

    Raises ``MirrorError`` if OSS refuses, because the caller's answer
    depends on it — a delete route that reported success here would tell
    someone their data is gone when it is not.
    """
    if not settings.oss_configured:
        return 0
    if not valid_job_id(job_id):
        raise MirrorError(f"refusing to delete under a malformed job id: {job_id!r}")
    if oss is None:
        try:
            oss = OSSArtifacts.from_settings(settings)
        except OSSUnavailable as exc:
            raise MirrorError(f"OSS is configured but unusable: {exc}") from exc
    try:
        # The manifest lives under this prefix too, so it goes with the
        # objects it describes. There is no ordering hazard on the way out:
        # a half-deleted mirror whose manifest survives is still *readable*
        # as a mirror, which is why the retry deletes the prefix again rather
        # than trusting a count.
        return await asyncio.to_thread(oss.delete_prefix, job_prefix(job_id))
    except Exception as exc:  # noqa: BLE001 - normalised, never swallowed
        raise MirrorError(
            f"could not delete the OSS mirror for {job_id}: {type(exc).__name__}"
        ) from exc


async def _read_manifest(
    oss: OSSArtifacts, key: str, *, chain: Chain | None = None,
) -> dict[str, Any] | None:
    """The current manifest, or None if there isn't a trustworthy one.

    ``head`` first, because ``alibaba_oss.head`` is the one call that
    distinguishes absent (None) from broken (raises). Going straight to
    ``get_bytes`` would turn a transport failure into "no manifest" and
    re-copy every object on every poll while OSS was having a bad minute.

    ``chain`` is what the log line below is keyed on. The manifest key is
    ``jobs/{job_id}/_mirror/manifest.json`` — every segment of it ours, so it
    would pass a provenance check — but it is also entirely derivable from the
    job id, so logging it would be one more string on the line and no more
    information on it. Every caller holds the job id; none of them needs to
    hand over a path.
    """
    if await asyncio.to_thread(oss.head, key) is None:
        return None
    data = await asyncio.to_thread(oss.get_bytes, key)
    payload = parse_manifest(data)
    if payload is None:
        log_event(
            log, "mirror.manifest_unreadable", level=logging.WARNING,
            chain=chain, reason="unparseable_manifest", bytes=len(data),
        )
    return payload


async def _read_diagnostics_manifest(
    oss: OSSArtifacts, key: str, *, chain: Chain | None = None,
) -> dict[str, Any] | None:
    """``_read_manifest``'s sibling for the diagnostics side. Same ``head``
    first, same reason: a transport failure must read as "could not check",
    never as "nothing failed" — the second would re-run the diagnostics copy
    on every poll while OSS was having a bad minute, same as the accepted
    side would.
    """
    if await asyncio.to_thread(oss.head, key) is None:
        return None
    data = await asyncio.to_thread(oss.get_bytes, key)
    payload = parse_diagnostics_manifest(data)
    if payload is None:
        log_event(
            log, "mirror.diagnostics.manifest_unreadable", level=logging.WARNING,
            chain=chain, reason="unparseable_manifest", bytes=len(data),
        )
    return payload


# -- reading it back ----------------------------------------------------------


@dataclass(frozen=True)
class VerificationReport:
    """What ``verify_job`` found. Empty ``problems`` means the mirror is good."""

    job_id: str
    checked: int = 0
    problems: tuple[str, ...] = field(default=())

    @property
    def ok(self) -> bool:
        return not self.problems


async def verify_job(job_id: str, oss: OSSArtifacts) -> VerificationReport:
    """Re-download every mirrored object and re-hash it against the manifest.

    The deep check, deliberately separate from ``mirror_job``, which verifies
    a transfer against OSS's own ETag as it goes. This one proves what is in
    the bucket *now* is what the manifest says — a different question, and
    the one an operator or a release rehearsal actually wants answered. It
    costs a full download of the job, so it is never on a request path.

    A missing manifest is a problem, not an exception: "this job was never
    mirrored" is a finding a report should be able to carry.
    """
    payload = await _read_manifest(
        oss, manifest_key(job_id), chain=Chain(job_id=job_id),
    )
    if payload is None:
        return VerificationReport(job_id=job_id, problems=("no readable manifest",))

    problems: list[str] = []
    objects = manifest_objects(payload)
    for obj in objects:
        try:
            data = await asyncio.to_thread(oss.get_bytes, obj.key)
        except OSSUnavailable as exc:
            problems.append(f"{obj.key}: unreadable ({exc})")
            continue
        if len(data) != obj.size_bytes:
            problems.append(
                f"{obj.key}: {len(data)} bytes in OSS, manifest says {obj.size_bytes}"
            )
        if _sha256(data) != obj.sha256:
            problems.append(f"{obj.key}: sha256 mismatch")
    return VerificationReport(
        job_id=job_id, checked=len(objects), problems=tuple(problems)
    )


async def read_manifest_objects(
    job_id: str, oss: OSSArtifacts
) -> tuple[MirroredObject, ...] | None:
    """One job's mirrored inventory as the manifest records it, or None.

    **The listing, without signing anything.** ``presign_job_artifacts``
    below answers the same question and then mints a URL per object, which is
    a signature per file for a caller that only wants to know what exists —
    on a run with a few hundred outputs that is a few hundred signatures
    thrown away. A browser LISTING a job needs exactly this and nothing more;
    it signs one URL later, when somebody clicks one.

    None means *no trustworthy manifest*, which is the same verdict
    ``_read_manifest`` reaches for an absent one and for an unreadable one —
    deliberately not distinguished here, because every caller's answer is the
    same: fall back to whatever the coordinator says. It is NOT an empty
    tuple: an empty tuple would be a claim that the mirror holds nothing,
    and ``mirror_job`` never writes a manifest that could say that.

    Raises ``OSSUnavailable`` when the bucket cannot be read at all — a
    statement about OSS rather than about this job, kept distinguishable for
    the same reason ``presign_mirrored_artifact`` keeps it.
    """
    payload = await _read_manifest(
        oss, manifest_key(job_id), chain=Chain(job_id=job_id),
    )
    if payload is None:
        return None
    return manifest_objects(payload)


async def presign_job_artifacts(
    job_id: str,
    oss: OSSArtifacts,
    *,
    ttl_s: int = DEFAULT_TTL_S,
) -> dict[str, str]:
    """``{key: presigned GET url}`` for one job's mirrored artifacts.

    **This is the only way a workload is given access to a mirrored object.**
    Never a key, never an endpoint plus credentials — a URL that reads one
    object and stops working on its own. Minted from the manifest rather than
    from a bucket listing so nothing outside a certified-complete mirror can
    ever be handed out.

    Mint these at the moment of use. A URL signed before a hibernation is
    expired when the sandbox wakes; ``alibaba_oss.DEFAULT_TTL_S`` is short
    precisely so that carrying one across a lifecycle boundary fails loudly
    instead of intermittently.
    """
    payload = await _read_manifest(
        oss, manifest_key(job_id), chain=Chain(job_id=job_id),
    )
    if payload is None:
        raise MirrorError(f"job {job_id} has no complete mirror to sign for")
    return {
        obj.key: await asyncio.to_thread(oss.sign_get, obj.key, ttl_s=ttl_s)
        for obj in manifest_objects(payload)
    }


async def presign_mirrored_artifact(
    job_id: str,
    key: str,
    oss: OSSArtifacts,
    *,
    ttl_s: int = DEFAULT_TTL_S,
    content_disposition: str | None = None,
) -> str | None:
    """One mirrored object's presigned GET, or None if it is not mirrored.

    ``presign_job_artifacts``' single-key sibling, for the browser's download
    route. A person clicking one file in the console needs one URL, and
    signing every object of the job to throw all but one away is a signature
    per shard per click on a run that may hold thousands of them.

    **None, not an exception, for a key the manifest does not list**, and that
    optional return is the whole point. "Not mirrored" is a FACT about this
    key, not a failure: ``classify_key`` deliberately leaves an unaccepted
    task's output on the coordinator's disk (repo hard rule 4), so a failed
    shard's ``stderr.txt`` is a key that legitimately exists, is legitimately
    readable, and legitimately has no copy in OSS. Raising for it would push
    the caller into treating a normal outcome as an error, and the first time
    someone "fixed" that by catching broadly it would swallow the transport
    failure below with it.

    **From the manifest, never from a bucket listing** — the rule
    ``presign_job_artifacts`` states, for the same reason. An object present
    under the prefix but absent from a complete manifest belongs to an
    interrupted mirror, and signing it hands out bytes nothing has certified.

    Raises ``OSSUnavailable`` when the bucket cannot be read at all. That is a
    statement about OSS and not about this key, and the two must stay
    distinguishable: a caller may well answer both by serving the coordinator's
    copy, but it can only *log* the difference if it is told the difference.

    ``content_disposition`` is passed straight to ``sign_get``, which folds it
    into the signature as ``response-content-disposition``. A browser sent to
    this URL is a NAVIGATION, and a navigation to a text or JSON object with
    no disposition renders it — which means clicking a download link would
    replace the console with a wall of JSON. The anchor's own ``download``
    attribute cannot fix that: OSS is a third origin, so the browser ignores
    it. Naming the file is therefore the header's job or nobody's.
    """
    payload = await _read_manifest(
        oss, manifest_key(job_id), chain=Chain(job_id=job_id),
    )
    if payload is None:
        return None
    if not any(obj.key == key for obj in manifest_objects(payload)):
        return None
    return await asyncio.to_thread(
        oss.sign_get, key, ttl_s=ttl_s, content_disposition=content_disposition
    )
