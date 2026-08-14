# Automatic resource routing: how compute marketplaces match jobs to machines

**Date:** 2026-08-13 · **Status:** research (feeds a routing design; changes nothing)
**Related:** `docs/superpowers/specs/2026-08-11-zolli-marketplace-design.md` (decisions M1–M14) ·
`flashml_cloud_api/router/` (read-only planner) ·
`docs/superpowers/specs/2026-08-13-device-profiles-provider-network-design.md`
(device profiles + provider network; its "Phase 2 — the contract" section is the
schema this doc's §5 consumes: trust_state, allocatable CPU, benchmark probes,
geo/uptime fields) · `docs/superpowers/plans/2026-08-13-pool-routing-phase1.md`
(Phase 1 implementation plan, in flight on `feat/pool-routing-phase1`)

---

## 0. TL;DR

The ask: users submit a job (model, resources, target price, bid) and the system
routes it to machines automatically — no browsing, no manual machine adding, with
sane trade-offs across price, reliability, and location.

The research answer, from studying Akash, Vast.ai, SaladCloud, RunPod, SkyPilot,
io.net, Prime Intellect, SF Compute, Nosana, Golem, Render, and Gensyn:

1. **We should not build an open auction.** Akash — the biggest name in this space —
   is actively retreating from its pure reverse auction because it produces
   *silent zero-bid failures* and punishes small providers with per-bid costs.
   Their 2026 flagship work (AEP-67) is a centralized pre-matching service with a
   live inventory snapshot and explicit rejection reasons — i.e., they are building
   what FlashML's architecture already is. Our centralized coordinator is not a
   compromise; it is where the market leader is heading.
2. **The winning shape is: default-on trust gate → capability gates → rank by
   quality-adjusted price → plan → entitlement.** Every surviving marketplace
   converged on the first three. We already have most of the pipeline built and
   tested — the marketplace engine (`marketplace.py`, 105 passing tests, zero
   routes) and the router (`router/`, read-only) — it just isn't wired to the
   submit path.
3. **What "automatic" concretely means here:** a `price:` block in `flashml.yaml`
   (max price + objective: cheapest/balanced/fastest), and on submit the system
   creates the bid, matches against open listings at the hosts' asks, escrows,
   and lets the pull loop honor the entitlements. Supply gaps fall through to
   auto-acquired rented capacity within the user's budget (the `capacity/`
   library already exists, unwired). Every decision is explainable — a ranked
   candidate table plus a rejection reason for every machine that didn't make it.
4. **Location is the cheapest problem on the list.** Nobody at the marketplace
   layer does latency-based placement. Coarse geo (country) for data residency +
   measured per-node bandwidth is the entire v1 story; for distributed training,
   the literature solves cross-region with *island topology* (dense local sync,
   sparse global sync), and our pools already are proto-islands.
5. **Terac (terac.com) routes *people*, not GPUs — and still validates our
   design.** (Correction: the first draft said "Terac does not exist"; the
   owner meant the expert marketplace terac.com.) It matches 160k+ verified
   domain experts to work from research firms and frontier AI labs through a
   screening pipeline (identity → credentials → AI-interview assessment →
   ongoing performance scoring) and *delivers* matches — the demand side never
   browses. That is our trust-lifecycle + deliver-don't-browse thesis in a
   second domain. The one part not to copy: Terac must match on semantic/
   embedding representations because human expertise has no objective schema.
   Compute does — so our match path stays structured and deterministic (§3.9).

The recommended pipeline (§5):

```
flashml.yaml ──▶ compile ──▶ classify workload ──▶ eligibility gates (exist: 9)
                                                        │
              explain table ◀── rank by effective ◀── trust gate (new: lifecycle)
              (why not / why)    price = ask ÷        │
                                 reliability          ▼
                                              plan: cheapest │ balanced │ fastest
                                                        │ (exists, read-only)
                                                        ▼
                                       bid ──▶ match at host's ask ──▶ escrow
                                       (schema exists; route it)
                                                        │
                nodes keep pulling ◀── entitlements honored at claim (hook exists)
                supply gap? ──▶ auto-acquire rented venue within budget (library exists)
```

---

## 1. The question, precisely

Today a machine joins because its owner installs flashnode and (optionally) binds a
pool; a job runs on whatever eligible machine asks first (FIFO through 9 capability
gates). There is no price on either side of that encounter, no quality ranking, and
no way for a submitter to say "up to $2/hr, prefer cheap over fast."

The target state: **the job description is the entire user interface.** Model,
resources, target price, bid, and (optionally) location constraints go in
`flashml.yaml`; the platform decides which machines, at what price, with what
fallback. This aligns with the standing design rule: runtime behavior derives from
the user's submission — internal tables only *resolve* the user's choice, and every
knob has a yaml override.

