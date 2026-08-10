# FlashML Handbook — the single entry point

> **Who this is for:** any agent or developer starting work in this
> workspace. Read this file top to bottom once; afterwards you only need
> `PROGRESS.md` (status + work log) and `SPRINT_PLAN.md` (what to do next).
> Everything here reflects **as-built** code, verified by tests — where
> something is aspiration, it is labeled *(planned)*.

---

## 1. What FlashML is

**One sentence:** FlashML turns fragmented computers — laptops, lab
workstations, cloud pools — into one fault-tolerant machine-learning
platform that sells *completed, verified work*, not machine-hours.

**The one idea underneath everything** (memorize this; every design
decision derives from it):

> Work is never *pushed* to a machine. A machine *pulls* a time-limited
> **lease** on a task, proves liveness with **heartbeats**, and only the
> first attempt to **commit a validated result** wins.

Consequences that fall out of this single pattern:
- A dead machine needs no handling: its lease expires, the task requeues.
- Laptops behind NAT participate: all connections are *outbound* HTTP.
- Retries can't corrupt results: commits are idempotent (first valid wins)
  and validated (uploaded bytes must hash to the claim).
- Contribution accounting is honest: credit accrues only for *accepted*
  work.

**What we sell / measure:** completed useful work per dollar and per
host-hour. Goodput, not uptime. See §7 metric definitions.

**Explicit non-goals** (from the master strategy report — do not drift):
no generic GPU marketplace, no "any device" promise, no internet-wide
synchronous training, no LLM agent making recovery decisions, no
blockchain, no zero-downtime claims. Recovery promise is **controlled
restart with bounded lost work**.

Strategy source of truth: `FlashML_Master_Product_Architecture_and_Strategy_Report.docx`.
Architecture decisions for the runtime: `FLASHRUNTIME_EVALUATION.md` +
`flashruntime/docs/adr/0003-reliability-runtime-first-planner-second.md`.

---

## 2. The three components

| Component | Repo | Visibility | One-line job | Depends on |
|---|---|---|---|---|
| **FlashRuntime** | `flashruntime/` | Public (Apache-2.0) | The protocol + the coordinator: plans jobs, expands them into tasks, runs the lease/checkpoint/recovery machinery, records everything in an append-only ledger | nothing |
| **FlashNode** | `flashnode/` | Public (Apache-2.0) | The agent on contributed machines: discovers capabilities, pulls leases, executes tasks in sandboxed tiers, relays checkpoints, commits results | flashruntime (protocol only) |
| **FlashML Cloud** | `flashml-cloud/` | **Private forever** | The business wrapper: users/orgs/auth, trust policy, billing/credits, managed dashboard, Alibaba deployment | flashruntime (protocol only) |

**The dependency rule (inviolable):** flashnode and flashml-cloud import
`flashruntime.protocol` and *nothing else* crosses repo boundaries. The
runtime imports neither app repo. Never copy private code into public
repos.

**The boundary principle:** the open runtime must be genuinely useful
without the cloud. A lab self-hosting the coordinator loses the dashboard
polish, join-code UX and credits — never execution capability. Self-test:
if the cloud API dies mid-job, running leases keep working (they do; the
coordinator owns them).

### 2.1 FlashRuntime — detailed breakdown

**Pure core** (`pip install` brings pydantic only — enforced by a
clean-venv smoke; keep it that way):

