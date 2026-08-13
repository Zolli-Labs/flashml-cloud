# Competition requirements — Beta × Alibaba Cloud × AMD

**This document is the requirements source.** Every subsequent iteration,
plan, and task must cite a requirement ID from §4 or §5. Work that cites no
requirement is out of scope until this document is amended.

**Scope decision, 2026-08-11:** two Alibaba services are in scope —
**FC Agent Sandbox** (compute) and **OSS** (artifacts). Everything else is
listed in §9 with an explicit trigger and stays unbuilt until the main two are
complete.

**Companions:** `2026-08-11-alibaba-submission-dossier.md` (positioning, field
analysis, spend, sheet content) · `plans/2026-08-11-alibaba-competition-demo.md`
(task-by-task build plan).

---

## 1. How to use this document

- **Requirement IDs are stable.** `G-*` gates, `D-*` scored dimensions,
  `C-*` FC core capabilities, `X-*` submission artifacts.
- **Every requirement names its evidence.** A requirement is not met because
  code exists; it is met when the named artifact exists and is reproducible.
- **Priority** is `P0` (submission fails without it), `P1` (core score),
  `P2` (multiplier — only after all P0/P1 are green).
- **Status** values: `not started` · `in progress` · `evidence captured` ·
  `verified`. Only `verified` may be claimed publicly.
- Iterations scope themselves by selecting requirement IDs, not by inventing
  work. If a task cannot name its ID, it is not this week's work.

---

## 2. Sources of truth

| Source | Date | Used for |
|---|---|---|
| `daniel@betauniversity.org` — deadline change | 2026-08-10 | §3 dates |
| `daniel@betauniversity.org` — rubric mail | 2026-08-04 | §4, §5 |
| `Ship The Next Hackathon.xlsx` | snapshot 2026-08-11 03:20 | `X-*` sheet columns |
| Alibaba docs 3045170 / 3045181 / 3045189 / 3045135 / 3045213 / 3047104 | fetched 2026-08-11 | §6 FC design |
| Alibaba docs 2512930 / 194364 | fetched 2026-08-11 | billing, credit |
| This repo, read directly | 2026-08-11 | what exists |

Documentation claims are accepted only where a code path and a test support
them. Anything else is marked `UNVERIFIED` inline.

---

## 3. Timeline and gates

| Milestone | When |
|---|---|
| Submission cutoff | **2026-08-15, 11:59 PM PT** (a Saturday; the mail says Friday) |
| Top 20 announced | 2026-08-17 |
| Demo Day | 2026-08-22, 555 Hamilton Ave, Palo Alto |
| Late penalty | −5 points per 5 minutes |

### Gate requirements — failure is disqualification

| ID | Requirement | Priority | Status |
|---|---|---|---|
| **G-1** | Live URL that **opens without a login** | P0 | code-complete on `develop` (share route `dccda82`; `middleware.ts` SHARE_PATH already covers `/share/<token>`, unchanged) — NOT merged to `main`, so not yet live in prod; that merge is the open blocker. Migrations 0022/0023 are already applied on both the dev and prod Supabase projects (verified 2026-08-13 by direct query) — not the blocker |
| **G-2** | Demo video ≤ 3 minutes, publicly viewable | P0 | not started |
| **G-3** | One-sentence description: WHO + WHAT + WHY NOW | P0 | drafted (dossier §6.2) |
| **G-4** | Row present and complete in the sheet before cutoff | P0 | **not started — no row exists** |

**G-1 is currently failing by construction.** `apps/web/middleware.ts`
redirects every non-marketing route to `/sign-in`, and access requires manual
admin approval. Judging runs across a weekend. **G-1 is satisfied by a
read-only public session page** — one completed run, full lifecycle, no auth,
no secrets — not by watching an approval queue.

---

## 4. Scored dimensions → requirements

Ten dimensions from the Aug 4 mail. `D-6` is stated as the largest axis and is
expanded separately in §5.

