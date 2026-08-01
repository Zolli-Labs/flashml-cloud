# M1 sprint — decision record

> **Purpose:** the *why* behind the M1 (deployed multi-user POC) design, written
> during the 2026-07-31 design session so later reviewers can re-open a decision
> deliberately instead of rediscovering it. Companion to
> `flashml-cloud/docs/superpowers/specs/2026-07-31-deployed-multi-user-poc-design.md`
> (the what) and `PROGRESS.md` (the evidence log).
>
> Format per decision: **what was decided**, **why**, **what it costs**, and
> **what would make us revisit**. A decision with no revisit trigger is a
> decision nobody can safely change later.

---

## D1 — POC ships on Supabase + Render, not Alibaba Cloud

**Decided:** Use Supabase (Auth, Postgres, Storage) and Render (three services)
for the deployed POC. Alibaba is deferred.

**Why:** `SPRINT_PLAN.md` Days 4–7 and `PLAN_2WEEKS.md` Stage 5 gated the entire
cloud milestone on Alibaba credentials. Those credentials have never existed in
this workspace — no `.env.alibaba`, no `aliyun`/`ossutil` CLI. The milestone was
blocked on a Day-0 dependency that never landed, so the "cloud" half of a
two-week plan stayed at zero while the local half finished. Supabase and Render
cost nothing the owner does not already hold and yield a public URL immediately.

**Cost:** `flashml-cloud/infra/alibaba/` (ACK/ACR/OSS/SLS manifests) stays
unexercised and drifts further from reality with every change to the deployed
path. The `S3CompatibleArtifactStore` / `OSSArtifactStore` split in
`flashruntime/artifacts/` is what keeps the drift bounded — artifacts stay
backend-neutral behind `artifact://` URIs.

**Revisit when:** Alibaba credentials actually exist, or a customer requires
mainland-China residency. Note that a mainland deployment additionally needs an
ICP filing (China entity, weeks) before any domain can serve traffic — that is a
business dependency, not an engineering one.

---

## D2 — The coordinator is a *private* service; the cloud API is the only public door

**Decided:** `flashml-coordinator` runs on Render with **no public URL**. Agents
and browsers reach it only through `flashml-api`.

**Why:** This retires the top-ranked risk structurally rather than patching it.
`HANDOFF.md` risk #2 and the 2026-07-29 deferred follow-up #1 record that
`PUT /v1alpha1/artifacts/{key}` is unauthenticated and not lease-scoped: any
registered node can overwrite another job's commit artifact or checkpoint
manifest, and the sha256 check is no defense because the attacker supplies both
the file and the hash. The standing guidance was "do not put the current
coordinator on a public IP for longer than a demo." Removing public ingress
means that surface is not internet-reachable at all, and the API enforces lease
scoping before forwarding.

It also preserves `HANDOFF.md` risk #5 (`LeaseManager`/`SqliteLeaseStore` are
safe only under a single event loop — never more than one uvicorn worker) *by
construction*: one private instance, one worker. A future autoscaling decision
cannot silently violate it.

**Cost:** All agent traffic — including artifact uploads — proxies through the
API, adding a hop. Acceptable at POC scale; revisit if upload throughput becomes
the bottleneck. Cheap to build because `CoordinatorClient`
(`flashnode/executor/client.py:29`) already takes a base URL.

**Revisit when:** upload volume makes the proxy hop expensive, *and*
lease-scoped signed upload URLs (direct agent → object store) have been built.
Do not re-expose the coordinator without that.

---

## D3 — Roles are capabilities on one account, not two account types

