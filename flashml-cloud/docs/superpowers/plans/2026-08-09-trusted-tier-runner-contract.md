# Trusted-Tier Runner Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the runner tier own its own health checks and its own child
environment, so a `--runner trusted` host stops quarantining itself over
Docker and stops losing its workload's output.

**Architecture:** Each runner class gains two tier-dependent methods —
`health_checks()` and `task_env(workdir)`. `flashnode work` calls the
runner's `health_checks` for both the startup gate and the loop's
post-failure re-check, sharing one bound method so the two cannot disagree.
The no-container runners add `FLASHML_WORK_DIR` to the child environment,
which the containerised runners continue to supply as a bind mount.

**Tech Stack:** Python 3.11+, pytest, `flashnode` package only. No protocol
change, no coordinator change, no `flashml-cloud` change.

**Spec:** `docs/superpowers/specs/2026-08-09-trusted-tier-execution-contract-design.md`
§2 and §3. §4 and §5 are a separate plan
(`2026-08-09-declared-dependencies-and-environments.md`) and are **not** in
scope here.

**Repo:** all paths are relative to `~/Work/Zolli-Labs/flashml/flashnode`.

**Test command:** `python -m pytest tests/ -v` from that directory. If no
dev install is present, `~/Work/Zolli-Labs/flashml-cloud/e2e/.venv/bin/python -m pytest`
has `flashnode` installed editable.

## Two deliberate deviations from the spec

Both are narrower than what §2.2 describes. Flagged rather than silently
taken.

1. **No formal `Runner` Protocol class.** The spec sketches
   `class Runner(Protocol)`. This plan adds the two methods directly to the
   four existing runner classes instead. There is no static type checker in
   this repo's CI, so a `Protocol` would be documentation with an import
   cost and no enforcement. Add it if a checker lands.
2. **The host-exec check set is two checks, not three.** §2.2 lists
   "interpreter present, workdir writable, disk headroom". Disk headroom is
   implemented in the *next* plan, where the number it compares against
   (`FLASHNODE_ENV_BUDGET_GB`) is defined. Shipping it here would mean
   inventing a threshold with nothing to measure it for.

## Global Constraints

- One package. Never add a trusted-only build, module split, or extra
  distribution — the 2026-08-01 topology rule forbids a second copy.
- `_TASK_ENV_WHITELIST` in `flashnode/executor/runner.py:40` **must not be
  extended.** It governs what the host's environment may leak into workload
  code. `FLASHML_WORK_DIR` is the agent's own contract, set explicitly.
- Containerised tiers (`DockerRunner`, `ArgvDockerRunner`) must end this
  plan behaving byte-identically: same checks, same child environment.
- The startup gate and the loop's re-check must resolve the tier through
  **one** expression. Two conditionals that happen to agree is the bug being
  fixed, not the fix.
- `pull=False` stays the `flashnode work` path for docker-tier checks: a
  transient registry blip must not stop an agent whose images are cached.
- Blocking status set stays `NON_BLOCKING_STATUSES = {"ok", "info"}`. The
  GPU check reports `info` and must never gate anything.

---

### Task 1: Tier-scoped check sets

**Files:**
- Create: `flashnode/executor/health.py`
- Create: `tests/test_tier_health.py`

**Interfaces:**
- Consumes: `flashnode.doctor.CheckResult`, `flashnode.doctor.run_checks`,
  `flashnode.doctor.default_workdir`.
- Produces:
  - `docker_tier_checks(*, workdir: Path | None = None) -> list[CheckResult]`
  - `host_exec_tier_checks(*, workdir: Path | None = None, interpreter: str = "python", which: Callable[[str], str | None] | None = None) -> list[CheckResult]`
  - `check_interpreter_present(name: str, *, which=None) -> CheckResult`
  - `check_workdir_writable(workdir: Path) -> CheckResult`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tier_health.py`:

```python
"""Which doctor checks gate which runner tier.

A pod and a Colab notebook are already containers and cannot nest a Docker
daemon. Running the docker checks against them fails by definition — and
before this module, that failure quarantined the host and blamed Docker.
"""

from __future__ import annotations

from pathlib import Path

