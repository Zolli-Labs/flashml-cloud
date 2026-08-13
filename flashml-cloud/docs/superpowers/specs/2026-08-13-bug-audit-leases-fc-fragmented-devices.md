# Bug audit — leases, the FC stall, and fragmented devices — 2026-08-13

**Method:** a multi-agent static audit (six parallel reviewers over flashnode,
flashruntime, and flashml-cloud, every finding verified at file:line by
reading the code; nothing was executed, nothing was edited). Complements the
same-day **full-pipeline test findings** doc, which observed these systems
live; this audit answers the two questions that doc parked — *why does the
Alibaba FC worker hold a task forever?* and *what actually happens when a
lease runs out before the node is done?* — and sweeps for everything adjacent.

Cross-references: `2026-08-13-full-pipeline-test-findings.md` (esp. §2.4 FC
stall, §4.1 progress watchdog), the trusted-tier execution contract
(2026-08-09), and the flashnode/flashruntime AGENTS.md known-gaps lists.

---

## 0. The two headline answers

### 0.1 Why the FC sandbox "holds the task and never gives it back"

Two layers, and both are needed to explain the observation (claim → unpack →
`datasets ready` → silence forever, renewing every ~23 s).

**Worker layer — what the sandbox is actually doing.** Ranked candidates,
strongest first (from tracing every call after the `datasets ready` log at
`flashnode/executor/loop.py:462`):

1. **A silent ~30-minute environment build against the Aliyun pip mirror,
   repeated per job.** `trusted_runner.py:69` → `environments.py:394-396`
   runs `pip install -r requirements.txt` with **no `-i`, no `--no-input`,
   no version-check suppression** — so inside FC it inherits
   `/etc/pip.conf`, which pins the **lagging Aliyun mirror**. This is the
   *exact* trap `sandbox_bootstrap.py`'s own `install_command`
   (`sandbox_bootstrap.py:370-383`) documents and guards against for
   installing the agent — and does **not** guard for the agent's
   task-dependency builds. On 2 vCPU pulling torch-class pins from that
   mirror, the build runs toward `_run`'s 1800 s ceiling
   (`environments.py:57,408`), **with zero log output** — no line anywhere
   brackets an environment build. Then `EnvironmentBuildError` → fail →
   requeue; the dep-cooldown (`loop.py:306`) is keyed **per job**, so each
   of the three consecutive poisoned jobs paid its own fresh silent build.
   From the coordinator: renewals every ~23 s, no progress, ~30 min per
   job. Matches every observed detail, including why RunPod pods (no
   pip.conf mirror pin) failed *fast* instead.
   **One-read confirmation:** `/home/user/.flashml/flashnode-work.log` on a
   revived sandbox — `task failed`/`EnvironmentBuildError` lines at ~30 min
   intervals prove it; a single lease_id renewed > 65 min disproves it.
2. **A kill that never reaps.** `subprocess.run(timeout=…)` (SIGKILL to the
   direct child, then an *unbounded* `wait()`) — a child stuck in
   uninterruptible sleep under FC's virtualized I/O ignores SIGKILL and
   `wait()` blocks forever. The only candidate that is *literally* forever
   within one lease.
3. **Slow-trickle HTTP.** urllib's 15 s timeout is per-socket-op, not
   per-request: a half-dead cloudflared tunnel delivering a byte every
   <15 s extends `checkpoint_latest`/`download_artifact`
   (`client.py:103,240-246`) arbitrarily, silently.

**Protocol layer — why nothing could see or stop it.** The coordinator
treats *heartbeat* (liveness) as if it were *progress*. `_AttemptHeartbeat`
(`loop.py:74-97`) is an independent daemon thread: it renews no matter what
the work thread is doing.

- Renewal is unbounded — no renewal budget, no total-lease-lifetime cap, no
  progress requirement (`leases/manager.py:151-163`).
- There is **no force-expire, revoke-lease, or coordinator-side quarantine
  anywhere** — the only reclaim mechanism in the entire runtime is the
  passive sweep hitting a deadline that renewals keep pushing forward.
  "Revoke" today = `POST /v1alpha1/machines/{id}/revoke` (cloud
  `app.py:3640`), which kills the machine **credential** — the lease still
  dies only by natural expiry, and the FC sandbox is a separate cleanup
  call.
