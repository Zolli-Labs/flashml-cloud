# On-Demand Rented Capacity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the control plane rent a GPU on demand for a user's job, bind it to that user's pool, run the work, and destroy it — then show the buyer the time-versus-money frontier that renting buys.

**Architecture:** A `ResourceProvider` protocol with one concrete implementation, driven by a reconciler rather than the submit path. Acquisition mints an ephemeral machine credential into the submitter's own pool (reusing `sandbox_identity.provision_sandbox_machine`), enrols a `flashnode work --runner trusted` agent, and records every step before attempting it. Teardown is guaranteed by a sweep, not by the request that started it.

**Tech Stack:** Python 3.10+, FastAPI, psycopg (Postgres), pytest. Next.js + TypeScript + Tailwind for the console surface.

**Spec:** `flashml-cloud/docs/superpowers/specs/2026-08-12-on-demand-capacity-design.md`

## Global Constraints

- **Runner is `--runner trusted`, never `--runner argv`.** Rented hosts are containers and cannot run Docker (`trusted_runner.py` opening paragraph).
- **Never set `FLASHNODE_SANDBOX_CAPABLE=true` on a rented host.** It would make the node accept public work it cannot isolate — a lie to the placement gate at `flashruntime/scheduler/__init__.py:620`.
- **Never rewrite `placement.pool` on a submitted job.** A job with no pool is public; renting cannot help it and the planner must say so. See spec D2.
- **One job per instance, never reused across submitters. Destroy, never stop.** Spec D3.
- **No flashruntime change.** Nothing in this plan may require a PyPI release or a four-site pin bump.
- **Do not touch wallet, `credit_requests`, ZC grants, or `Cost` reduction.** Another agent owns those (spec §4.1). If a task appears to need them, stop and report.
- **1 ZC = $1 USD** where a comparison is unavoidable (spec §4.1), but prefer showing both units.
- **Migrations:** this feature's migration shipped as **`0022_rented_capacity.sql`**. `0020` and `0021` belong to another agent. The next free number is **0023** — check `ls migrations/` before claiming one.
- **Tests:** `cd flashml-cloud/apps/api && .venv/bin/pytest tests/<your file> -q`. **Run only your own test file, not the whole suite** — another agent has uncommitted work in this tree and its failures are not yours to see or fix. `conftest.py` spins a real Postgres per session, applies real migrations, and **never truncates between files**, so every test must clean up the rows it creates in a `finally`.
- **Renamed after the plan was written:** `router/frontier.py` → **`router/tradeoff.py`**, `frontier()` → **`tradeoff_curve()`**, `FrontierPoint` → **`TradeoffPoint`**. This was not cosmetic: `router/__init__.py:116` already re-exports a *different* `plan.frontier` function, which shadows a module of that name and makes it unreachable through the package. Tasks 8 and 9 below still use the old names in places — **the code is right, this document is stale.**
- **Every acquisition step records its event before attempting the thing it describes**, so a restart finds evidence rather than an orphan.

---

## Phase 0 — Verification (blocks Phase 2 only)

### Task 1: Answer the three venue questions

**Suggested agent: sonnet** (research + API probing; no repo surgery)

**Files:**
- Create: `flashml-cloud/docs/superpowers/specs/2026-08-12-venue-capability-findings.md`

**Interfaces:**
- Produces: a venue choice consumed by Task 7. Until this lands, Task 7 cannot start.

- [ ] **Step 1: Answer whether an FC GPU function can hold a polling process**

`flashnode work` is a long-lived loop. FC is invocation-driven. Determine from Alibaba's documentation and, if possible, the account already configured on dev: maximum execution duration for a GPU function, whether a process may outlive a request, and whether elastic instances are reclaimed mid-run.

Record the answer with a citation. "Probably" is not an answer; if it cannot be established, say so and mark FC GPU unusable for this phase.

- [ ] **Step 2: Answer whether the instance metadata endpoint can be blocked**

Spec D3 requirement 3. For each candidate venue (FC GPU, Alibaba ECS GPU, RunPod), determine whether task code running unsandboxed can reach the cloud metadata service, and whether it can be blocked or the instance given a zero-permission role. **A venue where it cannot is disqualified.**

- [ ] **Step 3: Answer which venue can be created and destroyed through an already-authenticated API**

For each candidate: the exact create and destroy calls, whether this repo already holds working credentials for them, and the per-hour price. RunPod's MCP tooling and the Alibaba OpenAPI tooling are both available in this environment.

- [ ] **Step 4: Record the enrolment style each venue supports**

Per spec §2.2: **push** (an exec/write-file channel exists — `bootstrap_worker` is reusable behind an adapter) or **pull** (boot with a start command that fetches a bootstrap over HTTP — the proven RunPod recipe). Name which each candidate supports.

- [ ] **Step 5: Recommend one venue for the first provider, with the runner-up**

State the choice in one sentence, with the disqualifying fact for each rejected venue.

- [ ] **Step 6: Commit**

```bash
git add flashml-cloud/docs/superpowers/specs/2026-08-12-venue-capability-findings.md
git commit -m "docs: venue capability findings — which venue the first ResourceProvider targets"
```

---

## Phase 1 — Venue-agnostic capacity core

Buildable now against a fake provider. Nothing here depends on Task 1.

### Task 2: The acquisition ledger

**Suggested agent: haiku** (mechanical SQL + a schema assertion)

**Files:**
- Create: `flashml-cloud/apps/api/migrations/0022_rented_capacity.sql`
- Test: `flashml-cloud/apps/api/tests/test_rented_capacity_schema.py`

**Interfaces:**
- Produces: table `rented_capacity`, consumed by Tasks 4, 5 and 6.

- [ ] **Step 1: Write the failing test**

```python
"""The acquisition ledger's shape. A rented machine costs money from the
moment it exists, so every column that teardown depends on is NOT NULL."""
from __future__ import annotations


def _columns(db, table):
    with db.cursor() as cur:
        cur.execute(
            """
            select column_name, is_nullable
              from information_schema.columns
             where table_schema = 'public' and table_name = %s
            """,
            (table,),
        )
        return {r["column_name"]: r["is_nullable"] for r in cur.fetchall()}


def test_rented_capacity_carries_what_teardown_needs(migrated_db):
    cols = _columns(migrated_db, "rented_capacity")
    # The provider handle is how we destroy it. Without it we are billing
    # for something we cannot name.
    assert cols["venue_id"] == "NO"
    assert cols["state"] == "NO"
    assert cols["owner_id"] == "NO"
    assert cols["pool_id"] == "NO"
    # Nullable: the handle does not exist until the venue answers, which is
    # exactly the window the reconciler exists to close.
    assert cols["provider_handle"] == "YES"
    assert cols["machine_id"] == "YES"
    assert cols["released_at"] == "YES"


def test_state_is_constrained(migrated_db):
    with migrated_db.cursor() as cur:
        cur.execute(
            """
            select 1 from information_schema.check_constraints
             where constraint_name = 'rented_capacity_state_check'
            """
        )
        assert cur.fetchone() is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_rented_capacity_schema.py -v`
