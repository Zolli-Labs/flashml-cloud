# Testing Credit Requests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give new accounts 10 ZC, let admitted users request additional testing credits for admin review, display the fixed 1 ZC = 1 USD value, and use that conversion when comparing ZC- and USD-priced compute.

**Architecture:** Preserve the append-only marketplace ledger and add a dedicated `credit_requests` state machine. Approval mints one idempotent `adjustment` entry tied to the request, browser routes expose user/admin workflows, the wallet and existing admin queue render those routes, and the router normalizes price only for comparison while retaining original settlement totals.

**Tech Stack:** PostgreSQL/Supabase migrations, psycopg 3, FastAPI, pytest, Next.js 16, React 19, TypeScript, Vitest.

## Global Constraints

- One Zolli credit has a fixed cash value: **1 ZC = 1 USD**.
- A new account's one-time starting grant is **10,000 millicredits = 10 ZC = $10**.
- Existing balances and historical 250 ZC grants must not be changed.
- Every ledger, balance, request, and price value remains integer millicredits; USD display values use decimal strings.
- A user may have only one pending credit request, but approved and declined history is retained.
- An admin may approve the requested amount, replace it with another positive amount, or decline.
- Approval is atomic and retry-safe: exactly one `adjustment` ledger entry references each approved request.
- ZC and USD source totals remain visible separately; normalized USD is used only for comparison and combined value display.
- Preserve all pre-existing uncommitted node-lifecycle work and unrelated files.
- Do not commit or push; the coordinator owns integration and publication decisions.

---

### Task 1: Credit Request Ledger Repository

**Files:**
- Rename: `flashml-cloud/apps/api/migrations/0019_ephemeral_machines.sql` → `flashml-cloud/apps/api/migrations/0020_ephemeral_machines.sql`
- Create: `flashml-cloud/apps/api/migrations/0021_credit_requests.sql`
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/marketplace.py`
- Modify: `flashml-cloud/apps/api/tests/test_marketplace.py`
- Modify: `flashml-cloud/apps/api/tests/test_schema.py`
- Modify: `flashml-cloud/apps/api/tests/test_attempt_settlement.py`
- Modify: `flashml-cloud/apps/api/tests/test_plan_preview.py`

**Interfaces:**
- Produces: `STARTING_GRANT_ZC = 10_000`, `USD_PER_ZC = Decimal("1")`.
- Produces: `create_credit_request(db, owner_id, *, requested_zc, purpose) -> Mapping[str, Any]`.
- Produces: `list_credit_requests(db, owner_id) -> list[Mapping[str, Any]]`.
- Produces: `list_admin_credit_requests(db, *, status) -> list[Mapping[str, Any]]`.
- Produces: `approve_credit_request(db, request_id, *, admin_id, approved_zc) -> Mapping[str, Any]`.
- Produces: `decline_credit_request(db, request_id, *, admin_id) -> Mapping[str, Any]`.
- Produces exceptions `PendingCreditRequestExists`, `CreditRequestNotPending`, and `InvalidCreditRequest`.

- [ ] **Step 1: Write schema and repository tests first**

Add tests proving: positive requested/approved amounts; trimmed purpose length 1–2,000; one partial-unique pending row per user; history remains; admin listing includes profile and current balances; approval with a changed amount posts exactly one `adjustment` leg using `ref_type='credit_request'`; repeat/concurrent decisions cannot mint twice; decline moves no ledger; `grant_starting_credits` now grants 10,000 while a previously written 250,000 grant remains untouched.

```python
def test_admin_may_approve_less_than_requested(db, owner, admin):
    request = market.create_credit_request(
        db, owner, requested_zc=50_000, purpose="Test a multi-GPU sweep"
    )
    decided = market.approve_credit_request(
        db, str(request["id"]), admin_id=admin, approved_zc=20_000
    )
    assert decided["requested_zc"] == 50_000
    assert decided["approved_zc"] == 20_000
    assert market.credit_balances(db, owner)["spendable_zc"] == 20_000
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv/bin/pytest -q flashml-cloud/apps/api/tests/test_marketplace.py flashml-cloud/apps/api/tests/test_schema.py`

Expected: failures because the migration table, constants, functions, and exceptions do not exist or still grant 250,000.

- [ ] **Step 3: Add migration and repository implementation**

The table uses UUID ids, cascading requester FK, `on delete set null` reviewer FK, bigint millicredits, status checks, state-shape checks, RLS, `(user_id, requested_at desc)` history index, `(status, requested_at)` admin index, and a partial unique pending index. Approval locks the request `for update`, calls `ensure_accounts`, posts one `adjustment` entry keyed by request id, then updates the decision in the same transaction.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `.venv/bin/pytest -q flashml-cloud/apps/api/tests/test_marketplace.py flashml-cloud/apps/api/tests/test_schema.py`

Expected: all focused tests pass.

- [ ] **Step 5: Self-review**

Check `git diff --check`, inspect every new SQL constraint and index, and record changed files and test output in the SDD report. Do not commit.

---

### Task 2: Credit Request HTTP API

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py`
- Modify: `flashml-cloud/apps/api/tests/test_market_routes.py`

