# Elastic work distribution — the fleet decides the split, not the submitter

**Date:** 2026-08-09
**Status:** approved design (brainstormed with the owner).
**Amends:** `2026-08-03-bounded-influence-design.md` §2.1 (see §5).
**Supersedes:** nothing. `shards` and `min_participants` are removed from
`flashml.yaml` by §6 and `rounds` is replaced by `epochs`; no earlier spec
defends them.

---

## 0. Decisions taken

1. **`shards` and `min_participants` leave the config.** Both are guesses
   about a fleet the submitter cannot see. Neither is replaced by a smarter
   guess — they are replaced by nothing. `rounds` becomes `epochs`, and
   round count becomes derived (§6).
2. **Work is cut into uniform chunks, and heterogeneity is expressed as
   chunk *count*.** Never as chunk size. §5 is why this is forced rather
   than preferred.
3. **A machine reports once per round.** Bandwidth is the binding
   constraint; the cost is that a machine which quits mid-round loses
   everything it did that round. Taken deliberately.
4. **A round closes on coverage, not on wall clock.** Round duration
   becomes an output the console measures, not an input the submitter
   guesses.
5. **The chunk count a machine completed is a coordinator-side fact**,
   derived from what was handed out, not a number the node reports free-form.
6. **The console shows the tradeoff as numbers and a graph**, not prose
   (§7).

---

## 1. The problem

`flashml.yaml` asks for `shards: 3`. That number fixes how many pieces a
round has, before any machine has connected.

Three failures follow, and all three are visible today:

- **Idle capacity.** 11 machines online, 3 shards — 8 do nothing, and
  nothing tells anyone.
- **Stuck work.** A shard's machine closes its lid; that shard is a hole in
  the round until the lease expires.
- **An unanswerable question.** The submitter is asked how many machines
  will be online during a job that has not started. They cannot know, and
  the fleet changes while the job runs.

The earlier draft of this work proposed warning the submitter when their
number looked wrong against the current fleet. That was rejected in
brainstorming: a warning tells someone to hand-tune a number the
coordinator already knows better, and the count it would compare against is
stale the moment a lid closes.

---

## 2. What the architecture already provides

Four properties made this design small. All four are load-bearing, and all
four already exist.

**The data is replicated, not distributed.** `examples/federated/train.py`
generates the full dataset from a shared `DATA_SEED` on every machine and
selects a stride:

```python
def shard_of(x, y, shard: int, num_shards: int):
    idx = torch.arange(shard, len(x), num_shards)
```

Its own docstring states the consequence: striding keeps each slice IID, and
*"data is not IID, and handling that is a research problem, not a demo."*

This is data-parallel training over a shared corpus, **not** cross-silo
federated learning. Every hard problem of real FL — non-IID clients, drift
from divergent local distributions, privacy — is out of scope by
construction. In particular it means a machine that processes 13× more
samples has drawn a 13× larger sample of *the same* distribution, so
sample-weighted averaging over unequal contributions is exactly right here.
That would be the hardest question in the design if the data were local to
each volunteer. It is not.

**A work unit ships indices, not data.** Because the corpus is present
everywhere, handing out work costs two integers.

**Claiming is already pull-based.** `LeaseManager.claim()`
(`flashruntime/leases/manager.py:86`) hands the next PENDING task to
whichever node asks. Nothing assigns work to a machine.

**Losing a machine is already a no-op.** `sweep()` (`manager.py:222`)
expires the lease and requeues the task (`TASK_REQUEUED`), so abandoned work
returns to the pool with no reassignment step.

**A round is already recompiled from scratch.** `fedavg.py:147`'s
`build_round_for` rebuilds each round rather than mutating one, and the
driver reads `task_ids` from the round's own plan and validates the count
*per round* (`fedavg_driver.py:529`). The driver was written for a task
count that varies between rounds. Only the plumbing reads a frozen number.

---

## 3. The constraint that shapes the mechanism

**Task containers run `--network none`.** A worker cannot request more work
mid-run. Whatever it will process, it must receive at task start.

This rules out the obvious design — a worker looping "claim a chunk, do it,
claim another." The coordinator must hand out a *list* up front and let the
worker take as much of it as it can.

---

## 4. The mechanism

### 4.1 Chunks

One pass over the data is cut into `C` uniform chunks. Chunk `c` is the
stride `arange(c, len(x), C)` — **the existing `shard_of` function,
unchanged**, with a large fixed `C` instead of a small fleet-sized one. The
user's training code keeps the contract it already has.

