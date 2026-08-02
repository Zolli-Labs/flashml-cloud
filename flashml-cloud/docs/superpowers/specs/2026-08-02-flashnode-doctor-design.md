# `flashnode doctor` — design

**Date:** 2026-08-02
**Scope:** flashnode (public repo). No protocol change, no flashruntime
release, no coordinator change.
**Status:** design. Unblocks M1 §10 items 4 and 10.

---

## 1. Why this, now

M1 Plan 7's remaining work is the §10 run-through, and it has now failed twice
on the same class of problem. From the 2026-08-02 entry in `PROGRESS.md`:

> Also: two host-side Docker failures, unrelated to us and both cryptic —
> `docker-credential-desktop` missing on macOS, engine `_ping` 500 on Windows.

Those two machines are why item 4 — *contributions from more than one machine*
— is still open. Neither is a distributed-systems problem. Both are host
misconfiguration that the agent cannot currently see, name, or refuse to work
around.

### 1.1 The check that exists verifies the thing that was not broken

`agent/cli.py` already gates the sandboxed tiers at startup:

```python
if shutil.which("docker") is None:
    print("... requires the `docker` CLI on PATH — refusing to start")
    return 2
```

**Both failing machines pass that check.** On Windows the `docker` binary was
on PATH and the *engine* returned 500. On the Mac the binary and the engine
were both fine and a *different* executable — `docker-credential-desktop`,
named as `credsStore` in `~/.docker/config.json` — was missing, which fails
when Docker authenticates a registry pull. `shutil.which("docker")` cannot
observe either.

### 1.2 What happens instead is worse than a crash

`executor/docker_runner.py` turns a non-zero `docker run` into

```python
raise TaskExecutionError(f"task exited {proc.returncode}: {tail}")
```

`executor/loop.py` catches `TaskExecutionError`, calls
`client.fail(lease.lease_id, ...)`, and the outer `run()` loop **continues
claiming**.

So a host with broken Docker does not stop and does not tell its owner. It
claims a task, fails it, claims the next one, fails it — for as long as it is
left running. The volunteer sees an agent that looks healthy. The submitter
sees their job failing with a Docker error tail from a stranger's laptop. One
misconfigured machine quietly degrades the pool it just joined.

That is the defect. This design fixes the diagnosis half of it; §7 is explicit
about the half it does not fix.

### 1.3 It is also the gate on §10 item 10

Item 10 is *"a friend — not the developer — completes signup → enroll → see
their machine contribute to a round, **unaided**, from written instructions."*
A friend will not derive `docker-credential-desktop` from `task exited 1`.
Without a diagnostic the agent owns, item 10 is permanently gated on the
developer being available to debug each tester's laptop, which is precisely
what item 10 exists to disprove.

## 2. Surface

```
flashnode doctor
```

