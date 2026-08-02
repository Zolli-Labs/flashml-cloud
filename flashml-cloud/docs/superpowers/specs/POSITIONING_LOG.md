# Positioning log — how the product thesis has moved

**Purpose:** one dated trail through what FlashML is *for* and who supplies the
compute. Product direction here has changed several times in a single day, each
time for a stated reason, and a decision you cannot retrace is one you end up
re-arguing.

**How to use it:** newest first. Each entry records what changed, what caused
it, and what it implies — including the entries that were later corrected.
Wrong turns are kept, not deleted: knowing an idea was tried and why it was
dropped is most of the value.

**Rules.** Append, never rewrite history. Date everything. Record the *trigger*
— the evidence or argument that moved it — not just the conclusion. If a later
entry contradicts an earlier one, say so explicitly and link it.

---

## 2026-08-02 (late) — Federated needs local data binding, which does not exist

**Changed:** weakens the use case ranked strongest one hour earlier. The
ranking stands; the readiness claim attached to it does not.

**Trigger:** owner asked whether, on decentralised machines, there needs to be
a way to send data or numbers "in a way people don't know".

**The finding, and it is not the encryption question.** Every task input is
downloaded from the coordinator as an artifact
(`flashnode/executor/loop.py` — inputs unpack to `workdir/inputs/<name>/`).
**Nothing mounts a host directory into a task.** A participant has no way to
supply data that stays on their own machine.

That breaks the federated premise. Real federated learning means the
hospital's data never leaves the hospital. Today it would have to be uploaded
to the platform first — at which point this is an ordinary compute service with
extra steps, and the compliance argument, which was the entire reason that use
case was not a price competition, evaporates.

The federated example dodges this deliberately and documents it in its own
`flashml.yaml`:

> "Task containers have no network, so anything not in the image and not in
> this repo does not exist at runtime — which is why the training data is
> **generated** rather than downloaded."

**Correction to the entry below:** "excellent fit, no blocker, works today" was
wrong. The *mechanism* — rounds, aggregation, checkpoint resume, surviving a
dropout — is proven. The *premise* is not, because there is no local data path.
The blocker is **local data binding**, and it is foundational rather than
incremental.

### On the privacy question itself

Two distinct problems, routinely conflated:

**What the host machine can see.** Whoever runs the task controls the machine
and can read its memory and disk. The sandbox protects the host from the
submitter, not the reverse. Irrelevant when a participant runs on their own
data; unavoidable for volunteer compute, where TEEs are the only real answer
and they are expensive.

**What leaks through the weights.** Sending numbers instead of data is **not
automatically private** — gradient-inversion attacks reconstruct training
examples from updates alone. An aggregator receiving deltas is a real exposure,
not a theoretical one.

| Mitigation | Gives | Cost |
|---|---|---|
| **Secure aggregation** | server sees only the SUM, never an individual update | moderate; masking protocol, dropout handling |
| Differential privacy | provable bound on individual contribution | accuracy loss to tune |
| Homomorphic encryption | compute on encrypted values | very expensive |
| TEE / enclaves | hardware-enforced | needs specific hardware |

Secure aggregation is what a hospital security review asks for by name, and
what Flower and NVIDIA FLARE ship.

**One architectural note for whoever builds it:** masking protocols spend most
of their complexity on participants dropping out mid-round. This runtime
assumes participants vanish — so the hard case for secure aggregation is the
normal case here. Not a blocker; not a weekend either.

**Ordering, deliberately:** local data binding before secure aggregation, and
**a conversation with a real federated group before either**. Whether they need
masking, differential privacy, or both is a ten-minute answer from a user and
an unanswerable one from a spec. Building cryptography before that question is
asked is how a year disappears.

## 2026-08-02 (late) — Use cases, and the one that is not a compute market

**Changed:** what to sell, and to whom first. Follows directly from the
economics entry below.

**Trigger:** owner asked what the application actually supports and who the
market is.

**Grounded in what is PROVEN, not aspiration.** The e2e suite covers sharded
K-means and hyperparameter search; the 2026-08-02 deployed run covered
federated averaging across rounds with checkpoint resume. That capability set —
shardable batch compute, checkpointing, survival of machine death, federated
averaging — is the whole basis for the list.

| Use case | Fit | Blocker |
|---|---|---|
| **Federated learning on data that cannot legally move** | **excellent** | **local data binding — see entry above** |
| Training on cheap preemptible capacity | strong | GPU support |
| Hyperparameter sweeps, classic/small ML | strong | none |
| A lab pooling its own machines | perfect | none (self-hosted flashruntime) |
| Simulation / RL rollouts / Monte Carlo | good | none |
| **Large-model / transformer fine-tuning** | **poor — do not claim it** | interconnect; not fixable here |

**The finding:** every entry except the first is a **compute market**, and in a
compute market someone cheaper eventually wins. Federated learning on
immovable data is not a compute market. Hospitals, banks and multi-site
consortia cannot pool patient or customer data — HIPAA, GDPR, institutional
policy. The alternative to FlashML there is not "cheaper compute", it is **no
model at all**.

