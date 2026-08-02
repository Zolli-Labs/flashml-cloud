# Attempt Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Credit accepted work on *every* job, not only federated rounds, by giving the API a durable `lease_id → (job_id, task_id)` mapping.

**Architecture:** A new `public.attempts` table written on proxied `claim` and consumed on proxied `complete`. The acceptance signal is the coordinator's response **body** (`{"accepted": bool}`), never its status code. Credit is written through the existing `db.record_contributions`, which is already idempotent by schema.

**Spec:** `docs/superpowers/specs/2026-08-02-attempt-ledger-design.md`

**Tech Stack:** FastAPI, psycopg 3, PostgreSQL, pytest. All work is in
`flashml-cloud/apps/api/`.

## Global Constraints

- **Repo:** `Zolli-Labs/flashml-cloud` (private). Everything here is under
  `flashml-cloud/apps/api/`. Nothing in this plan touches `flashruntime` or
  `flashnode` — **no public release and no pin bump is required.** If you
  find yourself editing `../flashml`, stop: you have misread the plan.
- **DO NOT TOUCH the web console.** The user is editing
  `apps/web/components/landing/*`, `apps/web/app/*`, `apps/web/components/nav/*`,
  `apps/web/components/motion/*`, and `apps/web/lib/motion.ts` in a parallel
  session. Never run `git add -A`. Stage only the explicit `apps/api` paths
  named in each Commit step.
- 🔴 **`flashml_cloud_api/app.py` HAS UNCOMMITTED WORK THAT IS NOT OURS.**
  Discovered 2026-08-02 on branch `feat/landing-redesign`: a ~142-line
  `GET /v1alpha1/jobs/{job_id}/events` route the user is writing in a
  parallel session, paired with their new `apps/web/lib/job-activity.ts` and
  `components/jobs/Swimlanes.tsx`. The earlier assumption that the user was
  only in `apps/web` was **wrong**.

  **RESOLVED mid-plan.** The parallel session committed that work as
  `6d61b94 feat: job events/tasks endpoints and a three-view job page` while
  Task 1 was running. The working tree went clean, so Tasks 3 and 4 **do**
  commit `app.py` after all — and should do so *promptly*, because the risk
  has now inverted: uncommitted route edits of ours sitting in a shared
  working tree would be swept into the user's *next* `git add app.py`.

  Still binding:
  1. Before each commit, `git show --stat HEAD` and confirm the commit
     contains only the files you named. The parallel session is still live.
  2. Edit `app.py` only with exact-match, minimal edits against the `claim`
     and `attempt_complete` routes (now at **lines 1418 and 1432** — the
     user's commit shifted them ~140 lines down from where this plan was
     first written). Do not reformat, reorder imports, or rewrite
     neighbouring routes.
- **We are on the user's branch `feat/landing-redesign`.** Do not switch,
  rebase, merge, or create branches — their working tree is live in another
  session. Tasks 1, 2 and 5 commit only files the user is not touching
  (`migrations/`, `db.py`, `tests/`).
- **Hard rule 4** (`flashml-cloud/CLAUDE.md`): attempted work ≠ accepted
  work. Idempotent commits, no double counting.
- **Hard rule 3:** durable state lives in Postgres.
- Both new database writes are **best-effort** — wrapped in
  `try/except Exception` with a `log.warning`, exactly like the existing
  `touch_machine_last_seen` call at `app.py:1263`. A credit ledger must never
  be why an agent's claim fails or its commit is refused.
- Run the API suite with the venv python and **the tests directory on the
  path** the way the existing suite does:
  `cd flashml-cloud/apps/api && .venv/bin/python -m pytest -q`
- Baseline before you start: **403 passed**. This plan adds **16** tests
  (1 + 5 + 3 + 6 + 1), for a final total of **419**.

---

### Task 1: Migration `0004_attempts.sql`

**Files:**
- Create: `flashml-cloud/apps/api/migrations/0004_attempts.sql`
- Test: `flashml-cloud/apps/api/tests/test_schema.py`

**Interfaces:**
- Consumes: `public.machines(id)` from `0001_initial.sql`.
- Produces: table `public.attempts` with columns
  `lease_id text pk, machine_id uuid, job_id text, task_id text,
  claimed_at timestamptz, accepted_at timestamptz`.

