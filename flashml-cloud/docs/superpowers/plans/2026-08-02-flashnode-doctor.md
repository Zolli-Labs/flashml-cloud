# `flashnode doctor` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the host agent a diagnostic that finds a broken Docker host by
name, and refuse to claim work on one — so M1 §10 items 4 and 10 stop failing
on host misconfiguration nobody can read.

**Architecture:** One new module, `flashnode/doctor.py`, holding six
independent check functions, a registry that runs them with a skip cascade, and
a formatter. Every check takes its side effects (`subprocess.run`,
`shutil.which`) as an injected parameter, so the whole suite is unit-testable
with recorded `docker` output and needs no daemon. `flashnode work` calls the
same registry as a fail-closed startup gate.

**Tech Stack:** Python ≥3.10, stdlib only (`subprocess`, `shutil`,
`dataclasses`, `pathlib`). pytest. No new dependencies — every dependency is
attack surface on someone else's machine (flashnode AGENTS.md).

**Spec:** `flashml-cloud/docs/superpowers/specs/2026-08-02-flashnode-doctor-design.md`

## Global Constraints

- **The code lives in the PUBLIC repo.** Every `flashnode/…` path below is
  relative to `~/Work/Zolli-Labs/flashml/flashnode/`. Only the final task
  touches `flashml-cloud`. Do not add a flashml-cloud import — the repos meet
  only over `flashruntime.protocol`.
- **No new runtime dependencies.** stdlib only.
- **Never modify the host.** The doctor prints fixes. It never edits
  `~/.docker/config.json`, installs anything, or starts a daemon (spec §7.4).
- **Every check is injectable.** No check calls `subprocess.run` or
  `shutil.which` directly; both arrive as parameters with real defaults. A
  check that cannot be tested without Docker has been written wrong.
- **The probe image is `ghcr.io/zolli-labs/flashml-python-slim:2026.08.1`**,
  defined once as `PROBE_IMAGE` in `flashnode/doctor.py`. Never `pytorch-cpu`
  — it is gigabytes (spec §3.2).
- **Fix text is under test.** Asserting only the boolean verdict lets the
  message regress to a stack trace, which is the defect this whole command
  exists to remove (spec §5).
- **Existing behaviour of `harden_args` must not change.** The doctor calls it;
  it does not modify it.
- Run tests from `~/Work/Zolli-Labs/flashml/flashnode/`. Unit tests:
  `pytest`. Integration: `pytest -m integration` (opt-in, `addopts =
  "-m 'not integration'"` in `pyproject.toml`).
- Baseline before starting: **flashnode 214 tests passing.**

---

### Task 1: The check framework, and checks 1–2

Delivers a working `flashnode doctor` with two checks. Later tasks add checks
to the registry without changing this scaffolding.

**Files:**
- Create: `flashnode/doctor.py`
- Create: `tests/test_doctor.py`
- Modify: `flashnode/agent/cli.py` (USAGE block ~line 16, `main()` ~line 265)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `CheckResult(name: str, status: str, detail: str = "", fix: str = "")`
    where `status` is one of `"ok"`, `"fail"`, `"skip"`.
  - `CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess]`
  - `run_command(argv: Sequence[str], *, timeout: float = 300.0) -> subprocess.CompletedProcess`
  - `check_cli_on_path(which=shutil.which) -> CheckResult`
  - `check_engine(run: CommandRunner) -> CheckResult`
  - `run_checks(*, pull: bool, run: CommandRunner = run_command, which=shutil.which, workdir: Path | None = None, raw_local_data: str | None = None) -> list[CheckResult]`
  - `format_results(results: Sequence[CheckResult]) -> str`
  - `exit_code(results: Sequence[CheckResult]) -> int`
  - `doctor_main(argv: list[str]) -> int`
  - `PROBE_IMAGE: str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_doctor.py`:

```python
"""Unit tests for the host doctor. No Docker daemon required — every check
takes its subprocess/which call as a parameter, so failures are driven by
recorded `docker` output rather than by a broken machine.

The stderr strings below are the REAL ones from the 2026-08-02 §10 attempt
(PROGRESS.md). They are the reason this command exists; keep them verbatim.
"""

from __future__ import annotations

import subprocess

from flashnode.doctor import (
    CheckResult,
    check_cli_on_path,
    check_engine,
    exit_code,
    format_results,
)

# The Windows failure that stopped the 2026-08-02 run-through.
ENGINE_PING_500 = (
    "error during connect: Get "
    '"http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/_ping": '
    "The system cannot find the file specified."
)


def _proc(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["docker"], returncode=returncode,
        stdout=stdout.encode(), stderr=stderr.encode(),
    )


def _runner(proc):
    def run(argv, **kwargs):
        return proc
    return run


def test_cli_on_path_passes_when_docker_is_found():
    result = check_cli_on_path(which=lambda _: "/usr/local/bin/docker")
    assert result.status == "ok"
    assert "/usr/local/bin/docker" in result.detail


def test_cli_on_path_fails_when_docker_is_absent():
    result = check_cli_on_path(which=lambda _: None)
    assert result.status == "fail"
    assert "install" in result.fix.lower()


def test_engine_passes_and_reports_the_server_version():
    result = check_engine(_runner(_proc(0, stdout="27.4.0\n")))
    assert result.status == "ok"
    assert "27.4.0" in result.detail


def test_engine_fails_on_a_ping_500_and_says_to_start_docker():
    """The exact Windows failure. `shutil.which` cannot see this: the binary
    is on PATH and the daemon behind it is dead."""
    result = check_engine(_runner(_proc(1, stderr=ENGINE_PING_500)))
    assert result.status == "fail"
    assert "_ping" in result.detail
    assert "start" in result.fix.lower() and "docker" in result.fix.lower()


def test_engine_fails_rather_than_raises_when_docker_is_missing_mid_run():
    def run(argv, **kwargs):
        raise FileNotFoundError("docker")
    result = check_engine(run)
    assert result.status == "fail"


def test_exit_code_is_zero_only_when_everything_passed():
    assert exit_code([CheckResult("a", "ok"), CheckResult("b", "ok")]) == 0


def test_a_skipped_check_exits_nonzero():
    """A host with unrun checks has not been certified. Reporting it healthy
    is the failure mode this command exists to remove (spec §2)."""
    assert exit_code([CheckResult("a", "ok"), CheckResult("b", "skip")]) == 1


def test_a_failed_check_exits_nonzero():
    assert exit_code([CheckResult("a", "fail")]) == 1


def test_format_shows_the_fix_for_a_failure_and_a_trailing_count():
    text = format_results([
        CheckResult("docker CLI on PATH", "ok", detail="/usr/local/bin/docker"),
        CheckResult("docker engine reachable", "fail",
                    detail=ENGINE_PING_500, fix="Start Docker Desktop."),
        CheckResult("pull a curated image", "skip", detail="needs the engine"),
    ])
    assert "[ok]" in text and "[FAIL]" in text and "[skip]" in text
    assert "Start Docker Desktop." in text
    assert "1 check failed, 1 skipped" in text


def test_format_says_nothing_alarming_when_all_pass():
    text = format_results([CheckResult("docker CLI on PATH", "ok")])
    assert "failed" not in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_doctor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'flashnode.doctor'`