**Interfaces:**
- Consumes Task 1 repository functions and exceptions exactly as named.
- Produces user routes `GET/POST /v1alpha1/credit-requests`.
- Produces admin routes `GET /v1alpha1/admin/credit-requests`, `POST /{request_id}/approve`, and `POST /{request_id}/decline`.
- Extends `GET /v1alpha1/credits` with `usd_per_zc`, `spendable_usd`, and `held_usd` decimal strings.

- [ ] **Step 1: Write route tests first**

Cover 401 signed-out, 403 non-admin, 400 invalid amount/purpose/status, 409 duplicate pending request, owner-scoped history, 404 already-decided request, edited approval amount, decline, balance conversion strings, and one-time adjustment under a repeated approval call.

```python
def test_admin_can_replace_the_requested_amount(client, db):
    user, admin = _new_user(db), _new_admin(db)
    created = client.post(
        "/v1alpha1/credit-requests",
        headers=_auth(user),
        json={"requested_zc": 50_000, "purpose": "Test rented GPUs"},
    ).json()
    response = client.post(
        f"/v1alpha1/admin/credit-requests/{created['id']}/approve",
        headers=_auth(admin),
        json={"approved_zc": 25_000},
    )
    assert response.status_code == 200
    assert response.json()["approved_zc"] == 25_000
```

- [ ] **Step 2: Run route tests and verify RED**

Run: `.venv/bin/pytest -q flashml-cloud/apps/api/tests/test_market_routes.py`

Expected: route 404s and old credit response shape.

- [ ] **Step 3: Implement route validation and mappings**

Use `admitted_user` for user routes and `admin_user` for admin routes. Parse JSON with `_json_object`; accept integer millicredits only; trim and validate purpose server-side; map duplicate pending to 409, invalid input to 400, and non-pending decisions to 404. Format USD via `Decimal(millicredits) / Decimal(1000)` with exactly two display decimals.

- [ ] **Step 4: Run route tests and verify GREEN**

Run: `.venv/bin/pytest -q flashml-cloud/apps/api/tests/test_market_routes.py`

Expected: all market route tests pass.

- [ ] **Step 5: Run API regression boundary**

Run: `.venv/bin/pytest -q flashml-cloud/apps/api/tests/test_marketplace.py flashml-cloud/apps/api/tests/test_market_routes.py flashml-cloud/apps/api/tests/test_schema.py`

Expected: all pass; record output in the SDD report. Do not commit.

---

### Task 3: Wallet and Admin Request UI

**Files:**
- Modify: `flashml-cloud/apps/web/lib/cloud-api.ts`
- Create: `flashml-cloud/apps/web/lib/credit-requests.ts`
- Create: `flashml-cloud/apps/web/lib/credit-requests.test.ts`
- Modify: `flashml-cloud/apps/web/lib/market-credits.ts`
- Modify: `flashml-cloud/apps/web/lib/market-credits.test.ts`
- Modify: `flashml-cloud/apps/web/components/market/CreditsPanel.tsx`
- Modify: `flashml-cloud/apps/web/app/(console)/market/page.tsx`
- Modify: `flashml-cloud/apps/web/app/(console)/admin/requests/page.tsx`