Expected: FAIL — relation "rented_capacity" does not exist.

If the fixture name `migrated_db` does not exist, read `tests/conftest.py` and use the real fixture that yields a migrated connection. Do not invent one.

- [ ] **Step 3: Write the migration**

```sql
-- 0022_rented_capacity.sql
--
-- One row per machine this control plane rented. The row is opened BEFORE
-- the venue is asked for anything, so a crash between "we decided to spend
-- money" and "the venue answered" leaves evidence rather than an orphan
-- that bills forever.
--
-- REQUESTED -> ACTIVE -> RELEASED, or -> FAILED from anywhere.
-- `provider_handle` is null only in REQUESTED; the reconciler's job is to
-- make that window short.

create table if not exists public.rented_capacity (
    id                uuid primary key default gen_random_uuid(),
    venue_id          text        not null,
    state             text        not null default 'REQUESTED',
    owner_id          uuid        not null,
    pool_id           uuid        not null,
    job_id            text        not null,
    provider_handle   text,
    machine_id        uuid,
    gpu_count         integer     not null default 1,
    usd_per_hour      numeric(10, 4),
    created_at        timestamptz not null default now(),
    acquired_at       timestamptz,
    released_at       timestamptz,
    failure_code      text,
    failure_detail    text,
    constraint rented_capacity_state_check
        check (state in ('REQUESTED', 'ACTIVE', 'RELEASED', 'FAILED'))
);

-- The reconciler's query: everything still costing money.
create index if not exists rented_capacity_unreleased_idx
    on public.rented_capacity (state)
 where state in ('REQUESTED', 'ACTIVE');

create index if not exists rented_capacity_owner_idx
    on public.rented_capacity (owner_id, created_at desc);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_rented_capacity_schema.py -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite to prove the migration did not break anything**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest -q`
Expected: no new failures.

- [ ] **Step 6: Commit**

```bash
git add flashml-cloud/apps/api/migrations/0022_rented_capacity.sql flashml-cloud/apps/api/tests/test_rented_capacity_schema.py
git commit -m "feat(capacity): acquisition ledger for rented machines"
```

---

### Task 3: The provider protocol and a fake

**Suggested agent: sonnet**

**Files:**
- Create: `flashml-cloud/apps/api/flashml_cloud_api/capacity/__init__.py`
- Create: `flashml-cloud/apps/api/flashml_cloud_api/capacity/provider.py`
- Test: `flashml-cloud/apps/api/tests/test_capacity_provider.py`

**Interfaces:**
- Produces: `CapacityRequest`, `AcquiredMachine`, `ProviderState`, `ReleaseOutcome`, `ResourceProvider`, `FakeProvider`. Consumed by Tasks 4, 5, 6, 7.

- [ ] **Step 1: Write the failing test**

```python
"""The provider contract, and the fake every other test leans on."""
from __future__ import annotations

import pytest

from flashml_cloud_api.capacity.provider import (
    AcquiredMachine,
    CapacityRequest,
    FakeProvider,
    ResourceProvider,
)


def _request(**over):
    base = dict(
        venue_id="fake", owner_id="o1", pool_id="p1", job_id="j1",
        gpu_count=1, min_vram_gb=24.0, coordinator_url="http://c",
        quoted_usd_per_hour=0.5,
    )
    base.update(over)
    return CapacityRequest(**base)


def test_fake_satisfies_the_protocol():
    assert isinstance(FakeProvider(), ResourceProvider)


@pytest.mark.asyncio
async def test_acquire_returns_a_handle_and_release_is_idempotent():
    p = FakeProvider()
    got = await p.acquire(request=_request())
    assert isinstance(got, AcquiredMachine)
    assert got.provider_handle
    first = await p.release(handle=got.provider_handle)
    second = await p.release(handle=got.provider_handle)
    assert first.destroyed is True
    # Releasing something already gone is success, not an error: the
    # reconciler will call this again and must not raise on a clean sweep.
    assert second.destroyed is True


@pytest.mark.asyncio
async def test_release_of_an_unknown_handle_is_not_an_error():
    p = FakeProvider()
    assert (await p.release(handle="never-existed")).destroyed is True


@pytest.mark.asyncio
async def test_a_failing_acquire_leaves_nothing_behind():
    """Any failure destroys what it created before raising — a half-created
    machine bills exactly like a whole one."""
    p = FakeProvider(fail_after_create=True)
    with pytest.raises(RuntimeError):
        await p.acquire(request=_request())
    assert p.live_handles() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_capacity_provider.py -v`
Expected: FAIL — `ModuleNotFoundError: flashml_cloud_api.capacity`

- [ ] **Step 3: Write the implementation**

