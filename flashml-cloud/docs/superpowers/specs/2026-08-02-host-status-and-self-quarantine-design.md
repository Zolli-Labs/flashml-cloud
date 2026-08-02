# Host status view and self-quarantine — design

**Date:** 2026-08-02
**Scope:** flashnode (public repo) only. No protocol change, no flashruntime
release, no coordinator or cloud-API change.
**Status:** design. Closes the gap `2026-08-02-flashnode-doctor-design.md` §7.1
left open, and gives a volunteer a reason to believe their machine is working.

---

## 1. Why these two, together

Both answer one question a host owner cannot currently answer: **is my machine
actually contributing?**

**Part A — the status view.** `flashnode work` configures
`logging.basicConfig` with a JSON formatter and writes to stderr. That is the
entire host-facing interface. A volunteer watching their terminal sees a
stream of `{"ts":…,"msg":…}` and has no way to tell contributing from
quietly-failing from idle-because-nothing-is-queued.

**Part B — self-quarantine.** The doctor checks the host at *startup*. A host
whose Docker dies an hour later still claims a task, fails it, claims the next
one, and repeats for as long as it is left running — `execute_one` catches
`TaskExecutionError`, calls `fail()`, and `run()` continues. Nothing tells the
owner, and nothing stops the machine from consuming the job's retries.

They belong in one slice because they share one piece of state: the
consecutive-failure counter. Part B decides when to stop; Part A is where the
owner sees it.

### 1.1 Correcting the doctor spec

`2026-08-02-flashnode-doctor-design.md` §7.1 said mid-session breakage is
server-side coordinator work and scoped it out entirely. Half of that was too
quick. Coordinator-side quarantine is still the durable fix — only the
coordinator can stop *routing* to a bad host — but the agent can stop
*volunteering* by itself, with no protocol change and no cross-repo work. That
half is cheap and belongs here.

## 2. Part B — what counts as "this host is broken"

The naive version — stop after N calls to `execute_one` that return `False` —
would quarantine healthy machines. `execute_one` returns `False` for three
different things, and only one of them implicates the host:

| Outcome | Means | Host's fault? |
|---|---|---|
| `TaskExecutionError` | the task could not be run here | **maybe** |
| `LeaseLost` | the lease expired or was reclaimed | no |
| `complete()` → `accepted=False` | output failed its hash check, or the commit lost a race | no |

That third row is the same trap the contributions ledger hit: the coordinator
answers **HTTP 200 with `{"accepted": false}`**, and reading a non-acceptance
as a host failure is how a healthy machine gets punished for losing three
races. Only `TaskExecutionError` increments the counter.

Even that is not conclusive. Some `TaskExecutionError`s are the *job's* fault
and would fail on any host: a module or image that is not allowlisted, a task
that exceeds its wall clock, a payload naming a local dataset this host never
advertised.

### 2.1 So the counter does not decide — the doctor does

At the threshold, the agent runs `run_checks(pull=False)` — the same six
checks minus the pull, exactly what the startup gate runs.

- **All checks pass** → this host is fine and the *jobs* are failing. Log that
  distinction explicitly, reset the counter, keep working.
- **Any check fails** → stop claiming, print the failing checks with their
  fixes, exit non-zero.

This is the point of the slice. A blind counter guesses; this measures. And a
host that stops working mid-session gets told *what* broke, in the same words
the doctor would have used at startup.

Threshold: **3** consecutive, `--max-consecutive-failures` /
`FLASHNODE_MAX_CONSECUTIVE_FAILURES` to override, `0` disables.

### 2.1.1 The loop must not import the doctor

The check has to run inside `ExecutorLoop.run` — only the loop can stop
claiming. But `loop.py` importing `flashnode.doctor` would be wrong twice
over.

**It risks a circular import.** `doctor.py` imports
`flashnode.executor.hardening` at module level, which initialises the
`flashnode.executor` package, whose `__init__` imports `loop`. Loop →
doctor → executor → loop is a cycle that resolves or explodes depending on
which module Python reaches first, which is the worst kind of bug to ship in
an agent running on machines we cannot reach.

**And it inverts the dependency.** The executor is the correctness core; the
doctor is a diagnostic built on top of it. Core code should not reach upward.

So `ExecutorLoop.__init__` takes `health_check: Callable[[], list] | None =
None`, defaulting to `None` (quarantine disabled — every existing caller and
test keeps working untouched). `cli.py` passes
`lambda: run_checks(pull=False)`. The loop knows only that it received
something callable returning a list of objects with a `.status`; it never
learns what a check is. That also makes the quarantine testable with a
two-line stub instead of a Docker daemon.

The loop exposes `quarantined: bool` so `cli.py` can return exit code 2
after `run()` returns.

### 2.2 Why not just re-run the doctor on a timer

Because a healthy idle host would pay for checks it does not need, and a
broken host would keep claiming for up to a full interval. Failures are the
signal that something changed; polling is a worse version of the same thing.

## 3. Part A — the status view

```
flashnode 0.3.1 · flashml-api.onrender.com · up 2h14m
  running    fed-2e2d4d6ab57f  ·  attempt 1  ·  38s
  session    12 accepted   0 failed
  heartbeat  2s ago
```

