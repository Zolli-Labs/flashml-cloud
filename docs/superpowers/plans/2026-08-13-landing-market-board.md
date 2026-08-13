# Landing page: market board + 7-section restructure

Approved in chat 2026-08-13 (bounded path; no separate spec). Owner's requirements:
shorter scroll, cut jargon sections, one clean OS/runtime statement, a GPU price
board with trend, less repetitive motion, hero unpinned and auto-advancing —
**and keep the existing landing visual style.**

## Global Constraints

- **Worktree only.** All work happens in
  `/Users/phongcao/Work/Zolli-Labs/flashml-cloud/.worktrees/landing-market-board`.
  Never run git anywhere else. Never run repository-scoped git commands
  (`stash`, `checkout <ref>`, `reset`, `clean`, `restore`, `rebase`). Commit
  with the pathspec on the commit itself: `git commit -m "..." -- <paths>`.
- **Test invocation.** API: `cd flashml-cloud/apps/api && .venv/bin/python -m pytest -q`
  (never the `.venv/bin/pytest` script — its shebang points at another checkout).
  Web: `cd flashml-cloud/apps/web && npm test`. Typecheck: `npx tsc --noEmit` in apps/web.
- **Price honesty discipline** (matches `prices.py` / `lib/market-prices.ts`):
  vendor amounts stay the vendor's decimal **strings**, never parsed-and-reformatted
  floats; the staleness verdict and age come from the API; a venue/GPU with no
  quote is an **omitted row, never a zero**; every quote renders with
  provenance (`captured_at`); nothing ever says "live".
- **Keep the landing style.** Mono uppercase eyebrows (`font-mono text-[11px]
  uppercase tracking-[0.13em] text-brand-foreground`), clamp-based headlines with a
  `text-muted-foreground` second phrase, hairline `--z-border-strong` dividers,
  `max-w-[1240px] px-5 sm:px-6` containers, surface alternation via the existing
  `landing-surface-{dark,light,sand,orange}` wrappers in `page.tsx`, prices in
  `font-mono tabular-nums`. New sections must be indistinguishable in idiom from
  the existing ones.
- **Endpoint JSON contract** (Tasks 1 and 3 both code to THIS, not to each other):

  `GET /v1alpha1/public/prices` → 200, no auth:

  ```json
  {
    "generated_at": "2026-08-13T12:00:00Z",
    "rows": [
      {
        "gpu": "RTX 4090",
        "sku": "NVIDIA GeForce RTX 4090",
        "provider": "runpod",
        "tier": "community",
        "amount": "0.34",
        "currency": "USD",
        "unit": "gpu-hour",
        "captured_at": "2026-08-13T12:00:00Z",
        "age_seconds": 1234.0,
        "stale": false,
        "trend": {
          "direction": "down",
          "pct": "-5.6",
          "previous_amount": "0.36",
          "previous_captured_at": "2026-08-12T12:00:00Z"
        }
      }
    ]
  }
  ```

  `trend.direction` ∈ `"up" | "down" | "flat" | "new"`. `"new"` (single
  observation) carries no other trend fields. `"flat"` means exactly equal
  decimal amounts and carries `pct: "0.0"` plus the previous fields. `pct` is
  computed with `Decimal` (never float), one decimal place, signed for up/down.
  Rows are ordered by the curated list order. Empty table → `rows: []`, still 200.
- **No subagents.** Implementers never dispatch agents or reviewers.

## Task 1: Public prices endpoint (API)

Files: `flashml-cloud/apps/api/flashml_cloud_api/prices.py`,
`flashml-cloud/apps/api/flashml_cloud_api/app.py`, new
`flashml-cloud/apps/api/tests/test_public_prices.py`.

1. In `prices.py`, add a landing-view function, e.g.
   `landing_rows(db, now, curated) -> list[dict]`. `curated` is an ordered
   tuple of `(display_label, sku_matcher)` pairs defined in `prices.py` as
   `LANDING_GPUS`. Pick **exact SKU strings** from migration `0019`'s seed data
   (read the migration): target display labels `H100 80GB`, `A100 80GB`,
   `A100 40GB`, `RTX 5090`, `RTX 4090`, `RTX 3090`, plus one more popular GPU
   present in the seed (e.g. L40S or RTX 5080 — pick whichever the seed has).
   For each GPU: take the latest quote for the SKU preferring tier
   `community`, falling back to `secure`; if neither exists, omit the row.