from flashnode.executor.health import (
    check_interpreter_present,
    check_workdir_writable,
    docker_tier_checks,
    host_exec_tier_checks,
)


def test_host_exec_tier_runs_no_docker_check(tmp_path):
    names = [r.name for r in host_exec_tier_checks(
        workdir=tmp_path, which=lambda _n: "/usr/bin/python")]
    assert not any("docker" in n.lower() for n in names)


def test_host_exec_tier_passes_on_a_writable_dir_with_an_interpreter(tmp_path):
    results = host_exec_tier_checks(
        workdir=tmp_path, which=lambda _n: "/usr/bin/python")
    assert all(r.status in ("ok", "info") for r in results), results


def test_host_exec_tier_fails_when_the_interpreter_is_missing(tmp_path):
    results = host_exec_tier_checks(workdir=tmp_path, which=lambda _n: None)
    bad = [r for r in results if r.status == "fail"]
    assert len(bad) == 1
    assert "python" in bad[0].detail
    assert bad[0].fix


def test_host_exec_tier_fails_on_an_unwritable_workdir(tmp_path):
    target = tmp_path / "nope"
    target.mkdir()
    target.chmod(0o500)
    try:
        results = host_exec_tier_checks(
            workdir=target, which=lambda _n: "/usr/bin/python")
        assert any(r.status == "fail" for r in results)
    finally:
        target.chmod(0o700)


def test_interpreter_check_reports_the_resolved_path(tmp_path):
    r = check_interpreter_present("python3", which=lambda _n: "/usr/bin/python3")
    assert r.status == "ok"
    assert "/usr/bin/python3" in r.detail


def test_workdir_check_leaves_nothing_behind(tmp_path):
    before = set(tmp_path.iterdir())
    assert check_workdir_writable(tmp_path).status == "ok"
    assert set(tmp_path.iterdir()) == before


