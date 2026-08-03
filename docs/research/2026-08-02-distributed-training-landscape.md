# Distributed Training Across Machines and Networks

**How the major ML frameworks distribute, how the platform industry organizes
compute, what the network really costs, and what FlashML should build —
with difficulty estimates and per-library implementation approaches.**

*Zolli Labs internal research paper — 2026-08-02. Private: contains
competitor analysis and internal effort estimates. Do not move into the
public `flashml` repository.*

---

## Abstract

FlashML today distributes work in exactly two shapes: independent leased
tasks (sklearn sweeps, hyperparameter search, sharded K-means) and
single-node coordinated process groups (`torchrun --standalone`; multi-node
raises `NotImplementedError`). The user-facing goal examined here is to
support TensorFlow, JAX, and Hugging Face Transformers for distributed
training, including true multi-node execution over networks.

We surveyed three bodies of evidence: (i) the internal distributed-training
machinery of PyTorch, TensorFlow, JAX, and the meta-frameworks above them
(Trainer/accelerate, DeepSpeed, Horovod, Ray Train, Lightning, Megatron);
(ii) the platform landscape — federated-learning platforms (Flower, Google's
production system, NVFlare, FedML/TensorOpera, the cross-silo systems), GPU
clouds and marketplaces (RunPod, Vast.ai, Salad, io.net, Akash), volunteer
computing (BOINC, Folding@home), and decentralized-training networks (Prime
Intellect, Nous Research, the Hivemind lineage); and (iii) the systems
literature on training over unreliable networks (DiLoCo and its scaling
laws, spot-instance systems, checkpoint systems, production failure
statistics, NAT traversal, verification of untrusted compute).

Five findings drive the recommendations. **(1)** Every framework separates a
tiny *control plane* (rendezvous: who participates, at which address) from a
heavy *data plane* (collectives: gradients every step); frameworks differ
mainly in who provides rendezvous — PyTorch ships a launcher, TF and JAX do
not, so supporting them obliges FlashML to become a rendezvous provider.
**(2)** The industry-standard answer to a dead worker in a collective group
is exactly FlashML's existing `RESTART_GROUP` policy row — gang restart from
checkpoint — because NCCL state is officially non-repairable; the exceptions
(TorchFT, Elastic Horovod, Pathways) prove the rule's cost. **(3)**
Synchronous data-parallel training dies outside datacenters on physics:
holding 95% utilization takes 100–300 Gbit/s for plain DP but only a
near-constant 1–5 Gbit/s under Streaming DiLoCo — while consumer uplinks
are 20–60 Mbit/s, which is why every real "decentralized" run to date is
islands-of-datacenter-GPUs over WAN, not laptops. **(4)** The
coordinator-mediated topology FlashML already ships as its FedAvg driver is
not a toy: Flower's Photon has federated-pretrained 7B/13B LLMs with
64–512× communication reduction, and Nous post-trained a production model
(Hermes 4.3) this way, with the decentralized run *beating* its centralized
control. **(5)** Communication *topology* — not framework — is the axis
that determines placement, admission, and recovery; FlashML's two execution
modes are a two-member version of the four-member axis the landscape
exhibits (local, independent, coordinator-mediated, collective).

We recommend formalizing that axis, then landing framework support as six
sequenced slices: complete the Transformers surface (S, days); a
framework-neutral group launcher plus TF and JAX single-node adapters (M,
1–2 weeks, fully testable on the M4 dev machine); the topology axis in the
protocol and recovery policy (M, ~1 week); trusted-pool multi-node
collectives behind a fail-closed `multinode_capable` gate (L, 3–4 weeks);
a framework-neutral coordinator-mediated engine generalizing the FedAvg
driver toward DiLoCo (L, 3–5 weeks, volunteer-pool compatible); and a
verification roadmap (XL, staged). Collective training never runs on the
volunteer pool; the volunteer pool gets the coordinator-mediated engine,
which is the configuration the evidence says actually works.

---

## 1. Introduction, scope, and method

### 1.1 The question

FlashML's runtime (`flashruntime`) operates jobs: plan, launch, observe,
recover — it deliberately does not reimplement distributed ML (ADR-0003).
Its framework surface today is three thin adapters
(`integrations/pytorch.py`, `huggingface.py`, `sklearn.py`, 153 lines
total) and one in-script helper (`flashruntime.torch`). PyTorch is the only
framework with a coordinated path, and only single-node: the adapter pins
`torchrun --standalone --local-addr=127.0.0.1` and raises
`NotImplementedError` for `nnodes > 1` (`integrations/pytorch.py:23`).

The question, as posed: support TensorFlow, JAX, and Transformers for
distributed training — and do it for *true multi-node through networks*,
understanding first how the libraries themselves do it, how competitor
platforms organize compute and handle node churn, and what extra complexity
the network adds. Then: estimate difficulty and specify approaches per
library.

### 1.2 Method

Six parallel research sweeps were run against primary sources (framework
documentation and source code, vendor docs, arXiv papers, engineering
blogs), one each for: PyTorch internals; TensorFlow + JAX; the
meta-frameworks; federated-learning platforms; GPU
marketplaces/volunteer computing; and the decentralized-training
literature. In parallel, the FlashML codebase was audited so every local
claim below cites a file and line that was read this session, not recalled.
Vendor-reported performance numbers are marked as such. All sources are
listed in §8; facts are current as of 2026-08-02 (TF 2.21.0, JAX 0.8.x,
transformers v5.x docs, PyTorch 2.13 docs).

### 1.3 How to read this

Part I (§2) explains how each framework distributes and where they differ —
read it to understand what an adapter must provide. Part II (§3) maps the
platform landscape and, specifically, how each platform tracks node
liveness and survives churn — read it to see which archetypes FlashML
competes with and which designs it has independently converged on. Part
III (§4) is the physics: what the network costs, and the algorithmic and
systems escape hatches. Part IV (§5) locates FlashML in that map. Part V
(§6) is the plan: difficulty estimates and per-library approaches, with
files and contracts named. §7 lists risks; §8 references.

Two terms recur and are worth pinning early:

- **Control plane** — small, latency-tolerant coordination traffic: who is
  in the job, at which address, who is alive, what work is claimed. In
  FlashML this is the dial-out HTTP surface every flashnode worker already
  speaks (`flashnode/executor/client.py`).
- **Data plane** — the training payload itself. In collective training this
  is gradients or parameter shards exchanged worker↔worker *every step*; in
  coordinator-mediated training it is model deltas exchanged worker↔hub
  *every round*. The two differ by three to five orders of magnitude in
  bandwidth demand, and that gap is the origin of nearly every design
  decision surveyed below.
---

## 2. Part I — How the frameworks distribute

### 2.1 The common anatomy

Strip any of the frameworks to its skeleton and the same four components
appear:

1. **Rendezvous / bootstrap** — N processes discover each other, agree on
   membership, and each learns its identity (rank / task index /
   process_id). Mechanically this is always a small key-value service +
   barrier: PyTorch's TCPStore, TF's cluster spec baked into `TF_CONFIG`,
   JAX's coordination service. Only *metadata* moves here — addresses,
   barrier counters, and the NCCL communicator id.
2. **Collectives (the data plane)** — allreduce/allgather/reduce-scatter
   executed by NCCL (GPU), Gloo/RING-gRPC (CPU), or XLA collectives,
   exchanging the actual tensors every step, worker↔worker, never through
   the bootstrap service.
3. **Checkpoint** — the only durable state; in every mainstream framework
   the unit of recovery.
4. **Failure detection** — timeouts, health-check probes, heartbeats;
   universally converting "peer stopped responding" into "kill the group,"
   because a half-dead collective otherwise hangs.

The frameworks differ in *who provides* step 1 and in what happens after
step 4 — and those two differences are precisely FlashML's business.

### 2.2 PyTorch

**Bootstrap.** `init_process_group(backend, init_method="env://")` reads
`MASTER_ADDR`/`MASTER_PORT`/`RANK`/`WORLD_SIZE`. Rank 0 binds a **TCPStore**
server on `MASTER_PORT`; every other rank connects as a client. The store
carries barrier counters and the NCCL unique id (rank 0 calls
`ncclGetUniqueId()`, writes it to the store; everyone calls
`ncclCommInitRank`); tensor traffic never touches it. Since PyTorch 2.4 the
TCPStore server is libuv-based and initializes 96K ranks in ~100 s.
Default process-group timeouts: NCCL 10 min, Gloo/MPI 30 min — after which
collectives abort and the process crashes.

**The launcher.** `torchrun` (TorchElastic) runs an **elastic agent** per
node that spawns workers and hands them a 13-variable env contract
(`RANK`, `LOCAL_RANK`, `WORLD_SIZE`, `GROUP_RANK`, `MASTER_ADDR`,
`MASTER_PORT`, `TORCHELASTIC_RESTART_COUNT`, `TORCHELASTIC_RUN_ID`, …).
Rendezvous backends: `static`, or `c10d` (a TCPStore, default port 29400,
no external dependency; etcd optional). The rendezvous contract is worth
copying verbatim into any coordinator design:

- **Barrier**: nodes block until at least `min` participants join, then
  wait `last_call_timeout` (default 30 s) for stragglers; complete
  immediately at `max`. `join_timeout` default 600 s.
- **Exclusivity**: late nodes queue as *waiting*; they do not join a
  running group.
- **Consistency with unstable ranks**: each member gets a rank 0..N-1, and
  "ranks are *not stable*" across re-rendezvous — user code must not
  assume rank permanence.
- **Failure semantics** (docs, verbatim): "for a training job with `n`
  workers, if `k<=n` workers fail **all workers are stopped and
  restarted**, up to `--max-restarts`" (default 0). Membership change:
  same — everyone killed, new `RANK`/`WORLD_SIZE`, "make sure to
  checkpoint your progress." Elasticity (`--nnodes MIN:MAX`) is therefore
  *restart-based*, not live.

**DDP mechanics.** (Li et al., VLDB 2020.) At construction, rank 0's
`state_dict` is broadcast; a Reducer registers a hook per parameter and
maps parameters to ~25 MiB buckets in roughly reverse parameter order.
During backward, each bucket's allreduce launches asynchronously the moment
its gradients are ready, overlapping communication with the rest of
backward — the single trick responsible for DDP's near-linear scaling to
256 GPUs. All ranks must allreduce buckets in identical index order or the
job silently corrupts or hangs. `no_sync()` suppresses the allreduce (the
primitive DiLoCo-style local steps build on).

