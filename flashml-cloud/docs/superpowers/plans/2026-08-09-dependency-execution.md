# Dependency Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a host act on the dependency list its tasks carry — build the
environment, run the workload in it, refuse honestly when it cannot, and let
its owner see and reclaim the disk.

**Architecture:** Nodes advertise `can_install_dependencies`; the scheduler
refuses to place dependency-bearing tasks on hosts that cannot. On a capable
host, `EnvironmentCache` builds a venv keyed by the hash of the resolved
list, the no-container runners invoke that venv's interpreter, and a build
failure becomes an attributable task failure that is not retried for the
same job.

**Tech Stack:** Python 3.11+, pytest, pydantic. `uv` when present, `venv` +
`pip` otherwise.

**Spec:** `docs/superpowers/specs/2026-08-09-dependency-provisioning-design.md`
§3 and §6, plus §4.2/§4.3/§5 of
`2026-08-09-trusted-tier-execution-contract-design.md` (whose §4.1 is
superseded).

**Prerequisite:** `2026-08-09-dependency-declaration.md` merged, so tasks
actually carry `payload["dependencies"]`. Tasks 1-2 below can be built
against hand-constructed payloads without it; Tasks 3-6 want it.

**Repo:** all tasks are in `flashml` (`~/Work/Zolli-Labs/flashml`).
`flashruntime/` for Task 2, `flashnode/` for the rest.

**Test commands.** `flashnode/.venv/bin/python -m pytest tests/ -v` from
`flashnode/`; `python -m pytest tests/ -v` from `flashruntime/`. Record both
baselines before starting.

## Global Constraints

- **An absent or empty `dependencies` payload key changes nothing.** No
  capability check, no venv, no interpreter substitution. Every job deployed
  today takes that path and must stay byte-identical.
- **The host never invents a requirement.** It installs the resolved list
  and nothing else — no host-side requirements file, no index selection, no
  fallback.
- **Containerised tiers never build environments.** They get their
  environment from the image; a venv there would install a second copy of
  what the image already has.
- `can_install_dependencies` defaults to **False** — fail closed, matching
  `unsandboxed_argv_capable`. A node predating the field must not be sent
  work it cannot do.
- **Never write to the host's own interpreter.** Everything lands in a venv
  under `$FLASHNODE_STATE_DIR/envs/`.
- `_TASK_ENV_WHITELIST` is not extended.
- The tier contract from the merged runner-contract plan stands: each runner
  owns its own health checks and its own child environment. Add to it; do
  not route around it.

---

### Task 1: Nodes say whether they can install

**Files:**
- Modify: `flashruntime/flashruntime/protocol/v1alpha1.py` — `NodeRegistration`
- Modify: `flashnode/flashnode/inventory/capabilities.py`
- Modify: `flashnode/flashnode/agent/cli.py` — the `_work` registration call
- Test: `flashruntime/tests/` protocol tests; `flashnode/tests/test_capabilities.py`

**Interfaces:**
- Produces: `NodeRegistration.can_install_dependencies: bool = False`, set by
  the agent from the runner tier.

- [ ] **Step 1: Write the failing tests**

In `flashnode/tests/test_capabilities.py`:

```python
def test_trusted_and_subprocess_tiers_can_install():
    for runner in ("trusted", "subprocess"):
        reg = describe(runner=runner)
        assert reg.can_install_dependencies is True, runner


def test_container_tiers_cannot_install():
    """--network none and a read-only rootfs. Not policy — physics."""
    for runner in ("docker", "argv"):
        reg = describe(runner=runner)
        assert reg.can_install_dependencies is False, runner


def test_the_field_defaults_closed():
    """A node registration that predates this field must not be sent work
    it cannot do — same polarity as unsandboxed_argv_capable."""
    from flashruntime.protocol.v1alpha1 import NodeRegistration

    reg = NodeRegistration(node_id="n", kubernetes_node="", hostname="h")
    assert reg.can_install_dependencies is False
```

