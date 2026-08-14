# Console density audit (UI-1a)

**Date:** 2026-08-13 · **Branch:** `agent/zolli-frontend-motion-system` (worktree
`.worktrees/frontend-motion-system`) · **Parent:** spec §3 UI-1a ← owner
direction 2026-08-13 ("too wordy, unorganized, numbers missing"),
`2026-08-12-console-ui-plan.md` §5.

Read-and-measure only. No app code touched. This is the brief UI-1b (the
refactor) executes from.

## 0. Method

Every route under `apps/web/app/(console)/` was read in full, along with every
child component it renders that carries numeric data (cards, panels, tables),
cross-referenced against the exact response shapes in `lib/cloud-api.ts`. A
"figure" is one distinct *kind* of number the API can supply — a table of N
rows each showing a price counts as one figure kind, not N. "Figures
available" means a field exists in a response this page already fetches and
is never rendered anywhere in that page's component tree; it was confirmed
by grep, not assumed, everywhere a claim names a specific unused field.

**Two pages don't fit the four-category verdict cleanly:** `activate` and
`workspaces` are pure action forms — a device-code wizard and a
create-workspace form — with no list or summary data available to lead with
by design, not by omission. They're marked **form, no numbers by design**
rather than forced into "prose by nature (docs)," which is reserved for the
two pages that are genuinely reference content.

## 1. Harness run

```
cd apps/web && PREVIEW_OUT=.preview npx vitest run --config preview/vitest.preview.config.ts
```

```
Test Files  5 passed (5)
     Tests  7 passed (7)
  Duration  2.40s
```

Emitted into `.preview/`: `console-primitives.html` (183,703 bytes),
`console-shell.html` (174,906 bytes), `console-pages.html` (172,413 bytes),
`account-panels.html` (178,867 bytes), `workspace-tables.html` (176,378
bytes). No `browse` binary was available in this environment (`command -v
browse` → not found), so these were not screenshotted — the paths above are
the evidence. These render shared **primitives** (StatePanel, tables, the
account/workspace component galleries), not full authenticated pages: a full
page needs a Supabase session no agent can obtain, which is expected and
matches the plan's own caveat.

## 2. Density table

**words** — approximate rendered prose (sentences), labels/headings/table
columns excluded, estimated from the concurrently-visible (not every
conditional branch) state. **figures shown** — distinct numeric figure kinds
the page renders from the API. **figures available** — numeric fields in the
API responses this page fetches that are never rendered, confirmed by
reading (or grepping) the consuming component. **verdict** — one of the four
categories, or the two form exceptions noted in §0.

