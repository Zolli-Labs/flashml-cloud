# Bounded influence: capping what one contributor can do to a model

**Date:** 2026-08-03
**Status:** design approved (two decisions taken by the owner, §0)
**Addresses:** `POSITIONING_LOG.md` open thread 4, partially — see §1.1 for
what this is *not*.

---

## 0. Decisions taken

| # | Question | Decision |
|---|---|---|
| 1 | Which threat first | **Bounded influence** (model poisoning), not spot-check re-execution. Zero extra compute; protects federated, the strongest use case. |
| 2 | Consequence of a failed check | **Record, do not act automatically.** The owner revokes by hand. A false positive that bans a volunteer is worse than a lie that earns one undeserved credit, with a fleet of two. |

## 1. What is actually unprotected today

`flashml_workloads/fedavg_weights.py::reduce_deltas` is in better shape than
the docs claim. It already rejects, with the reasoning written down:

- **non-positive sample counts** — the comment documents the attack:
  `(delta=-999, n=-999)` and `(delta=1.0, n=1000)` sum to a healthy total of
  1, but the weights are −999 and 1000, so an "average" of two updates of
  magnitude ~1 comes out at **999001.0**;
- **NaN/Inf sample counts**, explicitly and by index, because every
  comparison against NaN is False and they slip all three numeric guards;
- **NaN/Inf/non-numeric weights**, via `require_finite`.

So everything *malformed* is caught. What is not caught is a contribution
that is **perfectly well-formed and adversarial**:

```
delta = 1e6 (finite), n = 500 (honest, positive, integral)
```

Every guard passes. The sample-weighted mean moves the model by whatever the
attacker chose. One node out of a quorum can steer the result arbitrarily.

That is the gap this closes.

### 1.1 What this is NOT

It is **not** result verification, and the log's thread 4 should not be
marked done by it.

- It bounds **magnitude**, not **direction**. A contributor sending a small,
  consistently-biased delta every round is unaffected.
- It does **not** detect a lazy node. Returning zeros, or replaying last
  round's delta, has a perfectly normal norm and still earns credit.
- It does **not** verify that any computation happened. Nothing here re-runs
  a task or compares two answers.
- It applies to **federated only**. A sweep or command job gets nothing.

Spot-check re-execution remains the thing thread 4 actually asks for, and is
untouched.

Also worth correcting: `docs/guides/donate-a-machine.md` says spot-check
verification is "designed but not built", and the positioning log says thread
4 is "Designed (`flashnode/benchmark/` ABCs)". Neither is true.
`flashnode/benchmark/` is **admission capability probing** — `cpu_hash_mbps`,
`mem_bandwidth_mbps`, `disk_write_mbps` — which measures how fast a host is,
not whether its answers are correct. No design for result verification exists
in either repo.

## 2. Mechanism: median-anchored L2 norm clipping

For each round, before the existing weighted mean:

1. Compute the L2 norm of every contribution's delta, flattened across all
   parameters.
2. `C = median(norms) × CLIP_FACTOR`.
3. Any contribution whose norm exceeds `C` is scaled by `C / norm`.
4. Proceed with the existing sample-weighted mean, unchanged.

### 2.1 The property that governs the design

**An honest round must be bit-identical to today.**

If no norm exceeds `C`, no contribution is scaled and the arithmetic is
exactly what it is now. This is the difference between a safety net and a
behaviour change, and it is asserted directly: existing rounds must produce
the same floats, not merely similar ones.

`CLIP_FACTOR` defaults to **3.0** for that reason. Honest per-shard variation
is well inside 3× the median; setting it at 1.0 would clip roughly half of
every honest round and silently alter results that are correct today.

### 2.2 Why median-anchored rather than a constant

A fixed `C` is unusable: the right magnitude depends on the model, the
learning rate, and the round number, none of which this module knows. The
median of the round's own norms is self-scaling and needs no configuration.

It is also the robust choice: with a majority of honest contributors the
median is an honest value, which is exactly the property an attacker-chosen
mean does not have.

### 2.3 Honest limits, documented rather than hidden

- **One contribution ⇒ never clips.** The median is that contribution's own
  norm, so `norm ≤ C` trivially. Correct: an outlier cannot be identified
  from a single sample.
- **Two contributions ⇒ weak.** The median of two values is their mean, which
  the attacker influences directly. Robust statistics need a majority, and
  with `min_participants = 2` there is no majority to have. This does not
  fail closed and it must not be described as protection at that quorum.
- **A colluding majority defeats it**, by construction. The median is only
  honest if most contributors are.