`tests/conftest.py` globs `migrations/*.sql` in sorted order, so a new file
is applied by the ephemeral Postgres fixture automatically. No fixture change
is needed.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_schema.py`:

```python
def test_attempts_table_exists_with_rls(db):
    """The attempt ledger: the API's durable lease -> (job, task) mapping.

    Without it the API can see that a completion was ACCEPTED but not what
    was completed, because the coordinator's complete response carries only
    `{"accepted": bool}`.
    """
    with db.cursor() as cur:
        cur.execute(
            "select column_name, data_type from information_schema.columns"
            " where table_schema = 'public' and table_name = 'attempts'"
            " order by column_name"
        )
        cols = {r["column_name"]: r["data_type"] for r in cur.fetchall()}
    assert cols == {
        "accepted_at": "timestamp with time zone",
        "claimed_at": "timestamp with time zone",
        "job_id": "text",
        "lease_id": "text",
        "machine_id": "uuid",
        "task_id": "text",
    }

    with db.cursor() as cur:
        cur.execute(
            "select relrowsecurity from pg_class"
            " where oid = 'public.attempts'::regclass"
        )
        assert cur.fetchone()["relrowsecurity"] is True
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd flashml-cloud/apps/api && .venv/bin/python -m pytest tests/test_schema.py::test_attempts_table_exists_with_rls -q
```
Expected: FAIL — `assert {} == {...}` (the table does not exist).

- [ ] **Step 3: Write the migration**

Create `migrations/0004_attempts.sql`:

```sql
-- 0004_attempts.sql
--
-- The attempt ledger: the API's durable mapping from a lease to the work it
-- covers.
--
-- WHY THIS EXISTS. `contributions` is keyed on (machine_id, job_id,
-- task_id), but the hop where the API learns work was ACCEPTED —
-- POST /v1alpha1/attempts/{lease_id}/complete — carries neither job_id nor
-- task_id. The request body is {output_sha256}; the response body is
-- {accepted: bool}. The only place those ids appear is the Lease returned by
-- the CLAIM one hop earlier. So the API must remember the claim to be able
-- to credit the completion.
--
-- Until this landed, `db.record_contributions` had exactly one caller —
-- inside fedavg.on_round — so only FEDERATED rounds paid anybody. Sweeps and
-- command jobs, which is what donated laptops are actually good at, credited
-- nobody at all.
--
-- APPLIED TO SUPABASE by hand, like 0003, against project
-- yualksqjjvlfscbbsygq ONLY. There is no migration runner in this service.
--
-- Both writes against this table are best-effort in the API. It is an
-- accounting record, never a precondition for scheduling work.

create table if not exists public.attempts (
    -- The coordinator's lease id. Primary key rather than a surrogate: a
    -- lease is claimed exactly once, so this is the natural key, and it
    -- makes a duplicated claim forward a no-op instead of a second row.
    lease_id    text primary key,
    -- The machine the CLAIMING token resolved to. A machine_id, not a
    -- node_id: the API had already resolved it, and a foreign key beats a
    -- string that would have to be re-resolved at credit time.
    machine_id  uuid not null references public.machines(id) on delete cascade,
    job_id      text not null,
    task_id     text not null,
    claimed_at  timestamptz not null default now(),
    -- Set when the credit is written, by the same UPDATE that reads the row.
    -- NOT the credit itself — `contributions` remains the ledger. This makes
    -- a completion processed twice VISIBLY a repeat (the second UPDATE
    -- matches no row) rather than something silently absorbed downstream by
    -- the unique index from 0003.
    accepted_at timestamptz
);

comment on table public.attempts is
    'Durable lease -> (job, task) mapping, written when a machine claims a '
    'lease and consumed when the coordinator reports that attempt ACCEPTED. '
    'Exists because the complete hop carries no job/task id. Not yet a full '
    'attempt history: failed and expired attempts leave no mark (see the '
    'design spec, section 4.2).';

alter table public.attempts enable row level security;