**Interfaces:**
- Consumes Task 2 route payloads without re-deriving policy constants.
- Produces `CreditRequest`, `AdminCreditRequest`, `listCreditRequests`, `submitCreditRequest`, `listAdminCreditRequests`, `approveCreditRequest`, and `declineCreditRequest` in `cloud-api.ts`.
- Produces pure helpers `parseZcInput`, `usdForMillicredits`, `creditRequestSummary`, and `restoreCreditRequest` in `credit-requests.ts`.

- [ ] **Step 1: Write pure helper and API contract tests first**

Test up-to-three-decimal parsing, refusal of zero/negative/excess precision, USD formatting from integer millicredits, requested-versus-approved copy, and idempotent queue restoration. Extend `cloud-api.test.ts` only if existing request-mocking patterns cover these new routes cleanly.

```typescript
expect(parseZcInput("10.125")).toBe(10125);
expect(usdForMillicredits(10125)).toBe("$10.13");
expect(creditRequestSummary({ requested_zc: 50000, approved_zc: 25000 }))
  .toContain("25 ZC approved from 50 ZC requested");
```

- [ ] **Step 2: Run focused web tests and verify RED**

Run: `npm test -- --run lib/credit-requests.test.ts lib/market-credits.test.ts`

Expected: missing module/helpers or missing dual-value wallet output.

- [ ] **Step 3: Implement wallet request experience**

Show spendable and escrow ZC with API-provided USD strings. Add a restrained `Request more credits` panel with requested ZC, live USD preview, required purpose, pending-state replacement, compact decision history, loading/error states, and successful refresh of both credits and requests.

- [ ] **Step 4: Implement admin credit queue tab**

Keep access cards unchanged. Add Access/Credit tabs; credit cards show requester identity, current balances, requested dual value, purpose, and date. Approve uses a prefilled editable ZC input and sends integer millicredits; decline sends no amount. Reuse optimistic removal/restoration and 404 race handling.

- [ ] **Step 5: Run web tests, lint, and type/build verification**

Run: `npm test -- --run lib/credit-requests.test.ts lib/market-credits.test.ts lib/access-request-queue.test.ts`

Run: `npm run lint -- lib/cloud-api.ts lib/credit-requests.ts lib/market-credits.ts components/market/CreditsPanel.tsx 'app/(console)/market/page.tsx' 'app/(console)/admin/requests/page.tsx'`

Run: `npm run build`

Expected: tests, lint, and build pass. Record exact output; do not commit.

---

### Task 4: Cash-Normalized Marketplace Routing

**Files:**
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/router/plan.py`
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/prices.py`
- Modify: `flashml-cloud/apps/api/tests/test_router_plan.py`
- Modify: `flashml-cloud/apps/api/tests/test_plan_preview.py`
- Modify: `flashml-cloud/apps/api/flashml_cloud_api/app.py`
- Modify: `flashml-cloud/apps/api/tests/test_market_routes.py`
- Modify: `flashml-cloud/apps/web/lib/cloud-api.ts`
- Modify: `flashml-cloud/apps/web/lib/job-routing.ts`
- Modify: `flashml-cloud/apps/web/lib/job-routing.test.ts`
- Modify: `flashml-cloud/apps/web/lib/market-prices.ts`
- Modify: `flashml-cloud/apps/web/lib/market-prices.test.ts`
- Modify: `flashml-cloud/apps/web/components/jobs/RoutingCard.tsx`
- Modify: `flashml-cloud/apps/web/components/market/PricesPanel.tsx`
- Modify: `flashml-cloud/apps/web/components/market/ListingsPanel.tsx`
- Modify: `flashml-cloud/apps/web/app/(console)/market/prices/page.tsx`

**Interfaces:**
- Produces `Cost.total_usd_value() -> float` using `zc + usd` under the fixed rate.
- Changes cheapest grouping/order to normalized unit USD price, then venue rank, then machine id.
- Changes frontier objectives to `(tasks_unplaced, total_usd_value, makespan)`.
- Preserves `Cost.zc` and `Cost.usd` as original settlement totals.
- Adds normalized USD values to plan previews and dual-unit labels to marketplace asks.

