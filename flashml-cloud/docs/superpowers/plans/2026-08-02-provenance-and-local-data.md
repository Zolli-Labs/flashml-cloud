# Provenance and local data — implementation plan

> **For agentic workers:** implement task-by-task. Each task ends with a green
> test and a commit. Steps use checkbox (`- [ ]`) syntax.

**Goal:** record accepted work per machine (Part A), and let a participant
supply data that never leaves their machine (Part B).

**Spec:** `../specs/2026-08-02-provenance-and-local-data-design.md`

## Global constraints

- **Accepted work only, never attempted.** `flashml-cloud/AGENTS.md` hard rule
  4. `fedavg._contributors()` already encodes this judgement — extend it, do
  not reimplement it.
- **Idempotency is a schema property.** A unique index plus `on conflict do
  nothing`, not a convention about calling once.
- **Security-relevant fields fail closed** (flashruntime AGENTS.md rule 3). An
  absent, `None`, or wrongly-typed capability counts as *not capable*.
- `flashruntime` stays runnable with no Postgres and no cloud.
- Existing suites green at every commit; counts logged in `PROGRESS.md`.
- **Never write a real credential, dataset path, or host name into a test.**

## Dependency order

```
Task 1 (protocol)  ──┬──▶ Task 4 (placement gate)
   flashruntime      ├──▶ Task 5 (flashnode advertise + mount)
                     └──▶ Task 6 (cloud: yaml + payload)

Task 2 (migration) ──▶ Task 3 (ledger write)      ← independent of all above
   flashml-cloud
```

Tasks 1 and 2 can start simultaneously. Tasks 4/5/6 all need Task 1.

---

### Task 1 — `NodeRegistration.local_datasets` (flashruntime)

**Repo:** `Zolli-Labs/flashml`
**Files:** `flashruntime/flashruntime/protocol/v1alpha1.py`,
`flashruntime/tests/test_protocol_local_datasets.py` (create)

**Produces:** `NodeRegistration.local_datasets: list[str]`, defaulting to `[]`.

- [ ] **Write the failing test.** Assert: the field defaults to `[]` when
      absent from the wire (an already-deployed agent advertises nothing);
      a list of names round-trips through `model_validate_json`; and the
      default is not shared mutable state between two instances.
- [ ] **Run it — expect ImportError/AttributeError.**
- [ ] **Add the field**, next to `argv_capable`, with a docstring saying why it
      defaults empty: an agent that has not opted in must never be eligible for
      local-data work, and security-relevant fields fail closed.
- [ ] **Run the full flashruntime suite.** No existing test may change.
- [ ] **Commit.**

---

### Task 2 — migration 0003, the uniqueness that makes credit idempotent

**Repo:** `Zolli-Labs/flashml-cloud`
**Files:** `flashml-cloud/apps/api/migrations/0003_contributions_unique.sql`
(create)

**Produces:** a unique index making a duplicate credit row impossible.

- [ ] **Write the migration.** Idempotent like the others (`create unique index
      if not exists`):

```sql
-- A credit ledger that can double-count is worse than no ledger: the error is
-- silent and compounds. AGENTS.md hard rule 4 requires idempotent commits, and
-- a convention about calling the writer once is not a guarantee — a round
-- callback can be retried and a driver can be restarted.
--
-- coalesce(task_id, '') because task_id is NULLABLE and NULLs never collide in
-- a unique index: without it, unbounded null-task rows could be written for
-- the same machine and job.
create unique index if not exists contributions_machine_job_task_idx
    on public.contributions (machine_id, job_id, coalesce(task_id, ''));
```

- [ ] **Verify the test fixture applies it.** `tests/conftest.py` globs
      `migrations/*.sql` in numeric order, so this is picked up with no change
      — confirm by running any Postgres-backed test and checking the index
      exists.
- [ ] **Commit.**

---

### Task 3 — write the ledger (flashml-cloud)

**Repo:** `Zolli-Labs/flashml-cloud`
**Depends on:** Task 2
**Files:** `flashml_cloud_api/db.py`, `flashml_cloud_api/fedavg.py`,
`tests/test_contributions.py` (create)

**Produces:** `dbmod.record_contributions(db, *, job_id, entries)` where each
entry is `{node_id, task_id, duration_s}`; called from `fedavg.on_round`.

- [ ] **Write the failing tests** against real Postgres (the existing
      `postgres_dsn` fixture):
      - a completed round writes one row per accepted contributor, resolved
        `node_id → machine_id`;
      - **calling it twice writes no duplicates** — the property Task 2 exists
        for;
      - a `node_id` with no `machines` row is skipped and the call still
        succeeds;
      - an empty entry list is a no-op, not an error.
- [ ] **Run — expect failure.**
- [ ] **Implement `record_contributions`.** Resolve `node_id → machine_id` in
      one query rather than per row. Insert with `on conflict do nothing`.
      Docstring must say why a missing machine row is skipped rather than
      raised: a self-hosted node has no cloud enrolment and that is a valid
      deployment, not an error.
- [ ] **Extend `_contributors`** — or add a sibling returning
      `{node_id, task_id, duration_s}` per accepted task — **without changing
      its acceptance judgement**. It filters on `COMPLETED` and deliberately
      refuses to credit a node that held a task which later failed elsewhere.
      That is hard rule 4 and must survive verbatim.
