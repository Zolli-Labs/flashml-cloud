# Host status view and self-quarantine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a host owner see that their machine is contributing, and stop it
volunteering for work it can no longer run.

**Architecture:** `ExecutorLoop` gains five plain counters and an **injected**
`health_check` callable — it never imports the doctor (§2.1.1 of the spec). A
separate daemon thread in a new `flashnode/status.py` reads those counters and
redraws a terminal block; if it dies, work continues. `cli.py` wires the two
together and owns every policy decision (TTY detection, threshold, exit code).

**Tech Stack:** Python ≥3.10, stdlib only (`threading`, `time`, `sys`). pytest.
No new dependencies.

**Spec:** `flashml-cloud/docs/superpowers/specs/2026-08-02-host-status-and-self-quarantine-design.md`

## Global Constraints

- **Public repo.** Every `flashnode/…` path is relative to
  `~/Work/Zolli-Labs/flashml/flashnode/`. Only Task 6 touches `flashml-cloud`.
- **`loop.py` must never import `flashnode.doctor`.** `doctor.py` imports
  `flashnode.executor.hardening`, which initialises the `flashnode.executor`
  package, whose `__init__` imports `loop`. The cycle resolves or explodes by
  import order. Inject the callable; Task 1 asserts the absence of the import.
- **Only `TaskExecutionError` counts as a host failure.** `LeaseLost` and
  `complete()` returning `accepted=False` are not the host's fault — the
  coordinator answers **HTTP 200 with `{"accepted": false}`** when an output
  fails its hash check or a commit loses a race.
- **Backwards compatible by default.** `ExecutorLoop` with no `health_check`
  behaves exactly as today; every existing caller and test stays untouched.
- **The renderer never blocks the loop.** Daemon thread, reads only. No locks
  in the claim path.
- **No watts, no FLOPS, no credits total.** Spec §3.3 — each is refused for a
  stated reason, not deferred for effort.
- Run tests from `~/Work/Zolli-Labs/flashml/flashnode/` with
  `.venv/bin/python -m pytest`.
- Baseline before starting: **flashnode 257 tests passing.**

---

### Task 1: Loop counters, and the failure that actually implicates the host

**Files:**
- Modify: `flashnode/executor/loop.py` (`__init__` ~line 139, `execute_one`
  ~line 302-317)
- Create: `tests/test_loop_counters.py`

**Interfaces:**
- Produces, on `ExecutorLoop`:
  - `tasks_failed: int`
  - `consecutive_failures: int`
  - `current_task: str | None`
  - `current_attempt: int | None`
  - `current_task_started: float | None` (`time.monotonic()`)
  - `quarantined: bool`
  - `__init__(..., health_check: Callable[[], list] | None = None, max_consecutive_failures: int = 0)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_loop_counters.py`:

```python
"""What counts as "this host is broken" — and what does not.

execute_one returns False for three different things and only one of them
implicates the machine. Treating them alike would quarantine a healthy host
that lost three lease races, which is the same trap the contributions ledger
hit: the coordinator answers HTTP 200 with {"accepted": false} when an output
fails its hash check or a commit loses a race.
"""

from __future__ import annotations

import flashnode.executor.loop as loop_mod
from flashnode.executor.loop import ExecutorLoop
from flashnode.executor.runner import TaskExecutionError


class _Lease:
    lease_id = "L1"
    task_id = "T1"
    attempt_number = 1
    payload: dict = {}
    inputs: dict = {}


def _loop(**kw):
    return ExecutorLoop(client=object(), node_id="n1", **kw)


def test_a_fresh_loop_starts_with_clean_counters():
    loop = _loop()
    assert loop.tasks_failed == 0
    assert loop.consecutive_failures == 0
    assert loop.current_task is None
    assert loop.quarantined is False


def test_task_execution_error_increments_the_host_failure_counter(monkeypatch):
    loop = _loop()
    monkeypatch.setattr(loop, "_execute_inner",
                        lambda lease: (_ for _ in ()).throw(TaskExecutionError("docker is unavailable")))
    assert loop.execute_one(_Lease()) is False
    assert loop.consecutive_failures == 1
    assert loop.tasks_failed == 1


def test_lease_lost_does_not_count_against_the_host(monkeypatch):
    """Someone else got the work. That says nothing about this machine."""
    loop = _loop()
    monkeypatch.setattr(loop, "_execute_inner",
                        lambda lease: (_ for _ in ()).throw(loop_mod.LeaseLost()))
    assert loop.execute_one(_Lease()) is False
    assert loop.consecutive_failures == 0


def test_a_rejected_output_does_not_count_against_the_host(monkeypatch):
    """complete() -> accepted=False is HTTP 200. The task RAN here fine; the
    coordinator declined the result or lost a race."""
    loop = _loop()
    monkeypatch.setattr(loop, "_execute_inner", lambda lease: False)
    assert loop.execute_one(_Lease()) is False
    assert loop.consecutive_failures == 0
    assert loop.tasks_failed == 0


def test_one_success_resets_the_streak(monkeypatch):
    loop = _loop()
    monkeypatch.setattr(loop, "_execute_inner",
                        lambda lease: (_ for _ in ()).throw(TaskExecutionError("boom")))
    loop.execute_one(_Lease())
    loop.execute_one(_Lease())
    assert loop.consecutive_failures == 2
    monkeypatch.setattr(loop, "_execute_inner", lambda lease: True)
    loop.execute_one(_Lease())
    assert loop.consecutive_failures == 0


def test_current_task_is_set_while_running_and_cleared_after(monkeypatch):
    loop = _loop()
    seen = {}

    def inner(lease):
        seen["task"] = loop.current_task
        seen["attempt"] = loop.current_attempt
        seen["started"] = loop.current_task_started
        return True

    monkeypatch.setattr(loop, "_execute_inner", inner)
    loop.execute_one(_Lease())
    assert seen["task"] == "T1"
    assert seen["attempt"] == 1
    assert seen["started"] is not None
    assert loop.current_task is None


def test_the_loop_module_does_not_import_the_doctor():
    """doctor.py imports flashnode.executor.hardening, which initialises the
    flashnode.executor package, whose __init__ imports loop. loop -> doctor
    -> executor -> loop resolves or explodes by import order — the worst kind
    of bug to ship to machines we cannot reach. Inject, never import."""
    source = __import__("pathlib").Path(loop_mod.__file__).read_text()
    assert "flashnode.doctor" not in source
    assert "from flashnode import doctor" not in source
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_loop_counters.py -v`
Expected: FAIL — `AttributeError: 'ExecutorLoop' object has no attribute
'tasks_failed'`, and `_execute_inner` does not exist.

- [ ] **Step 3: Extract `_execute_inner` and add the counters**

In `flashnode/executor/loop.py`, add to `__init__` (after
`self.tasks_accepted = 0`):

```python
        # Host-facing state. Plain attributes on purpose: flashnode/status.py
        # reads them from a separate thread on a timer, and putting a lock in
        # the claim path to protect a counter would be trading a correctness
        # risk for a cosmetic one.
        self.tasks_failed = 0
        self.consecutive_failures = 0
        self.current_task: str | None = None
        self.current_attempt: int | None = None
        self.current_task_started: float | None = None
        self.quarantined = False
        # Injected, never imported — see the plan's Global Constraints.
        # None disables the quarantine entirely, which is what every existing
        # caller gets.
        self.health_check = health_check
        self.max_consecutive_failures = max_consecutive_failures
```

and to the signature, after `max_unpacked_members`:

```python
        health_check=None,
        max_consecutive_failures: int = 0,
```

Now split `execute_one`. Rename the **existing body** to `_execute_inner`
(everything from the current `try:` through `finally: hb.stop()`), and give
`execute_one` this new body:

```python
    def execute_one(self, lease: Lease) -> bool:
        """Run one lease, and record what its outcome says about this HOST.

        Three different things return False here and only one implicates the
        machine:
          TaskExecutionError  — could not run it here            -> counts
          LeaseLost           — someone else has the work        -> does not
          accepted=False      — coordinator declined the result  -> does not

        That last one is HTTP 200. Counting it would punish a healthy host
        for losing a commit race — the same trap the contributions ledger
        hit by crediting on 2xx.
        """
        self.current_task = lease.task_id
        self.current_attempt = lease.attempt_number
        self.current_task_started = time.monotonic()
        try:
            accepted = self._execute_inner(lease)
        except TaskExecutionError:
            # _execute_inner already reported fail() and logged the cause.
            self.tasks_failed += 1
            self.consecutive_failures += 1
            return False
        if accepted:
            self.consecutive_failures = 0
        return accepted
        # NOTE: no `finally` for the counters — current_* is cleared below so
        # a raised error still clears it.
```