| Package | What it does (as built) | Key invariants |
|---|---|---|
| `protocol/` | Versioned wire schemas: `v1alpha1` (JobSpec, Event, Lease, TaskSpec/TaskAttempt, CheckpointManifest, FailureClass/RecoveryDecision, node messages) + `plan_v1alpha1` (PlanRequest → PlanReport/StrategyPlan) | Additive changes only within a version; security fields fail closed; every wire message carries its version |
| `planner/` | Deterministic strategy planner: curated candidate menu → memory/comm/time estimators → objective ranking → explained plan incl. rejections. Walkthrough: `flashruntime/docs/planner/README.md` | **Never imports ML frameworks**; same input ⇒ same plan (content-hashed); estimates labeled `static\|profiled\|ledger`; omit what inputs can't support |
| `leases/` | The Mode A state machine: claim/heartbeat/expiry-sweep/idempotent commit; `LeaseStore` seam with InMemory + **SqliteLeaseStore** (restart-durable, full lease history) | Time injected (no clock of its own); every transition emits a typed Event; late commits are *rejected with evidence*, never errors |
| `checkpoint/` | `CheckpointCatalog`: parts-first/**manifest-last** commit, validation ladder (hash→restore-verified→invalid), topology-compatible `latest_valid()`, `lost_work()` | No manifest ⇒ no checkpoint, by construction; quarantined manifests never selectable |
| `recovery/` | `classify(FailureSignals)` precedence taxonomy + `decide(failure, mode)` versioned policy table | Total over FailureClass × mode; correlated incidents FREEZE automation; deterministic — no scoring, no learning, no agent |

**Infrastructure ring** (opt-in extras):

| Package | What it does (as built) |
|---|---|
| `service/` | The coordinator (FastAPI): job submit + task expansion (`modea.py`), lease/node/artifact endpoints, checkpoint endpoints (`checkpoints.py`), SQLite event ledger (`ledger.py`), dashboard at `GET /` (`dashboard.py`), 2 s sweeper, KubeRay optional (`FLASHML_ENABLE_KUBERAY=0` = cloud-free) |
| `backends/` | `ExecutionBackend` contract + KubeRay backend (JobSpec → RayJob; Mode B). Pins: KubeRay 1.6.2, Ray 2.46.0 |
| `artifacts/` | `ArtifactStore` protocol: MinIO/S3-compatible + native Alibaba OSS |
| `engine/ algorithms/ adapters/ storage/` | Pre-K8s prototype (lazy-loaded, `[prototype]` extra) — kept working, not the future |
| `flashml_workloads/` | Runnable task modules: `sklearn_trial` (HPO), `kmeans_shard`+`kmeans_driver` (map/reduce over leases), `sgd_trainer` (checkpointable, **bit-identical resume**), `sharded_kmeans` (Ray/Mode B) |

**Execution modes** (keep them mentally separate):
- **Mode 0 — local single process.** First-class; the planner must be able
  to recommend *not* distributing.
- **Mode A — independent leased tasks.** The system's own protocol; any
  device with outbound HTTP. Blast radius of a failure = one task.
- **Mode B — coordinated training** (KubeRay pools). Recovery is *borrowed
  and recorded*: K8s replaces pods, the framework restarts, FlashRuntime
  owns checkpoint selection and the audited timeline. Blast radius = the
  worker group.
- **Mode C — per-step elastic** (torchft-class). Schema-reserved
  (`recovery_model`), not built.

### 2.2 FlashNode — detailed breakdown

| Package | What it does (as built) |
|---|---|
| `agent/` | CLI (`work` = device executor, `agent` = K8s DaemonSet reporter), K8s helper |
| `identity/` | Stable node ID file (Ed25519 signing *(planned)*) |
| `inventory/` | Capability discovery: psutil + K8s allocatable |
| `executor/client.py` | stdlib-urllib outbound HTTP: leases, artifacts, checkpoints; sends `X-FlashML-Join-Code`; **no third-party deps by policy** (every dep is attack surface on a stranger's machine) |
| `executor/runner.py` | Tier 1: allowlisted Python modules in a subprocess, wall-clock timeout, **scrubbed env** (only PATH/HOME/PYTHONPATH/LANG/LC_ALL/TMPDIR reach task code) |
| `executor/docker_runner.py` | Tier 2: allowlisted images, `--network none`, cpu/mem limits, `--read-only` + tmpfs, host-uid mapping. Same `run(payload, workdir, inputs) → outdir` interface — the loop never changes when the tier does |
| `executor/loop.py` | The work cycle: claim → download inputs → run (attempt-heartbeat thread) → upload outputs + sha256 → commit. Plus: **auto-re-register** when a node heartbeat is refused (coordinator restart), and the **checkpoint relay** (see §3.3) |
| `benchmark/ telemetry/ artifacts/ config/` | Scaffolds awaiting their vertical slice |

**Two heartbeats — never merge them:** attempt heartbeat → coordinator
(lease liveness; 410 = stop working); node heartbeat → registry
(online/offline; refusal = re-register).

**Task execution contract** (what every task module implements):
`python -m <module> --spec spec.json --out OUTDIR`; spec = `{task_id,
params, inputs: {name: local path}}`; must write `OUTDIR/metrics.json`;
checkpointable tasks additionally write `OUTDIR/ckpt/step-NNNNNN.json` and
accept `inputs.resume`. Both ends allowlist the module (coordinator at
expansion, executor at run) — fail closed twice.

### 2.3 FlashML Cloud — detailed breakdown

As built: `apps/api` (POC-era FastAPI node registry + job proxy — **not
yet re-pointed** at the current lease/checkpoint surface), `apps/web`
(Next.js dashboard), `infra/` (kustomize local + Alibaba ACK/ACR/OSS/SLS
profiles + sandbox tier), `scripts/alibaba/` (ACR build/push, render).
Its near-term work is Stage 5 (see `SPRINT_PLAN.md`). Long-term it owns
what must never be open: trust tiers, billing/credits/payouts, the
reliability graph, enterprise policy.

---

## 3. Architecture — local (as built, verified)

```
Developer ──JobSpec──▶ Coordinator (FastAPI :8100)
                        ├─ expansion: hyperparameter_search | sharded_kmeans → TaskSpecs
                        ├─ LeaseManager ← SqliteLeaseStore (leases.db — restart-durable)
                        ├─ Ledger (SQLite, append-only events; job state DERIVED)
                        ├─ CheckpointCatalog (per job::task; in-memory — see §6)
                        ├─ Local artifact host (dir; sha256; size cap)
                        ├─ Node registry (in-memory; join codes; agents re-register)
                        └─ Dashboard (GET /, self-contained HTML, 2 s poll)
                              ▲ outbound HTTP only
        ┌─────────────────────┴──────────────────────┐
   FlashNode A (laptop)                        FlashNode B (workstation)
   claim → download → run(tier) → relay ckpts → upload → commit
```

### 3.1 The claim cycle (Mode A, normal path)
1. Agent registers (join code if configured) → appears in `GET /v1alpha1/nodes`.
2. `POST /leases/claim {node_id}` → coordinator sweeps expired leases
   first, then leases the next PENDING task (payload: module, params,
   input URIs, output_prefix, image, optional checkpoint config).
3. Agent downloads inputs to a fresh workdir, starts the attempt-heartbeat
   thread (interval = lease window / 3), runs the task via its tier.
4. Agent uploads every `out/` file with sha256; `POST /attempts/{id}/complete
   {output_sha256}` → coordinator **re-hashes the artifact at the task's
   commit_key** — mismatch/missing ⇒ attempt fails and requeues; match ⇒
   COMMITTED, node credited.
5. Job state is *derived* from task counts on read + by the 2 s sweeper;
   terminal states emit JOB_SUCCEEDED/FAILED into the ledger.

### 3.2 The failure paths (all covered by tests)
- **Silent death** (power/network loss): heartbeats stop → lease deadline
  passes → sweep expires it → task PENDING again → another node claims
  (attempt_number+1). The dead node's late commit is rejected-with-evidence.
- **Polite failure** (task error): agent calls `fail(reason)` → requeue
  until `max_attempts`, then task FAILED → job FAILED.
- **Coordinator restart**: lease table + history rehydrate from
  `leases.db`; agents notice refused node heartbeats and re-register;
  a *pre-restart lease* remains renewable and committable (tested).
- **Invalid output**: commit-time hash validation fails the attempt.
- **Attempts exhausted**: TASK_EXHAUSTED event; job FAILED honestly.

### 3.3 Checkpointed training (Stage 7, as built)
Tasks are network-isolated, so **the agent is the checkpoint courier**:
- *Before* a run (payload has `checkpoint`): agent asks
  `GET /jobs/{j}/tasks/{t}/checkpoints/latest`, downloads the manifest's
  part, passes it as the `resume` input.
- *During* the run: a relay thread ships each new `ckpt/step-*.json`:
  upload → register part → commit single-part manifest. A final flush on
  death ships the dying attempt's last checkpoint.
- Trainer determinism contract: batches indexed by step ⇒ resume is
  **bit-identical** to the uninterrupted run (pinned by test — recovery
  must never silently change results). Deliberate-crash hook
  (`kill_at_step`) fires only on *fresh* starts so retries recover.
- Recovery economics: `GET .../lost-work?failed_at_step=S` → steps lost
  since the latest valid manifest (e.g. crash at 35, ckpt at 30 ⇒ 5).

### 3.4 Design invariants (violating any of these is a bug)
1. Job/task status is **derived from events/state tables** — never a
   hand-mutated field.
2. Commits are idempotent AND validated — exactly one accepted output per
   task, ever, and it hashes to what's on disk.
3. Manifest-last: a partially uploaded checkpoint cannot exist as a
   checkpoint.
4. All device connections are outbound; the coordinator never dials a node.
5. Both ends allowlist executable modules/images; unknown = refuse.
6. Agent secrets never enter task environments.
7. Recovery decisions are typed, versioned, logged — no discretion, no LLM.
8. The planner never imports ML frameworks; plans are frozen + hashed.

---

## 4. Architecture — cloud target (Stage 5+, planned)

**Principle: the coordinator's address changes; the code does not.** Every
cloud service replaces a local stand-in behind an existing seam:

| Capability | Local (today) | Cloud (target) | Seam |
|---|---|---|---|
| Artifact/checkpoint bytes | Coordinator-hosted dir | **Alibaba OSS** (native `OSSArtifactStore` exists; device uploads via short-lived **STS** creds minted by the cloud API) | `ArtifactStore` protocol + `FLASHML_ARTIFACT_BACKEND` |
| Images | none needed (subprocess tier) / local | **ACR**, immutable tags | `deploy/docker/`, `scripts/alibaba/acr-*.sh` |
| Coordinator hosting | your machine | 1 small **ECS** via compose first; **ACK** later | uvicorn + env config |
| Durable state | SQLite (`ledger.db`, `leases.db`) | **ApsaraDB RDS PostgreSQL**, same append-only schema | `Ledger` + `LeaseStore` seams |
| Logs | JSON stdout | **SLS** (config exists: `infra/alibaba/sls/`) | Logtail |
| Mode B pool | kind (POC, verified) | **ACK** node pool + KubeRay (overlay exists: `infra/alibaba/ack/`) | `ExecutionBackend` |
| Auth | join codes | cloud API auth (Supabase/Clerk planned) layered over join codes | flashml-cloud |
| Metrics | none yet (Stage 8) | ARMS managed Prometheus | after Stage 8 lands |

Deployment order is deliberately **ECS-first, not ACK-first** — one small
step at a time; the ACK pool arrives with cloud-Stage 6. Runbook lives in
`PLAN_2WEEKS.md` (Stages 5–6) + `flashml-cloud/infra/alibaba/`.

---

## 5. Implementation guide (how to build here)

**Process (non-negotiable):**
1. **TDD.** Write the failing test, watch it fail *for the right reason*,
   write minimal code, watch it pass, run the whole suite. Every hardening
   fix this project shipped was caught or pinned by a red test first.
2. **Vertical slices.** No empty scaffold modules; a package appears when
   its demo-able slice lands.
3. **Log your work** in `PROGRESS.md` per the protocol there (§ "How to
   log work").
4. **Docs move with code.** If you change a public surface, update the
   repo README/AGENTS in the same session; run `make check-docs` after
   touching `SYSTEM_OVERVIEW.md` (edit only flashruntime's copy).

**Testing pyramid (as it exists):**
- Unit: `flashruntime` (109) + `flashnode` (28) — pure, no infra, fast.
- `pytest -m integration` (flashruntime/tests/integration) — Docker/K8s/
  MinIO, auto-skip with instructions.
- `e2e/` at workspace root (own venv; `make e2e`) — real processes, real
  HTTP: kill-machine sweep, K-means convergence, cross-machine resume.
  `make e2e-demo` = watchable SIGKILL version.

**Recipes for common extensions:**
- *New task workload:* add the module to `flashml_workloads/` obeying the
  §2.2 contract → allowlist it in BOTH `service/modea.py`
  (`ALLOWED_TASK_MODULES`) and `flashnode/executor/runner.py`
  (`DEFAULT_ALLOWED_MODULES`) → add an expansion branch if it needs a new
  workload type → unit-test the math, e2e-test the loop.
- *New runner tier:* implement `run(payload, workdir, inputs) → outdir`;
  the loop and relay need zero changes (DockerRunner is the template).
- *New planner coverage:* add a `planner/catalog.py` entry (model/GPU/link
  = data, not code); new strategy family = ShardingConfig mapping +
  library stack + menu entry; pin the arithmetic in `tests/test_planner.py`.
- *New coordinator endpoint:* router module under `service/`, wired in
  `create_app`; every state change goes through the ledger event stream.

**Environment variables reference (coordinator):** `FLASHML_ENABLE_KUBERAY`,
`FLASHML_LEDGER_PATH` (leases.db lives beside it), `FLASHML_LOCAL_ARTIFACTS_DIR`,
`FLASHML_JOIN_CODE`, `FLASHML_MAX_ARTIFACT_MB`, `FLASHML_PROFILE`,
`FLASHML_ARTIFACT_*` (store selection). **(agent):**
`FLASHNODE_COORDINATOR_URL`, `FLASHNODE_JOIN_CODE`, `FLASHNODE_RUNNER`,
`FLASHNODE_ALLOWED_IMAGES`, `FLASHNODE_WORKDIR`, `FLASHNODE_STATE_DIR`.

---

## 6. Edge-case register (handled ✅ / open ⚠)

**Leases:** ✅ late/zombie commit after expiry (rejected w/ evidence);
✅ duplicate commit (first wins); ✅ heartbeat after expiry (410, worker
must stop); ✅ attempts exhausted; ✅ claim starvation behind dead workers
(claim sweeps first); ✅ coordinator restart mid-lease; ⚠ clock skew is
irrelevant by design (all deadlines are coordinator-side) — *keep it that
way*; ⚠ `_find_lease` is O(all tasks) — fine at current scale, index it
when task counts grow.

**Artifacts:** ✅ path traversal (`..` refused); ✅ size cap (413);
✅ commit-time hash validation; ⚠ no per-node upload auth beyond
registration — any network peer can PUT artifacts (acceptable local;
**must** be closed by STS-scoped uploads in Stage 5); ⚠ no GC of orphaned
attempt outputs.

**Checkpoints:** ✅ partial upload can't become a checkpoint; ✅ hash
mismatch at commit (409); ✅ quarantine falls back to previous valid;
✅ world-size compatibility gating; ✅ dying attempt's last file (relay
final flush); ⚠ **manifests are in-memory** — a coordinator restart
orphans durable checkpoint files (top of the missing list); ⚠ retention:
nothing prunes old checkpoints yet.

**Executor:** ✅ unlisted module/image (fail closed, both ends); ✅ lease
lost mid-run (result discarded; server would reject anyway); ✅ env
scrubbing; ✅ coordinator-restart re-register; ✅ colima $HOME-only mounts
(`FLASHNODE_WORKDIR`); ⚠ no disk-space guard on workdirs; ⚠ Docker tier
untested on Linux cgroups v1 vs v2 specifics; ⚠ subprocess tier trusts
`PYTHONPATH` — fine for trusted pools, not for community tier (Docker is
the community answer).

**Planner:** ✅ deterministic; ✅ nearest-miss on dead ends; ✅ honest
omission of un-estimable numbers; ⚠ all constants `basis: static` until
the profiling stage exists; ⚠ OOM-in-practice feedback loop (planner
defect logging) designed but not wired.

**Known conscious debts (not bugs):** node registry is in-memory
(re-register covers it); recovery `classify()/decide()` not yet called by
the service (lease expiry is the only live path); SSE not implemented
(2 s polling); `flash.run(plan)` missing (planner → JobSpec is manual).

---

## 7. Research register (investigate before building)

| # | Topic | Why it matters | Questions to answer | When |
|---|---|---|---|---|
| R1 | **Manifest persistence** | restart orphans checkpoints | reuse SqliteLeaseStore pattern vs fold into ledger events replay? migration to Postgres later? | before Stage 5 |
| R2 | **STS-scoped device uploads** (OSS) | closes the open-PUT hole | RAM role design; presigned-PUT vs STS token; expiry vs long tasks; fallback for local profile | Stage 5 |
| R3 | **Metric definitions from the ledger** | Stage 8 correctness | exact MTTD/MTTR event pairs (LEASE_EXPIRED↔TASK_COMMIT_ACCEPTED?); goodput denominator for Mode A; per-job vs per-pool windows | Stage 8 (next) |
| R4 | **Postgres migration** | cloud durability | SQLAlchemy vs raw; keep append-only event schema; one DB or ledger+leases split; RDS vs in-cluster | cloud Stage 6 |
| R5 | **HF Trainer + PEFT LoRA recipe** | first real-DL workload on the proven checkpoint contract | DCP vs Trainer-native checkpoints for single-node; checkpoint size vs relay cadence (multi-GB parts!); does the relay need multipart/resumable upload? | month 2 |
| R6 | **Long-poll vs WebSocket for claims** | claim latency + coordinator load at fleet scale | current 1 s poll per agent; at ~100s of agents move to long-poll (`claim?wait=30s`) or WS; NAT keepalive behavior | when fleet >20 |
| R7 | **Ray Train V2 / torchft watch** | Mode B/C roadmap bets | V2 API stabilization; torchft maturity for third parties | quarterly re-check |
| R8 | **gVisor/Kata tier** | community-pool isolation ladder | feasibility on target hosts; GPU passthrough constraints | pre-community launch |
| R9 | **Scheduler/placement** | today claim = FIFO; no capability matching | when payloads carry resource needs (vram_gb), claim must filter by node capability; scoring comes after ledger data exists | with GPU tasks |
| R10 | **Ledger growth** | SQLite ledger is append-only forever | retention/compaction policy; event volume at N agents × M heartbeats | before long-running deployments |

---

## 8. Definition of Done (any task, any agent)

1. Red test existed first and was watched failing for the right reason.
2. Full unit suites green (`flashruntime`, `flashnode`); e2e green if the
   loop was touched.
3. Docs updated in the same session (README/AGENTS current-state; overview
   synced if touched; `make check-docs` clean).
4. `PROGRESS.md` entry written per the logging protocol.
5. No new dependency in flashnode/core-flashruntime without justification
   against the dependency rules.

## 9. Document map

| Doc | Role |
|---|---|
| `HANDBOOK.md` (this) | Product + architecture bible; read once |
| `PROGRESS.md` | **Authoritative status** + work log + logging protocol |
| `ROADMAP.md` | Product roadmap to real users: personas, funnel, P0–P2 priorities, decisions needed |
| `archive/SPRINT_PLAN.md` | The original two-week sprint (complete; archived) |
| `archive/PLAN_2WEEKS.md` | Original staged plan (bannered complete-local); still the Alibaba runbook detail |
| `archive/FLASHRUNTIME_EVALUATION.md` | Deep architecture evaluation (planner scope, library stances, StrategyPlan) |
| `flashruntime/docs/SYSTEM_OVERVIEW.md` | Product overview **shared across repos** — edit only this copy, `make sync-docs` |
| `flashruntime/docs/planner/README.md` | Planner code walkthrough |
| `flashruntime/docs/adr/` | Decision records (ACK Edge, PAI-DLC, runtime-first/planner-second) |
| `e2e/README.md` | Cloud-free proof suite + real-second-machine runbook |
| per-repo `README.md` / `AGENTS.md` | Public face / agent context + current state + missing lists |
| `archive/` | Historical: POC plan/report (frozen record; the POC code itself remains the Mode B path) |