Adapt `describe(...)` to the real signature in `inventory/capabilities.py` —
it already takes the runner-derived flags (`argv_capable`,
`unsandboxed_argv_capable`, `module_capable`); this is one more of the same
shape, threaded from `cli.py` exactly as those are.

- [ ] **Step 2: Run the tests to verify they fail**

Expected: FAIL — `AttributeError: 'NodeRegistration' object has no attribute
'can_install_dependencies'`.

- [ ] **Step 3: Write the implementation**

In `protocol/v1alpha1.py`, beside `unsandboxed_argv_capable`:

```python
    #: Whether this host can materialise a job's declared dependencies.
    #: True for the no-container tiers, which have host network and a
    #: writable disk. False for the container tiers, where `--network none`
    #: and a read-only rootfs make installation impossible at run time —
    #: physics, not policy. Defaults False and is `is`-checked at the gate:
    #: an agent predating this field must not be sent work it cannot do.
    #: Distinct from `unsandboxed_argv_capable`, which is a security opt-in;
    #: this is a statement of capability.
    can_install_dependencies: bool = False
```

In `cli.py`'s registration call, beside the existing tier-derived flags:

```python
        can_install_dependencies=(opts.runner in ("trusted", "subprocess")),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run both suites. Expected: PASS, no drop from either baseline.

- [ ] **Step 5: Commit**

```bash
git add flashruntime/flashruntime/protocol/v1alpha1.py flashnode/ flashruntime/tests/
git commit -m "feat: nodes advertise whether they can install dependencies"
```

---

### Task 2: The eighth placement gate

**Files:**
- Modify: `flashruntime/flashruntime/scheduler/__init__.py` — `eligible`
- Test: the existing scheduler/placement test module

**Interfaces:**
- Consumes: `can_install_dependencies` from Task 1, `payload["dependencies"]`
  from the declaration plan.

Place it beside the existing gates, before the `allowFallback` waiver, and
follow their established shape exactly: `is True` checks, type-confusion
fails closed, a comment saying what breaks if it is missing.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_task_with_dependencies_needs_a_host_that_can_install():
    task = _task(payload={"argv": ["python", "x.py"],
                          "dependencies": ["torch==2.3.1"]})
    assert not IsolationAwarePlacement().eligible(
        task, _node(argv_capable=True, can_install_dependencies=False))
    assert IsolationAwarePlacement().eligible(
        task, _node(argv_capable=True, can_install_dependencies=True))


def test_a_task_without_dependencies_is_unaffected():
    """Every job deployed today. The gate must be invisible to them."""
    task = _task(payload={"argv": ["python", "x.py"]})
    assert IsolationAwarePlacement().eligible(
        task, _node(argv_capable=True, can_install_dependencies=False))


def test_an_empty_dependency_list_is_not_a_requirement():
    task = _task(payload={"argv": ["python", "x.py"], "dependencies": []})
    assert IsolationAwarePlacement().eligible(
        task, _node(argv_capable=True, can_install_dependencies=False))


def test_a_missing_capability_fails_closed():
    """An agent predating the field advertises nothing; it must not be
    chosen for work it cannot do."""
    task = _task(payload={"argv": ["python", "x.py"],
                          "dependencies": ["torch==2.3.1"]})
    assert not IsolationAwarePlacement().eligible(task, _node(argv_capable=True))


def test_allow_fallback_does_not_waive_this_gate():
    """The waiver covers the sandbox tier and nothing else. A host that
    cannot install still cannot install."""
    task = _task(payload={"argv": ["python", "x.py"],
                          "dependencies": ["torch==2.3.1"],
                          "pool": "p1",
                          "isolation": {"tier": "sandboxed", "allowFallback": True}})
    assert not IsolationAwarePlacement().eligible(
        task, _node(argv_capable=True, can_install_dependencies=False,
                    capabilities={"pools": ["p1"]}))
```

Reuse the `_task`/`_node` helpers the existing placement tests already use.

