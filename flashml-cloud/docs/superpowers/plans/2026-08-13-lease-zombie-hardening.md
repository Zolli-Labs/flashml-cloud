# Lease & Zombie Hardening Implementation Plan (flashml repo)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the zombie/stall class from the 2026-08-13 bug audit: a lease that expires (or is revoked) must stop the work, must not corrupt another attempt's results, and must not burn the task's failure budget.

**Architecture:** Three moves, designed as one set. (1) The coordinator learns the difference between *liveness* and *progress*: renewals are refused after a progress-silent window, expiries get their own budget separate from worker-reported failures, and operators get force-expire. (2) The node learns to *stop*: lease loss kills the running process group and fences the checkpoint relay and uploads. (3) The wire model carries `lease_seconds` so renewal pacing stops depending on the device clock.

**Tech Stack:** Python ≥3.10, pydantic v2 (protocol models), FastAPI (service), stdlib `subprocess`/`threading` (agent), pytest.

**Spec:** `flashml-cloud/docs/superpowers/specs/2026-08-13-bug-audit-leases-fc-fragmented-devices.md` §0, §1 (C3, C6–C8, C10–C13), §5 fix shapes 1–5, 7. Read it first.

## Global Constraints

- **Repo/branch:** work in the `flashml` monorepo, in a worktree at `.worktrees/lease-zombie-hardening`, branch `fix/lease-zombie-hardening` cut from `fix/trusted-tier-execution` (tip `e6a69d6`). That branch already fixed the 600 s cap and silent mirror builds (`c133c5f`) — do not re-fix those; do not touch `agent/cli.py`'s runner construction or `environments._build`'s pip flags.
- **Shared checkout:** NEVER run repo-scoped git commands (`stash`, `checkout <ref>`, `reset`, `clean`, `restore`) in `~/Work/Zolli-Labs/flashml` itself; all work happens inside the worktree. Commit with explicit paths only.
- **Protocol is versioned and additive:** every new field gets a default that reproduces today's behavior when absent; parsing yesterday's JSON must still work (pydantic v2 ignores unknown keys on the reading side — rely on defaults, never on required new fields).
- **Dependency direction:** flashruntime imports nothing from flashnode. flashnode imports `flashruntime.protocol` only.
- **POSIX kill semantics** (`start_new_session`, `os.killpg`) are guarded by `os.name == "posix"`; on other platforms fall back to current behavior.
- **Suites:** flashruntime venv needs `uv pip install -e ".[dev,service]"` (service extra is REQUIRED — eight test modules import the HTTP service). flashnode baseline on this branch: **588 passed, 12 deselected**. Both suites green before the final commit.
- **TDD:** every task = failing test first, then minimal implementation. Frequent commits, explicit paths, message style `fix(flashruntime): …` / `fix(flashnode): …`, ending with the standard co-author trailer.

## File Structure (whole tranche)

```
flashruntime/flashruntime/protocol/v1alpha1.py    # +Lease.lease_seconds, +TaskSpec budgets, +2 EventTypes
flashruntime/flashruntime/leases/manager.py       # budget split, watchdog, note_progress, force_expire,
                                                  # lease_is_live, cancel event, late-fail evidence,
                                                  # idempotent-winner complete
flashruntime/flashruntime/leases/store.py         # TaskRecord: +failures_seen, +expiries_seen, +last_progress_at
flashruntime/flashruntime/leases/sqlite_store.py  # +runtime_json column (ALTER TABLE), round-trip
flashruntime/flashruntime/service/checkpoints.py  # zombie fencing (410) + note_progress on both writes
flashruntime/flashruntime/service/modea.py        # force-expire route
flashruntime/tests/test_leases.py                 # extend
flashruntime/tests/test_service_modea.py (or the existing service test module)  # extend
flashnode/flashnode/executor/procs.py             # NEW: run_with_group_kill (group spawn + bounded reap)
flashnode/flashnode/executor/task_logs.py         # run_capturing uses procs helper
flashnode/flashnode/executor/environments.py      # _run uses procs helper
flashnode/flashnode/executor/runner.py            # SubprocessRunner: abort seam
flashnode/flashnode/executor/trusted_runner.py    # abort seam, =-token rewrite, payload env forwarding
flashnode/flashnode/executor/argv_runner.py       # abort() = docker kill
flashnode/flashnode/executor/docker_runner.py     # abort() = docker kill
flashnode/flashnode/executor/loop.py              # lost_event plumbing, on_lost→abort, upload fencing,
                                                  # relay stop, hb interval from lease_seconds,
                                                  # dep-cooldown re-key
flashnode/flashnode/executor/client.py            # HTTPException retry net, 403/410→LeaseLost mapping
flashnode/tests/…                                 # per task below
```

Two independent lanes: **Lane A = flashruntime (Tasks 1–4, strict order)**, **Lane B = flashnode (Tasks 5–8; 5 before 6; 7 and 8 anytime)**. Task 9 gates the merge.

---

### Task 1: Protocol additions (Lease.lease_seconds, TaskSpec budgets, EventTypes)