- [ ] **Step 3: Write the implementation**

Create `flashnode/doctor.py`:

```python
"""Host health checks for the sandboxed execution tiers.

WHY THIS EXISTS. Two machines stopped the 2026-08-02 §10 run-through, and
neither was a distributed-systems problem: `docker-credential-desktop`
missing on macOS, and a Docker engine answering `_ping` with 500 on Windows.
The startup gate at the time was `shutil.which("docker")`, which BOTH
machines pass — the binary was on PATH in both cases.

What happened instead is worse than a crash. `docker_runner` turns a
non-zero `docker run` into TaskExecutionError; `loop.py` catches it, calls
fail() on the lease, and keeps claiming. A host with broken Docker therefore
claims a task, fails it, claims the next one, and never tells its owner. The
volunteer sees a healthy-looking agent; the submitter sees their job failing
with a Docker error tail from a stranger's laptop.

Every check takes its side effects as a parameter. That is not test
decoration: a diagnostic you can only exercise on a broken machine is one
nobody can keep correct.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

__all__ = [
    "PROBE_IMAGE",
    "CheckResult",
    "check_cli_on_path",
    "check_engine",
    "doctor_main",
    "exit_code",
    "format_results",
    "run_checks",
    "run_command",
]

#: The image every container-level check runs. python-slim, never
#: pytorch-cpu: registry auth, TLS and the credential helper are properties
#: of the REGISTRY, so the smallest curated image proves the same thing, and
#: making a volunteer download gigabytes to learn their helper is missing is
#: a hostile diagnostic. Kept in step with flashml-cloud's published tags.
PROBE_IMAGE = "ghcr.io/zolli-labs/flashml-python-slim:2026.08.1"

CommandRunner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class CheckResult:
    """One check's verdict.

    `fix` is the point of the whole module: a volunteer will not derive
    "your credential helper is missing" from `task exited 1`.
    """

    name: str
    status: str  # "ok" | "fail" | "skip"
    detail: str = ""
    fix: str = ""


def run_command(argv: Sequence[str], *, timeout: float = 300.0) -> subprocess.CompletedProcess:
    """The real side effect. 300s because a cold image pull is slow on a
    home connection and a doctor that times out on a healthy host is worse
    than no doctor."""
    return subprocess.run(list(argv), capture_output=True, timeout=timeout, check=False)


def _text(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw.strip()
    return raw.decode(errors="replace").strip()


def check_cli_on_path(which: Callable[[str], str | None] = shutil.which) -> CheckResult:
    name = "docker CLI on PATH"
    found = which("docker")
    if found:
        return CheckResult(name, "ok", detail=found)
    return CheckResult(
        name, "fail",
        detail="`docker` was not found on PATH",
        fix="Install Docker Desktop (macOS/Windows) or your distribution's "
            "docker package, then re-run `flashnode doctor`.",
    )


def check_engine(run: CommandRunner) -> CheckResult:
    """The binary existing says nothing about the daemon behind it. This is
    the check the Windows machine needed and did not have."""
    name = "docker engine reachable"
    argv = ["docker", "version", "--format", "{{.Server.Version}}"]
    try:
        proc = run(argv, timeout=30.0)
    except FileNotFoundError as exc:
        # `docker` vanished between check 1 and here. Report, never raise:
        # a diagnostic that crashes has diagnosed nothing.
        return CheckResult(name, "fail", detail=str(exc),
                           fix="Install Docker and re-run `flashnode doctor`.")
    except subprocess.TimeoutExpired:
        return CheckResult(
            name, "fail", detail="`docker version` did not answer within 30s",
            fix="The Docker daemon is hung. Restart Docker Desktop (or "
                "`systemctl restart docker`) and re-run `flashnode doctor`.",
        )
    if proc.returncode == 0 and _text(proc.stdout):
        return CheckResult(name, "ok", detail=f"server {_text(proc.stdout)}")
    return CheckResult(
        name, "fail", detail=_text(proc.stderr) or _text(proc.stdout),
        fix="The docker CLI is installed but no daemon answered. Start "
            "Docker Desktop and wait for it to report Running, or start the "
            "docker service, then re-run `flashnode doctor`.",
    )


def run_checks(
    *,
    pull: bool,
    run: CommandRunner = run_command,
    which: Callable[[str], str | None] = shutil.which,
    workdir: Path | None = None,
    raw_local_data: str | None = None,
) -> list[CheckResult]:
    """Run every check, in order, skipping what a prior failure makes
    meaningless.

    `pull=False` is the `flashnode work` path: an agent is a long-running
    daemon on someone else's machine, and a transient registry blip must not
    stop one whose images are already cached (spec §4.1).
    """
    results = [check_cli_on_path(which=which)]
    if results[-1].status != "ok":
        return results
    results.append(check_engine(run))
    return results


def format_results(results: Sequence[CheckResult]) -> str:
    lines = []
    for r in results:
        tag = {"ok": "[ok]  ", "fail": "[FAIL]", "skip": "[skip]"}[r.status]
        lines.append(f"  {tag} {r.name:<30} {r.detail.splitlines()[0] if r.detail else ''}".rstrip())
        for extra in r.detail.splitlines()[1:]:
            lines.append(f"         {extra}")
        if r.fix:
            lines.append(f"         fix: {r.fix}")
    failed = sum(1 for r in results if r.status == "fail")
    skipped = sum(1 for r in results if r.status == "skip")
    if failed or skipped:
        parts = []
        if failed:
            parts.append(f"{failed} check{'s' if failed != 1 else ''} failed")
        if skipped:
            parts.append(f"{skipped} skipped")
        lines.append(
            ", ".join(parts)
            + ". Fix the above, then re-run `flashnode doctor`."
        )
    else:
        lines.append("All checks passed. Start contributing with "
                     "`flashnode work --runner docker`.")
    return "\n".join(lines)


def exit_code(results: Sequence[CheckResult]) -> int:
    """Skipped counts as not-passed. A host whose checks did not run has not
    been certified, and calling it healthy is the exact failure this command
    removes."""
    return 0 if all(r.status == "ok" for r in results) else 1


def doctor_main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="flashnode doctor",
        description="Check this machine can actually run FlashML tasks.",
    )
    parser.parse_args(argv)
    results = run_checks(pull=True)
    print(format_results(results))
    return exit_code(results)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_doctor.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Wire the command into the CLI**

In `flashnode/agent/cli.py`, add to the `USAGE` string after the `logout`
entry (~line 28):

```
  doctor    check this machine can run tasks (docker engine, images, mounts)