- Every watchdog keys on the same deadline: `reconcile_expired_attempts`
  (`db.py:1922`) matches only passed deadlines; `machines.last_seen_at` is
  refreshed **by the attempt-heartbeat path** (`app.py:8246`); the capacity
  reconciler spares any machine with fresh `last_seen_at` and in-flight work
  (`capacity/reconcile.py:642`) — so a stalled worker on a **rented GPU
  keeps it alive and billing** indefinitely.
- The console actively reassures: the stalled case lands in
  `deriveStallReason`'s healthy branch (`apps/web/lib/job-activity.ts:157`)
  — "1 task in flight. Next lease deadline in 23s," forever.
- FC billing continues until TTL (1 h default) or explicit kill
  (`alibaba_sandbox.py:74,1157-1159`).

So: the worker stalls for mundane reasons, and the platform is structurally
unable to notice, name, or break the hold. Fix shapes in §5.

### 0.2 What happens today when a lease expires but the node isn't done

The chain, verified end to end: sweep expires the lease and requeues
(`manager.py:222-242`) → another node claims attempt 2 → the original node
**keeps computing** (nothing kills the runner; `hb.lost` is only read
*after* `runner.run()` returns, `loop.py:514`) → its eventual commit is
rejected (idempotency holds, `manager.py:181-190`). Correct at the commit
gate — and defective everywhere else:

1. **The zombie's checkpoint relay keeps writing.** `_CheckpointRelay`
   (`loop.py:100-147`) consults neither `hb.lost` nor any stop signal;
   checkpoint keys are per **task**, not per attempt. In non-enforcing mode
   (any self-hosted coordinator; enforcement exists only under
   `FLASHML_REQUIRE_NODE_AUTH`, set in prod/dev via `render.yaml`) the
   catalog accepts every commit with **no lease-liveness check**
   (`catalog.py:65-124`) and `latest_valid()` breaks step ties by newest
   `created` (`catalog.py:166-169`) — **last writer wins**, so attempt 3 can
   resume attempt 1's diverged lineage. Silent training corruption.
2. **The zombie can overwrite accepted results.** Output upload
   (`loop.py:526-534`) never re-checks `hb.lost`; non-enforcing
   `put_artifact` has no authorization (`modea.py:949-950`), and
   `job_result` reads `metrics.json` from disk (`modea.py:1247-1264`) — the
   job-level answer silently becomes the *rejected* attempt's bytes. One
   slow node, no attacker.
3. **The expiry burned an attempt.** `claim` increments `attempts_used`
   (`manager.py:127`); `_release` exhausts at `max_attempts`
   (`manager.py:297-311`). Three lease losses — pure infrastructure churn,
   every worker healthy — permanently FAIL a task **no node ever failed**.
   On fragmented devices lease loss is the *common* case. Probably the
   single highest-leverage correctness bug in the lease core.
4. **The blackout tolerance is ~40 s against a 60 s default lease.**
   Default `lease_seconds=60` (`protocol/v1alpha1.py:464`, `modea.py:256`),
   heartbeat every ~20 s (`loop.py:82`), but one failed renewal retries
   internally for up to ~63 s (4 × 15 s + backoff, `client.py:38-39`) and a
   failure is just logged (`loop.py:93-94`). Any coordinator/network blip
   > ~40 s expires the lease under a healthy, working node — feeding
   defect 3.
5. **The failure reason is lost.** A late `fail()` raises server-side
   (`manager.py:338-342`), `client.fail` ignores the status
   (`client.py:189-190`) — the ledger records only "heartbeat deadline
   passed." Late *commits* are recorded as evidence; late *failures* are
   dropped.

---

## 1. Critical — fix before real users (P0/P1)

### flashnode (needs a 0.4.1 release to reach testers — see release gate)

