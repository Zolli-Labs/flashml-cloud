# Foundation B1 — Lease store: optimistic concurrency and Postgres

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `LeaseStore` safe for more than one concurrent writer, and add a Postgres implementation, so the coordinator can eventually run multiple workers without double-leasing a task.

**Architecture:** Every `TaskRecord` carries a `version`. `save()` becomes a compare-and-swap: it persists only if the stored version still matches the version the caller read, then increments. A losing writer gets `ConflictError`, and `LeaseManager.claim()`/`sweep()` retry in a bounded loop. This mechanism works for both the FIFO claim path and the `policy.choose(...)` scheduler path — a row-locking claim (`SELECT … FOR UPDATE SKIP LOCKED`) would only cover the first and would silently leave the second racy. SQLite and in-memory stores implement the same contract, so the OSS single-machine path shares the semantics and the tests.

**Tech Stack:** Python 3.10+, pydantic v2, sqlite3 (stdlib), psycopg 3, pytest.

## Global Constraints

- `flashruntime` is **public** and must stay runnable with no Postgres. `InMemoryLeaseStore` and `SqliteLeaseStore` are not deprecated and not removed.
- `psycopg[binary]>=3.1` goes in a new `postgres` extra, never in core dependencies. Core stays `pydantic>=2.7` plus what is already there.
- `LeaseManager` remains a pure state machine: no I/O, no threads, no clock of its own. Time stays injected via `now`.
- Existing suites must stay green at every commit. Record counts per suite in `PROGRESS.md` per its logging protocol.
- Postgres connection strings in this project use the **session pooler on :5432**, never the transaction pooler on :6543 — psycopg uses prepared statements by default and transaction mode breaks them intermittently.
- Do not change `flashnode` or `flashml-cloud` in this plan. The store contract is internal to `flashruntime`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `flashruntime/leases/store.py` | `TaskRecord`, `LeaseStore` protocol, `ConflictError`, `InMemoryLeaseStore` | Modify |
| `flashruntime/leases/sqlite_store.py` | SQLite implementation | Modify |
| `flashruntime/leases/postgres_store.py` | Postgres implementation | **Create** |
| `flashruntime/leases/manager.py` | Retry loops in `claim()` and `sweep()` | Modify |
| `flashruntime/leases/__init__.py` | Export `ConflictError`, `PostgresLeaseStore` | Modify |
| `flashruntime/service/app.py` | Select store from env | Modify |
| `flashruntime/pyproject.toml` | `postgres` extra | Modify |
| `tests/test_lease_store_contract.py` | Shared contract suite, parameterized over stores | **Create** |
| `tests/integration/test_postgres_lease_store.py` | Real-Postgres concurrency test | **Create** |

Postgres lives in its own module rather than inside `sqlite_store.py` so that importing `flashruntime.leases` never requires psycopg. The import is lazy — see Task 5.

---

### Task 1: Version and the compare-and-swap contract

**Files:**
- Modify: `flashruntime/leases/store.py`
- Test: `tests/test_lease_store_contract.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `ConflictError(Exception)`; `TaskRecord.version: int` (starts at 0, set by `add()`); `LeaseStore.save(record: TaskRecord) -> None` raising `ConflictError` when `record.version` no longer matches the stored version, and incrementing `record.version` in place on success.

`save()` takes no new parameter. The record carries its own expected version, so no existing call site changes shape — only its failure modes.

- [ ] **Step 1: Write the failing test**

Create `tests/test_lease_store_contract.py`:

```python
"""Contract every LeaseStore implementation must satisfy.

Parameterized so a new backend is proven against the same behaviour as the
reference in-memory store, rather than against its own assumptions.
"""
import pytest

from flashruntime.leases.store import (
    ConflictError,
    InMemoryLeaseStore,
    TaskRecord,
)
from flashruntime.protocol.v1alpha1 import TaskSpec, TaskState


def make_spec(job_id="job-1", task_id="task-000"):
    return TaskSpec(job_id=job_id, task_id=task_id, payload={}, lease_seconds=30)


@pytest.fixture(params=["memory"])
def store(request):
    return InMemoryLeaseStore()


def test_add_starts_version_at_zero(store):
    record = TaskRecord(make_spec())
    store.add(record)
    assert record.version == 0


def test_save_increments_version(store):
    record = TaskRecord(make_spec())
    store.add(record)
    record.state = TaskState.LEASED
    store.save(record)
    assert record.version == 1


def test_save_with_stale_version_raises_conflict(store):
    record = TaskRecord(make_spec())
    store.add(record)
    store.save(record)          # version 0 -> 1
    record.version = 0          # simulate a writer that read the old row
    with pytest.raises(ConflictError):
        store.save(record)


