# FlashML — progress log & work journal

**The authoritative status document.** New here? Read `HANDBOOK.md` first
(product + architecture), then `SPRINT_PLAN.md` (what to do next), then the
checklist and newest entries below (where things stand).

## How to log work (protocol for every agent/developer)

Log **one entry per completed slice** (a feature red→green, a hardening
fix, a deployment, a research note) — newest first, under `## Entries`.
Never batch a whole day into one vague paragraph; never log work that
isn't verified.

Entry template:

```
### YYYY-MM-DD — <imperative title> (<scope: repo(s)/stage/R#>)
What/why: 1–3 sentences — the change and the reason it was needed.
How verified: the actual evidence — test counts per suite, the demo
  command run and what it showed. No evidence ⇒ not done, don't log it.
Gotchas: anything a future agent must know (root causes, env quirks,
  decisions made and their why). Omit if none.
Next: the single most useful next action. Parking lot: ideas deliberately
  NOT done.
```

Rules:
1. **Evidence or it didn't happen.** "All tests pass" must carry numbers
   (`flashruntime 109, flashnode 28, e2e 3`).
2. **Update the stage checklist** below in the same edit when a stage's
   status changes ([ ] / [~] / [x] with a one-line justification).
3. **Docs move with code** — if you changed a public surface, say which
   README/AGENTS you updated in the entry.
4. **Root causes, not symptoms** — when you fix a bug, log what it *was*
   (e.g. "Thread._stop shadowed by an Event"), so nobody re-discovers it.
5. **Research notes are entries too** — an R# investigation logs its
   conclusion and links any ADR it produced.
6. Keep entries ≤ ~15 lines; depth belongs in ADRs/HANDBOOK, linked from
   the entry.

---

Historical note: this file began as the companion to `PLAN_2WEEKS.md`
(local rebuild, complete as of 2026-07-19). The original rule stands:
every slice ends with a runnable demo — cut scope, never the demo.

## Stage checklist

- [ ] **Stage 0** (Day 1) — one-file lease/heartbeat/commit sim
      (`rebuild/stage0_minimal.py`): worker dies, lease expires, task
      reclaimed, late commit rejected, event timeline printed.
- [x] **Stage 1** — coordinator + agents over HTTP ✅ (implemented directly
      in repos; kill-one-agent recovery proven in e2e/, 2026-07-19).
- [x] **Stage 2** — executor Tier 1 (subprocess) + **Tier 2 DockerRunner** ✅
      (image+module allowlists fail closed, --network none, cpu/mem limits,
      read-only rootfs, uid mapping; real-Docker e2e test green on colima;
      `flashnode work --runner docker` + FLASHNODE_ALLOWED_IMAGES/
      FLASHNODE_WORKDIR).
- [x] **Stage 3** — hyperparameter search ✅ + **leased sharded K-means** ✅
      (kmeans_shard map task, kmeans_driver reduce + job-per-iteration,
      e2e: 3 iterations × 4 shards across 2 agents converge to true
      centers in ~1.5 s).
- [~] **Stage 4** — repos + dashboard + **join codes** ✅ (FLASHML_JOIN_CODE
      gates registration; agent sends X-FlashML-Join-Code) + LAN runbook
      (`make local-coordinator` / e2e/README) ✅; **2 real physical
      machines** awaits the user running the runbook; cloud-API front held
      for Stage 5.
- [ ] **Stage 5** (Days 8–9) — Alibaba ECS-first: OSS artifacts (STS
      creds), ACR images, one ECS running coordinator+API+web, SLS logs;
      laptop joins over the public internet. ⚠ gated on Day-0 credentials.
- [~] **Stage 6** — local half ✅ (durable SQLite lease store; in-flight
      leases survive coordinator restarts; agents auto-re-register);
      RDS PostgreSQL + SSE + ACK hybrid pool remain for the cloud stage.
