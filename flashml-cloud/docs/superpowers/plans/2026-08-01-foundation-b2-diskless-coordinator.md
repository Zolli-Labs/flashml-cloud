# Foundation B2 — A coordinator with no disk

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every remaining piece of coordinator state off the local disk — checkpoint manifests, the job ledger, and artifact bytes — then remove the Render disk and lift `--workers 1`, so a deploy stops dropping in-flight leases.

**Architecture:** Each store gets a protocol with an in-memory or SQLite implementation for the OSS single-machine path and a Postgres or object-storage implementation for the deployed control plane. Artifacts additionally change *shape*: agents transfer bytes directly against short-lived lease-scoped URLs instead of streaming through the API. The disk comes out only when nothing writes to it.

**Tech Stack:** Python 3.10+, pydantic v2, psycopg 3, an S3-compatible object store, pytest.

## Global Constraints

- **Depends on Plan B1.** Do not start until `PostgresLeaseStore` exists and the concurrency contract test is green. B2 reuses B1's connection conventions and schema.
- `flashruntime` is **public** and must stay runnable with no Postgres and no object store. Every in-memory and SQLite implementation is retained, not deprecated.
- Postgres DSNs use the **session pooler on :5432**, never the transaction pooler on :6543.
- Coordinator Postgres objects live in the **`coordinator` schema** and nothing else. It must never read `public` — that schema belongs to the API and holds accounts (spec §4.5).
- `type: pserv`, and the absence of `healthCheckPath`, do **not** change. This plan changes how the coordinator scales, not who can reach it.
- The unscoped operator token is **out of scope** — that is S3.
- Existing suites stay green at every commit; counts logged in `PROGRESS.md`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `flashruntime/checkpoint/catalog.py` | Accept an injected `ManifestStore` instead of an internal dict | Modify |
| `flashruntime/checkpoint/postgres_store.py` | Postgres `ManifestStore` | **Create** |
| `flashruntime/service/ledger.py` | Extract a `LedgerStore` protocol; keep SQLite as one implementation | Modify |
| `flashruntime/service/postgres_ledger.py` | Postgres `LedgerStore` | **Create** |
| `flashruntime/artifacts/store.py` | Add `LocalArtifactStore`; add scoped-URL minting to the protocol | Modify |
| `flashruntime/service/app.py` | Select every store from config; scoped-URL endpoints | Modify |
| `render.yaml` | Remove the disk, lift `--workers 1` | Modify |

---

### Task 1: CheckpointCatalog takes an injected ManifestStore

`flashruntime/checkpoint/store.py` already defines a `ManifestStore` Protocol (`add` / `save` / `get` / `all`) with an `InMemoryManifestStore`. `CheckpointCatalog` ignores it and keeps `self._manifests: dict[str, CheckpointManifest]`. This task connects the two — no new abstraction is needed.

**Files:**
- Modify: `flashruntime/checkpoint/catalog.py`
- Test: `tests/test_checkpoint_catalog_store.py` (create)

**Interfaces:**
- Consumes: `ManifestStore`, `InMemoryManifestStore` from `flashruntime/checkpoint/store.py`.
- Produces: `CheckpointCatalog(on_event=None, store: ManifestStore | None = None)`. Omitting `store` builds an `InMemoryManifestStore`, so every existing call site keeps working unchanged.

- [ ] **Step 1: Write the failing test**

```python
"""The catalog's durability is the store's job, not the catalog's."""
import pytest

from flashruntime.checkpoint.catalog import CheckpointCatalog
from flashruntime.checkpoint.store import InMemoryManifestStore


def test_defaults_to_an_in_memory_store():
    catalog = CheckpointCatalog()
    assert isinstance(catalog._store, InMemoryManifestStore)


def test_uses_the_injected_store(commit_one_manifest):
    """A manifest committed through one catalog is visible to a second
    catalog sharing the store — which is what surviving a restart means."""
    store = InMemoryManifestStore()
    first = CheckpointCatalog(store=store)
    manifest_id = commit_one_manifest(first)

    second = CheckpointCatalog(store=store)
    assert second.latest_valid(job_id="job-1") is not None
    assert store.get(manifest_id) is not None
```