**Files:**
- Modify: `flashruntime/flashruntime/protocol/v1alpha1.py` (TaskSpec ~line 453, Lease ~line 485, EventType ~line 199)
- Test: `flashruntime/tests/test_protocol_v1alpha1.py` (extend existing protocol test module; if none exists for these models, add `flashruntime/tests/test_protocol_lease_fields.py`)

**Interfaces:**
- Produces: `TaskSpec.max_expiries: int` (default 9, ge=1), `TaskSpec.max_silent_seconds: float` (default 1800.0, ge=0; 0 disables the watchdog), `Lease.lease_seconds: float` (default 0.0 = unknown/legacy), `EventType.TASK_CANCELLED`, `EventType.LEASE_RENEWAL_REFUSED`. Tasks 2, 3, 4, 6 consume these exact names.

- [ ] **Step 1: Write the failing tests**

```python
def test_taskspec_budget_defaults_and_backcompat():
    spec = TaskSpec(job_id="j", task_id="t", payload={})
    assert spec.max_expiries == 9
    assert spec.max_silent_seconds == 1800.0
    # Yesterday's JSON (no new fields) must still parse with the defaults.
    old = TaskSpec.model_validate_json(
        '{"job_id":"j","task_id":"t","payload":{},"max_attempts":3,"lease_seconds":60.0}'
    )
    assert old.max_expiries == 9 and old.max_silent_seconds == 1800.0

def test_lease_carries_lease_seconds_with_legacy_default():
    old = Lease.model_validate_json(
        '{"lease_id":"ls-1","task_id":"t","job_id":"j","node_id":"n",'
        '"attempt_number":1,"deadline":"2026-08-13T00:00:00+00:00","payload":{}}'
    )
    assert old.lease_seconds == 0.0

def test_new_event_types_exist():
    assert EventType.TASK_CANCELLED.value == "TASK_CANCELLED"
    assert EventType.LEASE_RENEWAL_REFUSED.value == "LEASE_RENEWAL_REFUSED"
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_protocol_lease_fields.py -v` → FAIL (attribute/validation errors).
- [ ] **Step 3: Implement** — add to `TaskSpec` after `lease_seconds`:

```python
    max_expiries: int = Field(default=9, ge=1)          # lease-expiry budget, separate from max_attempts
    max_silent_seconds: float = Field(default=1800.0, ge=0)  # renewal watchdog window; 0 disables
```

  add to `Lease` after `deadline`: `lease_seconds: float = 0.0` with a comment that 0.0 means "issued by a coordinator that predates this field." Add the two EventType members under a `# Lease hardening (additive, August 2026)` comment.
- [ ] **Step 4: Run tests** → PASS. Run the whole protocol/lease test files to catch regressions.
- [ ] **Step 5: Commit** — `git add flashruntime/flashruntime/protocol/v1alpha1.py flashruntime/tests/test_protocol_lease_fields.py && git commit -m "feat(flashruntime): lease_seconds on the wire, expiry and silence budgets on TaskSpec"`

---

### Task 2: Budget split — expiries stop burning the failure budget

**Files:**
- Modify: `flashruntime/flashruntime/leases/store.py` (TaskRecord), `flashruntime/flashruntime/leases/manager.py` (`claim`, `fail`, `sweep`, `_release`), `flashruntime/flashruntime/leases/sqlite_store.py`
- Test: `flashruntime/tests/test_leases.py`, `flashruntime/tests/test_sqlite_lease_store.py` (extend the existing modules)

**Interfaces:**
- Consumes: Task 1's `TaskSpec.max_expiries`.
- Produces: `TaskRecord.failures_seen: int = 0`, `TaskRecord.expiries_seen: int = 0`, `TaskRecord.last_progress_at: datetime | None = None`; `_release(record, now, requeue_event, *, budget: str)` where `budget` is `"failure"` or `"expiry"`. Task 3 consumes `last_progress_at` and the `"expiry"` release path.

- [ ] **Step 1: Failing tests** (drive the manager with injected `now`, exactly like the existing tests in `test_leases.py`):

```python
def test_expiries_do_not_burn_the_failure_budget():
    mgr = LeaseManager()
    mgr.add_task(TaskSpec(job_id="j", task_id="t", payload={}, max_attempts=3, lease_seconds=10))
    t0 = datetime(2026, 8, 13, tzinfo=timezone.utc)
    for i in range(3):  # three claim→expire cycles: pure churn, no worker ever failed
        lease = mgr.claim("n1", now=t0 + timedelta(seconds=i * 100))
        assert lease is not None
        mgr.sweep(now=t0 + timedelta(seconds=i * 100 + 11))
    record = mgr.records("j")[0]
    assert record.state == TaskState.PENDING          # NOT FAILED — the old behavior
    assert record.expiries_seen == 3 and record.failures_seen == 0

def test_worker_failures_still_exhaust_at_max_attempts():
    mgr = LeaseManager()
    mgr.add_task(TaskSpec(job_id="j", task_id="t", payload={}, max_attempts=2, lease_seconds=10))
    t0 = datetime(2026, 8, 13, tzinfo=timezone.utc)
    for i in range(2):
        lease = mgr.claim("n1", now=t0 + timedelta(seconds=i))
        mgr.fail(lease.lease_id, "boom", now=t0 + timedelta(seconds=i))
    assert mgr.records("j")[0].state == TaskState.FAILED

def test_expiry_budget_eventually_terminates_a_cursed_task():
    spec = TaskSpec(job_id="j", task_id="t", payload={}, max_attempts=3, lease_seconds=10, max_expiries=4)
    mgr = LeaseManager(); mgr.add_task(spec)
    t0 = datetime(2026, 8, 13, tzinfo=timezone.utc)
    for i in range(4):
        mgr.claim("n1", now=t0 + timedelta(seconds=i * 100))
        mgr.sweep(now=t0 + timedelta(seconds=i * 100 + 11))
    assert mgr.records("j")[0].state == TaskState.FAILED   # detail names the expiry budget
```

