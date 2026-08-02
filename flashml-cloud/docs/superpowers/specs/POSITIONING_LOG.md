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

1. **Contributions ledger** — nothing records accepted work; no host can be
   credited. Needed under every thesis above. *In progress.*
2. **Result verification** — the gate for any price-competitive market.
   Designed (`flashnode/benchmark/` ABCs), unimplemented.
3. **Capability-aware placement** — a GPU job must not land on a laptop, and a
   500-config sweep should. `IsolationAwarePlacement` reads no capabilities
   today.
4. **GPU support** — four changes: probe, `--gpus`, a CUDA image, placement.
   Now testable on a rented Pod for under a dollar, which removes the earlier
   objection that it was unverifiable.
5. **Desktop app (S4)** — deferred by both 2026-08-02 notes. Revisit only if
   laptop volunteers prove to be the supply that matters.

**Unanswered by everything above:** which tier a real user would pay for. Every
note so far is supply-side reasoning. None of it is demand evidence.