def test_conflicting_save_does_not_increment(store):
    record = TaskRecord(make_spec())
    store.add(record)
    store.save(record)
    record.version = 0
    with pytest.raises(ConflictError):
        store.save(record)
    assert record.version == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd flashruntime && .venv/bin/pytest tests/test_lease_store_contract.py -v`
Expected: FAIL — `ImportError: cannot import name 'ConflictError'`.

- [ ] **Step 3: Implement the minimal change**

In `flashruntime/leases/store.py`, add the exception above `TaskRecord`:

```python
class ConflictError(Exception):
    """A `save()` precondition failed: the stored record moved on since the
    caller read it. The caller must re-read and re-apply its decision —
    never retry blindly with the same stale record."""
```

Add `"version"` to `TaskRecord.__slots__` and initialise it in `__init__`:

```python
    __slots__ = (
        "spec", "state", "attempts_used", "active_lease",
        "accepted_attempt_id", "lease_history", "version",
    )

    def __init__(self, spec: TaskSpec):
        self.spec = spec
        self.state = TaskState.PENDING
        self.attempts_used = 0
        self.active_lease: Lease | None = None
        self.accepted_attempt_id: str | None = None
        self.lease_history: dict[str, Lease] = {}
        self.version = 0
```

Document the precondition on the protocol's `save`:

```python
    def save(self, record: TaskRecord) -> None:
        """Persist `record`, but only if the stored version still matches
        `record.version`. On success the store increments `record.version`
        in place. On mismatch it raises `ConflictError` and changes nothing.

        This is the only concurrency control in the system: the manager is
        a pure state machine and holds no locks."""
        ...
```

Give `InMemoryLeaseStore` a version table. Live references make the record and the stored object identical, so versions must be tracked separately or the check is vacuous:

```python
    def __init__(self) -> None:
        self._tasks: dict[tuple[str, str], TaskRecord] = {}
        self._versions: dict[tuple[str, str], int] = {}

    def add(self, record: TaskRecord) -> None:
        key = (record.spec.job_id, record.spec.task_id)
        if key in self._tasks:
            raise ValueError(
                f"task {record.spec.task_id} already exists in job {record.spec.job_id}"
            )
        record.version = 0
        self._tasks[key] = record
        self._versions[key] = 0

    def save(self, record: TaskRecord) -> None:
        key = (record.spec.job_id, record.spec.task_id)
        stored = self._versions.get(key)
        if stored is None:
            raise ConflictError(f"unknown task {key}")
        if stored != record.version:
            raise ConflictError(
                f"task {key} is at version {stored}, caller holds {record.version}"
            )
        self._versions[key] = stored + 1
        record.version = stored + 1
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd flashruntime && .venv/bin/pytest tests/test_lease_store_contract.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Run the full suite — this is the step that finds the breakage**

Run: `cd flashruntime && .venv/bin/pytest -q`
Expected: PASS. Every existing `save()` call now increments a version, which no existing test asserts on, so this should be green. If a test fails because it calls `save()` twice on a record it never re-read, that test was relying on the unconditional write — fix the test to re-read, not the store to be lenient.

- [ ] **Step 6: Commit**

```bash
git add flashruntime/leases/store.py flashruntime/tests/test_lease_store_contract.py
git commit -m "feat(leases): version TaskRecord and make save() a compare-and-swap

Concurrency control for multi-writer stores. save() persists only if the
stored version still matches the caller's, then increments. InMemoryLeaseStore
tracks versions separately because its live references make an
identity-based check vacuous."
```

---

### Task 2: Compare-and-swap in SqliteLeaseStore

**Files:**
- Modify: `flashruntime/leases/sqlite_store.py`
- Test: `tests/test_lease_store_contract.py:store` (extend the fixture)

**Interfaces:**
- Consumes: `ConflictError`, `TaskRecord.version` from Task 1.
- Produces: `SqliteLeaseStore` satisfying the same contract; schema gains a `version INTEGER NOT NULL DEFAULT 0` column, migrated in place.

- [ ] **Step 1: Extend the contract fixture to cover SQLite**

In `tests/test_lease_store_contract.py`, replace the fixture:

```python
from flashruntime.leases.sqlite_store import SqliteLeaseStore


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryLeaseStore()
    return SqliteLeaseStore(tmp_path / "leases.db")
```

Add a rehydration test, because SQLite is the first store where version must survive a restart:

```python
def test_version_survives_rehydration(tmp_path):
    path = tmp_path / "leases.db"
    first = SqliteLeaseStore(path)
    record = TaskRecord(make_spec())
    first.add(record)
    first.save(record)
    assert record.version == 1

    second = SqliteLeaseStore(path)
    rehydrated = second.get("job-1", "task-000")
    assert rehydrated.version == 1

    stale = second.get("job-1", "task-000")
    stale.version = 0
    with pytest.raises(ConflictError):
        second.save(stale)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd flashruntime && .venv/bin/pytest tests/test_lease_store_contract.py -v`
Expected: FAIL — the `sqlite` parameter fails `test_save_with_stale_version_raises_conflict` (SQLite currently writes unconditionally), and `test_version_survives_rehydration` fails on `rehydrated.version == 1`.

- [ ] **Step 3: Add the column and the migration**

In `_SCHEMA`, add `version INTEGER NOT NULL DEFAULT 0,` before `PRIMARY KEY`. Add `version` to the end of `_COLUMNS`.