- [ ] **Step 2: Run** → FAIL (first test: task is FAILED after 3 expiries today).
- [ ] **Step 3: Implement.** In `store.py` add the three fields to `TaskRecord.__init__` beside `attempts_used`. In `manager.py`:
  - `claim`: keep `attempts_used += 1`; add `record.last_progress_at = now`.
  - `fail`: `record.failures_seen += 1` before `self._release(record, now, EventType.TASK_REQUEUED, budget="failure")`.
  - `sweep` expiry branch: `record.expiries_seen += 1` then `self._release(..., budget="expiry")`.
  - `_release` signature `(self, record, now, requeue_event, *, budget)`; terminal test becomes:

```python
        exhausted = (
            record.failures_seen >= record.spec.max_attempts
            if budget == "failure"
            else record.expiries_seen >= record.spec.max_expiries
        )
        if exhausted:
            record.state = TaskState.FAILED
            detail = (
                f"all {record.spec.max_attempts} attempts failed"
                if budget == "failure"
                else f"lease expired {record.expiries_seen} times without completing"
            )
            self._emit(EventType.TASK_EXHAUSTED, ..., detail=detail, now=now)
```

- [ ] **Step 4: Run the full lease test module** — existing tests that asserted FAILED-after-N-expiries will break; update ONLY assertions that encoded the old conflation (each updated test gets a one-line comment citing audit C12). `pytest tests/test_leases.py -v` → PASS.
- [ ] **Step 5: SQLite round-trip.** Failing test in `test_sqlite_lease_store.py`:

```python
def test_budget_counters_survive_reopen(tmp_path):
    db = tmp_path / "leases.db"
    store = SqliteLeaseStore(db)
    mgr = LeaseManager(store=store)
    mgr.add_task(TaskSpec(job_id="j", task_id="t", payload={}, lease_seconds=10))
    t0 = datetime(2026, 8, 13, tzinfo=timezone.utc)
    mgr.claim("n1", now=t0); mgr.sweep(now=t0 + timedelta(seconds=11))
    reopened = SqliteLeaseStore(db)
    rec = reopened.get("j", "t")
    assert rec.expiries_seen == 1 and rec.failures_seen == 0 and rec.last_progress_at is not None
```

  Implement: in `sqlite_store.py` after `executescript(_SCHEMA)` run a guarded `ALTER TABLE lease_tasks ADD COLUMN runtime_json TEXT` (check `PRAGMA table_info` for the column first). `_persist` writes `json.dumps({"failures_seen": …, "expiries_seen": …, "last_progress_at": record.last_progress_at.isoformat() if … else None})`; `_load` restores with `.get(...)` defaults so pre-upgrade rows load as zeros/None.
- [ ] **Step 6: Run both test modules** → PASS.
- [ ] **Step 7: Commit** — `fix(flashruntime): lease churn no longer burns the failure budget (audit C12)`

---

### Task 3: Progress watchdog + operator/lifecycle fixes in the manager

**Files:**
- Modify: `flashruntime/flashruntime/leases/manager.py`
- Test: `flashruntime/tests/test_leases.py`

**Interfaces:**
- Consumes: Task 1 fields, Task 2 release paths.
- Produces (Task 4 and flashnode consume): `note_progress(job_id, task_id, now=None) -> None`, `lease_is_live(lease_id, now=None) -> bool`, `force_expire(job_id, task_id, reason, now=None) -> bool`; `heartbeat` raises `LeaseError` (→410) after silence; `claim` returns leases with `lease_seconds` set; `complete` returns True for the accepted winner's retry; `fail` on a known-but-dead lease records evidence and returns; `cancel_task` emits `TASK_CANCELLED`.

- [ ] **Step 1: Failing tests**