Add a `commit_one_manifest` fixture to `tests/conftest.py` that drives
`register_part` then `commit` with a real part and a valid checksum, returning
the manifest id. Copy the argument shapes from the existing
`tests/test_checkpoint.py` rather than inventing them — the validation path
rejects anything hand-waved.

- [ ] **Step 2: Run it**

Run: `cd flashruntime && .venv/bin/pytest tests/test_checkpoint_catalog_store.py -v`
Expected: FAIL — `CheckpointCatalog.__init__() got an unexpected keyword argument 'store'`.

- [ ] **Step 3: Inject the store**

```python
    def __init__(
        self,
        on_event: EventSink | None = None,
        store: ManifestStore | None = None,
    ) -> None:
        self._on_event = on_event
        self._store = store if store is not None else InMemoryManifestStore()
        # Parts announced but not yet committed. Deliberately NOT persisted:
        # losing this on restart costs an agent one re-registration, and
        # writing pre-commit scratch to Postgres buys no durability the
        # system needs.
        self._registered: dict[tuple[str, str, int], dict[str, tuple[str, int]]] = {}
```

Replace every `self._manifests[...]` access with the store:

| Was | Becomes |
|---|---|
| `self._manifests[manifest.manifest_id] = manifest` (in `commit`) | `self._store.add(manifest)` |
| `self._manifests.get(manifest_id)` (in `_require`) | `self._store.get(manifest_id)` |
| `for m in self._manifests.values()` (in `latest_valid`) | `for m in self._store.all()` |

`mark_restore_verified` and `quarantine` mutate a manifest they fetched via
`_require` — each must now call `self._store.save(manifest)` afterwards, or the
mutation is lost on any store that is not holding live references. **This is
the bug this task exists to prevent**: it is invisible with `InMemoryManifestStore`
and fatal with Postgres.

- [ ] **Step 4: Run the tests**

Run: `cd flashruntime && .venv/bin/pytest tests/test_checkpoint_catalog_store.py tests/test_checkpoint.py tests/test_checkpoint_local.py -v && .venv/bin/pytest -q`
Expected: new tests PASS, existing checkpoint tests PASS unchanged, full suite PASS.

- [ ] **Step 5: Commit**

```bash
git add flashruntime/checkpoint/catalog.py flashruntime/tests/
git commit -m "refactor(checkpoint): catalog delegates to the ManifestStore protocol

The protocol and an in-memory implementation already existed; the catalog kept
its own dict and ignored them. Mutating paths (mark_restore_verified,
quarantine) now save explicitly — a no-op under live references, load-bearing
under any durable store."
```

---

### Task 2: PostgresManifestStore

**Files:**
- Create: `flashruntime/checkpoint/postgres_store.py`
- Test: `tests/integration/test_postgres_manifest_store.py` (create)

**Interfaces:**
- Consumes: `ManifestStore` protocol; B1's Postgres conventions.
- Produces: `PostgresManifestStore(dsn: str, schema: str = "coordinator")` with a `reset()` for tests, mirroring `PostgresLeaseStore`.

- [ ] **Step 1: Write the failing test**

Mirror `tests/integration/test_postgres_lease_store.py` from B1: `pytestmark = pytest.mark.integration`, skip unless `FLASHML_TEST_POSTGRES_DSN` is set, and cover — a committed manifest is visible from a second store instance; `save()` persists a mutation such as quarantine; `all(scope=...)` filters; `get()` on an unknown id returns `None`.

Add one test that is specific to this store and is the actual regression being fixed:

```python
import os

import pytest

from flashruntime.checkpoint.catalog import CheckpointCatalog

DSN = os.environ.get("FLASHML_TEST_POSTGRES_DSN")


def new_store():
    """A FRESH store object on the same database — this is what 'the
    coordinator restarted' means from the data's point of view."""
    if not DSN:
        pytest.skip("FLASHML_TEST_POSTGRES_DSN not set")
    from flashruntime.checkpoint.postgres_store import PostgresManifestStore

    return PostgresManifestStore(DSN, schema="flashml_test")


@pytest.fixture
def store():
    s = new_store()
    s.reset()
    return s


def test_a_restart_does_not_orphan_a_committed_manifest(store, commit_one_manifest):
    """HANDOFF risk #3: the checkpoint FILES survived a restart, the index
    did not, so perfectly good work was unreachable."""
    before = CheckpointCatalog(store=store)
    commit_one_manifest(before)

    after = CheckpointCatalog(store=new_store())   # fresh store, same database
    assert after.latest_valid(job_id="job-1") is not None
```

- [ ] **Step 2: Run to verify it fails**

```bash
cd flashruntime
docker run --rm -e POSTGRES_PASSWORD=pw -p 5433:5432 -d --name flashml-pg-test postgres:16
FLASHML_TEST_POSTGRES_DSN=postgresql://postgres:pw@localhost:5433/postgres \
  .venv/bin/pytest tests/integration/test_postgres_manifest_store.py -v -m integration
```
Expected: FAIL — module not found.

- [ ] **Step 3: Implement it**

One table in the `coordinator` schema. A manifest is a pydantic model with a
stable `manifest_id`, so store it whole as `JSONB` with the query fields lifted
into columns — there is no need to normalise `parts` into rows, and doing so
would couple the schema to a protocol version that is allowed to evolve:

```sql
CREATE TABLE IF NOT EXISTS coordinator.checkpoint_manifests (
    manifest_id TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL,
    attempt_id  TEXT NOT NULL,
    step        INTEGER NOT NULL,
    manifest    JSONB NOT NULL,
    created     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_manifests_job_step
    ON coordinator.checkpoint_manifests (job_id, step DESC);
```

`add` inserts; `save` is an unconditional `UPDATE ... SET manifest = %s` — unlike
leases, manifests are not contended (one attempt owns one manifest) so they need
no compare-and-swap. `get` and `all` deserialise with
`CheckpointManifest.model_validate`.

The `idx_manifests_job_step` index exists because `latest_valid(job_id=...)` is
on the recovery path — it runs when a machine has just died and someone is
waiting.

- [ ] **Step 4: Run the tests**

Run the integration command from Step 2.
Expected: all PASS. Then `.venv/bin/pytest -q` and `docker rm -f flashml-pg-test`.

- [ ] **Step 5: Commit**

```bash
git add flashruntime/checkpoint/postgres_store.py flashruntime/tests/integration/
git commit -m "feat(checkpoint): PostgresManifestStore, retiring HANDOFF risk #3

A coordinator restart no longer orphans committed checkpoint files. Manifests
store whole as JSONB with query fields lifted out, so the schema does not
couple to a protocol version that is allowed to evolve."
```

---

### Task 3: The ledger behind a protocol

`flashruntime/service/ledger.py` is a concrete class — "POC-scale persistence:
one file, synchronous sqlite3 behind a lock". It holds jobs and the event
stream, and it is the third thing on the disk.

**Files:**
- Modify: `flashruntime/service/ledger.py`
- Create: `flashruntime/service/postgres_ledger.py`
- Test: `tests/test_ledger_contract.py`, `tests/integration/test_postgres_ledger.py`

**Interfaces:**
- Produces: `LedgerStore` Protocol — `upsert_job(job: JobRecord)`, `get_job(job_id) -> JobRecord | None`, `list_jobs() -> list[JobRecord]`, `append_event(event: Event)`, `events_for(job_id) -> list[Event]`. `Ledger` (SQLite) and `PostgresLedger` both satisfy it.

- [ ] **Step 1: Write the shared contract test**

Create `tests/test_ledger_contract.py`. Write these against the **existing**
`Ledger` first — they should pass immediately. That is the point: they pin
current behaviour before a second implementation exists to drift from it.