| # | Finding | Where | Scenario |
|---|---|---|---|
| C1 | **Trusted tier hard-caps every task at 600 s** — `TrustedArgvRunner()` built bare, `FLASHNODE_TASK_TIMEOUT_S` silently ignored (docker tier reads it, default 3600) | `trusted_runner.py:27`, `agent/cli.py:331` | Any real training run >10 min on pods/Colab/FC — the tier's entire audience — times out, requeues, burns all attempts. Looks like an infinite mystery failure; the env var "fix" does nothing |
| C2 | **Env builds are silent and mirror-poisoned** — no index pin, no `--no-input`, no log bracketing, dep-cooldown keyed per job | `environments.py:394-396`, `loop.py:306` | The FC stall (§0.1). Also any China-region/proxied host |
| C3 | **No process-group kill** — timeout SIGKILLs the direct child only; no `start_new_session`/`killpg` | `task_logs.py:211`, `trusted_runner.py:95`, `runner.py:195` | `torchrun`/dataloader children survive as orphans holding GPU memory; next attempt on the host CUDA-OOMs mysteriously. Grandchildren holding the stdout pipe also turn fast exits into false wall-clock timeouts |
| C4 | **No agent-death reconciliation** — no startup sweep of `flashnode-*` containers/workdirs; container wall-clock lives only in the dead agent | grep-verified absent | Agent SIGKILL/OOM/lid-close ⇒ container runs forever unsupervised on a volunteer's machine; trusted tier leaks the process tree + tempdir |
| C5 | **Artifacts fully buffered in RAM both directions; plain-input downloads have no size cap at all** | `client.py:104,246,249` | 2 GB FC sandbox or 8 GB laptop OOMs on a big checkpoint — and the OOM kills the *agent* (renewals stop, machine silently leaves pool, container orphaned per C4) |
| C6 | **`IncompleteRead` escapes the retry/exception taxonomy and counts against the host** | `client.py:110` vs retry tuple | Wi-Fi drop mid-download × 3 ⇒ healthy volunteer walks the quarantine path — the exact 2026-08-06 mis-attribution, one exception class over |
| C7 | **Lease loss during upload also counts against the host (enforcing mode)** — upload 403 surfaces as bare RuntimeError → last-line-of-defence | `client.py:248-255`, `modea.py:934-961`, `loop.py:366-377` | Lease expires mid-multi-file-upload ⇒ quarantine-path strike for losing a race |
| C8 | **Heartbeat interval mixes server and client clocks** — `(server_deadline − client_now)/3`, computed once; `Lease` doesn't carry `lease_seconds` | `loop.py:82`, `protocol/v1alpha1.py:485-494` | A no-NTP device (Pi, woken laptop) with clock behind > ~2× window renews *after* expiry — loses every healthy lease, re-claims (no cooldown), burns all attempts, gets rated "shaky." Clock ahead ⇒ 2 s renewal spam |
| C9 | **`claim` transport-retry is not idempotent** — response lost to timeout ⇒ retry claims a *different* task; first lease is orphaned, attempt burned | `client.py:101-115,150` | Two lost responses on a `max_attempts=3` task ⇒ FAILED with zero executions; each orphan stalls the queue a full lease window |
| C10 | **Kill-on-lease-lost doesn't exist** — `hb.lost` read only after the run; cancel reaches the node only as a refused heartbeat it ignores mid-run | `loop.py:514` | Cancelling a 6-hour job keeps burning the volunteer's GPU for 6 hours; duplicate execution wastes the fleet |

### flashruntime (coordinator)

| # | Finding | Where | Scenario |
|---|---|---|---|
| C11 | **Heartbeat ≡ progress; renewal unbounded; no force-expire/revoke/quarantine primitive anywhere** | `leases/manager.py:151-163`; grep-verified | §0.1 — the FC hold is unbreakable without killing the machine credential |
| C12 | **Lease churn burns the attempt budget** — every claim increments `attempts_used`, expiry-requeues included | `manager.py:127,297-311` | §0.2.3 — tasks FAIL from infrastructure weather; the common case on fragmented devices |
| C13 | **Zombie attempts clobber checkpoints and accepted results in non-enforcing mode** — no lease-liveness check in the catalog; `latest_valid()` last-writer-wins; `put_artifact` unauthorized | `catalog.py:65-124,166-169`, `modea.py:949-950,1339-1356` | §0.2.1–2. Enforcing mode reduces it to a 403 hammer every 0.3 s |
| C14 | **Lease endpoints share one event loop with direct synchronous SQLite** — `record_event` and the 2 s sweeper's `ledger.list_jobs()` block the loop (the ledger docstring *claims* `asyncio.to_thread`; there are zero `to_thread` calls in the service) | `service/app.py:196,210-211` | Slow disk or a grown jobs table stalls every claim/heartbeat/complete fleet-wide. (Silver lining: single-loop serialization is also why there's no double-claim race today) |
| C15 | **Deterministic failures burn the whole fleet** — `recovery.classify`/`decide` is never called on the lease path (zero grep hits in `service/`); `APPLICATION_ERROR → FAIL_JOB` never fires; requeue is instant with no cooldown | `recovery/policy.py:38-44`, `manager.py:297-311` | A poison task fails N distinct volunteers in seconds; a 100-task job = 100 × max_attempts doomed env builds. Spec §5 cooldown remains unexecuted |