| ID | Dimension | What satisfies it here | Service | Pri | Status |
|---|---|---|---|:--:|---|
| **D-1** | Scenario realism & user value; fits execute-wait-execute | A real ML pipeline: training commits a model, a prepared evaluator waits for it. The wait is a machine fact, not a contrived human gate | FC | P1 | not started |
| **D-2** | UX; first success in 5 min; progress visible on long tasks | Public session page (G-1) readable in <15 s; lifecycle states rendered live; no login | — | P1 | not started |
| **D-3** | Runs stably on FC Sandbox; triggers hibernation instead of idling; reliable wake; fault tolerance | Three consecutive end-to-end runs, no manual timing; plus lease-based recovery of a killed training worker | FC | P0 | not started |
| **D-4** | End-to-end observability; trace ID across the chain **including hibernation**; staged checkpoints; structured logs | One correlation id spanning `session_id` → `sandbox_id` → `job_id` → `task_id` → `lease_id`, persisted append-only, covering the hibernated window | — | P1 | verified — `observability.py`, migration `0026_correlation_id.sql`, trace route `GET /v1alpha1/trace/{correlation_id}` (`09eecc0`, 11 tests) on `develop`. MCP trace tool NOT shipped — `09eecc0`'s own message defers it to the public repo as a separate follow-up (verified 2026-08-13) |
| **D-5** | Extensible skills; swap tools without rewriting; resume from checkpoint; minimal deps | `SandboxGateway` Protocol with a fake implementation; checkpoint restore already generic; pinned template with no install on the live path | FC | P1 | not started |
| **D-6** | ⭐ **FC Sandbox core advantage validation** | See §5 | FC | P0 | not started |
| **D-7** | Security & least privilege; **valid credentials after hibernation**; human confirmation for high-risk actions | Hashed one-session machine token, dedicated pool, child env allowlist, revoke-on-cleanup — plus **credential re-mint on resume** (§7.4) | FC/OSS | P1 | not started |
| **D-8** | Delivery completeness; demo script; one-click reproduction; explicit execute→hibernate→wake→continue | `scripts/competition/run_demo.py` runs the whole loop unattended; runbook states quota/allowlist prerequisites | — | P1 | not started |
| **D-9** | Cost & efficiency; hibernation saving **quantified** | Measured durations against published rates; the honest baseline argument (dossier §3.1); the OSS-shrinks-hibernation result (§7.3) | FC/OSS | P1 | evidence captured — `cost_worksheet.py` (`4d9a027`) + worksheet `.evidence/cost-worksheet-20260813T124945Z.md`; every row labelled measured/published/derived, rates explicitly modelled-not-billed (verified 2026-08-13) |
| **D-10** | Continuity & reusability; reusable components; path to production; configurable policies | FC is one supply tier behind an existing worker protocol; OSS makes artifacts provider-neutral; claim ladder states what is roadmap | Both | P1 | not started |

**Anti-patterns the rubric docks for — treat each as a negative requirement:**
no polling to fake a wait · no idle-spinning sandbox · no unlocatable failure ·
no happy-path-only demo · **no VM isolation without explaining why it was
needed** · no claiming alerts without configuring one.

---

## 5. D-6 expanded — the six FC core capabilities

This is the largest scoring axis. Each row is a requirement.

| ID | Capability | What we will show | Pri | Status |
|---|---|---|:--:|---|
| **C-6.1** | Extreme elasticity | Bounded concurrent sandbox creation, each hosting a FlashNode that claims one independent task. Report measured create rate, p50/p95 latency, failure rate, and **the cap we chose and why**. All killed in `finally` | P2 | not started |
| **C-6.2** | Strong isolation (compute + network + storage) | A probe job that **attempts and fails** to read outside its workspace, reach a forbidden host, and see host processes. Paired with the reason we need it: **we execute user-supplied ML code from a submitted repo** | P1 | evidence captured in code — `isolation_probe.py` + `test_isolation_probe.py`, 19 tests, keyless (`d3896d0`) on `develop`. Live run against a real FC sandbox is owner-coordinated, not done (verified 2026-08-13) |
| **C-6.3** | E2B compatibility | The same evaluation task run against real E2B and against FC, changing **only** `E2B_API_KEY` / `E2B_API_URL` / `E2B_DOMAIN`, producing an **identical model hash and identical `metrics.json`** | P2 | not started |
| **C-6.4** | Stateful sessions | Marker nonce hash, background process identity, **and a warm artifact/dependency cache** all intact across the hibernation boundary | P0 | evidence captured — measured: `.evidence/alibaba-hibernation-modes-20260813T041325Z.json`, 10/10 boundaries survived, all 7 continuity properties intact every time (marker, pid, pid-identity, boot id, RAM secret, heartbeat, warm cache) (verified 2026-08-13) |
| **C-6.5** | ⭐⭐ Hibernation & wake | **Both modes, for two genuinely different waits** — deep hibernation across the long wait for training, light hibernation across short gaps between evaluation shards. Measured wake latency for each; cost quantified against the right baseline | P0 | evidence captured — deep hibernation measured and claimable: p50 wake 1109 ms (keep_memory=True), 93.78% cost saving vs. active (`.evidence/cost-worksheet-20260813T124945Z.md`). Light hibernation **NOT REACHED** — both pause selectors resumed in the deep-hibernation latency band; no millisecond-level selector exists in e2b 2.31.0 (`mode_finding`, same evidence JSON) — honestly reported, not claimed (verified 2026-08-13) |
| **C-6.6** | Observability (SLS + trace + metrics + alerting + a real debugging story) | Deferred with the SLS decision (§9). P1 substitute: our own append-only ledger plus FC's supported CPU/memory metrics, with the gap stated honestly | P2 | split — correlation trace shipped (D-4, route `09eecc0`); SLS/alerting still absent, by decision (§9), gap stated honestly; the real debugging story is packaged in `2026-08-13-debugging-story-evidence.md` (verified 2026-08-13) |

