# Positioning note — what FlashML aggregates, and whose machines

**Date:** 2026-08-02
**Status:** analysis. Not a spec. Written because it contradicts the ordering
in `2026-08-01-foundation-design.md` §2, and that contradiction should be on
paper rather than discovered in three weeks.
**Companion:** `2026-08-02-colab-gpu-pooling-strategy-note.md`, which reaches
the same conclusion from a different direction.

---

## 1. The framing

Owner's words: *"we are more like an open router for GPU — two main computing
resources, hosting by anybody like on a marketplace, or the big providers, or
in future data centers at home instead of small laptops."*

That is a sharper description of the product than "open volunteer network",
and this note takes it seriously: where it holds, where it does not, and what
it implies for what gets built next.

## 2. Where the OpenRouter analogy holds

OpenRouter's value is not the models. It is being the layer that makes many
suppliers interchangeable: one interface, automatic failover, price arbitrage,
one bill. The equivalent for training compute is a real position.

And FlashML has something most compute aggregators do not: **fault tolerance
as a first-class property rather than an operational concern.** The lease
state machine, expiry sweep, idempotent commit, and checkpoint catalog all
assume a machine disappears mid-task. Most schedulers assume it does not, and
bolt on retries afterwards.

That matters because **the unreliability that makes cheap supply cheap is
exactly the thing the runtime is built for**: spot instances that get
reclaimed, preempted pods, a gaming rig whose owner starts a game, a laptop
whose lid closes. Cheap supply and unreliable supply are the same supply.

This is the strongest thing in the codebase and it is a genuine wedge.

## 3. Where the analogy breaks, and it matters

**OpenRouter routes stateless requests.** An inference call is seconds long,
carries its own input, and is trivially fungible across providers.

**FlashML routes stateful jobs.** A training job runs for hours, carries
weights between rounds, and needs its checkpoints where it resumes.

The binding constraint is therefore not routing — it is **bandwidth**. Every
federated round ships model weights to each participant and deltas back. On
home internet, with a model of any size, the network dominates and adding
machines makes the job *slower*, not faster.

The M1 spec already says this plainly, and it should keep being said:

> "Item 4 proves *collaborative* training, not that it is faster than one
> machine — over home links with small models it will usually be slower than
> local training."

So the defensible positioning is narrower than "any GPU workload anywhere":

> **An aggregation layer for fault-tolerant, shardable training** —
> hyperparameter sweeps, federated rounds, embarrassingly-parallel work.

Not "run your 70B fine-tune across strangers' machines". That claim would not
survive contact with a first customer, and making it would burn the credibility
the fault-tolerance story earns.

## 4. Three supply tiers, honestly rated

| Tier | Reliability | Cost to us | Support burden | Value per machine | Available |
|---|---|---|---|---|---|
| **Rented providers** (RunPod, Lambda, cloud spot) | high | money | **none** — we control the box | high | **today** |
| **Home rigs** (4090s, ex-mining, enthusiast desktops) | medium | cheap | medium | high | needs GPU support |
| **Volunteer laptops** | low | free | **high** | **very low** | today |

### 4.1 The laptop tier is the one we built, and it is worth the least

Evidence from the 2026-08-02 acceptance run, not speculation: a MacBook Air
ran **all three shards of every round** and a toy MLP still took 101 seconds
across five rounds. A laptop CPU contributes close to nothing to real training.

Meanwhile the support cost is the highest of the three. That single run
produced two unrelated Docker failures on two machines — a missing
`docker-credential-desktop` on macOS, an engine `_ping` 500 on Windows —
neither of which means anything to a non-expert, and both of which landed on
us to diagnose.

**High support cost, near-zero compute value.** That is the worst cell in the
table, and it is the tier the current plan optimises hardest for.

### 4.2 Rented providers work today and need almost nothing

A rented Pod runs `pip install flashnode` and starts claiming work. No ToS
problem — renting compute and using it for compute is the product being sold.
No install funnel, no host support, no trust problem, no payouts.

