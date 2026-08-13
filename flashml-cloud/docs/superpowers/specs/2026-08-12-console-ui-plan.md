# Console UI — spec and implementation plan

**Date:** 2026-08-12 · **Audience:** a session picking this up cold
**Read first:** `2026-08-12-shipped-and-verified.md` (what landed today and
what is proven), `flashml-cloud/CLAUDE.md`, `flashml-cloud/flashml-cloud/CLAUDE.md`

Submission deadline **2026-08-15**. Prioritise accordingly: §A before §B
before §C.

---

## 0. The one-sentence problem

**The engine is built and tested; the surface that would let anyone see it is
missing.** Roughly 365 tests sit behind API surfaces the console never calls.
Nothing in §A requires new domain logic — it is wiring and design.

## 1. Where things are

```
apps/web/
  app/(marketing)/      landing, contact, privacy, security, terms
  app/(console)/        overview, jobs, jobs/[jobId], submit, pools,
                        workspaces, w/[poolId]/*, machines, metrics,
                        account/*, admin/requests, docs, how-it-works
  components/landing/   ~20 components — the visual language to match
  components/jobs/      ArtifactsCard, CheckpointsCard, RoutingCard, …
  lib/                  the TESTED decision layer (see §5)
```

Design tokens live in `app/globals.css` — ~295 custom properties, Tailwind v4
`@theme` style (there is no `tailwind.config.*`). **Those tokens are the single
source of truth for colour, surface and type.** 30 console files already use
them, so this is an alignment job, not a rewrite.

## 2. Style: the console must feel like the site

The owner's report is that the signed-in product does not look like the landing
page. Treat `app/(marketing)` + `components/landing/` as the reference and bring
the console to it.

**Do this as an audit with a written findings list before changing anything.**
Enumerate, per console route: which token it uses vs which the landing
equivalent uses, spacing scale, type scale, border and radius treatment, empty
and error states. Then fix in one pass with the list as the checklist.

Rules:
- **Never introduce a new colour.** If a value is not in `globals.css`, either
  it is wrong or the token is missing — add the token, do not inline a hex.
- Keep components markup-only and put decisions in `lib/` (§5). Several console
  components were recently refactored this way; match them, don't diverge.
- Dark is the product's native mode. Verify both if a light mode exists.

## 3. §A — The marketplace (highest value, and it is NOT frontend-only)

> **STOP: the marketplace has zero HTTP routes.** `marketplace.py` (ledger,
> listings, bids, matching — 52 tests) and `prices.py` (quotes with
> `captured_at`/`source` provenance — 53 tests) are imported by `app.py` only
> for a credits conversion and a price label. **A frontend session cannot build
> this UI without first adding the API surface.** Budget for backend work.

### 3.1 API routes to add (`app.py`, `tags=["browser"]`)

Follow the established doctrine exactly: `current_user` + a viewer/ownership
check, **404 never 403**, and `_jsonable` for output.

| Route | Purpose |
|---|---|
| `GET /v1alpha1/credits` | this account's balance, spendable vs held |
| `GET /v1alpha1/credits/ledger` | entries, newest first, paginated |
| `GET /v1alpha1/market/listings` | open asks with class, price, record |
| `POST /v1alpha1/market/listings` | list a machine you own |
| `DELETE /v1alpha1/market/listings/{id}` | withdraw |
| `GET /v1alpha1/market/matches` | this account's matches and their state |
| `GET /v1alpha1/prices` | current quotes with `captured_at` + `source` |

Read `marketplace.py`'s module docstring before designing any of it. Three
invariants it will not forgive:

1. **A match is a PRICED ENTITLEMENT, not an assignment.** The runtime is
   pull-only; a match makes a task *eligible at a price* and the host still
   claims it. Never render a match as "this machine is now running your job".
2. **Escrow holds on `claimed`, never on `granted`.** `hold_escrow_on_claim`
   still has no caller — wiring it is part of this work.
3. **Settlement is on ACCEPTED work, not elapsed time.** This is the product's
   distinctive property. The UI should make it legible, not bury it.

### 3.2 The marketplace UI

A new console section. Minimum viable, in order:

- **Credits** — balance, spendable vs held, and the ledger as an append-only
  list. Double-entry: every row has a counterparty. Do not collapse it into a
  single "balance went up" feed; the counterparty is the point.
- **Listings** — machines offered, their capability class, ask price, and
  record. `is_donated(ask_zc_per_hour)` and `price_label()` already exist; use
  them rather than formatting prices yourself.
- **Price comparison** — the pitch. Quotes carry `captured_at` and `source`;
  **render both**. `is_stale()` exists — a stale price must look stale.

**ZC and USD retain separate source settlement fields.** The fixed 1 ZC = $1
USD equivalent may appear on wallet, credits, and marketplace surfaces only;
the API supplies that display value and the scheduler uses it for comparison.
The job routing card keeps its original ZC and USD columns and does not render
a combined cash total.

## 4. §B — Finish the surfaces that exist

**The routing card is on the job page's Placement tab and has never been looked
at.** First task of this whole plan: open `flashml-dev-web`, sign in, submit
one of the `Zolli-Labs/flashml-demo-suite` branches, and *look*. Today's
lesson, three times over, was that "tests pass" did not mean "works".

- **Metrics page** (`(console)/metrics`) — audit whether it renders real
  numbers or placeholders. Stage 8 was "metrics computed from the ledger"; the
  events exist. If it is stubbed, say so rather than leaving it ambiguous.
- **Overview / workspace overview** — should answer "what is my fleet doing
  right now" in one screen. Machines online, jobs running, credits.
- **Federated jobs list an empty Artifacts card by design** — per-round keys do
  not compose with the fetch route. Render an explanation, not an empty box.
- **Submit flow** — pre-submit routing preview is deliberately *not* built (see
  the shipped-and-verified note, §3). Do not attempt it without adding a
  compile-only path; the cheap version lies about task counts.

## 5. Conventions that are not negotiable

**Decision layer in `lib/`, markup in `components/`.** vitest collects
`lib/**/*.test.ts`. Components stay markup-only so copy and state logic are
testable without rendering. `lib/job-routing.ts`, `lib/job-artifacts.ts` and
`lib/task-checkpoints.ts` are the pattern — read one before writing a new one.

**Honesty rules — these outrank polish and are house style, not taste:**

- Never render a number, name, size or timestamp the API did not return. No
  placeholders, no sample data, no optimistic guesses.
- **A failed read must never render as an empty result.** "Could not read this"
  and "there is nothing" are different sentences. Every panel needs at least
  four states: loading, present, empty, unreadable.
- `null` means *not observed*, never `0`. `basisLabel(null, n)` → "not
  observed".
- **No fixture-shaped or credential-shaped literals anywhere in the repo.** The
  owner has rejected these explicitly. Build test fixtures at runtime.

**Verification gate for every change:** `npm test`, `npx tsc --noEmit`,
`npm run lint`, `npm run build` (needs `flashml-cloud/.env.dev` sourced —
`next.config.ts` hard-fails without `NEXT_PUBLIC_CLOUD_API`). Baselines to beat,
not regress — measured 2026-08-12 ~22:00: **web 866 passed / 52 files**,
**api 2633 passed, 2 skipped, 3 deselected, 1 xfailed**.

**Measure them yourself before you start, including these.** The figures this
replaced (web 767/46, api 2177) were stale by roughly 100 and 450 tests. A
baseline that is too low certifies a regression as an improvement, which is
strictly worse than having no baseline at all.

## 6. §C — After the deadline

- Pre-submit routing preview, once a compile-only path exists that resolves
  datasets honestly.
- A per-round artifact shape so federated runs can list outputs.
- `hold_escrow_on_claim` wired end to end with a UI that shows holds releasing.

## 7. What to test against

Dev: `https://flashml-dev-api.onrender.com` · console
`https://flashml-dev-web.onrender.com`. Demo workloads:
`Zolli-Labs/flashml-demo-suite`, branches `train` / `hpo` / `federated` /
`evaluate` — submit via `POST /v1alpha1/jobs/from-repo` with
`{"repo": "...", "ref": "<branch>"}`. Four succeeded runs already exist on dev
with real artifacts, checkpoints and OSS mirrors, so the pages have real data to
render rather than needing fixtures.