```

and in `main()`, before the `login` branch (~line 273):

```python
    if args and args[0] == "doctor":
        from flashnode.doctor import doctor_main

        return doctor_main(args[1:])
```

- [ ] **Step 6: Verify the command runs**

Run: `python -m flashnode.agent.cli doctor; echo "exit=$?"`
Expected: two check lines and an exit code reflecting your machine.

- [ ] **Step 7: Run the whole suite and commit**

Run: `pytest`
Expected: 224 passed (214 baseline + 10)

```bash
git add flashnode/doctor.py tests/test_doctor.py flashnode/agent/cli.py
git commit -m "feat(doctor): the check framework, plus CLI and engine checks

shutil.which('docker') passes on both machines that stopped the last §10
run-through. The binary was on PATH in each case; the Windows daemon behind
it answered _ping with 500."
```

---

### Task 2: Check 3 — a curated image pulls

**Files:**
- Modify: `flashnode/doctor.py`
- Modify: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `CheckResult`, `CommandRunner`, `PROBE_IMAGE`, `_text` (Task 1).
- Produces: `check_pull(run: CommandRunner, image: str = PROBE_IMAGE) -> CheckResult`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_doctor.py` (and add `check_pull` to the import block
at the top):

```python
# The macOS failure that stopped the 2026-08-02 run-through. The engine was
# healthy; a credential helper named in ~/.docker/config.json was not
# installed, and Docker consults it when authenticating a registry pull.
CREDS_HELPER_MISSING = (
    'error getting credentials - err: exec: "docker-credential-desktop": '
    "executable file not found in $PATH, out: ``"
)


def test_pull_passes_and_names_the_image():
    result = check_pull(_runner(_proc(0, stdout="Status: Image is up to date")))
    assert result.status == "ok"
    assert "flashml-python-slim" in result.detail


def test_pull_fails_on_a_missing_credential_helper_and_says_it_needs_no_login():
    result = check_pull(_runner(_proc(1, stderr=CREDS_HELPER_MISSING)))
    assert result.status == "fail"
    assert "docker-credential-desktop" in result.detail
    assert "credsStore" in result.fix
    assert "no login" in result.fix.lower()


def test_pull_fails_on_denied_and_points_at_image_visibility():
    """The GHCR-private outage: every job died at execution after signup,
    install and enrolment all appeared to work."""
    result = check_pull(_runner(_proc(1, stderr="denied: denied")))
    assert result.status == "fail"
    assert "public" in result.fix.lower()


def test_pull_uses_the_small_image_never_pytorch():
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = list(argv)
        return _proc(0)

    check_pull(run)
    assert seen["argv"][:2] == ["docker", "pull"]
    assert "python-slim" in seen["argv"][2]
    assert "pytorch" not in seen["argv"][2]


def test_pull_reports_a_timeout_rather_than_raising():
    def run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=300)
    assert check_pull(run).status == "fail"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_doctor.py -k pull -v`
Expected: FAIL — `ImportError: cannot import name 'check_pull'`

- [ ] **Step 3: Write the implementation**

Add to `flashnode/doctor.py` (and add `"check_pull"` to `__all__`):

```python
def check_pull(run: CommandRunner, image: str = PROBE_IMAGE) -> CheckResult:
    """Pull the smallest curated image.

    This is the check the Mac needed. The pull is the only step that touches
    a registry and therefore the only one that consults a credential helper
    — tasks themselves run `--network none`. It also re-catches the GHCR
    visibility regression, where the images went private and every job died
    at execution while signup, install and enrolment all looked fine.
    """
    name = "pull a curated image"
    try:
        proc = run(["docker", "pull", image], timeout=600.0)
    except subprocess.TimeoutExpired:
        return CheckResult(
            name, "fail", detail=f"`docker pull {image}` did not finish in 10 minutes",
            fix="Check this machine's internet connection, then re-run "
                "`flashnode doctor`.",
        )
    except OSError as exc:
        return CheckResult(name, "fail", detail=str(exc),
                           fix="Install Docker and re-run `flashnode doctor`.")
    if proc.returncode == 0:
        return CheckResult(name, "ok", detail=image)
    err = _text(proc.stderr) or _text(proc.stdout)
    if "credential" in err or "docker-credential" in err:
        fix = ("Your ~/.docker/config.json names a credential helper that is "
               "not installed. Either start Docker Desktop, or remove the "
               '"credsStore" line — these images are public and need no login.')
    elif "denied" in err or "unauthorized" in err:
        fix = ("The registry refused an anonymous pull. These images are "
               "meant to be public; report this — it is our bug, not yours.")
    else:
        fix = ("Check this machine's internet connection and that "
               "ghcr.io is reachable, then re-run `flashnode doctor`.")
    return CheckResult(name, "fail", detail=f"{image}\n{err}", fix=fix)
```

Then extend `run_checks`, replacing its final `return results`:

```python
    if results[-1].status != "ok":
        return results
    if pull:
        results.append(check_pull(run))
    return results
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_doctor.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add flashnode/doctor.py tests/test_doctor.py
git commit -m "feat(doctor): pull a curated image, the check the Mac needed

The pull is the only step that consults a credential helper — tasks run
--network none. python-slim, never pytorch-cpu: the helper is a property of
the registry, and gigabytes to prove it is a hostile diagnostic."
```

---

### Task 3: Check 4 — the workdir bind-mounts

**Files:**
- Modify: `flashnode/doctor.py`
- Modify: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `CheckResult`, `CommandRunner`, `PROBE_IMAGE`, `_text`.
- Produces:
  - `PROBE_FILENAME: str` (`"flashnode-doctor-probe.txt"`)
  - `PROBE_CONTENT: str` (`"flashnode-doctor"`)
  - `default_workdir() -> Path`
  - `check_workdir_mount(run: CommandRunner, workdir: Path, image: str = PROBE_IMAGE) -> CheckResult`

**Note on why this check READS rather than WRITES.** The curated images end
in a fixed non-root `USER` (10001). With the minimal flag set there is no
`--user`, so a container writing into a host-owned bind mount hits permission
denied on a perfectly healthy Linux host. Reading a file the host wrote proves
what this check is for — the VM can see this directory — without that false
failure.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_doctor.py` (extend the import block, and add
`from pathlib import Path` plus `import os` at the top):

```python
def test_workdir_mount_passes_when_the_container_reads_the_probe_back(tmp_path):
    result = check_workdir_mount(_runner(_proc(0, stdout="flashnode-doctor")), tmp_path)
    assert result.status == "ok"


def test_workdir_mount_writes_a_probe_file_the_container_can_read(tmp_path):
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = list(argv)
        # The file must exist on the host BEFORE the container runs.
        assert (tmp_path / "flashnode-doctor-probe.txt").is_file()
        return _proc(0, stdout="flashnode-doctor")

    check_workdir_mount(run, tmp_path)
    assert "-v" in seen["argv"]