The one constraint: a RunPod-style Pod is itself a container and cannot nest
Docker, so the runner drops to `subprocess` with no sandbox. That is
acceptable **because we rented the machine** — the sandbox exists to protect a
host from a submitter, and when those are the same party the threat mostly
disappears. For full sandboxing, rent a VM or bare metal instead of a
container, where the driver, NVIDIA Container Toolkit and `--gpus` all work
normally.

This is also the **"rent your own fleet"** model, which sidesteps the two
hardest business problems at once: no payouts to individuals (no identity
verification, tax forms, fraud, chargebacks), and no host support burden.

### 4.3 Home rigs are the interesting middle

Someone running a 4090 in a spare room has a real GPU, already has drivers,
probably already has Docker, and is not intimidated by a terminal. High value
per machine, moderate reliability, and — crucially — **they do not need an
installer.** They need their GPU to be detected and matched to GPU work.

## 5. What this changes about the roadmap

The current program (`2026-08-01-foundation-design.md` §2) orders S4 — the
signed desktop app with a bundled sandbox VM — as the largest and most
important remaining build. Its entire justification is removing install
friction for **laptop volunteers**.

If tiers 1 and 2 are where the compute actually is, that justification does not
hold:

| | Current program | If supply is providers + home rigs |
|---|---|---|
| Desktop app + bundled VM (S4) | largest item, the growth unlock | **not needed by either tier** |
| GPU detection + `--gpus` (D9) | deferred to M1.5 | **prerequisite** |
| Capability-aware placement (M2) | after M1 | **prerequisite** — a GPU job must not land on a laptop |
| Result verification (S5) | before public launch | still required for tier 2, not tier 1 |
| Contributions ledger | credit for hosts | required for tier 2, useful for all |

**The largest planned item becomes the least urgent, and the two deferred
items become the gate.** That is not a small reordering and it should be a
conscious decision rather than a drift.

### 5.1 Present state of the deferred work

- `NodeCapabilities.gpus` is `list[dict]` and **always empty** —
  `flashnode/inventory/capabilities.py` has no GPU probe at all.
- The Docker runner passes seven hardening flags and **no `--gpus`**
  (`flashnode/executor/hardening.py`) — verified 2026-08-02.
- No CUDA image exists in the curated set.
- `IsolationAwarePlacement` gates on `sandbox_capable`/`argv_capable` and
  **reads no capabilities at all** — a 4-core laptop and a 64-core workstation
  are indistinguishable to it.

So "support GPUs" is four changes, not one.

### 5.2 And it is now testable

Earlier advice was to defer GPU work because none of it could be verified
without hardware. Renting removes that objection: `flashruntime`'s own records
show a RunPod validation run on 2×RTX 4090 costing **$0.0725**. GPU support can
be built and proven for under a dollar, on a machine nobody has to own.

## 6. Competitive reality

This space is not empty, and the differentiator has to be sharper than
"aggregation":

- Consumer-GPU marketplaces exist (Vast.ai, Salad and similar).
- Multi-cloud routing exists (SkyPilot and similar).
- Decentralised-training efforts exist (Prime Intellect and similar).

*(Names from general knowledge, not researched for this note — verify current
positioning before relying on any of it.)*

What is plausibly ours: **fault tolerance as the design centre, plus a
federated path for data that cannot be pooled.** Aggregation alone is a
feature others already have. Surviving a supply base that constantly
disappears, and serving groups who cannot move their data, is a narrower and
more defensible claim.

## 7. What to verify before committing

1. **Ask one real user** which tier they would pay for. Nothing in this note is
   demand evidence; it is all supply reasoning.
2. **Measure the bandwidth wall.** Run the federated example with a
   realistically sized model over real home links and find where more machines
   stop helping. That number decides whether the product is "sweeps and
   federated rounds" or something broader.
3. **Price against the alternative.** If a lab's real need is one A100 for six
   hours, renting one may beat pooling twelve small GPUs on every axis. Know
   where the crossover is.
4. **Decide the tier order deliberately** — and if it is providers first,
   reopen the S4 priority in the foundation spec rather than leaving two
   documents disagreeing.

## 8. What does not change

The repo topology, release pipeline, and pinned-version discipline from S1 are
orthogonal to all of this and remain correct. So does the diskless control
plane (B2): every tier above makes deploys-that-drop-leases worse, not better.