-- Credit lookup is by primary key, so it needs no index. This one serves the
-- "what has this machine worked on" query and the cascade delete.
create index if not exists attempts_machine_id_idx on public.attempts (machine_id);
```

- [ ] **Step 4: Run it and watch it pass**

```bash
cd flashml-cloud/apps/api && .venv/bin/python -m pytest tests/test_schema.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/apps/api/migrations/0004_attempts.sql \
        flashml-cloud/apps/api/tests/test_schema.py
git commit -m "feat(api): add public.attempts, the durable lease -> (job, task) mapping"
```

---

### Task 2: `db.record_attempt` and `db.claim_attempt_credit`

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/db.py`
- Test: `flashml-cloud/apps/api/tests/test_contributions.py`

**Interfaces:**
- Consumes: `public.attempts` (Task 1); `insert_machine` and the `db` /
  `_new_user` fixtures already imported at the top of `test_contributions.py`.
- Produces, for Tasks 3 and 4:
  - `record_attempt(db, *, lease_id: str, machine_id: str, job_id: str, task_id: str) -> None`
  - `claim_attempt_credit(db, *, lease_id: str, machine_id: str) -> dict | None`
    returning `{"job_id": str, "task_id": str, "duration_s": float}` or
    `None`.

`claim_attempt_credit` is a single `UPDATE ... RETURNING`, not a
`SELECT`-then-`UPDATE`. Two completions racing must not both be credited, and
one statement makes that a database property instead of a Python one.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_contributions.py`:

```python
# ---------------------------------------------------------------------------
# attempts: the lease -> (job, task) mapping that lets ANY job pay out
# ---------------------------------------------------------------------------

def _lease() -> str:
    return f"lease-{uuid.uuid4().hex[:12]}"


def test_claim_attempt_credit_returns_the_claimed_work(db):
    machine = _enrol(db, _node_id("credit"))
    lease, job = _lease(), _job()
    dbmod.record_attempt(
        db, lease_id=lease, machine_id=machine, job_id=job, task_id="task-000"
    )

    row = dbmod.claim_attempt_credit(db, lease_id=lease, machine_id=machine)

    assert row is not None
    assert row["job_id"] == job
    assert row["task_id"] == "task-000"
    # A float, not a Decimal: record_contributions writes it straight through
    # to a numeric column and psycopg hands back Decimal for extract(epoch).
    assert isinstance(row["duration_s"], float)
    assert row["duration_s"] >= 0.0


def test_claim_attempt_credit_is_once_only(db):
    """The second completion of one lease credits nothing.

    Idempotence here does NOT lean on the 0003 unique index — it is a
    property of this UPDATE. Belt and braces: the index catches a duplicate
    that arrives by any other route, this catches it before a row is built.
    """
    machine = _enrol(db, _node_id("once"))
    lease = _lease()
    dbmod.record_attempt(
        db, lease_id=lease, machine_id=machine, job_id=_job(), task_id="task-000"
    )

    assert dbmod.claim_attempt_credit(db, lease_id=lease, machine_id=machine)
    assert dbmod.claim_attempt_credit(db, lease_id=lease, machine_id=machine) is None


def test_claim_attempt_credit_refuses_another_machine(db):
    """Credit follows the machine that CLAIMED, never the one that asked.

    The coordinator enforces lease ownership, so this is unreachable today.
    It is written anyway: whose work this was should not depend on a remote
    component's authorization staying correct.
    """
    owner = _enrol(db, _node_id("owner"))
    thief = _enrol(db, _node_id("thief"))
    lease = _lease()
    dbmod.record_attempt(
        db, lease_id=lease, machine_id=owner, job_id=_job(), task_id="task-000"
    )

    assert dbmod.claim_attempt_credit(db, lease_id=lease, machine_id=thief) is None
    # ...and the real owner is still able to collect.
    assert dbmod.claim_attempt_credit(db, lease_id=lease, machine_id=owner)


def test_claim_attempt_credit_unknown_lease_is_none(db):
    machine = _enrol(db, _node_id("unknown"))
    assert dbmod.claim_attempt_credit(db, lease_id=_lease(), machine_id=machine) is None


def test_record_attempt_twice_keeps_one_row(db):
    """A retried claim forward must not create a second attempt."""
    machine = _enrol(db, _node_id("dup"))
    lease, job = _lease(), _job()
    for _ in range(2):
        dbmod.record_attempt(
            db, lease_id=lease, machine_id=machine, job_id=job, task_id="task-000"
        )
    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.attempts where lease_id = %s", (lease,)
        )
        assert cur.fetchone()["n"] == 1