| Route | Lines | Words | Figures shown | Figures available (unshown) | Layout | Verdict |
|---|---:|---:|---:|---:|---|---|
| `w/[poolId]/overview` | 165 | ~30 | 6 | 3 — credits (`spendable_zc`, `held_zc`), job-state breakdown beyond active/finished | tiles | numbers-first already |
| `w/[poolId]/jobs` | 245 | ~20 | 5 | ~3 — succeeded/partial/cancelled not broken into filter chips | table | has numbers, buried |
| `w/[poolId]/machines` | 76 | ~50 | 2 (both from shared header) | 2 — no fleet-size total or status breakdown of its own | table | has numbers, buried |
| `w/[poolId]/people` | 80 | ~21 | 2 (+ per-row `machine_count`/`machines_online`) | 1 — no team-wide machine rollup | table | has numbers, buried |
| `w/[poolId]/settings` | 86 | ~65 | 2–3 (invite `uses_remaining` buried in a sentence) | ~1 — invite state not a distinct number | form/prose | has numbers, buried |
| `w/[poolId]/submit` | 321 | ~65 | 0–1 (findings count, post-submit only) | 1 — online-machine ratio only shown as binary "0 online" warning | form | prose-heavy, numbers available |
| `jobs/[jobId]` | 1015 (+~1900 in cards) | ~700 | ~38 across 3 tabs | 8 kinds, several whole arrays — see §3.1 | mixed: tiles+table+diagram, heavy prose framing | has numbers, buried |
| `metrics` | 288 | ~90 | 12 — full `PlatformMetrics` surface | 0 | tiles | numbers-first already |
| `market` | 156 (+617 in panels) | ~100 | 10+ — 4 wallet tiles, ledger, activity strip, match cards | 2 — `lifetime.granted_zc`/`refunded_zc` likely untiled (only 4 tile icons exist) | tiles+table | numbers-first already |
| `market/listings` | 165 (+793 in panel) | ~65 | 8–10 — header trio, book rows, market hint | ~1 — `resolved_n` not separately shown beyond the rate | tiles+form+table | numbers-first already |
| `market/prices` | 66 (+421 in panel) | ~55 | 10+ — board strip, full ticker table, history, external quotes | 0 — full `PricesView` surface covered | table+tiles | numbers-first already |
| `workspaces` | 142 | ~40 | 0 | 0 — creation form, nothing to list by design | form | form, no numbers by design |
| `account` | 121 (+445 in panels) | ~100 | 6 — storage used/limit/%, contributions total × 2 + per-machine | 0 — both `AccountStorage` and `MyContributions` fully covered | form+tiles | numbers-first already |
| `account/machines` | 339 | ~20 | 2 (Online now / Enrolled) | 1 — revoked count never tallied | table+tiles | numbers-first already |
| `account/cli` | 279 | ~25 | 1 (Active) | 1 — revoked count never tallied | table+tiles | numbers-first already |
| `account/github` | 222 | ~30 | 0 | 1 — `installations.length` never shown as a headline count | prose+list | has numbers, buried |
| `admin/requests` | 664 | ~15 | 0 aggregate (rich per-row: `spendable_zc`/`escrow_zc`/`requested_zc`) | 3 — no pending-count badge on either tab, no total ZC requested across the queue | cards | has numbers, buried |
| `activate` | 389 | ~15–50 (state-dependent) | 0 | 0 — nothing quantitative in a device-approval confirmation | form (wizard) | form, no numbers by design |
| `docs` | 314 | ~500 | 0 | 0 — calls no API at all (confirmed: only `cloudApiBase()` for a copy-paste URL) | prose | prose by nature (docs) |
| `how-it-works` | 389 | ~500 | 0 | 0 — calls no API at all (confirmed, static server component) | prose+diagram | prose by nature (docs) |

**Correction to the plan's own inventory:** the plan named `account/*` as a
prose-heavy target alongside `settings` and `docs`. Read in full — including
`StoragePanel` and `ContributionsPanel`, which the plan's recon apparently
didn't open — `account` is already one of the better numbers-first pages in
the console: it fully surfaces `AccountStorage` and `MyContributions` with
zero unshown fields. `docs` and `how-it-works`, by contrast, are correctly
prose-by-nature: both were confirmed (not assumed) to call no API endpoint
at all.

## 3. Ranked worst-first, with prescriptions

Because most of this console's numeric surfaces turned out to already be
wired — `metrics`, `market`, `market/prices`, `market/listings`, `account`
all fully cover their API responses — a strict `(figures available − figures
shown)` sort produces a misleading order: it would rank `account/github`
(one trivial unshown count) above `jobs/[jobId]` (eight unshown figure
*kinds*, several of them whole arrays). The ranking below weights by the
substantiveness of the confirmed gap and the page's prominence, with word
count as the secondary signal the plan asked for.

1. **`jobs/[jobId]`** — lead the Placement tab's routing table with the
   per-machine `candidates` list (`price_zc_per_hour`, `reliability_tier`,
   `acceptance_rate`) that `RoutingCard`/`lib/job-routing.ts` never reference
   at all (`grep candidates lib/job-routing.ts components/jobs/RoutingCard.tsx`
   → no hits) — every venue-level number is shown, but the machine-level
   pricing behind it is dropped entirely.
2. **`w/[poolId]/overview`** — lead with a 4th Stat tile for `getCredits()`'s
   `spendable_zc`/`held_zc`. This is the one item on the console-ui-plan §4
   checklist ("Machines online, jobs running, credits") this page never
   shipped — the other two are already tiles.

   *Landed 2026-08-14 as a 4th cell in `components/shared/StatStrip`, not a
   4th tile: the instrument-panel register that arrived on `develop` after
   this audit was written made the strip, not a tile grid, the shape for a
   page-leading set of counts. The figure and its honesty rules are
   unchanged — loading renders a skeleton, an unreadable balance renders the
   strip's em-dash carrying the retry, and neither renders a fabricated 0.*