- [ ] **Step 2: Run the tests to verify they fail**

Expected: FAIL — a host with `can_install_dependencies=False` is currently
eligible for everything.

- [ ] **Step 3: Write the implementation**

In `eligible`, beside the pool and GPU gates:

```python
        # Fail-closed like the gates above, and checked before the
        # allowFallback waiver for the same reason: the waiver covers the
        # sandbox tier only, and no waiver makes a read-only container with
        # no network able to run `pip install`.
        #
        # Empty and absent both mean "requires nothing" — an empty list must
        # not exclude every container host from an ordinary job.
        if task.payload.get("dependencies"):
            if node.get("can_install_dependencies") is not True:
                return False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run the full `flashruntime` suite.

- [ ] **Step 5: Commit**

```bash
git add flashruntime/flashruntime/scheduler/__init__.py flashruntime/tests/
git commit -m "feat(flashruntime): dependency-bearing tasks need a host that can install"
```

---

### Task 3: `EnvironmentCache`

**Files:**
- Create: `flashnode/flashnode/executor/environments.py`
- Create: `flashnode/tests/test_environments.py`

**Interfaces:**
- Consumes: `flashnode.identity.store.state_dir()`,
  `flashnode.executor.runner.TaskExecutionError`.
- Produces:
  - `environment_key(dependencies: list[str]) -> str`
  - `EnvironmentCache(root: Path | None = None, budget_gb: float | None = None)`
  - `EnvironmentCache.ensure(dependencies: list[str]) -> Path` — the venv's
    interpreter path, built if absent
  - `EnvironmentCache.entries() -> list[EnvEntry]` — for Task 6
  - `EnvironmentBuildError(TaskExecutionError)`

**`EnvironmentBuildError` subclasses `TaskExecutionError`.** `ExecutorLoop
._execute_inner` already calls `fail()` and logs the cause before raising,
and `execute_one` catches `TaskExecutionError` (`loop.py:285`) to count it.
Subclassing means the attributable message reaches the coordinator through
the path that exists, and Task 5 adds only bookkeeping.

**Requirements, not suggestions:**

- Key is the sha256 of the list **stripped and lowercased, order preserved**
  — NOT sorted. A requirements file is order-sensitive: an `--index-url`
  line governs the lines after it, so reordering changes which wheel you get.
  (The superseded plan said sorted. That was wrong for exactly this reason.)
- `uv venv` + `uv pip install -r` when `uv` is on PATH; `python -m venv` +
  `pip install -r` otherwise. The list is written to a requirements file and
  installed with `-r`, which is what makes `--index-url` lines work at all.
- Eviction is least-recently-**used**. Touch a marker on every `ensure()` hit.
- Budget from `FLASHNODE_ENV_BUDGET_GB`, default `8`.
- **A build that would leave under 1 GB free is refused before it starts.**
  A 5 GB pod discovering this mid-install has a half-built venv and a full
  disk.
- Build into a temporary directory and rename into place only on success —
  the write-then-rename rule `identity/store.py` already uses for the node
  id. A partial environment is never returned.

- [ ] **Step 1: Write the failing tests**

```python
def test_order_is_part_of_the_identity():
    """A requirements file is order-sensitive: --index-url governs the lines
    after it. Two orderings are two different environments."""
    assert environment_key(["--index-url https://x/cpu", "torch==2.3.1"]) != (
        environment_key(["torch==2.3.1", "--index-url https://x/cpu"])
    )


def test_whitespace_and_case_do_not_split_an_environment():
    assert environment_key([" Torch==2.3.1 "]) == environment_key(["torch==2.3.1"])


def test_ensure_builds_once_and_reuses(tmp_path, monkeypatch):
    builds = []
    cache = EnvironmentCache(root=tmp_path)
    monkeypatch.setattr(cache, "_build",
                        lambda deps, dest: builds.append(deps) or _fake_venv(dest))
    assert cache.ensure(["packaging"]) == cache.ensure(["packaging"])
    assert len(builds) == 1