```

- [ ] **Step 2: Run them and watch them fail**

```bash
cd flashml-cloud/apps/api && .venv/bin/python -m pytest tests/test_contributions.py -q -k attempt
```
Expected: FAIL — `AttributeError: module 'flashml_cloud_api.db' has no attribute 'record_attempt'`.

- [ ] **Step 3: Implement both functions**

Add to `flashml_cloud_api/db.py`, directly below `record_contributions`:

```python
def record_attempt(
    db: psycopg.Connection,
    *,
    lease_id: str,
    machine_id: str,
    job_id: str,
    task_id: str,
) -> None:
    """Remember that ``machine_id`` claimed ``lease_id`` for a task.

    This is the mapping the credit path needs and cannot otherwise get: the
    completion hop carries only a lease id, while ``contributions`` is keyed
    on ``(machine_id, job_id, task_id)``.

    ``on conflict do nothing`` because a claim that is forwarded twice — a
    retry, a duplicated request — describes one lease, not two.
    """
    with db.cursor() as cur:
        cur.execute(
            "insert into public.attempts"
            "            (lease_id, machine_id, job_id, task_id)"
            "     values (%s, %s, %s, %s)"
            " on conflict (lease_id) do nothing",
            (lease_id, machine_id, job_id, task_id),
        )


def claim_attempt_credit(
    db: psycopg.Connection,
    *,
    lease_id: str,
    machine_id: str,
) -> dict[str, Any] | None:
    """Take the right to credit ``lease_id``, exactly once.

    Returns ``{"job_id", "task_id", "duration_s"}`` for a lease this machine
    claimed and has not yet been credited for, or ``None``. ``None`` covers
    every "do not pay" case at once: unknown lease, already credited, or a
    different machine asking.

    One ``UPDATE ... RETURNING`` rather than a select then an update — two
    completions arriving together must not both come back with a row, and a
    single statement makes that the database's problem rather than this
    process's.

    ``duration_s`` is lease-held wall clock (claim to credit), which includes
    input download and output upload. That is the honest number for a
    contribution ledger. It is cast to ``float`` because ``extract(epoch …)``
    is ``numeric`` and psycopg returns ``Decimal``, which would otherwise
    reach ``record_contributions`` and land in the column as a different type
    from every row the federated path writes.
    """
    with db.cursor() as cur:
        cur.execute(
            "update public.attempts"
            "   set accepted_at = now()"
            " where lease_id = %s and machine_id = %s and accepted_at is null"
            " returning job_id, task_id,"
            "           extract(epoch from (now() - claimed_at)) as duration_s",
            (lease_id, machine_id),
        )
        row = cur.fetchone()
    if row is None:
        return None
    return {
        "job_id": row["job_id"],
        "task_id": row["task_id"],
        "duration_s": float(row["duration_s"]),
    }
```

Check the imports at the top of `db.py` already include `Any` from `typing`;
add it to the existing import if not.

- [ ] **Step 4: Run them and watch them pass**

```bash
cd flashml-cloud/apps/api && .venv/bin/python -m pytest tests/test_contributions.py -q
```
Expected: PASS, 5 new tests.

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/db.py \
        flashml-cloud/apps/api/tests/test_contributions.py
git commit -m "feat(api): record_attempt + claim_attempt_credit, once-only by UPDATE"
```

---

### Task 3: Record the attempt on a successful claim

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py` (the `claim` route, ~line 1276)
- Test: `flashml-cloud/apps/api/tests/test_agent_proxy.py`

**Interfaces:**
- Consumes: `db.record_attempt` (Task 2); `RecordingTransport`, whose
  `payload` and `status_code` are settable per test.
- Produces: an `attempts` row for every claim that returns a lease.

The claim route currently returns `await proxy(...)` directly. It must
inspect the response first. `proxy` returns a Starlette `Response`, whose
bytes are on `.body`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_proxy.py`.

**Use the file's real fixtures.** The agent fixture is `machine` — a
**module-scoped dict** `{"owner", "id", "token", "node_id"}` (line 163), not
separate `machine_token`/`machine_id` fixtures. It is *shared by every test
in the module*, so never assert on a whole-table count: scope every
assertion to this test's own `lease_id` or `job_id`.

