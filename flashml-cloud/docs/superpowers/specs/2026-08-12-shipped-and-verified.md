# Shipped 2026-08-12 — and what was verified against the live system

Everything below is **deployed to dev** (`d9ee388`) and was exercised against
real infrastructure, not only tests. Where something is proven, the evidence is
named. Where it is not, it says so.

---

## 1. Fault tolerance was never on. Now it is.

`flashnode/executor/loop.py:466` gates **both** the checkpoint relay and
`resume.json` staging on `payload["checkpoint"]`. Nothing in the cloud ever set
it — absent from `ALLOWED_KEYS`, never emitted by `compile.py`, zero callers.
**Every job submitted through the console shipped no checkpoints while it ran
and restarted from step 0 after machine death.**

`e2e/test_training_resume.py` stayed green throughout, because it hand-builds
its JobSpec and bypasses the authoring surface. It tested the runtime, not the
product.

Both compilers now emit `parameters["checkpoint"] = {}` unconditionally. There
is deliberately **no `checkpoint:` key** — the relay's directory and glob are
hardcoded in the agent, so a key could only accept values nobody reads; it is
refused with an explanation instead. Two preflight WARNINGs and a guide section
teach the convention.

**Verified live:** job `593a9c0d9e33` — 10 checkpoints relayed during the run,
manifest committed on the coordinator at **step 2810, `hash_verified`**.

## 2. Artifacts are real and served from OSS

There was no browser listing route; the console read `job.artifacts`, which is
always `[]` for lease-backed jobs, so the Artifacts card was empty for every
job ever run.

Added `GET /jobs/{id}/artifacts` (viewer-checked) and
`GET /jobs/{id}/artifact-url/{key}` returning a presigned OSS URL, with the
coordinator proxy as fallback. A **sibling** path segment, not a suffix:
`{key:path}` is greedy, so `.../{key}/download-url` would make a file named
`download-url` permanently unreachable.

A mid-flight bug worth remembering: the first design used a plain `<a download>`
so the anchor could follow a 307. **A browser navigation carries no
`Authorization` header, so every download 401'd.** The fix is an authenticated
JSON call for the URL, then navigation to OSS.

**Verified live:** 92 objects in `zolli-flashml-artifacts-zrs`, `_mirror/
manifest.json` written **last** per job. `artifact-url` → 200, anonymous fetch
of the presigned URL → 200 with
`Content-Disposition: attachment; filename="task-000__metrics.json"`.

## 3. The router is visible

`preview-plans` was fully built and the console called it **zero times**. Now on
the job page's Placement tab: kind, one-sentence evidence, all four venues with
verbatim reasons. `suited: false` ("Can't run this work") and
`acquirable: false` ("No capacity we can reach") render as different
statements — collapsing them would imply we *chose* not to use a venue we
cannot reach.

Pre-submit preview was investigated and deliberately **not** built:
`compile_to_jobspec` has one call site, and compiling without network dataset
resolution changes the task count that feeds the evidence sentence. A preview
claiming 40 trials for a job that submits as 4 is the lie the honesty rules
forbid.

## 4. Datasets, live from OSS

Two buckets, separated by exposure and never to be mixed:

| bucket | exposure | holds |
|---|---|---|
| `zolli-flashml-artifacts-zrs` | Block Public Access **ON** | job artifacts |
| `zolli-flashml-datasets` | `public-read` | public demo data only |

**Verified live:** the resolver pins the manifest at `sha256:b51ad06e…`; 6 train
shards + 1 holdout; the host agent fetched them anonymously and sha256-verified
each before the sandbox closed.

## 5. Four workload types succeeded on deployed infrastructure

Repo: `Zolli-Labs/flashml-demo-suite`, one branch per workload — `flashml.yaml`
is resolved at the **repo root** only, so four workloads need four roots.

| Workload | Classified | Result |
|---|---|---|
| train | COMMAND | 10 checkpoints, manifest at step 2810 |
| hpo | **HPO** | 6/6 trials, best **0.81075** (`hidden=16`) vs 0.811 Bayes ceiling |
| federated | **FEDERATED** | 4 tasks across rounds |
| evaluate | **EVALUATION** | 0.4888 from `untrained-initialisation` — the documented floor |

`no-checkpoint` preflight warnings reached the submitter at submit time on
`evaluate` and `federated`.

## 6. Bugs found by running, not by testing

- **Flat glob vs nested layout.** Shards land at `train/*.npz`; every script
  globbed `*.npz` non-recursively and found nothing. The fix needed a second
  change nobody would guess: with `rglob`, `holdout/eval.npz`.name is
  `eval.npz` — no marker — so the **holdout would have been trained on**.
- **`X` vs `x`.** Dataset written with uppercase `X`, workloads read lowercase.
  Two agents, two conventions, a `KeyError` inside a container.
- **`render.yaml` literals do not exist on a service unless the blueprint
  syncs.** `OSS_ENDPOINT` is declared as a plain `value:` and had never been
  applied to `flashml-dev-api`. Secrets were set by hand and correct; the
  *non-secret* was missing, and `oss_configured` needs all four. **Audit the
  other literal-declared vars on both services.**
- **NOT_CONFIGURED logs nothing** while every mirror *failure* logs. So "OSS is
  broken" and "OSS is not configured" are indistinguishable from outside. One
  startup line would have saved a diagnostic round trip.

## 7. Not proven yet

1. **The console UI has never been opened.** Every verification above went
   through the API. The routing and artifacts cards are tested and
   type-checked; nobody has looked at `flashml-dev-web`.
2. **One venue.** Every task ran on `fn-da2f96c2de8e4408`, a laptop. RunPod and
   the Alibaba sandbox are unexercised.
