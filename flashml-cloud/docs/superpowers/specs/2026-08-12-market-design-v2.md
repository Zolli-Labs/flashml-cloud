# Market design v2 — from text dumps to a market terminal

Owner review of v1 (screenshots 2026-08-12): the pages list everything as
plain text and read as fake. Credits is two numbers and a sentence; the
ledger is one line. Listings is a bare form with no machine context, no
specs, no price guidance — "a fake market where people put whatever."
Prices is two static columns nobody wants to read; the ask is a **stock
market for compute**: per-class tickers with movement, even when the data
is minimal.

This spec is the redesign. It keeps the v1 doctrine — it is not being
relaxed, only dressed better:

- every number, name, size, timestamp still comes from the API;
- null is never 0; unproven is a sentence; a failed read is unreadable,
  never empty;
- ZC and vendor currencies stay side by side, never summed;
- no colour that is not a token in `app/globals.css`;
- decisions in `lib/` (vitest), markup in `components/`.

The design idea in one line: **the console already has a terminal's data
shape — give it a terminal's visual grammar**: stat tiles with icons,
tables with right-aligned tabular numerals, state badges, sparklines,
ticker deltas, designed empty states. Density over prose.

---

## 1. `/market` — the wallet

v1: two tiles + a one-line ledger + a text matches box.
v2: a wallet header, an activity strip, a real ledger table, match cards.

### 1.1 Header tiles (grid of four, icon each)

| tile | value | source |
|---|---|---|
| Spendable | `spendable_zc` | `GET /credits` (exists) |
| Held in escrow | `held_zc` | same |
| Earned (lifetime) | sum of `earned_accepted_work` credits | new `lifetime` field |
| Spent (lifetime) | sum of `spent_accepted_work` debits | new `lifetime` field |

`GET /v1alpha1/credits` grows `lifetime: {earned_zc, spent_zc,
granted_zc, refunded_zc}` — sums over `credit_entries`, i.e. read out of
the ledger like everything else. A brand-new account shows 0/0 for these
two *because the ledger says so* — a lifetime sum of zero is a true zero,
unlike a missing measurement; the tile is labelled "lifetime" so 0 reads
as "nothing yet", and the tile subtitle says "no accepted-work settlements
yet" when the sum is 0.

### 1.2 Activity strip