```python
"""Contract every LedgerStore implementation must satisfy.

Ordering is part of the contract, not an accident of the backend: SQLite
returns insertion order for a bare SELECT and Postgres does not.
"""
import pytest

from flashruntime.service.ledger import Ledger


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger.db")


def test_job_round_trips(ledger, make_job):
    job = make_job("job-1")
    ledger.upsert_job(job)
    assert ledger.get_job("job-1") == job


def test_get_job_returns_none_when_unknown(ledger):
    assert ledger.get_job("job-nope") is None


def test_upsert_updates_rather_than_duplicating(ledger, make_job):
    ledger.upsert_job(make_job("job-1", status="running"))
    ledger.upsert_job(make_job("job-1", status="completed"))
    assert len(ledger.list_jobs()) == 1
    assert ledger.get_job("job-1").status == "completed"


def test_list_jobs_returns_insertion_order(ledger, make_job):
    for jid in ("job-a", "job-b", "job-c"):
        ledger.upsert_job(make_job(jid))
    assert [j.job_id for j in ledger.list_jobs()] == ["job-a", "job-b", "job-c"]


def test_events_return_in_append_order(ledger, make_event):
    for i in range(5):
        ledger.append_event(make_event("job-1", detail=f"e{i}"))
    assert [e.detail for e in ledger.events_for("job-1")] == [
        "e0", "e1", "e2", "e3", "e4"
    ]


def test_events_for_unknown_job_is_empty(ledger):
    assert ledger.events_for("job-nope") == []
```

`make_job` and `make_event` are fixtures in `tests/conftest.py` returning a
`JobRecord` and a protocol `Event`. Copy their required fields from the
existing ledger tests rather than guessing — both are pydantic models and will
reject an incomplete construction.

- [ ] **Step 2: Run — expect PASS**

Run: `cd flashruntime && .venv/bin/pytest tests/test_ledger_contract.py -v`
Expected: PASS. A failure here means the contract as written does not describe
the current `Ledger` — fix the test, since `Ledger` is the incumbent.

- [ ] **Step 3: Declare the protocol**

Add `LedgerStore(Protocol)` to `ledger.py` above the existing class, with the
five methods and a docstring stating that ordering is part of the contract
(`list_jobs` and `events_for` are both order-sensitive, and Postgres will not
preserve insertion order without an explicit `ORDER BY`).

Leave `Ledger` otherwise untouched.

- [ ] **Step 4: Implement PostgresLedger**

Create `flashruntime/service/postgres_ledger.py` with two tables in the
`coordinator` schema:

```sql
CREATE TABLE IF NOT EXISTS coordinator.jobs (
    job_id TEXT PRIMARY KEY,
    job    JSONB NOT NULL,
    seq    BIGSERIAL
);
CREATE TABLE IF NOT EXISTS coordinator.events (
    id     BIGSERIAL PRIMARY KEY,
    job_id TEXT NOT NULL,
    event  JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_job ON coordinator.events (job_id, id);
```

`list_jobs` orders by `seq`, `events_for` by `id`. Both explicitly — the
contract test from Step 1 is what catches it if they are not.

Extend the contract-test fixture to cover `PostgresLedger` in
`tests/integration/test_postgres_ledger.py`, importing the same test bodies
rather than restating them.

- [ ] **Step 5: Run both**

Run the unit contract suite and the integration suite against a real Postgres.
Expected: identical assertions pass for both implementations.

- [ ] **Step 6: Commit**

```bash
git add flashruntime/service/ledger.py flashruntime/service/postgres_ledger.py flashruntime/tests/
git commit -m "feat(service): LedgerStore protocol with SQLite and Postgres implementations

Ordering is part of the contract and is now tested — Postgres does not preserve
insertion order without an explicit ORDER BY, and the SQLite incumbent quietly did."
```

---

### Task 4: LocalArtifactStore and scoped-URL minting

`ArtifactStore` (Protocol), `S3CompatibleArtifactStore`, and `OSSArtifactStore`
already exist and are tested against MinIO. The service ignores them and writes
to a raw `FLASHML_LOCAL_ARTIFACTS_DIR`. This task closes that gap and adds the
shape change from spec §4.4.