3. **`admin/requests`** — lead each tab trigger with its pending count
   ("Access (3)" / "Credits (2)") and sum `requested_zc` across the visible
   credit queue. This page has the *lowest* word count in the console (~15)
   and *zero* aggregate figures — its problem is a missing rollup layer, not
   prose.
4. **`w/[poolId]/machines`** — lead the tab with a fleet-size Stat row
   (total / online / pending / revoked). The Machines tab currently shows
   zero numbers of its own; every figure a viewer sees on it comes from the
   shared `WorkspaceHeader`.
5. **`w/[poolId]/submit`** — replace the binary "0 Machines online" warning
   (only rendered when the count is exactly zero) with the real ratio, e.g.
   "2 of 5 machines online," always.
6. **`w/[poolId]/settings`** — pull the invite's `uses_remaining` out of
   `formatInviteState`'s prose sentence into its own Stat.
7. **`w/[poolId]/people`** — add a workspace-wide "N machines across your
   team" rollup above `MemberTable`; the per-member `machine_count` /
   `machines_online` columns already exist but nothing sums them.
8. **`account/github`** — show `installations.length` as a small headline
   count once it's non-zero. Lowest priority — most accounts have 0–2
   installations, so the number carries little weight — kept on the list
   only because it's a confirmed, real gap.

### 3.1 `jobs/[jobId]`'s eight unshown figure kinds, source-verified

- `PreviewCandidate[]` (`candidates`, from `previewJobPlans`) — the whole
  per-machine pricing/reliability array. `RoutingCard` and
  `lib/job-routing.ts` never reference it.
- `CheckpointManifest.checkpoint_duration_s` — how long the last checkpoint
  write took. Not in `lib/task-checkpoints.ts`'s output shape.
- `CheckpointManifest.world_size` / `compatible_world_sizes` — resume
  compatibility. Same absence.
- `TradeoffOwned.reachable_machines` and `.ask_zc_per_hour` — these *are*
  carried into `TradeoffPanel` by `lib/job-tradeoff.ts` (`reachableMachines`,
  `askZcPerHour`) but `TradeoffCard.tsx` never renders either
  (`grep reachableMachines\|askZcPerHour components/jobs/TradeoffCard.tsx` →
  no hits) — the data survives the whole pipeline and is dropped at the last
  step.
- Task-state aggregate on the Placement tab — the task table lists every row
  individually; nothing sums "N pending / N leased / N completed / N failed."
- Event-type breakdown on the Ledger tab — `{events.length} events` is the
  only aggregate; no count by event type (ACCEPTED/FAILED/LOST/…).

## 4. The missing shared primitive

There is no reusable `StatTile`. Confirmed by grep, not the two examples the
plan cited — **seven** separate hand-written `function Stat(...)` /
`CountTile(...)` / `HeaderStat(...)` implementations exist:

```
app/(console)/metrics/page.tsx              CountTile
app/(console)/market/listings/page.tsx      HeaderStat
app/(console)/w/[poolId]/overview/page.tsx  Stat
components/jobs/RoundProgress.tsx           Stat
components/jobs/TradeoffCard.tsx            Stat
components/jobs/RoutingCard.tsx             Stat
components/share/JobRecovery.tsx            Stat
```

— plus **twelve** more files that skip the local-function step entirely and
write `.metric-value` / `.metric-lg` + `.label-caps` divs inline: `account/cli`,
`account/machines`, `jobs/[jobId]` (page-level `Stat`, counted above, but the
page also inlines the pattern directly in `ProgressBar`), `metrics`,
`w/[poolId]/overview`, `ContributionsPanel`, `StoragePanel`,
`CheckpointsCard`, `RoundProgress`, `RoutingCard`, `TradeoffCard`,
`market/CreditsPanel`, `market/PricesPanel`, `components/jobs/SandboxLifecycle.tsx`.

Every one of these renders the identical shape — a large mono figure over a
caps-label caption, sometimes with a hint line — against the same two CSS
classes. **Pages that would adopt a shared `StatTile`:** every row of the
density table marked `tiles` or `tiles+*` — `metrics`, `w/[poolId]/overview`,
`account`, `account/machines`, `account/cli`, `market`, `market/listings`,
`market/prices` — plus the `jobs/[jobId]` cards (`RoutingCard`,
`TradeoffCard`, `RoundProgress`) and `admin/requests` once it gains the
queue-count tiles prescribed in §3.