`_migrate()` currently only handles the primary-key rebuild. Add a column check that runs after it, before `_load()`:

```python
    def _add_version_column(self) -> None:
        """Older databases predate optimistic concurrency. ALTER TABLE ADD
        COLUMN with a DEFAULT is safe in SQLite and preserves every row —
        in-flight leases must survive the upgrade."""
        info = self._conn.execute("PRAGMA table_info(lease_tasks)").fetchall()
        if not info:
            return
        if any(col[1] == "version" for col in info):
            return
        self._conn.execute(
            "ALTER TABLE lease_tasks ADD COLUMN version INTEGER NOT NULL DEFAULT 0"
        )
        self._conn.commit()
```

Call it from `__init__` immediately after `self._conn.executescript(_SCHEMA)`:

```python
        self._migrate()
        self._conn.executescript(_SCHEMA)
        self._add_version_column()
```

- [ ] **Step 4: Load the version during rehydration**

In `_load()`, add `version` to the SELECT and to the unpack:

```python
        rows = self._conn.execute(
            "SELECT spec_json, state, attempts_used, active_lease_json,"
            " accepted_attempt_id, lease_history_json, seq, version"
            " FROM lease_tasks ORDER BY seq"
        ).fetchall()
        for (spec_json, state, attempts, lease_json, accepted,
             history_json, seq, version) in rows:
            ...
            record.version = version
```

- [ ] **Step 5: Make `save()` conditional**

`add()` keeps using `_persist` for the initial insert. Split the update path out:

```python
    def add(self, record: TaskRecord) -> None:
        key = (record.spec.job_id, record.spec.task_id)
        if key in self._cache:
            raise ValueError(
                f"task {record.spec.task_id} already exists in job {record.spec.job_id}"
            )
        record.version = 0
        self._cache[key] = record
        self._seq += 1
        self._insert(record, self._seq)

    def save(self, record: TaskRecord) -> None:
        cur = self._conn.execute(
            "UPDATE lease_tasks SET"
            "  state = ?, attempts_used = ?, active_lease_json = ?,"
            "  accepted_attempt_id = ?, lease_history_json = ?,"
            "  version = version + 1"
            " WHERE job_id = ? AND task_id = ? AND version = ?",
            (
                record.state.value,
                record.attempts_used,
                record.active_lease.model_dump_json() if record.active_lease else None,
                record.accepted_attempt_id,
                self._history_json(record),
                record.spec.job_id,
                record.spec.task_id,
                record.version,
            ),
        )
        if cur.rowcount != 1:
            self._conn.rollback()
            raise ConflictError(
                f"task ({record.spec.job_id}, {record.spec.task_id}) changed"
                f" underneath a writer holding version {record.version}"
            )
        self._conn.commit()
        record.version += 1
```

Rename the existing `_persist` to `_insert` (it is now only used by `add()`), keeping the `seq` handling, and factor the history serialisation out so both paths share it:

```python
    def _history_json(self, record: TaskRecord) -> str:
        return json.dumps(
            {lid: json.loads(lease.model_dump_json())
             for lid, lease in record.lease_history.items()}
        )
```

Import `ConflictError` at the top: `from flashruntime.leases.store import ConflictError, TaskRecord`.

- [ ] **Step 6: Run the contract suite and the full suite**

Run: `cd flashruntime && .venv/bin/pytest tests/test_lease_store_contract.py -v && .venv/bin/pytest -q`
Expected: contract suite PASS across both parameters plus the rehydration test; full suite PASS.

- [ ] **Step 7: Commit**

```bash
git add flashruntime/leases/sqlite_store.py flashruntime/tests/test_lease_store_contract.py
git commit -m "feat(leases): compare-and-swap saves in SqliteLeaseStore

Adds a version column, migrates existing databases in place (in-flight
leases must survive the upgrade), and makes save() a conditional UPDATE
that raises ConflictError when it matches no row."
```

---

### Task 3: Bounded retry in claim() and sweep()

**Files:**
- Modify: `flashruntime/leases/manager.py`
- Test: `tests/test_leases_conflict.py` (create)

**Interfaces:**
- Consumes: `ConflictError` from Task 1.
- Produces: `LeaseManager.claim(...)` returns `None` after `_MAX_CLAIM_RETRIES` consecutive conflicts instead of raising; `LeaseManager.sweep(...)` skips a record whose expiry lost a race and still returns the count it actually expired.

- [ ] **Step 1: Write the failing test**

Create `tests/test_leases_conflict.py`:

```python
"""Retry behaviour when two writers race for the same task."""
import pytest

from flashruntime.leases.manager import LeaseManager
from flashruntime.leases.store import (
    ConflictError,
    InMemoryLeaseStore,
    TaskRecord,
)
from flashruntime.protocol.v1alpha1 import TaskSpec, TaskState


def make_spec(task_id="task-000"):
    return TaskSpec(job_id="job-1", task_id=task_id, payload={}, lease_seconds=30)


class ConflictOnceStore(InMemoryLeaseStore):
    """Raises ConflictError on the first save, then behaves normally.

    Stands in for a second coordinator worker that won the race."""

    def __init__(self):
        super().__init__()
        self.conflicts = 0

    def save(self, record: TaskRecord) -> None:
        if self.conflicts == 0:
            self.conflicts += 1
            raise ConflictError("simulated race")
        super().save(record)


class AlwaysConflictStore(InMemoryLeaseStore):
    def save(self, record: TaskRecord) -> None:
        raise ConflictError("permanent contention")


def test_claim_retries_past_a_single_conflict():
    store = ConflictOnceStore()
    manager = LeaseManager(store=store)
    manager.add_task(make_spec())

    lease = manager.claim(node_id="node-a")

    assert lease is not None
    assert store.conflicts == 1


def test_claim_gives_up_and_returns_none_under_permanent_contention():
    store = AlwaysConflictStore()
    manager = LeaseManager(store=store)
    manager.add_task(make_spec())

    assert manager.claim(node_id="node-a") is None


def test_sweep_counts_only_leases_it_actually_expired(monkeypatch):
    from datetime import timedelta

    store = InMemoryLeaseStore()
    manager = LeaseManager(store=store)
    manager.add_task(make_spec("task-000"))
    manager.add_task(make_spec("task-001"))

    lease_a = manager.claim(node_id="node-a")
    lease_b = manager.claim(node_id="node-b")
    assert lease_a is not None and lease_b is not None

    calls = {"n": 0}
    original = store.save

    def flaky_save(record):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConflictError("another worker expired this one first")
        original(record)

    monkeypatch.setattr(store, "save", flaky_save)

    expired = manager.sweep(now=lease_a.deadline + timedelta(seconds=1))

    assert expired == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd flashruntime && .venv/bin/pytest tests/test_leases_conflict.py -v`
Expected: FAIL — all three raise `ConflictError` out of `claim`/`sweep`.

- [ ] **Step 3: Add the retry loop to claim()**

In `manager.py`, add a module-level constant below `EventSink`:

```python
# How many times a claim re-reads and retries after losing a compare-and-swap
# race. Five is generous for the contended case (many nodes, few pending
# tasks) and bounded so a pathological loop cannot spin. Exhaustion returns
# None, which every caller already handles as "nothing claimable right now".
_MAX_CLAIM_RETRIES = 5
```

Rename the existing `claim` body to `_claim_once` (identical code, same signature) and add:

```python
    def claim(
        self,
        node_id: str,
        job_id: str | None = None,
        now: datetime | None = None,
        policy: object | None = None,
        node: dict | None = None,
    ) -> Lease | None:
        """Claim the next PENDING task for `node_id`, or None when nothing is
        claimable.

        Retries on ConflictError: with several coordinator workers, two
        claims can select the same record and only one compare-and-swap can
        win. The loser re-reads and tries the next candidate."""
        for _ in range(_MAX_CLAIM_RETRIES):
            try:
                return self._claim_once(node_id, job_id, now, policy, node)
            except ConflictError:
                continue
        return None
```

Import it: `from flashruntime.leases.store import ConflictError, InMemoryLeaseStore, LeaseStore, TaskRecord`.

- [ ] **Step 4: Make sweep() conflict-tolerant**

A sweep that loses a race means another worker already expired that lease — the correct response is to skip it, not to abort the whole sweep. In `sweep()`, wrap the release:

```python
            try:
                self._release(record, now, requeue_event=EventType.TASK_REQUEUED)
            except ConflictError:
                continue  # another worker expired this lease first
            expired += 1
```

Move the `expired += 1` and the `_emit` of `LEASE_EXPIRED` to *after* a successful release, so the count and the event stream both describe what actually happened rather than what was attempted.

- [ ] **Step 5: Run the tests**

Run: `cd flashruntime && .venv/bin/pytest tests/test_leases_conflict.py -v && .venv/bin/pytest -q`
Expected: 3 PASS; full suite PASS.

- [ ] **Step 6: Commit**

```bash
git add flashruntime/leases/manager.py flashruntime/tests/test_leases_conflict.py
git commit -m "feat(leases): bounded retry on compare-and-swap conflicts

claim() re-reads and retries up to 5 times, then returns None — which every
caller already treats as 'nothing claimable'. sweep() skips a lease another
worker expired first and counts only what it actually expired."
```

---

### Task 4: The concurrency contract test

**Files:**
- Modify: `tests/test_lease_store_contract.py`

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Produces: `test_concurrent_claims_never_double_lease`, reusable for `PostgresLeaseStore` in Task 5.

This is the test the whole plan exists for. Today the no-double-lease property holds by accident — one event loop — and nothing asserts it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_lease_store_contract.py`:

```python
import threading

from flashruntime.leases.manager import LeaseManager


