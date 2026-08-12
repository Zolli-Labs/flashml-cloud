# State of play after 2026-08-12, and the next building phase

**Read this before `2026-08-11-open-gaps.md`** — that document's status
sections were written before today's runs and several are now stale. Evidence
for every "proven" claim below is in `2026-08-12-shipped-and-verified.md`.

Submission deadline **2026-08-15**.

---

## 1. Proven on deployed infrastructure

Not "tests pass" — these ran on dev, against real machines, with receipts.

| | Evidence |
|---|---|
| **Fault tolerance** | 200-epoch job leased on an RTX 4090 (Iceland); pod **destroyed** mid-run; RTX 3090 (Czechia) reclaimed it ~30 s later and **resumed at step 16298**, executing 142/200 epochs |
| **Cross-venue fan-out** | one HPO sweep, 6 trials, **2/2/2 across three venues** and two isolation tiers simultaneously |
| **Routing by hardware** | `gpu-train` ran only where a GPU existed; the laptop was never eligible |
| **All five workload kinds** | COMMAND, HPO, FEDERATED, EVALUATION, TRAINING — each classified correctly, unprompted |
| **Datasets** | published to OSS, pinned at submit, fetched anonymously per machine, sha256-verified before the sandbox closed |
| **Artifact mirroring** | 185 objects for one job; `_mirror/manifest.json` written **last** |
| **Presigned download** | `artifact-url` → 200, anonymous fetch → 200 with a correct `Content-Disposition` |
| **Preflight** | `no-checkpoint` warnings reached the submitter at submit time |

Total rented spend for the whole experiment: **~$0.89**.

## 2. Alibaba: storage proven, compute NOT

Worth stating plainly because this is an **Alibaba** competition and it is easy
to overclaim from today's results.

**Proven:** OSS as the dataset origin (public bucket, anonymous
sha256-verified fetch by every machine) and as the artifact store (mirrored,
manifest-last, presigned downloads). Two buckets, deliberately separated by
exposure.

**Not proven:** the **FC Agent Sandbox as a compute venue**. Its configuration
is now complete on dev (`E2B_API_KEY`, region, `FC_SANDBOX_POOL_ID`, pool
created) and a sandbox was separately measured earlier in the week — create
p50 901 ms, pause 2.6 s, wake 1.1 s, a 45-minute hibernation survived with a
`flashnode` inside still claiming leases. **But no task in today's cross-venue
run executed on Alibaba compute.** Every task ran on the laptop or a RunPod
GPU.

`fc-gpu` remains `acquisition: NONE` — nothing creates an FC GPU function, so
that venue cannot carry a candidate at all.

## 3. The next building phase, in priority order

### 3.1 Console — blocked on another session, then owner testing

Handoff is `2026-08-12-console-ui-plan.md` with the paste-ready brief in
`2026-08-12-ui-handoff-prompt.md`. **Nobody has opened `flashml-dev-web` yet.**
Every verification to date went through the API. Owner will test the console
tomorrow once that session has run.

Two concrete defects already queued for it:

1. **Mirrored artifacts are invisible once the coordinator forgets a job.** The
   listing is built from the coordinator while `storage`/`mirrored_at` come
   from our own row, so a finished job answers `files=0, storage="oss"` while
   OSS holds 185 objects for it. `_mirror/manifest.json` already carries the
   listing and should be the source when `mirrored_at` is set. This hits
   **finished** jobs — the ones people return to look at.
2. **The marketplace has zero HTTP routes.** `marketplace.py` + `prices.py` =
   105 tests, no API surface. Not a frontend-only task.

### 3.2 Run one job on Alibaba compute

The single highest-value gap for *this* competition. The mechanism exists
(`sandbox_orchestrator.start_session` enrols a sandbox as an ordinary
FlashNode) but today it is only driven for evaluation sessions — "the
mechanism is general, the caller is not". Starting a sandbox worker manually
and binding it to the demo pool is the same recipe already proven for RunPod:
mint a token before the machine exists, seed `node-id` + `credentials.json`,
run `flashnode work --runner trusted`.

### 3.3 Coordinator durability

Dev's coordinator is free-tier with an **in-memory node registry and job
registry**. Restarts produce `unregistered node — register first` storms until
agents re-register, and jobs vanish from listings (see 3.1's defect). During a
live demo this looks like a product failure. Either move it off free tier for
demo day or rehearse around it knowingly.

### 3.4 Deferred, deliberately

- Pre-submit routing preview — needs a compile-only path that resolves
  datasets honestly; the cheap version misreports task counts.
- Per-round artifact shape so federated runs can list outputs.
- Private datasets — design recorded in
  `2026-08-12-private-datasets-design-note.md`, including the timing trap
  (URLs minted at submit, fetched at lease).
- Upstream flashnode fixes — see `2026-08-11-checkpoint-always-on.md` §4.
  These need a PyPI release and a four-site pin bump; not before Friday.

## 4. Standing operational facts

- **`allowFallback` iff `pool`.** Unsandboxed (rented) machines are eligible
  only for pool-scoped jobs. Submit publicly and rented capacity sits idle,
  silently, while billing. This is the one that costs money to rediscover.
- **`render.yaml` literals do not exist on a service unless the blueprint
  syncs.** `OSS_ENDPOINT` was missing while the hand-set secrets were correct.
  Audit the other literal-declared vars on both services.
- **`NOT_CONFIGURED` logs nothing** while every mirror failure logs, so "not
  working" and "not configured" are indistinguishable from outside.
- RunPod: CPU pods cannot use templates; community cloud would not schedule;
  the `runpod/pytorch` image crash-loops a naive `pip install` (Debian
  `cryptography`, no RECORD file) — install into a venv.
- Fetching the pod bootstrap over HTTP means a push repairs a **running** pod.
