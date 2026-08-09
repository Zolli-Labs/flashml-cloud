# Declared Dependencies and Cached Environments Implementation Plan

> **SUPERSEDED — DO NOT IMPLEMENT.** Written against §4.1 of the
> trusted-tier spec, which was replaced by
> `../specs/2026-08-09-dependency-provisioning-design.md`. Its Task 2 has the
> compiler read a repo-root `requirements.txt`, which would pull the default
> PyPI torch — the CUDA build plus ~2 GB of `nvidia-*` wheels — onto a CPU
> host, and would create a second source of truth for what a curated image
> already contains.
>
> Replaced by two plans: `2026-08-09-dependency-declaration.md` (the
> declaration half, written) and `2026-08-09-dependency-execution.md` (the
> host half, not yet written). Tasks 3, 4 and 5 below survive almost
> unchanged and are the basis for the execution plan — keep this file for
> that, not to implement from.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a job declare its Python dependencies, and let the
no-container tiers materialise them in a cached per-dependency-set virtual
environment, so a rented pod runs a job without anyone hand-installing torch
onto the right interpreter.

**Architecture:** `flashml.yaml` gains an optional `dependencies:` list,
resolved at submit time by the `flashml-cloud` compiler (falling back to a
repo-root `requirements.txt`) into `workload.parameters`. `CommandRecipe`
validates and forwards it into every task payload. On the host,
`EnvironmentCache` builds a venv keyed by the hash of the normalised list,
and the no-container runners invoke that venv's interpreter. A build failure
is an attributable task failure, and the host stops paying to rebuild the
same broken environment for the same job.

**Tech Stack:** Python 3.11+, pytest, pydantic. `uv` when present, `venv`
+ `pip` otherwise.

**Spec:** `docs/superpowers/specs/2026-08-09-trusted-tier-execution-contract-design.md`
§4 and §5. §2 and §3 shipped on 2026-08-09 in
`2026-08-09-trusted-tier-runner-contract.md` and are assumed present.

## Two repos, two branches

| Tasks | Repo | Path |
|---|---|---|
| 1, 3, 4, 5 | `flashml` (public) | `~/Work/Zolli-Labs/flashml` |
| 2 | `flashml-cloud` (private) | `~/Work/Zolli-Labs/flashml-cloud` |

Task 1 must merge before Task 2 is useful, and both before Tasks 3-5 can be
exercised end to end. Hard rule 2: any schema a FlashNode must understand
belongs upstream in `flashml` first. Do not invert this order.

**Test commands.** In `flashml`: `flashnode/.venv/bin/python -m pytest tests/ -v`
from `flashnode/`, and the flashruntime suite from `flashruntime/`. In
`flashml-cloud`: `pytest` from `apps/api/`. Record each baseline before
starting; any drop is a regression.

## Global Constraints

- **Dependencies are resolved at SUBMIT time, never on the host.** The
  payload carries a resolved list of requirement strings. A host that reads
  a path, a file, or a fallback rule is wrong — every node running the job
  must materialise the same environment.
- **An empty or absent dependency list changes nothing.** No venv is built,
  the interpreter is not substituted, and behaviour is byte-identical to
  today. This is the path every currently-deployed job takes.
- **Containerised tiers ignore dependencies entirely.** `DockerRunner` and
  `ArgvDockerRunner` get their environment from the image; building a venv
  there would install a second copy of what the image already has.
- **`argv[0]` is substituted only when it is a bare interpreter name** —
  `python`, `python3`, `python3.<n>`. An absolute path is a submitter being
  deliberate and is left untouched.
- **Never write to the host's own interpreter.** All installation happens
  inside a venv under `$FLASHNODE_STATE_DIR/envs/`.
- `_TASK_ENV_WHITELIST` is not extended. The venv reaches the child through
  `argv[0]` and `PATH`, both set by the runner.
- Nothing in this plan may make a currently-passing job fail. Every gate
  added is conditional on a non-empty dependency list.