2. Trend: compare the latest quote against the **previous distinct
   `captured_at`** observation with the same (provider, sku, tier, unit,
   currency) key. Reuse `quote_history` if it fits; otherwise a direct query
   on the same table is fine. Equal amounts → `flat`; one observation → `new`.
   All arithmetic in `Decimal`; `pct` per the contract above.
3. In `app.py`, add `GET /v1alpha1/public/prices` **without**
   `Depends(current_user)` (keep `db_conn`). Module-level TTL cache: dict of
   `{value, expires}` on `time.monotonic()`, 60 seconds, with a small reset
   hook or injectable TTL so tests aren't flaky. Docstring must state why the
   route is public (curated scraped vendor catalogue data only — no user, pool,
   wallet, or ZC-ask data) and name the cache as the DB-protection measure.
   Serve **only** vendor quote data — do NOT include the ZC ladder, asks, or
   marketplace board data in this response.
4. Tests (`test_public_prices.py`), written first: (a) 200 with **no**
   Authorization header while `/v1alpha1/prices` without auth is a 401/403
   contrast; (b) row shape matches the contract exactly for a seeded
   two-observation SKU including up/down/flat `pct` strings; (c) single
   observation → `direction: "new"`; (d) empty table → `{"rows": []}`;
   (e) tier preference: community preferred, secure fallback; absent → omitted;
   (f) amounts in the response are the exact strings inserted, unrounded.
   Follow the existing test-suite conventions in `tests/test_prices.py` for
   fixtures/DB setup.

## Task 2: Hero auto-advances on a loop (web)

Files: `flashml-cloud/apps/web/components/landing/coordinator-map/useMapStory.ts`,
`flashml-cloud/apps/web/components/landing/Hero.tsx` (only if it consumes the
removed scroll plumbing). Do **not** touch `app/(marketing)/page.tsx` — Task 4
removes the `220svh` scroll track there.

1. Read `useMapStory.ts` fully first. It currently has two drivers: a
   scroll-track reader and a timer fallback. Remove the scroll driver
   entirely; the timer becomes the only driver.
2. Make the timer **loop forever**: after the final beat, hold ~3–4s on the
   completed state, then restart from the first beat. No
   `requestAnimationFrame`/scroll listeners may remain.
3. Preserve the existing `prefers-reduced-motion` behavior (static `resumed`
   state, no timer scheduled).
4. If any test covers `useMapStory` or Hero, update it to the new contract;
   run the web suite. If nothing covers the loop transition, add a small test
   for the beat-advance/loop logic only if the hook's logic is extractable
   without a DOM harness — otherwise state that in the report.

## Task 3: Market board lib + PriceBoard section (web)

New files only: `flashml-cloud/apps/web/lib/landing/market-board.ts`,
`flashml-cloud/apps/web/lib/landing/market-board.test.ts`,
`flashml-cloud/apps/web/components/landing/PriceBoard.tsx`.

1. `lib/landing/market-board.ts`: TypeScript types mirroring the endpoint
   contract verbatim; pure helpers the component renders from:
   `trendGlyph(direction)` → `▲ | ▼ | –` (`new` → `NEW` text chip);
   `trendToneClass(direction)` → up: `text-[var(--z-warning)]`, down:
   `text-[var(--z-healthy)]` (a falling price is good news for buyers — state
   this in a comment), flat/new: `text-muted-foreground`; a
   `capturedLabel(row)` that reuses `ageLabel` from `lib/market-prices.ts`.
   Also `fetchLandingPrices()`: server-side fetch of
   `${apiBase}/v1alpha1/public/prices` with `next: { revalidate: 300 }`,
   resolving `apiBase` the same way the rest of the app does (read
   `lib/cloud-api.ts` for the env var; fall back to `http://localhost:8000`).
   Returns `null` on any fetch/parse failure — never throws.
