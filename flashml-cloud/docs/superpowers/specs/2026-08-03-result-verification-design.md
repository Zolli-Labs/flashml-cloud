# Result verification: three slices, cheapest first

**Date:** 2026-08-03
**Status:** DRAFT for review. Revised after the owner asked why we re-compute
instead of checking the harness around the computation — a better question
than the one the first draft answered, and it reordered the build.
**Addresses:** `POSITIONING_LOG.md` open thread 4.

Bounded influence (`2026-08-03-bounded-influence-design.md`) caps how far one
contributor can move a federated model. It detects no lie. This is the work
that does.

---

## 0. What changed from draft 1

Draft 1 went straight to spot-check re-execution. That left free signal on
the floor.

**The structural fact:** anything the agent reports about itself, it can
fabricate. Sandbox id, exit code, self-reported duration, telemetry — the
agent is the untrusted party, so none of it is proof.

**The economic fact, which is more useful:** the *point* of lying is to save
compute. Verification does not need to be a proof. It needs to make a
convincing fake cost about as much as doing the work.

| To pass | The liar must | Costs them |
|---|---|---|
| API-observed elapsed time | sleep the right duration | ~nothing |
| self-reported telemetry | fabricate a consistent profile | ~nothing |
| plausible result shape | emit a small random delta | ~nothing |
| **a second execution matching** | **actually compute it** | **full price** |

Only the last closes it. But the first three are nearly **free**, and they
catch the naive attacker — which is most attackers. So: cheap first.

## 1. The threat

**The lazy node** wants credit without computing. Returns zeros, or replays a
previous result. It passes every check in the system today: the file exists,
the hash matches (it hashed what it uploaded), the values are finite, the
counts are sane, the shapes agree, the norm is unremarkable. It earns a
`contributions` row identical to an honest node's.

**This is the target.** The subtle poisoner — small, plausible, consistently
biased — is not solved by any slice here.

## 2. Slice 1: anomaly detection on time we already measure

**Cost: zero. No protocol change. No release. No extra compute.**

`public.attempts.claimed_at` is set by the **API** when it records a claim,
and `claim_attempt_credit` computes `now() - claimed_at` at credit time. Both
timestamps are ours. This is already stored as `contributions.duration_s` on
every credited task.

**A node cannot fabricate it.** It can only inflate it, by sleeping — and a
node that sleeps for the full duration has spent wall-clock but no CPU, which
is a different and much weaker attack than returning instantly.

Today's ledger: **29 credited tasks, mean 9.1s, range 5.5–37.5s.** A result
committed 0.3s after claim is not a fast machine; it is a machine that did not
download the input, let alone train on it.

### 2.1 What it flags

For each accepted attempt, compare its observed elapsed against the
distribution of *other machines' elapsed on the same task shape*.

- Peer group: same `job_id` for federated rounds (every shard is the same
  work); otherwise same task across the job.
- Flag when elapsed is below a floor derived from peers — a fraction of the
  peer median, not an absolute constant, because task cost varies by orders
  of magnitude.
- **Requires a baseline.** With fewer than N peer samples there is no
  distribution, and the verdict is `unknown` — never `pass`. Absence of
  evidence is not evidence of honesty, and this is exactly where that error
  would creep in.

### 2.2 What it cannot do

- A liar that sleeps defeats it entirely. It costs them latency and nothing
  else.
- It cannot distinguish "returned instantly because it cheated" from
  "returned instantly because the input was cached and the machine is fast".
  Hence: **flag, never reject** — §5.
- A first-of-its-kind task has no peers and is unverifiable by this slice.

It is a tripwire for the laziest attack, at zero cost. That is all, and it is
worth having because it is free.

## 3. Slice 2: execution evidence from the agent

**Cost: a protocol change and a release. No extra compute.**

`CompleteRequest` is today, in full:

```python
class CompleteRequest(BaseModel):
    output_sha256: str
```

That is everything the agent says about a completed task. Meanwhile
`flashnode/telemetry/` already models `TelemetrySample` and `GpuSample` — cpu
percent, memory, GPU utilisation, temperature, power — **wired to nothing.**

Slice 2 adds an optional `evidence` block: wall-clock the agent measured,
mean CPU and GPU utilisation, the image digest it actually ran, the container
exit code.

**Explicitly evidence, never proof.** Its value is twofold:

1. **Cross-checking against what we observe.** The agent's claimed duration
   must be consistent with the API-observed elapsed from slice 1. A liar now
   has to keep two stories straight.
