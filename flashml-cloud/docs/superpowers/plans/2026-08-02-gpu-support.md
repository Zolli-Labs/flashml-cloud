# GPU Detection and GPU-Aware Placement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A job can ask for a GPU, only GPU hosts receive it, and it actually runs on the device.

**Spec:** `flashml-cloud/docs/superpowers/specs/2026-08-02-gpu-detection-and-placement-design.md`

**Two repos.** Tasks 1–7 are in the PUBLIC `Zolli-Labs/flashml`; tasks 8–9 in the PRIVATE `Zolli-Labs/flashml-cloud`. Task 10 releases and re-pins. Task 11 validates on real hardware.

## Global Constraints

- **Public repo** `~/Work/Zolli-Labs/flashml` — Apache-2.0, goes to PyPI. No
  secrets, no private business logic, no references to flashml-cloud internals.
- **flashnode stays dependency-light.** The probe shells out to `nvidia-smi`.
  Do **not** add `pynvml`, `nvidia-ml-py`, `GPUtil`, or any package.
- **Hard rule 3 (flashruntime):** security-relevant wire fields fail closed.
- **flashml-cloud is on branch `feat/flashnode-doctor`** with a live working
  tree in a parallel session. Never switch/create/rebase branches there, never
  `git add -A`, and `git show --stat HEAD` before every commit.
- Baselines, measured 2026-08-02: flashruntime **542 passed, 7 skipped, 20
  deselected**; flashnode **257 passed, 6 deselected**; apps/api **444**;
  e2e **61**.
- **flashruntime tests need the venv on PATH.** `.venv/bin/python -m pytest`
  alone reports 1 failure in `test_sklearn_sweep_end_to_end`, because that
  test spawns a bare `python`. Always run:
  `cd flashruntime && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q`
  Getting this wrong looks exactly like a regression you caused.
- **`--gpus` must be absent entirely when no GPU is requested.** The existing
  flag list has to stay byte-for-byte identical for every job that exists
  today, exactly as `local_inputs` did.

---

### Task 1: `GpuInfo` and `ResourcesSpec.gpuPerTask`

**Files:** `flashruntime/flashruntime/protocol/v1alpha1.py`, `flashruntime/tests/test_protocol.py`

- [ ] **Step 1: failing tests** — a `GpuInfo` round-trips with only `index`
      set; `NodeCapabilities(gpus=[GpuInfo(index=0)])` validates; a raw dict
      still coerces (old agents on the wire); `ResourcesSpec().gpuPerTask == 0`
      and `gpuPerTask=-1` raises.
- [ ] **Step 2: run, watch fail**
- [ ] **Step 3: implement**

```python
class GpuInfo(BaseModel):
    """One GPU as the host's driver reports it.

    Every field but `index` is optional: a probe that cannot read a value
    says nothing rather than guessing. Typed now although v1 matches only on
    COUNT — the wire format is the expensive thing to change later, because
    these agents run on machines we cannot reach.
    """
    schema_version: Literal["v1alpha1"] = SCHEMA_VERSION
    index: int
    name: str = ""
    memory_total_mb: int | None = None
    driver_version: str = ""
    compute_capability: str = ""
```

`NodeCapabilities.gpus: list[GpuInfo]`. `ResourcesSpec` gains
`gpuPerTask: int = Field(ge=0, default=0)`.

- [ ] **Step 4: run, watch pass**
- [ ] **Step 5: commit**

---

### Task 2: The fifth placement gate

**Files:** `flashruntime/flashruntime/scheduler/__init__.py`, `flashruntime/tests/test_scheduler.py`

Read the existing four gates' docstring first. Match its structure and state
the polarity choice explicitly, as the other four do.

- [ ] **Step 1: failing tests** — one per DoD item 3–6, plus type confusion:
      `capabilities.gpus` absent / `None` / `"gpu"` / `{}` all count as none;
      a `gpus` requirement of `"1"`, `-1`, or `1.5` makes the task ineligible
      everywhere rather than crashing; `gpus: 0` runs anywhere.