**Files:**
- Modify: `flashruntime/artifacts/store.py`, `flashruntime/artifacts/__init__.py`
- Test: `tests/test_local_artifact_store.py`, `tests/integration/test_minio_artifact_store.py` (extend)

**Interfaces:**
- Produces: `LocalArtifactStore(root: Path)` satisfying `ArtifactStore`; and on the protocol, `presign_put(object_key: str, expires_s: int) -> str` and `presign_get(object_key: str, expires_s: int) -> str`.

- [ ] **Step 1: Write the failing test for LocalArtifactStore**

```python
"""LocalArtifactStore — the ArtifactStore the OSS and e2e paths use."""
import pytest

from flashruntime.artifacts.store import LocalArtifactStore


@pytest.fixture
def store(tmp_path):
    return LocalArtifactStore(tmp_path / "artifacts")


@pytest.mark.asyncio
async def test_put_then_get_round_trips_bytes(store, tmp_path):
    src = tmp_path / "model.bin"
    src.write_bytes(b"\x00\x01weights\xff")
    await store.put_file(src, "job-1/model.bin")

    dest = tmp_path / "pulled.bin"
    await store.get_file("job-1/model.bin", dest)
    assert dest.read_bytes() == src.read_bytes()


@pytest.mark.asyncio
async def test_exists_is_truthful(store, tmp_path):
    assert await store.exists("job-1/model.bin") is False
    src = tmp_path / "m.bin"
    src.write_bytes(b"x")
    await store.put_file(src, "job-1/model.bin")
    assert await store.exists("job-1/model.bin") is True


@pytest.mark.asyncio
async def test_list_prefix_returns_only_matching_keys(store, tmp_path):
    src = tmp_path / "f"
    src.write_bytes(b"x")
    await store.put_file(src, "job-1/a.bin")
    await store.put_file(src, "job-1/b.bin")
    await store.put_file(src, "job-2/c.bin")

    keys = sorted(r.object_key for r in await store.list_prefix("job-1/"))
    assert keys == ["job-1/a.bin", "job-1/b.bin"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "evil",
    ["../escaped.bin", "job-1/../../escaped.bin", "/etc/passwd"],
)
async def test_keys_that_escape_the_root_are_rejected(store, tmp_path, evil):
    """Object keys arrive from agents. A key that resolves outside the root
    must be refused, never normalised into something that works."""
    src = tmp_path / "f"
    src.write_bytes(b"x")
    with pytest.raises(ValueError):
        await store.put_file(src, evil)


def test_presign_is_refused_rather_than_faked(store):
    with pytest.raises(NotImplementedError):
        store.presign_put("job-1/model.bin", expires_s=300)
```

`pytest-asyncio>=0.23` is already in `flashruntime`'s `dev` extra
(`pyproject.toml:69`), so `@pytest.mark.asyncio` needs no new dependency.
Verified 2026-08-01.

Note that `ArtifactRecord.backend` is `Literal["minio", "oss", "local"]` —
**`"local"` is already a permitted value** in the protocol
(`protocol/v1alpha1.py:233`). `LocalArtifactStore` was anticipated by the wire
format; this task is filling in an implementation the schema already allows,
not widening the protocol. Do not add a new backend literal.

- [ ] **Step 2: Run to verify it fails**

Run: `cd flashruntime && .venv/bin/pytest tests/test_local_artifact_store.py -v`
Expected: FAIL — `ImportError: cannot import name 'LocalArtifactStore'`.

- [ ] **Step 3: Implement LocalArtifactStore**

An `ArtifactStore` over a directory, `backend = "local"`. Reuse the module's
existing `_sha256` for `ArtifactRecord` checksums so local and S3 records are
comparable. Reject any `object_key` that escapes the root after
normalisation — resolve and confirm the result is still under the root.

- [ ] **Step 4: Add presigning to the protocol**

```python
    def presign_put(self, object_key: str, expires_s: int) -> str:
        """A URL that permits exactly one PUT of exactly this key, expiring in
        `expires_s`. This is what replaces the unauthenticated artifact PUT on
        the coordinator (HANDOFF risk #2): the credential is scoped to one key
        and one verb, so there is no open endpoint left to reach."""
        ...

    def presign_get(self, object_key: str, expires_s: int) -> str: ...
```