def test_concurrent_claims_never_double_lease(store):
    """N threads claim from one store; every task is leased at most once.

    The store is the only synchronisation point — LeaseManager holds no
    locks by design, so this is a direct test of the compare-and-swap."""
    manager = LeaseManager(store=store)
    task_count = 20
    for i in range(task_count):
        manager.add_task(make_spec(task_id=f"task-{i:03d}"))

    claimed: list = []
    guard = threading.Lock()
    barrier = threading.Barrier(8)

    def worker(node_index: int):
        barrier.wait()          # maximise overlap on the contended path
        while True:
            lease = manager.claim(node_id=f"node-{node_index}")
            if lease is None:
                return
            with guard:
                claimed.append((lease.job_id, lease.task_id))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed) == len(set(claimed)), "a task was leased twice"
```

- [ ] **Step 2: Run it**

Run: `cd flashruntime && .venv/bin/pytest tests/test_lease_store_contract.py::test_concurrent_claims_never_double_lease -v`
Expected: PASS for both `memory` and `sqlite`.

**If it fails, do not weaken the test.** A failure here means the compare-and-swap has a hole, and that hole is the exact bug this plan exists to prevent.

Two things this test deliberately does **not** assert, both for good reasons:

- `len(claimed) == task_count`. Retry exhaustion under heavy contention legitimately leaves tasks pending for the next claim. Asserting completeness would make the test flaky for a reason unrelated to correctness.
- The integrity of `record.attempts_used` on the in-memory and SQLite stores. Both hand out **live references**, so two threads that select the same record mutate one shared object — the loser of the compare-and-swap has already incremented `attempts_used` before finding out it lost. No task is double-*leased* (only the winner's `save()` returns a lease), but the attempt counter can over-count. This is a real limitation of live-reference stores under threads, and it is precisely why `PostgresLeaseStore` returns snapshots instead (Task 5). Do not "fix" the in-memory store by adding locks: it is a single-process reference implementation, the service never runs it concurrently, and locking it would hide the distinction this plan is built on.

- [ ] **Step 3: Commit**

```bash
git add flashruntime/tests/test_lease_store_contract.py
git commit -m "test(leases): assert concurrent claims never double-lease a task

The property previously held by accident (single event loop) and was
untested. Parameterized so every backend is held to it."
```

---

### Task 5: PostgresLeaseStore

**Files:**
- Create: `flashruntime/leases/postgres_store.py`
- Modify: `flashruntime/leases/__init__.py`, `flashruntime/pyproject.toml`
- Test: `tests/integration/test_postgres_lease_store.py` (create)

**Interfaces:**
- Consumes: the contract from Tasks 1–4.
- Produces: `PostgresLeaseStore(dsn: str)` satisfying `LeaseStore`. Unlike the other two stores it holds **no record cache** — `get`, `next_pending`, `leased`, and `all` read from the database and return fresh `TaskRecord` objects, because a cache cannot be coherent across processes.

- [ ] **Step 1: Add the optional dependency**

In `flashruntime/pyproject.toml`, under `[project.optional-dependencies]`:

```toml
postgres = ["psycopg[binary]>=3.1"]
```

Core dependencies are unchanged. `flashruntime` must remain installable and runnable with no Postgres.

- [ ] **Step 2: Write the failing integration test**

Create `tests/integration/test_postgres_lease_store.py`:

```python
"""PostgresLeaseStore against a real database.

Skipped unless FLASHML_TEST_POSTGRES_DSN is set. Run locally with:
  docker run --rm -e POSTGRES_PASSWORD=pw -p 5433:5432 -d postgres:16
  FLASHML_TEST_POSTGRES_DSN=postgresql://postgres:pw@localhost:5433/postgres \
    .venv/bin/pytest tests/integration/test_postgres_lease_store.py -v -m integration
"""
import os
import threading

import pytest

from flashruntime.leases.manager import LeaseManager
from flashruntime.leases.store import ConflictError, TaskRecord
from flashruntime.protocol.v1alpha1 import TaskSpec, TaskState

pytestmark = pytest.mark.integration

DSN = os.environ.get("FLASHML_TEST_POSTGRES_DSN")


def make_spec(job_id="job-1", task_id="task-000"):
    return TaskSpec(job_id=job_id, task_id=task_id, payload={}, lease_seconds=30)


@pytest.fixture
def store():
    if not DSN:
        pytest.skip("FLASHML_TEST_POSTGRES_DSN not set")
    from flashruntime.leases.postgres_store import PostgresLeaseStore

    s = PostgresLeaseStore(DSN, schema="flashml_test")
    s.reset()          # drop and recreate the schema — test isolation
    return s


def test_save_with_stale_version_raises_conflict(store):
    record = TaskRecord(make_spec())
    store.add(record)
    store.save(record)
    record.version = 0
    with pytest.raises(ConflictError):
        store.save(record)


def test_reads_are_snapshots_not_shared_references(store):
    """Two readers must not share one object — that is what makes the
    compare-and-swap meaningful across processes."""
    record = TaskRecord(make_spec())
    store.add(record)

    first = store.get("job-1", "task-000")
    second = store.get("job-1", "task-000")

    assert first is not second
    first.state = TaskState.LEASED
    assert second.state == TaskState.PENDING