---

### Task 1: The payload carries the dependency list

**Repo:** `flashml`. **Files:**
- Modify: `flashruntime/flashruntime/recipes/command.py` — `validate_params`, `expand`
- Test: `flashruntime/tests/test_recipes_command.py` (or the existing command-recipe test module — find it and append)

**Interfaces:**
- Consumes: nothing.
- Produces: task payload key `dependencies: list[str]`, present only when
  the workload declared a non-empty list.

**Read this before writing code.** `command.py`'s `local_inputs` forward
carries a comment that is the design warning for this whole task:

> Dropping it does NOT fail closed. The gate sees a task requiring nothing,
> places it on any node, and flashnode mounts nothing — so the task runs
> without the data it asked for. Both ends of this hop have tests that pass
> while it is broken, because each constructs the payload directly.

`dependencies` has exactly the same shape of failure: drop the forward and
the host builds no environment, the workload dies on `ModuleNotFoundError`,
and every unit test on both sides still passes. Step 1 therefore includes an
end-to-end forward test, not only a payload-shape test.

- [ ] **Step 1: Write the failing tests**

```python
def test_dependencies_reach_every_task_payload():
    """The forward, end to end from JobSpec to TaskSpec. Both ends of this
    hop have unit tests that pass while the forward is missing — see the
    local_inputs comment in command.py for the precedent."""
    spec = _job_spec(parameters={
        "command": ["python", "/work/inputs/code/train.py"],
        "task_params": [{"shard": "0"}, {"shard": "1"}],
        "dependencies": ["torch==2.13.0", "numpy"],
    })
    tasks = CommandRecipe().expand("job-1", spec)
    assert len(tasks) == 2
    for t in tasks:
        assert t.payload["dependencies"] == ["torch==2.13.0", "numpy"]


def test_absent_dependencies_leave_the_key_absent():
    """Absent stays absent, never an empty list — the same rule
    unpack_inputs follows, so the no-dependency path keeps being exercised."""
    spec = _job_spec(parameters={"command": ["python", "x.py"]})
    task = CommandRecipe().expand("job-1", spec)[0]
    assert "dependencies" not in task.payload


def test_dependencies_must_be_a_list_of_strings():
    problems = CommandRecipe().validate_params(
        {"command": ["python", "x.py"], "dependencies": "torch==2.13.0"}
    )
    assert any("dependencies" in p for p in problems)


def test_dependencies_rejects_non_string_members():
    problems = CommandRecipe().validate_params(
        {"command": ["python", "x.py"], "dependencies": ["torch", 3]}
    )
    assert any("dependencies" in p for p in problems)
```

Find the existing command-recipe test module and its `_job_spec` helper;
reuse them rather than inventing a second builder.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/ -k dependencies -v` from `flashruntime/`
Expected: FAIL — `KeyError: 'dependencies'` and empty problem lists.

- [ ] **Step 3: Write the implementation**

In `validate_params`, beside the existing `command` and `inputs` checks:

```python
        deps = params.get("dependencies")
        if deps is not None:
            if not isinstance(deps, list) or isinstance(deps, (str, bytes)):
                problems.append(
                    "'dependencies' must be a list of pip requirement strings "
                    "(a list, not a single string)"
                )
            elif not all(isinstance(d, str) for d in deps):
                problems.append("'dependencies' must be a list of strings")
```

In `expand`, beside the `unpack_inputs` and `local_inputs` forwards:

```python
            if p.get("dependencies"):
                # Absent and empty both stay absent: an empty list means
                # "no dependencies", which is the no-venv path, and emitting
                # `[]` would stop that path being exercised.
                #
                # Resolved at SUBMIT time by the flashml-cloud compiler, never
                # on the host — every node running this job must materialise
                # the same environment, and a host that re-resolved could
                # not. Same failure shape as `local_inputs` above: drop this
                # line and the host builds nothing, the workload dies on
                # ModuleNotFoundError, and both ends' unit tests still pass.
                payload["dependencies"] = list(p["dependencies"])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run the full flashruntime suite. Expected: PASS, no drop from baseline.