That changes every axis:
- Willingness to pay comes from compliance budgets, not compute budgets.
- Models are small and participants few, so the bandwidth ceiling in §3 of the
  positioning note never binds.
- It does not compete with Vast.ai or Salad at all — different problem.
- It needs **no GPU work**. The mechanism is deployed and proven; the local
  data path is not — see the entry above, which corrects the readiness claim
  without changing the ranking.

**Competition is different too:** Flower (flwr.ai), NVIDIA FLARE. Real, but a
different set than GPU marketplaces — and neither is built around surviving a
participant vanishing mid-round, which is this runtime's centre.

**Recommendation:** first customer conversation should be a group doing
federated learning on data they cannot pool. It is the only use case where
FlashML is not competing on price.

**Explicitly not claimed:** large-model training. It is most of what "AI
compute" means commercially right now, and claiming it would not survive a
first customer.

---

## 2026-08-02 (late) — The laptop economics do not work, at any price

**Changed:** retires the paid volunteer-laptop marketplace entirely. This
entry removes a direction rather than reordering one.

**Trigger:** owner followed the cost argument to its end — *"the cost of GPU is
so cheap that people don't even try to host their machine for very little
money... their machine making like $0.001 an hour"*.

**The arithmetic.** If a 4090 rents at ~$0.40/hr and a laptop CPU is ~1/200th
of its compute, a laptop-hour is worth ~**$0.002**. A laptop under sustained
load draws ~45W; at ~$0.15/kWh that is ~$4.86/month running continuously.

| Machine, 24/7 | Earns/mo | Power/mo | Net |
|---|---|---|---|
| Volunteer laptop | $1.44 | −$4.86 | **−$3.42** |
| Gaming PC w/ 4090 (50% util) | ~$144 | −$48 | **+$96** |

**A volunteer laptop loses money by participating.** Not "earns too little" —
negative. No price makes it rational, because the compute is worth less than
the electricity it consumes. This is why Salad and Vast.ai target gaming PCs
and not laptops: arithmetic, not branding.

**Implication.** CPUs are dead as PAID supply and alive as FREE supply. Two
models remain for laptops:
- **Donation** — contribute to science or a cause, with nobody pretending
  anyone is earning. BOINC and Folding@home run on exactly this.
- **Barter** — earn credits toward your OWN jobs. No money moves, so the
  electricity comparison is never made. `public.contributions` is already
  shaped for this: accepted work per machine.

**And the demand side is thinner than it looks.** A 200-config sweep of small
models is ~17 GPU-hours ≈ **$7** to rent. Nobody builds a distributed system to
avoid $7. The defensible pitches for a CPU pool are **access** (no card, no
quota, no cloud account) and **scale** (where the bill is actually real) —
never "cheaper".

**Three paths, by friction:**

| Path | Supply problem | Payouts | Verification | Blocker |
|---|---|---|---|---|
| **Rent & resell** | none — we buy it | none | none | **nothing** |
| Gaming-PC marketplace | hard, incumbents hold supply | yes | required | GPU support + trust |
| Laptop barter/donation | easy | none | light | needs a reason to care |

Rent-and-resell is the only one with no structural blocker, and the fault
tolerance is a genuine feature on cheap preemptible capacity rather than pure
arbitrage.

**Caveats worth re-checking:** GPU spot pricing moves constantly; electricity
varies by region (~$0.10 US to ~$0.30+ parts of Europe); a laptop left on
anyway has a lower marginal cost than the figure above. None change the
direction, only the magnitude.

**Supersedes:** the "everybody joins and competes on price" framing, for
laptops specifically. Gaming-PC GPU supply is unaffected — that maths works.

---

## 2026-08-02 (evening) — Workload class, not machine class

**Changed:** the axis for judging supply.

**Trigger:** owner pushed back on the positioning note: *"isn't one of our
ideas that even though CPUs, if we operate at scale it could beat GPUs — we're
trying to make a commercialised market where everybody joins and competes on
price and reliability."*

**Finding:** the note had rated supply by *machine type* and concluded laptops
were near-worthless. That is true for deep learning and **false for
low-communication work**, which is what this runtime already does well.

- ~200 laptop CPUs ≈ one RTX 4090 on raw FP32. Not a bad trade against 200
  free machines.
- The trade collapses **only when machines must sync**. Communication scales
  with N; home upload does not. That is the ceiling, and it is physics.
- For a hyperparameter sweep — a few floats of communication per unit — a CPU
  pool scales roughly linearly. The e2e suite already proves this shape
  (sharded K-means, hyperparameter search).

**Implication:** two products share one runtime.

| | Supply | Status |
|---|---|---|
| Low-communication, high-parallelism (sweeps, simulation, small federated models) | laptops are legitimate | **works today** |
| Deep learning on real models | GPUs, rented or home rigs | needs 4 changes (§5.1 of the note) |

