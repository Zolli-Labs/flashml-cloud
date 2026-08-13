# The debugging story — a held FC task, two root causes

**Status: evidence packaged, 2026-08-13.** Written for judges against C-6.6
("one real debugging story: how you used Trace/logs/metrics to locate and
solve a specific problem") and D-4 (correlation id + observability). Every
claim below cites the source section it comes from. Nothing here is inferred
beyond what those sections state; where a fix is not yet released, that is
said plainly rather than rounded up to "done."

Sources: `2026-08-13-full-pipeline-test-findings.md` (§2.4, §6, §6.1) and
`2026-08-13-bug-audit-leases-fc-fragmented-devices.md` (§0.1, §3) — both
written the same night this story happened, from live observation and a
six-agent static audit, not from memory.

---

## 1. The symptom, as the user saw it

An Alibaba FC Agent Sandbox joined the fleet, claimed a task, unpacked its
datasets, and then went quiet. The lease kept renewing — about every 23
seconds — with no checkpoint, no log line, no progress artifact of any kind.
The console did not say "stuck." It said the opposite: `deriveStallReason`'s
healthy branch rendered *"1 task in flight. Next lease deadline in 23s,"*
forever, because the coordinator treats heartbeat renewal as proof of life,
not proof of work (`bug-audit` §0.1). A sandbox that was dead in every way
that mattered looked, from the console, exactly like one that was fine.

That gap — the fleet claims a capability, and completion is a separate,
unobserved fact — is what the rest of this document is about
(`full-pipeline-test-findings.md`, opening summary).

---

## 2. The hunt — what was actually consulted, and what each surface ruled in or out

The stall surfaced on the same night as a second, concurrent incident (a
14:00Z connection-exhaustion outage, §6 below), so the investigation used
five different surfaces, not one:

| Surface | What it showed | Ruled in / ruled out |
|---|---|---|
| **Job event ledger** — `GET /v1alpha1/jobs/{id}/events` | Lease renewals ~every 23 s with zero progress events; later, in the post-fix re-test, task-005 handed back to the *same* node four times before `TASK_EXHAUSTED` | Ruled **in**: the worker was alive at the protocol level and renewing normally — this is what made the stall invisible rather than an obvious crash (`bug-audit` §0.1; `full-pipeline` §6.1) |
| **flashnode work log on the revived sandbox** — `/home/user/.flashml/flashnode-work.log` | `EnvironmentBuildError` lines at ~30-minute intervals | Ruled **in** the silent-mirror-build hypothesis over two competing candidates (a SIGKILL-that-never-reaps, and slow-trickle HTTP) — the audit names this the one-read confirmation: `EnvironmentBuildError` at ~30-min spacing proves candidate 1; a single lease renewed >65 min with the runner already started would instead point at the kill-that-never-reaps candidate (`bug-audit` §0.1 point 1, §6.1) |
| **Correlation-id trace route** — `GET /v1alpha1/trace/{correlation_id}` | Walks `session_id → sandbox_id → job_id → task_id → lease_id` for jobs and sandbox sessions that carry the id | Ruled **out** a fully automated join into the coordinator's own event ledger — the ledger stream an operator actually reads to diagnose a stall does not carry the id yet, so the FC session had to be joined to its coordinator-side task by its `fc-<session_id>` node-id instead (`bug-audit` §3 — see §5 below, this is the honest limit) |
| **Render logs** | The pod's own log stream showed Render's full 502 HTML page recorded as an application error string, and instance-failure emails arriving every ~3 minutes | Ruled **out** Render rate-limiting or platform misbehavior; ruled **in** that Render was correctly restarting an instance that could not reach its database (`full-pipeline` §6) |
| **Supabase pooler stats** | Session-pooler client count against the 15-client cap; after the fix, 25 concurrent connections sustained against the same 15-cap benchmark | Ruled **in** `EMAXCONNSESSION` (connection exhaustion) as the outage's root cause, and confirmed the `:6543` transaction-pooler mitigation actually raised the ceiling rather than just moving the failure (`full-pipeline` §6) |

The outage and the FC stall were not the same bug, but they shared a night
and a re-test: after the pooler fix held (30/30 concurrent requests where 15
used to be the death line), the same job was re-run and the FC sandbox
*still* failed — for a third, unrelated reason, described next
(`full-pipeline` §6.1).

---

## 3. Two distinct root causes

### 3.1 Silent Aliyun-mirror pip build + heartbeat that isn't progress

`trusted_runner.py:69` → `environments.py:394-396` ran `pip install -r
requirements.txt` with no `-i`, no `--no-input`, and no version-check
suppression. Inside the FC sandbox that inherits `/etc/pip.conf`, which pins
the lagging Aliyun mirror. On 2 vCPU pulling torch-class pins from that
mirror, the build ran silently toward the 1800 s ceiling — zero log output,
no line anywhere bracketing an environment build. The per-job dependency
cooldown meant each of three consecutive poisoned jobs paid its own fresh
~30-minute silent build (`full-pipeline` §2.4; `bug-audit` §0.1 candidate 1).

This alone would have expired and requeued, except the coordinator has no
way to distinguish a renewing-but-frozen worker from a working one: heartbeat
renewal is an independent daemon thread that renews no matter what the work
thread is doing, renewal is unbounded, and there is no force-expire or
revoke primitive — only the passive sweep, which a renewing lease keeps
pushing forward (`bug-audit` §0.1, C11). A rented FC sandbox held this way
keeps billing until TTL or an explicit kill.

Separately, when a build *did* finish, the trusted tier capped every task at
600 s regardless of `FLASHNODE_TASK_TIMEOUT_S` — `TrustedArgvRunner()` was
built bare and silently ignored the env var (`full-pipeline` §2.4, audit C1).

### 3.2 The cp312 wheel cliff on FC's Python 3.13

Found in the post-fix re-test (job `292eb5c67fc9`), after the pooler fix had
already resolved the connection exhaustion for that run. The FC sandbox
image ships Python 3.13.13; the curated sklearn manifest pins
`numpy==1.26.4`, whose published wheels stop at cp312. Every environment
build on FC therefore fell to a source build and died. This was only visible
at all because of the log-bracketing fix from §3.1 — before it, a doomed pip
resolve and a hang were indistinguishable. With no cooldown or node
exclusion, the scheduler then handed the same task back to the same
incompatible FC node four times before `TASK_EXHAUSTED`, burning a healthy
task's entire attempt budget while two capable machines sat idle
(`full-pipeline` §6.1, audit C15).

---

## 4. Fixes, and their actual state

| Fix | Repo / branch | Evidence | State |
|---|---|---|---|
| Index-pinned (`PIP_INDEX_URL` to real PyPI, requirements-file `--index-url` still wins), `--no-input`, and log bracketing (dependency count + elapsed time) around every environment build | `flashml` public repo, `fix/trusted-tier-execution` | commit `c133c5f`, 588 tests passed | **Pushed** |
| `uv venv` pinned to the agent's own interpreter (`--python sys.executable`) instead of whatever Python leads PATH | same branch | commit `46df740`, 586 tests passed | **Pushed** |
| Trusted-tier task timeout honors `FLASHNODE_TASK_TIMEOUT_S` (same 3600 s default as the docker tier) instead of a silent 600 s cap | same branch | part of `c133c5f` | **Pushed** |
| Progress-scoped renewal budget + operator force-expire (closes C11/C16: a lease that renews with zero progress artifacts is refused renewal instead of renewing forever) | `flashml`, `fix/lease-zombie-hardening`, merged into `integration/2026-08-13` | commits `aa1fdfb`, `350b05b`, `d93cf10`, `f346637` | **Implemented on the integration branch — release-gated, not yet published** |
| Split the churn budget from the failure budget (closes C12: an expiry from infrastructure churn no longer counts the same as a genuine `fail()`) | same branch | commit `715af42` | **Implemented on the integration branch — release-gated, not yet published** |

"Release-gated" is literal: `flashml-cloud` consumes the runtime as a pinned
PyPI version (`flashruntime==0.6.0` as of 2026-08-11), so none of the
integration-branch fixes reach a deployed coordinator until they are
released and the pin is bumped at all four sites. Nothing above is claimed
as live in production.

---

## 5. The honest limit

The correlation id (D-4) survives a hibernation boundary by design — it
lives in a column, not a request context, so a process that never minted it
can read it back after a sandbox pauses and wakes
(`observability.py` header; `2026-08-12-observability-and-verification-gaps.md`
§3 D-1/D-2). `GET /v1alpha1/trace/{correlation_id}` walks that chain across
jobs and sandbox sessions today.

It stops at the coordinator. The event ledger — the actual stream an
operator reads to diagnose a live stall — does not carry the correlation id;
the join from a coordinator-side task to its FC worker is done today by
matching the worker's self-reported `fc-<session_id>` node-id, not by the
correlation id (`bug-audit` §3). That gap is why the confirmation step in
§2 above had to be a manual node revive and a log-file read instead of one
trace call. It is open, not fixed, and it is not claimed otherwise anywhere
in this document.

---

## 6. What to show the judges

1. **The trace call** — `GET /v1alpha1/trace/{correlation_id}` on the
   affected job, showing the chain of jobs/sessions the id already spans.
2. **The ledger dump** — `GET /v1alpha1/jobs/{id}/events` on the stalled
   job: renewals every ~23 s, zero progress events, in contrast with a
   healthy job's `TASK_COMMIT_ACCEPTED` cadence.
3. **The work-log line** — `/home/user/.flashml/flashnode-work.log` from
   the revived FC sandbox: `EnvironmentBuildError` at ~30-minute spacing.
4. **Before/after env-build log lines** — silent (no line at all) before
   `c133c5f`, versus bracketed with dependency count and elapsed time after.
5. **The one honest gap** — say out loud that the trace stops at the
   coordinator and the FC join is still by node-id, not by correlation id.