- [ ] **Step 5: Commit**

```bash
git add flashruntime/recipes/command.py flashruntime/tests/
git commit -m "feat(flashruntime): tasks carry their job's declared dependencies

Resolved at submit time and forwarded per task, so every node running a
job materialises the same environment."
```

---

### Task 2: `flashml.yaml` declares dependencies

**Repo:** `flashml-cloud`. **Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/flashml_yaml.py`
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/compile.py`
- Test: `flashml-cloud/apps/api/tests/` — find the existing flashml_yaml and compile test modules and append

**Interfaces:**
- Consumes: the payload key from Task 1.
- Produces: `FlashmlConfig.dependencies: list[str]`, and
  `workload.parameters["dependencies"]` in the compiled spec.

**Resolution order, in the compiler, at submit time:**
1. an explicit `dependencies:` list in `flashml.yaml`, verbatim;
2. otherwise a `requirements.txt` at the repository root — one requirement
   per line, `#` comments and blank lines dropped;
3. otherwise nothing (key absent).

Copy the shape of `_local_inputs` in `compile.py:191-215`, including its
comment about `CommandRecipe` not forwarding unrecognised parameters.

- [ ] **Step 1: Write the failing tests**

```python
def test_explicit_dependencies_win_over_requirements_txt(tmp_path):
    (tmp_path / "requirements.txt").write_text("scikit-learn\n")
    config = parse_flashml_yaml(
        "version: 1\nname: j\nimage: img:1\nentrypoint: train.py\n"
        "dependencies:\n  - torch==2.13.0\n"
    )
    params = compile_to_parameters(config, repo_root=tmp_path)
    assert params["dependencies"] == ["torch==2.13.0"]


def test_requirements_txt_is_read_when_no_list_is_declared(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "# a comment\n\ntorch==2.13.0\nnumpy>=1.26\n"
    )
    config = parse_flashml_yaml(
        "version: 1\nname: j\nimage: img:1\nentrypoint: train.py\n"
    )
    params = compile_to_parameters(config, repo_root=tmp_path)
    assert params["dependencies"] == ["torch==2.13.0", "numpy>=1.26"]


def test_neither_source_leaves_the_key_absent(tmp_path):
    config = parse_flashml_yaml(
        "version: 1\nname: j\nimage: img:1\nentrypoint: train.py\n"
    )
    params = compile_to_parameters(config, repo_root=tmp_path)
    assert "dependencies" not in params


def test_dependencies_must_be_a_list_not_a_string():
    with pytest.raises(ConfigError, match="dependencies"):
        parse_flashml_yaml(
            "version: 1\nname: j\nimage: img:1\nentrypoint: train.py\n"
            "dependencies: torch==2.13.0\n"
        )
```

Adapt the helper names to whatever the existing tests in this repo call the
parse and compile entry points — do not invent new public functions.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/ -k dependencies -v` from `apps/api/`
Expected: FAIL — the key is never produced and no `ConfigError` is raised.

- [ ] **Step 3: Write the implementation**

In `flashml_yaml.py`, beside `_validate_args`:

```python
def _validate_dependencies(value: object) -> list[str]:
    """``dependencies: ["torch==2.13.0"]`` → that list.

    A list, never a single string — same rule and same reason as `args`.
    `dependencies: torch==2.13.0` is a plausible typo that would otherwise
    be read as a list of characters.
    """
    if not isinstance(value, list) or isinstance(value, (str, bytes)):
        raise ConfigError(
            f"flashml.yaml 'dependencies' must be a list of pip requirement "
            f"strings, got {value!r}"
        )
    if not all(isinstance(d, str) for d in value):
        raise ConfigError(
            f"flashml.yaml 'dependencies' must be a list of strings, got {value!r}"
        )
    return [d.strip() for d in value if d.strip()]
