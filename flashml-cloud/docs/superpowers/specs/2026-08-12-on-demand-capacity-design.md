# Renting on demand, and the time-versus-money plan

**Status: PARTLY BUILT as of 2026-08-12. Read this block before trusting any
claim below it.**

Layer 1's venue-agnostic core and §3.1's curve are implemented and reviewed
(12 commits, `c59d59f..7aac237`, 63 tests). **No cloud adapter exists and no
live rental has ever happened.** These statements in the text below are now
**false**, and are left in place only so the reasoning that produced them
survives:

| Where | Now false |
|---|---|
| §6 out-of-scope | "Relaxing `assert_pool_isolated`. Not required" — **it is required.** `provision_sandbox_machine` asserts isolation *internally* (`sandbox_identity.py:266`), so renting into a user's own populated pool is refused today. This blocks the feature's stated purpose and awaits an owner decision. |
| §2.2 | "Ephemeral machine identity … already revoke and unbind rentals" — **declined during implementation, correctly.** That sweep measures from `coalesce(last_seen_at, created_at)` with a 15-minute default, and a rented host has no `last_seen_at` until it has booted and pulled a multi-gigabyte image; it would revoke machines that are still starting. |
| §2.1 | `CapacityRequest` "carries … a deadline" — **no such field was built.** That dropped field is close to the cost backstop the wiring is now blocked on. |
| §2.3 | "job settles → `provider.release()` [best effort]" — **no settle path exists.** `release_capacity` has zero callers. |
| §5.2 | "Alibaba ECS GPU … the likely first provider" — **reversed** by the committed venue findings: FC GPU is disqualified outright, RunPod is recommended first, ECS is the runner-up and 4–8× the price. |
| D5 | "revoked, **expired**, or orphaned" — "expired" was deliberately replaced by heartbeat-derived windows, because age alone destroys running jobs. |
| §4.4 | "the operator-side spend ceiling … remains an owner decision" — a **default shipped anyway**. Both ceilings are *rates*, not totals. |

**The largest open risk is not in this document at all:** nothing bounds total
dollars. Both budget ceilings cap $/hr on *new* commitments; the sweep now
(correctly) never destroys a heartbeating machine; and flashnode keeps
heartbeating after its job ends. A successful rental therefore bills
indefinitely with nothing to stop it. See the plan's Task 6 step 5 gates.

Supersedes `2026-08-12-next-phase.md` §3.2 ("run one job on Alibaba compute"),
which proposed hand-starting an FC sandbox worker and binding it to the demo
pool. That does not work — `assert_pool_isolated` forbids it in both
directions — and it was aiming at the wrong target anyway.

Submission deadline **2026-08-15**.

---

## 0. The product, in the owner's words

> Users submit jobs and add all their resources. We show them the trade-off:
> adding more GPUs from the market accelerates the job but spends ZC; not
> renting preserves ZC and takes longer. Time against money. We charge ZC and
> route RunPod and Alibaba GPUs for them. If a group already has some
> compute, use that, and plan the whole training pipeline — how much more
> compute, under their time and their money.

**Sequencing, also the owner's:** automate the renting first; wire the
decision layer over it afterwards. This document follows that order, because
the decision layer is largely already built and the renting is not.

---

## 1. What already exists

This is the part worth reading before designing anything, because it is most
of the product.

### 1.1 The planner is built

`router/plan.py` is the frontier: *"which machines should be eligible, what it
costs, how long."* A plan already names a fleet and a split — *"eight
machines, roughly five trials each, 14.2 ZC, done in 38 minutes"* — and
already computes both ends of the trade-off:

- **cheapest** — a scan down a price-ordered list.
- **fastest** — a water-fill that equalises finish times.

`O(M log M + N log min(N, S))`, no LP solver, returns *"in well under a second
behind a submit button"*. The arithmetic collapses because the tasks it
targets are independent — HPO trials, eval shards.

### 1.2 The estimates are built and graded by evidence

`router/estimator.py` carries `estimate_task_seconds`, `planning_seconds`,
`project_across_class`, `hardware_class`, and a basis ladder that says how
much to trust a number: `measured` (this job, observed) → `estimated` (same
shape, seen elsewhere) → weaker rungs, plus reliability tiers and a
survivorship note.