def test_docker_tier_still_delegates_to_run_checks(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr("flashnode.executor.health.run_checks",
                        lambda **kw: seen.update(kw) or [])
    docker_tier_checks(workdir=tmp_path)
    assert seen["pull"] is False
    assert seen["workdir"] == tmp_path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tier_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flashnode.executor.health'`

- [ ] **Step 3: Write minimal implementation**

Create `flashnode/executor/health.py`:

```python
"""Which doctor checks gate which runner tier.

The docker checks are not universal. A pod or a Colab notebook is already a
container and cannot nest a second Docker daemon — that is the entire reason
`--runner trusted` exists. Running the docker set against such a host fails
by definition, and before this module that failure reached
`ExecutorLoop.health_check` and quarantined the host with `docker CLI on
PATH`: a verdict that is true, useless, and impossible to act on.

Each tier names its own set here. `flashnode/agent/cli.py` binds one of them
to the runner it built, and uses that ONE binding for both the startup gate
and the loop's post-failure re-check.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path

from flashnode.doctor import CheckResult, default_workdir, run_checks

#: What a no-container tier invokes when the compiled argv says `python`.
DEFAULT_INTERPRETER = "python"


def check_interpreter_present(
    name: str, *, which: Callable[[str], str | None] | None = None
) -> CheckResult:
    """The no-container tiers exec the workload directly, so the interpreter
    the compiled argv names has to exist on PATH. Resolved at call time, not
    import time, so a test can substitute it."""
    which = which or shutil.which
    found = which(name)
    if not found:
        return CheckResult(
            "task interpreter on PATH",
            "fail",
            detail=f"`{name}` was not found on PATH",
            fix=(f"Install {name}, or make sure the interpreter this host "
                 f"should run tasks with is named `{name}` on PATH."),
        )
    return CheckResult("task interpreter on PATH", "ok", detail=found)


def check_workdir_writable(workdir: Path) -> CheckResult:
    """No bind mount to verify here — the workdir IS a host directory. What
    matters is only that the agent can create task directories in it."""
    probe = Path(workdir) / ".flashnode-write-probe"
    try:
        Path(workdir).mkdir(parents=True, exist_ok=True)
        probe.write_text("ok")
    except OSError as exc:
        return CheckResult(
            "workdir writable",
            "fail",
            detail=f"{workdir}: {exc}",
            fix=("Set FLASHNODE_WORKDIR to a directory this account can "
                 "write to, then re-run `flashnode doctor`."),
        )
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    return CheckResult("workdir writable", "ok", detail=str(workdir))


def docker_tier_checks(*, workdir: Path | None = None) -> list[CheckResult]:
    """`--runner docker` and `--runner argv`. Unchanged behaviour: the full
    container set, without the pull (a registry blip must not stop an agent
    whose images are already cached)."""
    return run_checks(pull=False, workdir=workdir)


def host_exec_tier_checks(
    *,
    workdir: Path | None = None,
    interpreter: str = DEFAULT_INTERPRETER,
    which: Callable[[str], str | None] | None = None,
) -> list[CheckResult]:
    """`--runner trusted` and `--runner subprocess`. No container, so no
    docker check has anything to say."""
    base = workdir if workdir is not None else default_workdir()
    return [
        check_interpreter_present(interpreter, which=which),
        check_workdir_writable(base),
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tier_health.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add flashnode/executor/health.py tests/test_tier_health.py
git commit -m "feat(flashnode): tier-scoped health check sets

The docker checks are not universal. A pod cannot nest a Docker daemon,
which is why --runner trusted exists; running the docker set against one
fails by definition."
```

---

### Task 2: Runners answer for their own tier

**Files:**
- Modify: `flashnode/executor/trusted_runner.py` (add `health_checks`)
- Modify: `flashnode/executor/runner.py` (`SubprocessRunner.health_checks`)
- Modify: `flashnode/executor/docker_runner.py` (`DockerRunner.health_checks`)
- Modify: `flashnode/executor/argv_runner.py` (`ArgvDockerRunner.health_checks`)
- Modify: `tests/test_tier_health.py`

**Interfaces:**
- Consumes: `docker_tier_checks`, `host_exec_tier_checks` from Task 1.
- Produces: every runner has
  `health_checks(self, *, workdir: Path | None = None) -> list[CheckResult]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tier_health.py`:

```python
def test_every_runner_answers_for_its_own_tier(tmp_path, monkeypatch):
    from flashnode.executor.argv_runner import ArgvDockerRunner
    from flashnode.executor.docker_runner import DockerRunner
    from flashnode.executor.runner import SubprocessRunner
    from flashnode.executor.trusted_runner import TrustedArgvRunner

    monkeypatch.setattr("flashnode.executor.health.run_checks",
                        lambda **kw: [CheckResult("docker CLI on PATH", "ok")])

    for runner in (DockerRunner(), ArgvDockerRunner()):
        names = [r.name for r in runner.health_checks(workdir=tmp_path)]
        assert "docker CLI on PATH" in names

    for runner in (TrustedArgvRunner(), SubprocessRunner()):
        names = [r.name for r in runner.health_checks(workdir=tmp_path)]
        assert not any("docker" in n.lower() for n in names)
```

Add `from flashnode.doctor import CheckResult` to the test file's imports.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tier_health.py::test_every_runner_answers_for_its_own_tier -v`
Expected: FAIL — `AttributeError: 'DockerRunner' object has no attribute 'health_checks'`

- [ ] **Step 3: Write minimal implementation**

In `flashnode/executor/trusted_runner.py` and `flashnode/executor/runner.py`
(`SubprocessRunner`), add this method to each class:

```python
    def health_checks(self, *, workdir: Path | None = None) -> list[CheckResult]:
        """No container, so no docker check applies — see executor/health.py."""
        from flashnode.executor.health import host_exec_tier_checks

        return host_exec_tier_checks(workdir=workdir)
```

In `flashnode/executor/docker_runner.py` and
`flashnode/executor/argv_runner.py`, add to each class:

```python
    def health_checks(self, *, workdir: Path | None = None) -> list[CheckResult]:
        """The full container set — this tier really does need Docker."""
        from flashnode.executor.health import docker_tier_checks

        return docker_tier_checks(workdir=workdir)
```

Add `from pathlib import Path` to each file's imports where absent, and
`from flashnode.doctor import CheckResult` to `docker_runner.py`,
`argv_runner.py` and `trusted_runner.py` — those three are off the cycle.

**`runner.py` is the exception, and a plain top-level import there fails at
import time.** `doctor.py:32` imports `executor/hardening.py`, which imports
`TaskExecutionError` from `runner.py` — so `from flashnode.doctor import
CheckResult` at module scope in `runner.py` closes
`runner → doctor → hardening → runner` and raises `ImportError` on `import
flashnode.executor.runner`. Guard it there and only there:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # runner -> doctor -> hardening -> runner closes at import time; the
    # `from __future__ import annotations` above already makes the
    # `-> list[CheckResult]` annotation a string at runtime, so this import
    # exists only for type checkers.
    from flashnode.doctor import CheckResult
```

Do **not** apply the guard uniformly to the other three — that is
indirection with no functional benefit in files that never close a cycle.

Import `health` inside the method body in all four, not at module scope:
`health` imports `doctor`, and a module-level import would recreate the
`loop → doctor → executor → loop` cycle the current code comments already
worry about.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tier_health.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add flashnode/executor/
git commit -m "feat(flashnode): each runner answers health_checks for its tier"
```

---

### Task 3: One binding for the gate and the re-check

**Files:**
- Modify: `flashnode/agent/cli.py:247-262` (startup gate)
- Modify: `flashnode/agent/cli.py:326-342` (`_blocking_problems`)
- Modify: `tests/test_work_gate.py`

**Interfaces:**
- Consumes: `runner.health_checks` from Task 2.
- Produces: no new symbols. `_work` resolves the tier exactly once.

This is the task that fixes the observed self-quarantine. The two call
sites currently each call `run_checks()` and each decide, separately, which
tier they are in — and only one of them decides correctly.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_work_gate.py`:

```python
def test_trusted_host_with_no_docker_is_not_gated_at_startup(monkeypatch):
    """The startup gate already exempted this tier; pin it so the refactor
    cannot regress it."""
    constructed = {}

    def fake_loop(*a, **k):
        constructed.update(k)
        raise SystemExit(0)

    monkeypatch.setattr("flashnode.doctor.run_checks",
                        lambda **kw: [CheckResult("docker CLI on PATH", "fail",
                                                  detail="not found", fix="Install Docker.")])
    monkeypatch.setattr("flashnode.executor.ExecutorLoop", fake_loop)
    monkeypatch.setattr("flashnode.identity.credentials.load_token",
                        lambda _c: "tok")
    with pytest.raises(SystemExit):
        _work(["--runner", "trusted", "--coordinator", "http://localhost:8100"])
    assert "health_check" in constructed


def test_trusted_host_re_check_does_not_report_docker(monkeypatch):
    """The bug: three failures of any cause quarantined a trusted host and
    named `docker CLI on PATH`, a check this tier is exempt from."""
    captured = {}

    def fake_loop(*a, **k):
        captured["health_check"] = k["health_check"]
        raise SystemExit(0)

    monkeypatch.setattr("flashnode.doctor.run_checks",
                        lambda **kw: [CheckResult("docker CLI on PATH", "fail",
                                                  detail="not found", fix="Install Docker.")])
    monkeypatch.setattr("flashnode.executor.ExecutorLoop", fake_loop)
    monkeypatch.setattr("flashnode.identity.credentials.load_token",
                        lambda _c: "tok")
    with pytest.raises(SystemExit):
        _work(["--runner", "trusted", "--coordinator", "http://localhost:8100"])

    problems = captured["health_check"]()
    assert not any("docker" in p.name.lower() for p in problems), problems
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_work_gate.py -v`
Expected: `test_trusted_host_re_check_does_not_report_docker` FAILS — the
returned problems contain `docker CLI on PATH`.

- [ ] **Step 3: Write minimal implementation**

In `flashnode/agent/cli.py`, replace the startup gate's
`results = run_checks(pull=False)` with a call through the runner, and move
the gate so it runs **after** the runner is constructed for every tier:

```python
    # The tier is resolved ONCE, here, by asking the runner we just built.
    # Two call sites each deciding their own tier is precisely the bug this
    # replaced: the startup gate exempted `trusted` and the loop's
    # post-failure re-check did not, so three failures of any cause
    # quarantined a pod and blamed Docker for it.
    from flashnode.doctor import NON_BLOCKING_STATUSES, format_results

    def _blocking_problems() -> list:
        """What the loop calls after a streak of host-side failures.

        Bound to the same runner as the startup gate below, so the two
        cannot disagree about which tier this host is in. Filtered with
        NON_BLOCKING_STATUSES so the informational GPU check never
        quarantines a CPU-only volunteer.
        """
        return [r for r in runner.health_checks(workdir=workdir_base)
                if r.status not in NON_BLOCKING_STATUSES]

    startup_problems = _blocking_problems()
    if startup_problems:
        print(
            f"flashnode work: this machine cannot run tasks with "
            f"--runner {opts.runner}.\n" + format_results(startup_problems)
            + "\n\nRun `flashnode doctor` for the full check, including "
              "the image pull this skipped.",
            file=sys.stderr,
        )
        return 2
```

Delete the old `results = run_checks(pull=False)` block inside the
`("docker", "argv")` branch and the old `_blocking_problems` definition near
line 326. Move `workdir_base = os.environ.get("FLASHNODE_WORKDIR") or None`
above this block so both uses see it.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_work_gate.py tests/test_cli_trusted.py -v`
Expected: PASS. The pre-existing docker-tier gate tests must still pass
unchanged — that is the byte-identical-behaviour constraint.

- [ ] **Step 5: Commit**

```bash
git add flashnode/agent/cli.py tests/test_work_gate.py
git commit -m "fix(flashnode): trusted hosts no longer quarantine themselves over Docker

The startup gate exempted --runner trusted; the loop's post-failure
re-check did not. Three failures of any cause killed a pod and reported
'docker CLI on PATH'. Both now share one binding to the runner's own
check set."
```

---

### Task 4: Deliver `FLASHML_WORK_DIR` to no-container tasks

**Files:**
- Modify: `flashnode/executor/trusted_runner.py:63` (the `run_capturing` call)
- Modify: `flashnode/executor/runner.py` (`SubprocessRunner`, same change)
- Modify: `flashnode/executor/runner.py:37-40` (whitelist comment)
- Modify: `tests/test_trusted_runner.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Runner.task_env(self, workdir: Path) -> dict[str, str]` on
  `TrustedArgvRunner` and `SubprocessRunner`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_trusted_runner.py`:

```python
def test_trusted_runner_tells_the_workload_where_work_is(tmp_path):
    """`/work` is a naming convention for this tier, not a mount. A workload
    that resolves its own paths from FLASHML_WORK_DIR (as the federated
    example does) gets the real directory, not a literal /work it would
    create on the host's root as a side effect of running as root."""
    runner = TrustedArgvRunner()
    script = tmp_path / "w.py"
    script.write_text(
        "import json, os, pathlib\n"
        "work = pathlib.Path(os.environ['FLASHML_WORK_DIR'])\n"
        "out = work / 'out'\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'metrics.json').write_text(json.dumps({'samples': 1}))\n"
    )
    workdir = tmp_path / "task"
    workdir.mkdir()
    outdir = runner.run({"argv": ["python", str(script)]}, workdir, {})
    assert (outdir / "metrics.json").is_file()


def test_trusted_runner_env_is_the_whitelist_plus_workdir(tmp_path):
    runner = TrustedArgvRunner()
    env = runner.task_env(tmp_path)
    assert env["FLASHML_WORK_DIR"] == str(tmp_path)
    assert "FLASHNODE_COORDINATOR_URL" not in env


def test_the_whitelist_itself_is_not_widened():
    """FLASHML_WORK_DIR is set explicitly, never inherited. Adding it to the
    whitelist would let any host environment forge it."""
    from flashnode.executor.runner import _TASK_ENV_WHITELIST

    assert "FLASHML_WORK_DIR" not in _TASK_ENV_WHITELIST
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trusted_runner.py -v`
Expected: FAIL — `KeyError: 'FLASHML_WORK_DIR'` in the workload, and
`AttributeError: 'TrustedArgvRunner' object has no attribute 'task_env'`.

- [ ] **Step 3: Write minimal implementation**

In `flashnode/executor/trusted_runner.py`, add the method and use it:

```python
    def task_env(self, workdir: Path) -> dict[str, str]:
        """The inherited whitelist, plus where `/work` actually is.

        This tier runs no container, so `/work` is a naming convention the
        argv rewrite honours — but a workload that resolves its own paths
        never passes through argv. Without this, such a workload writes to a
        literal `/work` on the host (silently succeeding as root) and the
        attempt fails with "produced no metrics.json".

        Set explicitly rather than added to _TASK_ENV_WHITELIST: the
        whitelist governs what the HOST's environment may leak into workload
        code, and this is the agent's own contract with the workload.
        """
        return {**task_env(), "FLASHML_WORK_DIR": str(workdir)}
```

Then change the `run_capturing` call from `env=task_env()` to
`env=self.task_env(workdir)`.

Apply the identical method and call-site change to `SubprocessRunner` in
`flashnode/executor/runner.py`.

Extend the comment above `_TASK_ENV_WHITELIST`:

```python
# The only environment a task subprocess INHERITS. Everything else — join
# codes, coordinator URLs, cloud credentials — is the *agent's* business and
# must never leak into workload code.
#
# Note what this list is not: variables the agent SETS for the workload on
# purpose (FLASHML_WORK_DIR) are added by the runner after this filter, not
# listed here. Putting them here would mean a host's own environment could
# forge them. The distinction matters — conflating the two is what let a
# no-container tier run for weeks writing its outputs to the wrong
# directory.
_TASK_ENV_WHITELIST = ("PATH", "HOME", "PYTHONPATH", "LANG", "LC_ALL", "TMPDIR")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trusted_runner.py tests/test_executor.py -v`
Expected: PASS. `test_executor.py` covers the docker tiers and must be
untouched — they still mount `/work` and set no variable.

- [ ] **Step 5: Commit**

```bash
git add flashnode/executor/trusted_runner.py flashnode/executor/runner.py tests/test_trusted_runner.py
git commit -m "fix(flashnode): no-container tiers tell the workload where /work is

A workload resolving FLASHML_WORK_DIR itself (the federated example) wrote
to a literal /work on the host, succeeded as root, and failed the attempt
with 'produced no metrics.json'."
```

---

### Task 5: Tier-correct advice

**Files:**
- Modify: `flashnode/agent/cli.py:136-140` (post-login hint)
- Modify: `flashnode/doctor.py:503-506` (post-doctor hint)
- Modify: `tests/test_cli_trusted.py`

**Interfaces:**
- Consumes: `docker_tier_checks` results from Task 1.
- Produces: `suggested_runner(results: list[CheckResult]) -> str` in
  `flashnode/executor/health.py`.

Both hints hardcode `--runner docker`. It is the first instruction a pod
operator receives and the first one that is wrong.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli_trusted.py`:

```python
def test_hint_suggests_trusted_when_docker_is_absent():
    from flashnode.doctor import CheckResult
    from flashnode.executor.health import suggested_runner

    assert suggested_runner(
        [CheckResult("docker CLI on PATH", "fail", detail="not found")]
    ) == "trusted"


def test_hint_suggests_docker_when_docker_works():
    from flashnode.doctor import CheckResult
    from flashnode.executor.health import suggested_runner

    assert suggested_runner(
        [CheckResult("docker CLI on PATH", "ok", detail="/usr/bin/docker")]
    ) == "docker"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_trusted.py -v`
Expected: FAIL — `ImportError: cannot import name 'suggested_runner'`

- [ ] **Step 3: Write minimal implementation**

Add to `flashnode/executor/health.py`:

```python
def suggested_runner(results: list[CheckResult]) -> str:
    """Which `--runner` to advise, from what the docker checks found.

    A host with no Docker is a pod or a notebook: telling it to run
    `--runner docker` is advice that cannot be followed, and it is the first
    thing the agent says after a successful enrolment.
    """
    for r in results:
        if r.name == "docker CLI on PATH" and r.status == "fail":
            return "trusted"
    return "docker"
```

In `flashnode/agent/cli.py:136-140`, replace the hardcoded string:

```python
    from flashnode.executor.health import docker_tier_checks, suggested_runner

    hint = suggested_runner(docker_tier_checks())
    print(
        "Start contributing with:  "
        f"flashnode work --runner {hint} --coordinator {opts.coordinator}"
    )
```

In `flashnode/doctor.py:503-506`, the results are already in hand:

```python
    else:
        from flashnode.executor.health import suggested_runner

        lines.append("All checks passed. Start contributing with "
                     f"`flashnode work --runner {suggested_runner(results)}`.")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/ -v`
Expected: PASS, the whole suite.

- [ ] **Step 5: Commit**

```bash
git add flashnode/executor/health.py flashnode/agent/cli.py flashnode/doctor.py tests/test_cli_trusted.py
git commit -m "fix(flashnode): suggest the runner this host can actually use"
```

---

### Task 6: End-to-end proof on a no-Docker host

**Files:**
- Create: `tests/test_trusted_tier_endtoend.py`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing.

This reproduces the origin session in one test: a host with no Docker, a
workload that resolves its own `/work`, and a failure streak that must not
quarantine anyone.

- [ ] **Step 1: Write the failing test**

Create `tests/test_trusted_tier_endtoend.py`:

```python
"""The 2026-08-09 RunPod session, as a test.

A pod enrolled, claimed three tasks, failed them all, and shut itself down
reporting `docker CLI on PATH` — on the one tier that is exempt from that
check by design.
"""

from __future__ import annotations

import json

from flashnode.doctor import NON_BLOCKING_STATUSES
from flashnode.executor.trusted_runner import TrustedArgvRunner


def test_a_pod_shaped_host_is_healthy_and_completes_a_task(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which",
                        lambda n: None if n == "docker" else f"/usr/bin/{n}")

    runner = TrustedArgvRunner()
    problems = [r for r in runner.health_checks(workdir=tmp_path)
                if r.status not in NON_BLOCKING_STATUSES]
    assert problems == [], problems

    script = tmp_path / "train.py"
    script.write_text(
        "import json, os, pathlib\n"
        "work = pathlib.Path(os.environ.get('FLASHML_WORK_DIR', '/work'))\n"
        "out = work / 'out'\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'metrics.json').write_text(json.dumps({'samples': 3}))\n"
    )
    workdir = tmp_path / "task-000"
    workdir.mkdir()
    outdir = runner.run({"argv": ["python", str(script)]}, workdir, {})

    assert json.loads((outdir / "metrics.json").read_text())["samples"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trusted_tier_endtoend.py -v`
Expected: PASS if Tasks 1-4 are complete. If run before them, FAIL on the
missing `health_checks` attribute — which is the point of writing it last.

- [ ] **Step 3: No implementation needed**

This task is a regression lock over work already done. If it fails, the
defect is in Tasks 1-4; fix there, not here.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest tests/ -v`
Expected: PASS, everything.

- [ ] **Step 5: Commit**

```bash
git add tests/test_trusted_tier_endtoend.py
git commit -m "test(flashnode): lock the 2026-08-09 pod failure as a regression"
```

---

## Manual verification

The unit tests cannot prove the pod works, because no pod is involved. After
Task 6, verify against the real thing:

1. `cloudflared tunnel --url http://localhost:8000` on the Mac; keep it up.
2. On the pod: `pip install -e` the branch, or `pip install flashnode==<rc>`.
3. `flashnode login --coordinator <tunnel>` and approve at
   `/activate?pool=<pool-id>` — the `?pool=` is what binds the machine.
4. `flashnode work --coordinator <tunnel> --runner trusted`
5. Submit `Zolli-Labs/flashml-example-federated`.
6. Expected: tasks complete on the pod. `curl <coordinator>/v1alpha1/jobs/<id>/tasks`
   shows `COMPLETED` with the pod's `node_id`.

The pod still needs `torch` installed by hand at this point — declared
dependencies are the *next* plan. What this plan proves is that the pod
stops quarantining itself and stops losing its output.

## What this plan does not do

- Declared dependencies, cached environments, `argv[0]` substitution, and
  the per-job cooldown — spec §4 and §5, in
  `2026-08-09-declared-dependencies-and-environments.md`.
- The RunPod template image and the console's hardcoded `localhost:8000` —
  spec §7, out of scope entirely.
