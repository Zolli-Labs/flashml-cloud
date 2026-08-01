# Windows Hosts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A friend on Windows can donate their machine. Today `flashnode work` crashes on Windows before it runs a single task.

**Architecture:** Two small, precise changes in `flashnode` — a platform-conditional `--user` flag and Windows bind-mount path handling — plus one **prerequisite in the curated images that is the actual safety-critical part of this plan.**

**Tech Stack:** Python ≥3.10, Docker Desktop (WSL2 backend), pytest.

This is **Plan 6 of 7** for M1. The acceptance bar (spec §10) names Mac **and Windows** because that is what the testers own.

---

## The trap at the centre of this plan

`flashnode/flashnode/executor/hardening.py:57` emits:

```python
"--user", f"{os.getuid()}:{os.getgid()}",
```

`os.getuid` and `os.getgid` **do not exist in Python on Windows** — this raises `AttributeError` before any container starts. That is the crash.

The obvious fix is to omit `--user` on Windows, because Docker Desktop does not map host uids the way a Linux daemon does. **But omitting it is only safe if the image itself declares a non-root `USER`.** Otherwise the container runs as **root inside the container**, and the sandbox that `--network none`, `--read-only`, `--cap-drop=ALL` and the rest are building silently loses its most basic property — while every test still passes, because nothing currently asserts the effective user.

So the order matters: **the images must declare a non-root `USER` first**, and the code must refuse to drop the flag if it cannot be sure. A Windows host that silently runs strangers' code as container root is worse than a Windows host that refuses to start.

## Global Constraints

- **Security fields fail closed** (`flashruntime/CLAUDE.md` rule 3). If the platform is unrecognised, or the image's user cannot be established, refuse rather than proceed unprivileged-in-name-only.
- **Do not weaken any existing flag.** `--network none`, `--read-only`, the `noexec,nosuid` tmpfs, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--pids-limit`, the `--memory`/`--memory-swap` pairing, and `--ulimit` all stay exactly as they are on every platform.
- **macOS and Linux behaviour must not change at all.** Their argv is asserted against a fixture; a diff there is a regression, not an improvement.
- Baselines: flashnode **85 passed, 1 skipped**, flashruntime **532 passed, 1 skipped**, e2e **15**. No new skips.

---

### Task 1: Curated images declare a non-root `USER`

**Files:** create `flashml-cloud/images/*/Dockerfile`, `flashml-cloud/images/README.md`; test `apps/api/tests/test_image_dockerfiles.py`

**This task comes first and Task 2 depends on it.** The three curated images from Plan 4 (`python-slim`, `sklearn`, `pytorch-cpu`) need real Dockerfiles, each ending with a non-root `USER` — a dedicated uid (e.g. `10001`), not `nobody`, so file ownership in `/work` is predictable.

Tests parse each Dockerfile and assert: a `USER` instruction exists; it is not `root` or `0`; it is the **last** user-affecting instruction (a later `USER root` would undo it); and the uid matches across all three images, so `/work` ownership is consistent whichever image a task uses.

Note in `images/README.md` that these are built and pushed in Plan 7, and that **the non-root `USER` is load-bearing for Windows hosts**, with a pointer to this plan — otherwise someone later "simplifies" the Dockerfile and reopens the hole.

---

### Task 2: Platform-conditional `--user`

**Files:** modify `flashnode/flashnode/executor/hardening.py`; test `flashnode/tests/test_hardening_platform.py`

Make the `--user` flag platform-conditional:
- **POSIX** (`hasattr(os, "getuid")`): unchanged — `--user {uid}:{gid}` exactly as today.
- **Windows**: omit `--user`, relying on the image's declared non-root `USER`.
- **Any other case** — no `getuid` and not Windows: **raise**, do not silently omit. An unrecognised platform must not quietly produce a root container.

Add a comment at the flag explaining *why* the omission is safe on Windows and what it depends on, naming the image requirement. The next person to read this line will otherwise assume the flag is optional in general.

Tests: on POSIX the argv contains `--user` with the real uid/gid; with `os.getuid` monkeypatched away and `sys.platform` forced to `win32`, the argv omits `--user` **and retains every other hardening flag** (assert the full expected argv, not just the absence); an unrecognised platform raises. The full-argv assertion is the one that matters — a fix that drops `--user` *and* accidentally drops `--cap-drop=ALL` would pass a narrower test.

---

### Task 3: Windows bind-mount paths

**Files:** modify `flashnode/flashnode/executor/hardening.py` (and `loop.py` if the workdir needs it); test `flashnode/tests/test_hardening_platform.py`

`-v {workdir}:/work` needs a path Docker Desktop accepts. A Windows `WindowsPath` renders as `C:\Users\phong\AppData\...`, and backslashes plus the drive colon break the `-v src:dst` split.

Convert to the form Docker Desktop accepts on Windows, and leave POSIX paths byte-identical. Test with a synthetic `PureWindowsPath` so the test runs on this machine — do not gate the test behind `sys.platform == "win32"`, or it will never run in CI and the bug will return.

Also confirm `FLASHNODE_WORKDIR` defaults sensibly on Windows: Docker Desktop only shares certain host directories, and a path outside them bind-mounts as an empty directory — the same class of failure colima produces on macOS, which has already cost this project real debugging time. Document the expected location.

---

### Task 4: Honest documentation, and what remains unproven

**Files:** `flashruntime/docs/guides/donate-a-machine.md`, `flashnode/README.md`, `../PROGRESS.md`

Document Windows as supported, with the Docker Desktop (WSL2) prerequisite and the `FLASHNODE_WORKDIR` guidance.

**State plainly what is not proven.** Every test in this plan runs on macOS with `sys.platform` faked. That verifies the argv we *construct*, not that Docker Desktop on real Windows accepts it. Until someone runs `flashnode work` on an actual Windows machine and completes a task, Windows support is **constructed-argv-verified, not execution-verified** — exactly the distinction the 2026-07-29 entry drew about the sandbox flags themselves.

Say so in the guide and the PROGRESS entry. The acceptance test (spec §10) is a friend on Windows enrolling and contributing; that is what upgrades this from constructed to proven, and it belongs to Plan 7.

---

## Self-Review

**Spec coverage.** §2.1's Windows worker platform → Tasks 2, 3. §5.5's two named fixes → Tasks 2, 3, with Task 1 as the safety prerequisite §5.5 calls out.

**Deliberately not here:** GPU hosts (M1.5 — needs NVIDIA hardware to verify honestly); the real-Windows execution proof (Plan 7's acceptance run).

**Ordering risk.** Task 2 must not merge before Task 1. If `--user` is dropped while the images still default to root, every Windows host runs strangers' code as container root and no test fails. If these are executed out of order or split across branches, that window exists silently.
