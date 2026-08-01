# Deployed multi-user POC — design

**Date:** 2026-07-31
**Scope:** flashml-cloud (primary), flashruntime, flashnode
**Milestone:** M1 of 3 (see §2)
**Status:** design approved. Revised 2026-07-31 to move Windows hosts and the
federated-averaging driver into M1, per the owner's minimum acceptance bar (§10).

---

## 1. Context and goal

The local milestone is complete and proven: plan → submit → lease → validated
commit → kill-a-machine recovery → distributed K-means → cross-machine
checkpoint resume, with durable leases. Suites are green (flashruntime 323,
flashnode 73). All of it runs on one developer's machines over localhost or LAN.

**The goal of this milestone is to put that loop on the public internet with real
accounts**, so that:

- a person signs in from a laptop or phone with Google and submits a job;
- a different person installs a package, logs their machine in, and that machine
  executes the first person's work;
- neither person is the developer who built it, and neither needs a shared secret.

This is the prerequisite for everything after it. Heterogeneity-aware placement
(M2) cannot be designed, tuned, or validated without real heterogeneous machines
attached to a real coordinator — which is exactly what this milestone produces.

### 1.1 What changed from the original plan

`SPRINT_PLAN.md` Days 4–7 and `PLAN_2WEEKS.md` Stage 5 target Alibaba Cloud
(ACK/ACR/OSS/SLS). That work is **deferred, not abandoned** — `infra/alibaba/`
stays in the repo and remains the strategic target. It is deferred because no
Alibaba credentials have ever existed in this workspace (no `.env.alibaba`, no
`aliyun`/`ossutil` CLI installed), so Stage 5 was blocked on a Day-0 dependency
that never landed.

This milestone uses **Supabase** (free tier) and **Render** (existing credit)
instead, which cost nothing additional and produce a public URL immediately.

---

## 2. Milestones

**M1 — Deployed and authenticated.** *This document.* Public URL, Google
sign-in, GitHub-repo job submission, per-machine agent credentials, and the
security work required before a coordinator faces the internet.

**M2 — Heterogeneity-aware distribution.** Concrete admission probes
(`flashnode/benchmark/`), a capability-matching `PlacementPolicy`
(`flashruntime/scheduler/`), shard sizing proportional to measured throughput,
a federated-averaging driver so neural networks get the round-based treatment
K-means already has, and straggler reassignment. See §9.

**M3 — Trust.** Result verification (a lying node is currently believed),
cooperative cancel, disk quotas.

### 2.1 In scope for M1

- Supabase Auth (Google + email), Postgres, and Storage.
- Three Render services: web, API, coordinator.
- One account with two capabilities: **Host** and **Developer**.
- GitHub repo → `flashml.yaml` → job, with preflight validation.
- `flashnode login` device flow; per-machine tokens; revocation.
- Artifact/checkpoint `PUT` scoped to the caller's live lease.
- Worker platforms: **macOS, Linux, and Windows**.
- **A federated-averaging driver** so one PyTorch model trains across several
  volunteer machines over the internet (§5.4).

### 2.2 Explicitly deferred

| Item | Milestone | Why |
|---|---|---|
| GPU hosts | M1.5 | `inventory/capabilities.py:99` hardcodes `gpus=[]`; no `--gpus` passthrough in either runner; no GPU placement gate. Needs NVIDIA hardware to verify — `scripts/runpod_gpu_e2e.py` can rent one for under $1. |
| Capability-proportional shard sizing | M2 | M1 uses equal shards with a round quorum (§5.4.3), which handles uneven hardware adequately without admission probes. |
| Custom image builds | M2+ | Needs a build host with Docker, a registry, and node allowlist propagation. The curated image set covers the POC. |
| Private GitHub repos | M1.5 | Public repos need no GitHub OAuth at all. Private repos need a linked GitHub identity. |
| Alibaba deployment | later | See §1.1. |
| Phone as a *worker* | out of scope | The sandbox is Docker. iOS cannot run a container daemon; Android cannot without root. Phones are first-class **clients** in M1. |
| Domain `zolliai.com` | later | Not purchased yet. M1 uses the Render-provided subdomain; nothing in the design depends on the hostname. |
| Multi-GPU DDP across volunteers | out of scope | `--network none` means ranks cannot rendezvous, and home links are orders of magnitude too slow for per-step gradient exchange. See §9.1. |