**Also surfaced:** a price-competitive market requires **result verification**
first. *"A lying node is currently believed"* — without verification the
cheapest seller is the dishonest one and the market selects for fraud.
`flashnode/benchmark/` is interfaces with no implementation.

**Corrects:** the entry immediately below.
**Document:** `2026-08-02-supply-side-positioning-note.md` §4.

---

## 2026-08-02 (afternoon) — Supply is rented providers and home rigs, not laptops

**Changed:** which supply tier to build for. **Later partially corrected — see
above.**

**Trigger:** owner reframed the product as *"more like an open router for GPU"*
with supply from marketplaces, big providers, and eventually home data centres
rather than small laptops.

**Finding at the time:** evidence from that day's acceptance run — a MacBook
Air ran all three shards of every round and a toy MLP still took 101 seconds,
while two machines produced two unrelated Docker failures that both landed on
us to diagnose. Highest support cost, lowest compute value.

**Implication drawn:** S4 (desktop app + bundled sandbox VM) exists to remove
install friction for laptop volunteers, and neither rented providers nor
home-rig owners need it. The largest planned item becomes the least urgent;
GPU detection and capability-aware placement become the gate.

**What survived the correction:** the roadmap implication. Rented providers
still need no installer, GPU support is still the gate for the deep-learning
product, and S4 is still not the most urgent thing.

**What did not:** "laptops are worth almost nothing" — true only for deep
learning.

**Document:** `2026-08-02-supply-side-positioning-note.md`.

---

## 2026-08-02 (afternoon) — Colab pooling: prohibited on free, allowed on paid

**Changed:** nothing yet. Recorded a constraint before it could cost anyone.

**Trigger:** owner's idea — a research group where each member runs `flashnode`
in a Colab notebook, pooling their GPUs.

**Finding:** Google's Colab FAQ (read 2026-08-02) names it directly among
restrictions specific to the **free** tier: *"running distributed computing
workers"*, alongside *"using multiple accounts to work around access or
resource usage restrictions"*. The ban lands on the researcher's account, not
ours. The same FAQ says those restrictions are lifted on a paid plan.

**Implication:** the appealing version (pool free accounts) is off the table.
A lab pooling Colab Pro subscriptions is legitimate and a different product.
Also: Colab cannot nest Docker, so the sandbox tier drops to `subprocess` —
acceptable among trusted peers, nowhere else.

**Document:** `2026-08-02-colab-gpu-pooling-strategy-note.md`.

---

## 2026-08-01 — Open volunteer network, desktop-app-first

**Changed:** the founding product decision for this phase.

**Trigger:** direct answers to four scoping questions.

**Decisions:**
- Audience: **open volunteer network** — anyone donates a laptop, anyone
  submits.
- Host surface: **native desktop app** with a bundled sandbox runtime, because
  the install funnel is the growth bottleneck.
- Submit surface: **CLI first**, console for watching.
- Topology: public `flashml` monorepo + private `flashml-cloud`.
- Control plane: keep the API/coordinator split; move coordinator state to
  Postgres.

**Status:** the topology and control-plane decisions stand and are largely
built (S1). **The audience and host-surface decisions are what the two
2026-08-02 notes call into question** — not because volunteers are worthless,
but because the workload determines whether a volunteer laptop is good supply,
and because rented providers need no installer at all.

**Document:** `2026-08-01-foundation-design.md` §2.

---

## Open threads, carried forward

Ordered by what unblocks the most:

1. **Contributions ledger** — nothing records accepted work. Needed under
   every thesis above, and it is the credit ledger the barter model runs on.
   *In progress.*
2. **Talk to one federated-learning group.** The only use case that is not a
   compute market. Costs nothing but a conversation and is the
   highest-information next action in this document — and it now answers two
   questions at once: is local data binding the shape they need, and is secure
   aggregation table stakes or a later ask.
3. **Local data binding** — a participant points the agent at a directory on
   their own machine and the task reads it without it ever being uploaded.
   Today every input comes from the coordinator as an artifact. The gate for
   federated, and foundational rather than incremental.
4. **Result verification** — the gate for any PAID marketplace. Designed
   (`flashnode/benchmark/` ABCs), unimplemented. Not needed for federated,
   barter, or rent-and-resell.
5. **Capability-aware placement** — a GPU job must not land on a laptop, and a
   500-config sweep should. `IsolationAwarePlacement` reads no capabilities
   today.
6. **GPU support** — four changes: probe, `--gpus`, a CUDA image, placement.
   Testable on a rented Pod for under a dollar. Required for preemptible
   training and the gaming-PC marketplace; **not** required for federated.
7. **Desktop app (S4)** — deferred by both 2026-08-02 notes, and further
   undercut by the laptop economics: an installer cannot fix a machine that
   loses money by participating. Revisit only under a donation or barter
   model, where nobody is being paid.

**Unanswered by everything above:** which use case a real user would pay for.
Every entry in this log is supply-side or economic reasoning. **None of it is
demand evidence.** Item 2 is the cheapest way to change that.
