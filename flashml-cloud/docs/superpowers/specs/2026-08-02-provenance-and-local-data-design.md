# Provenance and local data — design

**Date:** 2026-08-02
**Scope:** flashml-cloud (API), flashruntime (protocol + placement), flashnode
**Status:** design. Follows directly from `POSITIONING_LOG.md`, open threads 1
and 3.

---

## 1. Why these two, together

Both are gates that the positioning log identified, and both are prerequisites
for the model FlashML is most defensible in.

**Part A — the contributions ledger.** `public.contributions` exists, is
documented as *"a machine's accepted contribution to a job (distinct from
merely attempted work). Used for host credit and metrics accounting"*, and
**nothing writes to it.** Zero rows after a successful five-round run. No host
can be credited for anything, and Stage 8's metrics page computes goodput and
lost-work from exactly this table.

Under the barter model — which the laptop economics leave as one of only two
viable options for CPU supply — this table stops being a metrics nicety and
becomes the **currency**.

**Part B — local data binding.** Every task input is downloaded from the
coordinator as an artifact. Nothing mounts a host directory. So a participant
cannot supply data that stays on their own machine, which breaks the premise
of the one use case that is not a price competition: federated learning where
data legally cannot move.

They are independent. Part A touches only flashml-cloud; Part B is cross-repo.

## 2. Part A — the contributions ledger

### 2.1 What gets recorded

One row per **accepted** unit of work: `(machine_id, job_id, task_id,
accepted_at, duration_s)`.

**Accepted, never attempted.** `flashml-cloud/AGENTS.md` hard rule 4:
*"Distinguish attempted work from accepted work everywhere money, credits, or
metrics are involved."* `fedavg._contributors()` already gets this right —
it filters on `COMPLETED` and explicitly refuses to credit a node that held a
task which later failed and was retried elsewhere. Part A extends that
function's data rather than reimplementing its judgement.

### 2.2 Idempotency is a schema property, not a code convention

Same hard rule: *"Idempotent commits; no double counting."* A round callback
can be retried; a driver can be restarted. Relying on the caller never firing
twice is exactly how a credit ledger silently inflates.

**Migration 0003** adds:

```sql
create unique index contributions_machine_job_task_idx
    on public.contributions (machine_id, job_id, coalesce(task_id, ''));
```

and every insert uses `on conflict do nothing`. Double-counting then requires
a schema change rather than a mistake.

`coalesce(task_id, '')` because `task_id` is nullable and NULLs do not collide
in a unique index — without it, a null-task row could be written unboundedly.

### 2.3 Nodes with no machine row are skipped, not fatal

A node registered directly against a self-hosted coordinator has no row in
`public.machines`. That is a legitimate deployment, not an error. Such a
contributor is skipped silently; a missing credit row must never fail a round
that already aggregated successfully — the same reasoning `_contributors`
already applies to an unavailable task list.

### 2.4 Where it is written

In `fedavg.on_round`, beside the existing `insert_job_round`, on the same
connection. Independent (non-federated) jobs are **out of scope for v1** — they
have no equivalent completion callback in the API today, and adding one is a
larger change than this warrants. Recorded as a gap in §5.

## 3. Part B — local data binding

### 3.1 The shape

A job declares that an input comes from the host rather than from an artifact:

```yaml
# flashml.yaml
local_inputs: ["patients"]
```

A host owner opts in by naming what they are willing to expose:

```bash
FLASHNODE_LOCAL_DATA="patients=/srv/data/patients-2026"
```

The agent advertises the **names** it can serve, never the paths. The
coordinator refuses to place a task requiring `patients` on a node that has
not advertised it. The runner bind-mounts the host directory **read-only** at
`inputs/patients/`, exactly where an artifact-sourced input would have landed —
so user code sees no difference.

### 3.2 The security properties this must have

**A job can never name a host path.** It names a label. The mapping from label
to path is chosen entirely by the host owner. Nothing a submitter writes can
traverse anywhere: the label is validated against a conservative alphabet and
is never joined to a filesystem path on the agent side.

**The mount is read-only.** A task may read the host's data; it may not modify
or delete it.

**The data is never uploaded.** It is not an artifact, has no `artifact://`
URI, and does not pass through the coordinator. That property is the entire
point, and §6's test asserts it rather than assuming it.

**The placement gate fails closed.** A fourth gate in
`IsolationAwarePlacement`, built exactly like the existing three: a task whose
payload lists `local_inputs` may only be leased to a node advertising every one
of those names, and an absent or non-list capability counts as *not* capable.
`allowFallback` does not waive it — the same reasoning that stops it waiving
the argv gate. A task landing on a node without the data would fail anyway;
failing closed makes it fail *before* the data is touched.

### 3.3 What this does not do in v1

- **No schema or content validation.** Whether `/srv/data/patients-2026`
  contains what the job expects is between the host owner and the job author.
- **No dataset versioning or integrity checking.** A label is a label.
- **No discovery.** A job author must already know the label exists, out of
  band. That is realistic for the target case — a consortium agrees on
  `patients` before anyone runs anything — and wrong for a public marketplace,
  which is not this use case.
- **No secure aggregation.** Data staying local does not make weight updates
  private; gradient inversion is real. That is a separate, larger piece of
  work, and the log is explicit that it should follow a conversation with a
  real user rather than precede one.

## 4. Cross-repo shape

| Repo | Change |
|---|---|
| flashruntime | `NodeRegistration.local_datasets: list[str]`; fourth placement gate |
| flashnode | parse `FLASHNODE_LOCAL_DATA`; advertise names; read-only bind mount |
| flashml-cloud | `local_inputs` in `flashml.yaml`; carry it into the task payload |

**The protocol change lands first and ships as a release**, because flashnode
and flashml-cloud both consume it as a pinned version. That is the discipline
Plan A established and it applies here: the field must exist in a published
`flashruntime` before either consumer can pin a version that uses it.

`local_datasets` defaults to `[]` — an already-deployed agent advertises
nothing and is therefore never eligible for local-data work. Security-relevant
fields fail closed (flashruntime AGENTS.md rule 3).

## 5. Known gaps this leaves

1. **Independent jobs record no contributions** (§2.4). Federated rounds do.
2. **No credit for work accepted after a round's quorum.** A machine that
   reports late still did the work; the round already completed without it.
   Whether that earns credit is a product decision, not a technical one, and
   v1 follows `_contributors`, which does not count it.
3. **A host can advertise a label it cannot serve.** Nothing verifies the
   directory exists or is readable until a task tries it. Fail-closed
   placement prevents wrong *placement*, not host misconfiguration.
4. **No secure aggregation** (§3.3).

## 6. Definition of done

1. A completed federated round writes one `contributions` row per accepted
   contributor, resolved to `machine_id`.
2. Running the same round callback twice writes no duplicate rows — asserted,
   against real Postgres.
3. A contributor with no `machines` row is skipped and the round still
   succeeds.
4. `flashml.yaml` accepts `local_inputs`; an unknown key still fails.
5. A task requiring a local input is **never leased** to a node that has not
   advertised it, including when the capability is absent, `None`, or a
   non-list.
6. `FLASHNODE_LOCAL_DATA` maps a label to a path; the runner mounts it
   **read-only**; a label that is not in the host's map is refused.
7. **The local directory is never uploaded** — no artifact is created for it,
   asserted rather than assumed.
8. Existing suites stay green, counts recorded in `PROGRESS.md`.

## 7. Out of scope

Result verification, capability-aware placement beyond the fail-closed gate,
GPU support, the desktop app. All are in `POSITIONING_LOG.md`'s open threads
behind these two.