def test_state_survives_a_new_store_instance(store):
    record = TaskRecord(make_spec())
    store.add(record)
    store.save(record)

    from flashruntime.leases.postgres_store import PostgresLeaseStore

    other = PostgresLeaseStore(DSN, schema="flashml_test")
    rehydrated = other.get("job-1", "task-000")
    assert rehydrated is not None
    assert rehydrated.version == 1


def test_concurrent_claims_never_double_lease(store):
    manager = LeaseManager(store=store)
    for i in range(20):
        manager.add_task(make_spec(task_id=f"task-{i:03d}"))

    claimed: list = []
    guard = threading.Lock()
    barrier = threading.Barrier(8)

    def worker(node_index: int):
        barrier.wait()
        while True:
            lease = manager.claim(node_id=f"node-{node_index}")
            if lease is None:
                return
            with guard:
                claimed.append((lease.job_id, lease.task_id))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed) == len(set(claimed)), "a task was leased twice"
```

- [ ] **Step 3: Run to verify it fails**

Run:
```bash
cd flashruntime
docker run --rm -e POSTGRES_PASSWORD=pw -p 5433:5432 -d --name flashml-pg-test postgres:16
FLASHML_TEST_POSTGRES_DSN=postgresql://postgres:pw@localhost:5433/postgres \
  .venv/bin/pytest tests/integration/test_postgres_lease_store.py -v -m integration
```
Expected: FAIL — `ModuleNotFoundError: flashruntime.leases.postgres_store`.

- [ ] **Step 4: Implement the store**

Create `flashruntime/leases/postgres_store.py`:

```python
"""Postgres-backed LeaseStore: the first store safe for more than one writer.

Unlike InMemoryLeaseStore and SqliteLeaseStore this store holds NO record
cache. Those two keep live TaskRecord objects and let the manager mutate
them in place, which is correct only because one process owns the state. A
cache here would be stale the moment a second coordinator worker wrote a
row, so every read hits the database and returns a fresh object, and
`save()`'s compare-and-swap is the only thing that decides who wins.

Requires the `postgres` extra: pip install "flashruntime[postgres]".
"""

from __future__ import annotations

import json

import psycopg
from psycopg.rows import tuple_row

from flashruntime.leases.store import ConflictError, TaskRecord
from flashruntime.protocol.v1alpha1 import Lease, TaskSpec, TaskState

_COLUMNS = (
    "spec_json, state, attempts_used, active_lease_json,"
    " accepted_attempt_id, lease_history_json, version"
)