- [ ] **Call it from `on_round`,** on the same connection as
      `insert_job_round`, and — like `_contributors` — never fatal: a credit
      row must not fail a round that already aggregated.
- [ ] **Run the full api suite.**
- [ ] **Commit.**

---

### Task 4 — the fourth placement gate (flashruntime)

**Repo:** `Zolli-Labs/flashml`
**Depends on:** Task 1
**Files:** `flashruntime/flashruntime/scheduler/__init__.py`,
`flashruntime/tests/test_placement_local_data.py` (create)

**Produces:** a task whose payload lists `local_inputs` is eligible only on a
node advertising every one of those names.

Read `IsolationAwarePlacement`'s existing three gates first and **copy their
shape exactly** — including how they treat type confusion. The docstring there
is the specification.

- [ ] **Write the failing tests.** A task requiring `["patients"]` is:
      - eligible on a node advertising `["patients", "labs"]`;
      - **not** eligible when the node advertises `["labs"]`, `[]`, nothing at
        all, `None`, or a non-list (the bare string `"patients"` must not
        count — the existing gates' type-confusion rule);
      - **not** waived by `allowFallback: true` — same reasoning that stops it
        waiving the argv gate;
      - unaffected when the task lists no `local_inputs`.
- [ ] **Run — expect failure.**
- [ ] **Implement the gate**, with a docstring in the register of the ones
      beside it, saying why the waiver does not apply.
- [ ] **Run the full flashruntime suite.**
- [ ] **Commit.**

---

### Task 5 — advertise and mount (flashnode)

**Repo:** `Zolli-Labs/flashml`
**Depends on:** Task 1
**Files:** `flashnode/flashnode/config/` or `inventory/capabilities.py`,
`flashnode/flashnode/executor/hardening.py`,
`flashnode/tests/test_local_data.py` (create)

**Produces:** `FLASHNODE_LOCAL_DATA="name=/path,other=/path2"` parsed into a
map; names advertised in registration; the runner bind-mounts read-only.

- [ ] **Write the failing tests:**
      - parsing: `"a=/x,b=/y"` → `{"a": "/x", "b": "/y"}`; empty/unset → `{}`;
        a malformed entry is refused rather than partially accepted;
      - **a label containing `/`, `..`, or outside `[A-Za-z0-9._-]` is
        rejected** — a label is never joined to a path, and this keeps it that
        way even if someone later changes that;
      - `discover()` advertises the label names and **never the paths** — a
        path is host-private and must not travel to the coordinator;
      - the constructed docker args mount the host path **read-only** at
        `inputs/<name>` (`:ro`);
      - a task requesting a label the host has not mapped is refused, and the
        error names the label.
- [ ] **Run — expect failure.**
- [ ] **Implement.** Follow the existing hardening tests' style: assert on the
      constructed argv, which needs no Docker daemon.
- [ ] **Run the full flashnode suite**, including with no `FLASHNODE_LOCAL_DATA`
      set — the default path must be unchanged.
- [ ] **Commit.**

---

### Task 6 — `local_inputs` in the job config (flashml-cloud)

**Repo:** `Zolli-Labs/flashml-cloud`
**Depends on:** Task 1
**Files:** `flashml_cloud_api/flashml_yaml.py`, `flashml_cloud_api/compile.py`,
`tests/test_flashml_yaml.py`, `tests/test_compile.py`

**Produces:** `local_inputs: [str]` accepted in `flashml.yaml` and carried into
the task payload so the placement gate can read it.

- [ ] **Write the failing tests:**
      - `local_inputs` parses to a list of names; absent → `[]`;
      - a non-list, or a list containing a non-string, is a `ConfigError`
        naming the key;
      - a label outside `[A-Za-z0-9._-]` is refused **at submit time** — the
        earliest place a job author can be told;
      - an unknown key still fails (do not widen `ALLOWED_KEYS` by accident);
      - `compile_to_jobspec` puts the names in the payload, and **does not**
        add them to `inputs` or `unpack_inputs` — they are not artifacts, and
        **nothing is uploaded for them**. Assert that explicitly: it is
        Definition-of-Done item 7 and the entire premise of the feature.
- [ ] **Run — expect failure.**
- [ ] **Implement**, adding `local_inputs` to `OPTIONAL_KEYS`.
- [ ] **Run the full api suite.**
- [ ] **Commit.**

---

## Release step (human)

Tasks 1, 4, 5 land in the public repo. Once green:

1. Bump `flashruntime` to 0.4.0 (a new protocol field) and `flashnode` to
   0.3.0; flashnode's floor becomes `>=0.4,<0.5`.
2. Tag `flashruntime-v0.4.0`, **wait for PyPI**, then `flashnode-v0.3.0` —
   flashnode's floor must exist before it publishes, which the `resolvable`
   job enforces.
3. Update the three pins in flashml-cloud: `apps/api/pyproject.toml`,
   `render.yaml`, `Makefile`. They must agree.

## Definition of done

Spec §6, items 1–8.

## Not in this plan

Independent-job contributions, credit for post-quorum work, dataset content
validation, secure aggregation. All recorded as gaps in spec §5.