### 1.3 The console already shows it

`preview-plans` was fully built and called zero times until today; it is now
on the job page's Placement tab, rendering all four venues with verbatim
reasons, and distinguishing `suited: false` from `acquirable: false` rather
than collapsing them.

### 1.4 The market has a surface

Market v2 (`c59d59f`) landed wallet, listings, a prices board with history and
depth, and `GET /machines/{id}/market-hint` — over a real API contract with
tests.

### 1.5 So the gap is one thing

Every GPU that has ever run a FlashML task got there because a person started
it. `router/venues.py:28`:

> **`acquisition` is load-bearing.** `automatic` means this API can bring
> capacity into existence; `manual` means a human starts it; `none` means the
> integration does not exist.

| Venue | GPU | `acquisition` today |
|---|---|---|
| `owned` | whatever enrolled | `automatic` — "already there, nothing to acquire" |
| `runpod` | real GPUs | `manual` — *"0 ResourceProvider implementations, searched not assumed"* |
| `fc-sandbox` | **none** (`gpuConfig: null`) | `automatic` |
| `fc-gpu` | up to Hopper 96 GB | **`none`** |

**No venue with a GPU is `automatic`.** The planner can already say "rent
three A10s and finish in 38 minutes" and nothing in this repo can create one.

---

## 2. Layer 1 — automated renting

### D1 — On demand. No persistent fleet.

Capacity is created when a job needs it and destroyed when the job is done.

Rejected: 2–3 always-on rented GPUs for marketplace inventory. A held RunPod
pod *"bills whether or not it is claimed"*, and paying for availability nobody
asked for is cost without matching revenue. On-demand also answers "which GPU
does the user want?" without guessing — the job states its requirement.

### D2 — `--runner trusted`, bound to the submitter's own pool.

**Not Docker.** An earlier draft of this spec required `--runner argv` so
rented hosts could advertise `sandbox_capable` and take public jobs. That is
not achievable: `--runner argv` needs a Docker daemon, and a rented host is
itself a container. `trusted_runner.py` says so in its opening line — *"hosts
that CANNOT run Docker — Colab notebooks and provider pods are themselves
containers."*

So rented capacity uses the runner that already exists and already works —
`TrustedArgvRunner`, proven on three RunPod GPUs on 2026-08-12. Its safety
comes from placement, and it is explicit that this is the deal:

> *"It is not a security boundary and never claims to be: the placement
> contract (pool + allowFallback + the operator's `--runner trusted` opt-in)
> is what keeps strangers' code away from it."*

**The rented machine is bound to the submitting user's own pool.** Console
"workspace" is API "pool", so every user already has one. The machine joins
that pool, runs only that user's work, and is destroyed. `allowFallback iff
pool` is satisfied honestly rather than bypassed, and no stranger's code ever
shares the box.

**Which pool, and what happens to a public job.** The pool is the one the job
was submitted against — `placement.pool`, already on the JobSpec. A user with
several workspaces does not have one guessed for them.

A job submitted with **no** pool is public, and renting cannot help it: the
rented machine is `sandbox_capable: false` and the public job requires true,
so a machine acquired for it would sit idle and bill. This design therefore
**never silently adds a pool to a job to make renting possible.** Rewriting
`placement.pool` behind the submitter's back would change who is eligible to
run their code — a placement decision made by us, on their behalf, invisibly.
Instead the planner reports the venue as unusable for that job and says why,
which is the same shape as today's `suited` / `acquirable` distinction.

Offering the user the choice — "this job is public; scope it to your
workspace and we can rent you three A10s" — is a **console** decision, made
explicitly by the person whose code it is. That is a Layer 2 surface, not a
Layer 1 behaviour.

This is entirely a `flashml-cloud` change. **No flashruntime release, no
four-site pin bump.** The alternative — teaching the coordinator that
operator-owned single-use machines may take public jobs — is a change to
`flashruntime/scheduler/__init__.py:620` and `protocol/v1alpha1.py:366`, both
in the public repo, and is correctly out of reach before Friday.

**What this costs:** no open marketplace where anyone's job lands on any
machine. Renting *for a user* works; renting *into a public pool* does not.
That is the upgrade the upstream change buys, later.

### D3 — The rented instance is the isolation boundary. Three requirements.

Dropping the container is sound — it is how Modal, Replicate and RunPod
serverless work — but only if the instance itself carries the boundary:

1. **One job per instance, never reused across submitters.** Reuse turns
   "no boundary" into "no boundary between two customers".
2. **Destroyed, not stopped.** A stopped instance retains disk.
3. **No path from task code to cloud credentials.** Task code runs
   unsandboxed on an instance *on our account*. The instance metadata
   endpoint must be blocked and the instance must carry a role with no
   permissions worth stealing.

Requirement 3 is the one that matters most and the one the container was
never protecting against anyway. Skip it and a submitted job can read the RAM
role and reach the whole Alibaba account. This is the security section of
this design; the container question was never it.

### D4 — The budget gate refuses. It never queues.

Checked **before** every `acquire`. Exceeding it fails the acquisition with a
stated reason rather than parking the job, because a queue that drains when
budget frees up is the same unbounded spend with a delay.

**Two ceilings, different questions.** A per-acquisition cap bounds one
mistake; a rolling-window cap across all acquisitions bounds a loop of
correct-looking decisions, which is what actually empties an account. The
standing operational ceiling on rented spend is **$10 total**, against which
the entire 2026-08-12 cross-venue experiment cost $0.89. The window figure is
an owner decision and must be set before this ships.

### D5 — The reconciler owns teardown.

Release is attempted when a job settles, but correctness does not depend on
that call succeeding. A sweep independently destroys infrastructure whose
machine row is revoked, expired, or orphaned.

Two precedents, both already in the repo: `cleanup_session` kills the sandbox
and revokes the credential *independently*, so neither failure hides the
other; and the sandbox reconciler's comment states the stakes — *"The thing
it is racing is money: an abandoned sandbox bills by the second, so this is
minutes and not hours."*

### 2.1 The interface

```python
class ResourceProvider(Protocol):
    venue_id: str          # VENUE_RUNPOD | VENUE_FC_GPU | VENUE_ECS_GPU

    async def acquire(self, *, request: CapacityRequest) -> AcquiredMachine:
        """Bring one machine into existence, enrolled and claiming.

        Returns only once the node has registered. Any failure destroys
        whatever was created before raising — a half-created machine bills
        exactly like a whole one.
        """

    async def release(self, *, handle: str) -> ReleaseOutcome:
        """Destroy it. Idempotent; the reconciler will call it again."""

    async def observe(self, *, handle: str) -> ProviderState:
        """What the provider says exists. The reconciler's source of truth
        for orphans — never inferred from our own rows."""
```

`CapacityRequest` carries the job's resource ask (GPU count, minimum VRAM via
`estimator.hardware_class`), **the submitter's pool**, and a deadline.

### 2.2 What is reused, not rebuilt

- **`bootstrap_worker`** (`sandbox_bootstrap.py:752`) — nine steps ending in
  an enrolled FlashNode with its credential file deleted. Its *arguments* are
  general (credential, coordinator URL, pool, marker nonce), but its first
  parameter is a `SandboxGateway`, and that protocol is FC-shaped:
  `create(template=, timeout_ms=, metadata=)`, `connect`, plus a
  write-file/exec channel.

  **So enrolment has two styles, and the provider owns which one it uses:**

  | Style | How the agent gets there | Fits |
  |---|---|---|
  | **push** | controller execs into the machine and writes files — reuse `bootstrap_worker` behind a gateway adapter | FC sandbox; anything with an exec API |
  | **pull** | machine boots with a start command that fetches a bootstrap script over HTTP and self-enrols | RunPod (the proven 2026-08-12 recipe); ECS user-data |

  Both end at the same observable state: a registered node claiming leases in
  the right pool. `acquire()` returns only once that is true, and *how* is not
  part of the interface. A pull-style provider does **not** call
  `bootstrap_worker` at all — forcing it to would mean inventing an exec
  channel that the venue does not have.
- **Ephemeral machine identity** — `machines.lifecycle = 'ephemeral'` and
  `db.expire_stale_ephemeral_machines` already revoke and unbind rentals that
  stop heartbeating, deliberately leaving persistent machines alone.
- **The RunPod bootstrap recipe**, validated 2026-08-12: mint the token
  before the machine exists, seed `node-id` + `credentials.json`, install
  into a venv (`runpod/pytorch` ships a Debian `cryptography` with no RECORD
  file), fetch the bootstrap over HTTP so a push repairs a running host.

### 2.3 Lifecycle

The logical sequence, driven by the reconciler — **not** a chain of calls on
the submit request:

```
tasks unclaimable → pick acquirable venue for the job's resources
       → BUDGET GATE (D4) ─────────── refuse here, with a reason
       → provider.acquire()
            create instance
            mint credential (lifecycle='ephemeral', bound to submitter's pool)
            bootstrap_worker → flashnode work --runner trusted
       → coordinator places the lease
       → task runs
       → job settles → provider.release()          [best effort]
       → reconciler sweep → release() again        [the guarantee]
```

Every step records its event before attempting the thing it describes, so a
restart finds evidence rather than an orphan — the ordering discipline
`start_session` already follows.

---

## 3. Layer 2 — the decision layer

Wired after Layer 1 works. Most of it exists (§1); what follows is what to
add and what not to claim.

### 3.1 What to add: the frontier between cheapest and fastest

`plan.py` computes the two endpoints. The product needs the **curve**: for
each additional rented machine, what happens to finish time and to cost. A
sweep over fleet sizes, each point reusing the existing fill, rendered as a
short table the buyer reads in one glance:

| Fleet | Finish | Cost | Basis |
|---|---|---|---|
| your 2 machines | 6 h 10 m | 0 ZC | measured |
| + 1 rented A10 | 2 h 05 m | … | estimated |
| + 3 rented A10 | 52 m | … | estimated |

The `Basis` column is not decoration. It is the difference between a number
from this job's own history and a number projected across a hardware class,
and the estimator already grades it.

### 3.2 The honest core: more GPUs usually does nothing

**This is the part that must not be got wrong, because it costs the user real
money.**

`plan.py`'s arithmetic works because *"tasks in the jobs this targets are
independent — HPO trials, eval shards"*. A fill spreads N tasks over M
machines. **If N is 1, no fleet on earth makes it faster.**

| Workload kind | Does adding machines help? |
|---|---|
| HPO | Yes — up to the trial count, then **exactly zero** |
| EVALUATION | Yes — up to the shard count |
| FEDERATED | Within a round only; rounds are sequential |
| TRAINING | **No** — unless the job itself does distributed training |
| COMMAND | **No** — one task |

The router already classifies all five kinds correctly and unprompted, on
deployed infrastructure. So the honest advisor is reachable: it should say
*"more machines will not make this job faster, and here is why"* at least as
often as it offers to sell capacity. A trade-off table that always slopes
downward is a sales tool, not a planner, and it would be the first thing a
knowledgeable user caught.

### 3.3 Resolved blocker — charging ZC for a USD GPU

Before the 2026-08-12 owner decision in §4.1, the codebase deliberately
refused to do this arithmetic. `plan.py` described `Cost` as a vector and
used venue precedence instead of comparing mixed currencies. That was the
correct historical implementation of M4, but it is no longer the active
marketplace doctrine.

> *"**Cost is a vector.** `Cost` carries ZC and USD side by side and offers
> no way to reduce them to one number, because the exchange rate that would
> take is precisely what decision M4 forbids and what `contributions.py`
> warns a credit balance must never imply. Cross-currency ordering is
> therefore done by **venue precedence** — a stated product preference —
> never by comparing magnitudes."*

The owner decision now fixes 1 ZC = $1 USD. The scheduler compares ZC and USD
at parity, while preserving the original settlement totals. The equivalent is
shown only on wallet, credits, and marketplace surfaces; CU and CNY remain
unconverted and are not treated as dollar prices.

### 3.4 Blocker B — pre-submit task counts are not honest yet

The trade-off table is a **pre-submit** surface: the user wants to see the
choice before committing. Pre-submit preview was investigated on 2026-08-12
and deliberately not built:

> *"`compile_to_jobspec` has one call site, and compiling without network
> dataset resolution changes the task count that feeds the evidence sentence.
> A preview claiming 40 trials for a job that submits as 4 is the lie the
> honesty rules forbid."*

Task count is the denominator of the entire fill. A pre-submit trade-off
table inherits this problem exactly. It needs a compile-only path that
resolves datasets honestly — named as deferred in `2026-08-12-next-phase.md`
§3.4 and now on the critical path for this feature.

**Consequence for the demo:** a post-submit trade-off view on the job page
carries no such debt and can ship first, because by then the task count is
real. The pre-submit version is the better product and the later one.

---

## 4. Owner decisions taken 2026-08-12

Recorded here because each **reverses a decision in
`2026-08-11-zolli-marketplace-design.md`**, and a reversal that is not written
down is how a correct implementation gets "corrected" back by the next agent
to read the older page.

### 4.1 The ZC peg — reverses M4 and §2.5.1

**1 ZC = $1 USD.** Both units are shown in wallet, marketplace and credit
views; the scheduler compares venues at 1:1, so a community machine asking
0.80 ZC/hr loses to a RunPod pod at $0.70/hr.

Reverses M4 (*"side by side, never converted"*) and §2.5.1, which defined the
unit physically — *"1 ZC buys one hour of the reference class `gpu-24gb`"* —
specifically to avoid promising a dollar value. Taken knowingly.

**Consequence for this design: §3.3 is unblocked.** The trade-off table may
total a mixed fleet, and `plan.py`'s `Cost` vector needs an explicit,
documented reduction rather than the refusal it currently encodes.

**Owned by another agent.** The wallet, `credit_requests`, the grant and the
1:1 routing are being implemented in a separate session. Nothing in this
document may edit those surfaces.

### 4.2 The grant and refills — reverses M7 and M10

New accounts receive **10 ZC** once; existing testing accounts are untouched.
Users may submit a request naming an amount and a testing purpose; an admin
approves it or substitutes a smaller amount. One pending request per user.

Reverses M7 (*"a large credit grant"*) and M10 (*"one-time. No refill.
Scarcity is what makes a price mean anything"*).

### 4.3 M14 — deferred, deliberately

**Every success return is accepted as-is. There is no result verification,
and settlement does not wait for one.** A verification layer comes later.

M14 accepted this gap on the ground that *"credits never convert to money in
either direction — the exposure is reputational inside the product, not
financial"*, and said to **revisit before credits gain an exit**. §4.1 is that
exit: a host can now be paid, in dollar-denominated credit, for work nobody
verified. The condition M14 named has been met and the gap is being carried
anyway, for the testing phase, with the owner's decision on 2026-08-12.

**This is the largest known risk in the system and it is accepted, not
absent.** Anything that later gives credits a cash-out path must close it
first.

### 4.4 Still open — the operator-side spend ceiling

§4.1 gives a natural per-user bound: a wallet holds 10 ZC, so one user cannot
cause more than $10 of renting. That is **not** the ceiling D4 asks for. The
aggregate across all users, and the cap on a runaway acquisition loop that
never charges anyone, are still unset and remain an owner decision.

## 5. Verify before writing provider code

1. Which venues can be created and destroyed through an API this repo already
   authenticates against, and at what price. `fc-gpu` is `acquisition: none`
   because *nothing in this repo creates an FC GPU function* — that is a
   recorded absence, not a difficulty estimate.
2. Whether an FC GPU function can hold a task-length process at all. FC is
   invocation-driven; `flashnode work` is a polling loop. The FC *sandbox*
   precedent is encouraging (a 45-minute hibernation survived with a live
   agent still claiming leases) but a sandbox is not a GPU function. If it
   cannot, **Alibaba ECS GPU** — a real VM, already named as Stage 5 — is the
   likely first provider, and it is not in the venue registry at all.
3. Whether the metadata endpoint can be blocked on each candidate venue
   (D3.3). A venue where it cannot is not usable for this design.

## 6. Out of scope

- The persistent fleet. Rejected in D1.
- Public (non-pool) jobs on rented capacity. Needs the upstream coordinator
  change; see D2.
- Relaxing `assert_pool_isolated`. Not required — this design does not route
  work through isolation-pool sessions.
- FC Agent Sandbox as a GPU venue. It is CPU-only, `gpuConfig: null`.
- Re-acquisition policy after a machine death. Needed once acquisition is
  automatic; not needed for a first implementation that acquires once.
