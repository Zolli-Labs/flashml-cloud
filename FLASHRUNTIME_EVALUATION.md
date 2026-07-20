# FlashRuntime: Critical Evaluation and Technical Architecture

Role: principal-engineer review of the "distributed workload compiler and
reliability runtime" hypothesis. Date: 2026-07-19. Sources verified against
current documentation where volatile (see Appendix: API Stability Register).
Assumptions and uncertainties are labeled inline as **[assumption]** /
**[uncertain]**.

---

## Executive verdict (read this even if you skip the rest)

**You are proposing two products under one name.**

1. A **reliability runtime**: job/task/attempt/lease state, checkpoint
   catalog, failure taxonomy, recovery orchestration, one execution contract
   across backends. This is technically sound, buildable incrementally,
   genuinely under-served, and consistent with your master strategy report.
   **Build this as the spine.**

2. A **workload compiler**: automatic strategy selection from constraints
   via memory/communication/cost models. This is a research-grade problem
   with a graveyard of prior art — Alpa (the best-known auto-parallelization
   system, OSDI '22) was archived in October 2024; DeepSpeed's Autotuner
   exists but is profiling-based, DeepSpeed-only, and cannot even tune
   offloading. Full generality here is **not achievable by a small team and
   should not be promised**.

The resolution is not to drop the planner — it is to **downgrade its claim
and invert its dependency**. Ship the planner as a *deterministic,
explainable feasibility filter over a curated strategy menu* ("plan
explainer"), not an optimizing compiler. And make it a *consumer* of the
reliability runtime's event ledger: every executed job produces measured
memory, throughput, checkpoint, and failure data that makes the next plan
estimate better. The runtime is the moat and the planner's training set;
the planner is the demo magnet and the on-ramp. In that order.

One more strategic observation: a `flash plan` command that takes a model +
GPUs + budget and prints *"DDP won't fit (needs 41 GB/GPU, you have 24);
QLoRA+DDP fits with 18% margin; ZeRO-3 offload fits but misses your 3-hour
deadline"* — with the arithmetic shown — is useful **with zero cluster
attached**. Nothing popular does this today (`accelerate estimate-memory`
gives a single static table with no constraints, no objectives, no
topology). That is your lowest-cost wedge and it is pure Mode-0 software.

---

## A. Product and abstraction

### A1. Is "distributed workload compiler and reliability runtime" the right category?

Half of it. "Reliability runtime" is correct and matches what the code
already started becoming (leases, events, checkpoint manifests, KubeRay
observation). "Compiler" is the wrong public claim for v1: a compiler
implies completeness and correctness over an input language; your input
language (arbitrary PyTorch programs × hardware × networks) is not closed,
and your optimization objective depends on measurements you don't have on
first contact with a workload. Call the planner what it will actually be
for the first two years: a **strategy planner** — deterministic candidate
generation, feasibility elimination, estimated comparison, explanation.
"Compiler" can become true later for the narrow workload classes where you
have enough measured data. Marketing a compiler and shipping a heuristic
table is how projects lose trust.

### A2. Is the abstraction too broad?

As specified in your hypothesis — yes, on three axes:

- **Workload breadth**: RL, MoE, pipeline multi-stage, pretraining are each
  their own engineering domain. §B marks what belongs in the MVP.
- **Estimator depth**: activation memory, fragmentation, and kernel
  workspace are not statically predictable to better than tens of percent
  (§D). A planner that pretends otherwise OOMs user jobs and dies by
  reputation.
- **Provider breadth**: every provider adapter is a permanent support cost.
  The master report's own guardrail applies: one adapter first.

The abstraction is *not* too broad in one important way: the **StrategyPlan
contract** (§L) can and should be designed now to cover the full space,
with most strategy families marked unimplemented. A schema that anticipates
tensor parallelism costs nothing; an implementation does.

### A3. What FlashRuntime should own

1. The **versioned public contract**: JobSpec, WorkloadSpec, StrategyPlan,
   Event, CheckpointManifest, TaskAttempt, Lease. (This is the existing
   `protocol/v1alpha1` grown, not a new thing.)
2. **Workload classification** (§B) and **candidate generation/selection**
   (§C) producing a backend-neutral StrategyPlan.
3. **Execution state**: jobs, attempts, tasks, leases, heartbeats, events —
   the append-only ledger as the single source of truth.
4. **Checkpoint catalog**: manifests, validity, compatibility, selection at
   recovery time (§H). Never the serialization itself.
5. **Failure taxonomy and recovery policy**: typed, deterministic,
   logged decisions (§G).
6. **Adapter interfaces** for launchers, providers, checkpoint backends,
   and the conformance tests that keep third-party adapters honest.
7. **The explanation**: every plan and every recovery action must be able
   to say why, from recorded inputs. This is a first-class output, not a
   log line.

### A4. What FlashRuntime must explicitly not own

- Training loops, autograd, optimizers, collective algorithms (NCCL et al.).
- Tensor serialization formats (DCP's, DeepSpeed's, safetensors are fine).
- Cluster provisioning and node lifecycle (SkyPilot / K8s / Slurm / provider
  APIs own machines; FlashRuntime requests and observes).
- Gang scheduling and queueing inside clusters (Kueue/Slurm).
- Model architecture knowledge beyond what metadata + profiling reveal.
  Do not build per-model config databases you must maintain forever —
  derive from `config.json` + measurement, cache learned results.
- Sandboxing/isolation (FlashNode's job) and anything commercial
  (FlashML Cloud's job).

### A5. How it differs from the neighbors

| System | What it actually is | Why it doesn't already do FlashRuntime's job |
|---|---|---|
| **Ray** | Distributed execution substrate (tasks/actors/data) + libraries on top | Executes what you tell it, within one Ray cluster. No cross-framework strategy planning; Train V2 handles torch process groups but you pick the strategy. Ray is a *backend* for Mode A and an optional one for Mode B. |
| **HF Accelerate** | Unifies single-process training code across devices/strategies; config-driven launch | User (or its config wizard) still chooses DDP/FSDP/DeepSpeed. No feasibility math, no state, no recovery orchestration, no cost/deadline objectives. |
| **Lightning** | Training-loop framework with pluggable strategies | Same: strategy is a user choice (`strategy="fsdp"`); fault tolerance is basic restart. Also owns the user's code structure, which FlashRuntime must not. |
| **SkyPilot** | Multi-cloud provisioning + managed job retries | Recovers *infrastructure*, is application-state blind: a recovered job restarts from scratch unless the app checkpoints itself. No workload model. Ideal *provider layer* under FlashRuntime. |
| **Kubeflow** | K8s-native ML platform (operators, pipelines) | Runs what the manifest says on K8s only. No planning, no cross-environment contract. |
| **DeepSpeed** | Training engine (ZeRO, offload) + its Autotuner | Autotuner is real prior art but: profiles only DeepSpeed configs, doesn't support tuning offloading, no cost/deadline objectives, no reliability dimension. A *strategy library* under FlashRuntime, and a validation baseline for your estimator. |
| **PyTorch Elastic / torchrun** | Process launcher with rendezvous + whole-group restart on membership change | The *mechanism* FlashRuntime's Mode B recovery drives — it decides nothing about strategy or checkpoints (app must load its own checkpoint after restart). |
| **Research autotuners** (Alpa, FlexFlow, Galvatron, Metis) | Automatic parallelism search | Alpa archived Oct 2024; the others are papers/prototypes tied to specific stacks. Evidence the full problem is hard, and that nobody owns the *productized, constraint-driven, explained* subset. |

### A6. Does a library already do most of the strategy selection?

No single one. The pieces exist separately: `accelerate estimate-memory`
(static memory table), DeepSpeed ZeRO memory estimator functions +
Autotuner (DeepSpeed-only profiling search), Ray Tune (hyperparameter
search — orthogonal), axolotl/torchtune presets (curated configs, no
constraint math). The integration — constraints in, ranked explained plans
out, one execution contract, recovery included — does not exist as a
maintained product. **[uncertain: niche startups may be attempting this;
a landscape re-check before public launch is cheap insurance.]**

### A7. Narrowest useful initial product

Three deliverables, in dependency order (they match the staged rebuild plan
already in `PLAN_2WEEKS.md`):

1. **Mode A reliability runtime**: leases, heartbeats, idempotent commit,
   event ledger, artifact store — hyperparameter search / batch eval as
   flagship workloads. (Weeks 1–2; already planned.)
2. **`flash plan` for single-node fine-tuning**: input = model id, method
   (full/LoRA/QLoRA), GPUs, budget, deadline; output = ranked feasible
   plans with arithmetic and rejections. Static estimator + optional
   profiling run. No cluster required — standalone wedge.
3. **One recovered coordinated-training path**: single-node LoRA (then
   1-node DDP) via torchrun + DCP, checkpoint catalog, kill-and-resume with
   measured lost work.

Everything else in the hypothesis is a roadmap item gated on these three
earning users.

---

## B. Workload classification

Classification must be **structural** (communication/memory/failure shape),
never label-based ("PEFT ⇒ FSDP" is wrong, as you say — PEFT changes *what
is trainable*, which changes the memory table's inputs, not the strategy
logic). Classify on measured/declared axes: coupling (none / periodic /
per-step), state locality (task-local / replicated / sharded), failure
blast radius (task / rank-group / job), and data motion (none / shard-in /
stream).

| Class | Communication | Memory pattern | Failure scope | Topology need | Backend (first choice) | Checkpoint need | MVP? |
|---|---|---|---|---|---|---|---|
| Independent tasks (HPO, CV, ablations, eval, synthetic data, Monte Carlo, independent LoRA runs) | None between tasks | Task-local | Single task | Any, incl. WAN devices | Own lease runtime; Ray Core on clusters | None (task retry) or per-task artifacts | **Yes — flagship** |
| Distributed preprocessing / feature extraction | Shuffle/exchange at stage boundaries | Streaming, disk-heavy | Partition (lineage re-derive) | LAN preferred; WAN possible if embarrassingly parallel | Ray Data; plain sharded tasks when no shuffle | Stage outputs as artifacts | Yes (sharded-map subset); Ray Data pipelines soon after |
| Batch inference | None | Weights replicated per worker | Single batch/task | Any with enough VRAM per worker | Lease runtime or Ray Data | None | Yes |
| Classical ML (sharded k-means, partial_fit, XGBoost) | Periodic reduce | Small shared state | Task or round | LAN or WAN (small state) | Own map/reduce over leases (exists); dist-XGBoost later | Round state (tiny) | Yes (exists) |
| PEFT fine-tuning, single node | None (1 GPU) or per-step all-reduce (DDP) | Frozen weights dominate; tiny trainable state | Process/rank | Single host | torchrun + Transformers/PEFT (+bitsandbytes) | **Yes — DCP/Trainer, interval policy** | **Yes — the Mode B beachhead** |
| Full fine-tuning, data-parallel | Per-step gradient all-reduce | ~16 B/param train state (§D) | Rank group | Single host → NVLink/IB multi-node | torchrun + DDP or FSDP2 | Yes | Edge of MVP: 1-node only |
| Model-state-sharded training (FSDP2 / ZeRO-2/3) | Per-layer all-gather + reduce-scatter, per step | Sharded params/grads/optimizer | Rank group (whole-group restart) | NVLink intra-node; ≥100 Gb/s inter-node **[assumption]** | torchrun + FSDP2; DeepSpeed when offload needed | Yes — DCP with resharding | Post-MVP (month 2–3) |
| Model evaluation suites | None | Task-local | Task | Any | Lease runtime | None | Yes (same as independent) |
| Large-model pretraining | Continuous, multi-dimensional | Sharded everything + huge data pipeline | Rank group, frequent | Homogeneous NVLink+IB cluster | torchtitan-class stacks | Advanced (async DCP) | **No — explicitly out** |
| RL (PPO/GRPO-style) | Hybrid: rollout fan-out + training sync | Actor + learner split | Mixed | Mixed | Ray + training backend | Policy + replay state | No |
| MoE training | All-to-all expert routing per layer | Expert-sharded | Rank group | NVSwitch/IB required | Megatron/DeepSpeed-MoE | Complex | No |
| Pipeline multi-stage (ETL→train→eval chains) | Artifact hand-off between stages | Per-stage | Stage | Any | FlashRuntime job graph (stages = jobs) | Stage artifacts | Schema yes, engine later |

### Are two execution modes sufficient? What's missing?

Your Mode A / Mode B split is correct and load-bearing (different failure
semantics ⇒ different recovery machinery). Two additions:

- **Mode 0 — local single-process — must exist from day one.** Not a
  degenerate case: it is the planner's fallback answer ("your job fits on
  one GPU; distribution would cost you money for nothing"), the profiling
  vehicle (§F), and the debugging story. A planner that cannot recommend
  *not distributing* will over-distribute, which is the most common real-
  world ML infrastructure mistake.
- **Mode C — elastic/semi-synchronous training — design for, don't build.**
  torchft (Meta/PyTorch) now provides per-step fault tolerance for HSDP and
  LocalSGD/DiLoCo semi-sync training — recovery *without* whole-group
  restart, demonstrated with thousands of injected failures and no
  checkpoints. It is young and moving **[uncertain: maturity for
  third-party production use]**, but it is exactly where Mode B's "stop the
  world" model is headed. Encode `recovery_model: whole_group_restart |
  per_step_elastic` in the StrategyPlan enum now so Mode C is an adapter
  later, not a schema break.

Pipelines-of-stages is not a fourth execution mode — it's composition of
jobs; keep it at the job-graph level.

---

## C. Strategy selection engine

### Architecture: generate → filter → estimate → rank → explain

Deterministic pipeline; identical inputs ⇒ identical plan (a versioned
`planner_version` field makes decisions reproducible and auditable — same
principle as the recovery policy engine).

1. **Generate** candidates from a *curated menu* keyed by workload class —
   not from an open search space. MVP menu: `local_cpu, local_gpu_1,
   ddp_single_node, qlora_1gpu, qlora_ddp, lora_ddp, lora_fsdp2,
   zero2, zero3, zero3_cpu_offload, independent_tasks(N)`. Each menu entry
   carries its knob ranges (micro-batch, grad-accum, precision, activation
   checkpointing on/off).
2. **Hard feasibility filter** (cheap, static): VRAM lower bound (§D) vs
   device; interconnect class vs strategy's communication class (§E);
   framework compatibility (e.g., QLoRA ⇒ bitsandbytes ⇒ CUDA/ROCm
   platform check); user constraint gates (offload allowed? max GPUs?).
   Every elimination is recorded with its arithmetic — rejections are half
   the product.
3. **Estimate** surviving candidates: peak VRAM + margin, step time =
   max(compute, exposed communication) + data-loading bound, startup time
   (image pull + model load + rendezvous), checkpoint overhead
   (size/bandwidth × frequency), expected failure cost (§G), monetary cost,
   deadline success probability.
4. **Rank** by the user objective: lexicographic — first drop plans
   violating hard constraints (budget, deadline, trust), then order by the
   mode: `cheapest` → cost; `fastest` → wall clock; `balanced` → cost ×
   (1 + deadline-risk penalty); `reliable` → expected completion
   probability. No learned scoring until the ledger has real data.
5. **Explain**: selected plan + per-candidate verdict table + the numbers.

### The decision rules you asked for

Let: `P` params, `p` trainable params, `V` per-GPU VRAM, `M(s)` estimated
peak memory of strategy `s` (§D), `G` gradient bytes exchanged per step,
`B` effective inter-GPU bandwidth, `t_c` per-step compute time,
`E = t_c / (t_c + t_comm_exposed)` scaling efficiency.

- **One GPU vs DDP**: choose 1 GPU when the job fits and meets the
  deadline: N-GPU DDP costs ≈ N× per hour for ≤N× speedup; it only wins
  when deadline requires it or per-hour pricing is sublinear. Planner rule:
  prefer the smallest world size whose estimated finish ≤ deadline ×
  0.8 **[assumption: 20% schedule buffer]**.
- **DDP vs FSDP2**: DDP while full replica fits: `M(ddp) ≤ (1−margin)·V`.
  DDP has one collective per step (overlappable) and trivial semantics.
  Switch to FSDP2 only when replication doesn't fit but sharded state does.
  Between them there is a cheaper middle: ZeRO-1/2-style optimizer/grad
  sharding (or FSDP2 with equivalent config) — try it before full param
  sharding, since param all-gather per layer is the expensive part.
- **FSDP2 vs DeepSpeed ZeRO-3**: functionally overlapping. Default
  **FSDP2** (in-tree, DTensor/DCP-native, torch-versioned — one fewer
  dependency); choose DeepSpeed when you need what only it has: mature CPU
  **and NVMe** offload (ZeRO-Infinity), MoE support, or the user's code
  already speaks DeepSpeed config. Do not present both as equal defaults —
  pick one default per situation and document why.
- **When QLoRA**: user allows quantized frozen weights AND bf16/fp16
  weights don't fit (or budget favors fewer/smaller GPUs). Warn: changed
  numerics vs LoRA — surface it in the explanation, never silently.
- **CPU offload**: last resort before "infeasible": accept only if
  estimated step time still meets the deadline; offload can be
  order-of-magnitude slower per step **[assumption: workload-dependent;
  profile before committing]**. NVMe offload only on measured NVMe
  bandwidth, and only if user allowed it.
- **Reject multi-node** when exposed communication kills scaling:
  `E < 0.5` ⇒ reject by default, `0.5 ≤ E < 0.7` ⇒ warn and require
  explicit opt-in **[assumption: thresholds to calibrate from ledger
  data]**. Ethernet <25 Gb/s with FSDP-class per-layer collectives is an
  automatic multi-node rejection in v1.
- **Convert to independent jobs** when the workload *is* N independent
  units (HPO trials, per-adapter experiments, eval shards): always prefer
  Mode A — per-task failure isolation beats any coordinated recovery. The
  classifier should detect "N seeds/configs over same model" and propose
  the conversion even when the user asked for "distributed training."
- **Shard data, not model**: model (+ margin) fits per device ⇒ DDP-class
  only. **Shard model, not data-architecture**: dataset streams fine but
  weights don't fit ⇒ FSDP/ZeRO with the same DistributedSampler.
- **Tensor/pipeline parallelism unavoidable**: when even fully sharded +
  offloaded single-layer working set exceeds one device, or model layers
  physically exceed device memory (100B+ class). Planner v1 must *detect*
  this and answer honestly: "beyond supported envelope — here is the
  torchtitan/Megatron territory," which is a feature, not a failure.
- **"No valid strategy" report**: emit nearest-miss analysis — which
  constraint each candidate violated and by how much, plus the minimal
  relaxation that unlocks one ("+1 GPU of 24 GB", "allow 4-bit",
  "deadline +40 min"). This turns dead ends into product moments.

---

## D. Memory modeling

### Component model (static, lower-bound by construction)

Per GPU, bytes. `P` total params, `p` trainable, `N_dp` data-parallel
degree, `N_sh` sharding degree.

| Component | Full FT (mixed prec., Adam) | LoRA (bf16 frozen) | QLoRA (4-bit frozen) |
|---|---|---|---|
| Weights | 2P (bf16 compute copy) | 2P | ~0.55–0.75P (NF4 + quant constants + pages) **[assumption]** |
| Master weights (fp32) | 4P (in optimizer) | 4p | 4p |
| Gradients | 2P (bf16) | 2p | 2p |
| Adam m+v (fp32) | 8P | 8p | 8p |
| → train-state subtotal | **≈16P** (the ZeRO paper's baseline) | 2P + 14p (p ≪ P) | ≈0.6P + 14p |
| Activations | see below — dominant wildcard | same | same |
| CUDA context + NCCL buffers + workspace | ~1.5–3 GB flat **[assumption]** | same | same |
| Fragmentation | multiplicative, not additive — covered by margin | | |

Sharding transforms: ZeRO-1 divides the 12P optimizer bytes by `N_sh`;
ZeRO-2 also gradients; ZeRO-3/FSDP2 also weights (but transient per-layer
all-gathers reintroduce one layer group's full params at peak — model this
as `+max_layer_params × 2 bytes`). Offload moves chosen components to CPU
RAM/NVMe — *check host RAM feasibility too*; planners that only check VRAM
kill hosts.

Activations: statically estimable only to first order. Transformer rough
form `≈ c · L · s · b · h` bytes with `c` ranging ~2–34 depending on flash
attention, fused kernels, precision, and checkpointing (full activation
checkpointing reduces to ~one layer's activations + recompute cost ~+30%
step time **[assumption]**). Sequence length and micro-batch are the levers
the planner may tune. **Do not trust `c` — profile it** (§F); use the
pessimistic end statically.

### Static vs profiled

Statically reliable: weights, gradients, optimizer states, sharding
arithmetic, checkpoint sizes (≈ train-state bytes for full; adapter-only
for PEFT). Profiling required: activation constant, fragmentation
behavior, actual peak (`torch.cuda.max_memory_allocated` + NVML),
dataloader throughput, offload penalty, checkpoint save/restore duration.

### Safety-margin policy

- Static-only estimate: require `M ≤ 0.80·V` to auto-launch; `0.80–0.95`
  ⇒ profiling mandatory before launch; `>0.95` ⇒ infeasible.
- Post-profiling: `peak_measured ≤ 0.90·V` to launch. **[assumption:
  starting values — recalibrate from ledger OOM/no-OOM outcomes; this is
  the first genuinely learnable parameter in the system.]**
- Always reserve the flat context/NCCL overhead before applying ratios.
- OOM in a launched job = planner defect: log estimate vs actual as a
  first-class event; it is the estimator's regression suite.

Explicitly: no formula predicts real memory to single-digit percent across
framework versions. The margin policy and the profile stage *are* the
memory model; the formulas only exist to kill impossible plans early.

---

## E. Communication modeling

### Link classification (measured or declared, never assumed from provider names)

`intra_gpu < nvlink (400–900 GB/s) < pcie (16–64 GB/s) < node_local ib
(200–400 Gb/s) < dc_ethernet (10–100 Gb/s) < cross_zone < cross_region <
public_internet (≤1 Gb/s, high jitter)`. FlashNode's benchmark supplies
measured bandwidth/latency per node pair class; planner consumes the
measurement, not the label.

### Per-step traffic by strategy family

- **DDP**: ring all-reduce moves `2·(N−1)/N · G ≈ 2G` bytes/GPU/step
  (G = gradient bytes = 2P full FT, 2p PEFT — *this is why PEFT+DDP is so
  WAN-tolerant relative to full FT*). One collective, overlappable with
  backward.
- **FSDP2/ZeRO-3**: per layer-group, all-gather params (fwd), re-gather
  (bwd), reduce-scatter grads ⇒ ≈3× sharded-param bytes per step in many
  collectives ⇒ latency-sensitive, needs NVLink/IB. **[assumption:
  3× first-order; prefetch/overlap changes exposure, not volume]**
- **ZeRO-2**: grads reduce-scatter + optimizer gather — between DDP and
  ZeRO-3.
- **TP**: activations exchanged per layer per micro-batch — NVLink domain
  only, effectively.
- **PP**: boundary activations only — lowest volume of the model-parallel
  family but latency-serialized (bubbles).

### Feasibility and reasonableness

`t_comm = volume/B + n_collectives · latency`;
`t_exposed = max(0, t_comm − overlap·t_c)` with overlap ≈ 0.5–0.7 for DDP,
lower for ZeRO-3 **[assumption]**; `E = t_c/(t_c + t_exposed)`.

Technically-possible-but-unreasonable is an *economic* verdict, so compute
it economically: `effective_cost = cost_per_hour / (throughput · E)` —
cost per useful sample. If 4 nodes at E=0.45 yield a higher cost per
sample than 1 node at E=1.0, the plan is dominated and the explanation
says so with both numbers. Reject dominated plans by default; render them
in the report (greyed out, with reasons) because *showing the rejected
alternatives is the trust-building feature*.

---

## F. Profiling and benchmarking

Three stages, as you proposed — with these specifics:

**Stage 1 — static** (milliseconds, free): model metadata from
`config.json` (params, layers, hidden, vocab) — never load weights to
count them **[note: gated repos need the user's HF token — surface
early]**; dataset metadata (row count, avg tokens); hardware from
FlashNode capability snapshots. Kill infeasible plans; decide whether
profiling is warranted.

**Stage 2 — profiling run**:
- *What*: the real container, real entrypoint, real data path, tiny
  horizon: `warmup_steps=3` (skips compile/cudagraph/cache effects) then
  `measure_steps=20` **[assumption: enough for steady-state step time on
  stable input shapes; variable-length batches need bucketed stats]**.
  Measure: peak VRAM, host RAM, step time, tokens/s, dataloader wait
  fraction, one checkpoint save+restore cycle (this also *validates the
  checkpoint contract before the real run* — recovery insurance, free).
- *Isolation from the real run*: separate run-id namespace, artifacts to a
  `profiles/` prefix, no `ArtifactRecord` commits to the job, RNG streams
  independent, and the profile run never registers checkpoints in the
  catalog. Enforced by the runtime, not by convention.
- *Large/expensive models*: profile at reduced cost, extrapolate
  structurally — same model with 2–4 layers (transformers are per-layer
  homogeneous: fit `mem = a + b·L`, `t = a' + b'·L` and extrapolate to full
  L **[assumption: holds for dense transformers; breaks for MoE/hybrid
  architectures — label extrapolated estimates in the plan]**), shorter
  sequence, micro-batch 1. When even that is too costly, skip.
- *Skip policy*: skip when `profile_cost > 2–5% · job_budget`, when a
  cache hit exists, or when static margins are comfortable (`M ≤ 0.6·V`).
  Always skip for Mode A tasks (the first tasks *are* the profile).
- *Cache key*: `(model_digest, strategy_family+knobs, gpu_class,
  framework_versions, seq_len_bucket, batch_bucket)` → measured profile,
  stored in the ledger. Every *real* run then back-feeds actual peak/
  throughput/checkpoint numbers into the same table with higher trust —
  **this is the flywheel: the runtime's ledger is the planner's dataset.**

---

## G. Reliability and recovery

Mode A machinery (leases/heartbeats/idempotent commit) is settled in
PLAN_2WEEKS.md — not repeated here. The failure taxonomy spanning both
modes:

| Failure | Signals | Classified as | Retry scope | Node reusable? | Group restart? | Checkpoint role | Escalate when |
|---|---|---|---|---|---|---|---|
| Application error | Deterministic non-zero exit, traceback, same-step repeat | App (not infra) | None by default; retry only if policy marks transient | Yes | No — fail job fast | None | Always → user, with logs |
| Data error | Loader exception, schema/hash mismatch on shard | Data | Skip/quarantine shard if policy allows, else fail | Yes | No | Data cursor in checkpoint | Quarantine rate > threshold |
| Worker process crash (OOM, segfault) | Exit signal, OOM-killer, CUDA OOM | Infra-transient (OOM ⇒ planner defect too) | Task (A) / group (B) | Yes unless repeated | B: yes | Latest valid | Same node 3× → cordon |
| Node loss | Heartbeat timeout, agent gone | Infra | Reassign leases (A) / replace + restart (B) | No — cordon | B: yes | Latest valid | Correlated (see below) |
| GPU failure | XID events, ECC, NVML/DCGM health, CUDA errors | Hardware | As node loss, GPU-scoped | Node maybe, GPU no | B: yes | Latest valid | Recurring XID → quarantine host |
| Driver failure | NVML unreachable, driver mismatch after reboot | Hardware/config | Node-scoped | After remediation only | B: yes | Latest valid | Always flag host |
| NCCL/RCCL error | Watchdog timeout, `ncclUnhandledError`, rendezvous failure | Ambiguous — root-cause via co-signals (a rank died? network?) | Group | Usually yes | **Yes — NCCL state is not repairable in place** | Latest valid | Repeats without identifiable dead rank → network investigation |
| Network degradation | Heartbeat jitter, throughput collapse, transfer retries | Infra-soft | A: move tasks; B: restart on better pool | Yes (lower score) | B: sometimes | Latest valid | Persistent → reroute pool |
| Storage timeout | Object-store 5xx/timeouts on artifact/checkpoint IO | Dependency | Retry IO with backoff; then pause job, don't kill compute **[wasting compute on dead storage is the worst branch]** | Yes | No | N/A — protect the catalog | Sustained → stop automation |
| Artifact/checkpoint corruption | Hash mismatch, partial manifest, load failure | Integrity | Reject artifact; retry task; fall back to previous valid manifest | Yes; score down producer | B: restart from older | **Critical — see §H** | Two consecutive bad checkpoints |
| Provider preemption | Provider event/spot notice | Infra-expected | Replace capacity, resume | N/A (gone) | B: yes | Latest valid; tighten interval on spot | Preemption rate ≫ priced-in rate |
| Correlated multi-node incident | ≥k failures in window across nodes/pool | Systemic | **Freeze automation** — no retry storms | — | — | Preserve state, stop | Always → human/policy |
| Control-plane failure | Coordinator/ledger unreachable | Self | Workers continue current leases/steps; no new grants; agents buffer events | Yes | No | Keep local until reconnect | Extended outage |

Rules the table encodes: (1) never auto-retry what looks deterministic;
(2) NCCL-class errors always mean whole-group restart in v1 — per-step
elastic recovery is Mode C/torchft territory, later; (3) correlated
failure freezes automation — retry storms during incidents are how
orchestrators destroy trust; (4) every classification + action is an
event with a `policy_version` — auditable, reproducible.

### Failure cost inside planning

`E[T] = T_ideal + λ·T_ideal · (t_detect + t_replace + t_restart +
t_lost)`, with `λ` = failures/hour for the pool (ledger-learned; priced-in
spot rates as priors **[assumption]**), `t_lost ≈ checkpoint_interval/2`.
Checkpoint interval: Young–Daly optimum `τ* = √(2·C·MTBF)` (C = measured
checkpoint duration from the profile stage) — bounded by the user's
maximum acceptable loss. Consequences the model surfaces naturally: spot
+ big checkpoint + short MTBF can make "cheap" capacity lose to reliable
capacity on *expected* cost — exactly the master report's
expected-cost-per-completed-job objective, now with a formula.

---

## H. Checkpoint architecture

Correct instinct: **never invent serialization**. PyTorch → DCP (parallel
save/load, load-time resharding across topologies — the property that
makes "restore on different world size" real). DeepSpeed → native
checkpoints (+ Universal Checkpoint for cross-topology conversion
**[uncertain: UCP coverage across all ZeRO configs — validate per
envelope]**). Ray/Mode A → task artifacts. HF Trainer → its checkpoint
dir, wrapped.

FlashRuntime owns the **manifest + catalog + validity + selection**. Your
field list is right; grouped schema in §L. The critical mechanics:

**Never mark a partially uploaded distributed checkpoint valid — by
construction, not by checking:**
1. Ranks upload shard files to
   `checkpoints/<job>/<attempt>/<step>/parts/…` with per-part sha256 +
   sizes.
2. Coordinator (or rank-0) verifies the expected part set (from the
   strategy's rank layout) against uploaded hashes.
3. **Manifest written last, to a different key, only after verification.**
   No manifest ⇒ checkpoint does not exist, regardless of how many bytes
   are present. The manifest *is* the commit record (two-phase commit; no
   in-place mutation; supersedes, never overwrites).
4. `validation_status` upgrades: `hash_verified` (cheap, automatic) →
   `restore_verified` (a real load succeeded — the profile stage's
   save/restore cycle provides this for free on plan; recovery prefers
   restore-verified manifests).
5. Recovery selection: latest manifest that is (a) verified, (b)
   compatible with the replacement topology (`compatible_world_sizes`,
   resharding capability), (c) not superseded by a quarantined producer.
   If none: report bounded lost work honestly.

Retention: keep last k=2 verified + periodic anchors **[assumption:
configurable]**; never GC the only restore-verified manifest.

---

## I. Public API

### The `flash.Job / flash.plan / flash.run` shape — appropriate, with corrections

1. **`plan()` is long-running** (may include profiling): make it return a
   `PlanReport` future/handle; CLI: `flash plan job.yaml` prints the
   report, `--no-profile` for static-only.
2. **Plans must be pinnable and diffable**: `flash.run(plan)` executes the
   *frozen* artifact (plan hash recorded in the job attempt); rerunning a
   changed environment warns on drift. Reproducibility is a reliability
   product's core promise.
3. **Escape hatch is non-negotiable**: `strategy=flash.Explicit(
   "fsdp2", knobs…)` — planner validates and explains but obeys. Experts
   will not adopt a black box; validated-explicit mode is also your best
   estimator feedback source.
4. **The unsolved hard part is the entrypoint contract** — an arbitrary
   `train.py` cannot be checkpoint-managed from outside. Define two
   integration tiers, explicitly:
   - **Tier 1 "launch-only"**: any script. Contract via environment:
     `FLASH_CHECKPOINT_DIR`, `FLASH_RESUME_FROM`, `FLASH_MAX_STEPS`;
     FlashRuntime watches the checkpoint dir, uploads, builds manifests.
     Works with unmodified HF Trainer (its `--resume_from_checkpoint` and
     save cadence already fit). Weakest guarantees: no step-level progress
     events.
   - **Tier 2 "instrumented"**: ~5-line SDK — `flash.init()`,
     `flash.report(step=, metrics=)`, `with flash.checkpoint() as dir:` —
     giving live progress in the ledger, atomic checkpoint registration,
     and clean lost-work math. MVP recipes (HF Trainer callback) implement
     Tier 2 *for* the user, so the beachhead path needs zero user code.
5. **YAML**: extend the existing `flashml.dev/v1alpha1` JobSpec — add
   `workload:`, `objective:`, `strategy: auto|explicit` blocks. **Do not
   create a second format**; one spec, SDK and YAML both bind to it.

Interfaces to define now (each: minimal protocol + conformance test, per
the master report's adapter-conformance principle):
`ExecutionBackend` (exists) · `Launcher.launch(plan, rendezvous) →
ProcessGroupHandle` · `CheckpointAdapter.{expected_parts, validate,
restore_args}` · `WorkloadPlugin.{classify, candidates, estimate_inputs}`
(entry-point registered — HPO/k-means already fit this shape) ·
`ResourceProvider.{offer, acquire, release, price}` · failure events =
typed `Event` subtypes on the existing ledger, versioned enum + payload
schemas.

Plan output format: §L; your `selected_plan` + `reasons` sketch is right
and becomes the `explanation` block.

---

## J. Backend architecture — role of each dependency

Legend: **Reuse** = call as-is · **Wrap** = put behind FlashRuntime
adapter · **Expose** = keep visible for experts · **Hide** = internal.

| Dependency | Solves already | FlashRuntime stance | MVP? | Risk / stability |
|---|---|---|---|---|
| **Ray Core** | Cluster task/actor execution, retries | Reuse; wrap as Mode A cluster backend (exists: KubeRay) | Yes (present) | Stable; heavy dependency — never required for Mode A on devices (own lease runtime there) |
| **Ray Data** | Distributed datasets, preprocessing, batch inference | Wrap as data-pipeline backend | Soon after MVP | Stable-ish; API still evolves |
| **Ray Train** | Torch process-group orchestration on Ray | Defer — overlaps torchrun path; adopt only if Ray-cluster-native training demanded | No | **V2 migration in progress** (V1 deprecated, V2 default via env flag since 2.43): wait for V2 to settle before building a public abstraction on it |
| **Ray Tune** | HPO scheduling/pruning algorithms | Selective: FlashRuntime owns trial *execution* (leases); may borrow Tune's search algorithms as a library | No (grid/random first) | Coupled to Train V2 churn |
| **torch.distributed / DDP** | Data-parallel training, overlapped all-reduce | Reuse; strategy family in plans | **Yes** | Very stable |
| **FSDP2 (`fully_shard`)** | Param-sharded training on DTensor | Reuse; the default sharded-training strategy | Month 2–3 | Production-ready, now the PyTorch-native center (FSDP1 deprecated in tutorials); DTensor/DeviceMesh still evolving at the edges |
| **torchrun / Elastic** | Launch, rendezvous, whole-group restart | Reuse as the Mode B launcher; FlashRuntime supplies checkpoint-resume logic around it (Elastic restarts, *app* must restore) | **Yes** | Stable |
| **DCP** | Parallel save/load, load-time resharding | Reuse; primary checkpoint backend behind CheckpointAdapter | **Yes** (Stage-7 path) | Core stable; `async_save` newer — adopt later **[uncertain: maturity]** |
| **DeviceMesh/DTensor/TP APIs** | Multi-dim parallelism primitives | Not yet; StrategyPlan enums reserve the names | No | Moving |
| **HF Transformers** | Models, configs, tokenizers | Reuse *inside recipes* — it is workload-layer, **not a backend** (see §K) | **Yes** | Stable enough; fast-moving surface — pin per recipe |
| **HF PEFT** | LoRA/adapters | Reuse in recipes | **Yes** | Stable |
| **HF Trainer** | Training loop, save/resume, callbacks | Wrap via callback = the Tier-2 contract for the beachhead | **Yes** | Stable |
| **HF Accelerate** | Unified device/strategy handling in user code | **Do not build on it as a layer** — it overlaps torchrun (launch) and strategy config, creating two owners of the same decisions. Remain compatible with user scripts that use it (Tier 1) | No | Stable but wrong altitude for FlashRuntime |
| **TRL** | RLHF/SFT loops | Later recipes | No | Fast-moving |
| **bitsandbytes** | 4/8-bit quantization | Reuse for QLoRA recipes | Yes (QLoRA path) | Platform-sensitive (CUDA-centric) — feasibility filter must gate on platform |
| **DeepSpeed** | ZeRO 1–3, CPU/NVMe offload, MoE | Wrap as alternative sharded-strategy family; its Autotuner = estimator baseline to validate against | No (month 3+) | Actively maintained (0.19.x, June 2026) but historically API-churny; config-file surface is the stable part — target that |
| **NCCL/Gloo** | Collectives | Hidden entirely (via torch); Gloo = CPU/dev fallback | Yes (implicit) | Stable |
| **Kubernetes/Kueue** | Cluster substrate, quotas, gang admission | Reuse; existing KubeRay backend; Kueue when multi-team quotas appear | Yes (present) | Stable |
| **Slurm** | HPC scheduling | Later launcher adapter (university demand is real) | No | Very stable |
| **SkyPilot** | Multi-cloud provisioning, managed-job infra recovery | Wrap as ResourceProvider — *provisioner, not runtime*; FlashRuntime adds the app-state awareness SkyPilot lacks | No (month 3+) | Stable, active |
| **Runpod/Vast** | GPU capacity APIs | ResourceProvider adapters, one first | No | Provider API churn; per-provider support cost |
| **torchft** | Per-step fault tolerance (HSDP), semi-sync (DiLoCo) | Watch; Mode C schema reservation only | No | Young, active (Meta); the roadmap threat/opportunity to Mode B's restart model |

---

## K. Internal architecture — evaluation of the proposed tree

The proposed layout is directionally right (planner ≠ backends;
backend-neutral StrategyPlan compiled by adapters — correct and
non-negotiable). Required changes:

1. **`spec/` already exists — it's `protocol/`.** Job/workload/resources/
   constraints/objectives/strategy schemas are the versioned public wire
   contract that flashnode and flashml-cloud import. Don't create a
   parallel `spec/` package; grow `protocol/v1alpha1` (or cut `v1alpha2`)
   with the new models. Planner internals that aren't wire contract stay
   in `planner/`.
2. **`backends/huggingface/` is a category error.** Transformers/PEFT/
   Accelerate are the *workload* layer (what the user's process runs), not
   an execution mechanism (how processes are placed/launched/restarted).
   Move to `recipes/` (or `workloads/` — merge with the existing
   `flashml_workloads/`): `recipes/hf_lora.py`, `recipes/sklearn_hpo.py`,
   each implementing the WorkloadPlugin interface. Keeping this axis clean
   is what stops the planner from ever containing `import transformers`.
3. **Name the four axes and keep them orthogonal** — provider (get
   machines) / launcher (start processes) / strategy (configure execution:
   ddp, fsdp2, zero3 — *data*, compiled from StrategyPlan by adapters, not
   one module per strategy under `backends/pytorch/`) / recipe (integrate
   user code). The proposed tree mixes strategy families into `backends/`;
   flatten to `backends/` (local, ray/kuberay — exists), `launchers/`
   (torchrun, k8s, slurm, skypilot), `strategies/` (compilers from
   StrategyPlan → torchrun env+args / deepspeed config), `recipes/`.
4. **`state/` largely exists** — `service/ledger.py` + `leases/` +
   `engine/`. Keep the append-only event model as the single source of
   truth (status derived, never hand-mutated); add `checkpoints.py`
   (catalog) and `workers.py` there.
5. **Add `explain/`** (or make it a planner stage): rendering
   PlanReport/recovery narratives is a first-class module, not
   print-statements — it's half the differentiation.
6. **Do not scaffold 40 empty files.** The repo already once held
   docstring-only packages for a year. Create each module in the vertical
   slice that makes it real (the PLAN_2WEEKS.md discipline). Tree shape is
   cheap; empty promises in a public repo are not.

Resulting shape (delta from today's repo):

```
flashruntime/
├── protocol/        # exists — grows: WorkloadSpec, Objective, StrategyPlan,
│                    # CheckpointManifest, failure-event payloads (versioned)
├── planner/         # new: classifier, candidates, feasibility, estimators
│                    # (memory/comm/cost/reliability), profiler driver, selector, explain
├── strategies/      # new: StrategyPlan → backend config compilers
├── backends/        # exists (base, kuberay, local) — execution substrates
├── launchers/       # new: torchrun first; k8s/slurm/skypilot later
├── recipes/         # absorb flashml_workloads: hf_lora, sklearn_hpo, kmeans
├── leases/ service/ # exists — Mode A runtime + ledger (+ checkpoint catalog)
├── recovery/        # exists as scaffold: failure classifier, policy, restart
├── profiling/       # new: hardware/model/data/network probes (FlashNode feeds hardware)
├── storage/         # exists as artifacts/ — + content_hash, manifest IO
└── sdk/ cli/        # exists — grows plan/run/status
```

---

## L. StrategyPlan model

Design requirements: versioned; backend-neutral (nothing a specific
launcher requires to *parse*); complete enough that `run(plan)` needs no
re-planning; carries its own provenance and explanation; hashable/frozen.

```yaml
apiVersion: flashml.dev/v1alpha1
kind: StrategyPlan
metadata:
  plan_id: pl_9f3a…            # content hash of spec+decisions
  job_ref: job_46a9…
  planner_version: 0.3.0        # decisions reproducible per version
  created: 2026-07-19T09:30:00Z

workload:
  class: peft_finetuning        # from the §B taxonomy
  mode: coordinated_training    # independent_tasks | dataflow | coordinated_training | local
  recovery_model: whole_group_restart   # reserved: per_step_elastic (Mode C)

topology:
  workers: 4
  gpus_per_worker: 1
  gpu_class: "24GB-ADA"         # capability class, never provider SKU
  colocated: same_host          # same_host | same_pod_lowlat | multi_node_ib | …
  interconnect_min: pcie4       # feasibility gate the scheduler must honor
  provider_pool: null           # bound at schedule time, not plan time

strategy:
  family: ddp                   # local|ddp|fsdp2|zero1|zero2|zero3|tp|pp|hybrid
  knobs:
    precision: bf16
    quantization: {frozen_weights: nf4}     # QLoRA
    peft: {method: lora, rank: 16, alpha: 32}
    micro_batch: 4
    grad_accum: 4
    activation_checkpointing: false
    offload: none               # none | optimizer_cpu | params_cpu | nvme

execution:
  launcher: torchrun
  recipe: hf_trainer_lora       # Tier-2 integration; or tier1_env for launch-only
  image: ghcr.io/…@sha256:…     # digest-pinned
  data: {source: s3://…, sampler: distributed, shards: auto}

checkpoint:
  backend: pytorch_dcp
  interval_seconds: 300         # Young–Daly-derived, user-capped
  manifest_required: true
  compatible_world_sizes: [1, 2, 4]    # via DCP resharding
  restore_verified_required_for_recovery: true

recovery:
  max_group_restarts: 3
  lease: null                   # Mode A only
  escalation: {correlated_failures: freeze, app_error: fail_fast}
  policy_version: 0.2.0

estimates:                      # every number carries provenance
  peak_vram_per_gpu: {value_gb: 19.4, margin: 0.15, basis: profiled, profile_id: prof_bb12}
  step_time_s: {value: 1.9, basis: profiled}
  scaling_efficiency: {value: 0.94, basis: static}
  startup_s: {value: 210, basis: static}
  checkpoint_overhead: {save_s: 14, size_gb: 1.2, basis: profiled}
  expected_failures: {rate_per_hour: 0.02, basis: ledger_pool_history}
  cost_usd: {expected: 11.40, p90: 14.80}
  deadline: {requested_min: 180, expected_min: 96, success_prob: 0.97}

explanation:
  selected_because:
    - "Base model quantized NF4 fits per GPU: 4.1 GB weights + 0.6 GB LoRA state + 13.2 GB activations(profiled) = 19.4 GB ≤ 24 GB × 0.90."
    - "DDP chosen over FSDP2: full replica fits; 1 collective/step of 84 MB (LoRA grads only) is negligible on PCIe4."
    - "4 workers is the smallest world size meeting the 3 h deadline with buffer (expected 96 min)."
  rejected:
    - {candidate: lora_bf16_1gpu, reason: "peak 28.9 GB > 24 GB × 0.95 — infeasible"}
    - {candidate: zero3_cpu_offload, reason: "feasible; expected 4.4 h > deadline"}
    - {candidate: qlora_1gpu, reason: "feasible; expected 5.1 h > deadline; cheapest option ($6.10) if deadline relaxed"}
```

Notes: (1) `estimates.basis` ∈ {static, profiled, ledger} is what makes
plans honest and improvable; (2) the plan never names concrete nodes —
binding capacity is the scheduler's job at run time, which keeps plans
portable across pools; (3) `explanation.rejected` is part of the contract,
not decoration; (4) freeze + hash the plan; the job attempt records
`plan_id` so incidents can always answer "what did we think, and why."

---

## Appendix: API Stability Register (verified July 2026)

| API | Status | Consequence for FlashRuntime |
|---|---|---|
| FSDP2 `fully_shard` | Production-ready, center of PyTorch-native large-model stack; FSDP1 deprecated in official tutorials | Default sharded strategy; skip FSDP1 entirely |
| torchrun / Elastic | Stable; whole-group restart semantics unchanged | Mode B launcher; FlashRuntime owns resume-from-checkpoint around it |
| DCP save/load + load-time resharding | Stable core; async_save newer (perf work landing, e.g. cached-plan speedups) | Primary checkpoint backend; adopt async later |
| Ray Core / Data / Tune | Stable / stable-ish / coupled to Train | Cluster Mode A backend (already in use) |
| Ray Train | **V1 deprecated; V2 rollout since 2.43, migration ongoing** | Do not build a public abstraction on it yet |
| DeepSpeed | Actively maintained (0.19.x, June 2026); Autotuner tunes ZeRO stages + micro-batch, not offload | Month-3 strategy family; estimator validation baseline |
| torchft | Active (fault-tolerant HSDP, LocalSGD/DiLoCo); young | Mode C schema reservation; revisit quarterly |
| Alpa | Archived October 2024 | Cautionary evidence for the "compiler" claim |

**Sources:**
[PyTorch fully_shard docs](https://docs.pytorch.org/docs/stable/distributed.fsdp.fully_shard.html) · [FSDP2 tutorial (FSDP1 deprecated)](https://docs.pytorch.org/tutorials/intermediate/FSDP_tutorial.html) · [torchtitan FSDP notes](https://github.com/pytorch/torchtitan/blob/main/docs/fsdp.md) · [torchft repo](https://github.com/meta-pytorch/torchft) · [PyTorch blog: fault-tolerant Llama w/ torchft](https://pytorch.org/blog/fault-tolerant-llama-training-with-2000-synthetic-failures-every-15-seconds-and-no-checkpoints-on-crusoe-l40s/) · [Ray Train V2 blog](https://www.anyscale.com/blog/ray-train-v2-unified-distributed-training-on-ray) · [Ray Train V1 deprecation](https://docs.ray.io/en/latest/train/api/deprecated.html) · [Train V2 migration issue](https://github.com/ray-project/ray/issues/49454) · [DeepSpeed releases](https://github.com/deepspeedai/DeepSpeed/releases) · [DeepSpeed Autotuning](https://www.deepspeed.ai/tutorials/autotuning/) · [DCP async_save recipe](https://docs.pytorch.org/tutorials/recipes/distributed_async_checkpoint_recipe.html) · [6× faster async checkpointing](https://pytorch.org/blog/6x-faster-async-checkpointing/) · [DCP docs](https://docs.pytorch.org/docs/stable/_sources/distributed.checkpoint.md.txt) · [Alpa (archived Oct 2024)](https://github.com/alpa-projects/alpa)