### flashml-cloud

| # | Finding | Where | Scenario |
|---|---|---|---|
| C16 | **No watchdog can see a stalled-renewing worker; the console narrates it as healthy** | `db.py:1919-1922`, `app.py:8246,8268`, `job-activity.ts:157` | §0.1. The data to say "renewed 400× with zero checkpoint parts" already flows through the API; no code reads it |
| C17 | **Federated runs cannot be cancelled at all** — 501 | `app.py:6379` | A stalled worker in a federated round + no cancel = the worst stuck-work story in the product, live in prod |
| C18 | **A stalled renewing worker on rented GPU bills forever** — capacity reconciler spares machines on liveness alone | `capacity/reconcile.py:642`, `app.py:8219-8252` | Money leak bounded only by the human noticing (RunPod budget memory: $10 cap) |
| C19 | **FC evaluation failure leaks the coordinator job** — `_fail`/`cleanup_session` kills sandbox + credential but never cancels `evaluation_job_id` | `sandbox_orchestrator.py:1697` | Job sits PENDING forever in a pool whose only machine was just revoked |

---

## 2. High-value correctness (fix soon)

- **Retried `complete` tells the winner it lost** — coordinator rejects any
  commit on a COMPLETED task, including from `accepted_attempt_id` itself;
  should answer accepted=true idempotently (`manager.py:181-190`). Skews
  contribution accounting on exactly the flaky networks volunteers have.
- **Resume trusts bytes blindly** — `download_artifact` never verifies the
  manifest's sha256 (`loop.py:471`, `client.py:240-246`), though
  `checkpoint/local.py:71-87` re-verifies on every read. Also resumes from
  `parts[0]` only — first multi-part checkpoint silently loses state
  (`checkpoints.py:35-43`).
- **Coordinator restart silently restarts long tasks from step 0** —
  in-memory catalog + durable SQLite leases; successor's `checkpoint_latest`
  404s and the resume input is skipped with **no log line, no event**
  (`loop.py:470`). The known gap's *silence* is its own defect.
- **The validation ladder is dead code** — `mark_restore_verified` /
  `quarantine` have zero call sites (`catalog.py:128-140`); a
  garbage-but-hash-valid checkpoint poisons every subsequent attempt until
  exhaustion, and nothing ever invalidates it.
- **`timeout_seconds` semantics trap** — flashml.yaml's field clamps into
  `lease_seconds` (`compile.py:182-186`): it does not bound runtime, it
  *extends single-machine hold time* (86400 → 3600 s of stall pinning).
- **Trusted tier drops the payload `env` block silently**
  (`trusted_runner.py:52-101` vs `argv_runner.py:69-73`) — HF_TOKEN/W&B
  jobs work on docker hosts and 401 on pods with nothing naming the cause.
- **`/work` rewrite misses `--flag=/work/...` equals-joined tokens**
  (`trusted_runner.py:83-88`) — same class as the argparse-default gap the
  pipeline doc found, on the path the rewrite claims to cover.
- **Exit-137/OOM never mapped** (`docker_runner.py:125-127`,
  `argv_runner.py:120-122`; no `docker inspect .State.OOMKilled`) — a
  memory-hungry job reads as a host fault and pushes small hosts toward
  quarantine; no payload-requirements vs host-capacity check exists.
- **Trusted tier has zero resource limits and no output-size cap**
  (`argv_runner.py:139-143` docker-only) — a buggy task fills a volunteer's
  disk, then uploads unbounded bytes.
- **`cancel_task` emits no event** (`manager.py:76-82`) — the ledger (the
  "status is derived from events" invariant) is blind to cancellation, and
  console attempt lanes stay open forever (`job-activity.ts:65`).
- **Sandbox pause-while-leased** — the FC prepare→pause→wake lifecycle can
  hibernate a worker holding a live lease; clock freezes, lease expires,
  woken work is discarded at 410. Nothing sequences "pause only when no
  lease held" (`sandbox_bootstrap.py` wake path).
- **k8s-profile `register()` retries a 403 forever** — `HTTPError ⊂
  URLError` (`daemon.py:57-71`); a bad join code spins silently. Same shape:
  any non-2xx on `claim` logs as "coordinator unreachable" forever
  (`client.py:150-156`) — a revoked credential is indistinguishable from an
  outage to the operator.