**Decided:** One login. `is_host` flips when a machine is enrolled,
`is_developer` when a job is submitted. Code keeps the **Host** / **Developer**
vocabulary; the UI phrases them as actions ("Share my machine" / "Run my
workloads").

**Why:** The owner asked for "two different profiles." Separate account types
would force anyone who both donates a machine and submits work to hold two
logins, and would double every ownership query. The vocabulary is not invented:
`flashml-cloud/docs/SYSTEM_OVERVIEW.md:39` and flashnode's README already
standardize on Host/Developer, so inventing new words would drift three repos'
docs at once.

**Cost:** The UI must make the two modes legible without separate accounts to
lean on.

**Revisit when:** hosts and developers need genuinely disjoint onboarding,
billing, or legal terms.

---

## D4 — Per-machine opaque tokens now; Ed25519 deferred

**Decided:** `flashnode login` runs a device flow (like `gh auth login`) and
stores an opaque bearer token. The shared `FLASHNODE_JOIN_CODE` is retired for
the deployed path.

**Why:** A single shared join code with no revocation cannot survive public
signup. `PROGRESS.md`'s own "Next" was already slice B (per-node identity), so
this aligns with the existing plan. Opaque tokens are instantly revocable
(`status='revoked'`) and simple to debug. Ed25519's real advantage — the server
never holds a verifier secret — matters at scale, not at POC size, and a token
stored on a machine is exactly as stealable as a private key stored on the same
machine.

**Cost:** The server holds hashed shared secrets. `flashnode/identity/` already
documents signing keys as the intended future step; that seam is left intact so
adopting them needs no data migration.

**Revisit when:** the host count makes a server-side secret store a meaningful
breach target, or a host requires hardware-backed keys.

**Load-bearing sub-decision:** the API resolves `node_id` **from the token**,
never from the request body. `CoordinatorClient.claim()` sends `node_id` in the
body (`client.py:91`); the API must overwrite, not validate, that value.
Trusting it would let any authenticated agent impersonate another machine.

---

## D5 — Code reaches workers as a repo tarball run in curated images, not a built image

**Decided:** Connect a GitHub repo; `flashml.yaml` names one of ~3 curated
images and a command; the API fetches the tarball and stages it as an
`artifact://` input at `/work/inputs/`. No image build.

**Why:** The owner asked for a Vercel/Render-style "connect your repo" flow.
Building a custom image per user needs a build host with Docker (Render standard
instances cannot), a registry, a build queue, gigabyte pulls for volunteers, and
allowlist propagation to every node. The tarball path needs none of it and is
the contract `docs/guides/bring-your-code.md` already specifies. A curated set is
also a *feature* for a volunteer network: hosts pre-pull three known images
rather than arbitrary images from strangers.

**Cost:** Users are limited to libraries present in the curated images. Preflight
(§5.3) exists to make that limit legible at submit time instead of as a failure
on someone else's machine.

**Revisit when:** a real user hits the dependency wall. `flashml.yaml`'s `image:`
field is the seam — it stops being restricted to aliases.

---

## D6 — Distributed training is federated averaging, not relayed DDP

**Decided:** One model trains across volunteers by exchanging weight deltas once
per round through the coordinator. Per-step DDP across volunteers is out of
scope, permanently.

**Why:** The owner asked whether rank communication could go "through the API."
It cannot, for two independent reasons. First, latency: 50–200 ms round trips
against hundreds of steps per second means communication cost exceeds compute by
orders of magnitude — this is physics, not a scheduling deficiency. Second,
`--network none` (the guarantee that a stranger's code cannot use a donated
machine to reach anything) means ranks cannot rendezvous at all;
`docs/guides/bring-your-code.md` already records that `mode: "coordinated"` is
unavailable on volunteer nodes.

Exchanging every N steps instead of every step cuts communication 100–500× and
tolerates home links. Critically, **this is the shape flashruntime already
has**: `flashml_workloads/kmeans_driver.py` — "One iteration = one Mode A job (N
independent shard tasks); the driver reduces the shard partials… pipelines are
jobs chained by a driver, not a new execution mode." FedAvg is that loop with
`reduce` swapped, inheriting its recovery properties unchanged.

**Cost:** FedAvg converges differently from synchronous SGD, and on non-IID
shards it can converge worse. Multi-GPU DDP remains available only on owned or
trusted nodes on one fast network.

**Revisit when:** owned/trusted fast-networked capacity exists (then DDP is
simply a different placement target, not a redesign).

---

## D7 — FedAvg rounds aggregate on a quorum, unlike K-means

**Decided:** The driver aggregates once `min_participants` of `N` shards commit,
or the round deadline passes. Deltas arriving after aggregation are **discarded**.

**Why:** `kmeans_driver` requires every shard (`if len(partials) !=
len(shard_uris): raise`). That is correct for a controlled local run and wrong
for a volunteer pool: one friend closing a laptop would stall every other
participant's round indefinitely. Partial participation is standard FedAvg
practice. It is also the cheap answer to heterogeneous hardware — uneven
machines degrade participation rate rather than blocking progress — which is why
M1 does **not** need M2's admission probes.

Late deltas must be discarded rather than carried forward because they were
computed against weights that no longer exist; applying them would silently
corrupt the average.

**Cost:** A consistently slow machine may rarely contribute, and its data is then
underrepresented. M2's capability-proportional shard sizing is the fix.

**⚠ Do not "harmonize" this with `kmeans_driver`.** The asymmetry is deliberate
and load-bearing. This mirrors the 2026-07-29 lesson where `argv_capable` and
`module_capable` were given deliberately *opposite* polarities and the code had
to warn future readers against unifying them.