def test_workdir_mount_fails_when_the_mount_is_empty_and_names_flashnode_workdir(tmp_path):
    """The colima gotcha: Docker Desktop and colima share only $HOME on
    macOS, so a workdir under /var/folders mounts as an EMPTY directory and
    every task silently sees no inputs."""
    result = check_workdir_mount(
        _runner(_proc(1, stderr="FileNotFoundError: /work/flashnode-doctor-probe.txt")),
        tmp_path,
    )
    assert result.status == "fail"
    assert "FLASHNODE_WORKDIR" in result.fix
    assert "$HOME" in result.fix


def test_workdir_mount_fails_when_the_container_reads_the_wrong_content(tmp_path):
    result = check_workdir_mount(_runner(_proc(0, stdout="something else")), tmp_path)
    assert result.status == "fail"


def test_workdir_mount_never_pulls(tmp_path):
    """Startup must not depend on a registry (spec §4.1)."""
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = list(argv)
        return _proc(0, stdout="flashnode-doctor")

    check_workdir_mount(run, tmp_path)
    assert "--pull=never" in seen["argv"]


def test_workdir_mount_says_run_the_doctor_when_the_image_is_not_cached(tmp_path):
    result = check_workdir_mount(
        _runner(_proc(125, stderr="Error: No such image: ghcr.io/zolli-labs/flashml-python-slim:2026.08.1")),
        tmp_path,
    )
    assert result.status == "fail"
    assert "flashnode doctor" in result.fix


def test_workdir_mount_cleans_up_its_probe_file(tmp_path):
    check_workdir_mount(_runner(_proc(0, stdout="flashnode-doctor")), tmp_path)
    assert not (tmp_path / "flashnode-doctor-probe.txt").exists()


def test_default_workdir_prefers_flashnode_workdir(monkeypatch, tmp_path):
    monkeypatch.setenv("FLASHNODE_WORKDIR", str(tmp_path))
    assert default_workdir() == tmp_path


def test_default_workdir_falls_back_to_the_system_temp_dir(monkeypatch):
    """Deliberately the same default ExecutorLoop uses (workdir_base=None),
    so the doctor fails on exactly the machines the agent would."""
    monkeypatch.delenv("FLASHNODE_WORKDIR", raising=False)
    assert default_workdir() == Path(tempfile.gettempdir())
```

Add `import tempfile` to the test file's imports.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_doctor.py -k "workdir" -v`
Expected: FAIL — `ImportError: cannot import name 'check_workdir_mount'`

- [ ] **Step 3: Write the implementation**

