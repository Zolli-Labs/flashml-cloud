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

**The property that makes quorum safe** (verified numerically, 2026-07-31).
`reduce_deltas` divides by the sum of *reporting* samples, not by the round's
full shard count. That renormalization is what keeps partial participation
correct:

| Case | Result |
|---|---|
| Both workers report (100 samples → 12, 300 samples → 20, from base 10) | `18.0` — identical to textbook FedAvg, the sample-weighted mean of the *trained* weights |
| Only the 300-sample worker reports | `20.0` — exactly that worker's trained weights |

If the divisor were the full shard count instead, a lone participant would move
the weights only 1/N of the way toward its result, so every dropped machine
would quietly slow training with no error and no log line. Anyone "fixing"
`reduce_deltas` to divide by `num_shards` reintroduces exactly that.

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

---

## D11 — Untrusted-input validation belongs in the reduce path, not the caller

**Decided (2026-07-31, after the Plan 1 whole-branch review):** `fedavg_weights`
validates every value that crosses the volunteer boundary — per-contribution
sample counts must be `> 0`, and all weight/delta values must be finite —
raising typed errors rather than computing on bad input.

**Why:** the whole-branch review found three Critical defects that per-task
reviews structurally could not, because each saw only its own diff. Two were
demonstrated, not theorised:

| Defect | Demonstrated effect |
|---|---|
| `reduce_deltas` validated only the *total* sample count | A node reporting `samples = -999` beside an honest peer produced a weight of **999001.0** where the correct step was **1.0** — a ~10⁶× amplification from a single integer, no lie about the delta needed |
| Python's `json` parses `NaN`/`Infinity` | One non-finite delta made every weight NaN, every later round trained from NaN, and `run_fedavg` reported **success** throughout |

The NaN case needs no attacker at all: a learning rate that diverges on one
shard does it. That is what moved this from "harden later" to "fix now".

A third — quorum counting artifact keys by filename *suffix* rather than the
round's expected task set — let one worker mint multiple "participants" from a
single lease (flashnode uploads output trees recursively), and aggregated
attempts the coordinator had **rejected**, since uploads happen before commit
acceptance.

**Cost:** the reduce path now rejects inputs it previously averaged. Honest
arithmetic is unchanged — the demo reports byte-identical numbers before and
after (`0.5361 → 0.1757`), which is how we know the fix did not alter results.

**Revisit when:** result verification (M3) lands. That addresses a *different*
threat — a node that lies plausibly — and does not subsume these, which are
about inputs that are malformed rather than dishonest.

**Deliberately NOT fixed here, because [[poc-stack-supabase-render-not-alibaba]]
Plan 2 addresses them properly:** artifact `PUT` is unauthenticated and not
lease-scoped, and the authoritative global model sits at a predictable key
(`jobs/{job_id}/round-{r:03d}/weights.json`) inside a volunteer-writable
namespace. A partial fix now would duplicate Plan 2's work and create a false
sense of coverage. **Nothing may face the public internet before Plan 2 lands.**

---

## D12 — Cross-repo seams need a test that imports both sides

**Decided:** invariants spanning flashruntime and flashnode are pinned in the
workspace `e2e/` suite, which may import both, rather than mirrored by hand in
either repo.

**Why:** `fedavg_worker` was added to the coordinator's `ALLOWED_TASK_MODULES`
but not to flashnode's `DEFAULT_ALLOWED_MODULES`. Each list was correct in
isolation, both suites were green, and even the flashruntime convergence test
passed — because it drives a hand-rolled urllib agent that does not enforce
flashnode's allowlist. A *real* agent refused every task, burned all four
attempts per shard, and the job FAILED. Federated averaging was completely
non-functional on genuine agents while everything reported green.

This is the failure the 2026-07-29 entry predicted, and structurally the same
shape as the `argv_capable`/`module_capable` polarity bug: a seam *between*
components where each side is individually defensible.

`flashnode/tests/test_allowlist_drift.py` can only mirror the list by hand —
`flashnode/AGENTS.md` scopes its flashruntime dependency to the `protocol`
package. So the live guard is `e2e/test_allowlist_parity.py`, which imports both
allowlists and asserts `ALLOWED_TASK_MODULES <= DEFAULT_ALLOWED_MODULES` (that
direction: the coordinator dispatching something the agent refuses is the
outage; the reverse is harmless).

**How to apply:** when adding anything that must agree across repos — a workload
module, a capability field, a wire constant — add the parity assertion to `e2e/`
in the same change. A mirrored copy is not a guard.