`S3CompatibleArtifactStore` implements both with the minio client's presigned
URL support. `LocalArtifactStore` **raises `NotImplementedError`** with a
message saying the local backend has no URL surface and is for single-machine
and test use — do not fake it by returning a `file://` path, which would appear
to work and then fail on a real agent.

- [ ] **Step 5: Run**

Run the unit tests, then the MinIO integration test extended to cover
round-tripping a file through a presigned PUT and GET.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add flashruntime/artifacts/ flashruntime/tests/
git commit -m "feat(artifacts): LocalArtifactStore and lease-scoped presigned URLs

Agents will transfer bytes directly against short-lived per-key URLs instead of
streaming through the API. The local backend refuses to presign rather than
faking a file:// URL that would fail on a real agent."
```

---

### Task 5: Wire the service to every store

**Files:**
- Modify: `flashruntime/service/app.py`
- Test: `tests/test_service_store_selection.py` (extend B1's file)

**Interfaces:**
- Consumes: Tasks 1–4, and B1's `build_lease_store`.
- Produces: `build_manifest_store`, `build_ledger_store`, `build_artifact_store`, following B1's pattern — default to the local/SQLite implementation, select Postgres or S3 from config, refuse an unrecognised scheme loudly.

New environment variables:

| Variable | Unset behaviour | Set behaviour |
|---|---|---|
| `FLASHML_LEASE_STORE_URL` *(B1)* | SQLite on `ledger_path`'s directory | `PostgresLeaseStore` |
| `FLASHML_COORDINATOR_DB_URL` | in-memory manifests, SQLite ledger | Postgres manifest store + ledger |
| `FLASHML_ARTIFACT_STORE_URL` | `LocalArtifactStore` on `FLASHML_LOCAL_ARTIFACTS_DIR` | `S3CompatibleArtifactStore` |

- [ ] **Step 1: Write the failing tests**

Extend B1's selection tests with the same three shapes per builder: default,
configured, and unrecognised-scheme-raises. Follow B1's monkeypatch pattern —
patch the class at its module path, since each builder imports lazily inside
the function.

- [ ] **Step 2: Run to verify it fails, then implement**

Each builder mirrors `build_lease_store`: a lazy import inside the branch so a
deployment without Postgres or MinIO never imports them, and a `ValueError`
naming the offending variable for anything unrecognised.

- [ ] **Step 3: Replace the raw artifact directory writes**

Find every place `settings.local_artifacts_dir` is used to read or write bytes
and route it through the artifact store. Add the two endpoints that issue
scoped URLs, each authorising against the caller's **live lease** — the same
confinement the current commit path enforces. An agent with no live lease for
the job gets a refusal, not a URL.

- [ ] **Step 4: Run everything**

Run: `cd flashruntime && .venv/bin/pytest -q`, plus the integration suites
against Postgres and MinIO.
Expected: PASS. Then run `make e2e` from the workspace root — it exercises the
real process boundaries and is what catches a store wired up backwards.

- [ ] **Step 5: Commit**

```bash
git add flashruntime/service/ flashruntime/tests/
git commit -m "feat(service): select manifest, ledger, and artifact stores from config