- [ ] **Step 2: run, watch fail**
- [ ] **Step 3: implement**

```python
required = task.payload.get("gpus")
if required is not None:
    # bool is an int subclass — `True` must not read as "1 GPU"
    if not isinstance(required, int) or isinstance(required, bool) or required < 0:
        return False                      # type-confused requirement ⇒ fail closed
    if required > 0:
        advertised = (node.get("capabilities") or {}).get("gpus")
        if not isinstance(advertised, list) or len(advertised) < required:
            return False
```

- [ ] **Step 4: run, watch pass**
- [ ] **Step 5: commit**

---

### Task 3: `CommandRecipe.expand` forwards the requirement — BREAK-POINT

**Files:** `flashruntime/flashruntime/recipes/command.py`, `flashruntime/tests/test_recipes.py`

**This is the hop that failed open last time.** `expand` builds each payload
from a fixed key list and reads `spec.spec.workload.parameters`. `gpuPerTask`
lives on `spec.spec.resources` — a branch `expand` does not currently read at
all. Without this the gate sees `gpus` absent on every task and places GPU
work anywhere.

- [ ] **Step 1: failing test** — build a `JobSpec` with
      `resources.gpuPerTask = 2` and assert `tasks[0].payload["gpus"] == 2`;
      and with the default, assert `"gpus" not in tasks[0].payload`.
- [ ] **Step 2: run, watch fail**
- [ ] **Step 3: implement**, after the `local_inputs` forward:

```python
        gpus = spec.spec.resources.gpuPerTask
        if gpus:
            # Absent stays absent, never 0 — the no-GPU path must keep
            # exercising the key-missing branch, as unpack_inputs does.
            payload["gpus"] = int(gpus)
```

- [ ] **Step 4: run, watch pass**  - [ ] **Step 5: commit**

---

### Task 4: The `nvidia-smi` probe

**Files:** create `flashnode/flashnode/inventory/gpu.py`, modify `flashnode/flashnode/inventory/capabilities.py`, create `flashnode/tests/test_gpu_probe.py`

**Interface:** `probe_gpus(run=subprocess.run) -> list[GpuInfo]` — the runner
is a parameter so the whole suite runs with no driver, exactly as `doctor.py`
parameterises its subprocess calls.

```
nvidia-smi --query-gpu=index,name,memory.total,driver_version,compute_cap \
           --format=csv,noheader,nounits
```

- [ ] **Step 1: failing tests** — parse two-GPU CSV output; `FileNotFoundError`
      (no driver) ⇒ `[]`; non-zero exit ⇒ `[]`; garbage/partial output ⇒ `[]`;
      a timeout ⇒ `[]`; `compute_cap` missing (older nvidia-smi) still yields a
      `GpuInfo` with the other fields. **Never raises, never guesses.**
- [ ] **Step 2: run, watch fail**
- [ ] **Step 3: implement.** Wrap everything in `try/except Exception`, pass
      `timeout=5` — a hung `nvidia-smi` must not hang registration.
- [ ] **Step 4:** wire it into `capabilities.py`, replacing
      `gpus=[],  # GPU probing is a documented follow-up; never guess.`
- [ ] **Step 5: run, watch pass**  - [ ] **Step 6: commit**

---

### Task 5: `--gpus` in `harden_args`, and BOTH runners — BREAK-POINT

**Files:** `flashnode/flashnode/executor/hardening.py`, `argv_runner.py`, `docker_runner.py`, `flashnode/tests/test_hardening.py`

**Both runners must forward it.** Last time the equivalent change was made in
`hardening` and neither runner passed the payload, so the feature was dead.
`doctor.py` also calls `harden_args` — it must keep working unchanged.

- [ ] **Step 1: failing tests** — `harden_args(..., gpus=1)` contains
      `--gpus` `1`; `harden_args(...)` with no `gpus` contains **no** `--gpus`
      and its flag list is byte-identical to before; `gpus=0` also emits
      nothing; **and a test per runner** asserting a payload carrying
      `{"gpus": 1}` produces a docker argv containing `--gpus`.