A single row of chips under the tiles, computed from the same ledger page
the UI already fetches: counts per reason in view ("2 holds · 1 settle ·
1 grant"). Real counts of real rows; when the ledger is empty the strip
is absent, not "0 of everything".

### 1.3 Ledger table

A table, not a list of sentences:

```
WHEN            MOVEMENT            COUNTERPARTY            AMOUNT
12 Aug, 04:22   Escrow held         your spendable→escrow   −1.000 ZC
```

- icon per reason (phosphor: `Coin` grant, `Lock` hold, `LockOpen`
  release/refund, `ArrowsLeftRight` settle), in a muted square;
- amount right-aligned, `font-mono`, signed, `text-evergreen` for
  credits to spendable, ink for debits, muted for self-transfers with the
  magnitude in the counterparty cell;
- `title` attribute carries the ISO timestamp and the ref; the visible
  column is locale time;
- day separators ("Today", "12 Aug") between rows of different dates;
- "Older" button pages by `next_before` as v1 does.

### 1.4 Matches as cards

Two columns (buyer / host). Each match is a card: class chip, tasks ×
agreed price in mono, a **state badge** with the v1 vocabulary and its
one-line consequence (granted = "entitled, no money moved", claimed =
"escrow held", settled/refunded/expired), and the held/charged/refunded
amounts as three mini-stats when non-zero. Empty side: a designed empty
state (icon + one sentence), not a paragraph.

---

## 2. `/market/listings` — a market, not a form

v1: a select + a number input. v2: choose a machine you can see, see what
the market says about it, then price it with the book in front of you.

### 2.1 Machine picker — radio cards, not a `<select>`

One card per machine from `GET /v1alpha1/machines`: name, online dot
(`last_seen_at`, the same rule the fleet tables use), a **spec line**
rendered from `capabilities` ("RTX 4090 · 24 GB · 1 GPU" / "8 cores ·
16 GB"), and the machine's acceptance record in its class when one exists
("81% of 34 resolved", else "unproven"). Selected card gets the brand
ring. The spec line is computed **server-side** (new `gpu_label` on the
machines route payload is NOT added — the machines route is shared; the
label comes from the new market-hint route below, fetched per selection).

### 2.2 Market hint — the price suggestion

Selecting a card calls a new read route:

```
GET /v1alpha1/machines/{id}/market-hint
→ {
    capability_class: "gpu-24gb" | null,
    unclassifiable: string | null,      // the ladder's own refusal words
    book: { open_asks: n, best_ask_zc, median_ask_zc, reference_zc_per_hour } | null,
    your_record: { acceptance_rate, resolved_n } | null
  }
```

Everything in it already exists server-side (`capability_class`,
`open_asks`, `REFERENCE_ZC_PER_HOUR`, `acceptance_rates`); the route only
composes it, so the suggestion is the market itself, never a model.
Median is the median of the open asks — a real order statistic of a real
book; with zero asks, `book` is null and the panel says "no open asks in
this class yet — you would set the first price", with the reference rung
as the only anchor. That is the honest version of a suggestion.

The form then shows, as chips: `best ask`, `median`, `reference`, and
three actions that write the ask input: **Match best ask**, **At
reference**, **Donate** (0). The ask input keeps v1's integer-millicredit
parsing. An unclassifiable machine shows the ladder's refusal sentence and
disables listing — the 409 the API would return, surfaced before the
click.

### 2.3 The book — rich rows

Grouped by class as v1, but each class header carries its spread
("best 0.9 · median 1.1 ZC/h") and each row shows:

- the host's machine spec line (`gpu_label`, new field on the ask view);
- host record badge: `81% · 34` in an evergreen-tinted chip, or an
  "unproven" chip in muted — never a number in place of either;
- ask (mono) and, when the rate exists, **effective price**
  (`ask / rate`, the `effective_price` the repository already computes)
  labelled "per accepted hour"; unproven rows say "no accepted-work
  record" where the effective price would sit;
- donated rows lead with a brand-tinted "donated" chip.

`GET /market/listings` asks grow `machine_name`, `gpu_label`,
`effective_zc_per_hour` (null when unproven or rate 0 — the unclearable
case renders "unclearable", the word `effective_price`'s own doc uses).

### 2.4 Your listings — a table

Columns: machine · class · ask · state badge · action. State badges use
the v1 vocabulary. Withdraw stays, with the same 404 doctrine.

---

## 3. `/market/prices` — the compute board

The stock-terminal view. Instruments are the eight capability classes;
the traded price is the best live ask in ZC; the history is
`price_observations`, which the repository already writes on every
listing change and already serves through `price_series`.

### 3.1 The board (main table)

```
CLASS            LAST     24H      DEPTH   [sparkline]   REFERENCE
gpu-24gb         1.000    ▲ 0.100  3 asks  ~~~~          1 ZC/h
gpu-80gb-hopper  —        —        0 asks  ╌╌╌╌          10 ZC/h
```

- LAST = newest `best_ask_zc`, mono; no live ask renders `—`, never 0;
- 24H = newest observation minus the newest observation older than 24 h,
  server-computed (`change_zc`, null when there is no pair — rendered
  `—`); signed, arrowed, **market convention colours**: up =
  `text-evergreen`, down = `text-destructive`, purely directional;
- DEPTH = count of open asks in the class (a real count);
- sparkline: inline SVG polyline of the last ≤24 observations, stroke
  `var(--z-orange)`; fewer than two points renders a dashed baseline —
  an honest "no history", same visual weight as a flat market;
- row click expands the observation history (time · best ask · n asks),
  newest first — the detail drawer a terminal would have.

`GET /v1alpha1/prices` `zc` entries grow `history: [{at, best_ask_zc,
open_asks}]` (≤24 rows) and `change_zc: number | null`.

### 3.2 External venues — the comparison strip

Right column, compact quote cards (provider · sku, amount in the vendor's
own digits and currency, captured-ago + source in 11px, stale cards
dimmed with the staleness sentence up front). Unpriced venues render as
cards too, "not observed". Nothing converts; the strip sits beside the
board the way an FX panel sits beside an equity board.

### 3.3 Board header strip

Three real numbers: open asks across the book, classes with a live book,
observations in the last 24 h. All counts the API can answer from rows it
already returns; the route adds `board: {open_asks_total, live_classes,
observations_24h}`.

---

## 4. Visual grammar (all pages)

- stat tiles: `rounded-lg border bg-surface` with a 9×9 icon square
  (`text-primary` on `bg-primary/10`), value in `metric-value`, label in
  `label-caps`, one-line subtitle in 11px muted;
- tables: `text-xs` header row in `label-caps`, rows `py-2.5`, numerals
  `font-mono` right-aligned, `divide-y divide-border`;
- badges/chips: `rounded-full border px-1.5 py-0.5 text-[10px] font-mono`;
  tones from tokens only (evergreen / warning / destructive / muted /
  brand);
- empty states: centred icon in a dashed square + one sentence — the
  metrics page's unmeasured-card pattern, reused;
- page header keeps `title` + one prose line, then a right-aligned header
  stat strip where the page has one.

No new tokens are required by this spec; if implementation finds a
missing shade, add the token to `globals.css` first (house rule).

---

## 5. API contract delta (implemented first, pytest-first)

1. `GET /v1alpha1/credits` += `lifetime{earned_zc,spent_zc,granted_zc,refunded_zc}`.
2. `GET /v1alpha1/market/listings` asks += `machine_name, gpu_label,
   effective_zc_per_hour`.
3. new `GET /v1alpha1/machines/{id}/market-hint` (current_user; 404 for
   unknown/foreign machine, same doctrine).
4. `GET /v1alpha1/prices` zc += `history[{at,best_ask_zc,open_asks}]`,
   `change_zc`; top-level += `board{...}`.
5. `marketplace.py` += `machine_gpu_label(capabilities)` (the spec-line
   renderer, tested) and `class_board(db, klass)` composing
   open/best/median/history/change (tested).

## 6. Execution plan (orchestrated)

Owner instruction: spec first, then small agents, main session
orchestrates and checks.

0. **Contract (main session, now)**: items 1–5 above, pytest-first; then
   `lib/cloud-api.ts` type/fetcher updates and the `lib/market-*`
   view-model additions (badge copy, delta formatting, sparkline points,
   day-grouping), each with vitest. This fixes every shape before any
   markup agent starts, so the three agents never touch shared files.
1. **Agent W (wallet)**: `components/market/CreditsPanel.tsx` +
   `MatchesPanel.tsx` + `app/(console)/market/page.tsx` per §1/§4.
2. **Agent L (listings)**: `components/market/ListingsPanel.tsx` +
   `app/(console)/market/listings/page.tsx` per §2/§4.
3. **Agent P (prices)**: `components/market/PricesPanel.tsx` + new
   `components/market/Sparkline.tsx` +
   `app/(console)/market/prices/page.tsx` per §3/§4.

Agents run in parallel on disjoint files; each runs only its own vitest
files. The main session then runs the full gate (web test/tsc/lint/build,
api pytest), reviews every diff against §4's grammar and the doctrine,
fixes drift itself, and only then calls it done.