```

Add `dependencies: list[str]` to `FlashmlConfig` with a default of an empty
list, and wire `_validate_dependencies` into the parse path beside `args`.

In `compile.py`, add beside `_local_inputs`:

```python
DEPENDENCIES_PARAM = "dependencies"


def _dependencies(config: FlashmlConfig, parameters: dict[str, Any],
                  repo_root: Path) -> None:
    """Resolve the job's Python dependencies, at SUBMIT time.

    Explicit list wins; otherwise a repo-root requirements.txt; otherwise
    nothing. Resolving here rather than on the host is what makes every node
    running this job build the same environment, and what lets a failure
    name a specific requirement instead of a missing file.

    Absent stays absent — see `_local_inputs` above for why an empty value
    must not be emitted, and note that CommandRecipe does not forward
    unrecognised workload parameters, so this key must match the name the
    recipe reads.
    """
    deps = list(config.dependencies)
    if not deps:
        req = repo_root / "requirements.txt"
        if req.is_file():
            deps = [
                line.strip()
                for line in req.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
    if deps:
        parameters[DEPENDENCIES_PARAM] = deps
```

Call it from the same place `_local_inputs` is called. If that call site has
no `repo_root` in scope, thread the staged repository path through to it —
and say so in your report, because it changes a signature.

- [ ] **Step 4: Run the tests to verify they pass**

Run the full `apps/api` suite. Expected: PASS, no drop from baseline.

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/ flashml-cloud/apps/api/tests/
git commit -m "feat(api): flashml.yaml declares Python dependencies

Explicit list, else a repo-root requirements.txt, resolved at submit time
into the workload parameters CommandRecipe forwards."
```

---

### Task 3: `EnvironmentCache`

**Repo:** `flashml`. **Files:**
- Create: `flashnode/flashnode/executor/environments.py`
- Create: `flashnode/tests/test_environments.py`

**Interfaces:**
- Consumes: `flashnode.identity.store.state_dir()`.
- Produces:
  - `environment_key(dependencies: list[str]) -> str` — sha256 of the
    normalised list
  - `EnvironmentCache(root: Path | None = None, budget_gb: float | None = None)`
  - `EnvironmentCache.ensure(dependencies: list[str]) -> Path` — returns the
    venv's interpreter path (a `Path`, not a str), building it if absent
  - `EnvironmentBuildError(TaskExecutionError)` — raised with the failing
    requirement and the installer's stderr tail

**`EnvironmentBuildError` subclasses `TaskExecutionError`**, imported from
`flashnode.executor.runner`. That is not cosmetic: `ExecutorLoop
._execute_inner` already calls `fail()` and logs the cause before raising,
and `execute_one` catches `TaskExecutionError` (`loop.py:285`) purely to
count it. Subclassing means the attributable message reaches the coordinator
through the path that already exists, and Task 5 only has to add cooldown
bookkeeping rather than a second reporting route.

Import it at module scope in `environments.py` — `runner.py` does not import
`environments`, so there is no cycle. Task 4 has the runners import
`environments` inside their method bodies, following the same rule the
`health` import already follows.

**Design points that are requirements, not suggestions:**

- The key is the sha256 of the dependency list **sorted, stripped, and
  lowercased**, so two jobs declaring the same requirements in a different
  order share one environment.
- `uv venv` + `uv pip install` when `uv` is on PATH; `python -m venv` +
  `pip install` otherwise. Same key either way — the builder is an
  implementation detail, not part of the identity.
- Eviction is least-recently-**used**, not least-recently-built. Touch a
  marker file on every `ensure()` hit.
- Budget from `FLASHNODE_ENV_BUDGET_GB`, default `8`.
- **A build that would leave under 1 GB of free disk is refused before it
  starts**, raising `EnvironmentBuildError`. The RunPod CPU pod that
  motivated this has a 5 GB container disk and a torch install is most of a
  gigabyte; discovering that mid-install leaves a half-built venv and a full
  disk.
- A partially built environment is never returned. Build into a temporary
  directory and rename into place only on success — the same
  write-then-rename rule `identity/store.py` already uses for the node id.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_same_requirements_in_a_different_order_are_one_environment():
    assert environment_key(["torch==2.13.0", "numpy"]) == environment_key(
        ["numpy", "torch==2.13.0"]
    )


def test_different_requirements_are_different_environments():
    assert environment_key(["torch==2.13.0"]) != environment_key(["torch==2.9.0"])


def test_ensure_builds_once_and_reuses(tmp_path, monkeypatch):
    builds = []
    cache = EnvironmentCache(root=tmp_path)
    monkeypatch.setattr(cache, "_build", lambda deps, dest: builds.append(deps)
                        or _fake_venv(dest))
    first = cache.ensure(["packaging"])
    second = cache.ensure(["packaging"])
    assert first == second
    assert len(builds) == 1


def test_a_failed_build_leaves_nothing_behind(tmp_path):
    cache = EnvironmentCache(root=tmp_path)
    with pytest.raises(EnvironmentBuildError):
        cache.ensure(["this-package-does-not-exist-zolli-test"])
    assert list(tmp_path.iterdir()) == []


def test_a_build_that_would_fill_the_disk_is_refused_before_it_starts(
    tmp_path, monkeypatch
):
    """5 GB container disks are the motivating case. Failing mid-install
    leaves a half-built venv AND a full disk."""
    monkeypatch.setattr(
        "flashnode.executor.environments._free_bytes",
        lambda _p: 512 * 1024**2,
    )
    cache = EnvironmentCache(root=tmp_path)
    with pytest.raises(EnvironmentBuildError, match="disk"):
        cache.ensure(["packaging"])


def test_eviction_removes_the_least_recently_used(tmp_path, monkeypatch):
    """Least recently USED, not least recently built — an environment three
    jobs keep hitting must outlive a newer one nothing has touched."""
    cache = EnvironmentCache(root=tmp_path, budget_gb=0.000001)
    monkeypatch.setattr(cache, "_build", lambda deps, dest: _fake_venv(dest))
    old = cache.ensure(["aaa"])
    middle = cache.ensure(["bbb"])
    cache.ensure(["aaa"])          # touch the oldest — it is now the newest use
    cache.ensure(["ccc"])          # forces eviction under the tiny budget
    assert old.exists(), "the recently-used environment was evicted"
    assert not middle.parent.parent.exists(), "the unused environment survived"
```

`_fake_venv(dest)` must create the same directory layout a real build
produces, including the interpreter path `ensure` returns, so these tests
exercise the cache rather than a mock of it.

Write `_fake_venv(dest)` as a local helper that creates the directory layout
`ensure` returns a path into, so these tests never run a real `pip install`
except in `test_a_failed_build_leaves_nothing_behind`, which must fail fast
on a name no index resolves.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_environments.py -v` from `flashnode/`
Expected: FAIL — `ModuleNotFoundError: flashnode.executor.environments`

- [ ] **Step 3: Write the implementation**

Implement `flashnode/executor/environments.py` to the interface above. Keep
it under ~180 lines; if it grows past that, the eviction policy probably
wants its own function rather than more branches inside `ensure`.

- [ ] **Step 4: Run the tests to verify they pass**

Run the full flashnode suite. Expected: PASS, no drop from baseline.

- [ ] **Step 5: Commit**

```bash
git add flashnode/flashnode/executor/environments.py flashnode/tests/test_environments.py
git commit -m "feat(flashnode): cached per-dependency-set environments"
```

---

### Task 4: The no-container runners use the environment

**Repo:** `flashml`. **Files:**
- Modify: `flashnode/flashnode/executor/trusted_runner.py`
- Modify: `flashnode/flashnode/executor/runner.py` (`SubprocessRunner`)
- Modify: `flashnode/tests/test_trusted_runner.py`

**Interfaces:**
- Consumes: `EnvironmentCache.ensure` from Task 3.
- Produces: `substitute_interpreter(argv: list[str], interpreter: str) -> list[str]`
  in `flashnode/executor/environments.py`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_bare_interpreter_name_is_replaced():
    assert substitute_interpreter(
        ["python", "/work/train.py", "--epochs", "8"], "/envs/a/bin/python"
    ) == ["/envs/a/bin/python", "/work/train.py", "--epochs", "8"]


def test_python3_and_versioned_names_are_replaced():
    for name in ("python3", "python3.13"):
        assert substitute_interpreter([name, "x.py"], "/e/bin/python")[0] == (
            "/e/bin/python"
        )


def test_an_absolute_interpreter_path_is_left_alone():
    """A submitter who wrote an absolute path meant it."""
    argv = ["/usr/bin/python3.11", "x.py"]
    assert substitute_interpreter(argv, "/e/bin/python") == argv


def test_a_non_interpreter_argv0_is_left_alone():
    argv = ["bash", "run.sh"]
    assert substitute_interpreter(argv, "/e/bin/python") == argv


def test_a_task_with_dependencies_runs_in_the_built_environment(tmp_path, monkeypatch):
    """The whole point: the workload runs on the interpreter the job's
    dependencies were installed into, not whatever `python` means on this
    host — the two were different on the pod that motivated this."""
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
        {"argv": ["python", str(script)], "dependencies": ["packaging"]},
        workdir, {},
    )
    written = json.loads((outdir / "metrics.json").read_text())
    assert written["exe"] == sys.executable