Checks the **docker tier** — the one a volunteer runs
(`cli.py` already tells them: *"Start contributing with: flashnode work
--runner docker"*). The subprocess tier needs no doctor: no engine, no
registry, no mounts.

One line per check. Exit `0` only if every check **passes**; `1` if any failed
*or was skipped* — a host with unrun checks has not been certified, and
reporting it as healthy is the failure mode this whole command exists to
remove. A failing check prints what to do, not a stack trace:

```
flashnode doctor
  [ok]   docker CLI on PATH             /usr/local/bin/docker
  [ok]   docker engine reachable        server 27.4.0
  [FAIL] pull a curated image           ghcr.io/zolli-labs/flashml-python-slim:2026.08.1
         error getting credentials — err: exec: "docker-credential-desktop":
         executable file not found in $PATH
         fix: your ~/.docker/config.json names a credential helper that is not
              installed. Either start Docker Desktop, or remove the
              "credsStore" line — these images need no login.
  [skip] workdir bind-mounts            needs the image above
  [skip] a hardened container runs      needs the image above
  [ok]   local datasets readable        none configured
1 check failed, 2 skipped. Fix the above, then re-run `flashnode doctor`.
```

No `--json`, no `--fix`, no per-check selection. YAGNI until something
consumes it.

## 3. The checks

Ordered so the first failure is the most specific one. Each is here because it
catches a failure that actually happened or a gap already recorded.

| # | Check | How | Catches |
|---|---|---|---|
| 1 | `docker` on PATH | `shutil.which` | the existing check, kept |
| 2 | engine reachable | `docker version --format {{.Server.Version}}` | the Windows `_ping` 500 |
| 3 | curated image pulls | `docker pull ghcr.io/zolli-labs/flashml-python-slim:2026.08.1` | the macOS credential helper; a GHCR visibility regression |
| 4 | workdir bind-mounts | `docker run -v <workdir>:/work`, write a file, read it back on the host | the colima `$HOME` gotcha |
| 5 | a hardened container runs | `docker run` with the real `harden_args(...)` | uid mapping, `--read-only`, tmpfs, `--cap-drop=ALL` |
| 6 | local datasets readable | `stat` each path in `FLASHNODE_LOCAL_DATA` | a label advertised but unservable |

Checks 4 and 5 both run a container, and both use the **same** image check 3
pulls — so if 3 fails, both skip rather than reporting a second, derived
failure. Check 6 is independent of Docker entirely and always runs.

### 3.1 Why 4 and 5 are separate

Check 5 subsumes check 4 mechanically — `harden_args` mounts the workdir too —
and they are still two checks, because splitting them localises the fault
without pattern-matching stderr. Check 4 uses the minimum flags that can
possibly work. If 4 fails, the problem is the mount or the VM's shared-path
config. If 4 passes and 5 fails, the problem is one of the hardening flags.

That distinction is load-bearing for exactly one platform. `PROGRESS.md`
records Plan 6 (Windows hosts) as:

> **Constructed-argv-verified, NOT execution-verified.**

Check 5 is the first thing in the system that executes those flags on the
machine they were written for. On a Windows tester it is a Plan 6 acceptance
test that happens to also be a diagnostic.

### 3.2 Which image check 3 pulls

`images/` publishes three: `python-slim`, `pytorch-cpu`, `sklearn`.
The doctor pulls **`python-slim`** only. `pytorch-cpu` is gigabytes; making a
volunteer download it to learn whether their credential helper works is a
hostile diagnostic. Registry auth, TLS and the helper are properties of the
*registry*, not of the image, so the smallest one proves the same thing.

Consequence, stated rather than buried: a host that passes the doctor can
still stall on first use of `pytorch-cpu` if the network dies mid-pull. The
doctor proves the pull *mechanism*, not that every image is already cached.

### 3.3 Check 6 closes a gap that is already written down

`2026-08-02-provenance-and-local-data-design.md` §5 records:

> 3. **A host can advertise a label it cannot serve.** Nothing verifies the
>    directory exists or is readable until a task tries it.

Still true. `config/local_data.py::parse_local_data` validates the label
charset, absoluteness, the `:` bind-mount hazard, and duplicates — and never
stats the path. So `FLASHNODE_LOCAL_DATA="patients=/srv/data/typo"` parses,
advertises `patients`, satisfies the coordinator's fail-closed placement gate,
and fails at mount time. Because the gate believes the advertisement, this
host is the *only* one eligible for that job, and every attempt routes back to
it. Check 6 stats each path: exists, is a directory, is readable.

The doctor reports it and does not repair it. Which directory is meant is the
owner's decision.

## 4. Wiring it into `flashnode work`

`flashnode work --runner docker|argv` replaces the `shutil.which` gate with
checks **1, 2, 4, 5 and 6**, and refuses to start if any fails, naming
`flashnode doctor`.

This converts *claims work and fails it silently* into *does not start, and
says why* — the same fail-closed posture as `hardening.local_data_mounts` and
the placement gate.

### 4.1 Startup deliberately does not pull

Check 3 is the one check `work` omits. An agent is a long-running daemon on
someone else's machine; a transient GHCR blip must not stop one whose images
are already cached, and startup is the worst possible moment to depend on a
registry.

So checks 4 and 5 run with `--pull=never`. If the image is not present locally
they report *"image not cached — run `flashnode doctor` once"* rather than
silently pulling. The doctor itself runs check 3 first, so by the time a
volunteer starts `work` the image is there.

**This is a behaviour change and it is intentional.** A fresh install
currently starts and pulls on its first task; after this it refuses until
`flashnode doctor` has run once. That is the point: the pull failure surfaces
as *"your credential helper is missing"* while the volunteer is still watching
their terminal, instead of as `task exited 1` on a stranger's job an hour
later. The console's enrolment instructions gain one line — run the doctor
once, before `flashnode work`.

## 5. Testing

TDD, matching the repo's existing two-layer pattern (stubbed transport, plus
real-Docker tests that auto-skip without a daemon).

