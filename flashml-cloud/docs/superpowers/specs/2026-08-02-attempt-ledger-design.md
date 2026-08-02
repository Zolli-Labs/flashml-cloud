# Attempt ledger — crediting accepted work on every job

**Date:** 2026-08-02
**Status:** design approved for implementation
**Closes:** §5.1 of `2026-08-02-provenance-and-local-data-design.md`
("Independent jobs record no contributions")

---

## 1. The problem

`record_contributions` has exactly one caller:

```
flashml_cloud_api/fedavg.py:330    _record_contributions(db, coordinator_job_id, accepted)
```

It sits inside `on_round`. So a machine is credited **only** for federated
rounds. Every other job — an sklearn sweep, a `command` job, anything
submitted from `flashml.yaml` — credits nobody, however much work the
machine actually did and however much of it was accepted.

That is a hole in the exact feature we shipped yesterday, and it is worst
for the workload class the positioning log says volunteer machines are
genuinely good at:

> *"a GPU job must not land on a laptop, and a 500-config sweep should"*
> — `POSITIONING_LOG.md`, open thread 5

A 500-config sweep is the case for donated laptops. It is also the case
that currently pays out zero. The credit ledger the barter model runs on
does not cover the workload the barter model is for.

## 2. Why this is not a one-line fix

The obvious move — credit in the API's `attempt_complete` proxy route — is
wrong twice over, and both reasons are load-bearing.

### 2.1 HTTP 200 does not mean accepted

The coordinator (`flashruntime/service/modea.py:674`) answers **200 with a
body** in three distinguishable cases:

| Coordinator outcome | Status | Body |
|---|---|---|
| output hash validated, commit won | 200 | `{"accepted": true}` |
| output validation failed → requeued | 200 | `{"accepted": false, "detail": …}` |
| late commit, another attempt already won | 200 | `{"accepted": false}` |
| lease unknown / not held | 404 | — |

Crediting on `2xx` would pay for work whose output **failed its hash check**
and for duplicate commits that lost the race. That is precisely what this
repo's hard rule 4 forbids:

> *Distinguish **attempted** work from **accepted** work everywhere money,
> credits, or metrics are involved. Idempotent commits; no double counting.*

**The acceptance signal is the `accepted` field in the response body, never
the status code.**

### 2.2 The complete call does not say what was completed

`CompleteRequest` is `{output_sha256: str}`. The response is
`{accepted: bool}`. Neither carries `job_id` or `task_id` — the route knows
only a `lease_id`. But `contributions` is keyed on
`(machine_id, job_id, task_id)`, so the credit cannot be written from what
the completion hop alone can see.

The missing mapping exists one hop earlier: `claim` returns a
`protocol.v1alpha1.Lease`, which carries `lease_id`, `job_id`, `task_id`,
and `node_id`. **The API must remember the claim in order to credit the
completion.**

### 2.3 Why not change the protocol instead

Adding `job_id`/`task_id` to the complete response would also work and is
arguably cleaner. It is rejected for now because it costs a `flashruntime`
release plus a coordinated pin bump in three files, to serve one consumer —
and because the API needs durable attempt records anyway (§2.4). Revisit if
a second consumer ever wants the same field.

### 2.4 A durable attempt record is the documented target, not scope creep

`flashml-cloud/CLAUDE.md`, hard rule 3:

> *Durable state (jobs, **attempts**, leases, checkpoints, failure events,
> recovery actions, usage, contribution) lives in Postgres with an
> append-only event ledger — never only in memory.*

The API has had no attempts table. This adds the first one, and the
protocol already defines the shape to mirror — `protocol.v1alpha1.TaskAttempt`
carries `attempt_id, task_id, job_id, node_id, attempt_number, started,
finished, outcome, output_sha256, accepted`.

## 3. Design

### 3.1 Data flow

