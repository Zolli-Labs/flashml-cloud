# GPU detection and GPU-aware placement

**Date:** 2026-08-02
**Status:** design approved (three decisions taken by the owner, §0)
**Closes:** `POSITIONING_LOG.md` open thread 6, and the first half of thread 5.

---

## 0. Decisions taken

| # | Question | Decision |
|---|---|---|
| 1 | Scope | **Full vertical slice + real GPU validation** on a rented RunPod (~$1). |
| 2 | Request shape | **`resources.gpus: <count>`**, mirroring `cpus` / `memory_gb`. No model or VRAM matching in v1. |
| 3 | CUDA image | **Build `images/pytorch-cuda` ourselves**, alongside the three existing images. |

Two further decisions taken without asking, both following existing hard rules:

- **The probe shells out to `nvidia-smi`, not `pynvml`.** flashnode's AGENTS.md:
  "keep the agent dependency-light — every dependency is attack surface on
  someone else's machine." The driver already ships the binary.
- **The placement gate fails closed** (§4).

## 1. What exists today

Nothing detects, advertises, requests, gates on, or exposes a GPU. But every
seam is already cut:

| Layer | Today |
|---|---|
| `NodeCapabilities.gpus` | field exists, untyped `list[dict[str, Any]]`, always `[]` |
| `flashnode/inventory/capabilities.py:100` | `gpus=[],  # GPU probing is a documented follow-up; never guess.` |
| `telemetry.GpuSample` | NVML-shaped pydantic model, designed, never populated |
| `IsolationAwarePlacement` | four gates, none about hardware |
| `executor/hardening.harden_args` | no `--gpus` |
| `ResourcesSpec` | `cpuPerTask`, `memoryPerTask` |
| `flashml.yaml` `resources:` | `cpus`, `memory_gb` (validated in `compile._resources`) |
| `images/` | `python-slim`, `pytorch-cpu`, `sklearn` |
| `scripts/runpod_gpu_e2e.py`, `tests/test_gpu_e2e.py` | **already exist** from the 2026-07-23 real-GPU validation |

The July GPU work validated *the runtime's* CUDA paths (nccl DDP, per-rank
device placement, kill-and-resume) on 2×RTX 4090. It did **not** touch the
volunteer/lease path, which is what this spec adds.

## 2. The shape of the change

```
flashnode                    flashruntime                  flashml-cloud
─────────                    ────────────                  ─────────────
nvidia-smi probe                                           flashml.yaml
  → capabilities.gpus  ──→   NodeRegistration                resources.gpus: 1
                               ↓                                 ↓
                             node_view["capabilities"]       compile._resources
                               ↓                                 ↓
                             IsolationAwarePlacement         ResourcesSpec.gpuPerTask
                               5th gate (fail closed)            ↓
                               ↓                             CommandRecipe.expand
harden_args --gpus   ←──     lease.payload["gpus"]  ←──────  payload["gpus"]
```

### 2.1 The three hops that broke last time

The `local_datasets` work was built correctly at both ends and broken in the
middle **three separate times**. Each is a named risk here, with the same
shape:

1. **`service/modea.py` node view.** `local_datasets` was omitted, so the gate
   read an absent capability on every node and its DoD item passed vacuously.
   **Already covered for GPU** — the view passes
   `"capabilities": entry.registration.capabilities.model_dump()`, so
   `capabilities.gpus` flows through untouched. Verified 2026-08-02. A test
   pins it anyway.
2. **`CommandRecipe.expand`.** It builds each payload from a fixed key list and
   silently drops anything not in it, so the requirement never reached the
   gate and the gate **failed open**. GPU has the same exposure and it is
   worse: `expand` reads `spec.spec.workload.parameters`, but `gpuPerTask`
   lives on `spec.spec.resources` — a *different* branch of the spec it does
   not currently read at all.
3. **Both flashnode runners.** Each had to forward the payload key to
   `hardening`, or the feature was implemented there and dead everywhere else.

Every one of those hops gets an explicit end-to-end test, not just unit tests
at either end.

## 3. Protocol (flashruntime 0.5.0)

### 3.1 A typed `GpuInfo`

`gpus: list[dict[str, Any]]` becomes `list[GpuInfo]`:

```python
class GpuInfo(BaseModel):
    """One GPU as the host's driver reports it. Every field optional: a
    probe that cannot read a value must say nothing rather than guess."""
    index: int
    name: str = ""
    memory_total_mb: int | None = None
    driver_version: str = ""
    compute_capability: str = ""
```

Typed now, even though v1 matches only on **count**, because the wire format
is the expensive thing to change later — these agents run on machines we
cannot reach. Collecting `memory_total_mb` and `compute_capability` from the
start means the data is already flowing when matching rules arrive; adding
fields to a model is cheap, changing `dict` to a model is not.

### 3.2 `ResourcesSpec.gpuPerTask`

```python
gpuPerTask: int = Field(ge=0, default=0)
```

`0` means "no GPU required", which is every job that exists today.

## 4. Placement: the fifth gate, fail closed

A task whose payload carries `gpus: N` (N ≥ 1) is eligible only on a node
whose `capabilities.gpus` is a **list** of at least N entries.

Polarity, in the terms `scheduler/__init__.py` already uses for its four
gates: this follows `argv_capable` / `local_datasets` (fail closed), **not**
`module_capable` (fail open).

