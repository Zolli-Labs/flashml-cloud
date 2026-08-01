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

## M1 checklist — deployed multi-user POC (2026-07-31 →)

Supersedes the Alibaba-gated cloud half of the stage list above: the POC ships
on **Supabase + Render**; Alibaba is deferred, not abandoned. Spec:
`flashml-cloud/docs/superpowers/specs/2026-07-31-deployed-multi-user-poc-design.md`.
Decisions and their revisit-triggers: `M1_DECISIONS.md`.

- [x] **Plan 1 — federated averaging** ✅ (2026-07-31) one model trained across
      independent machines by exchanging weight deltas per round; proven
      against a real coordinator AND two genuine flashnode agents, with a
      falsification check showing the loss decrease is caused by aggregation.
- [x] **Plan 2 — agent identity + lease-scoped writes** ✅ (2026-08-01)
      per-machine bearer tokens, writes confined to live leases, lease
      lifecycle endpoints authenticated, operator credential for drivers,
      fail-closed startup guard. **`HANDOFF.md` risk #2 is closed**, proven
      against a real coordinator process over real HTTP (e2e 15 passed).
      Self-service enrolment (device flow) still belongs to Plan 3.
- [~] **Plan 3 — cloud API + Supabase** — Auth (Google), Postgres schema, RLS.
      Supabase project `flashml-poc` (`yualksqjjvlfscbbsygq`) created
      2026-08-01, $0/month (D13).
- [ ] **Plan 4 — GitHub repo → job + preflight** — `flashml.yaml`, curated
      images, static import/network checks at submit time.
- [ ] **Plan 5 — web app** — rebuilt against the real API (the current
      `apps/web/lib/api.ts` still targets a legacy coordinator).
- [ ] **Plan 6 — Windows hosts** — `hardening.py:60`'s `os.getuid()` is
      platform-conditional; curated images must declare a non-root `USER`
      first, or dropping the flag silently means container root.
- [ ] **Plan 7 — deploy + acceptance** — Render services, curated images to
      GHCR, and the §10 run-through, whose real test is a friend completing
      signup → enroll → contribute unaided.

## Entries

<!-- newest first -->

### 2026-08-01 — Per-machine identity + lease-scoped writes; risk #2 closed (flashruntime + flashnode + e2e; M1 Plan 2 of 7)
What/why: `PUT /v1alpha1/artifacts/{key}` accepted **any key from any caller**
with no authentication — only path containment and a size cap — so one
volunteer could overwrite another job's committed result, and the
federated-averaging model sits at a predictable key in that same writable
namespace. The sha256 commit check was no defense: the attacker supplies both
file and hash. This is `HANDOFF.md` risk #2, the reason the coordinator could
never go on a public IP. Now: a pluggable `NodeAuthenticator` resolves a bearer
token to a node, and every write is confined to `jobs/{job}/{task}/` for tasks
that node holds a **live** lease on. `flashnode login` stores a per-coordinator
token at 0600 and `CoordinatorClient` sends it on every request.
How verified: flashruntime **475 passed**, flashnode **85 passed, 1 skipped**,
workspace e2e **15 passed**. The 8 write-scope assertions run against a **real
uvicorn coordinator subprocess over real HTTP**, not FastAPI's in-process
`TestClient` — that distinction matters and an earlier claim that the hole was
closed was made on `TestClient` evidence alone and was wrong. Results: no token
401; forged token 401; **node-b writing under node-a's held task 403**; node-a
under its own task 200; **write after lease expiry 403** (2 s lease, checked
against the deadline directly so it does not depend on the 2 s sweeper);
round-weights key 403 for a node token and 200 for an operator token; **node-b
failing or completing node-a's lease 403/403** with the victim's lease left
`LEASED`; and a `claim` whose body says `node_id: node-a` under token `tok-b` is
served as **node-b**. The fedavg demo converges `0.5361 → 0.1757` and exits 0
**both with and without enforcement**.
Gotchas: (a) **scoping the writes alone did not close the hole.** The lease
lifecycle endpoints were unauthenticated — `claim` took `node_id` from the
request *body*, and `complete`/`fail`/`heartbeat` checked nothing — so an
attacker never needed to defeat write scoping: fail another node's attempt
until the task requeues to you, then write legitimately. This violated the
spec's own §5.2 rule ("resolve node_id from the token, never the body"), which
the plan's self-review had ticked off as satisfied because it was applied to
the write path only. (b) **TOCTOU:** authorization ran before
`await request.body()`, so a slow chunked upload held an *attacker-controlled*
window open past its own lease expiry, past requeue, past another node's
commit — defeating revocation entirely. Fixed by re-authorizing after the body
is read. (c) A **non-ASCII bearer token** made `hmac.compare_digest` raise
`TypeError` → an unauthenticated remote 500 and a 500-vs-401 oracle.
(d) **Enforcement would have made the drivers unrunnable:** `fedavg_driver` and
`kmeans_driver` both PUT artifacts while holding no lease. A driver is a
legitimate writer running in the trusted cloud API, so it gets
`FLASHML_OPERATOR_TOKENS` — a credential class, not an exemption. The demo is
now run under enforcement in CI-able form precisely to pin this.
(e) FastAPI validates request bodies **before** the handler, so a malformed
body yields 422 and never reaches the authorization check — a test asserting
401/403 with an invalid body proves nothing. This bit both a task brief and one
of my own manual probes.
Next: M1 Plan 3 — Supabase auth + schema + the cloud API, targeting the newly
created project `flashml-poc` (`yualksqjjvlfscbbsygq`; see `M1_DECISIONS.md`
D13 — do **not** migrate the org's other projects, they are a different
product). Parking lot, all still true and deliberately unbuilt: tokens are
configured statically on the coordinator (self-service enrolment is Plan 3);
**result verification is unbuilt — a node can still lie about its own results
and be believed** (M3); `POST /v1alpha1/jobs` is unauthenticated (Plan 3);
reads are unscoped by design.

