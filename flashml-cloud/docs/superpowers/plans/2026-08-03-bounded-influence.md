# Bounded Influence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Cap how far one contributor can move a federated model, record when the cap binds, and change nothing about an honest round.

**Spec:** `flashml-cloud/docs/superpowers/specs/2026-08-03-bounded-influence-design.md`

**Two repos.** Tasks 1–2 in PUBLIC `Zolli-Labs/flashml`; Task 3 in PRIVATE `Zolli-Labs/flashml-cloud`.

## Global Constraints

- **The governing property: an honest round is BYTE-IDENTICAL to today.**
  `reduce_deltas` has 30+ tests pinning current behaviour and exactly one
  production caller. If any of those tests change meaning, the design is
  wrong — stop and report rather than editing them to fit.
- `flashml_workloads/fedavg_weights.py` is **pure stdlib on purpose** (same
  rule as `kmeans_shard` and `sgd_trainer`). No numpy. Norms are computed
  with `math` and comprehensions.
- Public repo: Apache-2.0, no secrets, no private-repo identifiers.
- flashml-cloud has a live parallel session. Never `git add -A`; run
  `git show --stat HEAD` before each commit.
- Baselines: flashruntime **595**, flashnode **338**, apps/api **462**,
  e2e **67**.
- flashruntime tests need the venv on PATH:
  `cd flashruntime && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q`

---

### Task 1: Clipping + reporting in `fedavg_weights`

**Files:** `flashruntime/flashml_workloads/fedavg_weights.py`, `flashruntime/tests/test_fedavg_weights.py`

**Produces**, for Task 2:
- `CLIP_FACTOR: float = 3.0`
- `ClipEvent` — a `BaseModel` or `NamedTuple` with `index: int`,
  `norm: float`, `cap: float`, `scale: float`
- `reduce_deltas_with_report(contributions, *, clip_factor=CLIP_FACTOR) -> tuple[dict, list[ClipEvent]]`
- `reduce_deltas(contributions, *, clip_factor=CLIP_FACTOR) -> dict` —
  unchanged signature for its existing caller, now a thin wrapper

- [ ] **Step 1: failing tests**

Cover, one test each:
- **the honest-unchanged property**: a multi-contribution round whose norms
  are all similar produces a result `==` (exact equality, not `approx`) to
  what the current implementation returns. Capture the expected value as a
  literal so it cannot drift with the implementation.
- a contribution of 1e6 among ~1.0 contributions is scaled to the cap, and
  the reduced result stays inside the honest convex hull
- a single contribution is never clipped (median is its own norm)
- **a zero-norm contribution does not divide by zero** and yields no NaN
- `ClipEvent` carries index / norm / cap / scale, and the index identifies
  the right contribution when several are clipped
- an unclipped round reports `[]`
- `clip_factor` ≤ 0, NaN or Inf raises rather than silently disabling the cap
- all existing guards still fire in the same order — a malformed
  contribution must still raise its original error, not be clipped first
  (**order matters**: validate, then clip)

- [ ] **Step 2: run, watch fail**
- [ ] **Step 3: implement**

Norm is L2 across every parameter's `data`, flattened:
`math.sqrt(sum(v*v for p in blob.values() for v in p["data"]))`.
Median of an even-length list is the mean of the two middles.

Clip AFTER the existing validation loop — a NaN weight must still raise
`NonFiniteWeights`, not be scaled.

- [ ] **Step 4: run, watch pass** — expect 595 + your new tests
- [ ] **Step 5: commit**

---

### Task 2: Attribution in the driver

**Files:** `flashruntime/flashml_workloads/fedavg_driver.py`, `flashruntime/tests/test_fedavg_driver.py` (or wherever driver tests live — check)

⚠️ **CORRECTED 2026-08-03 — this task's original text was wrong.** It claimed
`collected` is `(delta, n, node_id)`. It is `(delta, samples, loss)`
(`_fetch`, line ~246) — the third element is the per-shard loss, used for
`mean_loss`. There is no node id in this driver at all.

Report **`task_id`** instead: `_fetch` preserves the order of `keys`, so
`ClipEvent.index` maps to `keys[event.index]`. The node id is joined
cloud-side in Task 3, where `_accepted_tasks` already produces
`{node_id, task_id, duration_s}`.

`RoundResult` (TypedDict, line ~70) gains:
`clipped: list[dict]` — each `{"task_id", "norm", "cap", "scale"}`.

- [ ] **Step 1: failing test** — a round with one adversarial contribution
      produces a `RoundResult` whose `clipped` names **that task id**; an
      honest round produces `clipped: []`.
- [ ] **Step 2: run, watch fail**
- [ ] **Step 3: implement** — switch the call to
      `reduce_deltas_with_report`, map indices to node ids, populate the key.
      Everything else in the driver is untouched.
- [ ] **Step 4: run, watch pass**  - [ ] **Step 5: commit**

---

### Task 3: Record it — PRIVATE REPO

**Files:** `flashml-cloud/apps/api/migrations/0005_job_rounds_clipped.sql`, `flashml_cloud_api/db.py`, `flashml_cloud_api/fedavg.py`, `tests/`

`db.record_job_round` already takes `contributors: list[str]` and stores it
with `Json(list(contributors))` — follow that exactly.

- [ ] **Step 1: migration**

```sql
alter table public.job_rounds
    add column if not exists clipped jsonb not null default '[]'::jsonb;
```

Header comment in the style of `0004`: what it is, why, and that it is
applied by `python -m flashml_cloud_api.migrate`, not by hand.

- [ ] **Step 2: failing tests** — `record_job_round` persists clip events;
      absent/empty stores `[]`; `on_round` passes what the driver reported.
- [ ] **Step 3: run, watch fail**  - [ ] **Step 4: implement**
- [ ] **Step 5: run whole suite** — 462 + new
- [ ] **Step 6: commit** (`migrations/`, `db.py`, `fedavg.py`, `tests/` only —
      never `app.py`, never `apps/web/`)

🔒 **Do NOT apply the migration to any real database.** Dev is auto-migrated
by CI on merge to `develop`; prod goes through `deploy-prod`.

---

### Task 4: Correct the two false claims

**Files:** `flashruntime/docs/guides/donate-a-machine.md`, `flashml-cloud/docs/superpowers/specs/POSITIONING_LOG.md`

Both currently assert a design that does not exist (spec §1.1).

- [ ] `donate-a-machine.md` limitation 1 — "Spot-check verification and a
      reputation system are designed but not built" is false; no such design
      exists in either repo. Say what IS true now: contributions are
      magnitude-capped in federated aggregation, individual results are still
      unverified, and nothing re-runs a task.
- [ ] `POSITIONING_LOG.md` thread 4 — "Designed (`flashnode/benchmark/`
      ABCs)" is wrong: that module is admission capability probing
      (`cpu_hash_mbps`, `mem_bandwidth_mbps`, `disk_write_mbps`), not result
      verification. **Append a dated entry, never rewrite** — that file is a
      trail. Thread 4 stays OPEN; this plan does not close it.
- [ ] commit