- **`sweep_stale_builds` deletes another agent's in-flight build** when two
  agents share a state dir (`environments.py:195,286-299`) — double-launch
  is reachable via the bootstrap's registration-timeout path
  (`sandbox_bootstrap.py:810-818`).
- **`_CheckpointRelay.finish()` races its own thread** — 10 s join vs ≤63 s
  retry cycle; unsynchronized `_shipped`; tempdir deleted under a mid-read
  ship (`loop.py:120-125`).
- **Non-410 heartbeat errors retry as "transient" forever**
  (`loop.py:88-94`) — a coordinator that restarted with an in-memory store
  answers 404 and the worker completes a task whose lease no longer exists.

---

## 3. UX on fragmented devices

- **Invisible refusal**: an ineligible node gets 204 — byte-identical to
  "queue empty" (`scheduler/__init__.py` documents this deliberately). The
  pipeline doc's finding 2.1 cost an hour to this. Neither the node
  ("waiting — no work queued") nor the task page ("pending") names the
  unmet capability. Fix is a diagnostic surface, not a placement change.
- **Console reassures during a stall** (C16); "1 machines online"
  pluralization already tracked in the pipeline doc.
- **No env-build bracketing in logs** — a 30-min install is
  indistinguishable from a hang in `--log-json` *and* in
  `verify_worker`'s counters-moved probe (`sandbox_bootstrap.py:996-1001`
  reports a doomed pip as "claiming/healthy").
- **Correlation id stops at the coordinator** — minted at job creation,
  joined through attempts and sessions, but the coordinator's event ledger
  (the stream an operator reads to diagnose a stall) never carries it
  (`app.py:6066` proxy); `fc-<session_id>` node-ids are the de-facto key.
- **Signal deaths render as `task exited -9: `** with an empty tail and no
  "likely OOM" hint (`trusted_runner.py:110`).
- **`--gpus N` on the trusted tier scopes nothing** — no
  `CUDA_VISIBLE_DEVICES`; a `gpus: 1` task on a 2-GPU host sees both
  (`hardening.py:186-207`).
- **`noexec /tmp` breaks triton/JIT** inside the sandbox
  (`hardening.py:238`) + read-only rootfs + no writable `$HOME` — the first
  GPU job that JIT-compiles fails mysteriously.

## 4. Not optimized (scale/cost, in rough order of onset)

1. `_find_lease` is O(all tasks × lease history) per heartbeat/complete/fail
   (`manager.py:328-336`); sweep O(leased) runs on **every claim** plus the
   2 s timer. Index leases by id.
2. `lease_history` and the checkpoint catalog's `_registered`/manifest maps
   grow forever (`catalog.py:62-63,118`); `latest_valid()` full-scans.
3. **Retention is stamped, validated, and never executed**
   (`modea.py:178-191` "Nothing reads this key yet"; `retention.py` has no
   service caller) — every artifact of every attempt persists until a
   manual `DELETE /jobs/{id}/artifacts`. Coordinator disk is a slow leak in
   fully-normal operation; orphaned parts from crash-between-upload-and-
   commit are never swept either.
4. Idle nodes poll claim at 1 Hz (`loop.py:635`) → at 100 nodes, 100
   sweep-carrying QPS on a SQLite-backed single event loop (C14 compounds).
5. Artifact bytes are double-buffered in RAM on both ends
   (`client.py:242`, `modea.py:1342`, 256 MB bound server-side only).
6. `capture_output=True` buffers child stdout unbounded in agent RAM
   (`task_logs.py:203-208`) — the 4 MiB cap applies to the file only.
7. Dataset capacity gate checks the *advertised* `dataset_cache_bytes`,
   never current free disk, and ignores already-cached shards
   (`scheduler/__init__.py:581-610`).
8. `_dep_cooldown` dict never pruned (`loop.py:197`); `max_tasks` counts
   accepted only (`loop.py:620`) so a failing node with `--max-tasks` never
   exits.

## 5. Fix shapes (the algorithm-level changes, ordered by leverage)

1. **Progress-scoped renewal budget** (closes C11/C16, the FC hold, and
   pipeline-doc 4.1): the coordinator already receives checkpoint part
   registrations on the renewal path's timeline. Rule: a lease may renew
   freely only while progress artifacts arrive; after N renewals (or T
   seconds) with zero checkpoint parts/log uploads, **refuse the renewal
   (410 + machine-readable reason)**. The worker's existing 410 path then
   aborts; pair with (3) so the abort kills the runner. Requeue **without**
   burning an attempt (see 2). N and T per task class; the demo relay ships
   a checkpoint every epoch, so even coarse thresholds work.