**Failure behavior.** If a peer dies mid-collective, NCCL survivors' GPU
kernels never complete — the job *hangs* until the watchdog aborts it.
NVIDIA is explicit that a failed communicator cannot be reused: recovery is
`ncclCommAbort` + re-create (a newer `ncclCommShrink` builds a *new* group
excluding dead ranks). PyTorch's watchdog thread aborts on timeout or async
NCCL error (`TORCH_NCCL_ASYNC_ERROR_HANDLING`, default = tear down the
process), and documented abort/re-init hazards (pytorch#115388, #119196,
NVIDIA/nccl#1013) explain why the industry's recovery unit is the
**process**, not the communicator. This is independent, primary-source
confirmation of the reasoning FlashML's policy table already encodes at
`recovery/policy.py`: "NCCL-class error — collective state is not
repairable in place; whole-group restart."

**Sharded training and checkpoints.** FSDP2 (`fully_shard`) shards
parameters as DTensors over a DeviceMesh; HSDP composes shard-within-group
+ replicate-across-groups. `torch.distributed.checkpoint` (DCP) writes one
file per rank plus metadata and — critically for any operator — supports
**load-time resharding**: save under one topology, restore under another.
DCP also ships a Hugging Face safetensors reader/writer, a useful
lingua-franca precedent for a framework-neutral weight codec.

**TorchFT — the frontier.** `meta-pytorch/torchft` adds *per-step* fault
tolerance across the data-parallel dimension: a Rust **Lighthouse** server
computes quorum from frequent heartbeats; each replica group's manager
wraps its cross-group process group (`ProcessGroupBabyNCCL` runs NCCL in a
killable subprocess — the direct engineering consequence of non-repairable
communicator state); each step is a distributed transaction gated by
`should_commit`; on failure the batch is discarded, the world shrinks, and
training continues *without* restart; recovering replicas fetch state from
a healthy peer, no stop-the-world checkpoint reload. Meta's public
validation run: a 1B Llama-3-style model on 300 L40S GPUs over **TCP-only
networking**, injected failures every 60 s → 82.3% step efficiency (83.3%
theoretical ceiling); failures every ~15 s (1,015 failures) → 30.2%
efficiency and the model *still converged*. The same post measured DiLoCo
(sync every 40 steps) at ~3.3× the throughput of HSDP on that ethernet
cluster. Status: nightly-only, pre-1.0, research-grade — but it defines
where "recovery" is going, and its architecture (heartbeat quorum service +
transactional commit + peer-state recovery) is strikingly lease-shaped.

**Network requirements.** Three flows with different port behavior: the
rendezvous endpoint (one well-known port, default 29400); the job TCPStore
(`MASTER_PORT`, one port); and the NCCL/Gloo data plane, which binds
listeners in the **ephemeral range (32768–60999) and forms pairwise
connections between all hosts**. A firewall posture that admits torchrun
multi-node must open ephemeral TCP in both directions between all training
hosts — the concrete reason collective training is NAT-hostile and
incompatible with `--network none`.

### 2.3 TensorFlow

**Bootstrap.** One env var, `TF_CONFIG`, holding the *entire* cluster:

```json
{"cluster": {"worker": ["h1:2222", "h2:2222"],
             "ps": ["h3:2222"], "chief": ["h0:2222"]},
 "task": {"type": "worker", "index": 1}}
```

`cluster` is identical on every process; `task` differs. There is **no
launcher and no default port** — a scheduler must spawn every process and
write each one's `TF_CONFIG` (Kubeflow's TFJob and Vertex AI do exactly
this; 2222 is convention). The strategy constructor starts the gRPC server
and parses `TF_CONFIG` **at construction time**, so the variable must be
set before any TF op runs — an ordering constraint an adapter must respect
(you cannot wire distribution after model build, as
`flashruntime.torch.prepare()` does for DDP).

**MultiWorkerMirroredStrategy (MWMS)** — synchronous data parallelism over
`CollectiveOps`: RING (gRPC transport) or NCCL (GPU allreduce only). Two
facts dominate its operational profile:

- **Hang is the default failure mode.** `CommunicationOptions.timeout_seconds`
  defaults to `None`; a collective whose peer died blocks indefinitely.
  A built-in peer health check (enabled by default: 30 s interval, 10 s
  probe timeout, 3 retries) eventually aborts collectives and raises
  `UnavailableError` — deliberately converting the hang into a crash so an
  external scheduler can restart the gang.
- **No elasticity, period.** Membership is fixed when the strategy is
  constructed; there is no TF equivalent of `--nnodes MIN:MAX`. Recovery =
  restart *all* workers, restore from checkpoint; the dataset iterator
  state is explicitly re-initialized, not restored.

**ParameterServerStrategy (PSS)** — the one genuinely churn-tolerant piece
of tf.distribute: a coordinator (`chief`) drives stateless `worker`s and
stateful `ps` servers; `ClusterCoordinator.schedule()` dispatches
`tf.function`s with **at-least-once** semantics — "if the worker … becomes
unavailable … the function will be retried on another available worker,"
and recovered workers get their datasets re-created and rejoin silently.
The asymmetry: a dead *worker* is routine; a dead *PS or coordinator* is
fatal ("not supported to recover from parameter server failure without
restarting the coordinator"). PSS is thus a coordinator-mediated topology
with two SPOFs — architecturally the closest in-framework analog to
FlashML's Mode A, with the lease coordinator playing the PS/chief role but
without the SPOF (SQLite-durable, restartable, and holding no gradient
state).

**Checkpointing.** TensorBundle format: a checkpoint is a *prefix*
(`ckpt-10.index` + `ckpt-10.data-00000-of-00001`…), plus a `checkpoint`
state file written by `CheckpointManager`. Two multi-worker caveats matter
to a manifest layer: saving itself executes collectives (reading
`ON_READ`-synchronized variables allreduces), so **all workers must call
save** — the chief writes the real directory while non-chief workers write
throwaway `workertemp_*` dirs; and `BackupAndRestore` deletes its
checkpoint on successful `fit()` exit. TF's preemption story is the
`PreemptionCheckpointHandler`: on SIGTERM/GCE maintenance notice, one
worker broadcasts a target step, *all* workers run to that step, save
coordinated, and exit 42 for the scheduler to restart — a
coordinated-last-step pattern worth borrowing for spot pools.

**Keras 3's distribution API** (`keras.distribution`: DeviceMesh /
DataParallel / ModelParallel) would in principle cover TF+JAX+PyTorch with
one adapter — but it is **implemented only on the JAX backend**, with the
TF/PyTorch backends "coming soon" for over a year. Conclusion for FlashML:
no shortcut; per-framework adapters remain necessary. (TF's DTensor is
alive but stagnant.)

### 2.4 JAX

**Model.** Multi-controller SPMD: every process runs the same program;
`jax.devices()` is the global device list, `jax.local_devices()` the local
slice; a diverging program *deadlocks* rather than erroring. There is no
launcher; something must start N identical processes and tell each
`jax.distributed.initialize(coordinator_address, num_processes,
process_id)` — with `process_id` forming a **dense 0..N-1 range**, before
any array operation. (On TPU/Slurm/OpenMPI/K8s these auto-detect; generic
GPU/CPU fleets — FlashML's case — must supply them.)

**The coordination service** — process 0 hosts a gRPC service that is
almost a checklist of what a rendezvous provider must offer: a key-value
store (used to assemble the global device topology, to broadcast the NCCL
unique id, and by Orbax for barriers), distributed barriers, and
heartbeat-based health checking (`heartbeat_timeout_seconds` default
100 s; `initialization_timeout` 300 s). Restarted processes carry new
*incarnation* ids, and NCCL communicators are cached keyed by
participant-set + incarnations. Failure semantics are **fate-sharing**: any
process's death kills the job, and "if process 0 fails, every process will
fail, even with fate-sharing disabled" — the coordinator is an absolute
SPOF. An experimental, GPU-only recoverability mode
(`jax_enable_recoverability`, `live_devices` barrier-semantics context
manager) exists as of the 0.8 era; TPU users are pointed at Pathways,
GCP's proprietary single-controller layer, for elastic shrink/regrow. OSS
JAX's practical recovery story is therefore identical to MWMS: gang
restart from checkpoint.

**Sharding.** `Mesh` + `NamedSharding`/`jax.P` under `jit`: the compiler
partitions computation to match data sharding and inserts collectives
(Shardy is the default partitioner since 0.7.1; GSPMD is legacy). The APIs
are process-count-agnostic — the same program spans hosts. Per-host input
loading via `jax.make_array_from_process_local_data`. On CPU, collectives
run over **Gloo** (`jax_cpu_collectives_implementation=gloo`) — which
makes multi-process JAX testable on the M4 development machine with no
GPU, a fact the implementation plan leans on.

**Orbax checkpointing** — async by default: a blocking phase
(device-to-host copy + shard deduplication) returns control to training,
then a background thread persists; `wait_until_finished()` joins it.
Save-time reductions from async: ~40% at 300M params to ~97% at 340B
(vendor-measured). **Atomicity is a commit protocol**: write to a temp
path, finalize by atomic rename — or, on stores without atomic rename, by
writing a `commit_success.txt` marker — and "an unfinalized directory is
never considered a valid checkpoint." That is the same parts-first /
commit-marker-last contract as FlashML's manifest
(`checkpoint/local.py`), so the integration is composition, not conflict:
after `wait_until_finished()`, hash the finalized directory and write the
FlashML manifest. Restore is driven by an abstract state tree independent
of the saving topology — arbitrary topology changes on load, like DCP.

### 2.5 The meta-layer: Transformers, accelerate, DeepSpeed — and the others

**Trainer = accelerate.** Since transformers v4.30.0 the Trainer's
distributed internals *are* accelerate (`create_accelerator()`; DDP, FSDP,
DeepSpeed, autocast all delegated). The Trainer is launcher-agnostic — it
reads whatever `RANK`/`WORLD_SIZE`/`LOCAL_RANK` the launcher exported, and
`deepspeed …`, `torchrun …`, and `accelerate launch …` are documented as
equivalent. `accelerate launch` itself *wraps torchrun* (exposing
`--rdzv_backend`, `--max_restarts`, …) or the DeepSpeed launcher or MPI.
The ecosystem has converged on torchrun's env contract as the substrate —
which is exactly the contract FlashML's `CommandWorkload` argv+env model
and `LaunchSpec.env` already carry, and why
`workloads/command.py:98` already classifies an `accelerate` argv as
`coordinated` despite no adapter emitting one yet.

**Checkpoint anatomy.** A Trainer `checkpoint-<step>/` holds
`model.safetensors`, `optimizer.pt`, `scheduler.pt`, `trainer_state.json`,
`training_args.bin`, per-rank `rng_state_{rank}.pth`, and tokenizer files;
`resume_from_checkpoint` restores all of it plus dataloader position.
Writes are gated on `is_world_process_zero` **except** where the format is
per-rank-sharded: DeepSpeed ZeRO (every rank writes
`*_zero_pp_rank_*_optim_states.pt` under `global_step<N>/`, consolidated
offline by the auto-emitted `zero_to_fp32.py`) and FSDP
`SHARDED_STATE_DICT`. Sharded formats couple the checkpoint to the world
size unless a conversion layer intervenes (zero_to_fp32, DeepSpeed
Universal Checkpointing, Megatron dist-ckpt, DCP resharding) — a
constraint FlashML's topology-compatible `latest_valid()` logic will need
to encode per framework. PEFT/LoRA shrinks checkpoints to
adapter-only megabytes (`adapter_model.safetensors` +
`adapter_config.json`) while resume still requires the optimizer/RNG/step
machinery — the cheapest possible resumable workload, and the right first
recipe for volunteer-adjacent pools.

**DeepSpeed specifics an adapter must know.** ZeRO-1/2 cost the same
communication volume as DDP; ZeRO-3 ~1.5× with latency-sensitive parameter
allgathers on the critical path (ZeRO++ claws back 4× via quantization +
hierarchy) — so ZeRO-3 is the *most* WAN-hostile strategy in common use,
consistent with FlashML's planner treating `zero3_cpu_offload` as a
capacity fallback, not a distribution strategy. DeepSpeed's native
multi-node launcher assumes pdsh over passwordless SSH + a hostfile — a
posture FlashML must not adopt; its `--no_ssh` torchrun-style mode is the
integration path. `ds_config.json` arrives via file, which maps onto
`LaunchSpec.files` (with the noted follow-up that files land in the output
dir, not the cwd — resolved by passing absolute paths in argv).

**The others, briefly.** *Horovod*: ring-allreduce with an elastic mode
whose design (in-memory `state.commit()` / rollback / rebroadcast from new
rank 0 on membership change) remains the cleanest no-checkpoint recovery
ever shipped — but the project is dormant (last release v0.28.1,
2023-06-12; "Inactive" per Snyk). *Ray Train*: gang-scheduled worker groups
on placement groups; any failure restarts the whole group from checkpoint
(`FailureConfig(max_failures)`); Train V2 adds restart-based elastic
resizing (`num_workers=(min, max)`); node death is declared by GCS health
probes after ~30 s; the head node is an SPOF without external Redis.
*Lightning*: strategies + ClusterEnvironments that detect external
launchers; Fabric is the Trainer-less wrapper. *Megatron*: a static
TP×PP×DP×EP×CP grid fixed at launch, no elasticity, dist-ckpt for
layout-shifted reload — the datacenter pole of the design space.

### 2.6 Comparison

| | PyTorch | TensorFlow | JAX (OSS) | HF stack (meta) |
|---|---|---|---|---|
| **Rendezvous** | TCPStore via `MASTER_ADDR/PORT`; torchrun c10d dynamic rendezvous | none — full cluster baked into `TF_CONFIG` per process | coordination service on process 0; dense `process_id` required | delegates to torchrun env contract |
| **Who spawns N processes** | `torchrun` (ships with framework) | **nobody** — scheduler's job | **nobody** — scheduler's job | `accelerate launch` → torchrun/DeepSpeed |
| **Controller model** | multi-controller SPMD | multi-controller (MWMS) / single-coordinator (PSS) | multi-controller SPMD (single-controller = proprietary Pathways) | inherits PyTorch |
| **Dead worker (collective)** | hang → watchdog abort (10 min default) → gang restart | hang by default → health-check abort (~30–60 s) → gang restart | fate-sharing kill-all (100 s heartbeat) → gang restart | inherits |
| **Worker churn tolerated?** | restart-based elastic (`--nnodes MIN:MAX`, ranks unstable); TorchFT = per-step, experimental | MWMS no; PSS workers yes (at-least-once), PS/chief no | no (experimental GPU recoverability; Pathways on GCP) | Ray Train V2 restart-elastic; Horovod elastic (dormant) |
| **Checkpoint format** | torch.save / DCP (per-rank shards + resharding; safetensors interop) | TensorBundle prefix (`.index` + data shards); all workers participate in save | Orbax OCDBT, async, commit-marker atomicity, reshardable | HF `checkpoint-*/` dir; ZeRO per-rank shards + converters |
| **Coordinator SPOF** | rank 0 store (job dies, restartable) | PSS chief + every PS | process 0, absolute | inherits |
| **What FlashML must provide** | address/port assignment for `nnodes>1` (launcher exists) | process spawning + per-process `TF_CONFIG` + backstop timeouts | process spawning + coordinator address + dense process ids | argv emission only (adapter, not launcher) |

The last row is the punchline of Part I: for PyTorch, FlashML fills in
addresses; for TF and JAX, FlashML *is* the launcher. A framework-neutral
group launcher — spawn N processes, assign rank identities, render each
framework's env dialect (`MASTER_ADDR`/`MASTER_PORT` vs `TF_CONFIG` vs
`initialize()` args), enforce a wall-clock barrier timeout as the backstop
none of the frameworks reliably provides — is the shared foundation, and
each adapter on top of it is small.
---