```python
def _mgr_with_events(**spec_overrides):
    events = []
    mgr = LeaseManager(on_event=events.append)
    mgr.add_task(TaskSpec(job_id="j", task_id="t", payload={},
                          lease_seconds=60, **spec_overrides))
    return mgr, events

T0 = datetime(2026, 8, 13, tzinfo=timezone.utc)

def test_watchdog_refuses_renewal_after_silence_and_requeues():
    # Timeline is load-bearing: each heartbeat must land INSIDE the live
    # lease window (else _require_live_lease raises for the wrong reason,
    # before the watchdog is consulted). lease 60s, silence budget 120s:
    #   claim t+0 (deadline t+60, progress baseline t+0)
    #   hb t+50  → live, silence 50s  → renewed (deadline t+110)
    #   hb t+100 → live, silence 100s → renewed (deadline t+160)
    #   hb t+130 → live, silence 130s > 120s → REFUSED, task requeued
    mgr, events = _mgr_with_events(max_silent_seconds=120)
    lease = mgr.claim("n1", now=T0)
    assert lease.lease_seconds == 60.0
    mgr.heartbeat(lease.lease_id, now=T0 + timedelta(seconds=50))
    mgr.heartbeat(lease.lease_id, now=T0 + timedelta(seconds=100))
    with pytest.raises(LeaseError):
        mgr.heartbeat(lease.lease_id, now=T0 + timedelta(seconds=130))
    rec = mgr.records("j")[0]
    assert rec.state == TaskState.PENDING and rec.expiries_seen == 1
    assert any(e.type == EventType.LEASE_RENEWAL_REFUSED for e in events)

def test_note_progress_resets_the_watchdog():
    mgr, _ = _mgr_with_events(max_silent_seconds=120)
    lease = mgr.claim("n1", now=T0)
    mgr.heartbeat(lease.lease_id, now=T0 + timedelta(seconds=50))
    mgr.note_progress("j", "t", now=T0 + timedelta(seconds=100))
    # silence measured from the note, not the claim: 60s < 120s ⇒ renewed
    renewed = mgr.heartbeat(lease.lease_id, now=T0 + timedelta(seconds=160))
    assert renewed.deadline == T0 + timedelta(seconds=220)
    assert mgr.lease_is_live(lease.lease_id, now=T0 + timedelta(seconds=161))

def test_watchdog_disabled_when_zero():
    mgr, events = _mgr_with_events(max_silent_seconds=0)
    lease = mgr.claim("n1", now=T0)
    for i in range(1, 200):  # ~3.3 silent hours of renewals: today's behavior
        mgr.heartbeat(lease.lease_id, now=T0 + timedelta(seconds=50 * i))
    assert mgr.records("j")[0].state == TaskState.LEASED
    assert not any(e.type == EventType.LEASE_RENEWAL_REFUSED for e in events)

def test_force_expire_requeues_a_held_task():
    mgr, events = _mgr_with_events()
    mgr.claim("n1", now=T0)
    assert mgr.force_expire("j", "t", "operator: stalled FC worker", now=T0) is True
    rec = mgr.records("j")[0]
    assert rec.state == TaskState.PENDING and rec.expiries_seen == 1
    assert any(e.type == EventType.LEASE_EXPIRED and "force-expired" in e.message
               for e in events)
    assert mgr.force_expire("j", "t", "again", now=T0) is False  # nothing leased now

def test_completed_winner_retry_is_accepted_idempotently():
    mgr, _ = _mgr_with_events()
    lease = mgr.claim("n1", now=T0)
    assert mgr.complete(lease.lease_id, "a" * 64, now=T0 + timedelta(seconds=1)) is True
    # the winner's retried commit (response lost to a timeout) must be
    # answered True, not told it lost — audit §2 "retried complete"
    assert mgr.complete(lease.lease_id, "a" * 64, now=T0 + timedelta(seconds=2)) is True

def test_late_commit_from_a_loser_is_still_rejected():
    mgr, _ = _mgr_with_events()
    first = mgr.claim("n1", now=T0)
    mgr.sweep(now=T0 + timedelta(seconds=61))            # first lease expires
    second = mgr.claim("n2", now=T0 + timedelta(seconds=62))
    assert mgr.complete(second.lease_id, "b" * 64, now=T0 + timedelta(seconds=63)) is True
    assert mgr.complete(first.lease_id, "a" * 64, now=T0 + timedelta(seconds=64)) is False

def test_late_fail_records_evidence_instead_of_raising():
    mgr, events = _mgr_with_events()
    lease = mgr.claim("n1", now=T0)
    mgr.sweep(now=T0 + timedelta(seconds=61))            # expired and requeued
    mgr.fail(lease.lease_id, "cuda OOM", now=T0 + timedelta(seconds=90))  # must NOT raise
    late = [e for e in events
            if e.type == EventType.TASK_ATTEMPT_FAILED and "late report" in e.message]
    assert late and "cuda OOM" in late[0].message
    rec = mgr.records("j")[0]
    assert rec.failures_seen == 0                        # a dead lease burns no budget
    assert rec.state == TaskState.PENDING                # and changes no state

def test_cancel_emits_event():
    mgr, events = _mgr_with_events()
    mgr.cancel_task("j", "t")
    assert events[-1].type == EventType.TASK_CANCELLED
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Reference kernel for the watchdog, inside `heartbeat` after `_require_live_lease`:

```python
        window = record.spec.max_silent_seconds
        baseline = record.last_progress_at
        if window > 0 and baseline is not None and (now - baseline).total_seconds() > window:
            self._emit(
                EventType.LEASE_RENEWAL_REFUSED, lease.job_id, lease.task_id,
                node_id=lease.node_id,
                detail=f"no progress for {(now - baseline).total_seconds():.0f}s "
                       f"(budget {window:.0f}s) — lease revoked, task requeued",
                now=now,
            )
            record.expiries_seen += 1
            self._release(record, now, EventType.TASK_REQUEUED, budget="expiry")
            raise LeaseError(f"lease {lease_id} revoked: no progress within {window:.0f}s")