- [x] **Stage 7** — checkpoint recovery wired end-to-end ✅ (2026-07-19):
      checkpoint HTTP endpoints (task-scoped catalog, parts-first/
      manifest-last over the wire), pure-stdlib sgd_trainer with
      bit-identical resume, executor checkpoint relay (agent = courier;
      tasks stay network-isolated), e2e: machine B crashes at step 35 →
      machine A resumes from B's relayed step-30 checkpoint → identical
      final weights; lost-work endpoint reports 5 steps, not 35. LoRA/
      torch rides the same contract at the cloud stage.
- [ ] **Stage 8** (Day 14) — goodput/MTTD/MTTR/cost metrics page from the
      ledger; record the three demos; write CASE_STUDY.md.

## Entries

<!-- newest first -->

### 2026-07-21 — Bring-your-own-code: command workloads + flash.submit (flashruntime)
What/why: FlashRuntime can now *operate* a user's own training repo without a
rewrite — the framework-neutral path ADR-0003 promised. Shipped Tasks 1–11 of
the command-workloads plan: `workloads/command.py` (`CommandWorkload`/`argv()`/
`to_jobspec()`), first concrete launcher (`launchers/local.py`), strategy
compiler (`strategies/command.py`), and service recipe (`recipes/command.py`);
`flash.submit(workload) → Run` (synchronous local compile→launch→wait→collect);
thin `integrations/` adapters (sklearn sweep, pytorch DDP, HF Trainer callback,
no framework imports at module level) + the 7-fn in-script `flashruntime.torch`
helper (prepare/checkpoint/log_metrics/…, wraps torch's own DDP and stops);
fail-closed sandbox placement for service-side command tasks.
How verified: TDD throughout; flashruntime suite 176 passed, 4 integration
deselected (was 109 at the local milestone, 125 at this branch's start) —
incl. 4 real bring-your-code e2e tests in `tests/test_examples_e2e.py`
(sklearn sweep, 2-proc CPU-gloo DDP ×2 scripts, kill-at-60 → resume bit-exact).
`docs/test_documentation.py` green. Docs: new `docs/guides/bring-your-code.md`,
README pointer, flashruntime AGENTS "Current state" bullet.
Gotchas: `CommandWorkload.source` is a `Source` model, not a bare string
(pydantic rejects `source="…"`). Service-side command *execution* is
expansion+lease only — running the `argv` payload needs flashnode's argv
runner tier (cross-repo, versioned; the recipe defines the coordinator half).
Next: flashnode argv runner so command jobs actually execute remotely.
Parking lot (spec §10): `flash.run(StrategyPlan)` wiring, remote providers
(RunPod) + `git_revision` source packaging, multi-node DDP rendezvous, async
`flash.submit`. Spec: `flashruntime/docs/superpowers/specs/2026-07-21-command-workloads-design.md`.

### 2026-07-19 — Complete designed interfaces for all future-work parts (both repos)
What/why: by user request, every not-yet-built component now has its final
interface surface with full input/output contracts and explanatory notes —
flashruntime: StrategyCompiler+LaunchSpec (strategies/), Launcher/
LaunchHandle/LaunchState (launchers/), WorkloadRecipe (recipes/),
PlacementPolicy + concrete FifoPlacement matching today's exact claim
behavior (scheduler/), ManifestStore seam + in-memory ref with the R1
migration steps written in-module (checkpoint/store.py), Profiler/
ProfileResult/ProfileCache with the four isolation invariants
(profiling/), ResourceProvider/Offer/AcquiredCapacity (providers/), and
flash.run() raising NotImplementedError with the designed pipeline in its
docstring; flashnode: AdmissionProbe + run_admission (budgeted,
failure-isolated), TelemetryCollector/TelemetrySample (machine-only;
NodeHeartbeat protocol addition noted), and concrete HostPolicy +
load_host_policy (conservative defaults, fail-closed, owner-narrows-only).
AGENTS rule 7 amended (designed interfaces ≠ empty scaffolding); HANDOFF
gained §6b interface table; flashnode missing-list updated.
How verified: TDD — 15 contract tests written first and watched red
(ModuleNotFound), then green; suites: flashruntime 119, flashnode 33,
e2e 3, all green.
Next: implementers start from the contract tests' dummy examples; first
consumers per SPRINT_PLAN (metrics Day 1; ManifestStore Day 3; recipes
Days 8–10).

### 2026-07-19 — Handoff: cleanup, HANDOFF.md, all work committed (workspace-wide)
What/why: session-end handoff (model changeover). Wrote `HANDOFF.md` — the
builder's exit notes: ranked risks (credentials, open artifact PUT,
in-memory manifests, disk pressure, single-writer assumption), hard-won
gotchas (colima $HOME mounts, repo-dir shadowing, Thread._stop, stale
editables, conftest scoping, env-scrub vs PYTHONPATH), judgment calls to
revisit consciously, small-debt list, and per-sprint-item tips. Cleaned:
root .DS_Store/.pytest_cache, scheduler scaffold docstring → R9, *.db
gitignored. **Secured all work in git**: each product repo committed on
branch `local-milestone-2026-07` (main untouched, nothing pushed — no
remotes); workspace root `git init`'d (docs + e2e + Makefile committed,
product repos gitignored as independent repos).
How verified: all working trees clean (0 changes ×4); suites green —
flashruntime 109, flashnode 28, e2e 3.
Next: first session of the new agent → HANDOFF.md §1 (confirm branches
with the user, decide merge-to-main, then SPRINT_PLAN Day 1).