3. **Resume after machine death is unproven on dev.** Byte-identical resume is
   proven locally across two machines with a real `SIGKILL`; on dev nothing has
   died yet. This is the headline claim and deserves a deliberate test.
4. **A federated run's artifact list is empty by design** — per-round keys would
   not compose with the fetch route.
5. **The marketplace has no HTTP surface at all.** See
   `2026-08-12-console-ui-plan.md`.

---

## 8. Cross-venue experiment — 2026-08-12, and §7 items 2 and 3 are now closed

Four machines, four venues, one pool. Total rental **~$0.89**.

| Venue | Machine | Tier |
|---|---|---|
| owned | laptop, 10-core arm64, no GPU | sandboxed (Docker) |
| runpod | RTX 4090, Iceland | trusted (unsandboxed argv) |
| runpod | RTX 3090, Czechia | trusted |
| runpod | RTX 4000 Ada, Iceland | trusted |

**Fan-out across venues.** The 6-trial HPO sweep completed **2 on the RTX 4000
Ada, 2 on the RTX 3090, 2 on the laptop** — one job served by three venues and
by two different isolation tiers at once.

**Routing by hardware, not preference.** `gpu-train` (`resources: {gpus: 1}`,
classified TRAINING) ran **only** on a GPU machine. The laptop was never
eligible. Proof from the task's own stdout: *"CUDA device 0: NVIDIA GeForce RTX
4090 — compute capability 8.9, 23.5 GiB VRAM"*.

**Resume after machine death, on deployed infrastructure.** Previously proven
only locally. The `gpu-death` job (200 epochs, checkpoint per epoch) was leased
on the RTX 4090; the pod was **destroyed outright** mid-run. The task was
reclaimed by the **RTX 3090** ~30 s later and reported:

> `RESUMED at step 16298 (epoch 58/200) — that much work survived the machine dying.`
> `epochs_executed 142` (of 200), finishing on a different card, a different
> generation, in a different country.

58 epochs of work crossed a machine death and a card change without being
recomputed.

**Which half of that is ours, and which is the task's — corrected 2026-08-12.**
The sentence above is safe to say about *this* run because we wrote the task.
It is not safe to generalise, and the step number in particular is not a
control-plane measurement.

The checkpoint relay discovers steps by globbing `step-*.json` under
`workdir/out/ckpt` (`preflight.py:125`, `:149`) — **filenames the task's own
code writes.** So `step` has a name we chose and a value the submitter chose.
The `\d+` in `_CKPT_CONVENTION` is a static advisory inside preflight, not an
enforced guarantee: `2026-08-11-open-gaps.md` §5 item 4 records a non-numeric
`step-*.json` re-failing every 0.3 s for a whole task, so non-integer step
names occur at runtime.

Split by who assigned the value:

| Ours — assigned by this codebase | The task's — self-reported |
|---|---|
| the pod was destroyed | `step 16298` |
| a different machine claimed the lease ~30 s later | `epoch 58/200` |
| which machine, in which venue and region | `epochs_executed 142` |
| lease transitions, attempt counts, timings | |
| the job completed | |

**The story survives intact on the left column alone**, and that is the version
to put in front of anyone: *a job's host was destroyed mid-run, a different GPU
in another country picked the work up about thirty seconds later, and the job
finished.* Every fact in that sentence is one this codebase assigned.

Quote the right-hand column as the task's own stdout, attributed — never as a
figure the platform verified.

**The general test**, adopted with the public share page (D16.1): *can you name
the line in our code that assigned this value, with no user input reaching it?*
Numbers are not safer than strings here; they are just harder to notice.

### Constraints this experiment discovered

1. **`allowFallback` iff `pool`.** Unsandboxed machines are eligible only for
   **pool-scoped** jobs. Rented capacity sits idle for a public job no matter
   how much of it is online — the four earlier runs could only ever reach the
   laptop. This is the single most important operational fact here.
2. **CPU pods cannot deploy from a RunPod template**, and `create-pod` has no
   start-command field, so a CPU pod cannot be given a bootstrap through the
   API at all. A cheap GPU pod was the shorter path to a start command.
3. **The rented pytorch image crash-loops a naive `pip install`.** Its
   Debian-packaged `cryptography` has no RECORD file, so pip cannot uninstall
   it to satisfy flashnode's dependency, exits non-zero, and `set -e` restarts
   the container every ~17 s. Install into a venv with
   `--system-site-packages`.
4. **Fetching the bootstrap over HTTP made the crash loop self-healing** — a
   push to the demo repo fixed a running pod without recreating it. Worth
   keeping deliberately.
5. Dev's coordinator is free-tier and its node registry is **in-memory**: a
   restart answers `unregistered node — register first` until agents
   re-register. Looks like a failure during a demo and is not.

### Found while summarising: mirrored artifacts go invisible when the coordinator forgets a job

`GET /jobs/{id}/artifacts` builds its listing from the **coordinator**, and
falls back to the coordinator for bytes. But `storage` and `mirrored_at` come
from our own row. So after the coordinator loses a job from its registry, a
mirrored job answers:

    state=None   files=0   storage="oss"   mirrored_at=2026-08-12T10:44:39Z

while OSS holds **185 objects** for that job id — every checkpoint, the model,
metrics, logs and `_mirror/manifest.json`. The data is safe and completely
unreachable through the product.

This is the failure the mirror exists to prevent, arriving one layer up: the
bytes survived the machine AND the coordinator, and the console still shows an
empty Artifacts card. **`_mirror/manifest.json` already contains the full key
listing** — when `mirrored_at` is set, the listing should be served from it
rather than from the coordinator, and the coordinator should be the fallback,
not the source of truth.

Belongs in the UI/API work: it makes the Artifacts card wrong precisely for
finished jobs, which are the ones people come back to look at.