---

## 2. Where FlashML already stands (so we design the delta, not the system)

From a full sweep of both repos (2026-08-13):

**Already built and live**
- Pull architecture: nodes claim work; the coordinator never pushes
  (`flashruntime/scheduler/__init__.py:628` — "FlashML is a PULL system").
- 9 fail-closed eligibility gates at claim: argv/module capability, local
  datasets, GPU count, exclude-list, pool, dependency install, dataset cache
  capacity, isolation tier (`scheduler/__init__.py:441-620`). Selection beyond
  gates is FIFO; an opt-in reliability policy re-orders tasks per claiming node
  (env `FLASHML_PLACEMENT_RELIABILITY=1`).
- Node inventory: cpu/mem/GPUs (name, VRAM, driver, compute capability), OS,
  arch, dataset cache, pools, 5 capability booleans — collected *now* explicitly
  so matching rules can arrive later (GpuInfo docstring, protocol §10.1).

**Already built, tested, and NOT wired**
- Marketplace engine + schema: `listings` / `bids` / `matches` /
  `price_observations` (migration `0018`), 8-rung capability-class ladder,
  escrow in millicredits, `effective_price = ask ÷ acceptance_rate`,
  `match_bid` greedy fill executing at the host's ask, unproven-host ¼ task
  share. 105 passing tests; **no bid or match HTTP route exists.**
- Router ("OpenRouter for compute", `flashml_cloud_api/router/`): workload
  classifier, venue table with measured facts, duration estimator with honest
  basis labels, `cheapest_plan` / `balanced_plan` / `fastest_plan` / `frontier`,
  read-only preview routes (`POST /v1alpha1/jobs/preview-plans`, `cost-quote`).
- Automatic acquisition: `capacity/acquire.py` rents one machine for one job
  (RunPod / Alibaba FC), with budget and reconcile/settle modules — called only
  from tests; the router's `rented` venue has no producer.
- Claim path already calls `marketplace.match_for_claim` + escrow hold
  (`app.py:9295-9320`) — the entitlement hook exists at the exact right place.

**Gaps a router must not pretend away**
- `gpuPerTask` never reaches the coordinator: the pinned runtime's
  `ResourcesSpec` predates the field, so pydantic silently drops it — a
  `gpus: 1` job compiles to a CPU spec today (`compile.py:608-624`). GPU-aware
  routing is fiction until a runtime release + 4-site pin bump.
- Two disagreeing GPU classifiers over the same jsonb column:
  `marketplace.capability_class` (smallest GPU wins) vs
  `router/estimator.hardware_class` (largest wins, different floors). Pick one.
- No price/bid/region field in `flashml.yaml`. No benchmark producers (the
  4-probe interface in `flashnode/benchmark/` is empty). No region, disk,
  bandwidth in `NodeCapabilities`. Reliability counters are split between
  coordinator memory (lost on deploy) and per-class acceptance rates.
- The parallel device-specs/provider-profile session is designing the schema
  for exactly these missing fields; its spec is the substrate this routing
  design consumes. Two flags already adopted there: durable per-machine
  reliability ledger (raw events, score derived at read time) and canonical GPU
  identity captured at registration.

---

## 3. What each marketplace does well — what we copy — how we improve it

Ordered by value to us, per the standing competitor-analysis rule.

### 3.1 Akash Network — the reverse auction, and the retreat from it

**How it works.** Tenant publishes a deployment (SDL) with escrow; each deployment
group becomes an on-chain order; every provider daemon independently decides to bid
(attribute match ∩ GPU spec match ∩ auditor-signature check ∩ inventory actually
reservable ∩ price under tenant max), prices via a local script, and posts a bid
with a 0.5 AKT deposit (max ~20 bids/order). The *tenant* then manually picks one
bid — sorted lowest-price-first in their console — and a lease forms. Payments
stream from escrow per block.

Their provider bid-price script is worth copying almost verbatim as a *shape*:
per-resource USD monthly targets (CPU $1.60/thread, RAM $0.80/GB, storage by
class) plus a hierarchical per-GPU-model price map
(`a100.80Gi.pcie=900 → a100.80Gi → a100 → default`), summed, converted, refused if
over the tenant's cap. Trust is a separate axis: providers self-declare arbitrary
attributes; *auditors* sign them on-chain; tenants require `signedBy` in the SDL.

**Where it failed — the most valuable findings in this whole report:**
- **Silent zero-bid failure.** Users see "available" GPUs, deploy, get zero bids,
  and no one tells them why (aggregate-vs-single-provider scarcity, per-node GPU
  fragmentation, CPU maxed while GPUs idle). This is Akash's #1 complaint.