```python
def _attempt_rows(db, lease_id: str) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "select machine_id, job_id, task_id, accepted_at"
            "  from public.attempts where lease_id = %s",
            (lease_id,),
        )
        return list(cur.fetchall())


def _lease_payload(lease_id: str, job_id: str, task_id: str = "task-000") -> dict:
    return {
        "schema_version": "v1alpha1",
        "lease_id": lease_id,
        "task_id": task_id,
        "job_id": job_id,
        "node_id": "whatever-the-agent-says",
        "attempt_number": 1,
        "deadline": "2026-08-02T00:00:00Z",
        "payload": {},
    }


def test_claim_records_the_attempt(client, transport, machine, db):
    lease_id = f"lease-{uuid.uuid4().hex[:12]}"
    transport.status_code = 200
    transport.payload = _lease_payload(lease_id, "cjob-abc123", "task-007")

    r = client.post(
        "/v1alpha1/leases/claim",
        json={},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    assert r.status_code == 200

    rows = _attempt_rows(db, lease_id)
    assert len(rows) == 1
    assert rows[0]["job_id"] == "cjob-abc123"
    assert rows[0]["task_id"] == "task-007"
    assert str(rows[0]["machine_id"]) == machine["id"]
    assert rows[0]["accepted_at"] is None


def test_claim_204_records_nothing(client, transport, machine, db):
    """204 is "no work right now". There is no attempt to remember.

    Asserted as a DELTA over this machine's rows, not an absolute count.
    `machine` is module-scoped and shared with every other test in this
    file, so an absolute count is just whatever the tests that happened to
    run first left behind — and an earlier draft of this test asserted
    `job_id = ''` against a `not null` column, which could never fail and
    guarded nothing.
    """
    def _count() -> int:
        with db.cursor() as cur:
            cur.execute(
                "select count(*) as n from public.attempts where machine_id = %s",
                (machine["id"],),
            )
            return cur.fetchone()["n"]

    before = _count()
    transport.status_code = 204
    transport.payload = None

    r = client.post(
        "/v1alpha1/leases/claim",
        json={},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    assert r.status_code == 204
    assert _count() == before


def test_claim_with_unparseable_body_does_not_fail_the_claim(
    client, transport, machine
):
    """The ledger is never allowed to break scheduling.

    A 200 whose body is not a lease (a coordinator change, a proxy in the
    middle) must cost the agent nothing — it still gets its response.
    """
    transport.status_code = 200
    transport.payload = {"not": "a lease"}

    r = client.post(
        "/v1alpha1/leases/claim",
        json={},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    assert r.status_code == 200
```

- [ ] **Step 2: Run them and watch them fail**

```bash
cd flashml-cloud/apps/api && .venv/bin/python -m pytest tests/test_agent_proxy.py -q -k "claim"
```
Expected: FAIL on `test_claim_records_the_attempt` — 0 rows.

- [ ] **Step 3: Wire the claim route**

Replace the `claim` route in `flashml_cloud_api/app.py`:

```python
    @app.post("/v1alpha1/leases/claim", tags=["agent"])
    async def claim(request: Request, machine: Machine = Depends(current_machine)):
        response = await proxy(
            request, machine, "/v1alpha1/leases/claim", force_node_id=True
        )
        # Remember what this machine was given. The completion hop reports
        # only `{"accepted": bool}` against a lease id, so this is the single
        # point where the API can learn which job and task a lease covers —
        # and without that mapping no non-federated job can ever credit
        # anybody. 204 ("nothing claimable") carries no lease and is skipped.
        #
        # Best-effort, like last_seen_at: an accounting row must never be the
        # reason a machine fails to pick up work.
        if response.status_code == 200:
            try:
                lease = json.loads(response.body)
                with contextlib.closing(app.state.connect()) as conn:
                    dbmod.record_attempt(
                        conn,
                        lease_id=lease["lease_id"],
                        machine_id=machine.id,
                        job_id=lease["job_id"],
                        task_id=lease["task_id"],
                    )
            except Exception:
                log.warning(
                    "could not record attempt for machine %s", machine.id
                )
        return response
```

