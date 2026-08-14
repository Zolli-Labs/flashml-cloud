# Pool-First Automatic Routing (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pool job whose `flashml.yaml` carries a `price:` block is automatically routed at submit time — planned against the live listing book, bid, matched at hosts' asks, and fully explained — with zero behavior change for jobs that don't carry one.

**Architecture:** Parse `price:` in the existing yaml layer; resolve the job's capability class cloud-side; run the already-tested `marketplace` engine (`open_asks → match_bid → create_bid → grant_matches`) from the `from-repo` submit path behind a fail-open guard; expose the decision as a `routing` block on the submit response and a `GET /jobs/{id}/routing` route. The claim-side entitlement/escrow hook already exists (`app.py` claim path) — this plan wires the demand side that feeds it.

**Tech Stack:** FastAPI + psycopg (apps/api), pytest. No new dependencies. No coordinator/runtime changes — everything is cloud-side, so the flashruntime 0.6.0 pin is untouched.

**Spec:** `docs/superpowers/specs/2026-08-11-zolli-marketplace-design.md` (M1–M14, §4 matching rule) + `docs/research/2026-08-13-automatic-routing-marketplace-matching.md` §5–§6 (Phase 1) — both in this repo (research doc one level up at repo root `docs/`).

## Global Constraints

- **Shared checkout:** execute in a worktree (`git worktree add .worktrees/routing-phase1 -b feat/pool-routing-phase1 develop`). Commit with pathspecs on the commit itself: `git commit -m "..." -- <path> <path>`. NEVER `git add -A`, `git stash`, `git reset`, `git checkout <ref>`, `git clean`, or any repo-scoped git command. To see pre-existing state: `git show develop:<path>`.
- **Fail-open on submit:** routing must never turn a submit that would have succeeded into a 4xx/5xx (mirror the `_human_spend_guard` pattern at `app.py:5007-5031`). Routing failure = job submits unrouted + a logged warning + `"routing": {"state": "skipped", ...}` in the response.
- **One-door boundary:** nothing outside `flashml_cloud_api/placement.py` imports `flashruntime.scheduler` or `flashruntime.service.modea` (`test_import_boundary` enforces it). This plan does NOT widen the door.
- **No new copies of gate logic:** market-layer exclusion reasons only (`not-listed`, `ask-above-cap`, `unproven-cap`, `machine-inactive`). Per-gate scheduler reasons are a *deferred* upstream runtime feature — do not imitate them locally.
- **Workspace stays free (M1):** a bid prices only listed machines. Unlisted pool machines keep claiming the job for free exactly as today.
- **Defaults (owner-approved 2026-08-13):** `objective` default `balanced`; no `rented` key in this phase; GPU-class bids refused with a clear message until the `gpuPerTask` pin gap closes (`compile.py:608-624`).
- **Money units:** listings/bids store integer `*_zc_per_hour`. Before writing Task 1's conversion constant, VERIFY the unit by reading the `ask_zc_per_hour` column comment in `migrations/0018_marketplace.sql` and the existing listing-creation route (`app.py:8436` area) — the yaml surface takes decimal ZC/hour (e.g. `2.00`) and must convert to exactly the unit those store. Encode the verified factor as one named constant in Task 1 and reuse it everywhere.
- **Run tests from `apps/api`:** `.venv/bin/pytest tests/<file> -x -q` (venv from `make setup` at repo root). The full marketplace suite (`tests/test_marketplace.py`, `tests/test_market_routes.py`) must stay green after every task.

## File Structure

- `flashml_cloud_api/flashml_yaml.py` — add `price` to `OPTIONAL_KEYS`, `_validate_price()`, `FlashmlConfig.price`.
- `flashml_cloud_api/routing.py` — NEW, the only new module: class resolution + plan/explain orchestration. Imports `marketplace`, `metrics`, `db`; never the runtime.
- `flashml_cloud_api/app.py` — hook in the two `from-repo`/`from-upload` submit paths + one new GET route.
- `tests/test_flashml_yaml_price.py`, `tests/test_routing.py`, `tests/test_routing_routes.py` — NEW.
- `e2e/` — one rehearsal scenario (Task 6), following the existing e2e layout (read `e2e/README.md` first).