- **Bid economics punish small providers.** Gas per bid/close against a 20-bid
  race made small hosts net-negative (one reported 5 AKT fees vs 1 AKT earned),
  centralizing supply toward big datacenters — the opposite of the mission.
- **Free-form attributes drift.** Self-declared key/values diverged enough that
  a JSON-Schema redesign is an open governance discussion.
- **Manual audits don't scale.** One of two auditors shut down; the roadmap
  concedes it and bets on TEE attestation (AEP-29).
- So in 2025–2026 Akash is building **AEP-67 bid screening**: an off-chain
  inventory service holding live gRPC state for every provider, a two-stage
  matcher (lossy SQL prefilter → strict bin-packer ported from the provider's Go
  code), and a UI that only shows providers that can actually bid — **with
  explicit rejection reasons** (capacity, GPU model, headroom, storage class,
  attributes, price).

**What we copy.** (a) The *declarative demand surface*: SDL's
attributes ∩ GPU spec ∩ trust requirement ∩ max price is exactly the shape of the
`flashml.yaml` extension in §5.1. (b) The bid-price script shape for host asks and
for our own rented-venue cost model. (c) Streaming escrow against a deposit
(we have escrow holds already). (d) **Rejection reasons as a first-class output**
of matching.

**How we improve.** We skip the decade of pain: no per-bid cost for hosts (a
listing is free and standing — decision M2 already says this), no tenant-side
manual bid picking (the router chooses; the user states an objective), and
matching runs centrally against live state from day one, which is precisely what
AEP-67 is retrofitting.

### 3.2 Vast.ai — quality-adjusted price as the ranking currency

**How it works.** Hosts set ask prices (GPU-hour, storage, and per-byte bandwidth
separately). Renters search a rich filter language (~50 fields: `gpu_name`,
`gpu_ram`, `reliability`, `dlperf`, `inet_down`, `geolocation`, …). Three defaults
do the real routing: `verified=true` (trust gate), `rentable=true`, and sort by a
composite "score" blending price and performance. **DLPerf** — Vast's measured
deep-learning benchmark — and **DLPerf-per-dollar** are the headline ranking
signals. `reliability` is a 0–1 score from uptime/interruption history (formula
private; community filters >0.95). Verification is an automated lifecycle:
unverified → verified (requires self-test, reliability ≥0.90, CUDA ≥12, network
speed scaled to VRAM) → deverified (hidden from search, auto-restored when fixed).
Interruptible instances are a continuous renter-vs-renter bid auction with pause
(not kill) semantics; on-demand always preempts interruptible. Their new
Serverless tier picks machines automatically by benchmarking candidate GPU classes
against the user's real traffic.

**What we copy.** (a) The trust-gate *lifecycle* (already adopted into the device
spec design). (b) Quality-adjusted price as the one ranking number — our
`effective_price = ask ÷ acceptance_rate` is already the same idea; when benchmark
probes land, extend the numerator's denominator: rank by
**measured-performance per credit**, the exact DLPerf-per-dollar move.
(c) Reliability as a derived 0–1 score over raw history. (d) Benchmark-driven
automatic placement (Serverless) as the north star for our estimator's
"measured > estimated > projected" basis ladder.

**How we improve.** Vast's renter still browses; their formula opacity is a
recurring complaint; and surprise bandwidth/storage bills are a top grievance. We
route by declared objective instead of browsing, we can afford to *show the math*
(the explain table, §5.5), and our pricing stays one number per hour with no
per-byte ambush.

### 3.3 SaladCloud — the honest playbook for volunteer hardware

**How it works.** Containers on ~60k gaming PCs. Salad does not pretend individual
nodes are reliable: interruptions are voluntary (owner takes the PC back),
external (power/network), or proactive (platform reallocates to higher priority).
The model: four **priority tiers** (High/Medium/Low/Batch — higher pays more and
preempts lower), **replica over-provisioning** (docs tell you to buy 5–10% extra),
per-node **trust ratings** gating workload eligibility, a `countries` allowlist as
the entire geo surface, and **in-container self-benchmarking** — the workload
tests its node at startup and exits (triggering reallocation) if it's inadequate.
Measured behavior: <4% interruption rate over multi-day windows, and interruption
frequency *falls* the longer a group runs.

**What we copy.** This is our closest cousin — fragmented volunteer devices — and
its answer is already half-built in FlashML: our two lease budgets
(`max_attempts` for real failures vs the more generous `max_expiries` for churn)
are Salad's over-provisioning in scheduler form. Copy deliberately:
(a) **over-entitlement** — grant matches for ~110% of `tasks_wanted` on volunteer
classes, refilled as matches expire; (b) the `countries:` allowlist shape for
data residency; (c) admission probes that run *on the node* and gate eligibility;
(d) priority tiers later, as the workspace-vs-market priority (M12) generalizes.