- [ ] **Step 1: Write router and market display tests first**

Prove a `$0.70/hour` rented candidate beats a `0.80 ZC/hour` community candidate, equal normalized prices use venue order, mixed source costs keep separate ZC/USD totals while exposing their sum, and frontier domination compares normalized value. Add API/web tests for `0.80 ZC/hour` plus `$0.80/hour equivalent`.

```python
def test_cheapest_compares_zc_and_usd_at_parity():
    plan = plan_request(candidates=[community(price=0.80), runpod(price=0.70)])
    assert plan.cheapest.assignments[0].venue == "runpod"
    assert plan.cheapest.cost.zc == 0
    assert plan.cheapest.cost.usd > 0
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `.venv/bin/pytest -q flashml-cloud/apps/api/tests/test_router_plan.py flashml-cloud/apps/api/tests/test_market_routes.py`

Run: `npm test -- --run lib/market-prices.test.ts`

Expected: old venue-first ordering, vector frontier, and single-unit labels fail the new assertions.

- [ ] **Step 3: Implement normalized comparison without losing settlement units**

Update doctrine comments and helper names throughout touched files. Group cheapest tiers by normalized price, retain venue order only as equal-price tie-break, and include normalized total in preview JSON. Do not convert or rewrite the ledger settlement currency.

- [ ] **Step 4: Implement marketplace dual-value labels**

Show ZC asks with USD equivalents and USD vendor prices with ZC equivalents only on wallet/marketplace surfaces. Keep external quote currency and ZC settlement fields distinct in API types.

- [ ] **Step 5: Run focused backend and frontend verification**

Run: `.venv/bin/pytest -q flashml-cloud/apps/api/tests/test_router_plan.py flashml-cloud/apps/api/tests/test_market_routes.py`

Run: `npm test -- --run lib/market-prices.test.ts lib/market-listings.test.ts lib/job-routing.test.ts`

Expected: all focused tests pass. Record output; do not commit.

---

### Task 5: Integrated Documentation and Verification

**Files:**
- Modify: `PROGRESS.md`
- Modify: `flashml-cloud/docs/superpowers/specs/2026-08-12-market-design-v2.md`
- Modify: `flashml-cloud/docs/superpowers/specs/2026-08-12-console-ui-plan.md`
- Modify: `flashml-cloud/docs/superpowers/specs/2026-08-12-ui-handoff-prompt.md`
- Modify: `flashml-cloud/docs/superpowers/specs/2026-08-12-on-demand-capacity-design.md`
- Modify: any touched comments that still assert ZC and USD are incomparable or the grant is 250 ZC

**Interfaces:**
- Consumes all prior tasks.
- Produces an auditable progress entry and a codebase with no active contradictory currency doctrine.

- [ ] **Step 1: Search and update active doctrine**

Run: `rg -n "250 ZC|no exchange rate|never converted|never summed|incomparable|STARTING_GRANT_ZC" flashml-cloud/apps flashml-cloud/docs/superpowers/specs/2026-08-12-market-design-v2.md`

Update active implementation comments, tests, and current marketplace spec. Do not rewrite archived plans or historical progress entries.

- [ ] **Step 2: Add newest-first progress entry**

Record the 10 ZC prospective grant, unchanged existing accounts, request/admin workflow, fixed conversion, normalized routing, migration numbers, and exact verification results.

- [ ] **Step 3: Run full API suite**

Run: `.venv/bin/pytest -q`

Expected: zero failures.

- [ ] **Step 4: Run full web suite and build**

Run: `npm test`

Run: `npm run lint`

Run: `npm run build`

Expected: zero failures/errors.

- [ ] **Step 5: Final repository checks**

Run: `git diff --check`

Run: `git status --short`

Confirm the unrelated `examples/demo-suite/`, the prior node-lifecycle edits, and the on-demand capacity spec remain preserved. Do not commit or push.