def test_a_failed_build_leaves_nothing_behind(tmp_path):
    cache = EnvironmentCache(root=tmp_path)
    with pytest.raises(EnvironmentBuildError):
        cache.ensure(["this-package-does-not-exist-zolli-test"])
    assert list(tmp_path.iterdir()) == []


def test_a_build_that_would_fill_the_disk_is_refused_first(tmp_path, monkeypatch):
    monkeypatch.setattr("flashnode.executor.environments._free_bytes",
                        lambda _p: 512 * 1024**2)
    cache = EnvironmentCache(root=tmp_path)
    with pytest.raises(EnvironmentBuildError, match="disk"):
        cache.ensure(["packaging"])


def test_eviction_removes_the_least_recently_used(tmp_path, monkeypatch):
    """Least recently USED, not built — an environment three jobs keep
    hitting must outlive a newer one nothing has touched."""
    cache = EnvironmentCache(root=tmp_path, budget_gb=0.000001)
    monkeypatch.setattr(cache, "_build", lambda deps, dest: _fake_venv(dest))
    old = cache.ensure(["aaa"])
    middle = cache.ensure(["bbb"])
    cache.ensure(["aaa"])
    cache.ensure(["ccc"])
    assert old.exists(), "the recently-used environment was evicted"
    assert not middle.parent.parent.exists(), "the unused environment survived"


def test_the_error_is_a_task_execution_error():
    """So the loop's existing reporting path carries it."""
    from flashnode.executor.runner import TaskExecutionError

    assert issubclass(EnvironmentBuildError, TaskExecutionError)
```

Write `_fake_venv(dest)` to create the same layout a real build produces,
including the interpreter path `ensure` returns, so these exercise the cache
rather than a mock of it.

- [ ] **Step 2: Run the tests to verify they fail**

Expected: FAIL — `ModuleNotFoundError: flashnode.executor.environments`.

- [ ] **Step 3: Write the implementation**

Implement to the interface above. Keep it under ~200 lines; if eviction
grows past a few branches inside `ensure`, give it its own function.

- [ ] **Step 4: Run the tests to verify they pass**

Run the full `flashnode` suite.

- [ ] **Step 5: Commit**

```bash
git add flashnode/flashnode/executor/environments.py flashnode/tests/test_environments.py
git commit -m "feat(flashnode): cached per-dependency-set environments"
```

---

### Task 4: The no-container runners use the environment

**Files:**
- Modify: `flashnode/flashnode/executor/trusted_runner.py`
- Modify: `flashnode/flashnode/executor/runner.py` (`SubprocessRunner`)
- Modify: `flashnode/flashnode/executor/environments.py` (add the helper)
- Modify: `flashnode/tests/test_trusted_runner.py`

**Interfaces:**
- Produces: `substitute_interpreter(argv: list[str], interpreter: str) -> list[str]`

- [ ] **Step 1: Write the failing tests**

```python
def test_a_bare_interpreter_name_is_replaced():
    assert substitute_interpreter(
        ["python", "/work/train.py", "--epochs", "8"], "/envs/a/bin/python"
    ) == ["/envs/a/bin/python", "/work/train.py", "--epochs", "8"]


def test_python3_and_versioned_names_are_replaced():
    for name in ("python3", "python3.13"):
        assert substitute_interpreter([name, "x.py"], "/e/bin/python")[0] == "/e/bin/python"


def test_an_absolute_interpreter_path_is_left_alone():
    argv = ["/usr/bin/python3.11", "x.py"]
    assert substitute_interpreter(argv, "/e/bin/python") == argv


def test_a_non_interpreter_argv0_is_left_alone():
    argv = ["bash", "run.sh"]
    assert substitute_interpreter(argv, "/e/bin/python") == argv