**Unit — no Docker required.** Every check takes an injectable command runner;
tests drive each one through its failure modes with recorded `docker` output,
including the two real ones verbatim: the `docker-credential-desktop` stderr
and an engine `_ping` 500. Assert the exit code, and assert the printed text
names the fix — a diagnostic whose message regresses to a stack trace has
failed at its only job, so the message is under test, not just the verdict.

**Integration — marked, auto-skips without a daemon.** Run the real doctor
against the real engine and assert all six pass. This is the test that would
have caught both field failures.

**Regression, in `flashnode work`.** Given a failing check, the loop is never
constructed and no lease is ever claimed. That is the §1.2 defect, asserted
directly.

## 6. Release

Protocol untouched, so no `flashruntime` release and no pin bump in
`apps/api/pyproject.toml` or `render.yaml`.

`flashnode` **0.3.1** to PyPI, then `NODE_VERSION := 0.3.1` in the `Makefile`
(the only place flashml-cloud pins the agent). Volunteers upgrade with
`pip install -U flashnode`.

## 7. What this does not fix

1. **Mid-session breakage.** The doctor runs at startup. A host whose Docker
   dies an hour in still claims and fails tasks exactly as it does today. The
   durable fix is server-side — quarantine a node after N consecutive
   failures — and it belongs to the coordinator, not the agent. Deliberately
   not scoped here; it is a different repo, a different failure model, and it
   should not delay the run-through.
2. **Diagnosing failures that are not the host's.** A task that fails because
   the *job* is broken looks identical from the agent. Distinguishing them
   needs the result verification of `POSITIONING_LOG.md` open thread 4.
3. **GPU.** No probe, no capability. Open thread 6.
4. **Repair.** The doctor prints fixes; it never edits `~/.docker/config.json`,
   installs anything, or starts a daemon. Modifying a volunteer's machine
   without asking is not a thing this project gets to do.

## 8. Definition of done

1. `flashnode doctor` exists, runs all six checks, exits 0 when they pass and
   1 when any fails.
2. Each check's failure output names a remedy. Asserted per check.
3. Fed the recorded `docker-credential-desktop` stderr, the doctor fails check
   3 and prints the credential-helper remedy.
4. Fed an engine `_ping` 500, it fails check 2 and prints the engine remedy.
5. A `FLASHNODE_LOCAL_DATA` label pointing at a non-existent, non-directory,
   or unreadable path fails check 6 and names the label.
6. `flashnode work --runner docker` runs checks 1, 2, 4, 5, 6, and on any
   failure **claims no lease** and exits non-zero naming `flashnode doctor`.
7. Under `flashnode work`, checks 4 and 5 use `--pull=never` and report a
   missing image as "run the doctor", not as a mount or hardening failure.
8. A skipped check exits 1, never 0.
9. On a real Docker host, all six pass — integration test, auto-skipped
   without a daemon.
10. flashnode suite green and above 214; counts recorded in `PROGRESS.md`.

## 9. Out of scope

Result verification, capability-aware placement, GPU support, the desktop app,
server-side node quarantine. All sit behind this in `POSITIONING_LOG.md`'s
open threads, and none of them is what stopped the last two run-throughs.