- [ ] **Step 2: run, watch fail**
- [ ] **Step 3: implement.** `harden_args(..., gpus: object = None)`, appending
      `["--gpus", str(n)]` only when `gpus` is a positive `int`. Non-int or
      negative ⇒ emit nothing (the placement gate already refused such a task;
      this is defence in depth, not validation).
- [ ] **Step 4:** both runners read `payload.get("gpus")` and pass it through.
- [ ] **Step 5: run, watch pass**  - [ ] **Step 6: commit**

---

### Task 6: A seventh, NON-GATING `doctor` check

**Files:** `flashnode/flashnode/doctor.py`, `flashnode/tests/test_doctor.py`

Report what `nvidia-smi` sees, or say plainly that no GPU was detected.

**It must not gate `flashnode work`.** Most volunteers have no GPU and must
keep taking CPU work; a gating GPU check would lock out the entire existing
fleet on upgrade. Follow whatever pattern `doctor.py` already uses to
distinguish informational from blocking checks — read it before writing.

- [ ] **Step 1: failing tests** — reports the count when the probe finds GPUs;
      reports "no GPU detected" without failing when it finds none; **`work`
      still starts on a GPU-less host.**
- [ ] **Step 2–4: fail → implement → pass**  - [ ] **Step 5: commit**

---

### Task 7: `images/pytorch-cuda`

**Files:** create `images/pytorch-cuda/Dockerfile`, modify `.github/workflows/images.yml`, `images/README.md`

```dockerfile
FROM nvidia/cuda:12.4.1-runtime-ubuntu22.04
# 12.4, not the 12.8 the July RunPod run used: a volunteer's driver is not
# something we control, and 12.4 is the widest currently-supported floor.
```

Install python3 + `torch --index-url https://download.pytorch.org/whl/cu124`.
Mirror `images/pytorch-cpu`'s structure — read it first and match it.

- [ ] **Step 1:** write the Dockerfile  - [ ] **Step 2:** add to the images
      workflow matrix  - [ ] **Step 3:** document in `images/README.md`
      (note it is ~3GB and only useful to GPU hosts)  - [ ] **Step 4: commit**

**Do not attempt to build it locally** — no GPU here and the build is large.
CI builds it; Task 11 proves it runs.

---

### Task 8: `resources.gpus` in flashml.yaml — PRIVATE REPO

**Files:** `flashml-cloud/apps/api/flashml_cloud_api/compile.py`, `tests/test_compile.py`

`_resources` currently emits `cpuPerTask` / `memoryPerTask`. Add `gpuPerTask`.

- [ ] **Step 1: failing tests** — `resources: {gpus: 1}` ⇒
      `spec["resources"]["gpuPerTask"] == 1`; absent ⇒ key absent or 0;
      `gpus: -1`, `gpus: "one"`, `gpus: 1.5` all raise `CompileError` naming
      the field.
- [ ] **Step 2–4: fail → implement → pass**  - [ ] **Step 5: commit**
      (`compile.py` and `tests/` only — never `app.py`, never `apps/web`)

---

### Task 8b: Register the image and bump the tag — ADDED 2026-08-02

**Found by the Task 7 worker: no task owned this, and Task 11 cannot pass
without it.** The API can never emit a `pytorch-cuda` reference unless it is
in `CURATED`, so a `gpus: 1` job would compile to an image that does not exist
in the registry the agent is allowed to pull from.

**The tag lives in THREE places, not two**, and the third is newly discovered:

| File | Symbol |
|---|---|
| `flashml/.github/workflows/images.yml` | `IMAGE_TAG` |
| `flashml-cloud/apps/api/flashml_cloud_api/images.py` | `IMAGE_TAG` |
| `flashml/flashnode/flashnode/doctor.py` | `PROBE_IMAGE` (tag baked into the string) |