2. **GPU utilisation ~0% on a task that requested a GPU** is a strong signal,
   and it is the one thing that partially covers GPU work, which slice 3
   cannot verify at all (§4.1).

Optional, and absent on any agent that predates it — recorded as `unknown`,
never as `pass`.

## 4. Slice 3: redundant assignment (upfront, not post-hoc)

**Cost: `f` × compute, and `f` × payout. The owner has accepted this.**

The only mechanism that requires the liar to actually compute.

**Dispatched POST-HOC: after a task is accepted, re-run it elsewhere.**

Draft 2 specified upfront redundancy — dispatch the sampled task to two
machines from the start, so a bad result is caught *before* it is aggregated
or credited. That is strictly better on correctness, and it is **deferred**,
not discarded (§4.3).

The reason is the fleet we actually have. Upfront redundancy makes every
sampled round wait for the **slower of two machines**. On a homogeneous
rented fleet that is a rounding error. On this one — laptops and a desktop,
5.5s to 37.5s for the same class of work, a 7× spread — it means the round
runs at the speed of the slowest volunteer, on top of an already small
quorum. The cost is paid in wall-clock on every sampled round, forever, to
buy earlier detection of an attack nobody has yet mounted.

| | post-hoc (this) | upfront (deferred) |
|---|---|---|
| latency | **none added** | round waits for the slower of two |
| compute | `f` × | `f` × (same) |
| credit | already paid when the check runs | withheld until both agree |
| a poisoned federated delta | already aggregated — but **magnitude-capped** | never enters the model |

The last row is why post-hoc is tolerable rather than merely cheaper:
bounded influence already caps what a single bad delta can do to the model.
Post-hoc detection plus a bound is a reasonable posture; post-hoc detection
with no bound would not be.

### 4.3 When to revisit upfront redundancy

It becomes the right design when the fleet stops being small and
heterogeneous — rented datacenter capacity, or hosts selected for comparable
throughput. The trigger is measurable rather than a matter of taste:

- the p95/p50 spread of task duration within a peer group is small (say < 2×),
  so waiting for the slower machine costs little; **and**
- work is being paid for, so crediting a result before checking it is a real
  loss rather than an accounting note.

Neither holds today. Both plausibly hold for the rented-provider tier the
positioning log identifies as where the compute actually is.

### 4.1 The determinism problem, which GPU support made worse

Comparison needs to know when two results are "the same", and they are rarely
bit-identical. **GPU work is worse: CUDA kernel selection varies per run and
float reduction is non-associative, so two honest runs of the same task on the
same GPU differ.** A comparator tuned for CPU determinism would flag honest
GPU hosts as liars — the hosts most worth keeping.

**Declared verifiability.** `flashml.yaml` gains `verifiable: true`
(default false): a claim by the submitter that running this twice on
equivalent hardware yields the same `metrics.json`. Only such tasks are
sampled, and the comparison is then near-exact, because determinism was
promised rather than hoped for.

This never pretends to verify what it cannot.

**The hole, stated plainly:** a strategic liar takes only `verifiable: false`
work — it can see the payload. Not fixable by hiding the flag; the node infers
it from never being re-run. Fixable *commercially*: unverifiable work is
best-effort, verifiable work is what a paying customer buys. A pricing
decision, not an engineering one.

### 4.2 Architecture: duplicate task ids, not a re-leased task

Upfront redundancy needs no change to the lease state machine either. A
checked task is expanded into **two ordinary tasks** with distinct ids
(`shard-003` and `shard-003~v`), the same payload, and mutually exclusive
placement. Both are leased, executed and committed by the normal path; the
comparison happens when both have committed.

Nothing about leases, retries or commits changes. The coordinator does not
know it is running a verification — which is also what stops a node
identifying one from the protocol.

**One runtime change is unavoidable:** node exclusion. Nothing today can say
"anywhere but there", and with two machines the twin lands on the same node
half the time, verifying nothing. A sixth placement gate, fail-closed on a
non-list like `local_datasets` and `gpus`.

The twin also carries `exclude_nodes` naming whichever node took the original
— which is only known once the original is claimed, so the two are dispatched
together and exclusion is enforced pairwise: whichever claims first excludes
the other. Simplest correct form: both tasks carry a shared
`verification_pair` id, and the gate refuses a node that already holds a
lease on the other member.

### 4.3 Detection is probabilistic