Wrap the three lines above in `try/finally` so `current_*` always clears:

```python
        try:
            ...as above...
        finally:
            self.current_task = None
            self.current_attempt = None
            self.current_task_started = None
```

`_execute_inner` must now **re-raise** `TaskExecutionError` after reporting it,
instead of returning False. Change its handler from:

```python
        except TaskExecutionError as exc:
            log.warning(...)
            try:
                self.client.fail(lease.lease_id, str(exc)[:500])
            except Exception:
                pass
            return False
```

to the same body ending in `raise` instead of `return False`. `LeaseLost`
keeps returning `False`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_loop_counters.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest`
Expected: 264 passed. If an existing executor test asserted `execute_one`
returns False on `TaskExecutionError`, it still does — the re-raise is caught
one level up. If any test called `_execute_inner`'s old name, update the call,
never the behaviour.

- [ ] **Step 6: Commit**

```bash
git add flashnode/executor/loop.py tests/test_loop_counters.py
git commit -m "feat(loop): count the failures that actually implicate this host

LeaseLost and a coordinator answering 200 with {accepted: false} are not the
machine's fault. Counting them would quarantine a healthy host that lost
three commit races — the trap the ledger hit by crediting on 2xx."
```

---

### Task 2: Self-quarantine — the counter does not decide, the doctor does

**Files:**
- Modify: `flashnode/executor/loop.py` (`run` ~line 333)
- Modify: `tests/test_loop_counters.py`

**Interfaces:**
- Consumes: `health_check`, `max_consecutive_failures`, `consecutive_failures`
  (Task 1).
- Produces:
  - `quarantined: bool` set True when the host fails its own checks; `run()`
    returns early.
  - `health_report: list | None` — the check results that caused it, so
    `cli.py` can print them (Task 5 reads this).
  - `_should_stop_volunteering() -> bool`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loop_counters.py`:

Add `import pytest` to the file's imports, then append:

```python
class _Check:
    def __init__(self, status):
        self.status = status
        self.name = "docker engine reachable"
        self.detail = "_ping 500"
        self.fix = "Start Docker Desktop."


class _Brake(Exception):
    """Stops a loop that is SUPPOSED to keep claiming, so a test asserting
    'it did not stop' cannot hang the suite."""


class _FailingClient:
    """Hands out the same lease forever; the runner always fails."""

    def __init__(self, brake_after=5):
        self.claims = 0
        self.brake_after = brake_after

    def claim(self, node_id):
        self.claims += 1
        if self.claims > self.brake_after:
            raise _Brake
        return _Lease()

    def node_heartbeat(self, node_id):
        return True

    def fail(self, lease_id, msg):
        pass


def _quarantine_loop(health, threshold=3):
    return ExecutorLoop(client=_FailingClient(), node_id="n1",
                        health_check=health, max_consecutive_failures=threshold,
                        poll_seconds=0)


def _always_fails(monkeypatch, loop):
    monkeypatch.setattr(
        loop, "_execute_inner",
        lambda lease: (_ for _ in ()).throw(TaskExecutionError("boom")))


def test_an_unhealthy_host_stops_claiming(monkeypatch):
    loop = _quarantine_loop(lambda: [_Check("fail")])
    _always_fails(monkeypatch, loop)
    loop.run()  # returns on its own — no brake needed, that is the point
    assert loop.quarantined is True
    # Three failures to reach the threshold, and then it STOPS. The bug this
    # exists to fix is a host that keeps claiming forever.
    assert loop.client.claims == 3


def test_a_healthy_host_keeps_working_because_the_JOBS_are_failing(monkeypatch):
    """Same three failures, but the machine checks out. The jobs are broken,
    not the host — reset and carry on."""
    loop = _quarantine_loop(lambda: [_Check("ok")])
    _always_fails(monkeypatch, loop)
    with pytest.raises(_Brake):
        loop.run()
    assert loop.quarantined is False
    assert loop.client.claims == 6  # kept going well past the threshold


def test_no_health_check_means_no_quarantine(monkeypatch):
    """Every existing caller passes nothing and must behave exactly as before."""
    loop = ExecutorLoop(client=_FailingClient(), node_id="n1", poll_seconds=0)
    _always_fails(monkeypatch, loop)
    with pytest.raises(_Brake):
        loop.run()
    assert loop.quarantined is False


def test_threshold_zero_disables_the_quarantine(monkeypatch):
    loop = _quarantine_loop(lambda: [_Check("fail")], threshold=0)
    _always_fails(monkeypatch, loop)
    with pytest.raises(_Brake):
        loop.run()
    assert loop.quarantined is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_loop_counters.py -k quarantine -v`