`json`, `contextlib`, `dbmod` and `log` are all already imported in this
module — verify rather than re-adding.

- [ ] **Step 4: Run them and watch them pass**

```bash
cd flashml-cloud/apps/api && .venv/bin/python -m pytest tests/test_agent_proxy.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit the TEST FILE ONLY**

`app.py` carries the user's uncommitted events route — see the global
constraints. Stage the test, leave the route change in the working tree.

```bash
git add flashml-cloud/apps/api/tests/test_agent_proxy.py
git commit -m "test(api): pin that a successful claim records an attempt row"
```

---

### Task 4: Credit on accepted completion

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py` (the `attempt_complete` route, ~line 1290)
- Test: `flashml-cloud/apps/api/tests/test_agent_proxy.py`

**Interfaces:**
- Consumes: `db.claim_attempt_credit` (Task 2), `db.record_contributions`
  (existing), and the attempts row written in Task 3.
- Produces: a `contributions` row per accepted attempt on any job.

**THE CENTRAL CORRECTNESS RULE OF THIS TASK.** The coordinator answers
**HTTP 200 with `{"accepted": false}`** when output-hash validation failed
and when the commit arrived late and another attempt already won
(`flashruntime/service/modea.py:674-707`). Crediting on `2xx` pays for work
that failed its hash check. Read the **body** field.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_proxy.py`:

```python
def _contribution_rows(db, job_id: str) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            "select machine_id, task_id, duration_s"
            "  from public.contributions where job_id = %s",
            (job_id,),
        )
        return list(cur.fetchall())


def _claim_one(client, transport, machine, *, lease_id, job_id):
    """Drive a real claim through the proxy so the attempts row exists."""
    transport.status_code = 200
    transport.payload = _lease_payload(lease_id, job_id)
    r = client.post(
        "/v1alpha1/leases/claim",
        json={},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )
    assert r.status_code == 200


def _complete(client, transport, machine, *, lease_id, body, status=200):
    transport.status_code = status
    transport.payload = body
    return client.post(
        f"/v1alpha1/attempts/{lease_id}/complete",
        json={"output_sha256": "0" * 64},
        headers={"Authorization": f"Bearer {machine['token']}"},
    )


def test_accepted_completion_credits_the_machine(client, transport, machine, db):
    lease_id = f"lease-{uuid.uuid4().hex[:12]}"
    job_id = f"cjob-{uuid.uuid4().hex[:10]}"
    _claim_one(client, transport, machine, lease_id=lease_id, job_id=job_id)

    _complete(client, transport, machine,
              lease_id=lease_id, body={"accepted": True})

    rows = _contribution_rows(db, job_id)
    assert len(rows) == 1
    assert str(rows[0]["machine_id"]) == machine["id"]
    assert rows[0]["task_id"] == "task-000"


def test_rejected_completion_credits_nobody(client, transport, machine, db):
    """200 + accepted:false. Output validation failed; the task requeues.

    This is the case that makes "credit on 2xx" wrong: the status code says
    the hop succeeded, the body says the WORK did not.
    """
    lease_id = f"lease-{uuid.uuid4().hex[:12]}"
    job_id = f"cjob-{uuid.uuid4().hex[:10]}"
    _claim_one(client, transport, machine, lease_id=lease_id, job_id=job_id)

    _complete(
        client, transport, machine,
        lease_id=lease_id,
        body={"accepted": False, "detail": "output validation failed; attempt requeued"},
    )

    assert _contribution_rows(db, job_id) == []


def test_late_commit_credits_nobody(client, transport, machine, db):
    """200 + bare accepted:false — another attempt already won this task."""
    lease_id = f"lease-{uuid.uuid4().hex[:12]}"
    job_id = f"cjob-{uuid.uuid4().hex[:10]}"
    _claim_one(client, transport, machine, lease_id=lease_id, job_id=job_id)

    _complete(client, transport, machine,
              lease_id=lease_id, body={"accepted": False})

    assert _contribution_rows(db, job_id) == []


def test_error_completion_credits_nobody(client, transport, machine, db):
    lease_id = f"lease-{uuid.uuid4().hex[:12]}"
    job_id = f"cjob-{uuid.uuid4().hex[:10]}"
    _claim_one(client, transport, machine, lease_id=lease_id, job_id=job_id)

    _complete(client, transport, machine, lease_id=lease_id,
              body={"detail": "unknown lease"}, status=404)

    assert _contribution_rows(db, job_id) == []