---

### Task 1: `price:` block in flashml.yaml

**Files:**
- Modify: `flashml_cloud_api/flashml_yaml.py` (keys block at :110, `FlashmlConfig` at :208, validator section)
- Test: `tests/test_flashml_yaml_price.py` (new)

**Interfaces:**
- Produces: `FlashmlConfig.price: dict | None` with exact shape `{"max_zc_per_hour": int, "objective": str, "budget_zc": int | None}` (integers in the ledger unit verified per Global Constraints; `objective` ∈ `{"cheapest","balanced","fastest"}`). Also `ZC_PER_HOUR_UNIT` (the verified decimal→integer factor) exported from this module.
- Consumes: nothing new.

- [ ] **Step 1: Verify the money unit** (Global Constraints). Run: `grep -n "ask_zc_per_hour" migrations/0018_marketplace.sql` and read the column comment; read the listing POST route at `app.py:8436` to see what integer the console sends for, e.g., a 0.22 ZC/hr ask. Record the factor in the test file header as a comment with the evidence line numbers.

- [ ] **Step 2: Write the failing tests**

```python
"""Unit evidence: <fill the two file:line refs from Step 1 here>."""
import pytest
from flashml_cloud_api.flashml_yaml import (
    FlashmlYamlError, parse_flashml_yaml, ZC_PER_HOUR_UNIT,
)

BASE = """\
version: 2
name: routed-job
image: pytorch-2.4
entrypoint: train.py
"""


def _parse(extra: str):
    return parse_flashml_yaml(BASE + extra)


def test_absent_price_is_none_and_nothing_else_changes():
    config = _parse("")
    assert config.price is None


def test_price_parses_decimal_zc_into_the_ledger_unit():
    config = _parse("price:\n  max_per_hour: 2.00\n")
    assert config.price == {
        "max_zc_per_hour": 2 * ZC_PER_HOUR_UNIT,
        "objective": "balanced",
        "budget_zc": None,
    }


def test_objective_is_validated_and_defaulted():
    config = _parse("price:\n  max_per_hour: 1.5\n  objective: cheapest\n")
    assert config.price["objective"] == "cheapest"
    with pytest.raises(FlashmlYamlError, match="objective"):
        _parse("price:\n  max_per_hour: 1.5\n  objective: fanciest\n")


def test_zero_and_negative_and_absent_max_are_refused():
    with pytest.raises(FlashmlYamlError, match="max_per_hour"):
        _parse("price:\n  objective: balanced\n")
    with pytest.raises(FlashmlYamlError, match="max_per_hour"):
        _parse("price:\n  max_per_hour: 0\n")
    with pytest.raises(FlashmlYamlError, match="max_per_hour"):
        _parse("price:\n  max_per_hour: -1\n")


def test_budget_must_cover_at_least_one_hour_at_the_cap():
    config = _parse("price:\n  max_per_hour: 2.0\n  budget: 25\n")
    assert config.price["budget_zc"] == 25 * ZC_PER_HOUR_UNIT
    with pytest.raises(FlashmlYamlError, match="budget"):
        _parse("price:\n  max_per_hour: 2.0\n  budget: 1\n")


def test_unknown_price_keys_are_refused_by_name():
    with pytest.raises(FlashmlYamlError, match="rented"):
        _parse("price:\n  max_per_hour: 2.0\n  rented: allow\n")
```