Expected: FAIL — `quarantined` never becomes True; the loop claims forever.

- [ ] **Step 3: Write the implementation**

In `ExecutorLoop.run`, replace `self.execute_one(lease)` with:

```python
            self.execute_one(lease)
            if self._should_stop_volunteering():
                break
```

and add the method above `run`:

```python
    def _should_stop_volunteering(self) -> bool:
        """After a streak of host-side failures, ask whether it is the HOST.

        A counter alone would guess. This measures: re-run the same checks
        `flashnode doctor` runs, and let the answer decide.

        - checks pass -> this machine is fine and the JOBS are failing. Say
          so, reset, keep working. A host that stops on someone else's broken
          job is a host that stops for no reason.
        - checks fail -> stop claiming. Continuing means burning this job's
          retries on a machine that cannot run anything.
        """
        if self.health_check is None or self.max_consecutive_failures <= 0:
            return False
        if self.consecutive_failures < self.max_consecutive_failures:
            return False
        results = self.health_check()
        unhealthy = [r for r in results if r.status != "ok"]
        if not unhealthy:
            log.info(_jlog(
                "consecutive task failures, but this host passes its own "
                "checks — the jobs are failing, not the machine",
                failures=self.consecutive_failures))
            self.consecutive_failures = 0
            return False
        self.quarantined = True
        self.health_report = results
        log.error(_jlog("stopping: this host can no longer run tasks",
                        failures=self.consecutive_failures,
                        failed_checks=[r.name for r in unhealthy]))
        return True
```

Add `self.health_report: list | None = None` to `__init__` beside
`quarantined`, so `cli.py` can print the detail.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_loop_counters.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```bash
git add flashnode/executor/loop.py tests/test_loop_counters.py
git commit -m "feat(loop): stop volunteering when this host fails its own checks

The counter does not decide — the doctor does. Three host-side failures
trigger the same checks the startup gate runs: healthy means the jobs are
broken, so reset; unhealthy means stop claiming and hand the owner a fix."
```

---

### Task 3: `format_status` — the pure function

**Files:**
- Create: `flashnode/status.py`
- Create: `tests/test_status.py`

**Interfaces:**
- Produces:
  - `format_status(loop, *, coordinator: str, version: str, now: float, started: float = 0.0) -> str`
  - `human_duration(seconds: float) -> str`
- Consumes: the counters from Task 1.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_status.py`:

```python
"""The status block a volunteer actually reads.

Rendering is a pure function of the loop's counters plus a clock, so it is
tested without a terminal, a thread, or a coordinator.
"""

from __future__ import annotations

from flashnode.status import format_status, human_duration


class _Loop:
    def __init__(self, **kw):
        self.tasks_accepted = 0
        self.tasks_failed = 0
        self.consecutive_failures = 0
        self.current_task = None
        self.current_attempt = None
        self.current_task_started = None
        self.quarantined = False
        self._last_node_hb = 0.0
        self.__dict__.update(kw)


def test_running_shows_the_task_and_how_long_it_has_been_going():
    text = format_status(
        _Loop(current_task="fed-2e2d4d6ab57f", current_attempt=1,
              current_task_started=100.0, tasks_accepted=12, _last_node_hb=138.0),
        coordinator="flashml-api.onrender.com", version="0.3.2", now=140.0,
    )
    assert "fed-2e2d4d6ab57f" in text
    assert "attempt 1" in text
    assert "40s" in text
    assert "12 accepted" in text
    assert "2s ago" in text


def test_idle_says_no_work_queued_is_normal():
    """The single most common worry a volunteer has, answered in the UI
    instead of in a support message."""
    text = format_status(_Loop(_last_node_hb=99.0), coordinator="c",
                         version="0.3.2", now=100.0)
    assert "no work queued" in text
    assert "normal" in text