```
agent ── POST /leases/claim ──► API ──► coordinator
                                 │      200 + Lease{lease_id, job_id, task_id}
                                 └──► attempts INSERT (best-effort)

agent ── POST /attempts/{id}/complete ──► API ──► coordinator
                                           │      200 {"accepted": true}
                                           ├──► attempts SELECT → job_id, task_id
                                           └──► record_contributions(...)
```

### 3.2 New table

```sql
create table public.attempts (
    lease_id    text primary key,
    machine_id  uuid not null references public.machines(id) on delete cascade,
    job_id      text not null,
    task_id     text not null,
    claimed_at  timestamptz not null default now(),
    accepted_at timestamptz
);
```

RLS enabled, matching every other table from 0001. `machine_id` rather than
`node_id`: the API already resolved the token to a machine, and a foreign key
is stronger than a string it would have to re-resolve later.

`accepted_at` is set when the credit is written. It is not the credit itself —
`contributions` remains the ledger — it exists so a completion that is
processed twice is visibly a repeat rather than silently absorbed by the
unique index.

### 3.3 Credit on claim-owner match only

The lookup is `where lease_id = %s and machine_id = %s`, using the machine
the **completing** token resolves to. A row that does not match is not
credited.

The coordinator already enforces lease ownership (`_require_lease_holder`),
so this cannot currently be reached. It is written this way anyway because
this is the credit ledger: it should not depend on a remote component's
authorization to be correct about who gets paid.

### 3.4 Federated jobs are already covered — and must not double-count

A federated run is one coordinator job **per round**. `fedavg` credits with
`job_id = the round's coordinator job id`, which is exactly the `job_id` the
lease for that round's task carries. So both paths compute the **same**
`(machine_id, job_id, task_id)` key, and the unique index from migration
0003 collapses them.

This is a required test, not an observation: if the two paths ever key
differently, federated hosts get paid twice and the error compounds silently.

### 3.5 Best-effort, always

Both writes are wrapped exactly like `touch_machine_last_seen` already is:

```python
try:
    ...
except Exception:
    log.warning(...)
```

A credit ledger must never be the reason a claim fails or a commit is
refused. A machine that loses a lease because Postgres hiccuped is a
correctness failure; a machine that misses one credit is an accounting
gap we can reconcile.

### 3.6 Duration

`duration_s = now() - claimed_at`, computed at credit time. This is
lease-held wall clock, not CPU time — it includes input download and output
upload. That is the honest definition for a contribution ledger and matches
what the federated path already records.

## 4. What this does not do

1. **No retroactive credit.** Work accepted before this ships is not
   backfilled; the attempts rows do not exist.
2. **Failed and expired attempts are not recorded.** The table gets a row on
   claim and an `accepted_at` on credit. `fail` and lease expiry leave the
   row untouched, so it is not yet a complete attempt ledger — it is the
   subset needed to credit accepted work. Extending it to a full
   append-only attempt history is Plan B2's business.
3. **No garbage collection.** Rows accumulate one per claim. At POC volume
   this is nothing; a retention policy is deferred until it matters.
4. **Still no result verification.** A lying node whose output happens to
   hash correctly is still believed. That is open thread 4 and unchanged
   here — this credits *accepted* work, where "accepted" means the
   coordinator validated the hash, not that the numbers are right.

## 5. Definition of done

1. `public.attempts` exists with RLS, created by migration `0004`.
2. A proxied `claim` that returns a lease writes an attempts row.
3. A proxied `claim` returning 204 writes nothing.
4. A completion with `{"accepted": true}` writes a contribution with the
   lease's `job_id` and `task_id`.
5. A completion with `{"accepted": false}` writes **no** contribution —
   asserted separately for the validation-failure and late-commit bodies.
6. A non-2xx completion writes no contribution.
7. Completing the same lease twice writes one contribution row.
8. A federated round credited by both `fedavg` and this path yields exactly
   one row per `(machine, round job, task)`.
9. A completion whose lease was claimed by a different machine writes no
   contribution.
10. Postgres failing on either write does not change the response the agent
    receives.
11. Full suites green: apps/api, flashruntime, flashnode, e2e.