class PostgresLeaseStore:
    def __init__(self, dsn: str, schema: str = "coordinator") -> None:
        self._dsn = dsn
        self._schema = schema
        self._ensure_schema()

    # -- connection ----------------------------------------------------------

    def _connect(self) -> psycopg.Connection:
        # autocommit=False: save() needs the UPDATE and its rowcount check
        # inside one transaction so a conflicting write cannot land between.
        return psycopg.connect(self._dsn, row_factory=tuple_row)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._schema}"')
            conn.execute(
                f'CREATE TABLE IF NOT EXISTS "{self._schema}".lease_tasks ('
                "  job_id TEXT NOT NULL,"
                "  task_id TEXT NOT NULL,"
                "  spec_json JSONB NOT NULL,"
                "  state TEXT NOT NULL,"
                "  attempts_used INTEGER NOT NULL,"
                "  active_lease_json JSONB,"
                "  accepted_attempt_id TEXT,"
                "  lease_history_json JSONB NOT NULL,"
                "  seq BIGSERIAL,"
                "  version INTEGER NOT NULL DEFAULT 0,"
                "  PRIMARY KEY (job_id, task_id))"
            )
            conn.execute(
                f'CREATE INDEX IF NOT EXISTS idx_lease_tasks_pending'
                f' ON "{self._schema}".lease_tasks (state, seq)'
            )
            conn.commit()

    def reset(self) -> None:
        """Drop and recreate the schema. Tests only."""
        with self._connect() as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{self._schema}" CASCADE')
            conn.commit()
        self._ensure_schema()

    # -- row <-> record ------------------------------------------------------

    def _to_record(self, row) -> TaskRecord:
        spec_json, state, attempts, lease_json, accepted, history_json, version = row
        record = TaskRecord(TaskSpec.model_validate(spec_json))
        record.state = TaskState(state)
        record.attempts_used = attempts
        record.active_lease = Lease.model_validate(lease_json) if lease_json else None
        record.accepted_attempt_id = accepted
        record.lease_history = {
            lid: Lease.model_validate(raw) for lid, raw in history_json.items()
        }
        record.version = version
        return record

    def _history_json(self, record: TaskRecord) -> str:
        return json.dumps(
            {lid: json.loads(lease.model_dump_json())
             for lid, lease in record.lease_history.items()}
        )

    # -- LeaseStore protocol -------------------------------------------------

    def add(self, record: TaskRecord) -> None:
        record.version = 0
        with self._connect() as conn:
            try:
                conn.execute(
                    f'INSERT INTO "{self._schema}".lease_tasks'
                    " (job_id, task_id, spec_json, state, attempts_used,"
                    "  active_lease_json, accepted_attempt_id,"
                    "  lease_history_json, version)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0)",
                    (
                        record.spec.job_id,
                        record.spec.task_id,
                        record.spec.model_dump_json(),
                        record.state.value,
                        record.attempts_used,
                        record.active_lease.model_dump_json() if record.active_lease else None,
                        record.accepted_attempt_id,
                        self._history_json(record),
                    ),
                )
            except psycopg.errors.UniqueViolation as exc:
                raise ValueError(
                    f"task {record.spec.task_id} already exists"
                    f" in job {record.spec.job_id}"
                ) from exc
            conn.commit()

    def save(self, record: TaskRecord) -> None:
        with self._connect() as conn:
            cur = conn.execute(
                f'UPDATE "{self._schema}".lease_tasks SET'
                "  state = %s, attempts_used = %s, active_lease_json = %s,"
                "  accepted_attempt_id = %s, lease_history_json = %s,"
                "  version = version + 1"
                " WHERE job_id = %s AND task_id = %s AND version = %s",
                (
                    record.state.value,
                    record.attempts_used,
                    record.active_lease.model_dump_json() if record.active_lease else None,
                    record.accepted_attempt_id,
                    self._history_json(record),
                    record.spec.job_id,
                    record.spec.task_id,
                    record.version,
                ),
            )
            if cur.rowcount != 1:
                conn.rollback()
                raise ConflictError(
                    f"task ({record.spec.job_id}, {record.spec.task_id}) changed"
                    f" underneath a writer holding version {record.version}"
                )
            conn.commit()
        record.version += 1

    def get(self, job_id: str, task_id: str) -> TaskRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                f'SELECT {_COLUMNS} FROM "{self._schema}".lease_tasks'
                " WHERE job_id = %s AND task_id = %s",
                (job_id, task_id),
            ).fetchone()
        return self._to_record(row) if row else None

    def next_pending(self, job_id: str | None = None) -> TaskRecord | None:
        clause = " AND job_id = %s" if job_id else ""
        params = (TaskState.PENDING.value, job_id) if job_id else (TaskState.PENDING.value,)
        with self._connect() as conn:
            row = conn.execute(
                f'SELECT {_COLUMNS} FROM "{self._schema}".lease_tasks'
                f" WHERE state = %s{clause} ORDER BY seq LIMIT 1",
                params,
            ).fetchone()
        return self._to_record(row) if row else None

    def leased(self) -> list[TaskRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                f'SELECT {_COLUMNS} FROM "{self._schema}".lease_tasks'
                " WHERE state = %s ORDER BY seq",
                (TaskState.LEASED.value,),
            ).fetchall()
        return [self._to_record(r) for r in rows]

    def all(self, job_id: str | None = None) -> list[TaskRecord]:
        clause = " WHERE job_id = %s" if job_id else ""
        params = (job_id,) if job_id else ()
        with self._connect() as conn:
            rows = conn.execute(
                f'SELECT {_COLUMNS} FROM "{self._schema}".lease_tasks'
                f"{clause} ORDER BY seq",
                params,
            ).fetchall()
        return [self._to_record(r) for r in rows]
```

- [ ] **Step 5: Export it lazily**

In `flashruntime/leases/__init__.py`, export `ConflictError` eagerly and `PostgresLeaseStore` lazily, so importing `flashruntime.leases` never requires psycopg:

```python
def __getattr__(name):
    if name == "PostgresLeaseStore":
        from flashruntime.leases.postgres_store import PostgresLeaseStore
        return PostgresLeaseStore
    raise AttributeError(name)
```

Add `"ConflictError"` and `"PostgresLeaseStore"` to `__all__`, and add `ConflictError` to the existing eager imports from `.store`.

- [ ] **Step 6: Run the integration tests**

Run:
```bash
cd flashruntime
FLASHML_TEST_POSTGRES_DSN=postgresql://postgres:pw@localhost:5433/postgres \
  .venv/bin/pytest tests/integration/test_postgres_lease_store.py -v -m integration
```
Expected: 4 PASS.

Then confirm nothing regressed and psycopg is genuinely optional:
```bash
.venv/bin/pytest -q
python -c "import flashruntime.leases; print('ok')"
docker rm -f flashml-pg-test
```
Expected: full suite PASS; the import succeeds.

- [ ] **Step 7: Commit**

```bash
git add flashruntime/leases/postgres_store.py flashruntime/leases/__init__.py \
        flashruntime/pyproject.toml flashruntime/tests/integration/test_postgres_lease_store.py
git commit -m "feat(leases): PostgresLeaseStore, the first multi-writer store

Holds no record cache — a cache cannot be coherent across processes, so
every read returns a fresh TaskRecord and the compare-and-swap in save() is
the only arbiter. psycopg lives behind a 'postgres' extra and a lazy import
so flashruntime still installs and runs with no Postgres."
```

---

### Task 6: Select the store from configuration

**Files:**
- Modify: `flashruntime/service/app.py:139`
- Test: `tests/test_service_store_selection.py` (create)

**Interfaces:**
- Consumes: `PostgresLeaseStore` from Task 5.
- Produces: the service uses `PostgresLeaseStore` when `FLASHML_LEASE_STORE_URL` is set to a `postgres://` or `postgresql://` DSN, and `SqliteLeaseStore` otherwise. Default behaviour is unchanged.