def test_a_task_with_dependencies_runs_on_the_environment_interpreter(
    tmp_path, monkeypatch
):
    """The whole point: the workload runs on the interpreter its
    dependencies were installed into, not whatever `python` means here."""
    monkeypatch.setattr(
        "flashnode.executor.environments.EnvironmentCache.ensure",
        lambda self, deps: Path(sys.executable),
    )
    script = tmp_path / "w.py"
    script.write_text(
        "import json, os, pathlib, sys\n"
        "out = pathlib.Path(os.environ['FLASHML_WORK_DIR']) / 'out'\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'metrics.json').write_text(json.dumps({'exe': sys.executable}))\n"
    )
    workdir = tmp_path / "task"
    workdir.mkdir()
    outdir = TrustedArgvRunner().run(
        {"argv": ["python", str(script)], "dependencies": ["packaging"]}, workdir, {}
    )
    assert json.loads((outdir / "metrics.json").read_text())["exe"] == sys.executable


def test_a_task_with_no_dependencies_builds_no_environment(tmp_path, monkeypatch):
    """Every job deployed today takes this path and must be untouched."""
    called: list = []
    monkeypatch.setattr("flashnode.executor.environments.EnvironmentCache.ensure",
                        lambda self, deps: called.append(deps))
    script = tmp_path / "w.py"
    script.write_text(
        "import json, os, pathlib\n"
        "out = pathlib.Path(os.environ['FLASHML_WORK_DIR']) / 'out'\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'metrics.json').write_text('{}')\n"
    )
    workdir = tmp_path / "task"
    workdir.mkdir()
    TrustedArgvRunner().run({"argv": [sys.executable, str(script)]}, workdir, {})
    assert called == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Expected: FAIL — `substitute_interpreter` missing; the runner ignores
`dependencies`.

- [ ] **Step 3: Write the implementation**

```python
_BARE_INTERPRETERS = re.compile(r"^python(3(\.\d+)?)?$")


def substitute_interpreter(argv: list[str], interpreter: str) -> list[str]:
    """Point a compiled argv at a specific interpreter.

    Only a BARE name is replaced. `python` as an argv token resolves against
    the host's PATH, and on the pod that motivated this it resolved to
    /usr/bin/python while pip was /usr/bin/python3.13 — two interpreters,
    one of which had torch. An absolute path is a submitter being deliberate.
    """
    if not argv or not _BARE_INTERPRETERS.match(argv[0]):
        return list(argv)
    return [interpreter, *argv[1:]]
```

In both no-container runners, before executing: if
`payload.get("dependencies")` is non-empty, `ensure()` the environment and
substitute `argv[0]` with `str(...)` of the returned `Path`. Import
`environments` inside the method body, following the tier contract's
established rule. Let `EnvironmentBuildError` propagate.

- [ ] **Step 4: Run the tests to verify they pass**

Run the full `flashnode` suite.

- [ ] **Step 5: Commit**

```bash
git add flashnode/flashnode/executor/ flashnode/tests/
git commit -m "feat(flashnode): no-container tiers run in the job's environment"
```

---

### Task 5: Attributable failure and a per-job cooldown

**Files:**
- Modify: `flashnode/flashnode/executor/loop.py`
- Modify: `flashnode/tests/test_loop_counters.py`

**A correction to the spec, which you implement instead of its wording.**
§5 says the host "stops claiming that job's tasks". It cannot: `ClaimRequest`
is `node_id` plus an optional `job_id`
(`flashruntime/service/modea.py:560`) and the agent claims blind. The
cooldown therefore means: **a claimed lease whose job already failed to
build fails immediately with the recorded reason, without rebuilding.** The
lease is still consumed and still fails; what is saved is the repeated
multi-minute install. Note this deviation in your report.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loop_counters.py`, reusing its `_Lease` and `_loop`
helpers (`Lease.job_id` is real — `protocol/v1alpha1.py:471`):

```python
from flashnode.executor.environments import EnvironmentBuildError


def _dep_lease(job_id="job-a", task_id="T1"):
    lease = _Lease()
    lease.job_id = job_id
    lease.task_id = task_id
    lease.payload = {"dependencies": ["torch==2.3.1"]}
    return lease