def test_a_task_with_no_dependencies_builds_no_environment(tmp_path, monkeypatch):
    """Every job deployed today takes this path and must be untouched."""
    called: list = []
    monkeypatch.setattr(
        "flashnode.executor.environments.EnvironmentCache.ensure",
        lambda self, deps: called.append(deps),
    )
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

Expected: FAIL — `substitute_interpreter` does not exist; the runner ignores
`dependencies`.

- [ ] **Step 3: Write the implementation**

`substitute_interpreter` in `environments.py`:

```python
_BARE_INTERPRETERS = re.compile(r"^python(3(\.\d+)?)?$")


def substitute_interpreter(argv: list[str], interpreter: str) -> list[str]:
    """Point a compiled argv at a specific interpreter.

    Only a BARE name is replaced. `python` as an argv token is resolved
    against the host's PATH, and on the RunPod pod that motivated this it
    resolved to /usr/bin/python while pip was /usr/bin/python3.13 — two
    interpreters, one of which had torch. An absolute path is a submitter
    being deliberate and is left alone.
    """
    if not argv or not _BARE_INTERPRETERS.match(argv[0]):
        return list(argv)
    return [interpreter, *argv[1:]]
```

In both no-container runners, before executing: if
`payload.get("dependencies")` is non-empty, `ensure()` the environment and
substitute `argv[0]`. Let `EnvironmentBuildError` propagate — Task 5 turns
it into an attributable task failure.