`module_capable` fails open because misplacing a module task only wastes
retry attempts. A GPU requirement is different in kind: a CUDA job on a
CPU-only box does not politely fail and requeue — it either crashes on
`torch.cuda.is_available()` or, worse, silently falls back to CPU and runs
two orders of magnitude slower while reporting success. Neither is something
to discover from a bill.

Type confusion fails closed, matching the local-data gate exactly:

- `capabilities.gpus` must be a genuine list. Absent, `None`, a bare string,
  or a dict counts as **no GPUs**.
- A `gpus` requirement present but not an `int` ≥ 0 makes the task ineligible
  everywhere rather than crashing the predicate.
- `gpus: 0` requires nothing and runs anywhere, exactly like tier `standard`
  and an empty `local_inputs`.

**The gate is one-directional.** A node with GPUs still receives CPU work.
Reserving GPU hosts for GPU jobs is a scheduling *optimisation* and a
separate decision; making it a gate here would idle the scarcest hardware on
the network.

**`allowFallback` does not waive it**, for the same reason it does not waive
the argv or local-data gates: it is the submitter's statement about their own
isolation posture, and has nothing to say about hardware that either exists
or does not.

## 5. Execution: `--gpus` (flashnode 0.4.0)

`harden_args` gains `--gpus <N>` when the payload requests them, alongside
the existing `--network none`, `--read-only`, `--cap-drop=ALL`,
`--security-opt=no-new-privileges`, non-root `--user`, `--pids-limit`,
`--cpus`, `--memory`.

`--network none` is retained. GPU work does not need the network, and the
NVIDIA container runtime does not require it.

### 5.1 A security gap this opens, recorded rather than hidden

**GPU memory is not zeroed between containers.** The NVIDIA runtime hands a
device to a container; when that container exits, whatever was in VRAM can in
principle be read by the next container to get the same device. On a network
of *untrusted volunteer machines running other people's jobs*, that is a
real cross-tenant leak, and it is not fixed by any flag in `harden_args`.

It is out of scope here and must not be quietly inherited. Mitigations exist
(driver-level persistence mode off, MIG, or simply not scheduling two
tenants' work onto one host) and belong in the same conversation as result
verification, which is `POSITIONING_LOG.md` thread 4.

## 6. Cloud (flashml-cloud)

`flashml.yaml`:

```yaml
resources:
  cpus: 2
  memory_gb: 8
  gpus: 1        # new; default 0
```

`compile._resources` validates it as a non-negative integer and emits
`gpuPerTask`. `CommandRecipe.expand` copies it into `task.payload["gpus"]`
when non-zero — **absent stays absent**, never `0`, matching the
`unpack_inputs` convention so the "no GPU" path keeps exercising the
key-missing branch.

## 7. The CUDA image

`images/pytorch-cuda`, built and published by the existing images workflow:

```
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04
+ python3, torch cu124
```

CUDA **12.4** rather than the 12.8 the July RunPod validation used: 12.4 is
the widest currently-supported driver floor, and a volunteer's driver is
something we do not control. A host too old for 12.4 simply fails the
`doctor` image-pull check rather than failing tasks.

## 8. `flashnode doctor`

`doctor.py` (added 2026-08-02 in a parallel session) gates `flashnode work`
fail-closed on six checks. GPU adds a **seventh, non-gating** check: report
what `nvidia-smi` sees, or say plainly that no GPU was detected.

Non-gating is deliberate. Most volunteers have no GPU and must keep taking
CPU work; a GPU check that blocked `work` would lock out the entire existing
fleet. It exists so a host who *believes* they contributed a GPU can find out
they did not — which is exactly the failure `doctor` was written for.

## 9. Definition of done

1. `nvidia-smi` present ⇒ `capabilities.gpus` lists one `GpuInfo` per device.
2. `nvidia-smi` absent, or failing, or emitting unparseable output ⇒ `[]`,
   never a crash and never a guess.
3. A `gpus: 1` job is **refused** on a node advertising `[]`.
4. The same job is **placed** on a node advertising one GPU.
5. `gpus: 2` is refused on a one-GPU node.
6. A job with no GPU requirement is placed on both.
7. `flashml.yaml resources.gpus: 1` reaches `task.payload["gpus"]` — asserted
   across the full chain, not at either end (§2.1).
8. `harden_args` emits `--gpus 1`, and emits nothing when none are requested.
9. `images/pytorch-cuda` builds and `torch.cuda.is_available()` is True in it.
10. **On a real rented GPU:** a `gpus: 1` job submitted through the cloud API
    is claimed by a GPU host, runs in the CUDA image, and reports a device.
11. Suites green: flashruntime, flashnode, apps/api, e2e.

## 10. Out of scope

1. **Model / VRAM / compute-capability matching.** The fields are collected;
   nothing matches on them. Matching rules need a mixed fleet to test against
   and we have one machine.
2. **Multi-GPU distributed training on volunteer nodes.** Already excluded by
   `docs/guides/donate-a-machine.md`; `nnodes > 1` still raises.
3. **GPU telemetry.** `GpuSample` stays unpopulated; this spec is about
   placement, not monitoring.
4. **Reserving GPU nodes for GPU work** (§4).
5. **The VRAM residue problem** (§5.1).
6. **AMD / ROCm / Apple Metal.** `nvidia-smi` only. An Apple Silicon host
   reports no GPU, which is honest — MPS is not what these jobs target.