`E[tasks before detection] = 1 / (f · c)` for a node cheating a fraction `c`:

```
f=0.05, c=1.00  →   20 tasks
f=0.05, c=0.10  →  200 tasks
f=0.05, c=0.01  → 2000 tasks
```

Systematic fraud is caught quickly; occasional fraud slowly, possibly never at
this fleet's volume. It is a deterrent whose strength is `f`, not a proof.
**A clean record is not evidence of honesty.**

Slices 1 and 2 lower the needed `f`, because they catch the lazy majority for
free — 2% may do what 5% would have.

## 5. Consequences: record, never reject

A `verifications` table: attempt, machine, slice, verdict
(`pass` / `flag` / `unknown`), detail.

**Nothing is enforced** — no quarantine, no clawback, no placement change.
Two reasons, and the second is the strong one:

1. A false positive costs a volunteer their machine; a false negative costs
   one undeserved credit. With a fleet of two that asymmetry is decisive.
2. **A slice-3 mismatch does not identify the liar.** Two results disagree and
   there is no trusted third opinion. Auto-punishing punishes the honest node
   half the time.

`unknown` must never be stored or displayed as `pass`.

## 6. Build order and why

| Slice | Cost | Catches | Needs a release? |
|---|---|---|---|
| **1 — observed elapsed** | none | instant-return liars | no |
| **2 — agent evidence** | none (compute) | inconsistent liars, idle GPUs | yes |
| **3 — re-execution** | `f` × compute + payout | a determined single liar | yes |

Slice 1 first because it is free, needs no release, and uses data already in
the ledger. Slice 3 last because it is the only one that costs real money, and
because slices 1–2 reduce how much of it you need.

Each slice is useful alone. None of them catches a colluding pair.

## 8. Known weaknesses, recorded for later

This is an early-stage layer. Every one of these is a deliberate trade-off,
not an oversight, and each is the natural next increment.

**8.1 Sleeping defeats slice 1.** Observed elapsed is a lower bound on
*elapsed*, not on *effort*. A node that sleeps 9 seconds and returns garbage
passes. Slice 2's CPU/GPU utilisation is the partial answer; nothing here is
a full one.

**8.2 Slice 2 is self-reported.** The agent is the untrusted party, so its
evidence is only as good as its willingness to lie consistently. It raises
the cost of faking; it does not close it. The real answer is hardware
attestation (TPM/SGX/NVIDIA confidential compute), unavailable on a volunteer
fleet.

**8.3 Collusion defeats everything here.** Two cooperating nodes verify each
other and agree on a wrong answer. Mitigations (reputation-weighted pairing,
a trusted third run) all need a fleet larger than two.

**8.4 Post-hoc detection is after the fact.** The result is already
committed, already credited, and — for federated — already aggregated by the
time the check runs. Bounded influence caps the damage, but the bad delta did
enter the model. Upfront redundancy fixes this and was deferred on latency
grounds (§4.3); the revisit trigger is written down there rather than left to
memory.

**8.5 A mismatch does not name the liar.** Two results disagree with no third
opinion. This is why nothing is enforced (§5), and it is the single biggest
reason this layer is advisory. A third run as tie-breaker is the obvious v2.

**8.6 GPU work is unverifiable by slice 3.** CUDA non-determinism means two
honest runs differ. Only slices 1 and 2 touch GPU tasks, and neither is
strong. This got worse the day GPU support shipped, which is worth
remembering as a general pattern: capability and verifiability trade off.

**8.7 A strategic liar takes only `verifiable: false` work.** It can read the
payload. The fix is commercial, not technical (§4.1).

**8.8 Slice 1 needs a peer baseline.** A first-of-its-kind task has no
distribution to compare against and is `unknown`. A cold-start fleet verifies
nothing by timing.

**8.9 Nothing verifies non-federated jobs beyond timing.** Sweeps and command
jobs get slices 1 and 2 only, unless the submitter declares them verifiable.

## 7. Out of scope

1. **Collusion.** Two cooperating nodes verify each other.
2. **Tie-breaking a mismatch** — needs a third run or a trust hierarchy.
3. **Any automatic consequence** (§5).
4. **Verifying non-deterministic work**, including all GPU work, in slice 3.
5. **Hardware attestation** (TPM/SGX/NVIDIA confidential compute) — the only
   real "verify without recomputing" answer, and unavailable on the fleet.
6. **Comparing anything but `metrics.json`.**
7. **Pricing verifiable vs unverifiable work** (§4.1).