```

  `claim` sets `lease_seconds=record.spec.lease_seconds` when building the `Lease`. `note_progress` looks up the record, and when `state == LEASED` sets `last_progress_at = now or _utcnow()` and saves (unknown task or non-leased state: silent no-op — the relay may race an expiry, and that is fine). `lease_is_live` = `_find_lease` + `_is_live`, returning False for unknown ids instead of raising. `force_expire`: require the record, return False unless LEASED, else emit `LEASE_EXPIRED` with `detail=f"force-expired: {reason}"`, bump `expiries_seen`, `_release(..., budget="expiry")`, return True. `complete` early branch: `if record.state == TaskState.COMPLETED and record.accepted_attempt_id == lease.lease_id: return True`. `fail`: replace `_require_live_lease` with `_find_lease` + an `_is_live` check; dead-but-known → emit `TASK_ATTEMPT_FAILED` with `detail=f"(late report — lease no longer live) {reason}"` and return. `cancel_task`: emit `TASK_CANCELLED` after saving.
- [ ] **Step 4: Run the module** → PASS.
- [ ] **Step 5: Commit** — `feat(flashruntime): progress watchdog, force-expire, and honest lease lifecycle events (audit C11/§5.1)`

---

### Task 4: Service wiring — fencing, progress notes, force-expire route

**Files:**
- Modify: `flashruntime/flashruntime/service/checkpoints.py` (both write endpoints), `flashruntime/flashruntime/service/modea.py` (new route; find the existing operator-authed route pattern by reading `DELETE /v1alpha1/jobs/{job_id}/artifacts` and reuse its exact auth dependency)
- Test: the existing service test module that exercises checkpoints/leases over `TestClient` (grep `tests/` for `checkpoints` + `TestClient`), extend it.

**Interfaces:**
- Consumes: Task 3's `lease_is_live`, `note_progress`, `force_expire`.
- Produces: `POST /v1alpha1/jobs/{job_id}/tasks/{task_id}/force-expire` body `{"reason": "<str>"}` → 200 `{"forced": true}` / 409 when nothing is leased / 404 unknown task; checkpoint `POST …/parts` and `POST …/commit` answer **410** `{"detail": "lease …"}` when `attempt_id` is not a live lease.

- [ ] **Step 1: Failing tests** (TestClient; follow the existing service-test fixtures for app construction):

```python
def test_zombie_checkpoint_write_is_refused_with_410(service):
    lease = claim_task(service)                       # existing helper pattern in the module
    expire_all_leases(service)                        # advance the injected clock / sweep
    r = service.post(f"/v1alpha1/jobs/{lease['job_id']}/tasks/{lease['task_id']}/checkpoints/parts",
                     json={"attempt_id": lease["lease_id"], "step": 1, "part": PART})
    assert r.status_code == 410

def test_checkpoint_write_resets_the_watchdog(service): ...
def test_force_expire_route_requeues(service):
    lease = claim_task(service)
    r = service.post(f"/v1alpha1/jobs/{j}/tasks/{t}/force-expire", json={"reason": "stalled"},
                     headers=OPERATOR_AUTH)
    assert r.status_code == 200
    assert claim_task(service)["lease_id"] != lease["lease_id"]   # re-claimable immediately
```

- [ ] **Step 2: Run** → FAIL (parts write is 200 from a dead lease today; route 404).
- [ ] **Step 3: Implement.** In `checkpoints.py`, in `register_part` and `commit`, after `authorize_task_write(...)`:

```python
        if not manager.lease_is_live(req.attempt_id):
            raise HTTPException(status_code=410,
                                detail="lease is no longer live — checkpoint write refused")