```python
"""What a place capacity comes from must be able to do.

Three methods, because teardown is the expensive half and it needs two of
them: `release` to destroy, and `observe` to find what we lost track of.
`observe` reads the VENUE, never our own rows — a reconciler that trusted
our rows could not by construction find an orphan, which is the one thing
it exists for.

Enrolment style is deliberately NOT in this interface. A push-style venue
(an exec channel exists) reuses `sandbox_bootstrap.bootstrap_worker`; a
pull-style venue boots with a start command that self-enrols. Both end at
the same observable state -- a registered node claiming leases in the right
pool -- and `acquire` returns only once that is true.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

__all__ = [
    "AcquiredMachine",
    "CapacityRequest",
    "FakeProvider",
    "ProviderState",
    "ReleaseOutcome",
    "ResourceProvider",
]


@dataclass(frozen=True)
class CapacityRequest:
    venue_id: str
    owner_id: str
    #: The pool the job was submitted against. Never invented, never
    #: defaulted -- a job with no pool is public and cannot use rented
    #: capacity at all (spec D2).
    pool_id: str
    job_id: str
    gpu_count: int
    min_vram_gb: float
    coordinator_url: str
    #: What this is expected to cost, read from the venue's price board
    #: BEFORE anything is created. The budget gate needs a number before
    #: the venue has been asked for anything, so the quote travels with the
    #: request rather than coming back with the machine. `None` is refused
    #: by the gate, deliberately: a venue that will not quote is a venue
    #: whose spend cannot be bounded.
    quoted_usd_per_hour: float | None = None


@dataclass(frozen=True)
class AcquiredMachine:
    provider_handle: str
    machine_id: str | None
    node_id: str | None
    usd_per_hour: float | None


@dataclass(frozen=True)
class ProviderState:
    exists: bool
    running: bool
    detail: str = ""


@dataclass(frozen=True)
class ReleaseOutcome:
    destroyed: bool
    detail: str = ""


@runtime_checkable
class ResourceProvider(Protocol):
    venue_id: str

    async def acquire(self, *, request: CapacityRequest) -> AcquiredMachine:
        ...

    async def release(self, *, handle: str) -> ReleaseOutcome:
        ...

    async def observe(self, *, handle: str) -> ProviderState:
        ...


@dataclass
class FakeProvider:
    """In-memory provider. The suite's stand-in for a venue that bills."""

    venue_id: str = "fake"
    fail_after_create: bool = False
    _live: set[str] = field(default_factory=set)

    def live_handles(self) -> list[str]:
        return sorted(self._live)

    async def acquire(self, *, request: CapacityRequest) -> AcquiredMachine:
        handle = f"fake-{uuid.uuid4().hex[:12]}"
        self._live.add(handle)
        if self.fail_after_create:
            # The contract: destroy what was created before raising.
            self._live.discard(handle)
            raise RuntimeError("injected failure after create")
        return AcquiredMachine(
            provider_handle=handle,
            machine_id=None,
            node_id=None,
            usd_per_hour=0.0,
        )

    async def release(self, *, handle: str) -> ReleaseOutcome:
        self._live.discard(handle)
        return ReleaseOutcome(destroyed=True)

    async def observe(self, *, handle: str) -> ProviderState:
        live = handle in self._live
        return ProviderState(exists=live, running=live)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_capacity_provider.py -v`
Expected: PASS

If `pytest.mark.asyncio` is unavailable, check how existing async tests in this suite are marked (e.g. `tests/test_alibaba_sandbox.py`) and follow that convention rather than adding a dependency.

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/capacity/ flashml-cloud/apps/api/tests/test_capacity_provider.py
git commit -m "feat(capacity): ResourceProvider protocol and in-memory fake"
```

---

### Task 4: The budget gate

**Suggested agent: sonnet**

**Files:**
- Create: `flashml-cloud/apps/api/flashml_cloud_api/capacity/budget.py`
- Test: `flashml-cloud/apps/api/tests/test_capacity_budget.py`

**Interfaces:**
- Consumes: `rented_capacity` (Task 2).
- Produces: `BudgetRefused` exception and `assert_within_budget(db, *, venue_id, usd_per_hour, settings)`. Consumed by Task 5.

- [ ] **Step 1: Write the failing test**

```python
"""The gate that stands between a bug and the account.

Refuses. Never queues -- a queue that drains when budget frees up is the
same unbounded spend with a delay."""
from __future__ import annotations

import pytest

from flashml_cloud_api.capacity.budget import BudgetRefused, assert_within_budget


class _Settings:
    rented_usd_per_acquisition_max = 2.0
    rented_usd_window_max = 10.0
    rented_usd_window_hours = 24.0


def test_a_single_expensive_acquisition_is_refused(migrated_db):
    with pytest.raises(BudgetRefused) as exc:
        assert_within_budget(
            migrated_db, venue_id="runpod", usd_per_hour=5.0,
            settings=_Settings(),
        )
    # The reason must name the number it exceeded. "Refused" alone sends
    # somebody to read source to find out why.
    assert "2.0" in str(exc.value)


def test_within_both_ceilings_is_allowed(migrated_db):
    assert_within_budget(
        migrated_db, venue_id="runpod", usd_per_hour=0.5, settings=_Settings(),
    )


def test_the_window_ceiling_counts_prior_acquisitions(migrated_db):
    with migrated_db.cursor() as cur:
        for _ in range(20):
            cur.execute(
                """
                insert into public.rented_capacity
                    (venue_id, state, owner_id, pool_id, job_id, usd_per_hour)
                values ('runpod', 'ACTIVE', gen_random_uuid(),
                        gen_random_uuid(), 'j', 1.0)
                """
            )
    migrated_db.commit()
    with pytest.raises(BudgetRefused) as exc:
        assert_within_budget(
            migrated_db, venue_id="runpod", usd_per_hour=0.5,
            settings=_Settings(),
        )
    assert "window" in str(exc.value).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_capacity_budget.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
"""Two ceilings, because they answer different questions.

A per-acquisition cap bounds ONE mistake. A rolling-window cap bounds a
LOOP of correct-looking decisions, which is what actually empties an
account. The window one is the load-bearing half.

Both refuse. Neither queues.
"""
from __future__ import annotations

from typing import Any

import psycopg

__all__ = ["BudgetRefused", "assert_within_budget", "window_spend_usd"]


class BudgetRefused(RuntimeError):
    """Acquisition would exceed a ceiling. Not retryable by waiting."""


def window_spend_usd(db: psycopg.Connection, *, hours: float) -> float:
    """Committed hourly rate across acquisitions opened in the window.

    Deliberately counts REQUESTED as well as ACTIVE and RELEASED: a row in
    REQUESTED may already have created something at the venue that we have
    not yet learned the handle for, and pretending it costs nothing is how
    a retry loop spends without ever being counted.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select coalesce(sum(usd_per_hour), 0)::float8 as total
              from public.rented_capacity
             where created_at > now() - make_interval(hours => %s)
            """,
            (float(hours),),
        )
        row = cur.fetchone()
    return float(row["total"] if row else 0.0)


def assert_within_budget(
    db: psycopg.Connection, *, venue_id: str, usd_per_hour: float | None,
    settings: Any,
) -> None:
    """Raise :class:`BudgetRefused` unless this acquisition fits both caps."""
    # An unpriced acquisition is refused rather than treated as free. A
    # venue that will not tell us the price is a venue we cannot bound.
    if usd_per_hour is None:
        raise BudgetRefused(
            f"venue {venue_id} quoted no price; an unpriced acquisition "
            "cannot be bounded and is refused"
        )

    per = float(settings.rented_usd_per_acquisition_max)
    if usd_per_hour > per:
        raise BudgetRefused(
            f"{venue_id} at ${usd_per_hour}/hr exceeds the per-acquisition "
            f"ceiling of ${per}/hr"
        )

    hours = float(settings.rented_usd_window_hours)
    cap = float(settings.rented_usd_window_max)
    already = window_spend_usd(db, hours=hours)
    if already + usd_per_hour > cap:
        raise BudgetRefused(
            f"the {hours}h window already commits ${already}/hr; adding "
            f"${usd_per_hour}/hr would exceed the window ceiling of ${cap}/hr"
        )