2. `PriceBoard.tsx`: **async server component**, named export `PriceBoard`,
   no props, rendering a `<section id="market">` in the exact landing idiom
   (see Global Constraints). Content: eyebrow "The compute market"; a
   clamp headline in the house style, e.g. `Today's GPU prices.` with a muted
   qualifier phrase like `Observed, not promised.`; a table/rows of the
   curated GPUs — GPU label, `font-mono tabular-nums` price + `/ gpu-hr`,
   trend glyph + pct with tone class, tier, and an "observed <ageLabel>"
   provenance cell. Wide content scrolls inside its own container on mobile.
   Footer: the two demand/supply lines ("One request puts every source to
   work — your machines, community hosts, RunPod, and Alibaba Cloud compete
   on price." / "Idle machines already cost you. Connect them and earn when
   they complete useful work.") plus the small honesty print "Early testing
   uses Zolli credits. Cash payout is not live."
3. States: `fetchLandingPrices()` null or `rows: []` → render the section
   header with one quiet muted line "No market observations yet." — never an
   error, never zeros, never invented numbers.
4. Tests: lib helpers only (trend glyph/tone mapping, captured label, fetch
   failure → null via a stubbed `fetch`). No DOM render test required.

## Task 4: 7-section page restructure + motion simplification (web)

Files: `app/(marketing)/page.tsx`, new `components/landing/HowItWorks.tsx`,
new `components/landing/PlatformStrip.tsx`, edits to `RecoveryDemo.tsx`,
`Faq.tsx`, `motion/SectionReveal.tsx`; deletions per step 6.

1. New page order, keeping the surface-wrapper pattern and alternating
   surfaces (no two adjacent sections share one; `ClosingCta` stays orange):
   Hero (dark, **scroll track and sticky wrapper removed** — plain `<Hero />`),
   PriceBoard (sand), HowItWorks (dark), RecoveryDemo (light),
   PlatformStrip (sand), ProfessionalServices (light), Faq +
   ClosingCta (unchanged surfaces).
2. `HowItWorks.tsx`: one section absorbing SimpleJourney's three steps
   (reuse its copy) plus a compact single row for the module facts worth
   keeping from SystemModules (flashnode allowlisted-image line; DDP/FSDP
   line). No pinned/scroll-triggered animation. SystemJourney and
   WorkflowScene are cut entirely, not merged.
3. `PlatformStrip.tsx`: the one-statement replacement for PlatformSupport.
   Eyebrow + one clamp headline ("Bring the machines you already use." may
   survive here — it is good copy), then a single strip: OS badges
   Linux / Windows / macOS and runtime chips sourced from the existing data in
   `lib/landing/platform.ts` (do not invent support claims; if Windows is
   Preview in `HOST_SUPPORT`, its badge carries a small `preview` tag — that
   is the only status wording allowed). One sentence beneath. **Cut**: the
   Proven/Preview card grid, RuntimeSupportExplorer, "Network expansion",
   MachineCompatibilityCheck.
4. `RecoveryDemo.tsx`: append EvidenceBand's four counted stats as a footer
   strip inside the section, preserving `EvidenceBand.tsx`'s honesty comment
   block (move the comment with the data — it is load-bearing history).
5. `Faq.tsx`: add one entry carrying the honest constraint line ("Zolli is
   best for work that can be divided or resumed. It is not currently designed
   for tightly synchronized training…") from the cut PlatformSupport copy.
6. Delete now-unused files: `MarketStory.tsx`, `SimpleJourney.tsx`,
   `SystemModules.tsx`, `SystemJourney.tsx`, `WorkflowScene.tsx`,
   `WorkloadFit.tsx`, `WorkloadRows.tsx`, `EvidenceBand.tsx`,
   `RuntimeSupportExplorer.tsx`, `MachineCompatibilityCheck.tsx`,
   `HeroMarketSwitch.tsx` (if now unused). **Before each deletion**, grep the
   whole web app (including `preview/` and any `*.render.tsx` harness files
   and tests) for imports; a file still imported anywhere is not deleted —
   update or note it instead.
7. `SectionReveal.tsx`: replace the line-draw/wipe treatment with one subtle
   fade-up (opacity + ~8px translate, once, ≤400ms, honoring
   `prefers-reduced-motion`). Keep the exported component API compatible so
   call sites keep working; remove now-dead line props from call sites you
   already touch, and leave a props-accepted-but-unused shim only where a
   call site is out of this task's file list.
8. Update any tests that import deleted components; run the web suite and
   `npx tsc --noEmit`.

## Task 5: Integration — suites, build, PROGRESS.md

1. Full API suite, full web suite, `npx tsc --noEmit`, and `npm run lint`
   (if configured) — all green in the worktree.
2. `npm run build` in `apps/web` must succeed. The marketing page must not
   fail the build when the API is unreachable (fetch returns null → quiet
   state; verify by building with no API running).
3. Append a dated entry to `PROGRESS.md` following the LOGGING PROTOCOL at
   the top of that file: landing restructure 12→7 sections, new public
   prices endpoint, hero unpinned to auto-advance.