def test_completing_twice_credits_once(client, transport, machine, db):
    lease_id = f"lease-{uuid.uuid4().hex[:12]}"
    job_id = f"cjob-{uuid.uuid4().hex[:10]}"
    _claim_one(client, transport, machine, lease_id=lease_id, job_id=job_id)

    for _ in range(2):
        _complete(client, transport, machine,
                  lease_id=lease_id, body={"accepted": True})

    assert len(_contribution_rows(db, job_id)) == 1


def test_completion_without_a_claim_credits_nobody(client, transport, machine, db):
    """No attempts row => nothing is known about this lease => no credit."""
    lease_id = f"lease-{uuid.uuid4().hex[:12]}"
    r = _complete(client, transport, machine,
                  lease_id=lease_id, body={"accepted": True})
    assert r.status_code == 200
    with db.cursor() as cur:
        cur.execute(
            "select count(*) as n from public.attempts where lease_id = %s",
            (lease_id,),
        )
        assert cur.fetchone()["n"] == 0
```

- [ ] **Step 2: Run them and watch them fail**

```bash
cd flashml-cloud/apps/api && .venv/bin/python -m pytest tests/test_agent_proxy.py -q -k "credit or complet"
```
Expected: FAIL on `test_accepted_completion_credits_the_machine` — 0 rows.

- [ ] **Step 3: Wire the complete route**

Replace the `attempt_complete` route in `flashml_cloud_api/app.py`:

```python
    @app.post("/v1alpha1/attempts/{lease_id}/complete", tags=["agent"])
    async def attempt_complete(
        lease_id: str, request: Request, machine: Machine = Depends(current_machine)
    ):
        response = await proxy(
            request, machine, f"/v1alpha1/attempts/{_seg(lease_id)}/complete"
        )
        # ACCEPTANCE IS THE BODY FIELD, NOT THE STATUS CODE.
        #
        # The coordinator answers 200 with `{"accepted": false}` in two
        # ordinary cases: the output's sha256 did not match what was
        # committed (the attempt is requeued elsewhere), and the commit
        # arrived after another attempt had already won the task. Both are
        # successful HTTP hops reporting unsuccessful WORK. Crediting on
        # `2xx` would pay for a failed hash check — hard rule 4, attempted
        # work is not accepted work.
        if response.status_code == 200:
            try:
                accepted = json.loads(response.body).get("accepted") is True
            except Exception:
                accepted = False
            if accepted:
                try:
                    with contextlib.closing(app.state.connect()) as conn:
                        credit = dbmod.claim_attempt_credit(
                            conn, lease_id=lease_id, machine_id=machine.id
                        )
                        if credit is not None:
                            dbmod.record_contributions(
                                conn,
                                job_id=credit["job_id"],
                                entries=[{
                                    "node_id": machine.node_id,
                                    "task_id": credit["task_id"],
                                    "duration_s": credit["duration_s"],
                                }],
                            )
                except Exception:
                    log.warning(
                        "could not credit accepted attempt for machine %s",
                        machine.id,
                    )
        return response
```

`claim_attempt_credit` returning `None` is the ordinary path for a repeat
completion or a lease this machine never claimed — not an error, and not
logged as one.

- [ ] **Step 4: Run them and watch them pass**

```bash
cd flashml-cloud/apps/api && .venv/bin/python -m pytest tests/test_agent_proxy.py -q
```
Expected: PASS.

- [ ] **Step 5: Commit the TEST FILE ONLY**

Same reason as Task 3. Report to the user that `app.py` is left staged-free
in the working tree with two route changes in it.

```bash
git add flashml-cloud/apps/api/tests/test_agent_proxy.py
git commit -m "test(api): pin that only accepted work is credited"
```

---

### Task 5: Prove federated rounds are not double-credited

**Files:**
- Test only: `flashml-cloud/apps/api/tests/test_contributions.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: nothing. This task exists to pin an invariant.