```

  and after the successful catalog call in each: `manager.note_progress(job_id, task_id)`. In `modea.py` add the route beside the other task-scoped admin routes, guarded by the same operator dependency as artifact deletion, calling `manager.force_expire(job_id, task_id, body.reason)`; map False → 409, unknown task (`LeaseError`) → 404.
- [ ] **Step 4: Run the service test module and the FULL flashruntime suite** (`pytest`) → PASS.
- [ ] **Step 5: Commit** — `feat(flashruntime): checkpoint writes prove a live lease and count as progress; operator force-expire (audit C13/§5.4)`

---

### Task 5: flashnode — process-group spawn with bounded reap

**Files:**
- Create: `flashnode/flashnode/executor/procs.py`
- Modify: `flashnode/flashnode/executor/task_logs.py:210-218` (`run_capturing`), `flashnode/flashnode/executor/environments.py` (`_run`, ~line 408)
- Test: `flashnode/tests/test_procs.py` (new)

**Interfaces:**
- Produces: `run_with_group_kill(command: list[str], *, timeout: float, reap_seconds: float = 30.0, on_spawn: Callable[[subprocess.Popen], None] | None = None, **popen_kwargs) -> subprocess.CompletedProcess` — `on_spawn` is invoked with the Popen immediately after spawn (exceptions from it must not leak); raises `subprocess.TimeoutExpired` (carrying partial output) on wall-clock expiry after SIGKILLing the **process group**; never blocks longer than `timeout + reap_seconds + 5`. Also `kill_group(proc: subprocess.Popen) -> None` (idempotent, safe on an exited process). `run_capturing` grows a passthrough `on_spawn` keyword. Task 6 consumes exactly these: runners pass `on_spawn=self._track` to remember their live child, and `abort()` calls `kill_group(self._current_proc)`.

- [ ] **Step 1: Failing tests**

```python
def test_grandchildren_die_with_the_group(tmp_path):
    # child forks a backgrounded sleeper that INHERITS stdout, then sleeps
    script = "import subprocess,sys,time; subprocess.Popen(['sleep','300']); time.sleep(300)"
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_with_group_kill([sys.executable, "-c", script], timeout=1.0)
    assert time.monotonic() - t0 < 10           # no pipe-hold, no untimed wait
    # the sleeper must be gone: scan our children via os.waitpid / psutil-free check
    ...

def test_partial_output_survives_the_kill():
    script = "print('progress-line', flush=True); import time; time.sleep(300)"
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        run_with_group_kill([sys.executable, "-c", script], timeout=1.0)
    assert b"progress-line" in (exc.value.stdout or b"")

def test_normal_exit_unchanged():
    proc = run_with_group_kill([sys.executable, "-c", "print('ok')"], timeout=10.0)
    assert proc.returncode == 0 and b"ok" in proc.stdout
```

- [ ] **Step 2: Run** → FAIL (module missing).
- [ ] **Step 3: Implement** — reference kernel (this is the audit's C3 + the "untimed `wait()`" stall; the bounded second communicate and the pipe-close escape are the load-bearing parts):

```python
def run_with_group_kill(command, *, timeout, reap_seconds=30.0, **popen_kwargs):
    posix = os.name == "posix"
    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=posix, **popen_kwargs,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_group(proc)
        try:
            out, err = proc.communicate(timeout=reap_seconds)
        except subprocess.TimeoutExpired:
            # A grandchild in a NEW session (daemonized) can hold the pipes even
            # after the group is dead, and a D-state child can refuse SIGKILL.
            # Closing our read ends is the escape hatch: never block forever
            # on a child that cannot die (audit §0.1 candidate 2).
            for stream in (proc.stdout, proc.stderr):
                if stream is not None:
                    stream.close()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log.error("child %s is unkillable (D-state?); abandoning reap", proc.pid)
            out, err = b"", b""
        raise subprocess.TimeoutExpired(command, timeout, output=out, stderr=err)
    return subprocess.CompletedProcess(command, proc.returncode, out, err)

def kill_group(proc):
    if proc.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError):
            pass
    proc.kill()
```

- [ ] **Step 4: Wire callers.** `run_capturing` replaces its `subprocess.run(...)` with `run_with_group_kill(command, timeout=timeout, **kwargs)` — the `capture_output=True` kwarg disappears (the helper owns the pipes); log-writing behavior on both paths is byte-identical (the existing tests in `test_task_logs.py` pin it). `environments._run` gets the same substitution (keep its 1800 s ceiling and error shape). Run `pytest tests/test_task_logs.py tests/test_environments.py tests/test_procs.py -v` → PASS.
- [ ] **Step 5: Commit** — `fix(flashnode): task children die as a group, and a reap is never unbounded (audit C3/§0.1)`

---

### Task 6: flashnode — kill-on-lease-lost, relay and upload fencing, skew-free heartbeat

**Files:**
- Modify: `flashnode/flashnode/executor/runner.py` (SubprocessRunner), `trusted_runner.py`, `argv_runner.py`, `docker_runner.py` (abort seams); `flashnode/flashnode/executor/loop.py` (`_AttemptHeartbeat` ~74-97, `_CheckpointRelay` ~100-147, `_execute_inner` ~387-569)
- Test: `flashnode/tests/test_executor_loop.py` (extend; it already stubs client + runner)

**Interfaces:**
- Consumes: Task 5's `kill_group`; Task 1's `Lease.lease_seconds` (via flashruntime; works with 0.0 legacy leases).
- Produces: every runner gains `abort() -> None` (idempotent, callable from another thread, safe when idle); `_AttemptHeartbeat(client, lease, lost_event, on_lost=None)` where `lost_event: threading.Event` replaces the `lost` bool (keep a `lost` property reading the event so existing call sites still work); `_CheckpointRelay(..., lost_event)`.

- [ ] **Step 1: Failing tests**

```python
def test_lease_loss_aborts_the_runner_midrun():
    # stub runner blocks in run() until its abort() is called, then raises TaskExecutionError
    # stub client's attempt_heartbeat raises LeaseLost on first call
    loop = ExecutorLoop(client=stub_client, node_id="n", runner=blocking_runner, ...)
    assert loop.execute_one(make_lease(lease_seconds=0.5)) is False
    assert blocking_runner.abort_called
    assert stub_client.uploaded == []               # nothing shipped after loss