(If the module's error type is not named `FlashmlYamlError`, use the error type the existing validators raise — check `_validate_resources` at `flashml_yaml.py:843` — and keep the `match=` assertions.)

- [ ] **Step 3: Run tests to verify they fail** — `pytest tests/test_flashml_yaml_price.py -x -q`, expect ImportError on `ZC_PER_HOUR_UNIT`.

- [ ] **Step 4: Implement.** Add `"price"` to `OPTIONAL_KEYS` (:111 block). Add module constant `ZC_PER_HOUR_UNIT: int` with the Step-1 verified value and a comment citing the evidence. Add `price: dict | None = None` to `FlashmlConfig` (:208). Add validator, following the style of the neighbors at :562-:660:

```python
_OBJECTIVES = ("cheapest", "balanced", "fastest")


def _validate_price(value: object) -> dict:
    """The market opt-in. Absent means today's behavior: workspace only, free.

    ``max_per_hour`` is decimal ZC per machine-hour and converts exactly into
    the ledger's integer unit; a value the unit cannot represent exactly
    (more decimal places than the unit carries) is refused rather than
    rounded — a price the user typed must be the price the bid carries.
    """
    raw = _validate_mapping(value, "price")
    allowed = {"max_per_hour", "objective", "budget"}
    unknown = set(raw) - allowed
    if unknown:
        raise FlashmlYamlError(
            f"price: unknown key(s) {sorted(unknown)!r}; "
            f"allowed: {sorted(allowed)!r}"
        )

    def to_unit(field: str, val: object, *, minimum_units: int) -> int:
        try:
            units = Fraction(str(val)) * ZC_PER_HOUR_UNIT
        except (ValueError, ZeroDivisionError):
            raise FlashmlYamlError(f"price.{field}: not a number: {val!r}")
        if units.denominator != 1:
            raise FlashmlYamlError(
                f"price.{field}: {val!r} has more precision than "
                f"1/{ZC_PER_HOUR_UNIT} ZC"
            )
        if units < minimum_units:
            raise FlashmlYamlError(f"price.{field}: must be at least "
                                   f"{minimum_units}/{ZC_PER_HOUR_UNIT} ZC")
        return int(units)

    if "max_per_hour" not in raw:
        raise FlashmlYamlError("price: max_per_hour is required")
    max_zc = to_unit("max_per_hour", raw["max_per_hour"], minimum_units=1)

    objective = raw.get("objective", "balanced")
    if objective not in _OBJECTIVES:
        raise FlashmlYamlError(
            f"price.objective: {objective!r} is not one of {_OBJECTIVES}"
        )

    budget = None
    if raw.get("budget") is not None:
        budget = to_unit("budget", raw["budget"], minimum_units=max_zc)

    return {"max_zc_per_hour": max_zc, "objective": objective,
            "budget_zc": budget}
```

Wire it where the other optional keys dispatch (search for where `_validate_resources` is called) and pass the result into the `FlashmlConfig` constructor. Import `Fraction` from `fractions` at the top if not present.

- [ ] **Step 5: Run the new tests AND the existing yaml suite** — `pytest tests/test_flashml_yaml_price.py tests/test_flashml_yaml*.py -q` (use the actual existing yaml test filename; find it with `ls tests/ | grep yaml`). Expected: all PASS.

- [ ] **Step 6: Commit** — `git commit -m "feat(api): price block in flashml.yaml" -- flashml_cloud_api/flashml_yaml.py tests/test_flashml_yaml_price.py`

---

### Task 2: Job capability-class resolution (`routing.py`, part 1)

**Files:**
- Create: `flashml_cloud_api/routing.py`
- Test: `tests/test_routing.py` (new)

**Interfaces:**
- Produces: `job_capability_class(resources: Mapping | None) -> str` and `GpuRoutingUnavailable(ValueError)`.
- Consumes: `marketplace.CAPABILITY_CLASSES`, `marketplace.CPU_LARGE_MIN_CORES` (`marketplace.py:150`).

- [ ] **Step 1: Write the failing tests** (append to new `tests/test_routing.py`)

```python
import pytest
from flashml_cloud_api import routing
from flashml_cloud_api.routing import GpuRoutingUnavailable, job_capability_class


def test_no_resources_is_the_small_cpu_class():
    assert job_capability_class(None) == "cpu-small"
    assert job_capability_class({}) == "cpu-small"


def test_the_cpu_split_mirrors_the_marketplace_threshold():
    from flashml_cloud_api.marketplace import CPU_LARGE_MIN_CORES
    assert job_capability_class({"cpus": CPU_LARGE_MIN_CORES}) == "cpu-large"
    assert job_capability_class({"cpus": CPU_LARGE_MIN_CORES - 1}) == "cpu-small"


def test_gpu_jobs_are_refused_with_the_pin_gap_named():
    with pytest.raises(GpuRoutingUnavailable, match="gpuPerTask"):
        job_capability_class({"gpus": 1})


def test_the_result_is_always_a_ladder_class():
    from flashml_cloud_api.marketplace import CAPABILITY_CLASSES
    assert job_capability_class({"cpus": 2}) in CAPABILITY_CLASSES
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_routing.py -x -q`, expect ImportError.

- [ ] **Step 3: Implement** (create `flashml_cloud_api/routing.py`)

```python
"""Submit-time routing: resolve what a job needs, plan against the book.

Design: docs/superpowers/specs/2026-08-11-zolli-marketplace-design.md §4/§6
and docs (repo root)/research/2026-08-13-automatic-routing-marketplace-matching.md §5.
This module never imports the runtime; eligibility stays the coordinator's,
matching stays `marketplace`'s. Everything here is orchestration and reasons.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import marketplace


class GpuRoutingUnavailable(ValueError):
    """GPU-class routing is blocked on the runtime pin.

    `gpuPerTask` is silently dropped by the pinned flashruntime ResourcesSpec
    (compile.py:608-624), so a routed GPU job would be priced for hardware the
    coordinator cannot yet reserve. Refuse loudly instead of routing a fiction;
    lands with the 0.6.1 release + 4-site pin bump.
    """


def job_capability_class(resources: Mapping[str, Any] | None) -> str:
    """The class the JOB needs — a property of the work (marketplace.py:1639)."""
    res = dict(resources or {})
    gpus = res.get("gpus") or 0
    if isinstance(gpus, bool) or int(gpus) > 0:
        raise GpuRoutingUnavailable(
            "price: routing for gpus > 0 is not available yet — the pinned "
            "runtime drops gpuPerTask (compile.py:608). Remove price: or set "
            "gpus: 0 until the 0.6.1 pin bump."
        )
    cpus = res.get("cpus") or 0
    if float(cpus) >= marketplace.CPU_LARGE_MIN_CORES:
        return "cpu-large"
    return "cpu-small"
```

(Verify the two CPU class names against `marketplace.CAPABILITY_CLASSES` at `marketplace.py:108` before committing — use the ladder's exact spellings.)

- [ ] **Step 4: Run tests** — `pytest tests/test_routing.py -x -q`. Expected: PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(api): job capability-class resolution for routing" -- flashml_cloud_api/routing.py tests/test_routing.py`

---

### Task 3: Plan + explain (`routing.py`, part 2)

**Files:**
- Modify: `flashml_cloud_api/routing.py`
- Test: `tests/test_routing.py` (extend)

**Interfaces:**
- Consumes: `marketplace.open_asks(db, class, acceptance_rates=...)` (`marketplace.py:1571`), `marketplace.match_bid(max_zc_per_hour=, tasks_wanted=, asks=, workspace_reserved=)` (:668), `marketplace.effective_price` (:606), `metrics.acceptance_rates` (`metrics.py:176` — read its signature and call it exactly as `class_board` at `marketplace.py:2481` does; copy that call shape).
- Produces:

```python
def plan_pool_routing(db, *, capability_class: str, max_zc_per_hour: int,
                      tasks_wanted: int) -> dict[str, Any]
```

returning EXACTLY:

```python
{
  "capability_class": str,
  "tasks_wanted": int,
  "tasks_filled": int,
  "tasks_unfilled": int,
  "book": [  # every open ask in the class, ranked, matched or not
    {"listing_id": str, "machine_id": str, "ask_zc_per_hour": int,
     "acceptance_rate": float | None, "effective_zc_per_hour": str | None,
     "tasks_assigned": int, "excluded": str | None},
  ],
  "nearest_miss": {"ask_zc_per_hour": int, "listing_id": str} | None,
  "plan": MatchPlan,   # the raw engine result, for the caller to grant
}
```

`excluded` ∈ `{None, "ask-above-cap", "unproven-cap", "no-tasks-left"}`. `effective_zc_per_hour` is `str(Fraction)` or None (unclearable). `nearest_miss` is the cheapest effective price above the cap when `tasks_unfilled > 0`, else None.

- [ ] **Step 1: Write the failing tests.** Use the DB fixture pattern from `tests/test_marketplace.py` (read its `db` fixture and seeding helpers first; reuse them — machines + listings seeded the same way). Cases:

```python
def test_the_book_is_ranked_and_matched_at_asks(db):
    # seed 3 active machines in one class: asks 100, 300, 200 with
    # acceptance rates 0.5, None, 1.0 → effective 200, 300(unproven), 200.
    # bid max 250, tasks 4, max_concurrent 2 each:
    # expect fills on the two clearing listings, tie broken by ask then id,
    # and the 300 listing present in book with excluded="ask-above-cap".
    ...

def test_a_starved_bid_reports_the_nearest_miss(db):
    # one listing at 500, bid max 250 → tasks_filled 0,
    # nearest_miss == {"ask_zc_per_hour": 500, "listing_id": <it>}
    ...

def test_an_empty_book_explains_itself(db):
    # no listings → book == [], nearest_miss None, tasks_unfilled == wanted
    ...
```

Write them as real tests with the seeding helpers — the shapes above are the assertions; the seeding calls come verbatim from `test_marketplace.py`'s existing helpers (`create_listing` at `marketplace.py:1400` signature).

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement `plan_pool_routing`.** Orchestration only: `rates = metrics.acceptance_rates(...)` (same call as `class_board`), `asks = marketplace.open_asks(db, capability_class, acceptance_rates=rates)`, `plan = marketplace.match_bid(max_zc_per_hour=..., tasks_wanted=..., asks=asks)`. Build the book by walking `marketplace.rank_asks(asks)` and joining fills by `listing_id`; compute `excluded` per the produced-shape rules; `nearest_miss` = first ranked ask whose effective price is not None and exceeds the cap, when `tasks_unfilled > 0`.

- [ ] **Step 4: Run the new tests + the full marketplace suite** — `pytest tests/test_routing.py tests/test_marketplace.py -q`. Expected: PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(api): pool routing plan with ranked book and reasons" -- flashml_cloud_api/routing.py tests/test_routing.py`

---

### Task 4: Wire routing into the pool submit path

**Files:**
- Modify: `flashml_cloud_api/app.py` — the `from-repo` submit handler (`:6093` region; find where `parse_flashml_yaml` result and the coordinator-accepted `job_id` are both in scope — around the `insert_job` call, mirroring the raw-submit shape at `:5056`) and the `from-upload` handler (`:6486`) which shares the compile helper — confirm whether they share one post-accept path; if they do, hook once there.
- Test: `tests/test_routing_routes.py` (new)

**Interfaces:**
- Consumes: `FlashmlConfig.price` (Task 1), `routing.job_capability_class` / `routing.plan_pool_routing` / `routing.GpuRoutingUnavailable` (Tasks 2–3), `marketplace.create_bid` (:1626), `marketplace.grant_matches` (:1779), the runtime task count for the job — use the SAME task count the submit path already knows (the coordinator's response or the expanded spec; find how `preview_plans`/`_route_plan` obtains `task_count` and reuse that exact source; do NOT invent an expansion).
- Produces: submit response gains `"routing"`; helper `route_submitted_job(db, *, user_id, job_id, config, task_count) -> dict` in `routing.py` so app.py carries only the hook.

- [ ] **Step 1: Write the failing route test.** Copy the app/client fixture and coordinator-stub pattern from `tests/test_market_routes.py` (read it first; reuse its fixtures). Cases:

```python
def test_a_priced_pool_job_creates_a_bid_and_matches(client, db, ...):
    # seed one active listed machine in cpu-small; submit a from-repo pool job
    # whose flashml.yaml has price.max_per_hour above the ask.
    # Assert: 201; response["routing"]["state"] == "routed";
    # one bids row (state partial/filled) for the job_id;
    # matches rows agree with response["routing"]["book"] fills;
    # est_task_seconds > 0 on the bid.

def test_a_job_without_price_routes_nothing(client, db, ...):
    # same submit, no price block → no bids row, "routing" absent from response.

def test_routing_failure_fails_open(client, db, monkeypatch, ...):
    # monkeypatch routing.plan_pool_routing to raise RuntimeError →
    # submit still 201, no bids row,
    # response["routing"] == {"state": "skipped", "reason": "routing-error"}.

def test_a_gpu_priced_job_is_refused_before_the_coordinator(client, db, ...):
    # price + resources.gpus: 1 → 400 whose detail names gpuPerTask,
    # and no job row was created (this is a VALIDATION failure, not routing).
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** In `routing.py`:

```python
def route_submitted_job(db, *, user_id: str, job_id: str,
                        config: Any, task_count: int) -> dict[str, Any]:
    """Create the bid and grant entitlements for a priced job. Fail-open caller."""
    price = config.price
    klass = job_capability_class(config.resources)
    est_seconds = _estimate_task_seconds(db, config, klass)  # see below
    planned = plan_pool_routing(
        db, capability_class=klass,
        max_zc_per_hour=price["max_zc_per_hour"], tasks_wanted=task_count,
    )
    bid = marketplace.create_bid(
        db, job_id=job_id, owner_id=user_id, capability_class_name=klass,
        max_zc_per_hour=price["max_zc_per_hour"], tasks_wanted=task_count,
        est_task_seconds=est_seconds,
    )
    if planned["plan"].fills:
        marketplace.grant_matches(db, bid_id=str(bid["id"]), plan=planned["plan"])
    out = {k: planned[k] for k in
           ("capability_class", "tasks_wanted", "tasks_filled",
            "tasks_unfilled", "book", "nearest_miss")}
    out.update({"state": "routed", "bid_id": str(bid["id"]),
                "objective": price["objective"]})
    return out
```

`_estimate_task_seconds`: call the router's estimator exactly as the cost-quote route does — read `app.py:5261` (`cost-quote`) and reuse its estimate source verbatim; if the estimate is absent/zero, fall back to `config.timeout_seconds or 3600` (never 0 — `create_bid` refuses it, `marketplace.py:1655`).

GPU refusal placement: `GpuRoutingUnavailable` must be raised during *validation*, before the coordinator call — in the handler, right after `parse_flashml_yaml`, `if config.price is not None: routing.job_capability_class(config.resources)` inside a try/except → HTTP 400 with the exception text.

The post-accept hook, mirroring the spend-guard's fail-open discipline (`app.py:5007-5031`):

```python
        routing_block = None
        if config.price is not None:
            try:
                routing_block = routing.route_submitted_job(
                    db, user_id=user_id, job_id=job_id,
                    config=config, task_count=task_count,
                )
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass
                log.warning(json.dumps({
                    "text": "routing failed open", "job_id": job_id,
                }))
                routing_block = {"state": "skipped", "reason": "routing-error"}
```

and merge `{"routing": routing_block}` into the JSON the handler returns when `routing_block is not None` (find where the handler builds its response; `_passthrough` responses need the block added to the parsed body — follow how the handler already augments or wraps the coordinator response, and if it strictly passes through, parse-augment-reserialize only when routing ran).

- [ ] **Step 4: Run** — `pytest tests/test_routing_routes.py tests/test_market_routes.py tests/test_marketplace.py -q`. Expected: PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(api): route priced pool jobs at submit" -- flashml_cloud_api/app.py flashml_cloud_api/routing.py tests/test_routing_routes.py`

---

### Task 5: `GET /v1alpha1/jobs/{job_id}/routing`

**Files:**
- Modify: `flashml_cloud_api/app.py` (place beside the other job GET routes; follow the auth/ownership pattern of `GET /v1alpha1/jobs` at `:6656` — owner-scoped)
- Test: `tests/test_routing_routes.py` (extend)

**Interfaces:**
- Consumes: `marketplace.bids_for_owner` (:1751) filtered by job_id — or a direct query mirroring `_BID_SELECT`; `marketplace.matches_for_bid` (:2091); `routing.plan_pool_routing` for the LIVE book re-explain.
- Produces: `{"bid": {...} | None, "matches": [...], "live_book": <Task-3 shape minus "plan">}` — 404 for a job the caller doesn't own (same as the job GET), `{"bid": None, ...}` for an unrouted job.

- [ ] **Step 1: Failing tests** — routed job returns its bid + matches + a live book; unrouted job returns `bid: None`; another user's job 404s.
- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement** — thin route: fetch bid by job_id + owner, matches via `matches_for_bid`, live book via `plan_pool_routing` (strip `"plan"` from the response — MatchPlan is not JSON).
- [ ] **Step 4: Run all three new test files + market suites.** Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(api): job routing inspection route" -- flashml_cloud_api/app.py tests/test_routing_routes.py`

---

### Task 6: E2E rehearsal through the authoring surface

**Files:**
- Create: one scenario in `e2e/` following the existing suite's layout — read `e2e/README.md` and the closest existing multi-machine scenario first, and mirror its structure exactly.
- Modify: none in `apps/api`.

**Interfaces:**
- Consumes: everything above, through HTTP only.

- [ ] **Step 1: Write the scenario.** With the local stack (`make e2e-setup LOCAL=1` world): two enrolled agents whose machines are listed at different asks (seed listings through the listings HTTP route, not SQL), one enrolled unlisted machine; submit a real `flashml.yaml` pool job (`gpus: 0`, `price.max_per_hour` above the cheaper ask only): assert (a) the submit response's `routing.book` ranks the cheap listing first and marks the expensive one `ask-above-cap`; (b) a match row exists for the cheap machine at ITS ask; (c) the job still completes (the unlisted machine may legally claim free — that is M1, assert it does NOT break the run); (d) `GET /jobs/{id}/routing` agrees with (a).
- [ ] **Step 2: Run it** — `make e2e` scoped to the new scenario per the suite's convention. Expected: PASS. A failure here that implicates claim-side escrow (`app.py:9295`) is a FINDING to report, not something this plan fixes silently.
- [ ] **Step 3: Log per protocol** — append the dated entry to `PROGRESS.md` (read its LOGGING PROTOCOL section first) naming: what routed, the evidence run, and the two deferred items (GPU classes → pin bump; per-gate reasons → upstream runtime).
- [ ] **Step 4: Commit** — `git commit -m "e2e: priced pool job routes against the listing book" -- e2e/<files> PROGRESS.md`

---

## Self-Review (done at planning time)

- **Spec coverage:** research §5.1 (`price:` yaml) → Task 1; §5.2 steps 1–6 (classify/rank/plan/bid/match) → Tasks 2–4; §5.4 explain → Tasks 3–5; §6 Phase-1 "MK-1 write path" → Tasks 4–5; verify-through-authoring-surface → Task 6. Deferred by name: `placement.accept`/`countries` (needs device-profile spec), `rented` (Phase 3), GPU classes (pin gap), per-gate reasons (upstream), objective-driven plan switching beyond the stored field (preview already offers the three plans; wiring objectives to venue selection is Phase 2+).
- **Placeholders:** Task 3 Step 1 test bodies are assertion-specs with seeding delegated to named existing helpers — acceptable because the helpers exist and are named; everything else carries code.
- **Type consistency:** `price` dict keys (`max_zc_per_hour`, `objective`, `budget_zc`) uniform across Tasks 1/4; `plan_pool_routing` return shape defined once (Task 3) and consumed by name in Tasks 4/5; `GpuRoutingUnavailable` defined Task 2, consumed Task 4.