---

---

## D13 — FlashML gets its own Supabase project, not an existing one

**Decided (2026-08-01):** created **`flashml-poc`**, project ref
**`yualksqjjvlfscbbsygq`**, region `us-east-1`, in the `Zolli AI` org
(`pnctfuztwhlclvjdmzal`). Cost confirmed **$0/month** (free tier).

**Why not the existing projects:** the org already holds `ZolliIAI-Prod`
(`sgyrzypimyullipjxgvo`) and `ZolliAI-Dev` (`ohqkajtzefseyrafzbfj`, paused).
Both belong to a **different product** — a real-estate CRM with 37 tables
(`houses`, `marketplace_listings`, `crm_contacts`, `showing_logs`,
`gmail_connections`). Critically, that schema already defines
`public.profiles`, which is one of the tables the FlashML schema needs (§4 of
the spec). Putting FlashML there would collide on that table and mix a POC's
auth and data into an unrelated product's database.

**How to apply:** all Plan 3 schema, RLS, and auth work targets
`yualksqjjvlfscbbsygq`. **Never** apply FlashML migrations to
`sgyrzypimyullipjxgvo` — it is a live production project for another product.

**Revisit when:** the POC graduates and needs its own org, or the free tier's
limits bind.

---

## D14 — Operator-asserted node identity for the private coordinator, not per-machine tokens registered into it

**Decided (2026-08-01, Plan 3 Task 6):** now that D2 has made
`flashml-coordinator` a private service, the cloud API is the only thing that
can reach it, and it holds exactly one credential for that: an operator
token. Every agent request the API forwards carries that operator token
**plus** `X-FlashML-On-Behalf-Of: <node_id>`, where `node_id` is resolved
from the caller's machine token (never from the request body — the rule
Plan 2 already learned the hard way on `claim`). The coordinator
(`flashruntime/flashruntime/service/modea.py`, Plan 3 Task 4) honours that
header **only** when the caller presents the operator credential; a node
token presenting the same header has it silently ignored, so a volunteer can
never assert another machine's identity. Once the header is accepted, the
coordinator authorizes exactly as it did before the indirection existed — the
write must fall inside a live lease held by that node (`live_leases_for_node`)
— so Plan 2's lease-scoping guarantee survives being fronted by an API that
didn't exist when that guarantee was proven.

**Why not the alternative (dynamic per-machine token registration into the
coordinator):** the API could instead mint a token per enrolled machine and
push it into the coordinator's own auth store at enrolment time, then let
agents authenticate to the coordinator directly with it. Rejected because it
recreates the exact shape of bug this plan exists to close: two systems
(`flashml-cloud`'s Postgres `machines` table and the coordinator's runtime
auth state) would each hold an independent, mutable opinion about which
tokens are valid for which node. Revocation would need to invalidate both —
miss the coordinator's copy (a cache that was never told to expire, a push
that failed silently, a restart that reloaded a stale snapshot) and a
revoked machine keeps working *at the coordinator* even though the cloud API
correctly shows it as revoked. Operator-asserted identity has no second copy
to go stale: revocation is one row flip in one table
(`enrolment.revoke_machine`), and the very next request through the API's
`current_machine` dependency sees it, because there is nothing else to ask.

**Cost:** the coordinator now trusts the API completely for identity claims —
if the operator token leaks, the leak is total (any node, any lease). This is
why the API validates `node_id` at both ends before it ever becomes a header
value (`NODE_ID_RE` at enrolment time, `valid_node_id` again in
`CoordinatorClient.forward`) — a CR/LF in it would be request splitting
against a service the whole security model now depends on trusting blindly.

**The coordinator must be unreachable from the internet once deployed.**
This decision is not a substitute for D2's private-networking requirement —
it is what makes the private-networking requirement *sufficient* rather than
merely convenient. Operator-asserted identity is safe only because the
operator token itself is unobtainable by anyone who isn't the cloud API. If
the coordinator is ever given a public IP or its Render service's networking
is loosened, the operator token becomes reachable from the internet and every
node's lease scoping is void — an attacker holding it can assert any
`node_id` at will. Render deploy (Plan 7) must configure
`flashml-coordinator` with no public ingress and verify that from outside the
private network before anything is called done.

**Revisit when:** result verification (M3) exists and volunteer nodes are no
longer implicitly trusted to report their own results honestly — at that
point the same "who can assert what" question reopens one layer up, not at
the transport.

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