### 2026-07-19 — Documentation system: HANDBOOK + SPRINT_PLAN + logging protocol + archive (workspace-wide)
What/why: PM-level doc overhaul so any future agent can onboard from one
place. New `HANDBOOK.md` (product thesis, per-component breakdown, as-built
local architecture with flows/invariants, cloud service mapping,
implementation recipes, edge-case register handled/open, research register
R1–R10, Definition of Done, doc map). New `SPRINT_PLAN.md` (next two weeks
day-by-day with demos/DoD; credential dependency + swap rule). PROGRESS.md
gained the **logging protocol** (entry template + evidence rules) and is
now the declared authoritative status doc. Cleaned: POC_PLAN/POC_REPORT →
`archive/` (all references repointed), flashruntime `docs/prototype/` →
`docs/archive-prototype/` with an ARCHIVED banner, root AGENTS doc map
reordered (HANDBOOK first).
How verified: `make check-docs` in sync; flashruntime docs link test
passes; suites green — flashruntime 109, flashnode 28, e2e 3.
Next: SPRINT_PLAN Day 1 (metrics engine, R3 first).

### 2026-07-19 — Documentation standardized across all repos
PROGRESS.md promoted to the authoritative status doc (workspace AGENTS doc
map reordered; PLAN_2WEEKS.md got a completion banner and remains the
Stage-5 Alibaba runbook). flashnode README rewritten from "pre-release
scaffold" to the real device executor (two tiers, relay, security
contract), AGENTS refreshed with done/missing. flashruntime README layout
now shows leases (SQLite-durable), checkpoint surface, workloads, and an
explicit missing list; AGENTS "Implementation queue" replaced with "Status
vs. plan" (done + missing, priority-ordered). SYSTEM_OVERVIEW §10 updated
to "local loop is DONE" with both proof points, synced to all repos.
flashml-cloud AGENTS re-targeted at the current runtime surface for
Stage 5. Verified: check-docs in sync, docs link test, and all suites
green (flashruntime 109, flashnode 28, e2e 3).