---

## 3. Architecture and deployment

### 3.1 Topology

**Supabase (free tier)**

- **Auth** — Google OAuth + email. Issues the JWT that the web app and API trust.
- **Postgres** — product data (§4).
- **Storage** — artifacts and checkpoints, reached through the existing
  `S3CompatibleArtifactStore` (`flashruntime/artifacts/store.py`), which already
  speaks the S3 API. This is configuration, not new code.

**Render (three services)**

| Service | Visibility | Notes |
|---|---|---|
| `flashml-web` | public | Next.js. Rebuilt against the real API — the current `apps/web/lib/api.ts` targets a legacy coordinator that no longer exists. |
| `flashml-api` | public | FastAPI. **The only internet-facing backend.** |
| `flashml-coordinator` | **private** | The flashruntime service. No public URL. Persistent disk for `leases.db`. Single uvicorn worker. |

Agents (`flashnode work`) point at `flashml-api`, never at the coordinator.

`flashml-api` and `flashml-coordinator` are both configured against the **same**
Supabase Storage bucket via `store_from_env()`. The coordinator needs its own
access because commit-time validation re-hashes the artifact
(`HANDOFF.md` §4) — it must read what the agent uploaded through the API.

Only `flashml-api` and `flashml-coordinator` must stay awake. `flashml-web`
serves static and SSR content and may sleep without correctness impact.

### 3.2 Why the coordinator is private

This single decision retires the top-ranked risk structurally instead of
patching it.

`HANDOFF.md` risk #2 and the 2026-07-29 deferred follow-up #1 record that
`PUT /artifacts/{key}` (`service/modea.py`) and the checkpoint endpoints
(`service/checkpoints.py`) are unauthenticated and not lease-scoped: any
registered node can overwrite another job's commit artifact or checkpoint
manifest, and the sha256 check is no defense because the attacker supplies both
the file and the hash. The standing guidance is "do not put the current
coordinator on a public IP for longer than a demo."

With no public ingress, that surface is not reachable from the internet at all.
The API becomes the single door and enforces lease scoping (§6.1) before
forwarding.

It also preserves `HANDOFF.md` risk #5 by construction: `LeaseManager` and
`SqliteLeaseStore` are safe only because FastAPI runs them on one event loop.
One private instance with one worker means a future scaling decision cannot
silently violate that assumption.

This is cheap to implement because `CoordinatorClient` (`executor/client.py:29`)
already takes a base URL and holds every endpoint the agent uses; the API mirrors
the `/v1alpha1/*` surface it already partially proxies.

### 3.3 Request flow

| # | Step |
|---|---|
| 1 | Developer signs in (Supabase Auth), connects a GitHub repo, picks a branch |
| 2 | API fetches the repo tarball via the GitHub API, stores it in Supabase Storage as an `artifact://` input |
| 3 | **Preflight** (§5.3). Failures surface here, before anything is queued |
| 4 | API writes a `jobs` row and POSTs a `JobSpec` to the coordinator |
| 5 | Coordinator expands to tasks and queues them |
| 6 | Agent calls `POST /v1alpha1/leases/claim` **on the API**; the API resolves the node credential to a machine and forwards |
| 7 | Agent stages inputs at `/work/inputs/`, runs the pinned image with `--network none`, writes `/work/out/metrics.json` |
| 8 | Agent uploads outputs through the API, which rejects any key outside its live lease prefix, into Supabase Storage |
| 9 | Coordinator validates sha256 and commits; the web app shows status, events, and results |

---

## 4. Data model

Supabase Postgres. `auth.users` is Supabase-managed.

| Table | Columns |
|---|---|
| `profiles` | `id` (FK `auth.users`), `display_name`, `github_login`, `is_host`, `is_developer`, `created_at` |
| `machines` | `id`, `owner_id`, `node_id` (unique), `name`, `platform`, `capabilities` jsonb, `token_hash`, `token_prefix`, `status` (`pending`/`active`/`revoked`), `last_seen_at`, `created_at`, `revoked_at` |
| `device_codes` | `device_code` (PK), `user_code` (unique), `machine_id`, `approved_by`, `expires_at`, `consumed_at` |
| `jobs` | `id` (mirrors coordinator `job_id`), `owner_id`, `name`, `source` jsonb (repo/ref/commit sha), `spec` jsonb, `status`, `created_at`, `finished_at` |
| `contributions` | `id`, `machine_id`, `job_id`, `task_id`, `accepted_at`, `duration_s` |