## 3. Reporting, without breaking the existing API

`reduce_deltas` has exactly one production caller
(`fedavg_driver.py:538`) and **30+ tests** pinning its current behaviour. Its
signature does not change.

A new function does the work and reports:

```python
def reduce_deltas_with_report(
    contributions: list[tuple[dict, int]],
    *,
    clip_factor: float = CLIP_FACTOR,
) -> tuple[dict, list[ClipEvent]]: ...

def reduce_deltas(contributions, *, clip_factor=CLIP_FACTOR) -> dict:
    return reduce_deltas_with_report(contributions, clip_factor=clip_factor)[0]
```

`ClipEvent` records the contribution index, its original norm, the cap
applied, and the resulting scale factor. Indices, not node ids — this module
is pure stdlib and knows nothing about nodes.

**Attribution is by `task_id` in the driver, and the node id is joined
cloud-side.** CORRECTED 2026-08-03: this section originally claimed the driver
holds `(delta, n, node_id)` triples. It does not — `_fetch` returns
`(delta, samples, loss)` and there is no node id anywhere in the driver. The
`_` at the call site is the per-shard loss, consumed two lines later for
`mean_loss`.

The driver reports `task_id`, which it holds exactly and for free
(`_fetch` preserves the order of `keys`). The node id is joined in the
cloud's `on_round`, where `_accepted_tasks` already builds
`{node_id, task_id, duration_s}` per accepted task.

That is better than fixing it in the driver, and not merely a workaround:
`_accepted_tasks`' own docstring records that the "which attempts count as
accepted" judgement was **moved rather than copied** so provenance and the
credit ledger can never disagree. Having the driver re-derive node ids from
`coord.tasks()` would reintroduce exactly that second opinion.

## 4. Recording (decision 2)

`RoundResult` gains a `clipped` entry: **task id**, original norm, cap, scale.
`on_round` adds `node_id` by joining on task id, and stores `None` rather
than dropping an event when a task has no accepted node id.

The cloud's `fedavg.on_round` already writes a `job_rounds` row per round and
already receives `RoundResult`. It records the clip events alongside it —
migration `0005`, a `clipped jsonb not null default '[]'` column on
`public.job_rounds`, matching how `contributors` is already stored.

**Nothing is enforced.** No machine is quarantined, no credit withheld, no
lease refused. The row exists so the owner can look. That is decision 2, and
the reason is that a false positive costs a volunteer their machine while a
false negative costs one undeserved credit — an asymmetry that only points
one way with a fleet this size.

## 5. Definition of done

1. A round where every norm is within `C` produces **byte-identical** output
   to `reduce_deltas` before this change — asserted against the existing
   fixtures, not approximated.
2. A contribution at 1e6 among honest contributions of ~1.0 is scaled to
   `C`, and **every contribution entering the mean has norm ≤ C**, so the
   mean does too.

   CORRECTED 2026-08-03: this originally said "stays within the honest convex
   hull", which is arithmetically false. With honest norms 0.9/1.0/1.1 and an
   adversary at 1e6: median 1.05, cap 3.15, mean
   `(0.9+1.0+1.1+3.15)/4 = 1.5375` — outside `[0.9, 1.1]`. No clip factor > 1
   fixes this at a small quorum, because a contribution admitted *at* the cap
   necessarily pulls the mean past the honest maximum; it holds only from
   ~19 honest contributors upward. The bound above is what the mechanism
   actually guarantees. (Unclipped, the same round averages 250000.75.)
3. A single contribution is never clipped.
4. Clipping never introduces NaN/Inf (a zero-norm contribution must not
   divide by zero).
5. `ClipEvent`s carry index, original norm, cap and scale.
6. The driver maps indices to node ids and puts them on `RoundResult`.
7. `job_rounds.clipped` records them; an unclipped round stores `[]`.
8. `clip_factor` is configurable, defaults to 3.0, and a non-positive or
   non-finite value is rejected rather than silently disabling the cap.
9. Suites green: flashruntime, flashnode, apps/api, e2e.

## 6. Out of scope

1. **Spot-check re-execution** — thread 4 proper.
2. **Trimmed-mean / Krum aggregation.** Both need `≥ 2k+1` contributions to
   drop `k`; at `min_participants = 2` there is nothing to trim. Revisit when
   a real quorum exists.
3. **Any automatic consequence** (decision 2).
4. **Non-federated workloads.**
5. **Direction-aware defences** (cosine similarity to the median update,
   sign-flip detection). Bigger, and worth doing only once there is a fleet
   to measure against.