def _raises_build_error(monkeypatch, loop, seen):
    def _inner(lease):
        seen.append(lease.job_id)
        raise EnvironmentBuildError(
            "host cannot satisfy dependencies: torch==2.3.1: "
            "No matching distribution found"
        )
    monkeypatch.setattr(loop, "_execute_inner", _inner)


def test_a_build_failure_counts_as_a_host_failure(monkeypatch):
    loop = _loop()
    _raises_build_error(monkeypatch, loop, [])
    assert loop.execute_one(_dep_lease()) is False
    assert loop.tasks_failed == 1


def test_the_same_job_is_not_rebuilt(monkeypatch):
    loop = _loop()
    seen: list[str] = []
    _raises_build_error(monkeypatch, loop, seen)
    for i in range(3):
        loop.execute_one(_dep_lease(task_id=f"T{i}"))
    assert seen == ["job-a"], "the build was retried after a known failure"


def test_a_different_job_is_still_attempted(monkeypatch):
    """A host is not broken because one submitter asked for something it
    cannot install."""
    loop = _loop()
    seen: list[str] = []
    _raises_build_error(monkeypatch, loop, seen)
    loop.execute_one(_dep_lease(job_id="job-a"))
    loop.execute_one(_dep_lease(job_id="job-b"))
    assert seen == ["job-a", "job-b"]


def test_the_cooldown_expires(monkeypatch):
    loop = _loop()
    seen: list[str] = []
    _raises_build_error(monkeypatch, loop, seen)
    loop.execute_one(_dep_lease())
    # Shorten the interval rather than clearing the recorded failure: the
    # point is that it EXPIRES, not that state can be reset.
    monkeypatch.setenv("FLASHNODE_DEP_COOLDOWN_S", "0")
    loop.execute_one(_dep_lease(task_id="T2"))
    assert seen == ["job-a", "job-a"]