**No `job_events` table.** The coordinator's ledger stays the single source of
truth and the API proxies it. This is the argument the existing
`apps/api/flashml_cloud_api/store.py` already makes ("Jobs/events stay in
FlashRuntime's ledger… rather than duplicating state at POC scale"). Mirroring
status into Postgres would create two truths that drift.

`jobs.status` is a **cached** projection for list views, refreshed on read from
the coordinator; the coordinator's value always wins on conflict.

**Roles are capabilities, not account types.** `is_host` flips when a machine is
enrolled, `is_developer` when a job is submitted. One login does both. The UI
phrases them as actions ("Share my machine" / "Run my workloads"); the code keeps
the **Host** / **Developer** vocabulary from `docs/SYSTEM_OVERVIEW.md:39` so the
three repos' documentation stays consistent.

**Database access is API-only.** RLS is enabled with deny-by-default policies for
the `anon` and `authenticated` roles, so a browser holding a valid JWT still
cannot query Postgres directly. Every read goes through the API, which filters on
`owner_id`. One enforcement point, one place to test.

---

## 5. Authentication and the job flow

### 5.1 Browser → API

Supabase JWT in `Authorization: Bearer`. The API verifies the signature against
Supabase's JWKS and reads `sub` as the user id. No session state in the API.

### 5.2 Agent → API

A per-machine bearer token issued through a **device flow** modelled on
`gh auth login`:

```
$ flashnode login
  Go to https://<app>/activate and enter code:  WXYZ-1234
  Waiting…  ✓ Approved as "Phong's MacBook Pro"
```

1. Agent `POST /v1alpha1/device/code`, sending the `node_id` from
   `flashnode/identity/store.py` (generated locally on first run) plus its
   hostname and platform → `{device_code, user_code, verification_uri, interval}`.
2. Agent prints the code and polls `POST /v1alpha1/device/token`.
3. The user, signed in on **any** device including a phone, enters the code and approves.
4. Approval creates the `machines` row, **binding that `node_id` to the row**,
   and returns the token **once**.
5. Agent writes `~/.flashnode/credentials.json` (mode 0600); `flashnode work` uses it.

The `node_id` is claimed at enrollment and is unique across `machines`, so a
second machine presenting an already-bound `node_id` is refused at approval time
rather than silently taking over an existing machine's identity. After
enrollment the token is authoritative: token → machine → `node_id`.

Tokens are opaque and random; only a hash is stored (`token_hash`), with
`token_prefix` retained for display. Revoking a machine in the web UI sets
`status='revoked'`, and the next agent call gets 401.

**Ed25519 is deliberately deferred.** `flashnode/identity/` already documents
signing keys as a future step. An opaque token is instantly revocable and simple
to debug; Ed25519's real advantage is that the server never holds a verifier
secret, which matters at scale rather than at POC size. The `identity/` seam is
left intact so M2/M3 can adopt it without a data migration.

**Security rule:** the API resolves `node_id` **from the token**, never from the
request body. `CoordinatorClient.claim()` currently sends `node_id` in the body
(`executor/client.py:91`); the API overwrites that value rather than validating
it. Without this, any authenticated agent could impersonate another machine.

### 5.3 `flashml.yaml` and preflight

A repo declares its job in `flashml.yaml` at the root:

```yaml
version: 1
name: cifar-sweep
image: pytorch-cpu           # from the curated set
entrypoint: train.py
args: ["--epochs", "20"]
sweep:                       # optional fan-out: one task per combination
  lr: [0.001, 0.01, 0.1]
  batch_size: [32, 64]
resources:
  cpus: 2
  memory_gb: 4
timeout_seconds: 1800
```

This compiles to a flashruntime `JobSpec` through `CommandRecipe`. The example
above expands to six independent tasks — the proven fan-out shape from Stage 3.

**Curated images for M1** (published to GHCR, and shipped as the agent's default
`FLASHNODE_ALLOWED_IMAGES` so hosts trust them without configuration):

- `ghcr.io/zolli-labs/flashml-python-slim:1` — stdlib only
- `ghcr.io/zolli-labs/flashml-sklearn:1` — numpy, pandas, scikit-learn
- `ghcr.io/zolli-labs/flashml-pytorch-cpu:1` — torch (CPU)

`flashml.yaml`'s `image:` field takes the short alias (`pytorch-cpu`); the API
resolves it to the pinned, immutable reference above. Users never write a
registry path in M1, which is what keeps the allowlist closed.

A fixed set matters beyond convenience: per the 2026-07-29 log, the coordinator
does **not** filter tasks by a node's image allowlist, so a mismatch is claimed
and then fails on the node. A curated set shipped as the default allowlist keeps
that consistent by construction.

**Preflight** runs at submit time, before the job is created, and reports every
problem at once:

| Check | Failure message shape |
|---|---|
| `flashml.yaml` parses and validates | schema errors with line numbers |
| `image` is in the curated set | "unknown image `foo`; available: …" |
| `entrypoint` exists in the tarball | "`train.py` not found in repo at `<sha>`" |
| Imports satisfied by the image | "imports `transformers`, not present in `pytorch-cpu`" |
| No network use | "uses `requests`; volunteer nodes run with `--network none`" |
| Writes `metrics.json` | "no reference to `metrics.json`; the coordinator validates on it at commit time and the task cannot commit without it" |

Import and network checks are a static `ast` scan of the repo's `.py` files —
no execution, so preflight is cheap and safe. The last two are **warnings** that
require an explicit override, not hard failures, because static analysis cannot
prove absence.

This is the "tell users their code isn't supported" requirement, and it fires
before a job ever reaches a volunteer's machine.

### 5.4 Distributed training: the federated-averaging recipe

This is the headline acceptance criterion: submit PyTorch code, and have **one
model** trained across several volunteer machines over the internet.

#### 5.4.1 Why this shape and not DDP

Per-step DDP gradient exchange cannot be relayed over the internet — 50–200 ms
round trips against hundreds of steps per second means communication cost
exceeds compute by orders of magnitude. It is also structurally impossible here:
`--network none` means ranks cannot rendezvous at all, which
`docs/guides/bring-your-code.md` already records ("`mode: "coordinated"` is not
available on volunteer nodes").

The workable form exchanges **every N steps instead of every step**: each worker
trains locally for a few hundred steps and sends only the accumulated weight
delta; a driver averages the deltas and broadcasts the next round's weights.
Communication drops 100–500×, which home links and NAT tolerate.

#### 5.4.2 Reusing the proven driver pattern

`flashml_workloads/kmeans_driver.py` already implements exactly this control
flow: submit one job per round → poll to `SUCCEEDED` → collect the round's
`metrics.json` artifacts → reduce → submit the next round. Its docstring states
the principle: "pipelines are jobs chained by a driver, not a new execution
mode." The recovery properties come along unchanged — a dead worker costs one
shard retry, and a dead driver resumes from the last completed round.

`fedavg_driver` is that loop with `reduce` replaced:

| K-means (proven) | FedAvg (new) |
|---|---|
| upload shard CSVs once | upload round weights each round |
| task: assign points → partial sums | task: load weights, train K local steps → weight delta |
| reduce: sums/counts → new centroids | reduce: weighted mean of deltas → new weights |
| next iteration | next round |

The per-worker task follows `flashml_workloads/sgd_trainer.py`'s contract
(`--spec spec.json --out OUTDIR`, inputs staged at `/work/inputs/`, results to
`/work/out/`), with torch replacing stdlib arithmetic.

#### 5.4.3 Stragglers: round quorum

`kmeans_driver` requires every shard (`if len(partials) != len(shard_uris):
raise`). FedAvg must not: a Windows laptop on home wifi would otherwise stall
every round for everyone.

The driver instead aggregates once **`min_participants` of `N`** deltas have
arrived, or a round deadline passes — standard FedAvg partial participation.
Late deltas are discarded, not applied to a later round, so averaging stays
correct. This is the cheap answer to heterogeneous hardware and it is why M1
does **not** need M2's admission probes: uneven machines degrade participation
rate rather than blocking progress.

#### 5.4.4 Data placement

Shipping a dataset per task per round would dominate the transfer budget. M1
supports two modes:

- **Baked-in** (the demo path): a small dataset ships inside the curated
  `flashml-pytorch-cpu` image; each task receives a shard *index* and selects its
  slice. Downloaded once per host, then free forever.
- **Input artifact**: user data staged at `/work/inputs/` as today. Suitable for
  small datasets; the UI warns above a size threshold.

#### 5.4.5 Where the driver runs

The driver is a control loop, not a training process, so it runs **inside
`flashml-api`** as a background task keyed to the job — not on a volunteer
machine. This keeps round aggregation off untrusted hardware, which matters
because until M3 lands a node's reported results are believed. Driver progress
(round number, participants, current loss) is written to the `jobs` row for the
UI to read.

**Repo boundary:** `fedavg_driver` itself belongs in **flashruntime**
(`flashml_workloads/`, beside `kmeans_driver`), not in flashml-cloud.
`CLAUDE.md`'s boundary principle requires the open runtime to stay genuinely
useful without the cloud, and federated averaging is a runtime capability, not
a commercial one — a self-hosted user running `flashml serve` should get it too.
flashml-cloud only *invokes* it as a hosted background task and renders its
progress. Nothing about the driver may depend on Supabase or the cloud schema.

### 5.5 Windows hosts

Two concrete changes, both in flashnode:

1. `executor/hardening.py:60` builds `--user {os.getuid()}:{os.getgid()}`;
   neither function exists in Python on Windows. The flag becomes
   platform-conditional — omitted on Windows, where Docker Desktop does not map
   host uids and the image's own non-root `USER` applies instead. The curated
   images must therefore **declare a non-root `USER`**, so dropping the flag does
   not silently mean container root.
2. The `-v {workdir}:/work` bind mount needs Windows path translation
   (`C:\Users\…` → the form Docker Desktop accepts).

`FLASHNODE_WORKDIR` must default to a path under the user profile, for the same
reason it exists on macOS: Docker Desktop only shares specific host directories.

---

## 6. Security changes required by this milestone

### 6.1 Lease-scoped artifact and checkpoint writes

**Today:** `PUT /v1alpha1/artifacts/{key}` accepts any key from any registered
node. **Change:** the API derives the allowed key prefix from the caller's live
lease (`jobs/<job_id>/tasks/<task_id>/…`) and rejects anything outside it with
403. Checkpoint part and commit endpoints get the same treatment.

This is deferred follow-up #1 from 2026-07-29, which the notes schedule
alongside per-node identity — exactly this milestone.

### 6.2 Shared join code removed

`FLASHML_JOIN_CODE` / `X-FlashML-Join-Code` (`service/app.py:76`,
`service/modea.py:270`) is one secret for all volunteers with no revocation. It
is replaced by per-machine tokens (§5.2). The coordinator keeps the join-code
path for self-hosted use; the deployed API does not use it and the coordinator
is unreachable except through the API.

### 6.3 Silent-idle diagnosis

Deferred follow-up #2: `/leases/claim` returns 204 for both "queue empty" and
"permanently ineligible for this node", and the agent logs neither — both
silent-idle bugs in the volunteer slice presented exactly this way. The API
distinguishes them and surfaces "waiting for a machine that can run X" in the
job view. This is small, and it is the difference between a POC that looks
broken and one that explains itself.

### 6.4 Not fixed in M1 (recorded, deliberate)

Result verification (a lying node is believed), cooperative cancel, `/work` disk
quotas, and `harden_args` yielding `--user 0:0` when the agent runs as root.
These are M3. They are acceptable for M1 because the pool is small and hosts are
people you can contact; they are **not** acceptable at open-signup scale, and
opening host registration beyond an invite list should be gated on M3.

---

## 7. Error handling

| Condition | Behavior |
|---|---|
| GitHub fetch fails | Job is never created; error shown with the HTTP status |
| Preflight fails | Job is never created; all problems itemized at once |
| No eligible node | Job stays PENDING; UI states what capability is missing (§6.3) |
| Node dies mid-task | Existing lease expiry → requeue. Proven; unchanged |
| Node drops mid-round (lid closed) | Round aggregates on quorum (§5.4.3); the job does not stall. The machine rejoins on next claim without operator action |
| Fewer than `min_participants` online | Round waits, job stays RUNNING, UI states how many machines are needed and how many are online |
| Driver crashes mid-run | Restarts from the last completed round's weights artifact; rounds are idempotent |
| Coordinator restarts | Durable SQLite leases on the Render disk. Proven; unchanged |
| Agent token revoked | 401; agent exits with a message naming the machine |
| Supabase unavailable | API returns 503 for product operations; the coordinator keeps running so in-flight work completes and commits |
| Render disk lost | Leases lost, jobs must be resubmitted. Accepted for M1; artifacts survive in Supabase Storage |

---

## 8. Testing

**Existing suites must stay green:** flashruntime 323, flashnode 73. Any drop is
a regression, not a tradeoff.

**New unit tests**

- JWT verification: valid, expired, wrong issuer, missing.
- Device flow: code issued, approved, consumed once, expired, polled before approval.
- Token resolution: body `node_id` is overwritten by the token's machine.
- Lease-scoped writes: a key outside the live lease prefix returns 403 (this test
  is the proof that §6.1 landed).
- Preflight: each check in §5.3 fires on a crafted repo fixture.
- `flashml.yaml` → `JobSpec` compilation, including sweep expansion arity.
- FedAvg reduce: averaging hand-built deltas yields the expected weights; a
  round with fewer than `min_participants` does not aggregate; a late delta
  arriving after aggregation is discarded rather than applied to the next round.
- FedAvg driver resume: a driver restarted mid-run continues from the last
  completed round, not from round 0.
- `harden_args` on Windows: omits `--user`, and the argv is asserted against a
  fixture so the flag cannot silently return. Paired with a test that the
  curated images declare a non-root `USER` — dropping the flag is only safe
  because of that.

**Integration**

- The four real-daemon Docker tests in flashnode currently **auto-skip** because
  no Docker daemon is available in the dev environment. They must actually run
  before this milestone ships — this deploy puts strangers' code on real
  machines, and until those pass the sandbox is proven only by constructed-argv
  assertions. Requires colima running.
- Cross-repo chain test in workspace `e2e/`: enroll → claim → run → scoped
  upload → commit, with a real token.

**Deployment acceptance (§10 is the checklist).** Note the environment gotchas
that have already cost debugging time: pytest `tmp_path` bind-mounts as an
**empty** directory under colima, so integration tests must use
`FLASHNODE_WORKDIR` under `$HOME` (2026-07-29 gotcha (b)); and subprocesses need
a neutral cwd or the repo directories shadow installed packages.

---

## 9. Notes toward M2 (distributed training on weak devices)

Recorded here because it shaped M1's boundaries and should not be rediscovered.

### 9.1 What is and is not possible

Per-step DDP gradient exchange relayed through an API is not achievable — 50–200 ms
round trips against hundreds of steps per second means communication cost exceeds
compute by orders of magnitude. This is latency, not a scheduling deficiency, and
no coordination layer fixes it. `docs/guides/bring-your-code.md` already states
the consequence: `mode: "coordinated"` is unavailable on volunteer nodes.

The workable form is to **exchange every N steps instead of every step**: each
worker trains locally for a few hundred steps and sends only the accumulated
weight delta to a driver, which averages and broadcasts the next round's weights.
That reduces communication 100–500×, tolerates high latency and slow links, and
is how federated learning works in practice.

**This is the shape flashruntime already has.** `flashml_workloads/kmeans_driver.py`:
"One iteration = one Mode A job (N independent shard tasks); the driver reduces
the shard partials into new centroids and submits the next iteration… pipelines
are jobs chained by a driver, not a new execution mode." A federated-averaging
driver replaces "sum shard partials → new centroids" with "average weight deltas
→ new weights" and reuses everything else, including the crash-recovery
properties (a dead worker costs one shard retry; a dead driver resumes from the
last completed iteration).

Workers never talk to each other — they exchange through the coordinator. That
keeps `--network none` intact, which is the property that makes donated hardware
safe to accept.

### 9.2 Scaffolding that already exists

- `flashruntime/scheduler/__init__.py` — `PlacementPolicy` with `eligible()`
  (capability gate: "payload wants vram_gb the node lacks → False") and `score()`
  ("prefer short tasks for soon-to-drain nodes"). Interface complete; only
  `FifoPlacement` and `IsolationAwarePlacement` are implemented. Research item R9.
- `flashnode/benchmark/__init__.py` — admission probes, "measured capability,
  never self-reported": `cpu_hash_mbps`, `mem_bandwidth_mbps`, `disk_write_mbps`,
  `net_down_mbps`. Interface and orchestration complete; concrete probes deferred.
- `flashml_workloads/kmeans_driver.py` — the round-based driver pattern, proven
  (3 iterations × 4 shards across 2 agents converging to true centers).

M2 is therefore: implement the probes, implement a capability-matching policy,
size shards proportional to measured throughput so a slow laptop does not hold up
a round, add the federated-averaging driver, and handle stragglers.

### 9.3 Also unbuilt

`flashruntime/providers/` is a complete interface with **zero** concrete
adapters. `scripts/runpod_gpu_e2e.py` is a one-off test harness, not a provider —
it SSHes into a rented box and runs pytest directly, never starting an agent or
claiming a lease. On-demand capacity rental is not a product feature today.

---

## 10. Definition of done

**The minimum bar, in the owner's words:** *submit a PyTorch job; friends on Macs
and Windows machines connect as hosts; the model trains distributed across them
over the network.*

M1 is complete when, on the deployed system:

1. A public HTTPS URL (Render subdomain) loads on a phone and a laptop, and
   Google sign-in works on both.
2. **A Mac and a Windows machine** are enrolled via `flashnode login`, approved
   from a phone browser, and show as online in the web UI. Linux is expected to
   work and is tested, but Mac + Windows is the bar, because that is what the
   testers own.
3. A public GitHub repo containing **PyTorch training code** with a `flashml.yaml`
   is submitted from the browser.
4. **The model trains across both machines via the federated-averaging driver**:
   several rounds complete, each with contributions from more than one machine,
   and the loss decreases monotonically across rounds. The job view names which
   machine contributed to which round.
5. Closing the lid on one machine mid-round does not stall the job — the round
   completes on quorum, and the machine rejoins later without manual steps.
6. Final weights and metrics are downloadable from the browser.
7. Revoking a machine stops it receiving work within one claim interval.
8. A crafted upload to a key outside the caller's live lease returns 403, proven
   by a test.
9. Preflight rejects a repo importing a package absent from its chosen image,
   with a message naming the package.
10. **A friend — not the developer — completes signup → enroll their machine →
    see their machine contribute to a round, unaided, from written instructions.**
11. Existing suites green (flashruntime ≥323, flashnode ≥73), and the flashnode
    Docker integration tests **run rather than skip**.

Items 4 and 10 are the real tests. The rest can be satisfied by someone who
already knows where the sharp edges are.

**Honest limits of this bar.** Item 4 proves *collaborative* training, not that
it is faster than one machine — over home links with small models it will
usually be slower than local training, and the spec does not claim otherwise.
What it proves is that the loop is real: independent machines, owned by
different people, on different operating systems, jointly improving one model
with fault tolerance. Speedup is a scale-and-scheduling question for M2.

---

## 11. Open questions

1. **Render service tier.** `flashml-coordinator` and `flashml-api` must not
   sleep — a suspended coordinator drops heartbeats and expires live leases.
   Confirm the credit covers two non-sleeping instances plus a persistent disk
   on the coordinator. `flashml-web` may run on a sleeping tier.
2. **Host invite gating.** §6.4 argues open host signup should wait for M3
   (result verification). M1 could gate host enrollment behind an invite code
   while developer signup stays open. Decide before launch. *(Note: the M1
   testers are the owner's friends, so an invite gate costs nothing now and
   removes the "a stranger lies about results" exposure entirely.)*
3. **Demo dataset.** Which small dataset ships baked into
   `flashml-pytorch-cpu` for §5.4.4 — MNIST (~11 MB, keeps the image small) or
   CIFAR-10 (~170 MB, a more convincing demo but a slower first pull for every
   host)?