def test_failures_are_shown_only_when_there_are_some():
    clean = format_status(_Loop(), coordinator="c", version="0.3.2", now=1.0)
    assert "0 failed" in clean
    dirty = format_status(_Loop(tasks_failed=3), coordinator="c",
                          version="0.3.2", now=1.0)
    assert "3 failed" in dirty


def test_the_coordinator_and_version_are_visible():
    text = format_status(_Loop(), coordinator="flashml-api.onrender.com",
                         version="0.3.2", now=1.0)
    assert "flashml-api.onrender.com" in text
    assert "0.3.2" in text


def test_human_duration_is_readable_at_every_scale():
    assert human_duration(9) == "9s"
    assert human_duration(75) == "1m15s"
    assert human_duration(8064) == "2h14m"


def test_a_quarantined_loop_says_so():
    text = format_status(_Loop(quarantined=True), coordinator="c",
                         version="0.3.2", now=1.0)
    assert "stopped" in text.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_status.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flashnode.status'`

- [ ] **Step 3: Write the implementation**

Create `flashnode/status.py`:

```python
"""What a host owner sees while their machine is working.

`flashnode work` used to emit only a JSON log stream, so a volunteer could
not tell contributing from quietly-failing from idle-because-nothing-is-
queued. That last one matters most: an idle agent looks identical to a
broken one, and "no work queued" is the answer to the most common worry.

Rendering is a pure function of the loop's counters plus a clock — no
terminal, no thread, no coordinator needed to test it.
"""

from __future__ import annotations

__all__ = ["format_status", "human_duration"]


def human_duration(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{(s % 3600) // 60:02d}m"


def format_status(loop, *, coordinator: str, version: str, now: float,
                  started: float = 0.0) -> str:
    lines = [
        f"flashnode {version} · {coordinator} · up {human_duration(now - started)}"
    ]
    if loop.quarantined:
        lines.append("  stopped    this machine can no longer run tasks — see below")
    elif loop.current_task:
        elapsed = human_duration(now - (loop.current_task_started or now))
        lines.append(
            f"  running    {loop.current_task}  ·  "
            f"attempt {loop.current_attempt}  ·  {elapsed}"
        )
    else:
        lines.append("  waiting    no work queued — this is normal")
    lines.append(
        f"  session    {loop.tasks_accepted} accepted   {loop.tasks_failed} failed"
    )
    lines.append(
        f"  heartbeat  {human_duration(now - loop._last_node_hb)} ago"
    )
    return "\n".join(lines)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_status.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add flashnode/status.py tests/test_status.py
git commit -m "feat(status): render the block a host owner actually reads

Idle says 'no work queued — this is normal', because an idle agent and a
broken one look identical today and that is the most common worry."
```

---

### Task 4: The renderer thread

**Files:**
- Modify: `flashnode/status.py`
- Modify: `tests/test_status.py`

**Interfaces:**
- Produces: `StatusView(loop, *, coordinator, version, stream, interval=1.0)`
  with `.start()` and `.stop()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_status.py`:

```python
import io
import threading
import time

from flashnode.status import StatusView


def test_the_view_redraws_and_stops_cleanly():
    loop = _Loop(tasks_accepted=4)
    out = io.StringIO()
    out.isatty = lambda: True
    view = StatusView(loop, coordinator="c", version="0.3.2", stream=out,
                      interval=0.01)
    view.start()
    time.sleep(0.05)
    view.stop()
    assert "4 accepted" in out.getvalue()
    assert not any(t.name == "flashnode-status" and t.is_alive()
                   for t in threading.enumerate())


def test_a_renderer_that_raises_never_takes_the_work_loop_with_it():
    """The view is cosmetic. A bug in it must not stop a machine from
    contributing — so the thread swallows its own errors and exits.

    Note the loop stand-in raises on ATTRIBUTE ACCESS rather than subclassing
    _Loop with a property: _Loop.__init__ assigns self.tasks_accepted, and
    assigning over a class-level property raises in the constructor, so the
    test would blow up before the thread ever started.
    """
    class Exploding:
        def __getattr__(self, name):
            raise RuntimeError("boom")

    out = io.StringIO()
    out.isatty = lambda: True
    view = StatusView(Exploding(), coordinator="c", version="0.3.2",
                      stream=out, interval=0.01)
    view.start()
    time.sleep(0.05)
    view.stop()  # must not raise
    assert not view._thread.is_alive()


def test_the_thread_is_a_daemon_so_it_cannot_block_shutdown():
    out = io.StringIO()
    out.isatty = lambda: True
    view = StatusView(_Loop(), coordinator="c", version="0.3.2", stream=out,
                      interval=0.01)
    view.start()
    assert view._thread.daemon is True
    view.stop()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_status.py -k view -v`