def test_upload_loop_stops_when_lease_lost_midway():
    # runner succeeds; client.upload_artifact flips lost_event after the first file,
    # then asserts no further uploads and no complete()
    ...

def test_relay_stops_on_lost_event_and_on_410():
    # relay with lost_event set ships nothing; a client whose checkpoint_register_part
    # raises LeaseLost stops the relay after one attempt (no 0.3s hammer)
    ...

def test_heartbeat_interval_from_lease_seconds_not_wall_clock():
    lease = make_lease(lease_seconds=60.0, deadline=far_future_or_skewed)
    hb = _AttemptHeartbeat(stub_client, lease, threading.Event())
    assert hb._interval == pytest.approx(20.0)
    legacy = make_lease(lease_seconds=0.0, deadline=now_plus(30))
    assert 2.0 <= _AttemptHeartbeat(stub_client, legacy, threading.Event())._interval <= 10.0
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
  - Runners: Subprocess/Trusted keep `self._current_proc` (set by the procs helper — add an optional `on_spawn` callback to `run_with_group_kill`, or have the runner pass `self` a reference; simplest: `run_capturing(..., proc_holder=holder)` where `holder` is a one-slot list the runner owns) and `abort()` calls `kill_group`. Docker tiers: remember the container name they already generate; `abort()` runs `docker kill <name>` via `subprocess.run(timeout=10)`, exceptions swallowed with a warning log.
  - `_AttemptHeartbeat.run()` on `LeaseLost`: `self._lost_event.set()`; `if self._on_lost: self._on_lost()` (wrap in try/except — an abort failure must not kill the heartbeat thread's clean exit). Interval: `lease.lease_seconds / 3.0` clamped to `[2.0, 60.0]` when `lease.lease_seconds > 0`, else the legacy `(deadline − now)/3` formula (cite audit C8 in the comment).
  - `_execute_inner`: build `lost = threading.Event()` once; hand it to hb and relay; `on_lost = getattr(self.runner, "abort", None)`. In the upload loop add `if lost.is_set(): log.warning(…); return False` at the top of each iteration, and once more before `self.client.complete(...)`.
  - `_CheckpointRelay`: loop condition also exits when `self._lost_event.is_set()`; `_ship_new` returns immediately if set; catch `LeaseLost` from either checkpoint call → set the event and return.
- [ ] **Step 4: Run `pytest tests/test_executor_loop.py -v`, then the full flashnode suite** → PASS (588+new).
- [ ] **Step 5: Commit** — `fix(flashnode): lease loss stops the work — abort seam, relay/upload fencing, skew-free renewal (audit C8/C10/§5.3)`

---

### Task 7: flashnode client — exception net and LeaseLost mapping

**Files:**
- Modify: `flashnode/flashnode/executor/client.py` (`_request` ~101-118, `attempt_heartbeat` ~158, `complete` ~165, `checkpoint_register_part` ~204, `checkpoint_commit` ~215, `upload_artifact` ~248, `claim` ~150)
- Test: `flashnode/tests/test_client.py` (extend the existing stubbed-transport module)

**Interfaces:**
- Consumes: nothing new. Produces: `LeaseLost` raised on 403/410 from lease-scoped endpoints (Task 6's fencing relies on this); `http.client.HTTPException` retried and mapped to `CoordinatorUnreachable`.

- [ ] **Step 1: Failing tests**

```python
def test_incomplete_read_is_a_network_hiccup_not_a_host_fault():
    # transport raises http.client.IncompleteRead(b"x") every time
    with pytest.raises(CoordinatorUnreachable):
        client.download_artifact("k", tmp_path / "f")   # NOT a bare IncompleteRead

def test_lease_scoped_403_and_410_raise_LeaseLost():
    for status in (403, 410):
        for call in (lambda: client.complete("ls-1", "0"*64),
                     lambda: client.upload_artifact(f, "jobs/j/t/out.bin"),
                     lambda: client.checkpoint_commit("j","t","ls-1",1,[part],"p")):
            with pytest.raises(LeaseLost): call()

def test_claim_auth_refusal_is_named():
    # 401 → RuntimeError whose message contains "refused this node's credential"
    ...
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Add `import http.client` and extend the retry tuple at `client.py:110` to `(urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException)` (note: `HTTPError` is returned earlier, so this catches only genuine transport-layer protocol breakage — cite audit C6). In `complete`/`upload_artifact`/`checkpoint_register_part`/`checkpoint_commit`/`attempt_heartbeat`: `if status in (403, 410): raise LeaseLost(f"{path}: {status}")` before the generic RuntimeError. `claim`: for 401/403 raise `RuntimeError(f"coordinator refused this node's credential (HTTP {status}) — re-enrol or check the pool")`. `fail` stays status-blind (by design).
- [ ] **Step 4: Run the client tests + full suite** → PASS.
- [ ] **Step 5: Commit** — `fix(flashnode): network protocol breakage retries, lease-scoped refusals stop the work (audit C6/C7)`

---

### Task 8: flashnode trusted-tier polish + dep-cooldown re-key

**Files:**
- Modify: `flashnode/flashnode/executor/trusted_runner.py` (rewrite block ~83-88, env ~62), `flashnode/flashnode/executor/loop.py` (`_dep_cooldown` usage ~197, 305-326, 335-336)
- Test: `flashnode/tests/test_trusted_runner.py`, `flashnode/tests/test_executor_loop.py`

**Interfaces:** self-contained.

- [ ] **Step 1: Failing tests**

```python
def test_equals_joined_work_tokens_are_rewritten(tmp_path):
    # argv ["--out=/work/out", "--data=/work/inputs/d", "--note=see /work docs"]
    # → first two rewritten onto the real workdir, third untouched (value not /work-prefixed)
    ...

def test_payload_env_reaches_the_child_but_never_overrides_the_runner(tmp_path):
    # payload {"env": {"HF_TOKEN": "x", "PATH": "/evil"}} → child sees HF_TOKEN=x,
    # PATH is the runner's own (interpreter-first) value
    ...

def test_dep_cooldown_keys_on_requirements_not_job():
    # two leases, DIFFERENT job_ids, SAME dependencies list: after job A's build fails,
    # job B's lease fails fast without a rebuild (client.fail called, runner untouched)
    ...
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Rewrite kernel (replaces the list comprehension at ~83):

```python
        def _rewrite(token: str) -> str:
            if token == _CONTAINER_WORKDIR or token.startswith(_CONTAINER_WORKDIR + "/"):
                return str(workdir) + token[len(_CONTAINER_WORKDIR):]
            head, sep, value = token.partition("=")
            if sep and (value == _CONTAINER_WORKDIR or value.startswith(_CONTAINER_WORKDIR + "/")):
                # --flag=/work/… never matched the token-wise rewrite (audit §2);
                # argparse's `--flag /work/…` form did. Same contract, both spellings.
                return head + "=" + str(workdir) + value[len(_CONTAINER_WORKDIR):]
            return token
        rewritten = [_rewrite(a) for a in argv]
```

  Env: `env = {**{k: str(v) for k, v in (payload.get("env") or {}).items()}, **env}` — payload first, runner's own values win (PATH/HOME/FLASHML_WORK_DIR are the agent's contract, a submitter cannot redirect them on a trusted host). Cooldown: key = `hashlib.sha256(json.dumps(sorted(map(str, deps))).encode()).hexdigest()[:16]` computed in a tiny helper `_dep_key(deps)`; use it at both the check site and the record site; prune entries older than `_dep_cooldown_seconds()` whenever the dict is consulted.
- [ ] **Step 4: Run both modules + full suite** → PASS.
- [ ] **Step 5: Commit** — `fix(flashnode): =/work rewrite, payload env on the trusted tier, cooldown keyed by requirements (audit §2)`

---

### Task 9: Cross-package gate

**Files:** none new (CHANGELOG entries only: `flashruntime/CHANGELOG.md`).

- [ ] **Step 1:** flashruntime venv (`uv venv && uv pip install -e ".[dev,service]"` inside the worktree's `flashruntime/`), run `pytest` → all green.
- [ ] **Step 2:** flashnode venv (`uv pip install -e ../flashruntime -e ".[dev]"`), run `pytest` → all green (baseline 588 + new).
- [ ] **Step 3:** e2e sanity from the worktree: the loop-level integration tests already in each suite cover claim→run→commit; if `e2e/` (flashml-cloud) is reachable, note for the reviewer that `make e2e-setup LOCAL=1` against this worktree is the pre-release rehearsal — do not run it from inside this plan.
- [ ] **Step 4:** CHANGELOG entries under an Unreleased heading: lease_seconds on the wire; expiry/silence budgets; watchdog; force-expire; fencing; group-kill; abort-on-lost. One line each.
- [ ] **Step 5:** Commit — `docs(flashruntime): changelog for the lease hardening tranche`

## Self-review notes

- Spec coverage: §5.1 (watchdog) = Tasks 3–4; §5.2 (budget split) = Task 2; §5.3 (kill-on-lost) = Tasks 5–6; §5.4 (operator primitives, runtime half) = Tasks 3–4; §5.5 (lease_seconds + fencing) = Tasks 1, 4, 6, 7; §5.6/5.7 landed earlier in `c133c5f` (out of scope here); C6/C7 = Task 7; §2's trusted-tier gaps = Task 8. **Not in this plan (deliberate):** streaming artifact transfer (C5), claim idempotency keys (C9), event-loop `to_thread` (C14), catalog persistence — each is a separate plan; cloud-side fixes (C16–C19) are plan 2 in flashml-cloud.
- Type consistency: `budget: str` literal `"failure" | "expiry"`; `lost_event: threading.Event` everywhere in Task 6; `lease_is_live(lease_id: str, now: datetime | None) -> bool` consumed by Task 4 exactly as produced by Task 3.
- Known merge caution: `fix/trusted-tier-execution` is also carrying peer-session work; rebase/merge order is decided at release time by whoever cuts flashnode 0.4.1 — this branch stacks on it and touches disjoint lines except `loop.py` (coordinate at merge).