**How we improve.** Salad pushes churn cost onto the customer (you buy the extra
replicas; your container does the filtering). Our runtime absorbs churn natively —
leases requeue without burning failure budget, checkpoints move through the
coordinator — so we can quote *effective* prices that already include measured
churn (that's what dividing by acceptance rate does) instead of making users
discover it.

### 3.4 RunPod — the catalog cloud, and ordered fallback

**How it works.** No marketplace browsing at all: pick GPU class, tier
(Secure = vetted datacenters, Community = P2P hosts — now closed to new hosts),
region; one posted price per class per tier; RunPod allocates any matching
machine. Spot pods take a bid and die with 5s SIGTERM. The one routing idea worth
stealing is in Serverless: the user lists **GPU types in priority order** and the
platform takes the first available.

**What we copy.** (a) The catalog abstraction *is* our capability-class ladder —
users think in classes, not machines; keep that. (b) The **ordered fallback
list** as user-facing syntax for acceptable hardware (§5.1 `accept:`).
(c) A note of caution: RunPod shutting Community Cloud to new hosts, and
TensorDock's marketplace being absorbed into Voltage Park's first-party fleet,
both say open supply sides get culled when quality costs exceed their value —
the trust gate is existential, not cosmetic.

### 3.5 SkyPilot — the router as an optimizer with a printed table

**How it works** (open source; NSDI '23). A task declares resources and optional
constraints; a Catalog holds per-provider/per-zone instance prices; a Tracker
observes availability and preemptions. The optimizer enumerates every feasible
(cloud, region, zone, instance) candidate, scores by estimated cost or time **at
zone granularity** (same instance varies >40% across zones), includes data-egress
cost, **prints the ranked candidate table before provisioning**, then provisions
down the list — all zones in the chosen cloud, then the next-cheapest cloud —
until something sticks. Managed jobs auto-recover from spot preemption by
re-searching across regions/clouds with checkpoints in object storage.

**What we copy.** Our `router/plan.py` is already a small SkyPilot (candidates →
water-fill → cheapest/balanced/fastest/frontier). Adopt: (a) the **printed
ranked table** as the explainability contract for every routing decision;
(b) the **provision-down-the-list** loop for the rented venue (try RunPod SKU in
region A, then B, then Alibaba FC) instead of fail-on-first-miss; (c) preemption
recovery as re-planning, which our lease sweep already gives us for free.

### 3.6 io.net — verification cadence and connectivity tiers

**How it works.** Cluster builder filtered by country list (stated reasons:
latency + data residency), named bandwidth tiers ("Ultra High Speed: 1600 MB/s
down / 1200 up"), and GPU model; io.net itself places the master node on vetted
infrastructure. Hardware is re-verified **continuously**: an hourly proof-of-work
challenge catches spoofed/virtualized GPUs, a proof-of-time-lock catches
double-renting, block-reward eligibility requires green uptime over the trailing
5 hours, and failures land in a public per-device log with explicit reasons.
Suppliers stake per card; a reliability score exists but its formula is
unpublished.

**What we copy.** (a) **Continuous re-verification with a public failure-reason
log** — verification is a heartbeat-adjacent process, not an enrollment event.
(b) Connectivity *tiers derived from measured bandwidth* as the user-facing
vocabulary. (c) Two-sided incentive separation: being verified-and-available is
worth something distinct from being hired (maps to future host-side credit
accrual for standing listings — no per-job cost, unlike Akash bids).

**How we improve.** Skip staking/slashing entirely (we're not permissionless-
adversarial at this scale; the trusted tier + sandbox already segments risk), and
publish the reliability derivation — opacity is a complaint everywhere it appears.

### 3.7 Prime Intellect — matching for *distributed training* specifically

**How it works.** Marketplace: normalizes offers from ~12+ clouds into standard
per-chip pricing (admitting cross-provider reliability varies "up to 100x").
Training: the unit of scheduling is an **island** — one well-connected 8×H100
node/datacenter — with dense FSDP inside and sparse DiLoCo sync between
(500× less communication; cross-continent runs at 90–95% utilization over
127–935 Mbit/s links). INTELLECT-1's ElasticDeviceMesh handles churn with
heartbeats + a "deathrattle" (dying nodes fail fast), and joiners fetch live
checkpoints from peers and enter at the next outer step contributing zero — no
stalls. INTELLECT-2 makes heterogeneity harmless via *asynchrony*: rollout
workers run at their own pace on slightly stale policies (validated to 4 steps
stale), and TOPLOC verification checks contributed work at ~1% of the cost of
generating it.

**What we copy.** The **island principle settles our location question**: don't
match individual far-flung devices into one tightly-coupled job; match
*well-connected groups* (for us: pools, and later auto-formed same-region
cohorts) and let the runtime's sync topology tolerate the gaps between groups.
Fail-fast membership and stall-free joining are runtime features we largely have
(lease expiry + coordinator-relayed checkpoints); the deathrattle (agent
actively signals death when it can) is a cheap flashnode addition.

### 3.8 SF Compute — the order book (file under "later")

**How it works.** A genuine two-sided order book for *blocks* of compute
(quantity × duration × start time × limit price, per hardware SKU); buys fill
only when crossing sells; the exchange can combine orders to clear; unused time
is resellable for credits. It solves *forward* allocation — reserving 64 GPUs
for next Tuesday — which posted-price marketplaces handle badly.

**What we copy — later.** Nothing in v1: order books need liquidity we don't
have, and decision M5 (no timer-driven price drift) plus M3 (bid/ask book,
execute at ask) already capture the useful half. When reservations become a real
demand ("I need the fleet Saturday"), SF Compute's block-with-resale model is
the design to lift; `available_from/until` on listings is the seam it plugs into.

### 3.9 Terac — and the other quick hits

- **Terac (terac.com)** — the owner's actual reference; the research agent
  found the company but wrongly dismissed it as out-of-domain. It is not
  compute: a two-sided **expert marketplace** (SF, founded 2025, $9M from
  Emergence / SignalFire / SV Angel) matching 160k+ verified domain experts
  across 115+ countries to paid work from market-research firms and frontier
  AI labs (RL post-training, evals, red-teaming). Their own tagline: **"The
  Efficient Resource Allocation Company."** The matching model:
  - *Supply admission is a pipeline, not a form:* identity attestation →
    credential verification → an open-ended **AI interview** probing domain
    knowledge → behavioral analysis → **ongoing performance scoring**. This
    maps 1:1 onto our trust lifecycle: enrollment → canonical GPU identity →
    benchmark probes → the reliability ledger. Their AI interview is the
    human-domain analog of our hardware probes: automated assessment at the
    door, continuous scoring after it.
  - *Deliver, don't browse:* "we connect you with projects that match your
    background" — a demand-side client never pages through 160k profiles.
    Second-domain validation of the router-delivers thesis.
  - *The caution (raised by the owner):* Terac's matching necessarily leans on
    semantic/embedding-style representations of jobs and skills — human
    expertise has no objective schema, so match quality depends on how well
    the embedding context captures both sides, and it degrades fuzzily.
    **Compute routing does not have this problem and must not import it:**
    machine capability is enumerable (VRAM, cores, bandwidth) and measurable
    (probes, acceptance history), so the match path stays structured,
    deterministic, and explainable — no embeddings anywhere in gating or
    ranking. If the platform ever adds human-data/labelling work as a job
    type, the same discipline applies: embeddings at most for candidate
    *retrieval*, with hard verified credentials and measured performance
    scores doing the actual gate and rank.
- *Name-collision notes:* **Teraco** is a South African colocation operator
  (Digital Realty) — racks, not a marketplace. The Akash-adjacent startups a
  compute reading of the name suggested:
  - **Theta EdgeCloud** — the most relevant: hybrid datacenter + community GPU
    marketplace; operators set hourly rates; the scheduler "weighs cost,
    latency, and capacity, then dispatches workloads," migrating tasks off
    flaky community rigs. That is our target shape stated in one sentence.
  - **Clore.ai** — P2P GPU rental; fixed-price on-demand + spot auction with
    host floor prices; idle hardware falls back to mining.
  - **Aethir** — enterprise DePIN aggregating owned/partner fleets for AI and
    cloud gaming.
- **Nosana** — per-GPU-model on-chain markets with FIFO queues (matching is
  queue position, not bidding — structurally FlashML's current claim loop with
  a price attached). Their stated lesson after three test phases: *real
  workloads exposed behavior "benchmarks alone couldn't provide."* Keep our
  canary/real-job feedback loop over synthetic scores.
- **Golem** — the oldest design: symmetric offer/demand property matching with
  an LDAP-like constraint language and bilateral negotiation. Elegant and
  cautionary: maximal generality, no default trust layer, minimal adoption.
  Constraint languages are a liability at our stage; a fixed schema wins.
- **Render** — OctaneBench score is the primary allocator, reputation breaks
  ties, tiers set price multipliers. Confirms benchmark-primary allocation for
  embarrassingly parallel work (our HPO sweeps qualify).
- **Gensyn** — verification-first litepaper (probabilistic proof-of-learning,
  pinpoint disputes, staking games); most of it never operated in production.
  Confirms M14 (defer verification) was right, and TOPLOC-style cheap
  verification is the eventual pattern, not proof-of-work-everything.

---

## 4. Design principles the evidence supports

1. **Centralized pre-matching with live state; never an open auction.** (Akash's
   retreat; AEP-67.) Matching runs against the machine table we already hold.
2. **A match is an entitlement, not an assignment.** (Marketplace design §0.1;
   Salad's reallocation reality.) The pull loop stays; routing only decides who
   *may* claim and at what price.
3. **Trust-gate default-on, as an automated lifecycle** (unverified → verified →
   deverified, auto-restore, reasons logged publicly). (Vast, Salad, RunPod,
   io.net unanimously.)
4. **One ranking number: quality-adjusted price.** `ask ÷ reliability` now
   (shipped as `effective_price`), `ask ÷ measured-perf-per-class` when probes
   land. (Vast's DLPerf-per-dollar; Render's benchmark-primary.)
5. **Store raw events; derive scores at read time.** Formulas must be able to
   evolve without migrations — and unlike everyone else, publish them.
6. **Structured attributes only.** (Akash's free-form drift; Golem's constraint
   language.) The capability-class ladder + typed GPU identity is the schema.
7. **Coarse geo + measured bandwidth; islands for training.** Country allowlist
   for residency (Salad), bandwidth tiers from measurement (io.net), island
   topology for cross-region training (Prime Intellect). No latency matrices.
   FlashML-specific: traffic flows through the coordinator relay, so
   node↔coordinator bandwidth is the number that matters.
8. **The user declares intent in `flashml.yaml`; defaults route.** Max price +
   objective + optional hardware/geo lists. Ordered fallback (RunPod) and
   `any_of` (SkyPilot) prove users think in acceptable-sets, not machines.
   (Owner's standing rule: submission drives knobs; tables only resolve.)
9. **Hosting must cost nothing per job opportunity.** (Akash bid economics
   centralizing supply.) Standing listings, no deposits, no per-match action.
10. **Every routing decision is explainable.** The SkyPilot printed table +
    AEP-67 rejection reasons: for each candidate, show the rank math; for each
    excluded machine, name the gate that excluded it. This kills the
    zero-bid-mystery failure mode before we ever have it.

---

## 5. Recommended architecture

### 5.1 The user surface (`flashml.yaml`)

All new keys optional; absent keys mean "workspace machines only, free" —
exactly today's behavior, so nothing breaks.

```yaml
version: 2
name: sentiment-sweep
image: pytorch-2.4
entrypoint: train.py
resources:
  gpus: 1
  memory_gb: 16

price:                    # NEW — presence opts the job into the market
  max_per_hour: 2.00      # hard cap per machine-hour (ZC; 1 ZC = 1 USD parity)
  objective: balanced     # cheapest | balanced | fastest  (default: balanced)
  budget: 25.00           # optional total cap across the whole job
  rented: allow           # allow | never  — may the router acquire cloud
                          #   capacity within the caps when the market can't
                          #   clear? (default: never)

placement:                # NEW — all optional
  accept:                 # ordered acceptable hardware classes (RunPod-style
    - gpu-24gb            #   fallback; omit = router picks from resources)
    - gpu-16gb
  countries: [US, CA, DE] # residency allowlist (Salad-style; omit = anywhere)
  pool: team-lab-3        # existing pool binding, unchanged
```

Resolution order per the standing rule: explicit yaml > derived from the user's
choices (e.g. `resources.gpus` + model size → minimum class) > default. The
three objectives map 1:1 onto the already-built `cheapest_plan` /
`balanced_plan` / `fastest_plan`.

### 5.2 The pipeline

1. **Submit & classify** — compile as today; `router/workload.py` classifies
   (hpo / training / federated / …) with evidence strings.
2. **Gates** — the 9 claim gates, applied ahead of time over the machine table
   via the one-door `placement_predicate()` (exists). Output: eligible set +
   *per-machine exclusion reason* for the explain table.
3. **Trust gate** — machine must be in `verified` state (device-spec session's
   lifecycle). Until verification ships, the existing unproven-host ¼-share cap
   is the interim gate (M14 honored).
4. **Rank** — `effective_price = ask ÷ acceptance_rate` (exists), upgraded to
   `ask ÷ perf-per-class` when benchmark probes report. Donated (ask 0)
   machines rank first at their reliability. Tie: ask, then listing id (exists).
5. **Plan** — the objective picks the plan; `frontier` powers the console
   comparison view. Water-fill respects `max_concurrent_tasks` (exists).
6. **Bid & match** — the plan is executed as a bid
   (`max_per_hour × est_task_seconds × tasks_wanted` sizes the escrow), matched
   greedily against open listings **at each host's ask** (M3; Akash-style
   execute-at-ask, never a clearing price). Partial fills stay open and refill
   as listings appear (schema already models `partial`).
   **Over-entitle volunteer classes ~110%** of `tasks_wanted` (Salad).
7. **Claim** — unchanged pull loop; `match_for_claim` + escrow hold at the
   claim site (already wired at `app.py:9295`). No match, no priced work — the
   workspace venue still serves free jobs.
8. **Churn** — lease expiry requeues without burning failure budget (exists);
   expired matches refund escrow and reopen the bid remainder; re-planning is
   just re-matching, per SkyPilot's recovery loop.
9. **Supply gap** — if the bid can't fill and `price.rented: allow`, the router
   walks the rented-venue candidate list SkyPilot-style (SKU → region → next
   provider) via `capacity/acquire_for_job`, strictly inside `max_per_hour` and
   `budget`. The rented machine is just another listing owned by the platform.

### 5.3 Location, concretely

- **v1 (ship with routing):** `countries` allowlist filter; per-node measured
  `net_up/net_down` (probes exist as names) folded into verification thresholds
  (Vast scales required bandwidth with VRAM — copy that curve); prefer
  higher-bandwidth-to-coordinator nodes for checkpoint-heavy workload kinds.
  One cheap scalar: RTT-to-coordinator measured at heartbeat.
- **v2 (federated/distributed jobs):** islands. A pool is an island; later,
  auto-form cohorts by (country, measured bandwidth tier). The matcher places
  *island-shaped* requirements ("8 tasks in one cohort") rather than scattering
  tightly-coupled work — the DiLoCo/INTELLECT evidence says sparse-sync between
  islands is the runtime's job, not the matcher's.

### 5.4 Explainability surface (the anti-Akash feature)

Every submit (and the read-only preview, which exists) returns:
- the ranked candidate table: machine class, ask, reliability, effective price,
  tasks assigned — the SkyPilot table;
- for every excluded machine: one line, one reason (`gate:pool`,
  `gate:datasets-capacity`, `trust:unverified`, `price:ask>max`, …);
- when nothing matches: the *nearest miss* ("2 machines match at $2.40/hr; your
  cap is $2.00" / "raising budget by $3 fills the job") — turning Akash's
  zero-bid mystery into a one-line fix.

### 5.5 What routing does NOT include (deliberate)

- No open/on-chain auction, no per-bid host costs, no staking/slashing.
- No clearing-price mechanism (M5: no timer drift; M3: execute at ask).
- No latency matrices or interconnect-aware single-job striping.
- No order book / forward reservations until demand proves out (§3.8).
- No third GPU classifier — unification is a prerequisite, not a variant.

---

## 6. Build order

Phases gate on the device-spec/provider-profile spec (parallel session) for new
node fields; each phase is independently shippable and testable through the
authoring surface (submit real `flashml.yaml`, per the standing
verify-through-the-authoring-surface rule).

**Phase 0 — prerequisites (unblocks everything)**
- Fix the `gpuPerTask` pin gap (runtime release + 4-site bump — release-gated).
- Unify the two GPU classifiers into one shared module; both call sites import it.
- Land the device-profile schema (peer spec): canonical GPU identity, trust
  state, reliability event ledger, benchmark slots, region, net_up/net_down.

**Phase 1 — the market goes live for jobs (MK-1 + routing write path)**
- HTTP surface for bids/matches over the tested engine (the "105 passing tests,
  zero routes" gap named in the next-phases doc).
- `price:` + `placement:` blocks in `flashml.yaml`; submit-time plan → bid →
  match → escrow; claim-side entitlement already wired.
- The explain table on preview and submit.
- Result: target price + bid routing works end to end on workspace + market
  venues, at asks, with FIFO replaced by ranked matching for priced jobs.

**Phase 2 — quality-aware routing**
- Benchmark probe producers in flashnode (4 named probes) + heartbeat RTT.
- Trust lifecycle transitions automated; verified becomes the default gate;
  deverified hides from matching with public reasons (io.net-style log).
- Effective price upgrades to measured perf-per-credit; reliability derived
  from the durable ledger replaces coordinator-memory counters.

**Phase 3 — automatic acquisition (the "never think about machines" phase)**
- Wire `capacity/acquire_for_job` as the rented-venue producer behind
  `price.rented: allow`, with SkyPilot-style fallback walking and hard budget
  caps. (Rehearse locally; the $10 rented-GPU ceiling applies to testing.)
- Auto-release on job completion via the existing reconcile/settle modules.

**Phase 4 — placement sophistication (evidence-gated)**
- Island-shaped placement for federated jobs (pool cohorts → auto cohorts).
- Priority tiers if market volume shows contention (Salad model generalizing
  M12's workspace-first rule).
- Reservations/order book only if users ask for forward capacity (SF Compute).

---

## 7. Open questions for the owner

1. **Objective default:** `balanced` as the default plan (the marketplace design
   argued Balanced wins; Vast defaults to a blended score) — confirm.
2. **`rented: allow` default:** proposed `never` (no surprise spend). An
   account-level override could let teams default it on. Confirm.
3. **Over-entitlement factor** for volunteer classes (proposed 110%): tune from
   measured churn once the reliability ledger accrues, or fix it now?
4. **Publish the reliability formula?** Recommendation: yes (differentiates from
   every competitor's opacity complaints); the anti-gaming risk is low while
   supply is invite-only pools.
5. **Does `price.max_per_hour` bound the *rented* venue too** (one cap for both
   markets), or should rented capacity get its own cap? Proposed: one cap —
   simpler mental model, matches "the yaml is the interface."

---

## 8. Addendum (2026-08-13, same day): two facts that adjust the picture

Learned after the research above was written, from parallel workstreams:

- **The console now lives on `zolliai.com`** (verified: `zolliai.com/activate`
  and `flashml-web.onrender.com/activate` serve the identical "Zolli Cloud"
  app). The domain-conflict note that placed a different product there is
  stale. Routing consequence: none to the design; supply-side onboarding docs
  and the activation/trust-lifecycle flows in §5 should name `zolliai.com` as
  the canonical activation surface, with `?pool=` at activation remaining the
  moment island/pool membership binds.
- **An E2B sandbox venue is staged for the prod API** (`E2B_API_KEY` +
  `E2B_REGION=ap-southeast-1`, pending credential mint). Routing consequences:
  (a) the router's venue table gains an *ephemeral sandboxed* venue —
  fast-start CPU microVM capacity that fits the `command` / `evaluation` /
  light-`hpo` workload kinds, slotting into the §5.2 step-9 waterfall ahead of
  heavier rented GPUs, priced like any platform-owned listing; (b) it is the
  first non-US point of presence (Singapore), which gives the §5.3 `countries`
  filter its first real second region and a concrete reason to record
  per-venue region from day one. This venue should appear in
  `router/venues.py` with a measured-facts row like the existing five, not a
  guessed one.

## 9. Sources

Primary documentation and code, gathered 2026-08-13 by four research agents;
full URL trails preserved in the session transcripts.

- **Akash:** docs.akash.network (SDL, providers & leases, console), GitHub
  akash-network/{node,provider,community} (bidengine/order.go, params,
  audit attributes), AEP-67 / AEP-29 / AEP-76 roadmap pages, governance
  discussions #434/#621/#1463, support#139/#180, Messari State of Akash Q2/Q4
  2025.
- **Vast.ai:** docs.vast.ai (pricing, rental types, search offers CLI/API,
  verification stages, serverless), vast.ai FAQ (DLPerf).
- **SaladCloud:** docs.salad.com (priority pricing, billing, service
  performance, long-running tasks, container groups API), salad.com/security,
  blog.salad.com benchmarks.
- **RunPod:** docs.runpod.io (choose-a-pod, pricing, serverless endpoints,
  worker affinity), runpodctl repo.
- **SkyPilot:** NSDI '23 paper, docs.skypilot.ai (auto-failover, managed jobs),
  release blogs 0.3/0.6.
- **io.net:** io.net/docs (clouds, proof-of-work, staking, block rewards,
  device reliability, network architecture).
- **Prime Intellect:** primeintellect.ai blog (compute exchange, OpenDiLoCo,
  INTELLECT-1), INTELLECT-2 paper (arXiv 2505.07291), protocol repo (archived
  2026-01-27).
- **SF Compute:** docs.sfcompute.com (how the market works); CNBC on compute
  futures; OneChronos/Auctionomics combinatorial auction launch.
- **Nosana:** learn.nosana.com (jobs program, markets, staking), retrospective
  blog. **Golem:** golem docs (demand/offer spec, requestor-provider
  interaction), yagna repo. **Render:** know.rendernetwork.com (tier pricing);
  secondary: Messari/Gemini/CoinGecko on allocation. **Gensyn:** docs.gensyn.ai
  (litepaper — marked legacy; testnet, RL Swarm).
- **Terac:** terac.com (home, /about — "The Efficient Resource Allocation
  Company", /ai, /platform, /researchers, /opportunities), Tracxn company
  profile (funding: $9M — Emergence, SignalFire, Audacious, SV Angel,
  Z Fellows). Name-collision checks: Teraco (Digital Realty colocation),
  Theta EdgeCloud docs + launch posts, Clore.ai docs, Aethir ecosystem blog,
  Shadeform docs, NVIDIA Brev docs, dstack docs, Lium/Bittensor SN51.
- **Internal:** repo sweep of flashml-cloud + flashml (scheduler, leases,
  marketplace, router, capacity, migrations 0018/0019, market design docs of
  2026-08-11/12/13, HANDBOOK research register R9).