- [ ] **Step 4: Run the tests to verify they pass**

Run the full flashnode suite. Expected: PASS, no drop from baseline.

- [ ] **Step 5: Commit**

```bash
git add flashnode/flashnode/executor/ flashnode/tests/
git commit -m "feat(flashnode): no-container tiers run declared dependencies"
```

---

### Task 5: Attributable failure and a per-job cooldown

**Repo:** `flashml`. **Files:**
- Modify: `flashnode/flashnode/executor/loop.py`
- Modify: `flashnode/tests/test_loop_counters.py` (or the closest existing loop test module)

**Interfaces:**
- Consumes: `EnvironmentBuildError` from Task 3.
- Produces: no new public symbols.

**A correction to the spec, which you must implement rather than the spec's
wording.** §5 says the host "stops claiming that job's tasks for a bounded
interval". It cannot: `ClaimRequest` is `node_id` plus an optional `job_id`
(`flashruntime/service/modea.py:560`), and the agent claims blind — it
learns which job a lease belongs to only after claiming it. So the cooldown
must mean: **when a claimed lease belongs to a job whose environment this
host has already failed to build, fail it immediately with the recorded
reason and do not attempt the build again.** The lease is still consumed and
still fails; what the cooldown saves is the repeated multi-minute install,
and what it preserves is an honest, identical failure reason each time.