Expected: FAIL — `ImportError: cannot import name 'StatusView'`

- [ ] **Step 3: Write the implementation**

Add to `flashnode/status.py` (and `"StatusView"` to `__all__`; add
`import threading`, `import time`):

```python
class StatusView:
    """Redraws `format_status` in place on a timer, from its own thread.

    It READS the loop and never calls into it. That is the whole design: the
    executor is correctness-critical — leases, heartbeats, the checkpoint
    relay — and a cosmetic feature must not appear anywhere in its control
    flow. The thread is a daemon, swallows its own exceptions, and if it dies
    the machine keeps contributing.
    """

    def __init__(self, loop, *, coordinator: str, version: str, stream,
                 interval: float = 1.0):
        self.loop = loop
        self.coordinator = coordinator
        self.version = version
        self.stream = stream
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = time.monotonic()
        self._lines_drawn = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="flashnode-status",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._draw()
            except Exception:
                # Cosmetic. Never take the work loop down with the view.
                return
            self._stop.wait(self.interval)

    def _draw(self) -> None:
        text = format_status(self.loop, coordinator=self.coordinator,
                             version=self.version, now=time.monotonic(),
                             started=self._started)
        if self._lines_drawn:
            # Move up and clear, so the block updates in place rather than
            # scrolling. Only ever used on a TTY (cli.py gates this).
            self.stream.write(f"\033[{self._lines_drawn}A\033[J")
        self.stream.write(text + "\n")
        self.stream.flush()
        self._lines_drawn = len(text.splitlines())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_status.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add flashnode/status.py tests/test_status.py
git commit -m "feat(status): redraw from a daemon thread that reads, never drives

The executor is correctness-critical; a cosmetic feature must not appear in
its control flow. The thread swallows its own errors and exits — a broken
view must never stop a machine contributing."
```

---

### Task 5: Wire it into `flashnode work`

**Files:**
- Modify: `flashnode/agent/cli.py` (`_work`)
- Create: `tests/test_work_status.py`

**Interfaces:**
- Consumes: `StatusView` (Task 4), `run_checks` (doctor), loop params (Task 1–2).
- Produces: `--log-json`, `--max-consecutive-failures`; exit code 2 on quarantine.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_work_status.py`:

```python
"""cli.py owns every policy decision the view and the quarantine need."""

from __future__ import annotations

import pytest

from flashnode.agent.cli import _work
from flashnode.doctor import CheckResult


@pytest.fixture()
def healthy(monkeypatch):
    monkeypatch.setattr("flashnode.doctor.run_checks", lambda **kw: [])
    monkeypatch.setattr("flashnode.identity.credentials.load_token", lambda _u: None)


def test_the_loop_is_given_an_injected_health_check(monkeypatch, healthy, tmp_path):
    """loop.py must never import the doctor; cli wires it (spec 2.1.1)."""
    seen = {}
    monkeypatch.setenv("FLASHNODE_STATE_DIR", str(tmp_path))
    import flashnode.executor as executor

    class Spy:
        def __init__(self, *a, **kw):
            seen.update(kw)
        def run(self, max_tasks=None):
            return 0
        quarantined = False

    monkeypatch.setattr(executor, "ExecutorLoop", Spy)
    monkeypatch.setattr(executor.CoordinatorClient, "register", lambda self, r: None)
    _work(["--runner", "subprocess", "--coordinator", "http://localhost:1"])
    assert callable(seen["health_check"])
    assert seen["max_consecutive_failures"] == 3


def test_max_consecutive_failures_is_configurable(monkeypatch, healthy, tmp_path):
    seen = {}
    monkeypatch.setenv("FLASHNODE_STATE_DIR", str(tmp_path))
    import flashnode.executor as executor

    class Spy:
        def __init__(self, *a, **kw):
            seen.update(kw)
        def run(self, max_tasks=None):
            return 0
        quarantined = False

    monkeypatch.setattr(executor, "ExecutorLoop", Spy)
    monkeypatch.setattr(executor.CoordinatorClient, "register", lambda self, r: None)
    _work(["--runner", "subprocess", "--coordinator", "http://localhost:1",
           "--max-consecutive-failures", "7"])
    assert seen["max_consecutive_failures"] == 7