## 5. Conventions this refactor is bound by

Quoted verbatim from `docs/superpowers/specs/2026-08-12-console-ui-plan.md`
§5, on this worktree:

> ## 5. Conventions that are not negotiable
>
> **Decision layer in `lib/`, markup in `components/`.** vitest collects
> `lib/**/*.test.ts`. Components stay markup-only so copy and state logic are
> testable without rendering. `lib/job-routing.ts`, `lib/job-artifacts.ts` and
> `lib/task-checkpoints.ts` are the pattern — read one before writing a new one.
>
> **Honesty rules — these outrank polish and are house style, not taste:**
>
> - Never render a number, name, size or timestamp the API did not return. No
>   placeholders, no sample data, no optimistic guesses.
> - **A failed read must never render as an empty result.** "Could not read this"
>   and "there is nothing" are different sentences. Every panel needs at least
>   four states: loading, present, empty, unreadable.
> - `null` means *not observed*, never `0`. `basisLabel(null, n)` → "not
>   observed".
> - **No fixture-shaped or credential-shaped literals anywhere in the repo.** The
>   owner has rejected these explicitly. Build test fixtures at runtime.
>
> **Verification gate for every change:** `npm test`, `npx tsc --noEmit`,
> `npm run lint`, `npm run build` (needs `flashml-cloud/.env.dev` sourced —
> `next.config.ts` hard-fails without `NEXT_PUBLIC_CLOUD_API`). Baselines to beat,
> not regress — measured 2026-08-12 ~22:00: **web 866 passed / 52 files**,
> **api 2633 passed, 2 skipped, 3 deselected, 1 xfailed**.
>
> **Measure them yourself before you start, including these.** The figures this
> replaced (web 767/46, api 2177) were stale by roughly 100 and 450 tests. A
> baseline that is too low certifies a regression as an improvement, which is
> strictly worse than having no baseline at all. Run the suite, write what it
> said, and date it.
>
> **And state the scope beside the number.** The gate above cannot be run as one
> command: `npm run build` needs `.env.dev` sourced, and sourcing it makes
> `middleware.test.ts` fail, because that test asserts the signed-out contract
> when Supabase config is *absent*. Run tests, typecheck and lint with no env;
> run the build in its own subshell. "tests, tsc, lint green; build **not run**"
> is four extra words and it is the difference between a verified claim and a
> claim about a smaller scope than the reader assumes.

Anything UI-1b adds — a shared `StatTile`, the credits tile on `overview`,
the queue-count badges on `admin/requests` — must satisfy the four-state rule
and the `null` ≠ `0` rule exactly as the existing tiles do (see `metrics`'s
`ReliabilityCard`, which is the reference implementation for "unmeasured,
rendered honestly, never as a fabricated zero").

## 6. Before/after measurement protocol for UI-1b

For every page UI-1b touches, report, using this audit's own method so the
numbers are comparable:

1. **Words per page** — same convention as §2 (rendered prose, headings/labels
   excluded, concurrently-visible state only). Report before → after.
2. **Figures shown per page** — same convention as §2 (distinct figure
   *kinds*, not row counts). Report before → after, and name which
   previously-available field each new figure surfaces.
3. **Figures available (unshown) per page** — should trend toward 0 for
   every page marked `has numbers, buried` in §2; report the count that's
   left and why, if any remains (e.g., a field genuinely not worth a tile).
4. **No new gaps** — re-run this audit's grep checks
   (`grep -rl "metric-value\|metric-lg"`, per-field greps like the ones in
   §3.1) against the *new* component set once `StatTile` lands, to confirm
   the refactor collapsed the seven-plus duplicate implementations into one
   rather than adding an eighth.
5. **The verification gate** — `npm test`, `npx tsc --noEmit`, `npm run lint`
   with no env sourced; `npm run build` in its own subshell with
   `.env.dev` sourced. Baselines to beat: **web 866 passed / 52 files**,
   **api 2633 passed / 2 skipped / 3 deselected / 1 xfailed** — re-measure
   immediately before UI-1b starts, per §5's own instruction, since this
   number moves.
6. **Honesty spot-check** — for each page touched, confirm a `null` API
   field still renders as "not observed" (or equivalent), never as `0` or a
   blank tile, and that a failed read still renders as `unreadable`, not as
   `empty`.