`C` is derived, not configured: the system targets a chunk that a median
machine finishes in tens of seconds, so a death costs little and per-chunk
bookkeeping stays cheap. Round 1 uses a default; later rounds use measured
throughput. An escape hatch may pin `C`; it is not part of the normal
authoring surface.

### 4.2 One task per machine per round

A round opens with weights `W_r`. A machine claims **one** task and receives:

- `W_r`, downloaded once
- an **ordered list of chunk ids**, sized generously (§4.4)
- a deadline

The worker processes chunks in list order, accumulating locally. At the
deadline it reports **one** result:

```
(delta, completed_chunk_ids)
```

Chunks it did not reach are requeued. It never re-downloads `W_r`, and it
uploads exactly one model-sized payload per round.

### 4.3 Rounds close on coverage

A round ends when the completed chunks cover the submitter's requested
fraction of the data, or when a **system backstop** on round duration fires
— whichever comes first.

The backstop is not a config field. It exists so that a Crew emptying out
mid-round cannot hang a job forever, and the submitter should never have to
reason about it.

Round *duration* is therefore an output. The console reports it as a
measurement after round 1, never as a prediction (§7).

### 4.4 How long the handed-out list should be

Generous, and adaptive: last round's measured throughput for that machine,
with headroom, defaulting on the first round. Over-handing costs a requeue;
under-handing wastes the tail of a fast machine's round.

The list length is also the ceiling on that machine's influence for the
round (§5), so it is a coordinator-side decision and never a node-supplied
one.

---

## 5. Why chunks must be uniform — amendment to bounded-influence §2.1

`2026-08-03-bounded-influence-design.md` §2.1 states the property governing
the clip:

> **An honest round must be bit-identical to today.**

and rests it on: *"Honest per-shard variation is well inside 3× the
median."* The clip is `C = median(‖Δᵢ‖) × 3.0`.

**That invariant holds only while shards are equal-sized, and an earlier
draft of this design broke it.** Sizing each machine's work to its own speed
means a machine 13× faster than the median takes ~13× the optimisation
steps. Accumulated displacement grows with step count — even at a
conservative √13 ≈ 3.6 it clears the 3.0 cap. Honest fast machines would be
clipped every round, silently down-weighted, and recorded in a clip report
an operator reads as evidence someone tried something.

Uniform chunks restore the invariant exactly. Every contribution covers the
same number of examples, so honest norms are comparable and the cap goes
back to never firing on an honest round. Heterogeneity lives in the *count*
of chunks, which the clip does not look at.

**What this widens, stated plainly.** A machine's influence becomes
`chunks_completed × cap` rather than `1 × cap`. Three things bound it:

1. The chunk list is handed out by the coordinator (§4.4), so the ceiling is
   ours, not the node's.
2. Wall clock bounds real work — a round is a round.
3. Whether a node *actually* processed the chunks it claims is not verified.
   That is the existing gap owned by
   `2026-08-03-result-verification-design.md`, not something this design
   introduces or closes.

A per-machine influence cap independent of chunk count needs per-node
identity (trust-hardening slice B, the shared join code). Out of scope here,
named in §9.

**A second amendment, in our favour.** `fedavg_weights.py` documents that
`samples` is chosen by an untrusted node, and defends against it. Under this
design the coordinator knows what it handed out, so the weight in the
average is bounded by a coordinator-side fact rather than a node's claim.
That is a strict improvement over today and should be reflected when the
reduce is touched.

**Unchanged limits.** §2.3's honest limits still apply: one contribution
never clips, two are weak. Coverage-closed rounds do not by themselves
guarantee several contributors — one fast machine can cover a round alone.
A floor on contribution *count* before the clip is meaningful is a
system-side property to settle during implementation, not a submitter knob.

---

## 6. The authoring surface

**Removed:**

| Field | Why |
|---|---|
| `shards` | a guess about the fleet; §4 derives it |
| `min_participants` | a machine count in a design where machines contribute unequally; coverage replaces it |

**Replaced:**

`rounds` → `epochs`. Today `rounds` means "how many times to combine." That
only doubles as "how much training happens" while a round is exactly one
pass — which is precisely what `sync_every` is about to make configurable.
Keeping both as inputs makes them non-orthogonal: lowering `sync_every` from
`1.0` to `0.1` with `rounds: 5` would quietly cut the training from five
passes to half of one, inside a knob that claims to be about sync frequency.

So the input is `epochs` — total passes over the data, the number people
already reach for — and round count becomes **derived**:

```
rounds = epochs / sync_every
```

shown in the console (§7.1), never typed.

**Added:**