Idle, and the distinction that matters most to a worried volunteer:

```
flashnode 0.3.1 · flashml-api.onrender.com · up 2h14m
  waiting    no work queued — this is normal
  session    12 accepted   0 failed
  heartbeat  2s ago
```

### 3.1 It reads state; it does not drive the loop

`ExecutorLoop` is correctness-critical — leases, heartbeats, checkpoint
relay. The view must not sit in its control flow.

So the loop gains **plain attributes** and nothing else: `tasks_failed`,
`consecutive_failures`, `current_task`, `current_task_started`,
`current_attempt`. A separate daemon thread reads them on a timer and
redraws. No callbacks, no observer registry, no new failure mode in the claim
path — if the renderer thread dies, work continues.

### 3.2 TTY only, and the JSON stays available

The view redraws in place with ANSI cursor movement, which is corruption in a
pipe, a log file, or a systemd unit. So:

- `sys.stdout.isatty()` **and** not `--log-json` → live view, and the JSON
  log handler is removed (two writers redrawing one terminal is unreadable).
- Otherwise → today's behaviour, byte for byte.

`--log-json` forces the old behaviour on a TTY, for anyone debugging.

### 3.3 What it deliberately does not show

**No credits or contribution totals.** `public.contributions` lives in the
cloud database and the agent has no endpoint to read its own total. Adding
one is a cloud-API change, and this slice stays inside flashnode. Session
counters are honest and cost nothing. Recorded as a gap in §5.

**No watts.** Not deferred for effort — it should not be built. CPU power has
no portable reading (macOS `powermetrics` needs sudo, Linux RAPL is
root-restricted, Windows exposes nothing simple), so any figure shown to the
laptop tier would be an estimate presented as a measurement. `ProbeResult`'s
rule is that a failed measurement is recorded honestly and *never fabricated*;
a watt display would violate it by construction. GPU power is genuinely
readable via NVML and already has a home in `GpuSample.power_watts` — that
belongs with the GPU tier, not here.

Separately, `POSITIONING_LOG.md` records that a volunteer laptop nets
**−$3.42/month**, and that barter survives precisely because "no money moves,
so the electricity comparison is never made." A watt meter makes it. That is a
product decision about which model FlashML is in, and it should be made
deliberately rather than arrived at through a status line.

**No FLOPS.** `benchmark/`'s docstring is explicit: raw numbers each, **never
a single synthetic score**, because a composite hides the bottleneck that
matters for a given workload. The probe suite is a separate slice.

## 4. Shape

| File | Change |
|---|---|
| `flashnode/status.py` | new — `StatusView`, the renderer thread, `format_status` |
| `flashnode/executor/loop.py` | five plain counters, `health_check` param, `quarantined` flag |
| `flashnode/agent/cli.py` | `--log-json`, `--max-consecutive-failures`, install the view, wire `health_check`, return 2 on quarantine |

`run_checks` is reused unchanged, injected rather than imported (§2.1.1).

## 5. Known gaps this leaves

1. **No contribution total** (§3.3) — session counters only, so restarting the
   agent resets what the owner sees.
2. **Coordinator-side quarantine still absent.** The agent stops volunteering;
   the coordinator will still route to a host that has gone silent in some
   other way. §1.1 stands.
3. **A job that fails on every host** still burns three attempts here before
   the doctor clears the machine. Correct — the agent cannot know a job is
   universally broken — but it means the counter resets rather than escalating.
4. **The view is a snapshot, not history.** No log of past sessions, no
   per-job breakdown. The console has that.

## 6. Definition of done

1. `flashnode work` on a TTY shows the live view; the JSON handler is removed.
2. Non-TTY output is byte-for-byte what it is today, and `--log-json` forces
   that on a TTY too.
3. Idle reads "no work queued — this is normal", not silence.
4. `LeaseLost` and `accepted=False` do **not** increment
   `consecutive_failures`; `TaskExecutionError` does. Asserted per case.
5. One success resets the counter.
6. At the threshold with a **healthy** host: the counter resets, work
   continues, and the log says the jobs are failing rather than the machine.
7. At the threshold with an **unhealthy** host: no further claim is made, the
   failing checks and their fixes are printed, exit code 2. Asserted by
   proving `claim` is not called again.
8. `--max-consecutive-failures 0` disables the quarantine.
9. The renderer thread dying does not stop or corrupt the work loop.
10. `ExecutorLoop` does not import `flashnode.doctor` — asserted, because the
    cycle in §2.1.1 resolves or explodes by import order and would not show up
    in a passing suite.
11. An `ExecutorLoop` built without `health_check` behaves exactly as today.
12. flashnode suite green and above 257; counts recorded in `PROGRESS.md`.

## 7. Out of scope

The admission probes (`benchmark/`), telemetry on the heartbeat (needs an
additive `NodeHeartbeat.telemetry` field and a release), GPU power, the
desktop app, coordinator-side quarantine. The probe suite should be sequenced
against the GPU protocol work now on `main`.