## 3. Part II — The platform landscape

### 3.1 A taxonomy

Every platform surveyed fits one of five archetypes, distinguished by two
variables: *who owns the machines* and *which data plane they can run*.

| Archetype | Examples | Machines | Data plane possible |
|---|---|---|---|
| Datacenter cluster | CoreWeave, Lambda, Megatron/Slurm shops | operator-owned, InfiniBand | full collective (TP/PP/DP) |
| GPU cloud / marketplace | RunPod, Vast.ai, Salad, io.net, Akash | third-party hosts, vetted to varying degrees | collective only inside one host or one provisioned cluster |
| Federated platform | Flower, Google FL, NVFlare, FedML | data owners' machines, never pooled | coordinator-mediated only |
| Volunteer computing | BOINC, Folding@home | anonymous public, NAT-ed | independent tasks only |
| Decentralized training network | Prime Intellect, Nous Psyche, Hivemind runs | mixed: datacenter islands + some consumer | coordinator-mediated / bounded-staleness collective hybrids |

FlashML spans an unusual three of these: it operates like a federated
platform (dial-out coordinator, quorum FedAvg), aspires to marketplace
economics (volunteer argv tier, planned cloud pools), and its e2e loop is
volunteer-computing-shaped (leases, deadlines, idempotent commit). No
surveyed competitor spans more than two.

### 3.2 Federated-learning platforms