**C-6.1 is unusually cheap for us and expensive for everyone else.** Elasticity
demos are contrived when the workload is one agent conversation. Ours is HPO
and evaluation shards — *N genuinely independent tasks* is the workload's
natural shape, not a stress test bolted on.

**C-6.2 answers the rubric's own anti-pattern directly.** "Using VM isolation
without explaining why you actually needed it" is a listed deduction. We run
arbitrary training and evaluation code from a user-submitted repository. That
is the strongest isolation justification available in this field.

**C-6.3 risk:** requires a real E2B account. `UNVERIFIED` whether we can obtain
one before the deadline. Fallback is to show the config diff and the single
code path, and state plainly that we could not obtain an E2B account — never
imply a comparison we did not run.

**C-6.5 is the differentiator.** Most entrants use one hibernation mode. Using
**deep for the long wait and light for the short gap** — chosen on measured
latency, not on availability — demonstrates the productized lifecycle model
Alibaba's own docs describe (3045181), rather than treating `pause()` as a
binary. Requires the **Pro** tier, which E2B SDK accounts auto-transition to
(3047104).

---

## 6. FC Agent Sandbox — how FlashML uses it

### 6.1 The role

**FC Agent Sandbox hosts a pool-scoped FlashNode.** It is not a new execution
abstraction; it is a machine that happens to be a hibernatable isolated
session. That choice reuses task placement, authentication, heartbeats,
checkpoint relay, output validation, and exactly-once acceptance — all of which
already exist and are tested.

```
FlashML pool
├── owned laptop        FlashNode  (always on, cheap, unreliable)
├── Alibaba ECS         FlashNode  (rented, wall-clock billed)     [§9]
└── FC Agent Sandbox    FlashNode  (hibernates at ~15% cost, ~1s wake)
```

The sandbox is the only member of a **dedicated pool**. No public task may
claim it, and no evaluation task may escape to another host. Pool membership is
the seventh placement gate and is already fail-closed.

### 6.2 The lifecycle

1. **Create** from a pinned template while training is being submitted.
2. **Prepare** — write `/home/flashml/prepared.json` with template version and
   a random nonce; install nothing (the template is pre-baked); start
   `flashnode work --runner trusted --max-tasks 1` against the dedicated pool.
3. **Idle** — the worker registers and heartbeats with no task available.
   *This state is deliberately shown, then deliberately ended* — leaving it here
   is the rubric's "sandbox spinning idle" anti-pattern.
4. **Deep hibernate.** Persist observed state, marker hash, process identity,
   pause latency, timestamp. Never infer state from a call; read it back.
5. **Wait** — training runs on the other pool members. A worker is killed
   mid-run; the lease expires, another node restores from the last valid
   checkpoint. *This is FlashRuntime's guarantee, not Alibaba's* — say so.
6. **External event** — the final model artifact is committed. Not a timer, not
   a poll of our own database: an artifact appearing in OSS (§7.2).
7. **Wake** via `Sandbox.connect(sandbox_id)`, which auto-resumes (doc 3045170).
   Verify marker hash, process identity, cache contents. Re-mint credentials
   (§7.4). Record wake latency.
8. **Evaluate** — claim the queued task, read the model, write accuracy /
   latency / model hash / `metrics.json`, commit once.
9. **Light hibernate** between evaluation shards if more than one is queued
   (C-6.5).
10. **Kill** in `finally`; revoke the machine token; observe the destroyed
    state; assert zero live sandboxes.