### 2026-07-31 — Federated averaging across volunteer machines (flashruntime + flashnode + e2e; M1 Plan 1 of 7)
What/why: one model can now be trained across several independent machines by
exchanging weight *deltas* once per round instead of gradients once per step —
the only shape that works over home internet, and the only one possible at all
on volunteer nodes, where `--network none` means ranks can never rendezvous.
`fedavg_driver` is `kmeans_driver`'s round loop with `reduce` swapped for a
sample-weighted delta mean; `fedavg_worker` is the per-shard task; a new
`federated_averaging` workload type expands one job per round. This is the
prerequisite for the deployed multi-user POC (`M1_DECISIONS.md` D6).
How verified: flashruntime **417 passed**, flashnode **75 passed, 1 skipped,
4 deselected**, workspace e2e **7 passed**. Convergence against a REAL
coordinator over real HTTP (real expansion, leases, artifact storage,
commit-time sha256): loss `0.5361 → 0.3781 → 0.2548 → 0.1757` over 4 rounds,
2/2 participants. **Falsification check** — with `apply_delta` monkeypatched to
return the base weights unchanged, the same run gives
`[0.5361, 0.5361, 0.5361, 0.5361]`, exactly flat, so the decrease is *caused*
by aggregating and broadcasting deltas rather than by workers retraining
locally each round. With two genuine `flashnode` `ExecutorLoop` agents:
`[0.5361, 0.3781, 0.2548]`, and the closed-laptop case
`[0.5361, 0.3734, 0.2458]` (round 0 with 2 participants, rounds 1–2 solo).
`scripts/fedavg_local_demo.py` exits non-zero if the final loss is not below
the first.
Gotchas: (a) **the cross-repo allowlist seam.** `fedavg_worker` was added to the
coordinator's `ALLOWED_TASK_MODULES` (`service/modea.py`) but NOT to flashnode's
own `DEFAULT_ALLOWED_MODULES` (`executor/runner.py:26`). Each repo's list looked
correct in isolation, both suites were green, and the flashruntime-side
convergence test passed — because it drives a hand-rolled urllib agent that does
not enforce flashnode's allowlist. A *real* agent refused every task
("module … is not allowlisted"), burned all 4 attempts per shard, and the job
FAILED. Federated averaging was completely non-functional on genuine agents
while everything reported green. This is precisely the failure the 2026-07-29
entry predicted ("a cross-repo chain test would pin the task-seam integration
that per-repo unit tests structurally cannot") and the same shape as the
`argv_capable`/`module_capable` polarity bug — a seam *between* components where
each side is individually defensible. Guarded now in two places:
`flashnode/tests/test_allowlist_drift.py` (manually mirrored, because
`flashnode/AGENTS.md` scopes its flashruntime dependency to `protocol` only) and
`e2e/test_allowlist_parity.py` (self-maintaining — e2e may import both repos).
(b) The **quorum rule is deliberately the opposite of `kmeans_driver`'s**, which
requires every shard: a volunteer pool must aggregate on `min_participants` or
one closed laptop stalls everyone. Late deltas are discarded, never applied to a
later round — they were computed against weights that no longer exist. Do NOT
"harmonize" these; `M1_DECISIONS.md` D7 records why, including that
`reduce_deltas` divides by *reporting* samples so a lone participant moves the
weights fully rather than 1/N of the way. (c) Task ids are zero-padded to 3
digits and the driver sorts them, so `num_shards` now fails closed above 999 —
`"shard-1000"` would sort before `"shard-999"` and float summation is not
associative. `_expand_kmeans` has the same latent bug via `it{iteration:02d}`
past 99 iterations — **not fixed, recorded here**. (d) The documented test
baseline only reproduces with the venv on `PATH`
(`PATH="$PWD/.venv/bin:$PATH" .venv/bin/pytest`); otherwise `LocalLauncher`
spawns a bare `python` that does not resolve and an unrelated test fails
spuriously.
(e) **The whole-branch review found three Criticals that per-task reviews
structurally could not**, because each saw only its own diff. Two were
demonstrated, not theorised: `reduce_deltas` validated only the *total* sample
count, so a node reporting `samples = -999` beside an honest peer produced a
weight of **999001.0** where the correct step was 1.0 (~10⁶× amplification from
one integer); and Python's `json` parses `NaN`/`Infinity`, so a single
non-finite delta NaN'd the model permanently while the run reported success —
which needs no attacker, just a diverging lr on one shard. A third: quorum
counted artifact keys by filename *suffix*, so one worker writing
`out/a/metrics.json` + `out/b/metrics.json` minted multiple participants from
one lease, and attempts the coordinator had **rejected** were aggregated anyway
(uploads precede commit acceptance). All fixed; validation now lives in
`fedavg_weights` on both the write AND read paths — the first fix covered only
the write side, and a scoped re-review caught that `resume_state` and
`blob_to_state` still admitted NaN. Honest arithmetic is unchanged: the demo
reports byte-identical numbers before and after. See `M1_DECISIONS.md` D11/D12.
Next: M1 Plan 2 — per-machine agent identity (device-flow tokens replacing the
shared join code) and lease-scoped artifact writes, which together close
`HANDOFF.md` risk #2. **Nothing may face the public internet before it lands:**
artifact `PUT` is still unauthenticated and the authoritative global model sits
at a predictable key in a volunteer-writable namespace — both deliberately left
to Plan 2 rather than half-fixed here. Parking lot: this proves *collaborative*
training, not *faster* — over home links with a small model it will usually be
slower than one machine, and the docs say so; capability-proportional shard
sizing and admission probes are M2; abandoned shards are never cancelled, so a
quorum round leaves a zombie task per round (needs cooperative cancel, M3).

### 2026-07-29 — Final-review fix wave + deferred follow-ups for the volunteer pool (flashruntime + flashnode)
What/why: the whole-branch review of the volunteer argv slice found 12 issues,
two High, both invisible to task-scoped review because they lived in the *seams
between* tasks. **F1:** making `argv_capable` imply `sandbox_capable` (itself the
fix for an earlier silent-idle-node bug) widened placement the other way — an
argv-only node became eligible for **module** tasks it cannot run, so
`ArgvDockerRunner` rejected the payload, the attempt failed, the loop re-claimed
within `poll_seconds`, and all four `maxTaskAttempts` burned in seconds, failing
somebody else's job. One volunteer joining a coordinator also running
`hyperparameter_search` would kill that sweep. Fixed with a `module_capable`
field + gate of the **opposite polarity** to the argv gate: argv fails *closed*
(safety), module fails *open* (availability) — every already-deployed agent
registers without the field, so a fail-closed default would silently cut the
whole fleet off from module work. The field comment and `PlacementPolicy`
docstring now warn against "harmonizing" them. **F2:** `donate-a-machine.md`
promised the coordinator filters tasks by the volunteer's image allowlist; no
code does that, so the claim was deleted and replaced with the truth (the agent
enforces it locally, so a non-allowlisted image is claimed and then fails on the
node). Also fixed: the timeout container-kill existed only in `ArgvDockerRunner`
while `DockerRunner` still leaked a live container for up to 900 s (name builder
now shared in `hardening.py`); a missing `docker` binary raised an uncaught
`FileNotFoundError` that killed the agent (startup refusal + `OSError` wrapped);
`ExecutorLoop` uploaded with `iterdir()` while the runner capped with `rglob()`,
so `out/<subdir>/*` was silently dropped yet still committed (now recursive,
with only a root-level `metrics.json` able to set the commit hash); bumped to
0.2.0 with the flashnode pin tightened to `>=0.2`, since the released 0.1.0
wheel lacks `argv_capable` and pydantic drops unknown kwargs silently — a node
on it would idle forever.
How verified: flashruntime **323 passed, 1 skipped, 20 deselected**; flashnode
**73 passed, 1 skipped, 4 deselected**; `scripts/build_docs.py --check` OK;
`scripts/audit_secrets.sh` CLEAN. Scoped re-review verdicted all 9 fixes
ADDRESSED, no new breakage. Polarity checked directly: a node omitting
`module_capable` stays eligible for module tasks (back-compat), `--runner argv`
does not, and the argv gate stays fail-closed.
Gotchas: (a) a controller-written chain check passed while F1 was live because
it only ever fed the node a *command* task — verification authored by whoever
ordered the change inherits that person's blind spot; (b) four plan briefs had
defects in their test scaffolding, worst being integration tests using pytest
`tmp_path`, which on colima bind-mounts as an EMPTY directory, so the two tests
proving `--network none` and `--read-only` would have gone green proving nothing.
Next: slice B — per-node Ed25519 identity replacing the shared join code.
**Deferred follow-ups from the review (recorded here so they survive):**
1. **Artifact/checkpoint `PUT` is unauthenticated and not lease-scoped**
   (`service/modea.py` `PUT /artifacts/{key}`, `service/checkpoints.py`) — any
   registered volunteer can overwrite *another job's* commit artifact or
   checkpoint manifest, and the sha256 check is no defense because the attacker
   supplies both file and hash. Scope PUT keys to the caller's live lease
   prefix. **Do this with slice B.**
2. `/leases/claim` returns 204 for both "queue empty" and "permanently
   ineligible", and the agent logs neither — both silent-idle bugs in this slice
   presented exactly that way. An event on "pending tasks exist, none eligible
   for this node" would have caught them on first run.
3. `/work` is an unquotaed host bind mount and `max_output_bytes` is checked
   only *after* the run, so a job can fill a volunteer's disk before the cap is
   consulted. A sampling check during the run would make the cap real.
4. No cooperative cancel: `execute_one` blocks in `runner.run()`, so a lost
   lease is noticed only after the container exits — up to 3600 s of a
   volunteer's CPU burned for a result that is discarded.
5. `harden_args` uses `os.getuid()`, so an agent run as root yields
   `--user 0:0` — container root. Refuse or warn at startup.
6. `CommandRecipe.validate_output` is never called from production code despite
   `recipes/__init__.py` documenting it as commit-time validation — nothing
   checks `metrics.json` is even valid JSON. Pre-existing dead contract.
7. **The sandbox is not kernel-verified.** The four real-daemon integration
   tests are committed but SKIP (no Docker in this environment). Run
   `pytest tests/integration/ -m integration -v` in flashnode once Docker is up;
   until then the hardening is proven only by constructed-argv assertions.
8. A cross-repo chain test (workspace `e2e/`) would pin the task-seam
   integration that per-repo unit tests structurally cannot; a working script
   from this run is worth promoting.

### 2026-07-29 — Volunteer compute pool: argv runner tier + composite lease key (flashruntime + flashnode — slices A/E of the multi-machine decomposition)
What/why: any machine can now join the pool as an untrusted volunteer via
`flashnode work --runner argv --coordinator <URL>` and execute a submitting
user's arbitrary command — previously a joined node could only run
`python -m <module>` from a fixed allowlist. `ArgvDockerRunner` +
`harden_args()` run the task's pinned image + argv with `--network none`,
`--read-only`, a `noexec,nosuid` `/tmp` tmpfs, non-root `--user`,
`--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--pids-limit`,
`--cpus`/`--memory` (`--memory-swap` pinned equal — otherwise the cap is
swap-bypassable), and `--ulimit nofile`; only the bound workdir (`/work`) is
writable, and a wall-clock timeout `docker kill`s the named container
directly (not just the local docker client). The coordinator gates this with
a new fail-closed `argv_capable` field and requires it alongside
`sandbox_capable` before leasing a command task; `CommandRecipe` refuses
`isolation.tier != "sandboxed"` for command jobs and rejects the
`allowFallback` waiver, so a submitter can never downgrade their own
isolation (only a coordinator operator can, via
`FLASHML_ALLOW_UNSANDBOXED_ARGV=1`, for a trusted fleet). Alongside this,
the lease key became composite `(job_id, task_id)` (both lease stores, with
an in-place SQLite migration from the old single-column PK) since a
volunteer pool is multi-job by definition and `CommandRecipe.expand()` names
tasks positionally (`task-000` in every job).
How verified: flashruntime **317 passed, 1 skipped, 20 deselected**;
flashnode **60 passed, 1 skipped**; `scripts/build_docs.py --check` OK
(new `docs/guides/donate-a-machine.md` + updated
`docs/guides/bring-your-code.md`/`docs/site/guides/jobspec-and-isolation.md`);
`scripts/audit_secrets.sh` CLEAN. Real-docker integration tests
(`tests/integration/test_argv_runner_docker.py` — network-off, read-only
rootfs, inputs-staging) exist and are written to prove the flags are
enforced by a live daemon, not just constructed correctly, but they
**auto-skip** in this environment (no Docker daemon available) — so what's
actually verified here is that `ArgvDockerRunner` builds the correct argv
for every case (missing/empty argv, non-allowlisted image, bad env key, the
`--memory`/`--memory-swap` pairing) and refuses before ever invoking
`subprocess`, not that dockerd enforces them. No volunteer-kill run was
observed either, for the same reason; the existing lease-expiry recovery
path is unchanged and was already proven (kill-a-machine sweep, earlier
entries) — it needs no new proof for the argv path specifically, just a
real-Docker re-run once a daemon is available.
Gotchas: (a) the composite `(job_id, task_id)` lease key fixed a **second,
silent** bug in `claim()`'s policy path — matching a chosen `TaskSpec` back
to a pending record by `task_id` alone picked the WRONG record whenever two
jobs both had a pending `task-000` (now matched on both fields); (b)
`argv_capable` belongs on `NodeRegistration`, not `NodeCapabilities` —
`NodeCapabilities` carries only hardware facts (cpu/memory/gpus/os/arch),
while argv-capability is a *runner posture* the agent chooses at startup,
same shape as the existing `sandbox_capable`; (c) a node needs **both**
`argv_capable` and `sandbox_capable` to be leased a command task — a node
running the module `DockerRunner` is genuinely sandbox-capable but cannot
execute argv, so overloading `sandbox_capable` would let the coordinator
place a command task on a node certain to fail it. This is why `--runner
argv` sets `argv_capable=True` AND implies `sandbox_capable=True` in
`discover()`, while `--runner docker` sets only `sandbox_capable` on its own.
Next: slice B — per-node Ed25519 identity, replacing the one shared
`FLASHNODE_JOIN_CODE` for all volunteers (no revocation today). Parking lot:
slice C (result verification — a lying volunteer node is currently
believed), slice D (GPU capability probing), and running the real-Docker
integration suite + a live volunteer-kill re-run once a Docker daemon is
available in the dev environment.

### 2026-07-24 — Final-review fix wave: republish benchmarks from fresh N=20 baseline (flashruntime — resilience-showcase correction)
What/why: the signing audit found the resilience showcase's headline "crash-storm
**16/16** auto-resumed" (entry below) OVERSTATED — only HALF the 16 trials are
crash-armed; the storm completes 16/16 but exactly **8** crashed trials auto-resume
(the other 8 run clean). **Correction:** the honest line is "16-trial storm, half
crash-armed: 16/16 completed, all 8 crashed trials auto-resumed, 0 manual". Also:
README perf numbers traced to a since-deleted `--repeats 5` baseline; relabelled
fault-matrix case (c) `hang+external SIGKILL → mid-run external SIGKILL` (the kill
fires ~step 2, before the hang); fixed the fault-matrix `tr_note` to report the MEAN
across repeats; hardened S6 with `FLASHML_JOIN_CODE=""`. Republished every number from
ONE fresh `--all --repeats 20` run (baseline copied from the emitted output, never
hand-edited).
How verified: flashruntime full suite **297 passed, 1 skipped, 20 deselected**;
`scripts/build_docs.py --check` OK (grouped tables render the new JSON);
`scripts/audit_secrets.sh` CLEAN. New measured headlines: fault matrix **5/5** handled
(0 manual), checkpoint integrity **20/20** survived `kill -9` (naive `torch.save`
**20/20** corrupted, EOFError×20), crash-storm **16/16** completed / **8** crashed
trials auto-resumed (goodput lower-bound 0.8, 0 manual), launch overhead **+0.04 s**,
checkpoint cost **−1.2 ms** (p90 6.2 ms, noise floor), dead-worker MTTD **3.05 s** /
MTTR **~3.5 ms**. flashruntime commit `d00c023` (branch `local-milestone-2026-07`, not pushed).
Gotcha: the first `--repeats 20` run died on `recovery_economics` with `os.killpg →
PermissionError (Operation not permitted)` in torchrun's elastic-agent SIGTERM teardown —
the sandbox blocking process-group signalling during the mid-run kill/resume flow; the one
allowed re-run with the sandbox disabled completed clean (all legs resumed from step 40).
Next: the Stage-8 ledger-metrics debt still stands (fold MTTD/MTTR/goodput into
ledger-derived aggregates). Parking lot: report.py's "reproduce with --repeats 5" hint is
now off by the N used for the committed baseline — cosmetic, left for a later doc pass.

### 2026-07-24 — Resilience showcase: 6 fault-injection benchmarks + grouped baseline (flashruntime — resilience-showcase S1–S6/T6)
What/why: the benchmark suite measured performance overheads but never the
fault-tolerance guarantees themselves. Added six resilience scenarios that
COUNT/time from real failure injection (never assert): S1 `fault_recovery_matrix`
(5 fault types → typed recovery), S2 `checkpoint_integrity` (`kill -9` in the
write window vs naive `torch.save`), S3 `crash_storm` (goodput under a storm of
crash-armed trials), S4 `submit_latency` + S5 `fanout_throughput` (perf), S6
`lease_recovery_latency` (real coordinator over sockets → MTTD/MTTR). Registry
now 11 scenarios; rows carry a `section` field; `report.render_document` groups
them into a "Performance" + "Resilience" table (T6). Refreshed the full measured
baseline (`baseline-Phongs-MacBook-Air-1731.json`, Apple M4, `--all --repeats 5`,
all 11 scenarios, ~4 min wall) + benchmarks.md Resilience intro + README teaser.
How verified: flashruntime full suite **297 passed, 1 skipped (CUDA-gated), 20
deselected** (was 264 at the flow-map entry; +33 across S1–S6 unit/pure-fn tests
and T6's +3 grouped-render tests; the long chaos loops sit behind `bench_stress`).
`scripts/build_docs.py --check` OK (grouped tables render from the committed
JSON); `scripts/audit_secrets.sh` CLEAN. Measured headlines: fault matrix **5/5**
handled (0 manual interventions), checkpoint integrity **5/5** survived under
`kill -9` (naive `torch.save` **5/5** corrupted, EOFError×5), crash-storm
**16/16** auto-resumed (goodput lower-bound 0.8, 0 manual), dead-worker MTTD
**3.0 s** / MTTR **~3 ms**. flashruntime commit `6e20a41` (branch
`local-milestone-2026-07`, not pushed).
Gotchas: **PRODUCT BUG found by S6 (pre-existing, routed to follow-ups):** the
lease stores key tasks by `task_id` ALONE (`InMemoryLeaseStore.add` and the
`SqliteLeaseStore` PRIMARY KEY) — two jobs each expanding to `trial-000` collide
with a 500 (`task trial-000 already exists`). Never hit before because existing
tests submit one job per coordinator. S6 worked around it (one job of N+1 tasks);
real fix = composite **`(job_id, task_id)`** keying in both stores. All other
review findings are deferred Minors (cosmetic/coverage), catalogued in
`flashruntime/.superpowers/sdd/progress.md` (per-task roll-up, lines ~84–93).
Next: fix the `(job_id, task_id)` lease-store keying so independent jobs coexist
on one coordinator. Parking lot: these MTTD/MTTR/goodput numbers are the first
measured slice of the Stage-8 ledger-metrics debt — fold them into ledger-derived
aggregates rather than re-measuring ad hoc.

### 2026-07-23 — Run-monitor flow map + KPI dashboard shipped in the viewer (flashruntime)
What/why: the live run viewer showed topology/loss/checkpoints/recovery but
no resource or process-level view. Spec'd and built layout A: a KPI strip
(state/elapsed/step/steps-per-sec/step latency/cpu/memory/gpu/restarts/
verified checkpoints) plus a machine → worker → rank process flow map with
a click-in detail panel, backed by real telemetry instead of just heartbeat
presence. Spec:
`flashruntime/docs/superpowers/specs/2026-07-23-run-monitor-flowmap-design.md`;
plan: `flashruntime/docs/superpowers/plans/2026-07-23-run-monitor-flowmap.md`.
New `flashruntime/monitor/` package (`ResourceSampler`): optional-psutil
machine + process-tree telemetry, degrading to stdlib-only sampling when
psutil isn't installed (new `[monitor]` extra, `psutil>=5.9`). `flash.submit`
starts a sampler beside every launched attempt. `flashruntime.torch` gained
per-rank heartbeat files (throttled, best-effort) so the viewer can place
ranks under workers under machines without guessing. `viewer/state.py`
enriches snapshots with telemetry tails + rank heartbeats + monitor samples;
the new shared `viewer/flowmap.py` component (CSS/JS strings, zero-CDN)
renders the flow map and KPI tiles, reused by the layout-A run page.
How verified: full suite **264 passed, 1 skipped (CUDA-gated), 9 deselected**
(up from 237 at the 0.1.0 deploy-ready milestone). Component escaping
policy re-checked after the KPI-tile-color bug (color values were going
through unescaped into the tile HTML — fixed and covered).
Gotchas: monitor sampling must stay best-effort and non-blocking — a stalled
psutil call must never stall the training loop it's sampling beside;
`ResourceSampler` runs off the hot path. Rank heartbeat files are throttled
writes, not a synchronization primitive — the viewer treats a missing/stale
heartbeat as "unknown", never as a hard failure signal.
Next: wire the flow map's resource charts into the Stage-8 ledger metrics
work so goodput/MTTD/MTTR reuse the same telemetry tails.

### 2026-07-23 — Deploy-ready milestone: 0.1.0 assembled + shipped on-branch (flashruntime — deploy-ready T1–T13)
What/why: closed the deploy-ready plan that turns the local runtime into a
publishable 0.1.0. Shipped across T1–T13: bring-your-own-code SDK
(`flash.submit(CommandWorkload)`) + `flashruntime submit`/`submit-spec` CLI;
sklearn/pytorch/HF adapters + optional `flashruntime.torch`; automatic
`max_restarts` recovery (classify/decide, one-call kill-and-resume); live run
viewer on `--watch`; PyTorch-style docs site (built/served at `/docs`/Pages-
ready); honest benchmark suite + measured M4 baseline; CI matrix + test-gated
PyPI trusted publishing + secrets audit; real-GPU validation; prototype engine
removed. T13 itself: README rewritten around the real story (install → 60-sec
run → one-call fault tolerance → viewer → docs → honest benchmark teaser → GPU
line) + AGENTS "Current state"/"Status vs. plan" refresh.
How verified: full suite **237 passed, 1 skipped (CUDA-gated), 9 deselected**
(was 183 at the command-workloads close, 109 at the local milestone).
`scripts/build_docs.py --check` OK; `scripts/audit_secrets.sh` CLEAN (worktree
+ history). Real-GPU e2e: 2×RTX 4090 (RunPod), nccl DDP + GPU kill-and-resume,
**$0.0725** total (detail in the 2026-07-23 Task-12 entry below). Docs moved
with code: `flashruntime/README.md` front sections + `flashruntime/AGENTS.md`
updated in this same slice.
Gotchas: AGENTS "scheduler is the only scaffold package" was stale — scheduler
now holds real `PlacementPolicy`/`FifoPlacement`/`IsolationAwarePlacement`;
corrected. Recovery is wired in the SDK (`flash.submit`) path only, not the
coordinator — kept honest in the missing-list. `flashruntime.torch` is a
package (`torch/`), not `torch.py`.
Next: multi-node DDP rendezvous (`nnodes>1` raises today); flashnode argv
runner so service-side command jobs execute remotely. Parking lot — launch-day
HUMAN steps (not code): create the PyPI project for Trusted Publishing, enable
GitHub Pages, set `main` branch protection; then tag `v0.1.0` to fire the
release pipeline.

### 2026-07-23 — RunPod real-GPU validation: nccl DDP + GPU resume proven (flashruntime — deploy-ready Task 12)
What/why: the CUDA paths (nccl DDP, per-rank device placement, GPU
kill-and-resume) were implemented but only unit-tested on CPU via a pure
device-selection helper. Rented one 2-GPU RunPod community box and ran them
for real, cloud-free, with a hard money cap.
How verified: `scripts/runpod_gpu_e2e.py` (dry-run `--plan-only` first, then
the real run) on **2×NVIDIA GeForce RTX 4090**, torch 2.7.1+cu128, CUDA 12.8.
`tests/test_gpu_e2e.py` → 2 passed: 2-proc nccl completes (metrics
`backend=nccl`, `device=cuda:0`); crash@step-40 resumes from the step-40
checkpoint onto the uninterrupted loss (`resumed_from==40`). GPU benchmarks
loop_overhead + recovery_economics both green (recovery: 40 steps not
recomputed). Pod terminated (HTTP 204); post-run list = 0 labeled pods. Two
runs, **$0.0725 total** (~3.5 min each, well under the $1 cap). flashruntime
locally **236 passed, 1 skipped** (the GPU test, CUDA-gated), 9 deselected.
Gotchas: the real-GPU run FOUND a genuine bug the CPU/gloo suite structurally
cannot — `ft.prepare` placed the model on cuda but the training loop left
batches on cpu → "two devices, cuda:0 and cpu" in cross_entropy (hit at the
first matmul). Fix: the example moves each batch to `ft.device()` (a no-op on
CPU, so the CPU e2e stays bit-identical). Also added `ft.device()`/`ft.backend()`
accessors + device/backend keys in the example metrics so the GPU test can
assert them. Harness gotcha: `| tee` masked the remote exit code (first run
falsely reported SUCCESS over 2 failed tests) — fixed with `set -o pipefail`.
Next: Stage-8 ledger metrics (MTTD/MTTR/goodput/lost-work). Parking lot:
RunPod 3090 community stock was too low to allocate (fell through to 4090);
multi-node GPU rendezvous stays a later slice.

### 2026-07-21 — Final-review fix wave: command-workloads hardening (flashruntime)
What/why: whole-branch review fix list applied on `local-milestone-2026-07`.
Security fix F1: the legacy `hyperparameter_search`/`sharded_kmeans` expansions
in `service/modea.py` were dropping the isolation stamp, so a `sandboxed` job
leased to non-sandbox nodes — now stamped like the command recipe. Robustness:
F2 literal/positional braces (`{}`/`{0}`) in argv → ValueError (422) not
IndexError (500); F3 `latest_valid_manifest` guards manifest-read AND per-part
re-hash with `except (OSError, ValueError)` (a `manifest.json` that is a
directory no longer crashes the restore scan); F4 fan-out trials get per-trial
job ids `local-NNN` so each has its own `FLASHML_CKPT_DIR` (no cross-trial
weight restore) while the non-fanout path keeps `local` (resume depends on it).
Docs: strategies `LaunchSpec.files` docstring reconciled to the real output-dir
target (F5); guide notes GPU DDP is a later slice, CPU/gloo is the proven path
(F6); guide warns that reusing one `output_dir` across DIFFERENT workloads can
restore foreign weights (F4).
How verified: TDD (failing test first per behavior change). Covering suite
32 passed; full `pytest` **183 passed, 4 deselected** (was 176 at task-11); the
kill-and-resume e2e (`test_kill_and_resume_reproduces_uninterrupted_result`)
still green — F4 left the non-fanout path untouched.
Gotchas: F4's non-fanout job id MUST stay `"local"` or the resume e2e breaks
(`<out>/local/ckpt` is the shared tree a resubmit restores from). F2 keeps the
"placeholder" wording so the pre-existing KeyError test still matches.
Next: flashnode argv runner so command jobs execute remotely.
Parking lot (accepted debt from this review): (a) no placement-failure
event/reason is surfaced for an unplaceable sandboxed task — it just sits
PENDING (204 on claim); revisit with the flashnode argv runner + a protocol
event addition. (b) The legacy `hyperparameter_search`/`sharded_kmeans`
expansions remain hand-coded fallback branches (now isolation-stamped), NOT
migrated onto the `WorkloadRecipe` registry the `command` type uses. (c)
`flash.submit` is `submit(workload, output_dir=None)` only — no provider/wait
params, and `run.artifacts` are plain `Path`s (not artifact records/URIs);
remote providers + async submit are spec §10.

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