2. **Split the churn budget from the failure budget** (closes C12):
   `attempts_used` counts only explicit `fail()` and watchdog kills;
   expiries/orphans count in a separate, larger `expiries_seen` with its own
   cap (so a genuinely cursed task still terminates). One migration, two
   counter columns, and fragmented devices stop failing healthy jobs.
3. **Kill-on-lease-lost** (closes C10 and half of C13): give runners an
   `abort()` seam (docker: `docker kill <name>`; trusted: `killpg`); the
   heartbeat thread calls it the moment `lost` flips; check `hb.lost`
   again before the upload loop. This also makes *cancel* actually stop
   volunteer compute — today it doesn't, which is a trust problem for the
   "safe + free to host" pitch.
4. **Operator primitives** (closes the manual-revoke gap): coordinator
   routes for force-expire-lease (task requeues, attempt not burned) and
   node-quarantine (claims refused with a *named* reason, not 204); cloud
   passthrough + console buttons; federated cancel (C17) and eval-job
   cancel in `_fail` (C19).
5. **Carry `lease_seconds` in the Lease wire model** and derive the
   renewal interval from it, never from `deadline − now` (closes C8).
   While in the schema: consider a fencing token (attempt-scoped write
   prefix) so artifact PUTs and checkpoint commits can be validated even in
   non-enforcing deployments (closes C13 properly; enforcing-only checks
   are a config away from silent corruption).
6. **Environment-build hygiene** (closes C2/§0.1): `-i
   https://pypi.org/simple/ --no-input --disable-pip-version-check` (or
   honor an explicit index env), log lines bracketing the build with a
   duration, dep-cooldown keyed by requirements-hash (not job id), and a
   build timeout well under the lease-renewal observability horizon.
7. **Timeout unification** (closes C1): one `FLASHNODE_TASK_TIMEOUT_S`
   honored by every tier, and `timeout_seconds` in flashml.yaml either
   made a real runtime bound (enforced worker-side) or renamed — today it
   reads as a ceiling and behaves as stall-latency.

## 6. Confirmation steps (cheap, before any fix)

1. Revive/exec into the stalled FC sandbox lineage and read
   `/home/user/.flashml/flashnode-work.log` — settles §0.1 in one read.
2. `pip config list` inside an FC sandbox — confirms the mirror pin reaches
   task env builds.
3. On any test host: run a task that spawns a child (`python -c` forking a
   sleeper), let the wall clock fire, `ps` the orphan — confirms C3.
4. Two-node local rehearsal: claim on node A, firewall A's heartbeats for
   70 s, watch B claim attempt 2 while A's relay keeps committing —
   confirms C13's last-writer-wins with the in-memory store.

---

## Addendum (same day, follow-up audit stream)

A second, independent trace of the FC stall arrived after §0.1 was written.
It asserts the **kill-that-never-reaps chain (§0.1 candidate 2) as
co-leading**, with two links §0.1 did not have:

- On the 2 GB FC microVM, the eval child wedges in **D-state /
  memory-reclaim** (not only virtio-fs I/O); `subprocess.run`'s
  post-timeout sequence is `kill()` then an **untimed `wait()`** — the
  stdlib itself blocks forever on a child that cannot die.
- The **900 s EVALUATING watchdog does not survive an API redeploy** (it is
  in-process state; the 3600 s stale-transient reconciler is the only
  backstop after a restart), and nothing ever cancels the coordinator eval
  job — so the requeued zombie task is then **claimed by the next session's
  worker in the same shared pool**, which is precisely the observed
  "poisons consecutive jobs until manual revoke."

The two leading candidates (Aliyun-mirror build cycle vs D-state wait) are
distinguished by the same one-read check in §6.1: `EnvironmentBuildError`
lines at ~30 min intervals ⇒ candidate 1; a single lease renewed
indefinitely with the runner already started ⇒ candidate 2. **Both are
closed by the same §5 fixes** (progress-scoped renewal + kill via process
group with a bounded reap + operator force-expire), so no fix decision
hangs on the distinction.

*Provenance: six parallel read-only audit agents (Fable) + first-hand
verification of the lease manager, executor loop, protocol schema, and
scheduler by the coordinating session. No file was modified; the shared
checkout's git state was untouched. Findings whose file:line came from a
subagent were spot-checked where they drive a P0 rating.*
