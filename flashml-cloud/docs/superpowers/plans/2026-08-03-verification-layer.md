# Verification Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** A verification layer that flags work that probably was not done — cheapest signal first, nothing enforced.

**Spec:** `flashml-cloud/docs/superpowers/specs/2026-08-03-result-verification-design.md`

**Two repos.** Tasks 1–2 PRIVATE (`flashml-cloud`); Tasks 3–5 PUBLIC (`flashml`).

## Global Constraints

- **Nothing is ever enforced.** No quarantine, no clawback, no placement
  change, no failed commit. Every slice writes a row and stops. A verifier
  that can refuse work can take the fleet down on a false positive.
- **`unknown` must never be stored or rendered as `pass`.** Absence of
  evidence is not evidence of honesty — this is the error most likely to
  creep in, and every task below has a test for it.
- Verification must never break the thing it observes. Every write is
  best-effort, wrapped like `touch_machine_last_seen` at `app.py:1263`.
- Baselines: flashruntime **620**, flashnode **338**, apps/api **464**,
  e2e **67**.
- flashruntime tests need the venv on PATH:
  `cd flashruntime && PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -q`
- flashml-cloud has a live parallel session: never `git add -A`,
  `git show --stat HEAD` before every commit, never touch `app.py` beyond the
  one route change in Task 2, never `apps/web/`.

---

### Task 1: The `verifications` ledger — PRIVATE

**Files:** `migrations/0006_verifications.sql`, `flashml_cloud_api/db.py`, `tests/`

```sql
create table if not exists public.verifications (
    id          uuid primary key default gen_random_uuid(),
    machine_id  uuid references public.machines(id) on delete cascade,
    job_id      text not null,
    task_id     text not null,
    slice       text not null check (slice in ('timing','evidence','redundancy')),
    verdict     text not null check (verdict in ('pass','flag','unknown')),
    detail      jsonb not null default '{}'::jsonb,
    created_at  timestamptz not null default now()
);
```

RLS enabled with no policies, like every other table (0001's rule).
`machine_id` nullable: a redundancy mismatch names two machines and neither is
the guilty one, so a row may be about a pair rather than a machine.

- [ ] failing tests → migration + `record_verification` → pass → commit

---

### Task 2: Slice 1, timing anomaly — PRIVATE

**Files:** `flashml_cloud_api/verify.py` (new), `flashml_cloud_api/app.py` (the credit block only), `tests/`

Runs where credit is already recorded — `attempt_complete`, after
`record_contributions`. Everything it needs is already there.

```python
def timing_verdict(peers: list[float], observed: float,
                   *, min_peers: int = 3, floor_ratio: float = 0.2
                   ) -> tuple[str, dict]:
    """('pass'|'flag'|'unknown', detail)."""
```

⚠️ **CORRECTED 2026-08-03 — the snippet below produced SIX wrong `pass`
verdicts and was rightly refused.** Verified side by side against the
implementation. The worst: with peers `[0.0, 0.0, 0.0]` the median is 0.0, so
`0.01 < 0.0` is False and the answer is **`pass`** — a job where every other
machine also returned instantly certifies the next liar. A verifier that
does that is worse than none.

The others: non-finite peers compared against a meaningless median; unusable
peers counted toward `min_peers` so `[9, 9, NaN, 0.0]` clears a 3-sample gate
on a 2-sample baseline; NaN or ±inf `observed` losing every comparison and
passing; and `floor_ratio=0` putting the floor at zero so nothing can ever
fall below it.

The rules that actually hold:

- peers are **OTHER machines'** `duration_s` for the same `job_id` — never the
  machine's own history, or a consistently fast liar becomes its own baseline
- **filter unusable peers first**, then count: non-finite and non-positive
  samples are not evidence and must not pad `min_peers`
- fewer than `min_peers` usable peers ⇒ `unknown`, never `pass`
- a non-finite `observed` ⇒ `unknown`; a non-positive `observed` ⇒ `flag`
  (both timestamps are the API's own, so it needs no peer to be impossible)
- `observed < median(usable peers) * floor_ratio` ⇒ `flag`
- otherwise `pass`
- `floor_ratio` outside `(0, 1]` raises — a verifier misconfigured into
  certifying everything is worse than one switched off

Tests: the three verdicts; a fast-but-honest machine at 0.5× median passes;
0.05× flags; two peers ⇒ unknown; an empty peer list ⇒ unknown; a zero or
negative observed ⇒ flag, not a crash.

- [ ] failing tests → implement → wire into the credit block (best-effort,
      never affecting the agent's response) → pass → commit

---

### Task 3: `ExecutionEvidence` on the wire — PUBLIC

**Files:** `flashruntime/flashruntime/protocol/v1alpha1.py`, `service/modea.py`, tests

```python
class ExecutionEvidence(BaseModel):
    wall_seconds: float | None = None
    cpu_percent_mean: float | None = None
    gpu_util_percent_mean: float | None = None
    image_digest: str = ""
    exit_code: int | None = None
```

`CompleteRequest` gains `evidence: ExecutionEvidence | None = None`.

**Optional, and it must stay optional.** Every deployed agent predates it; a
required field would 422 every completion in the fleet on upgrade. This is
the same fail-safe reasoning as `module_capable`'s fail-open polarity, and
for the same reason — availability, not security.

The coordinator accepts and ignores it (it has no ledger); the API reads it.

- [ ] failing tests → implement → pass → commit

---

### Task 4: flashnode reports evidence — PUBLIC

**Files:** `flashnode/flashnode/executor/loop.py` (or wherever `complete` is called — check), `flashnode/tests/`

Measure wall-clock around the runner; read the image digest it actually ran;
capture the container exit code. CPU/GPU means are **best-effort**: if the
sampler is unavailable, send `None` — never a fabricated number, and never a
zero standing in for "unknown".

**No new dependencies.** GPU utilisation via `nvidia-smi` like the probe;
absent ⇒ `None`.

- [ ] failing tests → implement → pass → commit

---

### Task 5: Node exclusion, the sixth placement gate — PUBLIC

**Files:** `flashruntime/flashruntime/scheduler/__init__.py`, tests

```python
excluded = task.payload.get("exclude_nodes")
if excluded is not None:
    if not isinstance(excluded, list):
        return False                       # type-confused ⇒ fail closed
    if node.get("node_id") in excluded:
        return False
```

Match the existing five gates' docstring style and state the polarity: **fail
closed**, like `local_datasets` and `gpus`. A task that ends up placeable
nowhere simply never runs, and slice 3 records `unknown` — never `pass`.

- [ ] failing tests → implement → pass → commit

---

### Task 6: Record the state honestly

- [ ] `PROGRESS.md` entry: what shipped, what each slice cannot do, and the
      §8 weaknesses verbatim enough that nobody has to re-derive them.
- [ ] `POSITIONING_LOG.md`: append a dated entry. **Thread 4 moves from OPEN
      to PARTIAL, not DONE** — slice 3's dispatch half is not built here, and
      collusion, tie-breaking and GPU verification remain unsolved.
- [ ] commit

## Deliberately NOT in this plan

**Slice 3's dispatch half** — expanding a task into a verification pair,
pairwise exclusion, and comparing before aggregation. Task 5 builds the gate
it needs; the scheduling is a second sitting. Recorded so the gate is not
mistaken for the feature.