Defaults are unchanged, so the OSS single-machine path and e2e keep working
with no Postgres and no object store. Scoped-URL endpoints authorise against
the caller's live lease, matching the existing commit-path confinement."
```

---

### Task 6: Remove the disk

**🔒 HUMAN GATE — this task changes production and one step is irreversible.**

**Files:** `render.yaml`

- [ ] **Step 1: Provision Postgres and the bucket**

Create the `coordinator` schema and a role owning only it — the coordinator must
not be able to read `public`, which holds accounts (spec §4.5). Create the
artifact bucket. Use the **session pooler on :5432**.

- [ ] **Step 2: Deploy with both backends configured, disk still attached**

Set `FLASHML_LEASE_STORE_URL`, `FLASHML_COORDINATOR_DB_URL`, and
`FLASHML_ARTIFACT_STORE_URL`. **Leave the disk mounted and `--workers 1` in
place.** Deploy and confirm: jobs submit, machines claim, checkpoints commit
and restore, artifacts round-trip. Nothing should be written to `/data` any
more, which the next step verifies.

- [ ] **Step 3: Prove the disk is idle before deleting it**

```bash
# via the Render shell on flashml-coordinator
find /data -newermt '-1 hour' -type f
```
Expected: **empty**. Any file here means something still writes to the disk;
find it and route it through a store before continuing.

Also confirm the artifacts that were already on the disk have been migrated —
list the bucket and compare counts against `/data/artifacts`. **Deleting a
Render disk is irreversible and takes the committed artifacts with it.**

- [ ] **Step 4: 🔒 HUMAN GATE — remove the disk and lift the worker cap**

In `render.yaml`, delete the `disk:` block from `flashml-coordinator`, drop
`FLASHML_LEDGER_PATH` and `FLASHML_LOCAL_ARTIFACTS_DIR`, and change the start
command to `--workers 2`.

Rewrite the comment above `--workers 1` rather than deleting it. It currently
cites HANDOFF risk #5 — that single-writer assumption is now retired by B1's
compare-and-swap, and the comment should say so, with a pointer to the
concurrency test. A future reader who finds the constraint gone and no
explanation will reasonably assume it was an accident.

Leave `type: pserv` and the absent `healthCheckPath` exactly as they are.

- [ ] **Step 5: Verify the thing this plan was for**

Deploy again and watch a machine that is **mid-task** across the deploy. Its
lease must survive: the agent keeps heartbeating, the task completes, and no
`LEASE_EXPIRED` event appears for it.

That observation is the definition of done. A green suite is not.

- [ ] **Step 6: Log it**

Record in `PROGRESS.md` with real evidence — the deploy timestamp, the job that
spanned it, and the event stream showing no expiry. Update the stage checklist
in the same edit.

---

## Definition of done

1. `CheckpointCatalog` takes an injected `ManifestStore`; a restart no longer orphans committed manifests.
2. The ledger has a protocol with SQLite and Postgres implementations, and ordering is tested for both.
3. `LocalArtifactStore` exists; the service reads and writes bytes only through `ArtifactStore`.
4. Agents transfer artifacts against lease-scoped presigned URLs; the unauthenticated coordinator PUT is gone.
5. Every store is selected from configuration, and an unrecognised scheme fails at boot rather than silently.
6. Defaults unchanged: `flashruntime` still runs with no Postgres and no object store, and e2e still passes on them.
7. `/data` receives no writes; the Render disk is removed.
8. The coordinator runs more than one worker.
9. **A deploy completes without expiring an in-flight lease — observed on a real job, not asserted.**

## Risks

1. **Deleting the Render disk is irreversible** and takes committed artifacts with it. Task 6 Steps 2–3 exist entirely to make that safe; do not compress them.
2. **Artifact migration is the fiddly part.** Existing objects under `/data/artifacts` must be copied to the bucket *and* remain readable by whatever references them. If manifests store an `artifact://` prefix, the prefix has to resolve the same way in both backends — check `CheckpointManifest.storage_prefix` before assuming.
3. **`--workers 2` is a real behaviour change.** Anything that was accidentally safe on one event loop becomes racy. B1's contract covers the lease store; the ledger and manifest stores are newly concurrent and their contract tests should be re-run under concurrency before Step 4.
4. **Presigned URLs need clock agreement.** Large skew between coordinator and agent makes short-lived URLs fail intermittently and look like network flakiness. Pick an expiry generous enough to absorb it — minutes, not seconds.
5. **Supabase Storage may not support signed uploads the way S3 does** (spec §4.4). Verify before choosing it over R2; the `ArtifactStore` abstraction makes the swap cheap, but only if the chosen backend can presign at all.

## Not in this plan

- The unscoped operator token, and `apps/api/app.py`'s remaining size — **S3**.
- Repo topology and releases — **Plan A**.
- The lease store itself — **Plan B1**, which this plan depends on.
