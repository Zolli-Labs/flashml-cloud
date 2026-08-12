# Console audit findings — 2026-08-12

The plan (2026-08-12-console-ui-plan.md §B) requires the audit in writing
before anything changes. This is that writing, plus what the §A and §B
work found along the way. The owner will verify every visual claim in a
browser; nothing below is "proven live" unless it says so.

## 1. What shipped with this audit (§A)

- Seven marketplace routes in `apps/api/.../app.py`, pytest-first
  (`tests/test_market_routes.py`, 33 tests): `/v1alpha1/credits`,
  `/v1alpha1/credits/ledger`, `/v1alpha1/market/listings` (GET/POST),
  `/v1alpha1/market/listings/{id}` (DELETE), `/v1alpha1/market/matches`,
  `/v1alpha1/prices`.
- `hold_escrow_on_claim` wired at the one hop where a lease first exists
  (`POST /v1alpha1/leases/claim`), best-effort like the attempt row beside
  it. Settlement and refund were already wired in `db.py`
  (`_close_out_attempt_money`); the hold was the only caller-less piece.
- Two new repository functions in `marketplace.py`:
  `ledger_movements_for_owner` (movements with ALL legs, counterparty
  included, cursor = oldest owner leg so a page never re-shows a two-legged
  movement) and `match_for_claim` (granted-match lookup, the mirror of
  `db._live_match_for_attempt` one state earlier).
- Console: `lib/market-credits.ts`, `lib/market-listings.ts`,
  `lib/market-prices.ts` (decisions, vitest-covered) + markup-only
  `components/market/*` + `/market`, `/market/listings`, `/market/prices`
  pages + a Market section in the rail.

Invariants held: match states render verbatim (granted says "entitled, no
money moved"); ZC and vendor currencies are adjacent columns with no
combining function in any lib; unproven hosts render "unproven", never 0;
a failed read renders unreadable, never empty.

## 2. §B findings, page by page

- **Job detail, federated Artifacts**: was an empty box saying "this job
  finished without writing any artifacts" about runs whose output lives
  under per-round jobs. FIXED: `lib/job-artifacts.ts` takes `federated`
  and swaps in `FEDERATED_ARTIFACTS_MESSAGE`; tested.
- **Metrics**: audited, NOT stubbed. Every tile is a real measurement from
  `GET /me/metrics`; goodput/MTTR/MTTD render as explicit "not measured
  yet" cards (dashed border, explanatory sentence), never as 0 or a bare
  dash. No change needed; the page already obeys the four-state doctrine.
- **Overview**: renders real fleet state — per-machine online derived
  from `last_seen_at` heartbeats (`lib/machine-scope.ts`), member/online
  summaries, job states. Not a placeholder. No change needed.
- **Landing first paint (observation, unproven)**: the onrender landing
  captured as a blank dark field while its DOM was fully present
  (hero copy, ledger, FAQ all in the accessibility tree). Likely the
  scroll-reveal animation never firing for a window that was never
  on-screen; could not be confirmed without driving the owner's browser,
  which was stood down. Owner to check: load the landing cold in a visible
  window and confirm the hero paints without scrolling.

## 3. Style audit — console vs landing

Method: greped every console and landing component for colour literals
(hex / rgb / hsl) and checked every class on the new market pages against
the `--color-*` / `--z-*` inventory in `app/globals.css`.

- **Console (`app/(console)`, `components/shell|jobs|workspace|market`)**:
  zero raw colour literals. Everything routes through semantic tokens
  (`bg-surface`, `border-border`, `text-muted-foreground`, `label-caps`,
  `title`, `metric-value`, `bg-primary`, `text-destructive`, …). The new
  market pages use only those classes, so they inherit the landing's dark
  token theme unchanged.
- **Landing/marketing components carry the only raw hexes**, and each is
  either an exact token value written as a literal or a near-miss that
  deserves a token of its own:

  | literal | where | nearest token |
  |---|---|---|
  | `#111416` | SystemModules | `--z-surface` (exact) |
  | `#f3f1ec` | ClosingCta text | `--z-text` (exact) |
  | `#0f1213` | ArchitectureSignal | between `--z-bg` `#0b0d0e` and `--z-surface` — candidate token `--z-surface-sunk` |
  | `#1b1f20`, `#25292a` | ClosingCta/ProfessionalServices hover | near `--z-surface-hover` `#1d2125` — hover literals, candidate tokens |
  | `#f2efe6` | Navbar sand button | near `--z-text` but warmer — candidate token `--z-sand` |
  | `rgb(243 107 50 / …)` | Hero, InformationPage | `--z-orange` `#f36b32` with alpha — candidate `--z-orange-*` alpha tokens |

  These are pre-existing on the REFERENCE surface, so they were left
  untouched: §B's mandate is that the console match the landing, and it
  does, via the shared tokens. If the landing literals are ever tokenized,
  add the candidates above to `globals.css` first — never inline the value
  a second time.

## 4. Known limits (honest list)

- No bids route and no buyer-side "buy" flow: §3.1's seven routes surface
  the market; creating bids is post-deadline (§C).
- The ledger page pages 25 movements at a time; the counterparty shows as
  a role ("the buyer's escrow"), never as another account's identity.
- `GET /market/listings` reports `resolved_n` from the hardware-class
  approximation `db.acceptance_rate_rows` documents; the work-class split
  stays unreachable until `attempts` carries a class column.