```

- [ ] **Step 4: Add the settings fields**

Modify `flashml-cloud/apps/api/flashml_cloud_api/settings.py`. Follow the file's existing pattern for env-var-backed fields with defaults; read a neighbouring field first and match it exactly.

```python
#: Per-acquisition ceiling. One mistake cannot exceed this.
rented_usd_per_acquisition_max: float = 2.0
#: Rolling-window ceiling across ALL acquisitions. This is the one that
#: bounds a loop of correct-looking decisions. The standing operational
#: ceiling on rented spend is $10 total.
rented_usd_window_max: float = 10.0
rented_usd_window_hours: float = 24.0
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_capacity_budget.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/capacity/budget.py flashml-cloud/apps/api/flashml_cloud_api/settings.py flashml-cloud/apps/api/tests/test_capacity_budget.py
git commit -m "feat(capacity): two-ceiling budget gate that refuses rather than queues"
```

---

### Task 5: The acquisition lifecycle

**Suggested agent: opus** (money, ordering discipline, failure paths)

**Files:**
- Create: `flashml-cloud/apps/api/flashml_cloud_api/capacity/acquire.py`
- Test: `flashml-cloud/apps/api/tests/test_capacity_acquire.py`

**Interfaces:**
- Consumes: `CapacityRequest`/`AcquiredMachine`/`ResourceProvider` (Task 3), `assert_within_budget`/`BudgetRefused` (Task 4), `rented_capacity` (Task 2), `sandbox_identity.provision_sandbox_machine`.
- Produces: `async acquire_for_job(db, provider, settings, *, request) -> str` returning the `rented_capacity.id`. Consumed by Task 6 and Task 7.

- [ ] **Step 1: Write the failing test**

```python
"""Acquisition: the row exists before the money does.

The ordering under test is the whole point. A crash between 'we decided to
spend' and 'the venue answered' must leave a REQUESTED row, because that
row is the only thing that will ever find the orphan."""
from __future__ import annotations

import pytest

from flashml_cloud_api.capacity.acquire import acquire_for_job
from flashml_cloud_api.capacity.budget import BudgetRefused
from flashml_cloud_api.capacity.provider import CapacityRequest, FakeProvider


class _Settings:
    rented_usd_per_acquisition_max = 2.0
    rented_usd_window_max = 10.0
    rented_usd_window_hours = 24.0
    coordinator_url = "http://coordinator"


def _request(owner_id, pool_id):
    return CapacityRequest(
        venue_id="fake", owner_id=str(owner_id), pool_id=str(pool_id),
        job_id="job-1", gpu_count=1, min_vram_gb=24.0,
        coordinator_url="http://coordinator", quoted_usd_per_hour=0.5,
    )


def _row(db, rid):
    with db.cursor() as cur:
        cur.execute("select * from public.rented_capacity where id = %s", (rid,))
        return cur.fetchone()


@pytest.mark.asyncio
async def test_a_successful_acquisition_lands_active_with_a_handle(
    migrated_db, an_owner, a_pool
):
    rid = await acquire_for_job(
        migrated_db, FakeProvider(), _Settings(),
        request=_request(an_owner, a_pool),
    )
    row = _row(migrated_db, rid)
    assert row["state"] == "ACTIVE"
    assert row["provider_handle"]
    assert row["acquired_at"] is not None


@pytest.mark.asyncio
async def test_a_refused_budget_creates_no_row_and_calls_no_provider(
    migrated_db, an_owner, a_pool
):
    """The gate runs BEFORE anything is created, so a refusal costs nothing
    and leaves nothing."""
    provider = FakeProvider()

    class _Tight(_Settings):
        rented_usd_per_acquisition_max = 0.0

    with pytest.raises(BudgetRefused):
        await acquire_for_job(
            migrated_db, provider, _Tight(),
            request=_request(an_owner, a_pool),
        )
    assert provider.live_handles() == []
    with migrated_db.cursor() as cur:
        cur.execute("select count(*)::int as n from public.rented_capacity")
        assert cur.fetchone()["n"] == 0


@pytest.mark.asyncio
async def test_a_provider_failure_records_FAILED_and_leaves_nothing_live(
    migrated_db, an_owner, a_pool
):
    provider = FakeProvider(fail_after_create=True)
    with pytest.raises(RuntimeError):
        await acquire_for_job(
            migrated_db, provider, _Settings(),
            request=_request(an_owner, a_pool),
        )
    assert provider.live_handles() == []
    with migrated_db.cursor() as cur:
        cur.execute("select state, failure_code from public.rented_capacity")
        row = cur.fetchone()
    assert row["state"] == "FAILED"
    assert row["failure_code"]
```

- [ ] **Step 2: Add the fixtures the test needs**

The test uses `an_owner` and `a_pool`. Read `tests/conftest.py` and the existing pool tests (`tests/test_db_pools.py`) to see how a pool and its owner are created in this suite, then add fixtures to `tests/conftest.py` that follow that exact pattern. Do not invent a schema — `provision_sandbox_machine` calls `lock_pool_for_owner`, which requires the owner to both **own** and be a **member** of the pool.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_capacity_acquire.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Write the implementation**

```python
"""Rent one machine for one job.

The order is the design, not an implementation detail:

1. **Budget gate first.** Before a row, before a credential, before the
   venue is asked anything. A refusal must cost nothing and leave nothing.
2. **Open the row.** From here every step has somewhere durable to be
   recorded and a restart has something to find.
3. **Mint the credential into the SUBMITTER'S pool.** Not an isolation
   pool -- this machine is meant to share a pool with the user's other
   machines. `assert_pool_isolated` is deliberately NOT called; it is an
   evaluation-session invariant and applying it here would forbid the
   thing this function exists to do.
4. **Acquire**, and record the handle in the same update that moves
   REQUESTED -> ACTIVE.

Any failure from step 2 onwards records FAILED and releases whatever the
provider created, because the row and its handle are the evidence of what
went wrong and the only route to the money still running.
"""
from __future__ import annotations