**Flower (Flower Labs — the named competitor).** Architecture: a
long-running **SuperLink** (server) and **SuperNodes** (client daemons),
with short-lived per-project **ServerApp**/**ClientApp** processes launched
by a SuperExec — multiple projects multiplex one federation. The SuperLink
is, precisely, a message broker with persistence (SQLite-backed state):
SuperNodes *pull* task messages and *push* results over the Fleet API
(gRPC request-response); even the ServerApp talks to the broker as a
client. Consequence, from their own network reference: "only outgoing
connections are necessary" — every participant dials out; hospitals and
homes open no inbound ports. Liveness: registration (`CreateNode` →
`node_id`) plus 30 s heartbeats, eviction after two missed (60 s). Node
auth (since 1.9): per-node EC keypair against a server-side allowlist,
ECDH+HMAC per connection. Rounds: strategies sample
`fraction_fit` of available clients (floor `min_fit_clients`, block on
`min_available_clients`) and by default **aggregate whatever subset
succeeds** (`accept_failures=True`). SecAgg+ ships. Simulation engine
(Ray-based) runs the same ClientApp code as deployment, with experiments
claimed to 15M virtual clients. Business: $20M Series A (Feb 2024, Felicis,
~$100M valuation; YC; angels include Clem Delangue and Scott Chacon); no
Series B found as of Aug 2026.

The result that matters most to this paper is **Photon / FlowerLLM**
(MLSys 2025): federated **end-to-end LLM pre-training** — 1.3B (2023), 7B
(Oct 2024), 13B ongoing — reporting 64–512× communication-volume reduction
vs data-parallel, the 7B run's communication time cut ~1000× (97.2 h →
0.1 h), ~35% *faster* wall-clock to equal perplexity than the centralized
baseline, and >2× throughput at 13B under ~3 Gbps inter-site links
(vendor-reported). Coordinator-mediated training is not a consolation
prize; at these scales, with the right algorithm, it is competitive.

**Google's production system** (Bonawitz et al., 2019 — the design the
industry copies). Anonymous devices **check in** when eligible (idle +
charging + unmetered); there is no persistent registration. **Pace
steering** tells each device when to check in next — synchronizing arrivals
for small populations, smearing them for large ones — absorbing a 4×
diurnal swing. A round is Selection (tens of thousands check in, a few
hundred are chosen) → Configuration (versioned plan + checkpoint pushed) →
Reporting (a time window; stragglers past it are ignored; below quorum the
round is abandoned). The number FlashML should steal: the server
**over-selects 130%** of the target count against an observed 6–10%
dropout. Server side is an actor tree (Coordinator / Selectors / Master
Aggregator / Aggregators) holding round state only in memory — an
aggregator crash loses only its devices' updates. Production scale:
~10M-device fleet, tested at 10K participants/round; a 1.4M-parameter
next-word model converged in ~3,000 rounds over 5 days across 1.5M
devices. Meta's **Papaya** (MLSys 2022) reuses the same actor taxonomy and
adds **FedBuff** async aggregation — a buffer of K≈10 staleness-weighted
updates replaces the round barrier entirely; at high concurrency async is
5× faster with ~8× less communication (vendor-reported).

**NVFlare (NVIDIA).** Cross-silo pole: provisioned identities via
password-protected startup kits (per-party mTLS PKI); clients dial out;
an Overseer service with agent heartbeats handles hot/cold server failover.
Deployment proof: the EXAM COVID model — 20 hospitals, five continents,
AUC 0.94 in two weeks, ~16% average AUC gain over local-only models
(Nature Medicine 2021). **OpenFL** (Intel/LF): Director/Envoy long-lived
processes, step-ca PKI, fixed federations where a missing collaborator
stalls the round. **FATE** (WeBank): FATE-Flow pipelines, EggRoll compute,
cross-party traffic through a neutral exchange node, Paillier HE, vertical
FL; party outage fails the job. **Substra** (Owkin/LF): per-org backends,
central orchestrator holding only metadata, traceability-first (MELLODDY:
10 pharma companies). **IBM FL**: aggregator + parties with a fusion
library and per-handler quorum/timeouts. **FedML → TensorOpera** is the
cautionary/confirming tale: an FL framework that pivoted into a
cross-cloud GPU scheduler ("run any AI job on any GPU cloud … decentralized
GPUs, multi-clouds, edge servers, and smartphones") — the same direction of
travel as flashml-cloud, from the opposite starting point.

**The cross-silo / cross-device split** organizes everything above:

| | Cross-silo | Cross-device |
|---|---|---|
| Population | 2–100 organizations | up to ~100M devices (Meta) |
| Identity | provisioned PKI, addressable, stateful | anonymous, stateless check-ins |
| Availability | ~always on; dropout is an incident | eligibility windows; 6–10% round dropout is *baseline* |
| Links | datacenter-grade | consumer uplink-bound |
| Dropout handling | retry/stall/HA failover | priced in statistically: over-selection, buffers, DP |

FlashML's volunteer pool sits between the poles — registered (join codes,
future per-node identity) but churny — and its FedAvg driver already
implements the cross-device answer (quorum + discard-late), while its
nascent cloud pools will behave like silos. The topology axis (§5.3) is
what lets one runtime serve both.
### 3.3 GPU clouds, marketplaces, and DePIN networks

**RunPod (the named competitor — and FlashML's own GPU vendor for the
2×4090 validation).** Two supply pools: Secure Cloud (vetted datacenters)
and Community Cloud (peer-to-peer hosts) — and the docs now state plainly
that **RunPod is no longer accepting new Community Cloud hosts**: the
marketplace intake is closed, and the company is converging on vetted
datacenter capacity. Its multi-node product, **Instant Clusters**, is
maximally instructive: 2–8 nodes (16–64 GPUs) self-serve, **inside a
single datacenter** on 1,600–3,200 Gbit/s InfiniBand/RoCE, allocated as a
static gang with injected env (`MASTER_ADDR`, `NODE_RANK`, `WORLD_SIZE`,
…), `NCCL_SOCKET_IFNAME=ens1` required, and — remarkably — **launch is
manual**: the docs instruct running `torchrun` in each pod's web terminal
individually. Meanwhile their **Global Networking** overlay connects pods
*across* 17 datacenters at **100 Mbit/s** — explicitly for microservices
and coordination, not training. One vendor, two networks, ~30,000× apart:
the cleanest confirmation of §4.1's partition function, from a company
that sells both. Also notable for FlashML: gang hardware + hand-rolled
launch means *orchestration on top of Instant-Cluster-class capacity is an
unfilled gap* — precisely where a runtime that plans, launches, observes,
and recovers adds value.

**Vast.ai.** The pure marketplace: hosts run a daemon and list machines at
self-set prices; rentals are Docker containers on hardware the host still
owns. Its coping mechanisms are the interesting part: a published
**reliability score** (uptime/interruption history) and **DLPerf**
synthetic benchmark rank the market; **interruptible rentals** are a
live bidding game (highest bid runs, others pause; on-demand always
preempts); NAT is handled by graceful degradation (proxy SSH via Vast's
relay for closed hosts, direct SSH preferred for open ones). Multi-node
arrived in 2026 as **overlay networks over physical clusters only**
(machines that share a datacenter LAN; RoCE/IB gated to A100/H100/H200) —
again, no cross-site training. Third-party review (ClusterMAX) rates it
Bronze, citing "unpredictable" reliability as "characteristic of the
aggregator model." The trust posture is candid: supply ranges "from tier 4
datacenters down to individual hobbyists," containers are not a hardware
trust boundary, and the defense offered for community hosts is
reputational, not technical.

**SaladCloud.** The consumer pole: gamers' PCs running a desktop agent,
selected into container groups by a proprietary per-node **trust rating**
plus heartbeats, with four preemption-ordered priority tiers. Salad's own
published numbers set the churn baseline for any consumer fleet: **90–95%
individual node reliability; ~35 hours average uptime before interruption;
no warning**. Networking is outbound-only (residential IPs; optional
inbound HTTP via an IPv6 gateway with a ~100 s response timeout). There is
no multi-node training story in their docs at all — for a platform of this
scale, the absence *is* the finding.

**io.net.** The cautionary DePIN tale. A Ray-fork orchestration layer over
a claimed mesh-VPN of aggregated GPUs — and the site of the April 2024
spoofing incident: exposed tokens plus metadata injection let airdrop
farmers register **~1.8M fake GPUs** ("an RTX 4090 split into infinite
virtual GPUs"). Remediation is now hourly proof-of-work per GPU,
time-lock checks, staking, and uptime minimums — and Messari's Q1-2025
audit counts **~6,720 verified daily GPUs**, roughly 2% of
registered-supply headlines. The business has drifted to an inference API
(the workload its topology can serve). Verdict for real multi-node
training: not credible today; Ray gives scheduling, not gradient-exchange
bandwidth.

**Akash.** On-chain reverse auction: tenants publish SDL manifests,
providers' bid engines compete, a lease settles in escrow, and the
provider daemon deploys onto Kubernetes *it* operates. Host trust =
third-party **audited attributes** signed on-chain — attestation of
claims, not measurement. A lease is scoped to one provider's cluster; no
cross-provider training fabric exists or is claimed.

**Gensyn.** The verification-first player (§4.6's rung 3). The Verde
paper's numbers are the state of the art for verified training:
checkpoint-hash bisection re-executes <6% of training at 20 checkpoints
(<1.1% at 100), then graph-level bisection isolates a single operator for
referee recompute (~100× cheaper than a step); RepOps makes results
bitwise-reproducible across GPUs at measured overheads from ~30%
(matmuls) to ~370% (some training workloads) — total <10× vs ~10,000× for
zk. Two limits matter: it assumes **single-GPU execution per provider**
(multi-GPU is future work), and the shipped mainnet product (Apr 2026) is
Delphi, a prediction market — verified *training* at scale remains
unshipped by everyone.

**Prime Intellect (marketplace side)** aggregates 50+ clouds into one
interface with spot sell-back and multi-node H100 clusters to 256 GPUs on
vetted datacenters — the pitch is a *reliability layer over brokers*,
citing a customer finding that "reliability of multi-node clusters between
providers can vary by up to 100x." **Petals** rounds out the archetype:
volunteer servers announce transformer-block ranges to a DHT, clients
beam-search a minimum-latency pipeline, servers rebalance toward
bottleneck layers, NAT-ed servers ride libp2p circuit relays — and the
security wiki is admirably blunt: peers can read and modify what flows
through them, there is no output verification, use a private swarm for
anything sensitive.

**The contrast case** — what the datacenter pole looks like when bought
rather than built: CoreWeave's GB200 NVL72 racks (400 Gbit/s
InfiniBand *per GPU*, SHARP in-network reductions, to 110K GPUs) and
Lambda's 1-Click Clusters (16–512 H100s, non-blocking rail-optimized
fabric). **Together AI is the strategic datapoint**: the company that
began in decentralized-training research ended up co-building ~36,000
GB200s — when decentralized-training economics met flagship-model
quality, it bought InfiniBand (the same arc as INTELLECT-3, §3.5).
**SkyPilot** shows the broker-plus-checkpoint-restart pattern FlashML's
SDK recovery already implements (detect preemption via termination
notice, reprovision across clouds, resume from checkpoint); **dstack**
and **Determined** (HPE) are the trusted-cluster control planes — the
assumption set every system in this section had to abandon.

### 3.4 Volunteer computing — the twenty-year-old syllabus

**BOINC** is the canonical design, and reading it against
`leases/manager.py` is an exercise in convergent evolution. Server side: a
work generator creates workunits; a **feeder** stages them in shared
memory so the scheduler never touches the DB per request; the
**transitioner** runs the state machine — dispatch stamps
`report_deadline = now + delay_bound` (a lease), timeouts flip results to
`NO_REPLY` and regenerate instances (expiry-sweep requeue); the
**validator** implements redundancy: replicate each workunit
`target_nresults` ways, find a consensus set among successes
(application-supplied fuzzy compare), designate a canonical result, grant
credit only on validation. **Adaptive replication** is the economics fix
FlashML's §6.6 should copy directly: track consecutive valid results
`CV(host)`; below 10 the host is untrusted and always replicated; above,
skip replication with probability `1 − 1/CV` (spot-checks forever), so the
duplication tax falls to ~1.1× for proven hosts and any invalid result
resets trust to zero. **Homogeneous redundancy** solves cross-hardware
float divergence by comparing replicas only within numerical equivalence
classes — the statistical answer to the problem Gensyn's RepOps solves
deterministically at 1.3–4× runtime cost. Client side is pure dial-out
HTTP with **random exponential backoff** (2^N with jitter on failures; a
1-day-capped work-fetch backoff) — the 2002 answer to NAT and thundering
herds that Flower, Salad, and FlashML all reinvented. Scale arc: SETI@home
peaked at 5.2M registered participants; BOINC's 2019-era ~93 PFLOPS claim
has decayed to ~20–26 PFLOPS across ~89K active computers today — volunteer
*supply* is a wasting asset without a demand-side product, a strategic
lesson in itself.

**Folding@home** differs in three instructive ways: a two-tier
**assignment-server → work-server** split (route by client capability,
then dispatch); deadlines derived from benchmark runtime
(`timeout = 20 × daysPerWU + 2` days); and the **Quick Return Bonus** —
`points × sqrt(k × deadline / elapsed)` — a *latency* incentive where
BOINC pays for throughput, directly relevant to any FlashML pool where
round time (not total work) is the scarce good. Verification is
physics-based sanity checking rather than replication, exploiting the
domain (implausible MD energies are detectable) — a reminder that
`ArtifactCorruption`-style output validation can sometimes replace
redundancy when the workload allows. The 2020 COVID surge to **~2.4
exaFLOPS** (faster than the top-500 supercomputers combined, on
embarrassingly parallel work) remains the existence proof for volunteer
scale — on the right topology.

### 3.5 Decentralized-training networks

These are the organizations actually running multi-node training over real
networks — the closest existing implementations of what "FlashML multi-node
through networks" would mean at ambition's limit.

**Prime Intellect.** The reference lineage:

- *OpenDiLoCo* (Jul 2024): DiLoCo reimplemented on Hivemind's DHT; 1.1B
  parameters trained across 3 countries at 90–95% compute utilization.
- *INTELLECT-1* (Nov 2024): the first 10B decentralized run — 1T tokens,
  42 days, up to 14 concurrent nodes (112 H100s) from 30 providers on 3
  continents, with nodes joining and leaving mid-run. The `prime`
  framework's mechanisms map almost one-to-one onto FlashML concepts:
  an **ElasticDeviceMesh** (dynamic global process group over WAN +
  local FSDP2 group per node) with **2 s heartbeats and eviction after
  6 s** (a lease, in FlashML vocabulary); failed all-reduces retried with
  dead nodes excluded; **H=100 inner steps** (~38 min compute) between
  outer syncs whose ring-allreduce medians were 103 s (US), 382 s
  (US+EU), 469 s (global) — 2–10% overhead; int8 pseudo-gradient
  quantization for a combined **400× bandwidth reduction vs DP**; joining
  nodes block and download the full checkpoint from peers (30–60 min).
  Networking: Tailscale VPN + parallel TCP streams. Results: 83% compute
  utilization globally, 96% US-only (vendor-reported).
- *PCCL*: a collective-communications library over plain TCP whose
  collectives tolerate **join/leave during ongoing operations** — 45 Gbit/s
  across Western Europe, 25 Gbit/s transatlantic (vendor-reported).
- *INTELLECT-2* (May 2025): 32B decentralized **RL** — a trusted central
  training cluster plus a permissionless inference swarm (4×RTX 3090
  admits a worker) generating rollouts; fully async with a two-step policy
  lag (reward curves match synchronous up to four steps); **SHARDCAST**
  broadcasts 62 GB of weights over an HTTP relay tree in ~14 min;
  **TOPLOC** activation commitments verify the untrusted rollouts (§4.6).
- *INTELLECT-3* (Nov 2025): a 106B MoE — trained, notably, on a
  **centralized 512×H200 cluster**, with the decentralized machinery
  serving the rollout/protocol side. An honest datapoint: when flagship
  quality was at stake, training consolidated; the decentralized value
  concentrated in the *permissionless inference* and *verification*
  layers.

**Nous Research.** The algorithmic pole. *DeMo* (with Diederik Kingma):
never allreduce full gradients — exchange only top-k DCT components of
momentum, with the untransmitted remainder retained locally as error
feedback; ~85× less data per GPU at 300M/1B scales. *DisTrO*: the
engineering line of the same idea — 1.2B pretraining with per-step
communication cut from 74.4 GB to 86.8 MB (857×). *Psyche* is the network:
a **coordinator implemented as a Solana smart contract** (run state
machine, participant list, randomness for data assignment and witness
election), epochs of ~500 steps with a Warmup → Training → **Witness**
(randomly elected witnesses attest peer liveness/correctness via Bloom
filters; a witness quorum advances the epoch) → Cooldown (checkpoint to
HuggingFace) lifecycle; join/leave at epoch boundaries only. Transport is
**iroh** (QUIC, Ed25519 peer identities, ~90% direct connections with
relay fallback). Production result: **Hermes 4.3 (Dec 2025) — the first
production model post-trained entirely on the decentralized network
(144K tokens/s across 24 nodes), and the Psyche-trained version
outperformed an identical centrally-trained control run** (vendor-reported
A/B). The 40B Consilience pretrain completed on testnet in Sept 2025.

**The Hivemind lineage** (the academic root both of the above draw on):
a Kademlia DHT for peer discovery over libp2p with circuit relays for
NAT-ed peers; *Moshpit SGD* — averaging in small reshuffled groups so a
failed peer spoils only its group's round, not the global step; *DeDLOC /
sahajBERT* — the one genuinely consumer-volunteer training run on record
(40 volunteers, 91 devices, mostly behind NAT via relays, ~8 days to a
near-SOTA Bengali ALBERT); and *SWARM parallelism* — stochastic pipeline
wiring over pools of unreliable heterogeneous peers (~1B effective-13B
model on preemptible T4s under 200 Mb/s links), built on the "square-cube"
observation that larger models are *relatively* cheaper to communicate.

Three cross-cutting lessons from this archetype:

1. **Nobody runs naive synchronous DP over WAN.** Every working system
   reduces synchronization *frequency* first (DiLoCo's H, Psyche's epochs,
   FedAvg's rounds) and stacks quantization second. Per-step gradient
   compression alone was tried and abandoned (§4.3).
2. **Membership is epoch- or round-granular.** Join/leave happens at sync
   boundaries (Psyche epochs, INTELLECT-1's outer steps, FedAvg rounds) —
   which is exactly the granularity FlashML's lease/round machinery
   already provides. Nobody admits a node mid-collective.
3. **The real fleet is datacenter islands, not laptops** — 14×8-H100
   nodes (INTELLECT-1), 24–50 nodes (Psyche runs), ≥4×3090 workers
   (INTELLECT-2's floor). Consumer uplinks (§4.5) keep true
   laptop-swarm *training* out of reach for LLM-scale models; sahajBERT
   is the exception that calibrates the ceiling (ALBERT-large, 8 days).
### 3.6 Node lifecycle across the landscape

How each system answers three questions: *how do machines join, how is
liveness tracked, and what happens when one goes offline?*

| System | Join | Liveness | Node goes offline |
|---|---|---|---|
| torchrun (PyTorch) | rendezvous barrier (min:max, 600 s join) | agent monitors workers (0.1 s) | kill ALL workers; re-rendezvous; restart from ckpt |
| TF MWMS | fixed TF_CONFIG at start | 30 s peer health probes | UnavailableError → external gang restart |
| TF PSS | workers dial coordinator | gRPC fail-fast signals | worker: retried at-least-once elsewhere; PS/chief: fatal |
| JAX OSS | dense process_id at init | 100 s heartbeats (coordination svc) | fate-sharing: everyone dies; restart from ckpt |
| TorchFT | replica groups join Lighthouse | per-step heartbeats, quorum | shrink world, discard batch, continue; rejoin via peer state |
| Ray Train | gang via placement group | GCS probes (~30 s to declare dead) | whole group restarts from ckpt (V2: at new size) |
| Slurm/Megatron pole | static allocation | scheduler node health | requeue whole job |
| **Flower** | CreateNode → node_id; keypair allowlist | 30 s pings; evict after 2 missed | round aggregates the survivors (`accept_failures`) |
| **Google FL** | anonymous check-in + pace steering | reporting window per round | over-select 130%; ignore stragglers; abandon below quorum |
| NVFlare | provisioned startup kits, mTLS, dial-out | Overseer heartbeats | server: hot/cold failover; client: proceed with responders |
| **BOINC** | attach to project; dial-out HTTP | deadlines, not heartbeats | `NO_REPLY` at deadline → regenerate instance; exp. backoff |
| Folding@home | AS routes to WS by capability | WU timeout/deadline | reassign at timeout; QRB rewards fast return |
| Salad | desktop agent; trust rating | heartbeats | auto-reallocate replica (90–95% node reliability, ~35 h uptime) |
| Vast.ai | host daemon lists machine | reliability score (history) | interruptible paused/preempted; score absorbs it |
| RunPod Instant Clusters | static gang, one DC | n/a (single allocation) | cluster degraded; user relaunches |
| io.net | launch binary + PoW verification | hourly PoW + uptime minimums | rewards slashed; cluster re-formed |
| Akash | provider bids on-chain | lease + escrow | lease ends; tenant redeploys |
| **INTELLECT-1** | node joins at outer-step boundary; ckpt from peers | 2 s heartbeats, evict at 6 s | drop from mesh; retry all-reduce without it |
| **Psyche** | join at epoch boundary; on-chain roster | witness attestations (Bloom filters) per epoch | witness quorum advances epoch without it |
| Petals | announce blocks to DHT | throughput self-reports; self-ping | clients route around; swarm rebalances layers |
| **FlashML today** | register (join code) + dial-out claim | node + attempt heartbeats; lease deadlines | lease expires → sweep requeues task; FedAvg round drops it under quorum |

Three families are visible: *barrier-and-restart* (the framework and
datacenter rows), *round-quorum* (the federated/decentralized rows —
Flower, Google, BOINC, INTELLECT-1, Psyche, FlashML), and
*market-reputation* (Vast, Salad, io.net — where liveness is priced
rather than handled). FlashML's machinery is already firmly in the second
family — the family §3.5's evidence says actually ships WAN training — and
the §5.3 topology axis is what will let it *also* speak the first family's
protocol on trusted pools without confusing the two.

Four cross-cutting observations close Part II:

1. **Bandwidth is the partition function.** Every system supporting real
   synchronous multi-node training does it inside one datacenter with
   RDMA; everything spanning the internet restricts itself to independent
   work, round-based aggregation, or inference pipelines.
2. **Dial-out is the universal NAT answer, and it is old.** BOINC's
   client-initiated HTTP with exponential backoff (2002) reappears as
   Flower's pull messaging, Salad's outbound-only agents, Petals' relays —
   and FlashML's executor client. Only vetted-host systems require
   inbound reachability.
3. **Two verification families** — statistical (BOINC quorum + adaptive
   replication; F@h sanity checks) and cryptographic-economic (Verde
   refereed delegation; TOPLOC commitments) — solving the same economics
   problem fifteen years apart: pay for verification only where trust is
   low.
4. **Registered ≠ active.** io.net's 1.8M spoofed registrations vs ~6.7K
   verified daily GPUs; Salad's 450K providers vs 60K daily; BOINC's 93 →
   ~25 PFLOPS decline; RunPod closing community intake. Every supply
   figure in this market is a claim until tied to a verification method —
   a caution that applies to any future FlashML volunteer-pool marketing,
   and an argument for building the verification story (§6.6) before the
   supply story.
---

## 4. Part III — The physics of the network, and what it costs to leave the datacenter

### 4.1 The bandwidth hierarchy

Every design decision surveyed in Parts I–II is downstream of one ladder:

| Link | Bandwidth (per node/GPU, order-of-magnitude) | Latency |
|---|---|---|
| NVLink 5 (B200, intra-node) | ~900 GB/s unidirectional | ns–µs |
| NVLink 4 (H100, intra-node) | ~450 GB/s | µs |
| DGX H100 inter-node (8× ConnectX-7) | ~400 Gbit/s × 8 ≈ 400 GB/s/node | µs (RDMA) |
| Commodity DC ethernet | 10–100 Gbit/s | 10s of µs |
| Leased WAN / inter-region | 1–10 Gbit/s effective | 30–150 ms RTT |
| Consumer broadband uplink | **20–60 Mbit/s** (US median up ~58 Mbit/s, 2026; global median down 98 Mbit/s) | 10–50 ms + jitter |

Top to bottom is four to five orders of magnitude. FlashML's pools live on
the bottom three rungs.

### 4.2 The allreduce cost model, and where synchronous DP dies

Ring allreduce moves `2(n−1)/n × G ≈ 2G` bytes per worker per step, each
direction, where G is the gradient payload (4 bytes/param fp32, 2 bf16).
Concretely: a 1B-parameter model is ~8 GB sent + 8 GB received per step in
fp32 (~4+4 GB bf16); a 7B model ~56 GB each way (~28 GB bf16). Training is
healthy while communication (overlappable with backward) stays under
compute time — a published worked example puts a 2B fp32 model on 32 GPUs
at 0.42 s compute/step: network-bound at 200 Gbit/s (0.64 s sync),
compute-bound at 400 Gbit/s (0.32 s). Two empirical anchors from Part I–II:
a 1B model doing HSDP over TCP-only ethernet ran ~9–11 s/step (TorchFT
run); and DeepMind's simulation finds that holding ~95% utilization needs
**100–300 Gbit/s for plain DP (growing with model size) versus a roughly
constant 1–5 Gbit/s under Streaming DiLoCo across 1B/10B/100B**.

Read against the ladder: synchronous DP is a datacenter-only algorithm;
Streaming-DiLoCo-class methods reach leased-WAN links between GPU islands;
*nothing* published reaches consumer uplinks for LLM-scale training. And a
coordinator that relays the data plane (a FastAPI process at ~1 GB/s,
serialized) sits ~2–3 orders below a ring — which is why "DDP through the
server" is not a configuration but a category error, and why FlashML's
FedAvg driver exchanges *round deltas*, not gradients.

### 4.3 The escape hatches, ranked by what they save

**(a) Reduce synchronization frequency — the primary lever.** DiLoCo: H
local AdamW steps per worker, then one allreduce of the *pseudo-gradient*
(θ_before − θ_after) applied by an outer Nesterov optimizer; H=500 →
500× fewer communication rounds at matching quality (8 workers, C4). The
scaling-laws follow-up (Mar 2025) found well-tuned DiLoCo *scales better
than DP* as models grow — larger optimal batch, better eval loss at fixed
tokens — moving the technique from "acceptable degradation" to "arguably
free." Streaming DiLoCo adds per-fragment staggered sync (~8× peak
bandwidth), τ-step overlap of communication under compute, and FP4
pseudo-gradients: ~400× fewer bits total. INTELLECT-1's production stack
(H=100 × int8) is the same multiplication. FedAvg is this lever's
cross-device limit case (rounds of minutes-to-hours). FlashML relevance:
the FedAvg driver's round loop *is* an outer optimizer with H=local-epochs
and plain averaging — DiLoCo is a parameterization away (outer Nesterov +
pseudo-gradients), not a new architecture.

**(b) Bounded staleness — hide the latency you cannot remove.** The
datacenter verdict on unbounded async (DistBelief → Hogwild → PS) was
negative: staleness grows with worker count and sync+backup-workers won
(Chen et al., 2016). Over WAN the barrier is unaffordable, so staleness
returned *bounded and measured*: FedBuff's K-update buffer with staleness
weighting (Meta production); INTELLECT-2's rollouts from ≤2–4-step-old
policies; Psyche training step n+1 while step n's results download;
Streaming DiLoCo's τ-late fragment merges. Design rule: async between
islands, sync within them.

**(c) Compress what still moves — multiplicative, not primary.** PowerSGD
(rank-r + error feedback; the DDP comm hook; used for DALL-E), DGC (top
0.1% coordinates, 270–600×), 1-bit Adam (~5× volume), DeMo/DisTrO (top-k
DCT of momentum, 85–857×). The honest systems result: per-step compression
does not change *frequency* — you still pay a WAN RTT and a straggler tax
every step — and datacenter studies find it often loses to well-overlapped
uncompressed allreduce. Hence the field's pivot to (a) with (c) stacked on
top.

**(d) Put the pipeline, not the batch, across the WAN.** SWARM/Petals-style
stochastic pipelines route activations (small relative to weights at large
width — the square-cube law) through pools of unreliable peers with
per-microbatch rerouting and stage rebalancing. Proven at ~1B on
preemptible T4s under 200 Mb/s. The most volunteer-shaped of the training
topologies, and the least production-proven.

### 4.4 Churn: what a dead node costs each design

Three regimes, now with their production statistics:

**Gang (all-or-nothing).** MWMS, OSS JAX, Megatron, Ray Train, torchrun:
one death = whole-group restart from checkpoint. The wild failure data
says how often: Meta's Llama-3 405B pretrain logged **466 interruptions in
54 days on 16,384 H100s (419 unexpected, 78% hardware — one every ~3 h)**;
Meta's fleet study measures MTTF 47.7 days at 8 GPUs falling to **7.9 h at
1,024 GPUs and a projected ~14 min at 131,072**; ByteDance production
attributes 55.8% of faults to hardware (ECC 38.9%) and found 42.5% of
jobs straggler-affected; hardware failures were 0.2% of Meta's jobs but
**18.7% of all runtime**. Gang recovery economics are therefore a
first-order cost at scale — which is why Google's Gemini reported goodput
97% only by keeping *redundant in-memory model-state replicas* rather than
restoring from storage, and why the checkpoint-systems literature
(CheckFreq's adaptive-frequency two-phase snapshots; Gemini-SOSP's
checkpoints into peer CPU RAM, ≤2% overhead at 2 h MTBF with >13× faster
recovery; Ant's DLRover shared-memory flash checkpoints; Orbax/DCP async)
exists at all. FlashML's manifest catalog and `lost_work()` economics sit
squarely in this literature.

**Elastic (membership changes, job continues).** torchrun MIN:MAX and Ray
Train V2 (restart-based: still a gang restart, just with a different world
size); Elastic Horovod (in-memory rollback + rebroadcast — dormant);
TorchFT (per-step quorum transactions — the frontier); Pathways
(proprietary). The spot-instance systems literature refines the same idea
under scheduled churn: Bamboo (redundant successor-stage compute in
pipeline bubbles, 3.7× over checkpoint-restart on spot), Oobleck
(precomputed pipeline templates, ≥f+1 replicas tolerate any f failures),
Varuna (job "morphing" across DP×PP configs), Parcae (predict preemptions,
migrate *before* they land — 10× over reactive under heavy churn).

**Quorum (the round absorbs churn).** Google FL (130% over-selection,
ignore stragglers, abandon under quorum), Flower (`accept_failures`,
min-clients floors), FedBuff/Papaya (no barrier at all), Psyche (witness
quorums per epoch), Moshpit (a failure spoils one small group's round),
INTELLECT-1 (evict at 6 s, retry the allreduce without the dead) — and
FlashML's FedAvg driver (`min_participants`, discard-late). Churn stops
being a failure mode and becomes a rate parameter.

The recovery-policy implication is the core architectural claim of this
paper: *these regimes are properties of the communication topology, not of
the framework* — and a runtime that knows the topology can pick the right
regime mechanically. FlashML's two-mode policy table already does this for
two of the three regimes; §5.3 completes the set.

### 4.5 NAT and the last mile

Measured traversal reality: classic hole punching succeeds ~70% of the
time and fails on symmetric NAT/CGNAT; Tailscale reports >90% of peer
connections end up direct with DERP TCP relays as universal fallback; iroh
(Psyche's transport) reports ~90% direct connections and ~95% of *data
volume* on direct paths, with QUIC connection migration; libp2p circuit
relays are how sahajBERT's NAT-ed volunteers participated. So worker↔worker
data planes over consumer networks are *possible* — at the price of a relay
infrastructure, a keypair identity layer, and accepting that ~10% of pairs
ride a relay. Combined with the uplink numbers (§4.1) the conclusion is
stark: NAT traversal is an engineering tax worth paying for
*coordinator-mediated* payloads (deltas, checkpoints — Psyche, Hivemind),
and not worth paying for per-step collectives, which the bandwidth forbids
anyway. FlashML's dial-out-only control plane already sidesteps the entire
problem for every topology except `collective` — where the trusted-pool
gate (§6.4) makes routability an admission criterion instead of a
traversal project.

### 4.6 Trust: verifying work you did not do

The verification ladder, with measured costs:

1. **Trust (status quo).** FlashML's volunteer tier today: "a lying node is
   currently believed" (documented known gap).
2. **Redundancy + quorum** (BOINC's 20-year-old answer): send each work
   unit to n hosts, accept on m-of-n agreement with fuzzy float compare;
   adaptive replication drops the duplication tax to ~1.1× for
   reliability-scored hosts. Defends against minority collusion; costs
   duplicated compute; fits *independent* tasks naturally.
3. **Refereed delegation** (Gensyn's Verde): replicate on n providers; on
   disagreement, a dispute game bisects the training graph to the first
   divergent operator and a referee re-executes *only that operator*.
   Requires bitwise-deterministic operators across GPUs (their RepOps
   library). Guarantees correctness if ≥1 replica is honest.
4. **Activation commitments** (Prime Intellect's TOPLOC): locality-
   sensitive hashes of top-k activations — 258 bytes per 32 tokens, ~1%
   prover overhead, validation up to 100× faster than generation; catches
   model/prompt/precision substitution. Verifies *inference/rollouts*
   (INTELLECT-2's untrusted half), not gradient computation.
5. **TEEs** (H100 confidential computing): 2–8% GPU overhead but 17–30%
   throughput/latency tax from PCIe encryption on realistic serving;
   collapses trust into NVIDIA's attestation chain.
6. **zkML: out of range for training.** Best published: ~388 s to prove
   *one* 7B inference — 10³–10⁴× the computation, before multiplying by a
   backward pass and millions of steps.

Placement for FlashML: rung 2 is implementable on the existing lease
machinery (m-of-n commits on redundant task replicas) and is the natural
"slice C" of the volunteer-trust roadmap; rung 4 becomes relevant the day
flashml serves RL rollouts or inference from volunteers; rungs 3/5 are
partner-or-buy, not build.

### 4.7 The WAN complexity tax, itemized

What multi-node-through-networks adds over a single machine — each item
with its mitigation and its FlashML status:

| # | Tax | Mitigation (landscape) | FlashML today |
|---|---|---|---|
| 1 | Rendezvous & addressing | store/coordination service; launcher assigns | absent — the core new build (§6.2, §6.4) |
| 2 | Process spawning ×N | torchrun agent; schedulers for TF/JAX | `LocalProcessLauncher` is 1-process; group launcher needed |
| 3 | NAT/firewalls | dial-out control planes; relays; or trusted routable pools | control plane already dial-out; data plane gated to trusted pool |
| 4 | Membership churn | gang restart / elastic re-rendezvous / quorum rounds | leases + policy table cover 2 of 3 regimes |
| 5 | Stragglers & heterogeneity | over-selection 130%; deadlines; stochastic routing; reliability scores | lease deadlines exist; over-selection & scoring absent |
| 6 | Bandwidth asymmetry | frequency reduction (H); quantization; upload-aware sizing | FedAvg rounds exist; DiLoCo/quantization absent |
| 7 | Coordinator SPOF | HA failover (NVFlare); on-chain state (Psyche); durable stores | SQLite-durable, restartable; no HA (acceptable at stage) |
| 8 | Trust/verification | quorum; refereed delegation; commitments; TEE | absent (documented); §6.6 roadmap |
| 9 | Checkpoint distribution | SHARDCAST trees; peer transfer; broadcast-load | artifact HTTP surface (single origin) — fine at current scale |
| 10 | Observability across sites | heartbeat telemetry; flight recorders; fault localization | viewer + per-rank heartbeat files extend naturally |
| 11 | Determinism/reproducibility | pinned images; RepOps; recorded seeds | pinned images + deterministic workloads already policy |
| 12 | Failure classification at distance | typed taxonomies (rare outside hyperscalers) | already built (`recovery/`) — a differentiator |

Rows 1–2 are the entry fee for TF/JAX at all (even single-node). Rows 3–8
are the multi-node fee. Rows 4, 11, 12 are where FlashML is *ahead* of most
of the surveyed field.
---

## 5. Part IV — Where FlashML stands

### 5.1 What exists, mapped to code

*(Every claim cites code read for this paper.)*

**Control plane, dial-out, lease-based.** Workers speak only outbound HTTP
to the coordinator: register/heartbeat, claim, attempt heartbeat/complete/
fail, checkpoint parts/commit, artifacts
(`flashnode/executor/client.py:93-187`). The lease state machine
(`leases/manager.py`) provides claim → heartbeat → idempotent
first-commit-wins, with expiry sweeps requeueing dead workers'
tasks — no special-casing of death. Composite `(job_id, task_id)` keys;
SQLite durability across coordinator restarts.

**Two execution topologies, encoded in recovery policy.**
`recovery/policy.py:33` defines `Mode = "independent_tasks" |
"coordinated_training"` and a 13-failure-class × 2-mode table of typed
decisions (RETRY_TASK vs RESTART_GROUP vs REPLACE_NODE vs PAUSE_JOB vs
FREEZE_AUTOMATION, with cordon and needs-checkpoint flags). The
coordinated column's rationale strings ("NCCL-class error — collective
state is not repairable in place") are, per §2.2, verbatim-consistent with
NVIDIA's documentation and the whole industry's behavior. `decide()` is
currently called from exactly one place — the SDK's `max_restarts` loop
(`sdk.py:365`); the service coordinator still recovers implicitly via
lease expiry.

**A coordinator-mediated topology in production shape — but as workload
code.** The FedAvg driver (`flashml_workloads/fedavg_driver.py`) runs
rounds as Mode A jobs: quorum aggregation (`min_participants`,
`round_timeout_s=600`), late deltas discarded with the correct staleness
argument, dead-driver resume from the last completed round, pure-stdlib so
it runs inside the cloud API. The worker (`fedavg_worker.py`) hardcodes a
torch MLP — the generalization gap §6.5 closes. This driver is
independently convergent with Google's reporting-window design and
Flower's `accept_failures` — FlashML got the cross-device answer right
before reading the literature.

**Single-node coordinated PyTorch.** `integrations/pytorch.py` emits
`torchrun --standalone` (nnodes>1 raises); `flashruntime/torch` wires
torch's own DDP + sampler, restores the newest *valid* manifest, and
mirrors per-rank heartbeat files for the viewer. The checkpoint contract
(`checkpoint/local.py`: hash parts, write `manifest.json` last, re-verify
on read) is the same commit-marker discipline Orbax arrived at (§2.4).

**A hardened but network-less volunteer tier.** `flashnode work --runner
argv` requires a non-empty image allowlist and runs argv in Docker with
`--network none`, read-only rootfs, cap-drop ALL, pid/cpu/memory limits
(`flashnode/executor/hardening.py:236`). Placement fail-closes on
`sandbox_capable` AND `argv_capable` (`NodeRegistration`,
`protocol/v1alpha1.py:316-327`). Documented known gaps: no result
verification, shared join code, no GPU probing, no coordinated multi-process
training on volunteers.

**A planner that already thinks in strategies.** `planner/candidates.py`:
single_gpu · ddp · fsdp2 · zero3_cpu_offload (+ QLoRA variants) ·
lease_tasks — framework-import-free, emitting `LaunchSpec`s whose contract
states "addresses/ports are ALWAYS resolved by the launcher" — i.e. the
architecture *reserved the rendezvous seam* before this project needed it.

### 5.2 What the landscape says about this design

**Three independent convergences validate the bones.**

1. *Flower converged on the same control plane.* Their SuperLink is a
   pull-based message broker where every participant — server-side app
   included — dials out; registration + 30 s heartbeats + eviction after
   2 missed ≈ FlashML's register/heartbeat/lease-expiry loop. When the
   best-funded FL company and FlashML's e2e loop are near-isomorphic,
   the shape is right.
2. *torchrun/Ray/TF all restart the gang from checkpoint*, which is
   FlashML's RESTART_GROUP row; TorchFT's improvement path (heartbeat
   quorum service + transactional commit + peer recovery) is
   lease-machinery-shaped, suggesting FlashML's primitives extend toward
   the frontier rather than away from it.
3. *INTELLECT-1's ElasticDeviceMesh* (2 s heartbeats, 6 s eviction,
   round-boundary membership) is a lease manager with different constants.

**Four gaps the landscape names precisely.**

1. **No rendezvous provider / group launcher** — the entry fee for TF/JAX
   (§2.6 punchline) and for PyTorch nnodes>1.
2. **Topology is under-specified.** Two modes conflate four regimes:
   today `local` and `collective` share "coordinated", while
   `independent` and `coordinator_mediated` share "independent_tasks" —
   and the FedAvg driver's correct quorum policy is invisible to
   `decide()`, which would return RETRY_TASK for a dead FedAvg worker the
   driver intends to *drop*. Latent today (service-side recovery is
   implicit); a real conflict the day service-side recovery wires up
   (existing Missing #4).
3. **Per-node identity + verification** — Flower shipped per-node
   keypairs; BOINC's quorum is 20 years old; FlashML has a shared join
   code and trust. The lease machinery makes rung-2 verification (m-of-n
   redundant commits) a natural extension.
4. **No over-selection / reliability scoring.** Google's 130% and BOINC's
   adaptive replication both exist because dropout is a rate, not an
   exception. FlashML's FedAvg driver has quorum but launches exactly
   `num_shards` tasks.

**One strategic read.** The archetypes FlashML straddles are converging on
each other: FedML became a GPU scheduler; Flower is pushing into LLM
pretraining; Prime Intellect runs a compute marketplace *and* a protocol.
The defensible position in that convergence is the one FlashML already
picked — the reliability layer (typed failure taxonomy, verified
checkpoints, honest recovery evidence) — because Part III's failure
statistics say reliability is the scarce good, and almost nobody outside
the hyperscalers ships a versioned, deterministic recovery policy.

### 5.3 The topology axis, formalized

The proposal that resolves gap 2 and structures every slice in Part V:
promote communication topology to a first-class protocol concept with four
values, replacing the two-mode string.

| Topology | Data plane | Who can run it | One worker dies | Exists today as |
|---|---|---|---|---|
| `local` | shared memory / localhost | any single machine | restart process | `torchrun --standalone` path |
| `independent` | none | anyone incl. NAT-ed volunteers | retry that task (RETRY_TASK) | Mode A leases |
| `coordinator_mediated` | worker↔hub artifacts, per round | anyone incl. NAT-ed volunteers | drop if quorum holds, else round retry | FedAvg driver (workload-level) |
| `collective` | worker↔worker sockets, per step | trusted routable nodes only (`multinode_capable`) | RESTART_GROUP from checkpoint | single-node only |

Placement derives from it (volunteers never see `collective`; routability
is an admission gate, not a traversal project). Recovery derives from it
(the policy table grows two columns whose entries §4.4's three regimes
dictate). Adapters declare which topologies a framework supports
(sklearn: independent; TF: local/collective + coordinator_mediated via
PSS-shaped recipes; JAX/PyTorch: local/collective + coordinator_mediated
via the §6.5 engine; HF: whatever torch does). And the same framework
adapter surface serves every pool — the user chooses a model and a pool;
the runtime chooses the regime.
---

## 6. Part V — Difficulty estimates and the recommended approach, per library

### 6.0 How the estimates are made

Sizes are S / M / L / XL with calendar ranges assuming the July-2026
velocity (the entire 0.1.0 feature set — SDK, adapters, recovery, viewer,
docs, CI — shipped in about a month of solo+agent work). Five difficulty
drivers, each named per slice: **(w)** wire-visible protocol changes
(version bump + flashnode floor move — the 0.4.1 lesson); **(s)** security-
posture changes (anything touching `--network none` or placement gates);
**(i)** new infrastructure components; **(t)** testability on the CPU-only
M4 dev box (slices that can't be locally tested are strictly harder);
**(b)** blast radius on the existing 317-test suite and the pydantic-only
core rule (framework imports stay inside functions; heavy deps behind
extras).

Sequencing principle: each slice ships alone, and the framework adapters
land *on* the group launcher rather than each inventing launch.

### 6.1 Slice 1 — Finish the Transformers surface (S: ~2–4 days)

*What:* an `accelerate` adapter + a PEFT/LoRA recipe. The HF Trainer
callback, manifest glob, and resume helper already work
(`integrations/huggingface.py`); `resolved_mode()` already classifies
`accelerate` argv as coordinated (`workloads/command.py:98`) — no adapter
emits it.

*Approach:*
- `integrations/huggingface.py` += `accelerate(script, *, num_processes,
  config_file=None, deepspeed_config=None, script_args="")` → emits
  `accelerate launch --num_processes N [--config_file …] script …`.
  DeepSpeed configs ride `LaunchSpec.files` and are referenced by
  **absolute path into `FLASHML_OUTPUT_DIR`** in argv — which resolves the
  flagged files-not-in-cwd follow-up (`strategies/__init__.py:64-73`)
  without moving files.
- A `peft_lora` recipe on the existing checkpoint contract: adapter-only
  checkpoints (MB-scale — §2.5) make it the flagship cheap resumable
  workload; resume via the existing `latest_checkpoint()` +
  `resume_from_checkpoint`.
- Tests mirror `test_torch_helper.py` / `test_examples_e2e.py` patterns;
  `accelerate launch --cpu` works on the M4.

*Drivers:* w:none · s:none · i:none · t:full · b:tiny. *Risk:* accelerate-
config-vs-TrainingArguments precedence confusions (§2.5's "Accelerate
ignores the deepspeed argument" caveat) — document one blessed path.

### 6.2 Slice 2 — Group launcher + TF and JAX adapters, single-node (M: ~1–2 weeks)

*What:* the entry fee from §2.6 — a launcher that spawns N processes with
per-rank env, and the two adapters + in-script helpers on top. Single-node
only (`127.0.0.1`), which per the 2×4090 validation is the configuration
real users hit first anyway.

*Approach:*
- `launchers/group.py` — `LocalGroupLauncher`: N `Popen`s from one
  `LaunchSpec`; one log per rank; a **rank-env mapper seam**
  (`env_for_rank(rank, world, coordinator_addr) -> dict`) supplied by the
  strategy compiler; aggregate `GroupLaunchHandle.poll()` = FAILED if any
  child failed (mirroring torchrun's k≤n rule), plus per-rank exit codes
  for `classify()`. Free port picked at launch (the launcher owns
  addresses — the seam `LaunchSpec.rendezvous` reserved).
- `integrations/tensorflow.py` — `mirrored(script, nproc)` renders per-rank
  `TF_CONFIG` (`{"cluster": {"worker": [127.0.0.1:p0..pN-1]}, "task":
  {"type": "worker", "index": r}}`). `flashruntime/tf` helper respects the
  ctor-ordering constraint (§2.3): instead of a post-hoc `prepare()`, it
  exposes `ft.strategy()` (constructs MWMS with an explicit
  `timeout_seconds` so hangs become errors — §2.3's default-hang), plus
  `checkpoint()` committing a manifest **after** `CheckpointManager.save()`
  returns, treating `prefix.index` + `prefix.data-*` as parts, honoring
  the all-workers-participate save rule; `log_metrics()`/accessors/
  heartbeat files identical to the torch helper. Same ADR-0003 guardrail:
  wire TF's own strategy, report facts, stop.
- `integrations/jax.py` — env-only contract: launcher exports
  `FLASHML_COORD_ADDR`, `FLASHML_NUM_PROCS`, `FLASHML_PROC_ID`;
  `flashruntime/jax.initialize()` reads them into
  `jax.distributed.initialize()` (dense ids guaranteed by the launcher);
  `checkpoint()` = Orbax `wait_until_finished()` → hash finalized dir →
  manifest (§2.4's composition), with a no-Orbax msgpack fallback for
  dependency-light users.
- Extras: `[tf]`, `[jax]` in pyproject; imports stay in-function (core
  smoke unaffected).
- Tests all-local: TF MWMS runs on CPU with localhost TF_CONFIG; JAX
  multi-process on CPU via `JAX_PLATFORMS=cpu` +
  `jax_cpu_collectives_implementation=gloo` (§2.4). Kill-a-rank tests
  assert the group handle reports FAILED and `classify()` sees the exit
  code — the same story `test_examples_e2e.py` tells for torch.

*Drivers:* w:none (launcher + adapters are not wire-visible) · s:none ·
i:launcher only · t:full · b:moderate (new launcher touches sdk submit
path). *Risk:* TF/macOS wheel quirks in CI; JAX API drift (pin minimums,
accept the maintenance note in §7).

### 6.3 Slice 3 — The topology axis in protocol and policy (M: ~1 week)

*What:* §5.3 made real. Mostly mechanical, but wire-visible — the
discipline slice.

*Approach:* `protocol/v1alpha1.py`: `topology` field on workload/task
specs; `NodeRegistration.multinode_capable: bool = False` (fail-closed,
same pattern as `argv_capable`); version → 0.5.0, flashnode floor moves in
the same change. `recovery/policy.py`: `Mode` becomes the four-value
topology; the table doubles with entries §4.4 dictates
(`coordinator_mediated` rows are quorum-shaped: WORKER_CRASH → DROP_FROM_
ROUND/no-retry when quorum holds; `local` rows are RESTART_GROUP-scoped to
one machine); `"coordinated_training"` accepted as a deprecated alias for
`collective` so stored events replay. `workloads/command.py` mode enum
follows with the same alias. `IsolationAwarePlacement`: `collective` ⇒
requires `multinode_capable`; volunteers structurally never selected. The
FedAvg driver declares `coordinator_mediated`, closing the latent
policy/driver disagreement (§5.2 gap 2).

*Drivers:* **w:yes** (the careful part) · s:placement-gate only ·
i:none · t:full · b:wide-but-shallow (policy tests are table-driven).

### 6.4 Slice 4 — Multi-node collective on trusted pools (L: ~3–4 weeks)

*What:* `nnodes > 1` for PyTorch, TF, and JAX on routable, operator-
trusted nodes (own cluster / rented cloud). Explicitly not volunteers,
not NAT traversal, not elasticity beyond restart.

*Approach:*
- **Rendezvous over the existing control plane.** New coordinator surface
  (`/v1alpha1/rendezvous/{job}/…`): a claimed rank-task registers its
  routable address; workers poll until all N registered or barrier
  timeout (torchrun's constants as defaults: join 600 s, last-call 30 s).
  No gang-scheduling rewrite: a coordinated job expands to N rank-tasks,
  each claimed through the normal `LeaseManager`; the *frameworks' own
  init barriers* (§2's TCPStore wait / MWMS startup sync /
  `jax.distributed.initialize`) do the waiting, and unmet rendezvous =
  barrier timeout = leases expire = tasks requeue. Rank 0's address
  becomes `MASTER_ADDR` / first `TF_CONFIG` worker / JAX coordinator.
- **Recovery = the policy table doing its job.** Any rank's FAILED attempt
  → classify → RESTART_GROUP/REPLACE_NODE per the collective column →
  coordinator cancels sibling leases, requeues the group, resumes from the
  job-scoped checkpoint tree (already shared across attempts —
  `launchers/local.py:105`). This lands the service-side classify/decide
  wiring (longstanding Missing #4) as a natural part.
- **Admission honesty:** `multinode_capable` nodes self-report routable
  addresses at registration; the docs state plainly (per §2.2) that
  collective pools need ephemeral-range TCP open between members — a
  pool-operator prerequisite, not something FlashML works around.
- Validation: CPU/gloo two-process-two-host on LAN Macs proves the
  rendezvous loop free; one RunPod Instant-Clusters run (the competitor's
  own multi-node product — §3.3) validates nccl for dollars, mirroring the
  $0.07 4090 playbook.

*Drivers:* w:yes (rendezvous surface) · **s:yes** (a second, network-open
node class — kept honest by fail-closed gating) · **i:yes** (rendezvous
service + group-restart orchestration) · **t:partial** (LAN CPU yes; nccl
needs rented GPUs) · b:moderate. This is the slice where §4.4's gang
economics arrive; it should land *after* 6.3 so its recovery column
exists.

### 6.5 Slice 5 — The coordinator-mediated engine, generalized (L: ~3–5 weeks)

*What:* promote the FedAvg driver from a torch-MLP demo to the
framework-neutral WAN/volunteer training path — the topology Photon,
Psyche, and INTELLECT-1 prove out (§3.5), and the only one the volunteer
pool can ever run (§4.5).

*Approach:*
- **Weight codec:** replace the JSON torch-shape codec
  (`fedavg_weights.py`) with **safetensors** as the interchange — readable
  and writable from torch, TF, JAX, and HF natively (DCP already ships a
  safetensors reader/writer — §2.2); keep pure-stdlib reduce on the
  driver side by operating on raw tensors.
- **Worker contract stays argv-shaped** (no new execution mode — "pipelines
  are jobs chained by a driver"): the round task runs the *user's* script
  with `--weights-in W.safetensors --delta-out D.safetensors --steps H`;
  thin per-framework helpers (`flashruntime.federated` for
  torch/TF/JAX/HF) implement load-train-diff-save in ~30 lines each,
  runnable inside `--network none` (artifact relay does the transfer —
  exactly how `sgd_trainer` works today).
- **Driver upgrades, each one literature-backed:** pseudo-gradient +
  **outer Nesterov** option = DiLoCo (§4.3a; the scaling-laws result is
  the argument it's not a quality sacrifice); int8/fp16 delta quantization
  (INTELLECT-1's multiplier); **over-selection** (launch
  `ceil(1.3 × num_shards)` per Google's 130%/6–10%) and per-node
  reliability scoring feeding placement; round deadlines already exist.
  Async/FedBuff buffering is a later flag, not v1.
- Fully M4-testable (processes + HTTP + files); convergence guarded the
  way `test_fedavg_convergence.py` already does, plus a DiLoCo-vs-sync
  parity test on the tiny MLP.

*Drivers:* w:minor (driver params) · s:none (volunteer-safe by
construction) · i:none · t:full · **b/risk: research-adjacent** —
convergence tuning is real work; mitigated by shipping plain-FedAvg
general first and gating DiLoCo behind an experimental flag.

### 6.6 Slice 6 — Verification and volunteer trust (XL: staged, not scheduled)

Ladder per §4.6: per-node keypair identity (Flower-proven; replaces the
shared join code) → **m-of-n redundant execution** on the lease machinery
(BOINC-proven; natural for `independent` and for §6.5's round deltas;
adaptive replication once reliability scores exist) → activation
commitments (TOPLOC-class) if/when volunteer inference or RL rollouts
become a product; TEE and refereed delegation are partner/buy. Each rung
is independently shippable; none blocks slices 1–5.

### 6.7 The roadmap in one table

| # | Slice | Size | Est. | Depends on | Wire | Security | M4-testable | Landscape proof |
|---|---|---|---|---|---|---|---|---|
| 1 | HF accelerate + PEFT | S | 2–4 d | — | no | no | yes | ecosystem converged on the env contract (§2.5) |
| 2 | Group launcher + TF/JAX single-node | M | 1–2 w | — | no | no | yes | TF/JAX ship no launcher (§2.3–2.4) |
| 3 | Topology axis | M | ~1 w | — | **yes** | gate only | yes | three churn regimes (§4.4) |
| 4 | Trusted-pool collective multi-node | L | 3–4 w | 2, 3 | yes | **yes** | partial | torchrun semantics; TorchFT direction (§2.2) |
| 5 | Coordinator-mediated engine (FedAvg→DiLoCo) | L | 3–5 w | 3 (not 4) | minor | no | yes | Photon 7B/13B; Hermes 4.3; INTELLECT-1 (§3.5) |
| 6 | Verification roadmap | XL | staged | 3 | yes | yes | mostly | Flower auth; BOINC quorum; TOPLOC (§4.6) |

Slices 1–3 ≈ one focused month and deliver the headline "TensorFlow, JAX,
and Transformers support" with correct single-node distribution and an
honest protocol. Slices 4 and 5 are parallel tracks after 3 — and if one
must lead, the evidence in Parts II–III favors **5**: it serves every pool
FlashML actually has today, degrades gracefully under churn, and is the
configuration the successful precedents (Photon, Psyche, INTELLECT-1) run
in production, while 4 serves the pool FlashML still has to acquire.
---

## 7. Risks and open questions

1. **Framework API drift.** JAX is mid-migration (Shardy default since
   0.7.1, GSPMD removed after Mar 2026; pmap in maintenance); TF's
   distribution story is in stasis (Keras 3 distribution JAX-only, DTensor
   stagnant); Horovod is dormant. Mitigation: adapters stay
   launch-convention-thin (the four-axes rule already mandates this), pin
   tested minimums in extras, and treat TF's MWMS as the supported TF path
   rather than anything newer.
2. **Dependency weight vs the pydantic-only core.** TF/JAX/Orbax are heavy.
   All imports stay inside functions; `[tf]`/`[jax]` extras; the clean-venv
   core smoke stays the gate. (The HF adapter already models this
   discipline.)
3. **Convergence risk in Slice 5's DiLoCo mode.** The scaling-laws paper
   de-risks the method class, but FlashML's tiny-model test regime must
   not overclaim: ship plain generalized FedAvg as the supported path,
   DiLoCo behind an experimental flag with a parity test, and publish the
   honest benchmark per the existing benchmarks policy.
4. **The collective pool is a new security surface.** `multinode_capable`
   nodes accept inbound ephemeral-range TCP from pool members — a posture
   change that must never leak into the volunteer tier. Fail-closed
   gating (the `argv_capable` pattern) plus doc-level pool-operator
   prerequisites contain it; a red-team pass on the rendezvous surface
   belongs in Slice 4's definition of done.
5. **Wire-protocol churn.** Slices 3 and 4 both touch `protocol/`; the
   0.4.1 lesson (one version string naming two protocols) says batch the
   changes: one 0.5.0 bump carrying topology + multinode_capable +
   rendezvous surface together, flashnode floor moved in the same commit.
6. **Testability ceiling.** nccl paths cannot run in CI; the GPU e2e
   remains a paid, scripted, occasional validation (the $0.07 playbook).
   Real risk of gloo-green/nccl-red bugs — §2.2's device-placement bug
   found only on real GPUs is the precedent; budget a RunPod hour per
   collective-slice milestone.
7. **Open question — who is the multi-node customer?** Slice 4 serves
   users with routable trusted pools (own hardware or rented clusters —
   where RunPod's manual-torchrun gap suggests genuine demand for an
   orchestration layer); Slice 5 serves the volunteer/federated ambition
   the product was founded on. The evidence ranks 5's precedents stronger
   (Photon, Psyche, INTELLECT-1 in production vs "rent InfiniBand"), but
   this is ultimately a product-strategy call, flagged rather than
   decided here.
8. **Open question — coordinator HA.** NVFlare has hot/cold failover;
   Psyche put the coordinator on a blockchain; FlashML has a restartable
   SQLite coordinator. Adequate now; revisit when a paying pool exists
   (the cloud stage's Postgres move is the natural moment).

---

## 8. References

*Primary sources actually consulted for this paper (grouped; URLs as
fetched 2026-08-02).*

**PyTorch.**
docs.pytorch.org/docs/2.13/distributed.html · /elastic/run.html ·
/elastic/rendezvous.html · /elastic/agent.html ·
/torch_nccl_environment_variables.html · /distributed.fsdp.fully_shard.html ·
/distributed.tensor.html · /distributed.checkpoint.html ·
docs.pytorch.org/docs/main/notes/ddp.html · arxiv.org/abs/2006.15704 (DDP,
VLDB'20) · docs.nvidia.com/deeplearning/nccl/user-guide (communicators,
env) · github.com/pytorch/pytorch/issues/115388, /119196 ·
github.com/NVIDIA/nccl/issues/1013 · github.com/meta-pytorch/torchft ·
pypi.org/project/torchft-nightly · pytorch.org/blog/fault-tolerant-llama-…
· github.com/pytorch/torchtitan · pytorch.org/blog/introducing-pytorch-monarch
· github.com/stas00/ml-engineering (network chapter) ·
docs.pytorch.org/tutorials/intermediate/TCPStore_libuv_backend.html

**TensorFlow.**
tensorflow.org/guide/distributed_training ·
/tutorials/distribute/multi_worker_with_keras ·
/tutorials/distribute/parameter_server_training · /guide/checkpoint ·
/guide/migrate/fault_tolerance · tensorflow source:
collective_all_reduce_strategy.py, collective_util.py,
failure_handling.py · keras.io/api/callbacks/backup_and_restore ·
keras.io/guides/distribution · blog.tensorflow.org (2.20/2.21 notes)

**JAX / Orbax.**
docs.jax.dev/en/latest/multi_process.html · …/jax.distributed.initialize
· …/fault_tolerance.html · …/parallel.html ·
…/jax.experimental.multihost_utils.html · …/shardy_jax_migration.html ·
arxiv.org/abs/2605.23066 (Orbax paper) · orbax.readthedocs.io
(async_checkpointing, optimized_checkpointing, atomicity) ·
openxla.org/shardy · cloud.google.com/ai-hypercomputer (Pathways
resilient-training)

**HF / DeepSpeed / others.**
huggingface.co/docs/transformers (trainer, deepspeed, fsdp, peft) ·
github.com/huggingface/transformers/releases/tag/v4.30.0 ·
huggingface.co/docs/accelerate (cli, launch, checkpoint, fsdp_and_deepspeed)
· deepspeed.ai/docs/config-json · /getting-started · /tutorials/zero ·
Rajbhandari et al., ZeRO (SC'20) · horovod.readthedocs.io (summary,
elastic) · github.com/horovod/horovod/releases · docs.ray.io/en/latest/train
(overview, fault-tolerance, ScalingConfig) ·
docs.ray.io/…/fault_tolerance/{nodes,gcs}.html · ray_config_def.h ·
lightning.ai/docs (strategy, fabric) · github.com/NVIDIA/Megatron-LM ·
transformers issues #26186, #24252, #29607, #35850, #26665

**Federated platforms.**
flower.ai/docs (explanation-flower-architecture,
ref-flower-network-communication, how-to-use-strategies, FedAvg API,
how-to-run-simulations, how-to-authenticate-supernodes, secure-aggregation)
· flower.ai/blog (Series A 2024-02-15; node auth 2024-06-25; Photon
2025-05-09) · arxiv.org/abs/2007.14390 (Flower) · 2205.06117 (Salvia
SecAgg) · **1902.01046 (Bonawitz — Google FL at scale)** ·
nvflare.readthedocs.io (overview, system_architecture, high_availability,
identity_security) · 2210.13291 (NVFlare) · Nature Medicine EXAM
(pubmed 34526699) · github.com/FedML-AI/FedML · docs.tensoropera.ai ·
openfl.readthedocs.io · jmlr.org/papers/volume22/20-815 (FATE) ·
docs.substra.org · 2210.08871 (MELLODDY) · 2007.10987 (IBM FL) ·
openmined.org/pysyft · machinelearning.apple.com (learning-with-privacy-
at-scale) · 2106.06639 (FedBuff) · 2111.04877 (Papaya) · 1905.06641
(HierFAVG)

**Marketplaces / volunteer.**
docs.runpod.io (instant-clusters, instant-clusters/pytorch,
pods/networking, choose-a-pod, serverless endpoint-configurations) ·
runpod.io/product/clusters · docs.vast.ai (hosting-overview, ssh,
multi-node-training-using-torch-nccl, understanding-verification) ·
clustermax.ai/cloudreview/vastai · docs.salad.com (networking, faqs,
priority-pricing, gateway) · blog.salad.com/benchmarking-saladcloud ·
io.net docs (proof-of-work, deploy-ray-cluster) · messari.io (io.net Q1'25;
Akash Q4'25) · theblock.co (io.net postmortem) · akash.network/docs
(provider) · docs.gensyn.ai · **2502.19405 (Verde/RepOps)** ·
blog.gensyn.ai (verde-in-production) · primeintellect.ai/blog/compute ·
**2209.01188 + 2312.08361 (Petals)** · petals wiki (FAQ, Security) ·
**boinc.berkeley.edu (Anderson paper; 1903.01699)** · BOINC wiki
(BackendLogic, JobReplication, Adaptive-Replication,
Homogeneous-Redundancy, ClientSched, RpcPolicy, BOINC_Security) ·
docs.foldingathome.org/ws · foldingathome.org faqs (deadlines, points,
passkey) · blogs.nvidia.com/blog/foldingathome-exaflop-coronavirus ·
github.com/exo-explore · together.ai/blog/multi-node-gpu-training ·
coreweave.com (GB200 NVL72 GA) · lambda.ai (1-Click Clusters) ·
usenix.org/nsdi23 (SkyPilot) · dstack.ai/docs · HPE Determined
acquisition (2021-06)

**Decentralized training & systems literature.**
**2311.08105 (DiLoCo)** · **2503.09799 (DiLoCo scaling laws)** ·
**2501.18512 (Streaming DiLoCo)** · 2407.07852 (OpenDiLoCo) ·
**2412.01152 (INTELLECT-1)** · 2505.14065 (PCCL) · **2505.07291
(INTELLECT-2)** · 2501.16007 (TOPLOC) · 2512.16144 (INTELLECT-3) ·
implicator.ai (INTELLECT-3 commentary) · **2411.19870 (DeMo)** · Nous
DisTrO preliminary report · nousresearch.com/nous-psyche ·
/the-next-phase-of-psyche · /introducing-hermes-4-3 ·
iroh.computer/solutions/nous · 2103.03239 (Moshpit) · 2106.10207
(DeDLOC/sahajBERT) · **2301.11913 (SWARM)** ·
github.com/learning-at-home/hivemind · 2204.12013 (Bamboo, NSDI'23) ·
2309.08125 (Oobleck, SOSP'23) · Varuna (EuroSys'22) · 2403.14097
(Parcae, NSDI'24) · CheckFreq (FAST'21) · Gemini (SOSP'23) ·
github.com/intelligent-machine-learning/dlrover · **2402.15627
(MegaScale, NSDI'24)** · 2509.16293 (ByteRobust, SOSP'25) · 2411.01791
(Minder) · Llama-3 failure reporting (tomshardware summary of the paper) ·
OPT-175B logbook (facebookresearch/metaseq) · **2410.21680 (Meta RSC
reliability)** · Gemini goodput (datacenterdynamics) · 1905.13727
(PowerSGD) · 1712.01887 (DGC) · 2102.02888 (1-bit Adam) · QSGD
(NeurIPS'17) · 2103.00543 (utility of gradient compression) ·
tailscale.com/blog/nat-traversal-… · pinggy.io (iroh 1.0) ·
libp2p.io/docs/hole-punching · Ookla Speedtest Global Index (2025–26) ·
2404.16109 (zkLLM) · **2503.11023 (decentralized LLM training survey,
EMNLP'25)**

**FlashML sources.** All local citations reference files read in this
workspace on 2026-08-02: `flashruntime/` (integrations, torch helper,
workloads/command.py, launchers, leases/manager.py, recovery/policy.py,
checkpoint/local.py, planner/candidates.py, strategies, protocol
v1alpha1), `flashml_workloads/` (fedavg_driver, fedavg_worker,
sgd_trainer), `flashnode/` (executor/client.py, executor/hardening.py),
and the repo AGENTS.md files.

---

*End of paper.*