A federated run is one coordinator job **per round**. `fedavg` credits with
`job_id = the round's coordinator job id`. The lease for that round's task
carries **the same** `job_id`. So both paths compute an identical
`(machine_id, job_id, task_id)` key and the unique index from 0003 collapses
them to one row.

If that ever stops being true, every federated host is paid twice and the
error compounds per round with nothing to notice it. Pin it.

- [ ] **Step 1: Write the test**

```python
def test_federated_round_credited_by_both_paths_yields_one_row(db):
    """fedavg and the completion proxy must agree on the ledger key.

    Both write (machine_id, job_id, task_id) where job_id is the ROUND's
    coordinator job. Identical keys => the 0003 unique index collapses them.
    A drift here pays federated hosts twice, once per round, silently.
    """
    node_id = _node_id("fedboth")
    machine = _enrol(db, node_id)
    round_job = _job()          # the round's coordinator job id
    lease = _lease()

    # path 1: the completion proxy's credit, via the attempts mapping
    dbmod.record_attempt(
        db, lease_id=lease, machine_id=machine,
        job_id=round_job, task_id="task-000",
    )
    credit = dbmod.claim_attempt_credit(db, lease_id=lease, machine_id=machine)
    dbmod.record_contributions(
        db, job_id=credit["job_id"],
        entries=[{"node_id": node_id, "task_id": credit["task_id"],
                  "duration_s": credit["duration_s"]}],
    )

    # path 2: fedavg's on_round credit for the same accepted task
    dbmod.record_contributions(
        db, job_id=round_job,
        entries=[{"node_id": node_id, "task_id": "task-000", "duration_s": 1.0}],
    )

    assert len(_rows(db, round_job)) == 1
```

- [ ] **Step 2: Run it**

```bash
cd flashml-cloud/apps/api && .venv/bin/python -m pytest tests/test_contributions.py -q -k federated_round_credited_by_both
```
Expected: PASS immediately — this pins existing behaviour rather than
driving new code. **If it fails, stop and report:** it means the two paths
disagree on the ledger key, which is a real defect in Task 4, not a bad test.

- [ ] **Step 3: Commit**

```bash
git add flashml-cloud/apps/api/tests/test_contributions.py
git commit -m "test(api): pin that fedavg and the completion proxy share one ledger key"
```

---

### Task 6: Full verification and status update

**Files:**
- Modify: `flashml-cloud/PROGRESS.md`
- Modify: `flashml-cloud/docs/superpowers/specs/POSITIONING_LOG.md`

- [ ] **Step 1: Run every suite**

```bash
cd flashml-cloud/apps/api && .venv/bin/python -m pytest -q
cd ../../../e2e && .venv/bin/python -m pytest -q
```

Expected: apps/api **403 + 16 new = 419 passed**; e2e **61 passed**,
unchanged. `flashruntime` (573) and `flashnode` (214) are untouched by this
plan — if either moves, something is wrong.

**On the e2e PATH artefact:** `.venv/bin/python -m pytest` does not put the
venv's `bin` on `PATH`, and at least one e2e test spawns a bare `python`.
If you see an unexpected e2e failure, check that before assuming a
regression.

- [ ] **Step 2: Report the numbers honestly**

State the actual counts. If a suite moved in a way this plan does not
predict, say so rather than reconciling it.

- [ ] **Step 3: Append to PROGRESS.md**

Follow the LOGGING PROTOCOL already in that file. Record: the attempt
ledger, that non-federated jobs now credit, and the two things still open
from the spec's §4 — failed/expired attempts leave no record, and there is
still no result verification.

- [ ] **Step 4: Update the POSITIONING_LOG open threads**

Thread 1 (contributions ledger) can now say it covers every job, not just
federated rounds. **Append, never rewrite** — that file is a dated trail.

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/PROGRESS.md \
        flashml-cloud/docs/superpowers/specs/POSITIONING_LOG.md
git commit -m "docs: attempt ledger shipped; every job now credits accepted work"
```

---

## Human gates

🔒 **Migration 0004 must be applied to Supabase by hand** before this is
deployed. There is no migration runner in this service; 0003 was applied the
same way. The API will otherwise log a warning on every claim and credit
nobody — best-effort by design, so nothing else breaks.

🔒 **Do not deploy** until the user has confirmed. The console work in the
parallel session is unrelated but shares the repo.