import asyncio

import psycopg

from flashml_cloud_api import sandbox_identity
from flashml_cloud_api.capacity.budget import assert_within_budget
from flashml_cloud_api.capacity.provider import CapacityRequest, ResourceProvider

__all__ = ["acquire_for_job"]


def _open_row(db: psycopg.Connection, request: CapacityRequest,
              usd_per_hour: float | None) -> str:
    with db.cursor() as cur:
        cur.execute(
            """
            insert into public.rented_capacity
                (venue_id, state, owner_id, pool_id, job_id, gpu_count,
                 usd_per_hour)
            values (%s, 'REQUESTED', %s, %s, %s, %s, %s)
            returning id
            """,
            (request.venue_id, request.owner_id, request.pool_id,
             request.job_id, request.gpu_count, usd_per_hour),
        )
        rid = str(cur.fetchone()["id"])
    db.commit()
    return rid


def _fail_row(db: psycopg.Connection, rid: str, code: str, detail: str) -> None:
    with db.cursor() as cur:
        cur.execute(
            """
            update public.rented_capacity
               set state = 'FAILED', failure_code = %s, failure_detail = %s
             where id = %s
            """,
            (code, detail[:2000], rid),
        )
    db.commit()


async def acquire_for_job(
    db: psycopg.Connection,
    provider: ResourceProvider,
    settings,
    *,
    request: CapacityRequest,
) -> str:
    """Rent one machine. Returns the ``rented_capacity`` row id."""
    # 1. Gate first. Raises BudgetRefused; nothing has been created.
    assert_within_budget(
        db, venue_id=request.venue_id,
        usd_per_hour=request.quoted_usd_per_hour, settings=settings,
    )

    # 2. The row, before the money.
    rid = _open_row(db, request, request.quoted_usd_per_hour)

    try:
        # 3. Identity, in the submitter's own pool.
        credential = await asyncio.to_thread(
            sandbox_identity.provision_sandbox_machine,
            db,
            owner_id=request.owner_id,
            pool_id=request.pool_id,
            node_id=f"rented-{rid[:12]}",
            label=f"rented {request.venue_id} for job {request.job_id}",
        )

        # 4. Acquire, then record the handle in the move out of REQUESTED.
        acquired = await provider.acquire(request=request)
        with db.cursor() as cur:
            cur.execute(
                """
                update public.rented_capacity
                   set state = 'ACTIVE', provider_handle = %s,
                       machine_id = %s, acquired_at = now(),
                       usd_per_hour = coalesce(%s, usd_per_hour)
                 where id = %s and state = 'REQUESTED'
                """,
                (acquired.provider_handle, credential.machine_id,
                 acquired.usd_per_hour, rid),
            )
            moved = cur.rowcount == 1
        db.commit()
        if not moved:
            raise RuntimeError(
                f"could not record handle {acquired.provider_handle} against "
                f"{rid}: the row is no longer REQUESTED"
            )
        return rid
    except BaseException as exc:
        _fail_row(db, rid, type(exc).__name__, str(exc))
        raise
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_capacity_acquire.py -v`
Expected: PASS

- [ ] **Step 6: Run the whole suite**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest -q`

- [ ] **Step 7: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/capacity/acquire.py flashml-cloud/apps/api/tests/test_capacity_acquire.py flashml-cloud/apps/api/tests/conftest.py
git commit -m "feat(capacity): acquisition lifecycle, budget-gated, row before money"
```

---

### Task 6: Release and the reconciler sweep

**Suggested agent: opus** (this is the guarantee; a bug here bills forever)

**Files:**
- Create: `flashml-cloud/apps/api/flashml_cloud_api/capacity/reconcile.py`
- Test: `flashml-cloud/apps/api/tests/test_capacity_reconcile.py`
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py` — add a loop beside the existing `_ephemeral_machine_loop` (around line 2228)

**Interfaces:**
- Consumes: `ResourceProvider` (Task 3), `rented_capacity` (Task 2).
- Produces: `async release_capacity(db, provider, *, rented_id)` and `async reconcile_rented(db, providers, *, settle_after_s)`.

- [ ] **Step 1: Write the failing test**