**Queue the evaluation task before resuming**, so the woken worker has
immediate useful work and the wake latency measured is the real
time-to-productive, not time-to-idle.

### 6.3 Constants (doc 3045135, verified 2026-08-11)

```
pip install e2b==2.31.0 e2b-code-interpreter==2.8.1
E2B_API_URL = https://api.<region>.e2b.fc.aliyuncs.com
E2B_DOMAIN  = <region>.e2b.fc.aliyuncs.com
template    = code-interpreter-v1
```

Regions: `cn-beijing` `cn-shanghai` `cn-hangzhou` `cn-shenzhen` `cn-hongkong`
`ap-southeast-1` `us-east-1` `us-west-1`. **Choose between `us-west-1`
(Silicon Valley — Demo Day is in Palo Alto) and `ap-southeast-1` on measured
wake latency.** Do not inherit the field's default.

### 6.4 The blocking dependency

Doc 3045170: *"Pause and resume is currently available only to allowlisted
accounts."* Doc 3047104 says E2B SDK instances auto-transition to **Pro**,
which carries both hibernation modes. Whether the tier transition satisfies the
gate is **UNVERIFIED and is the single most important open question.**

Resolve by: (a) the smoke test, empirically, today; (b) asking DingTalk group
**179855020297** (named in 3047104 for tier configuration) and Discord.

**If pause is blocked, do not simulate it.** Demonstrate active-state stateful
sessions, state the account limitation plainly, and score lower honestly.

---

## 7. OSS — how FlashML uses it

### 7.1 What OSS replaces, and what it does not

OSS replaces **the coordinator's local artifact disk**
(`flashml-coordinator-data`). It does **not** replace Supabase, which holds
small relational rows — jobs, pools, machines, attempts, contributions, auth.
No migration, no auth change, no schema change.

`flashruntime/artifacts/store.py` already implements `OSSArtifactStore` with
native `oss2` and optional STS, behind a backend-neutral `artifact://` URI.
Wiring is configuration; the client is written.

### 7.2 The three jobs OSS does here

**(a) It makes the wake trigger an observable fact.** The external event is a
model artifact **appearing in OSS**, not our database saying training finished.
A shared, provider-neutral object store is a better event source than our own
control plane's opinion — and it is checkable by anyone, including a judge.

**(b) It decouples evaluation from our control plane.** The woken sandbox reads
the model from OSS directly. So the wake→evaluate path does **not** require our
API to be alive. This makes the API-restart-during-hibernation rehearsal a
genuine reliability property rather than a recovery trick: **the control plane
can die mid-hibernation and the evaluation still completes.**

**(c) It makes destroy-vs-hibernate a fair comparison.** Because artifacts do
not live on the sandbox's disk, killing and recreating a sandbox loses nothing.
That is what allows the honest cost comparison in dossier §3.1 to be measured
rather than argued.

### 7.3 OSS shrinks the hibernation bill — the design principle

Deep-hibernation disk is billed as `memory × 2 + disk` **with no free
allowance** (doc 3045213). So **anything kept on the sandbox's local disk is
paid for, per hour, for the entire wait.**

| Sandbox (4 GiB mem) | Hibernated disk | $/hr (mainland) |
|---|---:|---:|
| Model + dataset held locally, 30 GiB disk | 38 GiB | 0.012120 |
| **Data in OSS, 15 GiB disk** | **23 GiB** | **0.007336** |

**≈ 39.5% cheaper hibernation**, in every region, purely from where bytes live.

This yields a design rule worth stating on the slide, because it is ours and
nobody else in the field will have it:

> **Environment stays, data streams.** Keep in the sandbox what is expensive to
> rebuild and small — interpreters, dependencies, benchmark tooling. Keep in OSS
> what is large and cheap to fetch — models, checkpoints, datasets. Hibernation
> then preserves the expensive thing and stops paying for the large thing.

The trade-off is fetch latency on wake, which is measurable. Measure it; if the
model fetch dominates wake time, the rule has a size threshold and we should
report it rather than assert the rule universally.

### 7.4 Credentials — the decision, and a bug to avoid

`ArtifactStore` exposes only `put_file` / `get_file`. **There is no presigned
URL support today** (verified). So a sandbox reading OSS directly needs one of:

| Option | Verdict |
|---|---|
| Bake an Alibaba access key into the sandbox | **Never.** User ML code runs there |
| Proxy every byte through our API | Safe, works today, but puts our API in the path and forfeits §7.2(b) |
| **Presigned URLs** minted by the cloud API | **Chosen.** No credential enters the sandbox at all; scoped to one object; naturally expiring. Needs a small `sign_url` addition on the cloud side (`oss2.Bucket.sign_url`) |
| STS scoped to `jobs/<job_id>/` | Correct and more general; `OSSArtifactStore` already accepts `security_token`. Use if the sandbox needs to enumerate rather than fetch known keys |

**⚠️ The bug the rubric is explicitly probing.** D-7 says *"valid credentials
after hibernation."* Any presigned URL or STS token minted **before** a
multi-hour deep hibernation is **expired when the sandbox wakes**. Credentials
must be **re-minted on resume**, never carried across the boundary. Design the
wake path to fetch fresh URLs as its first action after the health check.

### 7.5 Integrity

Parts first, manifest last, SHA-256 verified — the existing checkpoint
discipline. The evaluated model hash must equal the committed model hash
exactly; that equality is the evidence for D-10 continuity.

---

## 8. Evidence register

A requirement is met when its artifact exists and re-running produces it again.

| Artifact | Proves |
|---|---|
| Smoke-test JSON: create/pause/resume latencies, marker hashes, PIDs, both regions | C-6.4, C-6.5, region choice |
| Three unattended end-to-end run logs | D-3, D-8 |
| Lease event excerpt: kill → expiry → requeue → different node id → restored step → one accepted commit | D-1, D-3, D-10 |
| Isolation probe output: attempted and denied | C-6.2 |
| Concurrency report: p50/p95 create, failure rate, chosen cap and rationale | C-6.1 |
| Dual-endpoint run with identical model hash | C-6.3 |
| Cost worksheet: measured durations × published rates, both baselines, OSS disk-shrink result | D-9 |
| Correlation-id trace spanning the hibernated window | D-4 |
| Cleanup ledger: every sandbox destroyed, every token revoked | D-3, D-7 |
| Public session page URL | G-1, D-2 |

Every bundle records environment versions, source commit, template digest,
timestamps, and hashes. No credentials, no user data.

---

## 9. Deferred — with triggers, not vibes

Nothing here is built until every P0 and P1 above is `verified`.

| Service | Trigger | What it would add |
|---|---|---|
| **ECS** | main two verified, and ≥½ day remains | A rented Alibaba tier in the same pool; makes the three-tier cost story literal. Highest value of the deferred set |
| **SLS** | main two verified | C-6.6, D-4 upgrade. Independent Alibaba-side record of the hibernated window. `infra/alibaba/sls/aliyunlogconfig.yaml` already exists and every log line carries `job_id` |
| **CloudMonitor** | SLS done | One alert: sandbox active past timeout. The rubric docks for claiming alerts without configuring one — so either configure one or claim nothing |
| **RAM/STS** | only if §7.4 moves off presigned URLs | More general credential scoping |
| **ACR** | only if the built-in template proves insufficient | Custom template image; `acr-build-push.sh` exists |
| **Qwen / Model Studio** | **never for this submission** | No LLM exists in our execution path. Adding one is the "model wrapper" the rubric penalises |
| **PAI-DLC / AIMaster** | post-competition | Managed distributed training backend. Alibaba already owns Alibaba-local fault tolerance; delegate rather than duplicate |
| **ECI · ACK · EventBridge · MNS · Hologres** | none | Logo count is not a scored dimension |
| **AMD / ROCm** | real ROCm hardware completing the same workload | GPU discovery is `nvidia-smi`-oriented; all GPU evidence is CUDA. No claim without a run |

State the deferrals on the slide. In a field where one team lists thirteen
services, *"here is what we did not use, and why"* reads as judgement.

---

## 10. Iteration scoping rule

Each iteration:

1. Selects requirement IDs from §4/§5 — **P0 before P1 before P2, no exceptions.**
2. Names, per ID, the evidence artifact from §8 it will produce.
3. Ends by moving each ID's status and attaching the artifact.
4. Claims nothing publicly that is not `verified`.

**Iteration 1 is fixed and narrow:** resolve §6.4. Everything below it —
architecture, score, and most of the remaining schedule — depends on one
question that costs cents to answer. Do not begin C-6.*, D-*, or the OSS work
until it returns GO.

Immediate non-engineering actions, none of which are blocked by §6.4:
**G-4** (claim the sheet row), the allowlist request, and the DingTalk/Discord
question in §6.4.