`sync_every` — passes of data between combines. Default `1.0`, which
reproduces today's behaviour exactly: one combine per pass, `rounds ==
epochs`.

Both inputs are training decisions, they are independent of each other, and
neither requires knowing who is online.

**Migration.** `shards`, `min_participants` and `rounds` all change meaning
or disappear for `mode: federated`, so this is a breaking change to a
shipped config. It takes a `version: 2` bump in `flashml.yaml`, with
`version: 1` federated configs either rejected with a named finding that
states the replacement, or read under today's semantics for one release.
Which of the two is an implementation-plan decision, not a design one.

**Preflight** keeps the config-contradiction checks that survive this change
(`epochs` or `sync_every` set on an `independent` job, which ignores both)
and drops every check that referenced `shards` or `min_participants`.

---

## 7. The console: numbers and a graph, not prose

The submitter is choosing between network cost and work lost to churn. That
is a quantitative tradeoff and must be shown as one. **No paragraph explains
a tradeoff better than the two numbers that move.**

### 7.1 Live figures on the submit screen

Recomputed as `sync_every` changes. Labels four words or fewer. Below:
`epochs: 5`, a 3 MB model, 6 machines online — all three held fixed, so the
row that moves is the tradeoff.

```
  sync_every    1.0          0.5          0.1
  ─────────────────────────────────────────────
  Rounds         5            10           50
  Uploads        30           60           300
  Traffic        90 MB        180 MB       900 MB
  Lost if quit   1 pass       ½ pass       ⅒ pass
```

Training is identical down every column — five passes in all three. Only
how often it syncs, and what that costs, changes.

All four rows are arithmetic on model size, `epochs`, `sync_every`, and the
Crew's current machine count. **Nothing here predicts speed**, so nothing
here can be wrong about the fleet.

### 7.2 The tradeoff graph

One small chart, two lines, crossing. It exists to show the *shape* — that
the two costs move in opposite directions — not to be read off precisely.

```
   cost
    │╲                            ╱
    │ ╲   traffic ───            ╱
    │  ╲                        ╱  lost work ---
    │   ╲___                ___╱
    │       ‾‾‾───────────‾‾
    └────────────────────────────────  sync_every
     often                        rarely
```

Marker on the current choice. Axis labels only; no legend paragraph.

### 7.3 Round duration, after the fact

Once round 1 completes:

> **Rounds ≈ 4 min** · measured, 6 machines

Never shown before a job runs. It depends on who is online, which is not
knowable at submit time, and a wrong prediction here is worse than no
prediction. This matches the honesty contract the planner already
follows — `basis: static` versus measured.

### 7.4 Job detail

The existing `FleetTopology` and `Swimlanes` need no change: they render
attempts, and attempts still exist. What is added is a per-round coverage
figure — *"round 3 · 94% covered · 7 machines"* — in the same terse register.

---

## 8. Independent mode

Unchanged. `mode: independent` expands its tasks once at submit
(`modea.py:116`) and has no round boundary at which to re-decide anything.
Re-partitioning committed work mid-flight would invalidate it.

`shards` remains meaningful and required there. The removal in §6 applies to
`mode: federated` only. Making independent jobs elastic needs a
re-partitioning story and is not attempted here.

---

## 9. Out of scope

1. **Result verification.** Whether a node did the chunks it claims. Owned by
   `2026-08-03-result-verification-design.md`.
2. **Per-node identity**, and therefore a per-machine influence cap
   independent of chunk count.
3. **Client drift** from unequal local work. Much milder than in real FL
   because §2's data is IID, but unbounded at extreme speed spreads. The
   cheap bound is a ceiling on chunks per machine per round; a proximal term
   would change user training code and is not proposed.
4. **Elastic independent mode** (§8).
5. **Delta encoding.** Deltas cross the wire as JSON floats so the driver
   never imports torch. That is roughly an order of magnitude worse than raw
   bytes and will bind before model size does anything else — a real limit,
   and a separate piece of work.

---

## 10. Rejected

**Warn the submitter their `shards` looks wrong.** The first version of this
work. Rejected: it asks someone to hand-tune a number the coordinator knows
better, against a machine count that is stale immediately.

**Pick `shards` per round from the online machine count.** Better, still
wrong. It is a census, taken at round start, invalid the moment anyone joins
or leaves — which is the whole problem.

**Size each machine's work to its own speed.** Intuitive, and it breaks
bounded-influence §2.1 silently (§5). Uniform chunks with variable counts
get the same elasticity and keep the invariant.

**Report per chunk instead of per round.** Bounds lost work to a single
chunk, but pays a model-sized upload per chunk. Bandwidth is the binding
constraint; the owner chose per-round explicitly.

**Let the submitter set round duration.** They cannot measure it — it
depends on whose machines are online. Coverage is theirs to choose;
duration is ours to report.