```python
"""Teardown is the guarantee. The request path is best effort."""
from __future__ import annotations

import pytest

from flashml_cloud_api.capacity.acquire import acquire_for_job
from flashml_cloud_api.capacity.provider import CapacityRequest, FakeProvider
from flashml_cloud_api.capacity.reconcile import (
    reconcile_rented,
    release_capacity,
)


class _Settings:
    rented_usd_per_acquisition_max = 2.0
    rented_usd_window_max = 10.0
    rented_usd_window_hours = 24.0


def _request(owner_id, pool_id, job="j1"):
    return CapacityRequest(
        venue_id="fake", owner_id=str(owner_id), pool_id=str(pool_id),
        job_id=job, gpu_count=1, min_vram_gb=24.0,
        coordinator_url="http://c", quoted_usd_per_hour=0.5,
    )


@pytest.mark.asyncio
async def test_release_destroys_and_marks_released(migrated_db, an_owner, a_pool):
    p = FakeProvider()
    rid = await acquire_for_job(
        migrated_db, p, _Settings(), request=_request(an_owner, a_pool)
    )
    await release_capacity(migrated_db, p, rented_id=rid)
    assert p.live_handles() == []
    with migrated_db.cursor() as cur:
        cur.execute(
            "select state, released_at from public.rented_capacity where id = %s",
            (rid,),
        )
        row = cur.fetchone()
    assert row["state"] == "RELEASED"
    assert row["released_at"] is not None


@pytest.mark.asyncio
async def test_release_is_idempotent(migrated_db, an_owner, a_pool):
    p = FakeProvider()
    rid = await acquire_for_job(
        migrated_db, p, _Settings(), request=_request(an_owner, a_pool)
    )
    await release_capacity(migrated_db, p, rented_id=rid)
    await release_capacity(migrated_db, p, rented_id=rid)  # must not raise


@pytest.mark.asyncio
async def test_the_sweep_releases_what_the_request_path_missed(
    migrated_db, an_owner, a_pool
):
    """The whole point: nobody called release, and the money still stops."""
    p = FakeProvider()
    rid = await acquire_for_job(
        migrated_db, p, _Settings(), request=_request(an_owner, a_pool)
    )
    # Age it past the settle window without touching the provider.
    with migrated_db.cursor() as cur:
        cur.execute(
            "update public.rented_capacity set acquired_at = now() - "
            "interval '3 hours' where id = %s",
            (rid,),
        )
    migrated_db.commit()

    touched = await reconcile_rented(
        migrated_db, {"fake": p}, settle_after_s=3600.0
    )
    assert rid in touched
    assert p.live_handles() == []


@pytest.mark.asyncio
async def test_the_sweep_leaves_fresh_rentals_alone(
    migrated_db, an_owner, a_pool
):
    p = FakeProvider()
    rid = await acquire_for_job(
        migrated_db, p, _Settings(), request=_request(an_owner, a_pool)
    )
    touched = await reconcile_rented(
        migrated_db, {"fake": p}, settle_after_s=3600.0
    )
    assert rid not in touched
    assert len(p.live_handles()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_capacity_reconcile.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
"""Destroying what we rented, whether or not anybody remembered to ask.

`cleanup_session` is the model: it kills the sandbox and revokes the
credential INDEPENDENTLY, so neither failure hides the other. The same
applies here -- a provider that will not answer must not stop the row from
being marked, and a row that will not update must not stop the destroy.

The sweep races money. `DEFAULT_RECONCILE_INTERVAL_S` says it in the
sandbox reconciler and it is just as true here: minutes, not hours.
"""
from __future__ import annotations

import psycopg

from flashml_cloud_api.capacity.provider import ResourceProvider

__all__ = ["release_capacity", "reconcile_rented", "unreleased_rows"]


def unreleased_rows(db: psycopg.Connection, *, settle_after_s: float) -> list[dict]:
    """Rows still costing money and old enough that nothing is mid-flight.

    REQUESTED is included on purpose: a row that never learned its handle
    may still have created something at the venue, and `observe` is the
    only way to find out.
    """
    with db.cursor() as cur:
        cur.execute(
            """
            select id, venue_id, state, provider_handle
              from public.rented_capacity
             where state in ('REQUESTED', 'ACTIVE')
               and coalesce(acquired_at, created_at)
                   < now() - make_interval(secs => %s)
             order by created_at
            """,
            (float(settle_after_s),),
        )
        return [dict(r) for r in cur.fetchall()]


async def release_capacity(
    db: psycopg.Connection, provider: ResourceProvider, *, rented_id: str
) -> bool:
    """Destroy one rental and mark it released. Idempotent."""
    with db.cursor() as cur:
        cur.execute(
            "select provider_handle, state from public.rented_capacity "
            "where id = %s",
            (rented_id,),
        )
        row = cur.fetchone()
    if row is None:
        return False
    if row["state"] == "RELEASED":
        return True

    destroyed = True
    handle = row["provider_handle"]
    if handle:
        # Independently of the row update below: a provider error here must
        # not prevent a second attempt, and must not be swallowed silently.
        outcome = await provider.release(handle=handle)
        destroyed = bool(outcome.destroyed)

    if destroyed:
        with db.cursor() as cur:
            cur.execute(
                """
                update public.rented_capacity
                   set state = 'RELEASED', released_at = now()
                 where id = %s and state <> 'RELEASED'
                """,
                (rented_id,),
            )
        db.commit()
    return destroyed


async def reconcile_rented(
    db: psycopg.Connection,
    providers: dict[str, ResourceProvider],
    *,
    settle_after_s: float,
) -> list[str]:
    """Sweep. Returns the ids it settled."""
    settled: list[str] = []
    for row in unreleased_rows(db, settle_after_s=settle_after_s):
        provider = providers.get(row["venue_id"])
        if provider is None:
            # A venue with no configured provider cannot be swept. Leave the
            # row: a stuck row is visible, a deleted one is not.
            continue
        if await release_capacity(db, provider, rented_id=str(row["id"])):
            settled.append(str(row["id"]))
    return settled
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_capacity_reconcile.py -v`
Expected: PASS

- [ ] **Step 5: Wire the loop into the app — DEFERRED, AND GATED**

> **STOP. This step is not ready and the text below is superseded in one
> respect: `settle_after_s` no longer exists.** The sweep now takes three
> named windows (`quiet_after_s`, `boot_grace_s`, `abandoned_after_s`), each
> with a safe default, so `reconcile_rented(conn, providers)` is correct with
> no arguments. Read `capacity/reconcile.py` — not this plan — for its API.
>
> **Three gates must close before this step runs. Each is a silent,
> money-losing failure if skipped:**
>
> 1. **There is no cost backstop for a healthy idle rental.** Age was the only
>    thing that ever stopped a booted machine, and removing it was correct —
>    but flashnode keeps heartbeating after its job ends, so a successful
>    rental matches the "quiet" branch with a fresh `last_seen_at` **for ever
>    and bills for ever**. `budget.window_spend_usd` does not help: it sums
>    rows *created* in the window — a ceiling on new commitments, not a stop
>    on running ones. The sweep is a **failure** backstop only. A **cost**
>    backstop must land with this step: either a settle path that calls
>    `release_capacity` when the job finishes, or an idle input (no lease
>    claimed for N minutes / job in a terminal state).
> 2. **The rented host must enrol at `sandbox_enrolment_url`, not
>    `settings.coordinator_url`.** `app.py` warns that `settings.coordinator_url`
>    is "right for a single-host dev run and wrong for every deployed one",
>    and a machine token means nothing to the coordinator. Reach for the wrong
>    one and rented hosts heartbeat past this API, leave `last_seen_at` null
>    for ever, and are destroyed at `boot_grace_s` — **60 minutes into a
>    healthy job, silently, on every rental.** Add a test asserting the rented
>    request's URL is the API's.
> 3. **`last_seen_at` is now load-bearing for money.** It has exactly one
>    production writer, in a `try/except` that logs and continues, over a
>    column documented as display-only. `test_agent_proxy.py` pins that the
>    route writes it, but for the console's reason, not this one. State the
>    money coupling at the write site and in that test's docstring.
>
> Consider shipping the first deployment **log-only** — the failure mode is
> silent and irreversible.

Read `app.py` around lines 2159–2260 — the existing `_reconcile_loop` and `_ephemeral_machine_loop`. Add a third loop following that exact shape (same logging style, same `<= 0 disables` convention, same startup-sweep-then-loop structure). Do not restructure the existing loops. The loop must catch, as `_ephemeral_machine_loop` does.

- [ ] **Step 6: Run the whole suite**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest -q`

- [ ] **Step 7: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/capacity/reconcile.py flashml-cloud/apps/api/tests/test_capacity_reconcile.py flashml-cloud/apps/api/flashml_cloud_api/app.py
git commit -m "feat(capacity): teardown guaranteed by a sweep, not by the request path"
```