def test_a_quarantined_run_exits_2_and_prints_the_failing_checks(
    monkeypatch, healthy, tmp_path, capsys
):
    monkeypatch.setenv("FLASHNODE_STATE_DIR", str(tmp_path))
    import flashnode.executor as executor

    class Quarantined:
        quarantined = True
        health_report = [CheckResult("docker engine reachable", "fail",
                                     detail="_ping 500",
                                     fix="Start Docker Desktop.")]
        def __init__(self, *a, **kw):
            pass
        def run(self, max_tasks=None):
            return 0

    monkeypatch.setattr(executor, "ExecutorLoop", Quarantined)
    monkeypatch.setattr(executor.CoordinatorClient, "register", lambda self, r: None)
    rc = _work(["--runner", "subprocess", "--coordinator", "http://localhost:1"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Start Docker Desktop." in err


def test_no_status_view_when_stdout_is_not_a_tty(monkeypatch, healthy, tmp_path):
    """Redrawing with ANSI into a pipe or a systemd journal is corruption."""
    monkeypatch.setenv("FLASHNODE_STATE_DIR", str(tmp_path))
    started = []
    monkeypatch.setattr("flashnode.status.StatusView.start",
                        lambda self: started.append(True))
    import flashnode.executor as executor
    monkeypatch.setattr(executor, "ExecutorLoop",
                        type("L", (), {"__init__": lambda s, *a, **k: None,
                                       "run": lambda s, max_tasks=None: 0,
                                       "quarantined": False}))
    monkeypatch.setattr(executor.CoordinatorClient, "register", lambda self, r: None)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    _work(["--runner", "subprocess", "--coordinator", "http://localhost:1"])
    assert started == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_work_status.py -v`
Expected: FAIL — `unrecognized arguments: --max-consecutive-failures`

- [ ] **Step 3: Write the implementation**

In `_work`, add after the `--poll-seconds` argument:

```python
    parser.add_argument(
        "--log-json", action="store_true",
        help="keep the machine-readable JSON log instead of the live status view",
    )
    parser.add_argument(
        "--max-consecutive-failures", type=int,
        default=int(os.environ.get("FLASHNODE_MAX_CONSECUTIVE_FAILURES", "3")),
        help="host-side failures in a row before re-checking this machine "
             "and stopping if it is broken (0 disables)",
    )
```

Pass the two new params where `ExecutorLoop` is constructed:

```python
    from flashnode.doctor import run_checks

    loop = ExecutorLoop(
        client, node_id, runner=runner,
        poll_seconds=opts.poll_seconds, workdir_base=workdir_base,
        registration=registration,
        # Injected, never imported by loop.py — see doctor.py's note and the
        # spec's §2.1.1. pull=False for the same reason the startup gate uses
        # it: a registry blip must not stop a working agent.
        health_check=lambda: run_checks(pull=False),
        max_consecutive_failures=opts.max_consecutive_failures,
    )
```

Then, replacing `accepted = loop.run(max_tasks=opts.max_tasks)`:

```python
    view = None
    if sys.stdout.isatty() and not opts.log_json:
        # Two writers redrawing one terminal is unreadable, so the JSON
        # handler goes when the view arrives. --log-json keeps it.
        from flashnode.status import StatusView

        logging.getLogger().handlers.clear()
        view = StatusView(loop, coordinator=opts.coordinator,
                          version=__version__, stream=sys.stdout)
        view.start()
    try:
        accepted = loop.run(max_tasks=opts.max_tasks)
    finally:
        if view is not None:
            view.stop()

    if getattr(loop, "quarantined", False):
        from flashnode.doctor import format_results

        print(
            "\nflashnode work: stopping — this machine can no longer run "
            "tasks.\n" + format_results(loop.health_report or [])
            + "\n\nFix the above, then `flashnode doctor` to confirm before "
              "restarting.",
            file=sys.stderr,
        )
        return 2
    print(f"flashnode work: {accepted} task(s) accepted", file=sys.stderr)
    return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_work_status.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m pytest`
Expected: 277 passed.

- [ ] **Step 6: See it for real**

Run, in one terminal:
`FLASHNODE_WORKDIR=$HOME/.cache/fn .venv/bin/python -m flashnode.agent.cli work --coordinator http://localhost:8100`

With no coordinator running it should show the block, report the coordinator
as unreachable in the log, and redraw in place rather than scrolling. Ctrl-C
must exit cleanly with the terminal usable.

- [ ] **Step 7: Commit**

```bash
git add flashnode/agent/cli.py tests/test_work_status.py
git commit -m "feat(work): live status view on a TTY, and wire the quarantine

cli owns the policy: TTY detection, the threshold, the injected health check
and the exit code. --log-json keeps the old stream for anything machine-read."
```

---

### Task 6: Docs, release 0.3.2, and the pin

**Files:**
- Modify: `flashnode/README.md`, `flashnode/AGENTS.md`, `flashnode/pyproject.toml`
- Modify: `~/Work/Zolli-Labs/flashml-cloud/Makefile` (`NODE_VERSION`)
- Modify: `~/Work/Zolli-Labs/flashml-cloud/PROGRESS.md`

- [ ] **Step 1: README**

Under the doctor section, add:

```markdown
While `flashnode work` runs on a terminal you get a live status block —
what it is running, how many tasks this session, when it last checked in.
"waiting · no work queued" is normal and means the pool has nothing for you
right now. Pipe the output anywhere, or pass `--log-json`, and you get the
machine-readable log instead.

If three tasks in a row fail on your machine, the agent re-runs its own
checks. If they pass, the jobs were broken and it carries on. If they fail,
it stops claiming and tells you what to fix — rather than burning a job's
retries on a machine that cannot run anything.
```

- [ ] **Step 2: AGENTS.md**

Extend the host-doctor paragraph with the mid-session half, and **delete the
"NOT covered: mid-session breakage" sentence** — it is no longer true of the
agent, only of the coordinator:

```
Mid-session: `ExecutorLoop` counts consecutive TaskExecutionErrors ONLY
(LeaseLost and a 200 with {"accepted": false} are not the host's fault) and
at the threshold runs an INJECTED health_check — loop.py must never import
doctor.py, which would close a loop -> doctor -> executor -> loop import
cycle. Healthy means the jobs are failing; unhealthy sets `quarantined` and
stops claiming. Coordinator-side quarantine is still absent.
```

- [ ] **Step 3: Version and release**

`flashnode/pyproject.toml`: `version = "0.3.2"`.

```bash
cd ~/Work/Zolli-Labs/flashml
git tag flashnode-v0.3.2 && git push origin main --tags
gh run watch
```

- [ ] **Step 4: Move the pin and verify against the published wheel**

`~/Work/Zolli-Labs/flashml-cloud/Makefile`: `NODE_VERSION := 0.3.2`.

```bash
cd ~/Work/Zolli-Labs/flashml-cloud
VIRTUAL_ENV=e2e/.venv uv pip install --refresh "flashruntime[service,sklearn,dev]==0.4.0" "flashnode==0.3.2"
make -B e2e
```

`--refresh` because PyPI's simple index lags its JSON API by a few minutes;
`-B` because `e2e` is a directory and is not in `.PHONY`, so make otherwise
reports it up to date and runs nothing. Expected: **61 passed**.

- [ ] **Step 5: Log it**

New `PROGRESS.md` entry at the top of `## Entries`, per the protocol: real
counts, the root cause of anything found, one Next. Record that only
`TaskExecutionError` counts and why, and that `loop.py` must not import
`doctor.py`.

- [ ] **Step 6: Commit both repos**

```bash
cd ~/Work/Zolli-Labs/flashml
git add flashnode/README.md flashnode/AGENTS.md flashnode/pyproject.toml
git commit -m "docs(flashnode): status view and self-quarantine; release 0.3.2"

cd ~/Work/Zolli-Labs/flashml-cloud
git add Makefile PROGRESS.md
git commit -m "chore: pin flashnode 0.3.2; log the status view and quarantine"
```

---

## Post-plan follow-ups (NOT in this plan)

1. **Coordinator-side quarantine** — the agent stops volunteering; only the
   coordinator can stop routing.
2. **Contribution total in the view** — needs a cloud-API endpoint for a
   machine to read its own accepted work; `public.contributions` already has
   the rows.
3. **The admission probes** (`benchmark/`) and **telemetry on the heartbeat**
   (needs an additive `NodeHeartbeat.telemetry` field) — sequence against the
   GPU protocol work now on `main`.