Note this deviation in the report. A pre-claim gate would need an eighth
placement predicate, which the spec's §1.5 already rejected on bootstrap
grounds.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_loop_counters.py`, reusing its existing `_Lease` and
`_loop` helpers (`Lease.job_id` is a real protocol field —
`flashruntime/protocol/v1alpha1.py:471` — so keying on it is sound):

```python
from flashnode.executor.environments import EnvironmentBuildError


def _dep_lease(job_id="job-a", task_id="T1"):
    lease = _Lease()
    lease.job_id = job_id
    lease.task_id = task_id
    lease.payload = {"dependencies": ["torch==2.13.0"]}
    return lease


def _raises_build_error(monkeypatch, loop, counter):
    def _inner(lease):
        counter.append(lease.job_id)
        raise EnvironmentBuildError(
            "host cannot satisfy dependencies: torch==2.13.0: "
            "No matching distribution found"
        )
    monkeypatch.setattr(loop, "_execute_inner", _inner)


def test_a_dependency_build_failure_counts_as_a_host_failure(monkeypatch):
    """EnvironmentBuildError subclasses TaskExecutionError, so it travels the
    reporting path that already exists — `ModuleNotFoundError` three hops
    later is what this replaces."""
    loop = _loop()
    _raises_build_error(monkeypatch, loop, [])
    assert loop.execute_one(_dep_lease()) is False
    assert loop.tasks_failed == 1


def test_the_same_job_is_not_rebuilt_after_a_failure(monkeypatch):
    loop = _loop()
    attempts: list[str] = []
    _raises_build_error(monkeypatch, loop, attempts)
    for i in range(3):
        loop.execute_one(_dep_lease(task_id=f"T{i}"))
    assert attempts == ["job-a"], "the build was retried after a known failure"


def test_a_different_job_is_still_attempted(monkeypatch):
    """A host is not broken because one submitter asked for something it
    cannot install."""
    loop = _loop()
    attempts: list[str] = []
    _raises_build_error(monkeypatch, loop, attempts)
    loop.execute_one(_dep_lease(job_id="job-a"))
    loop.execute_one(_dep_lease(job_id="job-b"))
    assert attempts == ["job-a", "job-b"]


def test_a_cooled_down_job_still_fails_with_the_recorded_reason(monkeypatch):
    """The lease is still consumed and still fails — what the cooldown saves
    is the repeated install, not the attempt."""
    loop = _loop()
    reasons: list[str] = []
    monkeypatch.setattr(loop, "_fail_lease",
                        lambda lease, reason: reasons.append(reason))
    _raises_build_error(monkeypatch, loop, [])
    loop.execute_one(_dep_lease())
    loop.execute_one(_dep_lease(task_id="T2"))
    assert len(reasons) == 2
    assert "torch==2.13.0" in reasons[1]


def test_the_cooldown_expires(monkeypatch):
    loop = _loop()
    attempts: list[str] = []
    _raises_build_error(monkeypatch, loop, attempts)
    loop.execute_one(_dep_lease())
    # Shorten the interval rather than clearing the recorded failure: the
    # point is that the cooldown EXPIRES, not that state can be reset.
    monkeypatch.setenv("FLASHNODE_DEP_COOLDOWN_S", "0")
    loop.execute_one(_dep_lease(task_id="T2"))
    assert attempts == ["job-a", "job-a"]