---

## Phase 2 — The concrete provider

### Task 7: Implement the venue chosen in Task 1

**Suggested agent: opus** (external API, credentials, real money)

**BLOCKED until Task 1 lands.** Its findings name the venue and the enrolment style; writing this task's detail before that would be guessing.

**Files:**
- Create: `flashml-cloud/apps/api/flashml_cloud_api/capacity/providers/<venue>.py`
- Test: `flashml-cloud/apps/api/tests/test_capacity_provider_<venue>.py`

**Interfaces:**
- Consumes: `ResourceProvider`, `CapacityRequest`, `AcquiredMachine` (Task 3).
- Produces: a concrete provider registered in the `providers` dict Task 6's loop passes to `reconcile_rented`.

**Requirements this task must satisfy, from the spec:**

- [ ] Implements `acquire`/`release`/`observe` and passes an `isinstance(..., ResourceProvider)` assertion.
- [ ] `acquire` returns only once the node has **registered** — not once the API call returned.
- [ ] Any failure destroys what it created before raising (assert with an injected failure, mirroring Task 3's test).
- [ ] `release` is idempotent and treats an already-gone handle as success.
- [ ] `observe` reads the **venue**, never our own rows.
- [ ] The agent is launched with `--runner trusted`. Assert the launch command contains it and does **not** contain `FLASHNODE_SANDBOX_CAPABLE`.
- [ ] The instance metadata endpoint is blocked, or the instance carries a zero-permission role (spec D3.3). Assert whatever mechanism Task 1 found.
- [ ] All venue API calls are behind an injected client so the suite runs with no network and no credentials — follow `tests/test_alibaba_sandbox.py`'s fake-gateway pattern.
- [ ] **No live acquisition in the test suite.** A real rental is a manual verification step, run once, recorded in the findings doc.

---

## Phase 3 — The time-versus-money frontier

Independent of Phases 0–2. Can run in parallel from the start.

### Task 8: The frontier sweep

**Suggested agent: sonnet**

**Files:**
- Create: `flashml-cloud/apps/api/flashml_cloud_api/router/frontier.py`
- Test: `flashml-cloud/apps/api/tests/test_router_frontier.py`

**Interfaces:**
- Consumes: the existing plan machinery in `flashml_cloud_api/router/plan.py` and `estimator.py`. **Read both before writing anything** — `plan.py` already computes cheapest and fastest, and this task adds the points between, reusing its fill rather than reimplementing it.
- Produces: `frontier(...) -> list[FrontierPoint]` consumed by Task 9.

- [ ] **Step 1: Write the failing test**

```python
"""The curve between cheapest and fastest.

The honest core: speedup is bounded by TASK COUNT. A fill spreads N tasks
over M machines, so if N is 1 no fleet on earth makes it faster, and the
frontier must say so rather than sloping downward to sell capacity."""
from __future__ import annotations

from flashml_cloud_api.router.frontier import frontier


def test_a_single_task_job_gains_nothing_from_more_machines():
    points = frontier(task_count=1, task_seconds=600.0, owned_slots=1,
                      rentable_slots=8, usd_per_hour=1.0)
    assert len(points) >= 1
    # Every point finishes at the same time as the first.
    assert len({round(p.finish_seconds) for p in points}) == 1
    assert points[-1].advice_code == "no_parallelism"


def test_speedup_stops_at_the_task_count():
    points = frontier(task_count=4, task_seconds=600.0, owned_slots=1,
                      rentable_slots=8, usd_per_hour=1.0)
    by_slots = {p.total_slots: p for p in points}
    # Four tasks over four slots is as fast as it gets; a fifth slot is
    # spend for nothing and must be labelled as such.
    assert by_slots[4].finish_seconds == by_slots[8].finish_seconds
    assert by_slots[8].advice_code == "beyond_task_count"


def test_cost_rises_only_with_rented_slots():
    points = frontier(task_count=8, task_seconds=600.0, owned_slots=2,
                      rentable_slots=2, usd_per_hour=1.0)
    zero = [p for p in points if p.rented_slots == 0]
    assert zero and all(p.usd_cost == 0.0 for p in zero)
    assert any(p.usd_cost > 0.0 for p in points if p.rented_slots > 0)


def test_points_are_ordered_by_fleet_size():
    points = frontier(task_count=8, task_seconds=60.0, owned_slots=1,
                      rentable_slots=3, usd_per_hour=1.0)
    assert [p.total_slots for p in points] == sorted(
        p.total_slots for p in points
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_router_frontier.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
"""The buyer's curve: what each additional machine buys, and when it buys
nothing.

`plan.py` computes the two endpoints -- cheapest as a price-ordered scan,
fastest as a water-fill. This is the sweep between them, and it exists to
make ONE thing visible that a two-endpoint view hides: the point past
which more machines change nothing.

That point is the task count. `plan.py`'s arithmetic works because the
tasks it targets are independent; a fill spreads N tasks over M slots. For
N = 1 -- a COMMAND job, a single TRAINING task -- the answer is that no
fleet is faster, and saying otherwise sells somebody a GPU that cannot
help them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = ["ADVICE_BEYOND_TASK_COUNT", "ADVICE_HELPS", "ADVICE_NO_PARALLELISM",
           "FrontierPoint", "frontier"]

#: More slots than tasks. Spend buys nothing from here on.
ADVICE_BEYOND_TASK_COUNT = "beyond_task_count"
#: One task. No fleet is faster, at any price.
ADVICE_NO_PARALLELISM = "no_parallelism"
#: This point is genuinely faster than the one before it.
ADVICE_HELPS = "helps"


@dataclass(frozen=True)
class FrontierPoint:
    total_slots: int
    owned_slots: int
    rented_slots: int
    finish_seconds: float
    usd_cost: float
    advice_code: str


def _finish_seconds(task_count: int, task_seconds: float, slots: int) -> float:
    """Water-fill: the slowest slot's pile is what everyone waits for."""
    if slots <= 0:
        return math.inf
    per_slot = math.ceil(task_count / slots)
    return float(per_slot) * float(task_seconds)


def frontier(
    *,
    task_count: int,
    task_seconds: float,
    owned_slots: int,
    rentable_slots: int,
    usd_per_hour: float,
) -> list[FrontierPoint]:
    """One point per fleet size, from owned-only up to owned + rentable."""
    points: list[FrontierPoint] = []
    for rented in range(0, max(0, rentable_slots) + 1):
        total = max(0, owned_slots) + rented
        if total <= 0:
            continue
        finish = _finish_seconds(task_count, task_seconds, total)

        if task_count <= 1:
            advice = ADVICE_NO_PARALLELISM
        elif total > task_count:
            advice = ADVICE_BEYOND_TASK_COUNT
        else:
            advice = ADVICE_HELPS

        # Rented capacity bills for the wall-clock it is held, which is the
        # whole job -- not for the fraction of it that this slot was busy.
        usd = float(rented) * float(usd_per_hour) * (finish / 3600.0)
        points.append(
            FrontierPoint(
                total_slots=total,
                owned_slots=max(0, owned_slots),
                rented_slots=rented,
                finish_seconds=finish,
                usd_cost=round(usd, 4),
                advice_code=advice,
            )
        )
    return points
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_router_frontier.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/router/frontier.py flashml-cloud/apps/api/tests/test_router_frontier.py
git commit -m "feat(router): time-versus-money frontier, honest about task-count limits"
```

---

### Task 9: Expose the trade-off curve on the job page

**Suggested agent: sonnet**

> **READ THIS BEFORE THE TASK BODY — the body below is stale in three ways.**
>
> 1. **Names.** The module is `router/tradeoff.py`, the function is
>    `tradeoff_curve()`, the dataclass is `TradeoffPoint`. Every mention of
>    `frontier` below is wrong. This matters more here than anywhere:
>    `router/__init__.py:116` re-exports a *different* `plan.frontier`
>    function, and importing a module of that name gets you the function
>    instead — silently. **This task was named as the one that would hit it
>    first.** Name the route `/tradeoff`, the panel `TradeoffPanel.tsx`, the
>    view model `lib/tradeoff.ts`.
> 2. **There are FIVE advice codes, not three.** `no_parallelism`,
>    `beyond_task_count`, `helps`, plus **`no_marginal_gain`** (a fleet size
>    that costs more and finishes no sooner) and **`baseline`** (the buyer's
>    own hardware at zero cost, which has no predecessor to improve on). The
>    view model must render all five. **`no_marginal_gain` must never render
>    as an upsell** — it is the code that exists to stop someone buying a GPU
>    that cannot help them, and it is the whole reason this module was
>    written. `baseline` needs neutral copy, not a "this helps" badge.
> 3. **Currency.** 1 ZC = $1 USD is now a settled owner decision, so a total
>    is permissible — but show both units. Another agent owns the wallet and
>    conversion surfaces; do not edit them.

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py` — add `GET /v1alpha1/jobs/{job_id}/frontier` beside the existing `preview-plans` route
- Test: `flashml-cloud/apps/api/tests/test_frontier_route.py`
- Modify: `flashml-cloud/apps/web/lib/cloud-api.ts`
- Create: `flashml-cloud/apps/web/components/jobs/FrontierPanel.tsx`
- Create: `flashml-cloud/apps/web/lib/frontier.test.ts`

**Interfaces:**
- Consumes: `frontier()` (Task 8).
- Produces: the console surface. **Post-submit only** — the pre-submit version is blocked on honest task counts (spec §3.4) and is explicitly out of scope.

- [ ] **Step 1: Write the failing API test**

```python
"""The route is viewer-checked and never invents a pool."""
from __future__ import annotations


def test_frontier_requires_the_viewer_to_own_the_job(client, other_users_job):
    r = client.get(f"/v1alpha1/jobs/{other_users_job}/frontier")
    assert r.status_code == 404


def test_a_public_job_is_told_renting_cannot_help_it(client, public_job):
    """Spec D2: never silently add a pool. Say why instead."""
    r = client.get(f"/v1alpha1/jobs/{public_job}/frontier")
    assert r.status_code == 200
    body = r.json()
    assert body["rentable"] is False
    assert "pool" in body["reason"].lower()


def test_a_pool_scoped_job_gets_points(client, pool_job):
    r = client.get(f"/v1alpha1/jobs/{pool_job}/frontier")
    assert r.status_code == 200
    body = r.json()
    assert body["rentable"] is True
    assert len(body["points"]) >= 1
    assert {"total_slots", "finish_seconds", "usd_cost", "advice_code"} <= set(
        body["points"][0]
    )
```

Read `tests/test_router_preview.py` (or whichever test covers `preview-plans`) and reuse its fixtures for `client` and the job fixtures rather than inventing new ones.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_frontier_route.py -v`
Expected: FAIL — 404 on an unknown route.

- [ ] **Step 3: Add the route**

Follow the `preview-plans` route's exact shape for viewer checks and error handling. The response body:

```python
{
    "rentable": bool,     # false for a public job
    "reason": str,        # why not, when rentable is false
    "currency_note": "Rented capacity is priced in USD. 1 ZC = $1 USD.",
    "points": [...],      # FrontierPoint, as dicts
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd flashml-cloud/apps/api && .venv/bin/pytest tests/test_frontier_route.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing view-model test**

Follow `lib/market-prices.test.ts` for style. The view model must:
- render `advice_code: "no_parallelism"` as a statement that more machines will not help, **not** as a disabled upsell
- show ZC and USD as separate labelled values, never summed into one number
- render a null cost as "—", never as `0`

- [ ] **Step 6: Build the panel and make the tests pass**

Run: `cd flashml-cloud/apps/web && npm test`

- [ ] **Step 7: Commit**

```bash
git add flashml-cloud/apps/api/flashml_cloud_api/app.py flashml-cloud/apps/api/tests/test_frontier_route.py flashml-cloud/apps/web/
git commit -m "feat(console): frontier panel on the job page"
```

---

## Execution order

```
Task 1 ──────────────────────────► Task 7
                                     ▲
Task 2 → Task 3 → Task 4 → Task 5 → Task 6
                                     
Task 8 → Task 9                    (parallel from the start)
```

Tasks 2–6 and 8–9 have no dependency on Task 1. Start Task 1 and Task 2 together.

## Manual verification, once Task 7 lands

Not a test — a recorded run.

- [ ] Submit a pool-scoped GPU job with no eligible owned machine.
- [ ] Watch a rental appear in `rented_capacity`, reach ACTIVE, and register as a node in the submitter's pool.
- [ ] Confirm the task runs on it and completes.
- [ ] Confirm the machine is **destroyed** and the row reads RELEASED.
- [ ] Confirm total spend against the venue's own billing page, and record it beside the $0.89 figure from 2026-08-12.