### 2026-07-19 — Stage 7: checkpointed training recovery, cross-machine (TDD)
Four red→green features: (1) **checkpoint HTTP surface** wiring the
CheckpointCatalog per (job, task) — parts/commit/latest/lost-work; 409 on
partial commits crosses the wire intact. (2) **flashml_workloads.
sgd_trainer** — pure-stdlib logistic SGD; batches indexed by step ⇒ resume
from checkpoint is *bit-identical* to the uninterrupted run (pinned by
test); kill_at_step fires only on fresh starts so retries recover instead
of re-dying. (3) **executor checkpoint relay** — the agent (not the
network-isolated task) downloads the latest manifest before a run and
ships each new ckpt file during it (upload→register→commit), with a final
flush so a dying attempt's last checkpoint survives. Caught for real:
Thread._stop shadowing (Event broke join), and flashnode/.venv's stale
editable flashruntime (reinstalled). (4) **e2e cross-machine resume**:
machine B crashes at step 35 → requeue → machine A resumes from B's
step-30 checkpoint → SUCCEEDED, weights identical to never-crashed
baseline, lost-work = 5 steps. Totals: flashruntime 109, flashnode 28,
e2e 3. Remaining local: Stage 8 metrics page + case study.

### 2026-07-19 — Hardening pass + durable lease state (5 findings, all fixed via TDD)
Deep-review findings → red→green each: (1) **commit validation** — the
coordinator now verifies the uploaded artifact at the task's commit_key
against the reported sha256; bad/missing output fails the attempt (requeue),
never commits ("accepted work = validated output" enforced, not assumed).
(2) **artifact size cap** — PUT > FLASHML_MAX_ARTIFACT_MB (default 256) →
413. (3) **task env scrubbing** — SubprocessRunner passes only
PATH/HOME/PYTHONPATH/LANG/LC_ALL/TMPDIR; FLASHNODE_JOIN_CODE etc. never
reach workload code. (4) **agent survives coordinator restart** —
refused node heartbeat triggers re-register (ExecutorLoop.registration).
(5) **SqliteLeaseStore** — LeaseStore protocol gained save(); manager
persists every transition; leases.db beside the ledger. Strongest proof
(test): submit → claim → coordinator restarts → same worker re-registers,
heartbeats its *pre-restart lease*, uploads, commits — SUCCEEDED with
attempts=1. Stage 6's local half is done. Totals: flashruntime 100,
flashnode 26, e2e 2. Next: Stage 7 checkpoint recovery wiring, Stage 8
metrics page.

### 2026-07-19 — Stages 2/3/4 closed out via TDD loop
Every feature red→green with the failure watched first. **DockerRunner**
(flashnode/executor/docker_runner.py): allowlisted-image container
execution behind the same runner interface; caught for real: colima only
shares $HOME, so task workdirs need FLASHNODE_WORKDIR (ExecutorLoop
workdir_base). Expansion payloads now carry the job image. **Leased
K-means**: kmeans_shard (pure-stdlib partial sums), kmeans_driver
(reduce_partials with empty-cluster rule, job-per-iteration run_kmeans,
shard_and_upload), sharded_kmeans expansion branch; e2e proves convergence
across 2 agents; the executor's fail-closed allowlist caught the missing
module exactly as designed. **Join codes**: FLASHML_JOIN_CODE on the
coordinator ⇒ 403 without X-FlashML-Join-Code; client + CLI send
FLASHNODE_JOIN_CODE. LAN second-machine runbook: `make local-coordinator
JOIN_CODE=…` + e2e/README instructions. Totals: flashruntime 95,
flashnode 24, e2e 2 — all green.