Add to `flashnode/doctor.py`. Add `os` and `tempfile` to the imports, add
`"check_workdir_mount"` and `"default_workdir"` to `__all__`, and add this
**module-level** import (Task 4 extends the same line — the checks must
import from one place, and Task 4's test monkeypatches it):

```python
from flashnode.executor.hardening import CONTAINER_WORKDIR, _bind_mount_source
```

```python
PROBE_FILENAME = "flashnode-doctor-probe.txt"
PROBE_CONTENT = "flashnode-doctor"


def default_workdir() -> Path:
    """Where the agent will actually stage task inputs.

    Mirrors ExecutorLoop's own default (`workdir_base=None` → the system
    temp dir) deliberately. On macOS that is /var/folders/…, which colima
    cannot see — so the doctor fails on precisely the machines where the
    agent would, which is the entire point.
    """
    return Path(os.environ.get("FLASHNODE_WORKDIR") or tempfile.gettempdir())


def _mount_failure_fix(err: str) -> str:
    if "No such image" in err or "not found" in err.lower():
        return ("The probe image is not cached on this machine. Run "
                "`flashnode doctor` once — it pulls it.")
    return ("The container could not see this directory. On macOS, "
            "colima and Docker Desktop share only $HOME: set "
            "FLASHNODE_WORKDIR to a path under your home directory "
            "(e.g. export FLASHNODE_WORKDIR=$HOME/.flashnode/work) and "
            "re-run `flashnode doctor`.")


def check_workdir_mount(
    run: CommandRunner, workdir: Path, image: str = PROBE_IMAGE
) -> CheckResult:
    """Can a container see the directory the agent stages inputs in?

    Minimum viable flags, on purpose. Check 5 runs the same probe with the
    full hardening set, so a failure HERE is a mount problem and a failure
    THERE is a flag problem — localised without pattern-matching stderr.

    The container READS a file the host wrote rather than writing one: the
    curated images end in a non-root USER and this flag set has no --user,
    so a write would hit permission denied on a healthy Linux host.
    """
    name = "workdir bind-mounts"
    probe = Path(workdir) / PROBE_FILENAME
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text(PROBE_CONTENT)
    except OSError as exc:
        return CheckResult(
            name, "fail", detail=f"{workdir}: {exc}",
            fix="The agent cannot write to its own work directory. Set "
                "FLASHNODE_WORKDIR to a writable path and re-run "
                "`flashnode doctor`.",
        )
    argv = [
        "docker", "run", "--rm", "--pull=never",
        "-v", f"{_bind_mount_source(Path(workdir))}:{CONTAINER_WORKDIR}",
        "-w", CONTAINER_WORKDIR,
        image,
        "python", "-c", f"print(open('{CONTAINER_WORKDIR}/{PROBE_FILENAME}').read(), end='')",
    ]
    try:
        proc = run(argv, timeout=120.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(name, "fail", detail=str(exc),
                           fix=_mount_failure_fix(str(exc)))
    finally:
        probe.unlink(missing_ok=True)
    if proc.returncode == 0 and _text(proc.stdout) == PROBE_CONTENT:
        return CheckResult(name, "ok", detail=str(workdir))
    err = _text(proc.stderr) or _text(proc.stdout) or "the container read nothing back"
    return CheckResult(name, "fail", detail=f"{workdir}\n{err}",
                       fix=_mount_failure_fix(err))
```

Extend `run_checks`, replacing its final `return results`:

```python
    if pull and results[-1].status != "ok":
        results.append(CheckResult("workdir bind-mounts", "skip",
                                   detail="needs the image above"))
        return results
    base = workdir if workdir is not None else default_workdir()
    results.append(check_workdir_mount(run, base))
    return results
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_doctor.py -v`
Expected: PASS (24 tests)

- [ ] **Step 5: Commit**

```bash
git add flashnode/doctor.py tests/test_doctor.py
git commit -m "feat(doctor): prove a container can see the agent's workdir

Reads a host-written probe rather than writing one — the curated images run
as a non-root USER and this flag set has no --user, so a write would fail on
a healthy Linux host for reasons unrelated to the mount."
```

---

### Task 4: Check 5 — a hardened container runs

This is the check that execution-verifies Plan 6. `PROGRESS.md` records the
Windows `--user` work as **"constructed-argv-verified, NOT
execution-verified"** — nothing in the system has ever run those flags on the
platform they were written for.

**Files:**
- Modify: `flashnode/doctor.py`
- Modify: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `check_workdir_mount`'s probe constants, `harden_args` from
  `flashnode.executor.hardening`.
- Produces: `check_hardened_run(run: CommandRunner, workdir: Path, image: str = PROBE_IMAGE) -> CheckResult`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_doctor.py` (extend the import block):

```python
def test_hardened_run_passes_when_the_probe_reads_back(tmp_path):
    result = check_hardened_run(_runner(_proc(0, stdout="flashnode-doctor")), tmp_path)
    assert result.status == "ok"


def test_hardened_run_carries_the_real_sandbox_flags(tmp_path):
    """Not a re-implementation of harden_args — the real one, so a change
    there is exercised here rather than drifting silently."""
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = list(argv)
        return _proc(0, stdout="flashnode-doctor")

    check_hardened_run(run, tmp_path)
    argv = seen["argv"]
    assert "--network" in argv and "none" in argv
    assert "--read-only" in argv
    assert "--cap-drop=ALL" in argv
    assert "--security-opt=no-new-privileges" in argv
    assert "--pull=never" in argv


def test_hardened_run_fails_on_a_rejected_flag_and_blames_the_flags(tmp_path):
    result = check_hardened_run(
        _runner(_proc(125, stderr="docker: Error response from daemon: "
                                  "invalid argument for --pids-limit")),
        tmp_path,
    )
    assert result.status == "fail"
    assert "--pids-limit" in result.detail
    assert "report" in result.fix.lower()


def test_hardened_run_fails_when_the_user_flag_cannot_be_built(tmp_path, monkeypatch):
    """_user_flag raises on a platform with no getuid and not win32 — a
    refusal to run unprivileged-in-name-only. The doctor must report that,
    not crash."""
    import flashnode.doctor as doctor_mod

    def boom(*a, **k):
        raise RuntimeError("cannot determine a safe --user for platform 'sunos5'")

    monkeypatch.setattr(doctor_mod, "harden_args", boom)
    result = check_hardened_run(_runner(_proc(0)), tmp_path)
    assert result.status == "fail"
    assert "safe --user" in result.detail


def test_hardened_run_cleans_up_its_probe_file(tmp_path):
    check_hardened_run(_runner(_proc(0, stdout="flashnode-doctor")), tmp_path)
    assert not (tmp_path / "flashnode-doctor-probe.txt").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_doctor.py -k hardened -v`
Expected: FAIL — `ImportError: cannot import name 'check_hardened_run'`

- [ ] **Step 3: Write the implementation**

Extend the module-level hardening import added in Task 3 to:

```python
from flashnode.executor.hardening import (
    CONTAINER_WORKDIR,
    _bind_mount_source,
    harden_args,
)
```

`harden_args` must be module-level, not imported inside the function — the
test above monkeypatches `flashnode.doctor.harden_args`, which only works if
the name lives on this module.

Add to `flashnode/doctor.py` (and `"check_hardened_run"` to `__all__`):

```python
def check_hardened_run(
    run: CommandRunner, workdir: Path, image: str = PROBE_IMAGE
) -> CheckResult:
    """The same probe as check 4, with the REAL sandbox flags.

    Check 4 uses the minimum that can work; this uses everything a task
    gets. So 4 passing and 5 failing localises the fault to a hardening
    flag, with no stderr pattern-matching — and on Windows this is the first
    thing in the system that has ever EXECUTED the platform-conditional
    --user path from Plan 6, which until now was only argv-verified.
    """
    name = "a hardened container runs"
    probe = Path(workdir) / PROBE_FILENAME
    try:
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text(PROBE_CONTENT)
    except OSError as exc:
        return CheckResult(name, "fail", detail=f"{workdir}: {exc}",
                           fix="Set FLASHNODE_WORKDIR to a writable path.")
    try:
        flags = harden_args(Path(workdir), cpus=1.0, memory_gb=1.0)
    except (RuntimeError, ValueError) as exc:
        probe.unlink(missing_ok=True)
        return CheckResult(
            name, "fail", detail=str(exc),
            fix="This platform is not one the sandbox can secure. Report "
                "it — running your machine unprivileged-in-name-only is not "
                "something we will do.",
        )
    argv = [
        "docker", "run", "--rm", "--pull=never", *flags, image,
        "python", "-c", f"print(open('{CONTAINER_WORKDIR}/{PROBE_FILENAME}').read(), end='')",
    ]
    try:
        proc = run(argv, timeout=120.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(name, "fail", detail=str(exc),
                           fix=_mount_failure_fix(str(exc)))
    finally:
        probe.unlink(missing_ok=True)
    if proc.returncode == 0 and _text(proc.stdout) == PROBE_CONTENT:
        return CheckResult(name, "ok", detail="sandbox flags accepted")
    err = _text(proc.stderr) or _text(proc.stdout) or "the container read nothing back"
    if "No such image" in err:
        return CheckResult(name, "fail", detail=err, fix=_mount_failure_fix(err))
    return CheckResult(
        name, "fail", detail=err,
        fix="The workdir mounts (check above passed) but your Docker "
            "rejected one of the sandbox flags. Please report this output — "
            "we will not loosen the sandbox to work around it.",
    )
```

Extend `run_checks`, replacing its final `return results`:

```python
    if results[-1].status != "ok":
        results.append(CheckResult("a hardened container runs", "skip",
                                   detail="needs the workdir mount above"))
        return results
    results.append(check_hardened_run(run, base))
    return results
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_doctor.py -v`
Expected: PASS (29 tests)

- [ ] **Step 5: Commit**

```bash
git add flashnode/doctor.py tests/test_doctor.py
git commit -m "feat(doctor): execute the real sandbox flags, not just build them

PROGRESS.md records Plan 6's Windows --user path as constructed-argv-
verified, NOT execution-verified. On a Windows tester this check is the
first thing that has ever run those flags on the platform they were for."
```

---

### Task 5: Check 6 — advertised local datasets are readable

Closes gap §5.3 of `2026-08-02-provenance-and-local-data-design.md`:
*"A host can advertise a label it cannot serve."* `parse_local_data` validates
the label charset, absoluteness, the `:` bind-mount hazard and duplicates —
and never stats the path.

The consequence is worse than one failed task. The coordinator's placement
gate believes the advertisement, so this host is the **only** one eligible for
that job, and every retry routes straight back to it.

**Files:**
- Modify: `flashnode/doctor.py`
- Modify: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `parse_local_data`, `LocalDataError` from `flashnode.config.local_data`.
- Produces: `check_local_datasets(raw: str | None = None) -> CheckResult`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_doctor.py`:

```python
def test_local_datasets_passes_when_none_are_configured():
    result = check_local_datasets(raw="")
    assert result.status == "ok"
    assert "none" in result.detail.lower()


def test_local_datasets_passes_for_a_readable_directory(tmp_path):
    data = tmp_path / "patients"
    data.mkdir()
    result = check_local_datasets(raw=f"patients={data}")
    assert result.status == "ok"
    assert "patients" in result.detail


def test_local_datasets_fails_on_a_path_that_does_not_exist_and_names_the_label(tmp_path):
    """The typo case. It parses, it advertises, the placement gate believes
    it, and every attempt routes back to this host."""
    result = check_local_datasets(raw=f"patients={tmp_path / 'typo'}")
    assert result.status == "fail"
    assert "patients" in result.detail


def test_local_datasets_fails_when_the_path_is_a_file_not_a_directory(tmp_path):
    f = tmp_path / "patients.csv"
    f.write_text("x")
    result = check_local_datasets(raw=f"patients={f}")
    assert result.status == "fail"
    assert "directory" in result.detail.lower()


def test_local_datasets_fails_on_an_unreadable_directory(tmp_path):
    import os
    import pytest as _pytest

    if os.getuid() == 0:
        _pytest.skip("root reads everything")
    data = tmp_path / "locked"
    data.mkdir(mode=0o000)
    try:
        result = check_local_datasets(raw=f"locked={data}")
        assert result.status == "fail"
    finally:
        data.chmod(0o755)


def test_local_datasets_reports_a_malformed_value_rather_than_raising():
    result = check_local_datasets(raw="patients")  # no '=' at all
    assert result.status == "fail"
    assert "FLASHNODE_LOCAL_DATA" in result.fix


def test_local_datasets_lists_every_bad_label_not_just_the_first(tmp_path):
    result = check_local_datasets(raw=f"a={tmp_path/'x'},b={tmp_path/'y'}")
    assert "a" in result.detail and "b" in result.detail
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_doctor.py -k local_datasets -v`
Expected: FAIL — `ImportError: cannot import name 'check_local_datasets'`

- [ ] **Step 3: Write the implementation**

Add to `flashnode/doctor.py` (and `"check_local_datasets"` to `__all__`):

```python
def check_local_datasets(raw: str | None = None) -> CheckResult:
    """Every label this host advertises must resolve to a readable directory.

    parse_local_data checks the label charset, that the path is absolute,
    that it carries no ':' that would re-read as a second mount, and that no
    label is mapped twice. It never stats the path. So a typo advertises a
    dataset this host cannot serve — and because the coordinator's
    fail-closed placement gate trusts that advertisement, this host becomes
    the ONLY one eligible for the job, and every retry comes back here.
    """
    from flashnode.config.local_data import LOCAL_DATA_ENV, LocalDataError, parse_local_data

    name = "local datasets readable"
    value = os.environ.get(LOCAL_DATA_ENV) if raw is None else raw
    try:
        mapping = parse_local_data(value)
    except LocalDataError as exc:
        return CheckResult(
            name, "fail", detail=str(exc),
            fix=f"{LOCAL_DATA_ENV} must look like "
                "label=/absolute/path,other=/absolute/path2. Fix it and "
                "re-run `flashnode doctor`.",
        )
    if not mapping:
        return CheckResult(name, "ok", detail="none configured")
    problems = []
    for label, path in sorted(mapping.items()):
        p = Path(path)
        if not p.exists():
            problems.append(f"{label}: {path} does not exist")
        elif not p.is_dir():
            problems.append(f"{label}: {path} is not a directory")
        elif not os.access(p, os.R_OK | os.X_OK):
            problems.append(f"{label}: {path} is not readable")
    if problems:
        return CheckResult(
            name, "fail", detail="\n".join(problems),
            fix=f"Point {LOCAL_DATA_ENV} at directories that exist and are "
                "readable, or remove the labels you cannot serve — the "
                "coordinator sends local-data jobs ONLY to hosts advertising "
                "them, so a bad label strands the job here.",
        )
    return CheckResult(name, "ok", detail=", ".join(sorted(mapping)))
```

Now **replace the whole `run_checks` body** with the version below. Tasks 1–4
grew it one branch at a time, which was the right way to keep each task
green, and it has reached the point where the cascade should be a loop rather
than four nested early returns. Check 6 is independent of Docker, so it runs
unconditionally after the cascade:

```python
def run_checks(
    *,
    pull: bool,
    run: CommandRunner = run_command,
    which: Callable[[str], str | None] = shutil.which,
    workdir: Path | None = None,
    raw_local_data: str | None = None,
) -> list[CheckResult]:
    base = workdir if workdir is not None else default_workdir()
    # Every container-level check, in order, each paired with the callable
    # that runs it. A failure skips the rest of THIS list; check 6 is
    # independent of Docker and always runs, after the loop.
    staged: list[tuple[str, Callable[[], CheckResult]]] = [
        ("docker CLI on PATH", lambda: check_cli_on_path(which=which)),
        ("docker engine reachable", lambda: check_engine(run)),
    ]
    if pull:
        staged.append(("pull a curated image", lambda: check_pull(run)))
    staged += [
        ("workdir bind-mounts", lambda: check_workdir_mount(run, base)),
        ("a hardened container runs", lambda: check_hardened_run(run, base)),
    ]

    results: list[CheckResult] = []
    stopped = False
    for label, check in staged:
        if stopped:
            results.append(CheckResult(label, "skip", detail="needs the check above"))
            continue
        results.append(check())
        if results[-1].status != "ok":
            stopped = True
    results.append(check_local_datasets(raw=raw_local_data))
    return results
```

- [ ] **Step 4: Add a test for the skip cascade**

Append to `tests/test_doctor.py`:

```python
def test_run_checks_skips_container_checks_when_the_engine_is_down_but_still_checks_datasets():
    results = run_checks(
        pull=True,
        run=_runner(_proc(1, stderr=ENGINE_PING_500)),
        which=lambda _: "/usr/local/bin/docker",
        raw_local_data="",
    )
    by_name = {r.name: r.status for r in results}
    assert by_name["docker engine reachable"] == "fail"
    assert by_name["pull a curated image"] == "skip"
    assert by_name["a hardened container runs"] == "skip"
    assert by_name["local datasets readable"] == "ok"
    assert exit_code(results) == 1


def test_run_checks_without_pull_never_calls_docker_pull(tmp_path):
    """The `flashnode work` path: a registry blip must not stop an agent
    whose images are cached (spec §4.1)."""
    calls = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        return _proc(0, stdout="flashnode-doctor")

    run_checks(pull=False, run=run, which=lambda _: "/usr/local/bin/docker",
               workdir=tmp_path, raw_local_data="")
    assert not any(c[:2] == ["docker", "pull"] for c in calls)
```

Add `run_checks` to the import block.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_doctor.py -v`
Expected: PASS (38 tests)

- [ ] **Step 6: Commit**

```bash
git add flashnode/doctor.py tests/test_doctor.py
git commit -m "feat(doctor): stat every advertised local dataset

Closes gap 3 of the provenance-and-local-data spec. parse_local_data never
stats the path, so a typo advertises a dataset this host cannot serve — and
the fail-closed placement gate then sends that job here and only here."
```

---

### Task 6: Make `flashnode work` refuse to start on a broken host

The §1.2 defect, fixed. Today a broken host claims a task, fails it, claims
the next one, and never stops.

**Files:**
- Modify: `flashnode/agent/cli.py:204-210` (replace the `shutil.which` gate)
- Create: `tests/test_work_gate.py`

**Interfaces:**
- Consumes: `run_checks`, `format_results`, `exit_code` (Tasks 1–5).
- Produces: no new public API. `_work` returns `2` when a check fails.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_work_gate.py`:

```python
"""`flashnode work` must not claim a lease on a host that cannot run tasks.

Before this gate, docker_runner raised TaskExecutionError, loop.py called
fail() on the lease and kept claiming — so a misconfigured machine burned
task after task while looking healthy to its owner and to the coordinator.
"""

from __future__ import annotations

import pytest

from flashnode.agent.cli import _work
from flashnode.doctor import CheckResult


@pytest.fixture()
def never_construct_a_loop(monkeypatch):
    """Fail loudly if the gate lets execution through."""
    import flashnode.executor as executor

    def boom(*a, **k):
        raise AssertionError("ExecutorLoop was constructed on an unhealthy host")

    monkeypatch.setattr(executor, "ExecutorLoop", boom)
    return boom


def test_work_refuses_to_start_when_a_check_fails(monkeypatch, capsys, never_construct_a_loop):
    monkeypatch.setattr(
        "flashnode.doctor.run_checks",
        lambda **kw: [CheckResult("docker engine reachable", "fail",
                                  detail="_ping 500", fix="Start Docker Desktop.")],
    )
    rc = _work(["--runner", "docker", "--coordinator", "http://localhost:8100"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "flashnode doctor" in err
    assert "Start Docker Desktop." in err


def test_work_gate_does_not_pull(monkeypatch, never_construct_a_loop):
    seen = {}

    def fake_run_checks(**kw):
        seen.update(kw)
        return [CheckResult("docker engine reachable", "fail")]

    monkeypatch.setattr("flashnode.doctor.run_checks", fake_run_checks)
    _work(["--runner", "docker"])
    assert seen["pull"] is False


class _Reached(Exception):
    """Raised where the subprocess tier should get to, so the test asserts on
    a specific point in _work rather than on any exception at all."""


def test_work_runs_no_doctor_for_the_subprocess_tier(monkeypatch):
    """The subprocess tier has no engine, no registry and no mounts, so the
    gate must not run for it."""
    called = []
    monkeypatch.setattr("flashnode.doctor.run_checks",
                        lambda **kw: called.append(kw) or [])

    def stop_here(*a, **k):
        raise _Reached

    monkeypatch.setattr("flashnode.identity.store.load_or_create_node_id", stop_here)
    with pytest.raises(_Reached):
        _work(["--runner", "subprocess", "--coordinator", "http://127.0.0.1:1"])
    assert called == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_work_gate.py -v`
Expected: FAIL — the gate does not exist, so `_work` proceeds past it.

- [ ] **Step 3: Write the implementation**

In `flashnode/agent/cli.py`, replace lines 204–210 (the `shutil.which` block)
with:

```python
        # A `docker` binary on PATH says nothing about the daemon behind it,
        # the credential helper Docker consults when it pulls, or whether
        # this machine's work directory is even visible inside the VM. Both
        # hosts that stopped the 2026-08-02 §10 run-through passed the old
        # `shutil.which` check and then failed every task they claimed —
        # docker_runner raises TaskExecutionError, loop.py calls fail() and
        # claims the next one, forever, silently.
        #
        # pull=False deliberately: an agent is a long-running daemon on
        # someone else's machine, and a transient registry blip must not
        # stop one whose images are already cached. `flashnode doctor` does
        # the pull.
        from flashnode.doctor import format_results, run_checks

        results = run_checks(pull=False)
        if any(r.status != "ok" for r in results):
            print(
                f"flashnode work: this machine cannot run tasks with "
                f"--runner {opts.runner}.\n" + format_results(results)
                + "\n\nRun `flashnode doctor` for the full check, including "
                  "the image pull this skipped.",
                file=sys.stderr,
            )
            return 2
```

`shutil` is still imported at the top of `_work`; leave it, it is used
elsewhere in the function.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_work_gate.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the whole unit suite**

Run: `pytest`
Expected: PASS, 41 more than the 214 baseline (255). If any existing test
constructed `_work` with `--runner docker` and no Docker, it now exits 2 —
fix the test by monkeypatching `flashnode.doctor.run_checks` to return `[]`,
never by weakening the gate.

- [ ] **Step 6: Commit**

```bash
git add flashnode/agent/cli.py tests/test_work_gate.py
git commit -m "feat(work): refuse to start on a host that cannot run tasks

Replaces shutil.which('docker'), which both §10 machines passed. A broken
host previously claimed a task, failed it, and claimed the next one for as
long as it was left running — telling neither its owner nor the submitter."
```

---

### Task 7: Integration test — all six against a real daemon

The test that would have caught both field failures.

**Files:**
- Create: `tests/integration/test_doctor_real_docker.py`

**Interfaces:**
- Consumes: `run_checks`, `exit_code`, the `docker_workdir` fixture from
  `tests/conftest.py`.

- [ ] **Step 1: Write the test**

Create `tests/integration/test_doctor_real_docker.py`:

```python
"""The doctor against a real Docker daemon.

Opt-in: pytest -m integration. Auto-skips without a daemon, matching
tests/integration/test_argv_runner_docker.py.

Uses the `docker_workdir` fixture (tests/conftest.py), NOT pytest's
tmp_path: on macOS colima and Docker Desktop share only $HOME, so tmp_path
bind-mounts as an empty directory — which is the very condition check 4
exists to detect, and would make this test fail for the right reason on a
perfectly good machine.
"""

import shutil
import subprocess

import pytest

from flashnode.doctor import exit_code, format_results, run_checks

pytestmark = pytest.mark.integration


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _docker_available(), reason="needs a real docker daemon")
def test_every_check_passes_on_a_healthy_host(docker_workdir):
    results = run_checks(pull=True, workdir=docker_workdir, raw_local_data="")
    assert len(results) == 6, [r.name for r in results]
    assert exit_code(results) == 0, "\n" + format_results(results)


@pytest.mark.skipif(not _docker_available(), reason="needs a real docker daemon")
def test_a_bad_local_dataset_label_is_caught_against_a_real_filesystem(docker_workdir):
    results = run_checks(
        pull=True, workdir=docker_workdir,
        raw_local_data=f"patients={docker_workdir / 'not-here'}",
    )
    bad = [r for r in results if r.name == "local datasets readable"]
    assert bad and bad[0].status == "fail"
    assert exit_code(results) == 1
```

- [ ] **Step 2: Run it**

Run: `pytest -m integration tests/integration/test_doctor_real_docker.py -v`
Expected: PASS on a machine with Docker. On a machine without one: 2 skipped.

If check 4 or 5 fails here, **do not weaken the check** — it has found a real
property of this host. Read its `fix` line and act on it; that is the whole
point.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_doctor_real_docker.py
git commit -m "test(doctor): all six checks against a real daemon

The test that would have caught both 2026-08-02 field failures."
```

---

### Task 8: Docs, release, and the pin

**Files:**
- Modify: `~/Work/Zolli-Labs/flashml/flashnode/README.md`
- Modify: `~/Work/Zolli-Labs/flashml/flashnode/AGENTS.md`
- Modify: `~/Work/Zolli-Labs/flashml/flashnode/pyproject.toml` (version)
- Modify: `~/Work/Zolli-Labs/flashml-cloud/Makefile:40` (`NODE_VERSION`)
- Modify: `~/Work/Zolli-Labs/flashml-cloud/PROGRESS.md`

- [ ] **Step 1: Document the command in the public README**

In `flashnode/README.md`, add before the section describing `flashnode work`:

```markdown
### Check your machine first

```bash
flashnode doctor
```

Runs six checks — the docker CLI, the engine behind it, an anonymous pull of
a curated image, whether a container can see your work directory, whether
the sandbox flags are accepted, and whether any local datasets you lend are
readable. Every failure names the fix.

Run it once before `flashnode work`. `work` repeats all of it except the
image pull and refuses to start if anything fails, because a host that
cannot run tasks should not be claiming them.
```

- [ ] **Step 2: Update the agent-facing notes**

In `flashnode/AGENTS.md`, under "Device profile", add to the
**Missing/next** sentence a note that the host doctor now exists:

```
`flashnode doctor` (doctor.py) checks the six host preconditions and gates
`flashnode work`; mid-session Docker breakage is NOT covered — a host whose
engine dies an hour in still claims and fails tasks, and the fix for that is
server-side node quarantine in the coordinator.
```

- [ ] **Step 3: Bump the version and release**

In `flashnode/pyproject.toml`, set `version = "0.3.1"`.

```bash
cd ~/Work/Zolli-Labs/flashml
git tag flashnode-v0.3.1
git push origin main --tags
```

The release workflow at the repo root publishes to PyPI. Watch it:
`gh run watch`.

- [ ] **Step 4: Move the pin**

In `~/Work/Zolli-Labs/flashml-cloud/Makefile` line 40:

```make
NODE_VERSION    := 0.3.1
```

`RUNTIME_VERSION` does **not** change — no protocol field moved, so
`apps/api/pyproject.toml` and `render.yaml` are untouched.

Verify: `cd ~/Work/Zolli-Labs/flashml-cloud && make e2e-setup && make e2e`
Expected: e2e 61 passing.

- [ ] **Step 5: Log it**

Add a `PROGRESS.md` entry at the top of `## Entries`, following the protocol
in that file (evidence with real numbers, root causes, a single Next):

```markdown
### 2026-08-02 — flashnode doctor: name the broken host instead of failing its tasks (flashnode)

What/why: M1 §10 items 4 and 10 have failed twice on host-side Docker
misconfiguration — `docker-credential-desktop` missing on macOS, engine
`_ping` 500 on Windows. The startup gate was `shutil.which("docker")`, which
BOTH machines pass. Worse, `docker_runner` raised TaskExecutionError,
`loop.py` called fail() and kept claiming, so a broken host burned task after
task while looking healthy to its owner, the coordinator and the submitter.

How verified: flashnode 214 → 255 unit tests (replace with the real count
from the final `pytest` run — a number nobody re-ran is not evidence), plus
2 integration tests green
against a real daemon (`pytest -m integration`). Both recorded field failures
are now fixtures: fed that exact stderr, the doctor fails the right check and
prints the remedy. e2e 61 against the 0.3.1 pin.

Gotchas:
1. Check 4 READS a host-written probe rather than writing one. The curated
   images end in a non-root USER and the minimal flag set has no `--user`, so
   a write fails on a healthy Linux host — a check that fails on good
   machines trains people to ignore it.
2. Checks 4 and 5 are separate ON PURPOSE. 5 subsumes 4 mechanically; keeping
   both localises a fault to mount-vs-flags without pattern-matching stderr.
3. `flashnode work` does NOT pull (`pull=False`). A registry blip must not
   stop a daemon whose images are cached. The cost: a fresh install must run
   `flashnode doctor` once, which is a deliberate onboarding change.
4. Check 6 closes gap 3 of the provenance-and-local-data spec:
   `parse_local_data` never stats the path, so a typo advertised a dataset
   the host could not serve — and the fail-closed placement gate then routed
   that job to this host and only this host.

Next: run M1 §10 with a second real machine — items 2, 4, 5, 6, 7, 10.
Parking lot: mid-session Docker breakage still burns tasks; the fix is
server-side node quarantine in the coordinator, deliberately not scoped here.
```

Update the M1 Plan 7 checklist entry in the same edit (logging rule 2). While
there, correct its stale claim that *"No job has yet been submitted, claimed
and completed against the DEPLOYED stack"* — the 2026-08-02 federated entry
disproves it.

- [ ] **Step 6: Commit both repos**

```bash
cd ~/Work/Zolli-Labs/flashml
git add flashnode/README.md flashnode/AGENTS.md flashnode/pyproject.toml
git commit -m "docs(flashnode): document the doctor; release 0.3.1"

cd ~/Work/Zolli-Labs/flashml-cloud
git add Makefile PROGRESS.md
git commit -m "chore: pin flashnode 0.3.1; log the host doctor"
```

---

## Post-plan follow-ups (NOT in this plan)

Recorded so they are not silently dropped:

1. **Server-side node quarantine** — a host whose Docker dies mid-session
   still claims and fails tasks. Coordinator work, different repo, different
   failure model (spec §7.1).
2. **`POSITIONING_LOG.md` open thread 3** still lists local data binding as
   open; it shipped and released in `flashruntime-v0.4.0`. One-line fix, but
   the log is append-only — add a dated entry, never rewrite.
3. **`DEV_DATABASE_URL` and `RENDER_API_KEY`** are still unset, so `develop`
   CI is red on every push and `deploy-prod.yml` cannot deploy. Owner-only
   credential work, unrelated to this plan but blocking the same milestone.