- [ ] **Step 1: Write the failing test**

Create `tests/test_service_store_selection.py`:

```python
"""Store selection is configuration, not code."""
import pytest

from flashruntime.service.app import build_lease_store
from flashruntime.leases.sqlite_store import SqliteLeaseStore


def test_defaults_to_sqlite(tmp_path):
    store = build_lease_store(ledger_path=str(tmp_path / "ledger.db"), url=None)
    assert isinstance(store, SqliteLeaseStore)


def test_postgres_url_selects_postgres_store(monkeypatch, tmp_path):
    created = {}

    class FakePostgresLeaseStore:
        def __init__(self, dsn, schema="coordinator"):
            created["dsn"] = dsn
            created["schema"] = schema

    monkeypatch.setattr(
        "flashruntime.leases.postgres_store.PostgresLeaseStore",
        FakePostgresLeaseStore,
    )
    store = build_lease_store(
        ledger_path=str(tmp_path / "ledger.db"),
        url="postgresql://user:pw@host:5432/db",
    )
    assert isinstance(store, FakePostgresLeaseStore)
    assert created["dsn"] == "postgresql://user:pw@host:5432/db"


def test_unrecognised_scheme_is_refused_loudly(tmp_path):
    with pytest.raises(ValueError, match="FLASHML_LEASE_STORE_URL"):
        build_lease_store(ledger_path=str(tmp_path / "l.db"), url="mysql://x/y")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd flashruntime && .venv/bin/pytest tests/test_service_store_selection.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_lease_store'`.

- [ ] **Step 3: Implement it**

In `flashruntime/service/app.py`, add above the settings class:

```python
def build_lease_store(ledger_path: str, url: str | None):
    """Choose the lease store from configuration.

    Refuses an unrecognised scheme rather than silently falling back to
    SQLite — a coordinator that quietly ignores its configured database
    would look healthy while losing every lease on restart."""
    if not url:
        return SqliteLeaseStore(Path(ledger_path).with_name("leases.db"))
    if url.startswith(("postgres://", "postgresql://")):
        from flashruntime.leases.postgres_store import PostgresLeaseStore

        return PostgresLeaseStore(url)
    raise ValueError(
        f"FLASHML_LEASE_STORE_URL has an unsupported scheme: {url!r}."
        " Use a postgresql:// DSN, or unset it for SQLite."
    )
```

Add the setting alongside `ledger_path` (line 63):

```python
    lease_store_url: str | None = field(
        default_factory=lambda: os.environ.get("FLASHML_LEASE_STORE_URL") or None
    )
```

Replace line 139:

```python
    lease_store = build_lease_store(settings.ledger_path, settings.lease_store_url)
```

- [ ] **Step 4: Run the tests**

Run: `cd flashruntime && .venv/bin/pytest tests/test_service_store_selection.py -v && .venv/bin/pytest -q`
Expected: 3 PASS; full suite PASS.

- [ ] **Step 5: Commit**

```bash
git add flashruntime/service/app.py flashruntime/tests/test_service_store_selection.py
git commit -m "feat(service): select the lease store from FLASHML_LEASE_STORE_URL

Defaults to SQLite, so nothing changes for existing deployments or the OSS
single-machine path. An unrecognised scheme is refused at boot rather than
falling back silently."
```

---

## Definition of done

1. `TaskRecord` carries a version; `save()` is a compare-and-swap on every store.
2. `ConflictError` is raised, never swallowed, by all three stores.
3. `claim()` retries up to 5 times and returns `None` on exhaustion; `sweep()` skips lost races and counts only what it expired.
4. `test_concurrent_claims_never_double_lease` passes against in-memory, SQLite, and Postgres.
5. Existing SQLite databases upgrade in place — an in-flight lease survives the version-column migration.
6. `import flashruntime.leases` works with psycopg absent; `pip install flashruntime` needs no Postgres.
7. `FLASHML_LEASE_STORE_URL` unset behaves exactly as today.
8. flashruntime, flashnode, and e2e suites green, with counts logged in `PROGRESS.md`.

## Not in this plan

- Removing the Render disk, lifting `--workers 1`, `CheckpointCatalog`, `Ledger`, artifacts → object storage. All of that is **Plan B2**, and none of it is safe until this contract is settled.
- Anything in `flashnode` or `flashml-cloud`.
- Repository topology and releases — **Plan A**.

## Open question carried from the spec

Spec §8.1 asked whether `SqliteLeaseStore` can implement the optimistic contract without leaking Postgres-shaped assumptions into the OSS path. Task 2 is where that gets answered. If the conditional `UPDATE` turns out to fight SQLite's single-writer model in practice, the honest outcome is a thin shared interface with two stores rather than a leaky abstraction — record the decision in `PROGRESS.md` and revise this plan rather than forcing it.