def test_a_task_with_no_dependencies_is_never_cooled_down(monkeypatch):
    """Every job deployed today has no dependencies key and must be
    unaffected by any of this."""
    loop = _loop()
    attempts: list[str] = []

    def _inner(lease):
        attempts.append(lease.job_id)
        raise TaskExecutionError("unrelated failure")

    monkeypatch.setattr(loop, "_execute_inner", _inner)
    plain = _Lease()
    plain.job_id = "job-c"
    for i in range(3):
        plain.task_id = f"T{i}"
        loop.execute_one(plain)
    assert attempts == ["job-c", "job-c", "job-c"]
```

`_fail_lease` and `_dep_cooldown` are names this task introduces; if the
loop already reports failures under a different private name, use that one
and say so in your report. `test_the_cooldown_expires` reads the interval at
check time rather than caching it at construction — if you implement it as a
constructor-time read, that test must be rewritten to construct a second
loop instead, and you must say so rather than deleting the test.

- [ ] **Step 2: Run the tests to verify they fail**

Expected: FAIL — the build error surfaces as a generic task failure and
every lease retries the build.

- [ ] **Step 3: Write the implementation**

Because `EnvironmentBuildError` subclasses `TaskExecutionError` (Task 3),
the *reporting* half is already handled: `_execute_inner` calls `fail()` and
`execute_one` counts it at `loop.py:285`. This task adds only the
bookkeeping.

In `execute_one`, catch `EnvironmentBuildError` **before** the existing
`except TaskExecutionError` clause — Python matches the first clause that
fits, so ordering here is the whole mechanism — and record
`self._dep_cooldown[lease.job_id] = (deadline, reason)`. Then re-raise or
fall through to the same counting the base clause does; do not duplicate the
counter increments.

At the top of `execute_one`, before any work: if `lease.payload.get(
"dependencies")` and `lease.job_id` is in `_dep_cooldown` with a live
deadline, fail the lease with the recorded reason and return `False` without
calling `_execute_inner`. Read `FLASHNODE_DEP_COOLDOWN_S` (default `900`) at
check time, not in `__init__`.

Leases with no `dependencies` key never touch this path at all.

- [ ] **Step 4: Run the tests to verify they pass**

Run the full flashnode suite. Expected: PASS, no drop from baseline.

- [ ] **Step 5: Commit**

```bash
git add flashnode/flashnode/executor/loop.py flashnode/tests/
git commit -m "fix(flashnode): a host that cannot satisfy a job says so once

Attributable failure naming the requirement, and no repeated multi-minute
rebuild of an environment already known to fail for that job."
```

---

## Manual verification

Unit tests cannot prove a pod works. After Task 5, with Tasks 1 and 2
merged:

1. `cloudflared tunnel --url http://localhost:8000` on the Mac; keep it up.
2. Add `dependencies: ["torch==2.13.0"]` to the federated example's
   `flashml.yaml` (or drop a `requirements.txt` at its root).
3. Deploy a CPU pod (`cpu3c`, ≥20 GB container disk — torch plus a venv
   needs more than the 5 GB default).
4. On the pod: `pip install flashnode`, `flashnode login --coordinator <tunnel>`,
   approve at `/activate?pool=<pool-id>`, then
   `flashnode work --coordinator <tunnel> --runner trusted`.
5. **Do not install torch by hand.** That is the whole test.
6. Submit the job. Expect: tasks `COMPLETED` with the pod's `node_id`, and a
   venv under `~/.flashnode/envs/` on the pod.

Then re-run with a deliberately impossible requirement and confirm the
console shows `host cannot satisfy dependencies: <name>` rather than a
`ModuleNotFoundError` traceback, and that the pod attempts the build once.

## Out of scope

- GPU wheel selection. Dependencies install as declared; choosing `cu121`
  over `cpu` stays the submitter's decision.
- A RunPod template image (spec §7).
- The four follow-ups parked at the §2/§3 merge — the doctor hint assuming
  enrolment, the contradictory docker trailer, the untested `argv` tier
  trailer, and `health.py`'s from-import. They are independent of this work.