### 2026-07-19 — Full local loop: coordinator + flashnode executor + e2e + dashboard
Stages 1–4 are effectively done, implemented directly in the repos (the
user redirected from scratch-rebuild to direct implementation).
**flashruntime service**: LeaseManager wired over HTTP (claim/heartbeat/
complete/fail), minimal node registry, **local artifact hosting** (PUT/GET
under a local dir — shared data without any cloud), hyperparameter_search
job→TaskSpec expansion (`execution.backend: leases`), derived job states +
2s sweeper, KubeRay now optional (FLASHML_ENABLE_KUBERAY=0), built-in
**dashboard at GET /** (nodes/jobs/tasks/events/artifacts, self-contained
HTML). **flashnode executor**: stdlib CoordinatorClient, SubprocessRunner
(module allowlist, Tier 1; Docker is Tier 2 next), ExecutorLoop with
attempt-heartbeat thread, `flashnode work` CLI. **e2e/ at workspace root**
(own venv, `make e2e-setup / e2e / e2e-demo`): pytest boots a real uvicorn
coordinator + 2 agents, plans via flash.plan, uploads shared dataset,
submits 12 trials, severs one machine's network mid-task → lease expiry →
requeue → 12/12 exactly-once with per-node credit (verified ~10s); demo
runner uses real `flashnode work` subprocesses + SIGKILL (verified:
trial recovered on attempt 2, credits 10+2). Tests: flashruntime 88,
flashnode 15, e2e 1. Gotchas fixed: conftest collection hooks see ALL
items (scope by path); repo dirs shadow installed packages when cwd is the
workspace root. Next: Docker runner (Tier 2), second physical machine,
then Stage 5 Alibaba ECS.

### 2026-07-19 — Clean core library + leases/checkpoint/recovery (flashruntime)
flashruntime is now a pure-Python library: core deps = pydantic only
(verified in a fresh venv — 6 packages total, planner+leases+checkpoint+
recovery all functional); numpy prototype lazy-gated behind [prototype];
Dockerfiles moved to deploy/docker/ (Makefile + ACR script updated);
integration tests isolated in tests/integration/ (auto-skip markers for
docker/kubernetes/minio; excluded from default pytest). Implemented three
new modules with wire models added to protocol/v1alpha1: **leases/**
(LeaseManager — claim/heartbeat/expiry/idempotent commit, late commits
rejected with evidence), **checkpoint/** (CheckpointCatalog — parts-first/
manifest-last, validation ladder, topology-compatible selection,
lost_work()), **recovery/** (FailureSignals classifier + versioned policy
table, total over FailureClass × mode). 81 unit tests passing (27 new).
Next: strategies/ compilers + service wiring of the LeaseManager (the
Stage 1–3 endpoints), then flashnode's device executor as its client.

### 2026-07-19 — Strategy planner v0.1.0 (flashruntime)
Built the `flash.plan()` API + `flashruntime plan` CLI: PlanRequest
(transformer fine-tune full/LoRA/QLoRA, generic PyTorch, classical ML,
independent tasks) → explained StrategyPlan with the library stack, knobs,
memory/comm/time/cost arithmetic, and every rejected candidate's reasons.
New: `protocol/plan_v1alpha1.py`, `planner/` (catalog, resolve, memory,
comm, timecost, candidates, selector, explain), examples, 18 tests
(54 total passing), walkthrough doc `docs/planner/README.md`. Framework-
import-free and deterministic per ADR-0003. Verified: Qwen-7B LoRA on
4×4090 selects ddp+lora via torchrun (~96 min, $2.82) with QLoRA/FSDP2/
offload correctly ranked or rejected; execution wiring is future work
(strategies/ compilers + rebuild Stages 0–3 lease runtime).

### 2026-07-19 — Stage prep (docs)
Plan rewritten as careful staged rebuild (PLAN_2WEEKS.md); FlashRuntime
architecture evaluated and decided (FLASHRUNTIME_EVALUATION.md → ADR-0003:
reliability runtime first, planner as explainable feasibility filter, four
axes, StrategyPlan). All repo docs updated to point implementation at the
stages: flashruntime README/AGENTS/SYSTEM_OVERVIEW (synced), flashnode
device-profile executor spec, flashml-cloud stage map. Next action: create
`rebuild/stage0_minimal.py`.