def test_a_task_with_no_dependencies_is_never_cooled_down(monkeypatch):
    loop = _loop()
    seen: list[str] = []

    def _inner(lease):
        seen.append(lease.job_id)
        raise TaskExecutionError("unrelated failure")

    monkeypatch.setattr(loop, "_execute_inner", _inner)
    plain = _Lease()
    plain.job_id = "job-c"
    for i in range(3):
        plain.task_id = f"T{i}"
        loop.execute_one(plain)
    assert seen == ["job-c", "job-c", "job-c"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Expected: FAIL — every lease retries the build.

- [ ] **Step 3: Write the implementation**

Reporting is already handled: `EnvironmentBuildError` subclasses
`TaskExecutionError`, `_execute_inner` calls `fail()`, `execute_one` counts
it at `loop.py:285`. This task adds bookkeeping only.

In `execute_one`, catch `EnvironmentBuildError` **before** the existing
`except TaskExecutionError` — Python matches the first fitting clause, so
the ordering is the mechanism — and record
`self._dep_cooldown[lease.job_id] = (deadline, reason)`, then fall through
to the same counting the base clause does without duplicating the
increments.

At the top of `execute_one`: if `lease.payload.get("dependencies")` and
`lease.job_id` is in `_dep_cooldown` with a live deadline, fail the lease
with the recorded reason and return `False` without calling
`_execute_inner`. Read `FLASHNODE_DEP_COOLDOWN_S` (default `900`) at check
time, not in `__init__`.

- [ ] **Step 4: Run the tests to verify they pass**

Run the full `flashnode` suite.

- [ ] **Step 5: Commit**

```bash
git add flashnode/flashnode/executor/loop.py flashnode/tests/
git commit -m "fix(flashnode): a host that cannot satisfy a job says so once"
```

---

### Task 6: `flashnode env` — see it and reclaim it

**Files:**
- Modify: `flashnode/flashnode/agent/cli.py` — new subcommand + USAGE
- Modify: `flashnode/tests/test_cli_trusted.py` or a new `tests/test_cli_env.py`

**Interfaces:**
- Consumes: `EnvironmentCache.entries()` from Task 3.

Environments accumulate on someone else's computer. A volunteer must be able
to see how much of their disk this holds and take it back without deleting
`~/.flashnode` and re-enrolling.

- [ ] **Step 1: Write the failing tests**

```python
def test_env_list_reports_size_and_last_use(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("FLASHNODE_STATE_DIR", str(tmp_path))
    ...  # build two fake entries via EnvironmentCache
    assert main(["env", "list"]) == 0
    out = capsys.readouterr().out
    assert "MB" in out or "GB" in out
    assert "total" in out.lower()


def test_env_purge_removes_everything_and_says_what_it_freed(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("FLASHNODE_STATE_DIR", str(tmp_path))
    ...  # build two fake entries
    assert main(["env", "purge"]) == 0
    assert "freed" in capsys.readouterr().out.lower()
    assert list((tmp_path / "envs").iterdir()) == []


def test_env_purge_leaves_the_credential_and_node_id_alone(tmp_path, monkeypatch):
    """Purging environments must never cost a volunteer their enrolment —
    that is the difference between this command and `rm -rf ~/.flashnode`."""
    monkeypatch.setenv("FLASHNODE_STATE_DIR", str(tmp_path))
    (tmp_path / "node-id").write_text("fn-abc\n")
    (tmp_path / "credentials.json").write_text("{}")
    main(["env", "purge"])
    assert (tmp_path / "node-id").is_file()
    assert (tmp_path / "credentials.json").is_file()


def test_env_list_on_a_host_with_no_environments_is_not_an_error(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("FLASHNODE_STATE_DIR", str(tmp_path))
    assert main(["env", "list"]) == 0
    assert "no environments" in capsys.readouterr().out.lower()
```

Replace each `...` with real construction through `EnvironmentCache` and its
`_build` stub, as Task 3's tests do — do not hand-craft directory layouts
that the cache would not produce.

- [ ] **Step 2: Run the tests to verify they fail**

Expected: FAIL — `env` is not a known subcommand.

- [ ] **Step 3: Write the implementation**

Add `env` to `main`'s dispatch and to `USAGE`, with `list` and `purge`
subcommands. `purge` removes only `envs/`, never the sibling `node-id` or
`credentials.json`.

- [ ] **Step 4: Run the tests to verify they pass**

Run the full `flashnode` suite.

- [ ] **Step 5: Commit**

```bash
git add flashnode/flashnode/agent/cli.py flashnode/tests/
git commit -m "feat(flashnode): flashnode env list/purge"
```

---

## Manual verification

The unit tests cannot prove a pod works. With both plans merged and the pin
bumped:

1. `cloudflared tunnel --url http://localhost:8000`; keep it up.
2. Deploy a CPU pod with **≥20 GB** container disk — torch plus a venv does
   not fit in the 5 GB default.
3. On the pod: `pip install flashnode`, `flashnode login --coordinator <tunnel>`,
   approve at `/activate?pool=<pool-id>`, then
   `flashnode work --coordinator <tunnel> --runner trusted`.
4. **Do not install torch by hand.** That is the entire test.
5. Submit the federated example. Expect tasks `COMPLETED` with the pod's
   `node_id`, and a venv under `~/.flashnode/envs/`.
6. `flashnode env list` on the pod shows it, with a size.

Then the negative cases:

7. Submit a job declaring an impossible requirement. The console must show
   `host cannot satisfy dependencies: <name>`, not a `ModuleNotFoundError`
   traceback, and the pod must attempt the build once — not four times.
8. Submit a job with extras while only a **docker**-tier host is online. It
   must stay pending rather than being claimed and failed.

## Out of scope

Derived images, the CUDA version matrix, and the cpu/cuda torch version
divergence — all spec §6. The four follow-ups parked at the runner-contract
merge are independent of this work.