`images.py` documents that it must equal the workflow. **Nothing documents
`doctor.py`.** It keeps working after a bump because old tags persist, so the
drift is silent — a host doctor probing a tag two releases old while the fleet
runs current images.

- [ ] **Step 1:** add a `CuratedImage` entry for `pytorch-cuda` in
      `apps/api/flashml_cloud_api/images.py`, matching the `pytorch-cpu` entry's
      shape. Read that entry first.
- [ ] **Step 2:** bump `IMAGE_TAG` in **all three** files to the same new value.
      **The tag is immutable** — the workflow's guard fails a repush, so a bump
      is mandatory, not optional, for any image change.
- [ ] **Step 3:** make the drift impossible to repeat. `doctor.py` should
      derive its probe reference from a single tag constant rather than
      hardcoding one, or a test should assert the three agree. Prefer the
      former; a test that only catches drift after someone causes it is the
      weaker fix.
- [ ] **Step 4:** run `apps/api` and `flashnode` suites.
- [ ] **Step 5:** commit each repo separately.

🔒 **Ordering:** publishing the image turns the images workflow red until
`IMAGE_TAG` is bumped, because the immutability guard correctly refuses to
repush the three existing images at `2026.08.1`. Bump first, then publish.


---

### Task 9: The whole-chain test — THE POINT OF THIS PLAN

**Files:** `e2e/test_gpu_placement.py` (new)

Unit tests at either end are exactly what passed while `local_datasets` was
broken in the middle, three times. This test walks the entire chain in one
place and is the only thing that can catch hop 2 or 3 regressing.

- [ ] **Step 1: write it**

```
flashml.yaml resources.gpus: 1
  → compile            → spec.resources.gpuPerTask == 1
  → CommandRecipe.expand → task.payload["gpus"] == 1
  → IsolationAwarePlacement:
        node advertising 1 GPU   → PLACED
        node advertising []      → REFUSED
  → harden_args(gpus=1)  → argv contains --gpus
and a job with NO gpus requirement:
  → "gpus" not in payload, placed on both, argv has no --gpus
```

- [ ] **Step 2: run**  - [ ] **Step 3: commit**

---

### Task 10: Release and re-pin 🔒 HUMAN GATE

flashruntime **0.5.0** (new wire fields), flashnode **0.4.0** (floor
`>=0.5,<0.6`). flashnode must be tagged only **after** 0.5.0 is installable
from PyPI — index propagation lagged the workflow by minutes last time.

Then the three pins that must agree: `apps/api/pyproject.toml`, `render.yaml`
(**both** coordinator services — prod and dev), `Makefile` `FLASHML_PIN`.

🔒 Do not tag or push a release without the owner saying so.

---

### Task 11: Real GPU validation 🔒 HUMAN GATE (~$1)

`flashruntime/scripts/runpod_gpu_e2e.py` already exists from the 2026-07-23
2×RTX 4090 run. Extend it, or write a sibling, to prove **DoD item 10**: a
`gpus: 1` job submitted through the cloud API is claimed by a GPU host, runs
in `pytorch-cuda`, and reports a device.

The July run found a real GPU-only bug the CPU suite could not (batches must
move to `ft.device()`). Expect this one to find something too.

**Also settle the base-image question while a GPU is rented**, since it costs
nothing extra. The Task 7 worker measured `nvidia/cuda:12.4.1-runtime` at
**1466 MB compressed** against **92 MB** for `-base-`, and torch's cu124
wheels ship their own CUDA runtime libraries as pip dependencies — so the
`-runtime-` base may be almost entirely redundant. That is ~1.4 GB per pull
on a volunteer's connection, which is exactly the argument `pytorch-cpu`'s
own header makes about image weight.

The spec names `-runtime-` and it stays for now: the failure mode of guessing
wrong is a job that runs here and fails only on a volunteer's machine. Build
both on the rented box, confirm `torch.cuda.is_available()` in each, and
switch only on evidence.

🔒 Costs real money. Owner triggers it.