---

## D8 — `fedavg_driver` ships in flashruntime (public), not flashml-cloud (private)

**Decided:** The driver lives in `flashruntime/flashml_workloads/`, beside
`kmeans_driver`. flashml-cloud only invokes it as a hosted background task.

**Why:** `CLAUDE.md`'s boundary principle: "The open runtime must stay genuinely
useful without this cloud… This repo wins by operating the network better, not
by crippling the public repositories." Federated averaging is a runtime
capability, not a commercial one — a self-hosted user running `flashml serve`
should get it. Putting it in the private repo would be exactly the crippling the
boundary principle forbids.

**Consequence:** The driver must not import torch (it runs inside `flashml-api`,
which stays light) and must not know about Supabase or the cloud schema. Weights
therefore cross the wire as JSON rather than `torch.save`, at ~2 MB per delta per
round for the demo model. Binary encoding is an M2 optimization.

**Revisit when:** delta size becomes a real transfer cost — then add a binary
codec behind the same interface, still in flashruntime.

---

## D9 — Windows in M1; GPU deferred to M1.5

**Decided:** macOS, Linux, and Windows hosts in M1. GPU pool support deferred.

**Why:** The owner's testers are "mostly mac and window users," so Windows is
load-bearing for the acceptance bar, not a nice-to-have. GPU is not: the loop can
be proven on CPU, and GPU needs NVIDIA hardware to verify honestly.

**Correction worth preserving:** an earlier draft of this analysis claimed "GPU
is not supported." That was wrong and was corrected. `scripts/runpod_gpu_e2e.py`
proved flashruntime's CUDA path on real hardware on 2026-07-23 (2×RTX 3090,
nccl DDP, `_resolve_device` → `cuda:0`, GPU kill-and-resume from a step-40
checkpoint, `pytest_rc: 0`, $0.04). What is missing is only *pool placement*:
`flashnode/inventory/capabilities.py:99` hardcodes `gpus=[]`, no runner passes
`--gpus`, and there is no GPU placement gate. The hard part is done; the gap is
plumbing. The RunPod harness never starts an agent or claims a lease — it SSHes
in and runs pytest — which is why it does not close the pool-placement gap.

**Windows specifics:** `flashnode/executor/hardening.py:60` builds
`--user {os.getuid()}:{os.getgid()}`; neither exists in Python on Windows. The
flag becomes platform-conditional — **which is only safe because the curated
images declare a non-root `USER`**. Dropping the flag without that would
silently run containers as root. Plus Windows bind-mount path translation.

---

## D10 — M1 is executed as seven separate plans, not one

**Decided:** M1 splits into seven plans: (1) FedAvg driver, (2) agent identity +
lease-scoped writes, (3) cloud API + Supabase, (4) GitHub→job + preflight,
(5) web app, (6) Windows hosts, (7) deploy + acceptance.

**Why:** Each produces working, testable software on its own. Plan 1 goes first
because it is the largest unknown and needs *zero* infrastructure — if federated
averaging across leased tasks does not converge, nothing downstream matters, and
that is discoverable locally in an afternoon rather than after a week of
Supabase and Render work.

---

## Open questions carried into execution

1. **Demo dataset** — MNIST (~11 MB) or CIFAR-10 (~170 MB) baked into
   `flashml-pytorch-cpu`? MNIST keeps every host's first pull fast; CIFAR demos
   better. Not blocking: Plan 1 uses synthetic data.
2. **Host invite gating** — result verification is M3, so until then a host can
   lie about results and be believed. Recommendation: gate *host* enrollment
   behind an invite while developer signup stays open. The M1 testers are the
   owner's friends, so this costs nothing now.
3. **Render tier** — `flashml-api` and `flashml-coordinator` must not sleep; the
   web service may.

---

## Environment findings (cost real time; recorded so they are not rediscovered)

- **The documented test baseline is only reproducible with the venv on `PATH`.**
  `.venv/bin/pytest -q` alone yields `1 failed, 319 passed, 4 skipped` — the
  failure is `test_examples_e2e.py::test_sklearn_sweep_end_to_end` raising
  `LaunchError: failed to start 'python'`, because `LocalLauncher` spawns
  `argv[0] = "python"` and invoking the venv's pytest directly does not put the
  venv on `PATH`. The same cause skips three torchrun tests. Correct invocation:
  `PATH="$PWD/.venv/bin:$PATH" .venv/bin/pytest`.
- The full suite including torchrun DDP tests takes **many minutes**; the fast
  subset (without them) runs in ~10 s. Budget accordingly in CI.
