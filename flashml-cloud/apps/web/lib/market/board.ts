/** What the price board is allowed to say, once the seed sits beside it.
 *
 * WHY THIS MODULE EXISTS. `lib/market-prices.ts` shapes one rung at a time
 * and knows nothing about the reference table. The board now interleaves
 * three kinds of number with different guarantees — the coordinator's own
 * book, a generated snapshot of vendor prices, and a class priced FROM that
 * snapshot because nobody has quoted into it yet — and every rule for keeping
 * them apart is a decision rather than markup: which rows exist, what a row says
 * when it has no number, which source stamp it carries, which rows a filter
 * chip selects, and which way a host's own class moved overnight. Those
 * live here where a test can reach them, and the components render what
 * comes back without re-deciding any of it.
 *
 * THE SOURCE STAMP IS PART OF THE ROW, not a flag the view may forget. A
 * `BoardRow` cannot be constructed without one.
 *
 * NULL IS NEVER ZERO, in either direction: no observation older than 24 h
 * is "no history", not a 0.0% move; an empty book is "—", not a free
 * machine; and a reference row has no depth at all, which is different from
 * a depth of zero.
 */
import type { PricePoint, PricesView, ZcRung } from "../cloud-api";
import { formatZc } from "../market-credits";
import { deltaCell, sparkPoints } from "../market-prices";
import {
  REFERENCE_CLASSES,
  classDerivation,
  referenceClass,
  referenceKlassFor,
  referenceSeries,
  referenceSpecLine,
  referenceZcPerHour,
  type ClassDerivation,
  type ReferenceClass,
  type SeriesPoint,
} from "./reference";

/** Where a row's numbers came from.
 *
 * `live` carries the observation count behind the row — the honest
 * denominator for "LIVE". It is only ever stamped on a rung that HAS
 * something: an observation, or an open ask. A rung with neither is not a
 * quiet live market, it is a book nobody has touched, and `LIVE · 0 obs` on
 * one advertised a market that did not exist yet.
 *
 * `reference` is a seed row: a vendor SKU, priced by the snapshot.
 * `derived` is a capability class priced FROM a seed row, and `note` is the
 * sentence naming which one — the chip cannot render without it.
 * `empty` is the honest fourth answer: a rung with no book and nothing in
 * the seed that describes it. No stamp, because there is nothing to stamp. */
export type RowSource =
  | { kind: "live"; observations: number }
  | { kind: "reference" }
  | { kind: "derived"; note: string }
  | { kind: "empty" };

/** The 24-hour move as a ticker cell. `direction` is purely directional
 * (market convention colours it) and the text never contains a number the
 * API did not report: a null change is the words, not a zero.
 *
 * `reference` says the movement was read off a series that is not our book.
 * It travels with the cell for the same reason `DayVerdict` carries one: a
 * green +3.4% in a column of live moves is a claim about our market, and a
 * view that cannot tell the two apart will make it. */
export interface ChangeCell {
  direction: "up" | "down" | "none";
  text: string;
  reference: boolean;
}

export function changeCell(changeZc: number | null): ChangeCell {
  // The only divergence from `deltaCell`: it renders null as an em dash,
  // which on a board full of em-dashed empty books reads as "no data of any
  // kind". Here the reason is specific — there is no observation older than
  // 24 h to subtract — so it gets its own words. The direction is still
  // its decision, not one taken twice.
  if (changeZc === null)
    return { direction: "none", text: "no history", reference: false };
  const { direction } = deltaCell(changeZc);
  const arrow = direction === "up" ? "▲ " : direction === "down" ? "▼ " : "";
  return {
    direction,
    text: `${arrow}${zcAmountText(Math.abs(changeZc))} ZC`,
    reference: false,
  };
}

/** The 24 h cell for a derived row: the last step of the very series the
 * row's sparkline draws.
 *
 * A PERCENTAGE, NOT A ZC DELTA, and that is the tell. A live cell says
 * `▲ 0.25 ZC` — an amount somebody could have paid. A derived cell says
 * `▲ 3.4%` — a shape, off a vendor series, with `reference` set so the
 * column renders it in neither of the market's two colours. */
export function derivedChangeCell(points: readonly SeriesPoint[]): ChangeCell {
  const step = lastStep(points);
  if (step === null)
    return { direction: "none", text: "—", reference: true };
  if (step.delta === 0)
    return { direction: "none", text: "unchanged", reference: true };
  const arrow = step.delta < 0 ? "▼" : "▲";
  // No percentage off a zero baseline — the same rule `dayVerdict` applies
  // to a donated ask, for the same reason: the division means nothing.
  const pct = step.pct === null ? "" : ` ${step.pct.toFixed(1)}%`;
  return {
    direction: step.delta < 0 ? "down" : "up",
    text: `${arrow}${pct}`,
    reference: true,
  };
}

/** A millicredit amount as money: `formatZc` plus the trailing zero it
 * trims, so a column of prices lines up on the decimal point instead of
 * mixing `0.5` with `4.59`.
 *
 * The same rule as the private `formatMarketZc` in `lib/market-prices.ts`,
 * repeated rather than exported from there — that module's own board row is
 * shipped and tested against these exact strings, and widening its API to
 * share four lines is a bigger change than the four lines. Whole numbers
 * stay whole, which is that module's decision and not a new one. */
export function zcAmountText(mzc: number): string {
  const text = formatZc(mzc);
  const decimals = text.split(".")[1]?.length ?? 0;
  return decimals === 1 ? `${text}0` : text;
}

/** One row of the board, live or reference, already worded. */
export interface BoardRow {
  /** The ticker: a capability class for a live row, a hardware SKU for a
   * reference one. */
  klass: string;
  /** The human name beside the ticker, when the seed knows this class.
   * Null rather than a repeat of the ticker. */
  displayName: string | null;
  /** Millicredits per hour, or null for an empty book. Kept alongside the
   * text because the rankings below sort on it.
   *
   * NULL ON A DERIVED ROW, whose text carries a price. The field means "the
   * ask", a derived row has none, and a sort that could not tell an estimate
   * from an ask would rank a vendor number against money somebody offered. */
  lastAskMzc: number | null;
  lastAskText: string;
  /** The fixed-parity cash equivalent, as the API worded it for a live row
   * and at the same digits for a reference one. */
  equivalentText: string | null;
  change: ChangeCell;
  /** Already normalised for `<Sparkline>`; null draws its dashed baseline.
   *
   * A reference row is ALWAYS null here, even though the seed carries 30
   * daily points. The sparkline in a table cell has no room for a caption,
   * and an unlabelled line next to a live one claims to be the same kind of
   * fact. The class page draws the seed history properly, with its badge.
   *
   * A DERIVED row is the exception, and `sparkDashed` is what buys it: the
   * caption a table cell cannot hold is replaced by the stroke itself, which
   * is dashed and muted and cannot be read as one of our own lines. */
  spark: { x: number; y: number }[] | null;
  /** Draw `spark` as a dashed curve rather than a solid one. Set here rather
   * than inferred from `source` in the table, so the two can never drift. */
  sparkDashed: boolean;
  /** Open asks behind the price. Null for a reference row: it has no book,
   * which is not the same as a book with nothing in it. */
  depth: number | null;
  source: RowSource;
  href: string;
  /** What the row is, for the board's filters. Derived once here so no
   * view ever re-reads a class name to work out what it is looking at. */
  kind: BoardKind;
  /** Whole GB of device memory, or null when nothing states it. Null is
   * "not stated", never 0 — see `rowVramGb`. */
  vramGb: number | null;
}

/** What kind of hardware a row is.
 *
 * `other` is an answer, not a fallback. The coordinator's ladder can grow
 * a class this console has never heard of, and filing it under GPU because
 * most rows are GPUs would be a guess with a filter chip on it. */
export type BoardKind = "gpu" | "cpu" | "tpu" | "other";

/** A row's kind: the seed's own `kind` when the seed knows the class, and
 * the class NAME when it does not.
 *
 * Only the leading segment of the name is read — `gpu-24gb`, `cpu-large` —
 * because that prefix is the capability ladder's own construction
 * (`marketplace.py`), not a pattern spotted in the strings. Anything the
 * ladder did not build that way is `other`. */
export function rowKind(
  klass: string,
  entry: ReferenceClass | null
): BoardKind {
  if (entry) return entry.kind;
  const prefix = klass.toLowerCase().split("-")[0];
  return prefix === "gpu" || prefix === "cpu" || prefix === "tpu"
    ? prefix
    : "other";
}

/** A row's device memory in whole GB, or null when nothing states it.
 *
 * The seed states it outright. A capability class states it in its own
 * name, because the ladder's GPU classes ARE VRAM floors: `gpu-24gb` is
 * 24, and `gpu-80gb-hopper` is 80 with a compute-capability qualifier
 * after it. `cpu-large` says nothing about VRAM and gets null — which is
 * why the VRAM filter offers an explicit Unknown rather than reading a
 * missing figure as zero. */
export function rowVramGb(
  klass: string,
  entry: ReferenceClass | null
): number | null {
  if (entry?.vramGb !== undefined) return entry.vramGb;
  const match = /^gpu-(\d+)gb/i.exec(klass);
  return match ? Number(match[1]) : null;
}

function classHref(klass: string): string {
  return `/market/prices/${encodeURIComponent(klass)}`;
}

/** Whether a rung has anything of its own to say.
 *
 * An observation or an open ask, either one. Both absent is the case the
 * owner was looking at: a rung the coordinator publishes because the ladder
 * has it, not because anything has happened in it. */
function rungHasBook(rung: ZcRung): boolean {
  return (
    rung.history.length > 0 || rung.depth > 0 || rung.best_ask_zc !== null
  );
}

function liveRow(rung: ZcRung): BoardRow {
  const klass = rung.capability_class;
  const entry = referenceClass(klass);

  // An untouched rung is not a live row with zeroes in it. It borrows a
  // vendor price if the seed describes its class, and says so plainly if
  // not — but either way it stops claiming a market.
  if (!rungHasBook(rung)) {
    const derived = classDerivation(klass);
    if (derived) return derivedRow(klass, derived, rung.depth);
    return {
      klass,
      displayName: entry?.displayName ?? null,
      lastAskMzc: null,
      lastAskText: "—",
      equivalentText: null,
      change: changeCell(null),
      spark: null,
      sparkDashed: false,
      depth: rung.depth,
      source: { kind: "empty" },
      href: classHref(klass),
      kind: rowKind(klass, entry),
      vramGb: rowVramGb(klass, entry),
    };
  }

  return {
    klass,
    displayName: entry?.displayName ?? null,
    lastAskMzc: rung.best_ask_zc,
    lastAskText:
      rung.best_ask_zc === null
        ? "—"
        : rung.best_ask_zc === 0
          ? "donated"
          : `${zcAmountText(rung.best_ask_zc)} ZC/hr`,
    equivalentText:
      rung.best_ask_usd === null ? null : `$${rung.best_ask_usd}/hr`,
    change: changeCell(rung.change_zc),
    spark: sparkPoints(rung.history),
    sparkDashed: false,
    depth: rung.depth,
    source: { kind: "live", observations: rung.history.length },
    href: classHref(klass),
    kind: rowKind(klass, entry),
    vramGb: rowVramGb(klass, entry),
  };
}

/** How many of a derived series' points the 7d cell draws. The column says
 * 7d; thirty daily points squeezed into the same 96px beside a live row's
 * week would be two different spans drawn as if they were one. */
const SPARK_POINTS = 7;

/** A capability class priced from the seed.
 *
 * THE ≈ IS PART OF THE NUMBER. Every string this row carries is prefixed,
 * because the row sits in a right-aligned column of asks somebody actually
 * made and is otherwise indistinguishable from them at a glance.
 *
 * `depth` is the rung's own count when there is a rung — an empty book is a
 * real 0 and reads differently from a seed row's "—", which has no book at
 * all — and null when this row stands in for a class no rung was published
 * for. */
function derivedRow(
  klass: string,
  derived: ClassDerivation,
  depth: number | null
): BoardRow {
  const entry = referenceClass(klass);
  return {
    klass,
    // The donor's name is NOT this class's name: `gpu-24gb` is not an A5000
    // and must not be labelled one. The two definitional classes keep the
    // name they already had, because for them the alias IS the definition.
    displayName: entry?.displayName ?? null,
    lastAskMzc: null,
    lastAskText: `≈ ${zcAmountText(derived.priceMzc)} ZC/hr`,
    equivalentText: `≈ $${zcAmountText(derived.priceMzc)}/hr`,
    change: derivedChangeCell(derived.history),
    spark: seriesSpark(derived.history.slice(-SPARK_POINTS)),
    sparkDashed: true,
    depth,
    source: { kind: "derived", note: derived.note },
    href: classHref(klass),
    kind: rowKind(klass, entry),
    vramGb: rowVramGb(klass, entry),
  };
}

/** A millicredit series in `<Sparkline>`'s 0–100 box, oldest → newest.
 *
 * The same normalisation as `sparkPoints`, including the flat-series centre
 * line, for a series that is already in order and already in millicredits —
 * so a derived curve and a live one are the same drawing of the same shape. */
function seriesSpark(
  points: readonly SeriesPoint[]
): { x: number; y: number }[] | null {
  if (points.length < 2) return null;
  const values = points.map((point) => point.valueMzc);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min;
  const step = 100 / (values.length - 1);
  return values.map((value, i) => ({
    x: i * step,
    y: span === 0 ? 50 : 100 - ((value - min) / span) * 100,
  }));
}

function referenceRow(entry: ReferenceClass): BoardRow {
  const mzc = referenceZcPerHour(entry);
  return {
    klass: entry.klass,
    displayName: entry.displayName,
    lastAskMzc: mzc,
    lastAskText: `${zcAmountText(mzc)} ZC/hr`,
    equivalentText: `$${zcAmountText(mzc)}/hr`,
    // A reference price is a level, not a movement. The seed's own history
    // would yield a daily delta, but calling it the 24h change of a book
    // nobody has listed in would be the exact confusion this row is badged
    // to prevent — so the cell states the absence and stops there.
    change: { direction: "none", text: "—", reference: true },
    spark: null,
    sparkDashed: false,
    depth: null,
    source: { kind: "reference" },
    href: classHref(entry.klass),
    kind: rowKind(entry.klass, entry),
    vramGb: rowVramGb(entry.klass, entry),
  };
}

/** Every row on the board: the coordinator's ladder in its own order, then
 * every seed class no rung already stands for.
 *
 * The ladder keeps its order rather than being sorted by price — it is a
 * capability ladder, and reading it as one is why `gpu-8gb` sits under
 * `cpu-large`. `filterRows` and `paginate` are windows onto this order and
 * never re-rank it: a live rung stays ahead of every seed-only row. */
export function boardRows(view: PricesView): BoardRow[] {
  const live = view.zc.map(liveRow);
  const covered = new Set(
    view.zc
      .map((rung) => referenceKlassFor(rung.capability_class))
      .filter((klass): klass is string => klass !== null)
  );
  const reference = REFERENCE_CLASSES.filter(
    (entry) => !covered.has(entry.klass)
  ).map(referenceRow);
  return [...live, ...reference];
}

/** The one row a class page is about, from whichever source knows the
 * class — a live rung first, the seed second, null when neither does.
 *
 * Same precedence and same wording as the board, so a class reached by
 * clicking its row does not restate its own price differently one page
 * later. Null is the page's "nothing known about this class", which is a
 * sentence rather than an error: an unknown ticker in a URL is a typo, not
 * a failure. */
export function classRow(
  view: PricesView | null,
  klass: string
): BoardRow | null {
  const rung = view?.zc.find((r) => r.capability_class === klass);
  if (rung) return liveRow(rung);
  // A derivation before the seed row, because a derivation is ABOUT this
  // class and a seed row is about a card: asked for `gpu-80gb-hopper` with
  // no rung to answer from, the seed hands back a row that calls itself
  // `H100-80G`, which is a different ticker with a different page.
  const derived = classDerivation(klass);
  if (derived) return derivedRow(klass, derived, null);
  const entry = referenceClass(klass);
  return entry ? referenceRow(entry) : null;
}

// ---------------------------------------------------------------------------
// classifying and paging the board
// ---------------------------------------------------------------------------
//
// Thirty-odd rows is a listing, not a board. The filters and the page window
// live here rather than in the table for the usual reason — which rows a
// chip selects is a decision about what a word means, and "24 GB" meaning
// "more than 16 and no more than 24" is exactly the kind of thing that
// should be readable in a test instead of inferred from a comparison
// operator inside JSX.

export type KindFilter = "all" | BoardKind;

/** The VRAM select, in the order it is offered.
 *
 * DELIBERATE GAP: 49–79 GB falls in no bucket, so a hypothetical 64 GB
 * card appears under Any and under its kind chip, but under no VRAM band.
 * The alternative is stretching "80 GB+" down to cover it, which puts a
 * card under a label that is false about it. No such row exists in the
 * seed or on the ladder today; if one arrives it needs its own band, not a
 * widened neighbour. */
export type VramFilter = "any" | "le16" | "24" | "32-48" | "80plus" | "unknown";

export const VRAM_FILTERS: readonly { value: VramFilter; label: string }[] = [
  { value: "any", label: "Any VRAM" },
  { value: "le16", label: "≤16 GB" },
  { value: "24", label: "24 GB" },
  { value: "32-48", label: "32–48 GB" },
  { value: "80plus", label: "80 GB+" },
  { value: "unknown", label: "Unknown" },
];

export interface BoardFilter {
  kind: KindFilter;
  vram: VramFilter;
}

/** Whether a row's memory falls in a band. A row whose VRAM nothing states
 * matches Any and Unknown and nothing else: the band filters can only
 * speak about rows they can measure, and quietly keeping unmeasured rows
 * in a "≤16 GB" result would put an unknown quantity under a number. */
function matchesVram(vramGb: number | null, filter: VramFilter): boolean {
  if (filter === "any") return true;
  if (filter === "unknown") return vramGb === null;
  if (vramGb === null) return false;
  if (filter === "le16") return vramGb <= 16;
  if (filter === "24") return vramGb > 16 && vramGb <= 24;
  if (filter === "32-48") return vramGb > 24 && vramGb <= 48;
  return vramGb >= 80;
}

/** The rows a filter selects, IN BOARD ORDER — `boardRows` already puts
 * every live rung ahead of every seed-only row, and filtering must not
 * re-rank them: a filtered board is a subset of the board, not a different
 * board. */
export function filterRows(
  rows: readonly BoardRow[],
  filter: BoardFilter
): BoardRow[] {
  return rows.filter(
    (row) =>
      (filter.kind === "all" || row.kind === filter.kind) &&
      matchesVram(row.vramGb, filter.vram)
  );
}

export interface KindChip {
  value: KindFilter;
  label: string;
  count: number;
}

/** The kind chips, counted against the unfiltered board.
 *
 * All / GPU / CPU / TPU are always offered, empty or not — they are the
 * vocabulary of the ladder, and a chip that vanishes when its count hits
 * zero reads as a filter that was never there. "Other" is the opposite
 * case: it names nothing in particular, so it appears only when there is
 * something in it to name. */
export function kindChips(rows: readonly BoardRow[]): KindChip[] {
  const count = (kind: BoardKind) =>
    rows.filter((row) => row.kind === kind).length;
  const chips: KindChip[] = [
    { value: "all", label: "All", count: rows.length },
    { value: "gpu", label: "GPU", count: count("gpu") },
    { value: "cpu", label: "CPU", count: count("cpu") },
    { value: "tpu", label: "TPU", count: count("tpu") },
  ];
  const other = count("other");
  if (other > 0) chips.push({ value: "other", label: "Other", count: other });
  return chips;
}

export const BOARD_PAGE_SIZE = 10;

export interface BoardPage {
  rows: BoardRow[];
  /** The page actually shown, which is the requested one CLAMPED. */
  page: number;
  pages: number;
  /** `n–m of N`, or `0 of 0` — never `1–0 of 0`. */
  rangeText: string;
}

/** One page of the board.
 *
 * THE CLAMP IS THE POINT. The caller holds the page number in component
 * state and the row set moves under it — a filter narrows to four rows
 * while page 3 is showing, a refresh drops a class. Every one of those
 * leaves the request out of range, and an out-of-range request must render
 * the last page rather than an empty table that looks like a failed read.
 * The clamped number comes back so the caller's Prev/Next can step from
 * what is on screen instead of from what it last asked for. */
export function paginate(
  rows: readonly BoardRow[],
  page: number,
  perPage = BOARD_PAGE_SIZE
): BoardPage {
  const total = rows.length;
  const pages = Math.max(1, Math.ceil(total / perPage));
  const wanted = Number.isFinite(page) ? Math.floor(page) : 1;
  const clamped = Math.min(Math.max(wanted, 1), pages);
  const start = (clamped - 1) * perPage;
  const window = rows.slice(start, start + perPage);
  return {
    rows: window,
    page: clamped,
    pages,
    rangeText:
      total === 0 ? "0 of 0" : `${start + 1}–${start + window.length} of ${total}`,
  };
}

/** One line of the "hottest right now" rail: a fact about the live book,
 * with the number that makes it true. */
export interface HotRow {
  klass: string;
  why: string;
  valueText: string;
  href: string;
}

/** What the live book is doing, in at most three lines.
 *
 * LIVE RUNGS ONLY, and it takes rungs rather than rows so that it cannot be
 * handed anything else. Neither the seed nor a derivation can answer any of
 * these questions — no depth, no movement, no ask — so a quiet market returns
 * fewer lines, or none, rather than lines about vendor prices dressed as our
 * own. "Cheapest live ask" naming a class nobody has listed in would be the
 * one place on this page where an estimate reads as a transaction.
 *
 * Each line is skipped when nothing qualifies for it, which is why the
 * result is a list and not a fixed triple. */
export function hottestRows(zc: readonly ZcRung[]): HotRow[] {
  const rows: HotRow[] = [];

  const deepest = zc
    .filter((rung) => rung.depth > 0)
    .sort((a, b) => b.depth - a.depth)[0];
  if (deepest) {
    rows.push({
      klass: deepest.capability_class,
      why: "deepest book",
      valueText: `${deepest.depth} open ask${deepest.depth === 1 ? "" : "s"}`,
      href: classHref(deepest.capability_class),
    });
  }

  // Non-zero, not merely non-null: a class that was observed 24 h ago at
  // exactly the same ask has a change, and it is not a mover.
  const mover = zc
    .filter((rung) => rung.change_zc !== null && rung.change_zc !== 0)
    .sort(
      (a, b) => Math.abs(b.change_zc as number) - Math.abs(a.change_zc as number)
    )[0];
  if (mover) {
    const change = mover.change_zc as number;
    rows.push({
      klass: mover.capability_class,
      why: "biggest 24h move",
      valueText: `${change > 0 ? "▲" : "▼"} ${zcAmountText(Math.abs(change))} ZC`,
      href: classHref(mover.capability_class),
    });
  }

  const cheapest = zc
    .filter((rung) => rung.best_ask_zc !== null)
    .sort((a, b) => (a.best_ask_zc as number) - (b.best_ask_zc as number))[0];
  if (cheapest) {
    const ask = cheapest.best_ask_zc as number;
    rows.push({
      klass: cheapest.capability_class,
      why: "cheapest live ask",
      valueText: ask === 0 ? "donated" : `${zcAmountText(ask)} ZC/hr`,
      href: classHref(cheapest.capability_class),
    });
  }

  return rows;
}

/** The series behind a class page's chart, and which kind of series it is.
 *
 * The order of the answers is the whole point: OUR observations if there are
 * two of them, a borrowed series only when there are not, and null when
 * nothing has one — never a vendor line quietly standing in for a live one on
 * a class that has started trading.
 *
 * `derived` carries the sentence naming its donor, because the chart it
 * draws is a card's history under a class's name and the caption has to say
 * whose. */
export type PriceHistoryData =
  | { source: "live"; points: SeriesPoint[] }
  | { source: "reference"; points: SeriesPoint[] }
  | { source: "derived"; points: SeriesPoint[]; note: string }
  | null;

/** @param klass the capability class being charted, when the caller knows it
 * independently of the rung. Defaults to the rung's own class, which is what
 * lets the class page pick a derivation up without being rewritten. */
export function classHistory(
  rung: ZcRung | null,
  entry: ReferenceClass | null,
  klass?: string
): PriceHistoryData {
  const observed = livePoints(rung?.history ?? []);
  if (observed.length >= 2) return { source: "live", points: observed };
  // Derivation before the seed row, for the two definitional classes where
  // both would answer: they resolve to the same donor and the same points,
  // and the derived arm is the one that also names it.
  const derived = classDerivation(klass ?? rung?.capability_class ?? "");
  if (derived && derived.history.length >= 2) {
    return {
      source: "derived",
      points: derived.history,
      note: derived.note,
    };
  }
  if (entry && entry.history.length >= 2) {
    return { source: "reference", points: referenceSeries(entry) };
  }
  return null;
}

/** The stamp a SERIES deserves, which is not always the stamp its price got:
 * a class with one live observation prices itself live and still has to
 * borrow a line to draw. Null where the series is ours, or absent — there is
 * nothing to disclose about our own book. */
export function historyStamp(data: PriceHistoryData): RowSource | null {
  if (data === null || data.source === "live") return null;
  if (data.source === "derived") return { kind: "derived", note: data.note };
  return { kind: "reference" };
}

/** Observations as chart points, oldest → newest, dropping the ones with no
 * ask. The API sends them newest-first; nothing is interpolated across the
 * gaps the drop leaves, exactly as `sparkPoints` does not. */
function livePoints(history: readonly PricePoint[]): {
  at: string;
  valueMzc: number;
}[] {
  return history
    .slice()
    .reverse()
    .filter((point) => point.best_ask_zc !== null)
    .map((point) => ({ at: point.at, valueMzc: point.best_ask_zc as number }));
}

/** The class page's spec line, when the seed knows the class. Null keeps
 * the header from printing a guess about hardware. */
export function classSpecLine(entry: ReferenceClass | null): string | null {
  return entry ? referenceSpecLine(entry) : null;
}

// ---------------------------------------------------------------------------
// the host's own classes
// ---------------------------------------------------------------------------
//
// The question a host opens this page with is not "what does the market
// cost" — it is "is MY machine cheaper or dearer than it was". That is one
// sentence per class they own, and the sentence has to survive the same
// rule as everything else here: a movement read off the generated seed is
// not a movement of our book, so it travels with the flag that says so and
// the view is expected to badge it.

/** One of the signed-in host's machines, already resolved to a capability
 * class by the caller (`GET /v1alpha1/machines/{id}/market-hint`).
 *
 * `label` rather than `name` because an unnamed machine still has an
 * identity its owner recognises — the caller decides whether that is the
 * name or the node id, the same way every other machine surface does. */
export interface HostMachine {
  id: string;
  label: string;
  klass: string;
}

/** A host's day, in one sentence.
 *
 * `direction` is purely directional and market convention colours it, the
 * same contract as `ChangeCell`. `reference` is not decoration: it says
 * the movement was computed from the generated seed, and a view that drops
 * it prints a vendor-band wiggle as a fact about our book. */
export interface DayVerdict {
  direction: "up" | "down" | "none";
  text: string;
  reference: boolean;
}

/** The last two points of whichever series is being drawn, in words.
 *
 * NOT `changeCell`. That one answers "what did the 24 h delta the API
 * reported look like"; this one answers "which way did the line on screen
 * just move", off the very points the chart drew — so the words under a
 * chart can never disagree with the chart. */
export function dayVerdict(data: PriceHistoryData): DayVerdict {
  const step = data === null ? null : lastStep(data.points);
  if (data === null || step === null) {
    return { direction: "none", text: "no history yet", reference: false };
  }
  // Anything that is not our own book: seed row or derivation alike. The
  // flag means "this movement is not ours", and a derived line is no more
  // ours than the seed line it was scaled from.
  const reference = data.source !== "live";
  const { prev, last, delta } = step;
  if (delta === 0) return { direction: "none", text: "unchanged today", reference };

  // "yesterday" only when a day actually turned over between the two
  // points. The seed is daily and always will be; our own observations are
  // recorded as the book moves, and two of them can be minutes apart —
  // "cheaper than yesterday" over a five-minute gap is a claim about a day
  // that did not pass.
  const since =
    prev.at.slice(0, 10) === last.at.slice(0, 10)
      ? "the last reading"
      : "yesterday";
  const word = delta < 0 ? "cheaper" : "pricier";
  const arrow = delta < 0 ? "▼" : "▲";
  // A move off a donated (0) baseline has no percentage — the division is
  // Infinity — so the words stand alone rather than carrying a figure that
  // means nothing. The direction is still true and still coloured.
  const pct = step.pct === null ? "" : ` ${arrow} ${step.pct.toFixed(1)}%`;
  return {
    direction: delta < 0 ? "down" : "up",
    text: `${word} than ${since}${pct}`,
    reference,
  };
}

/** The last movement in a series: the two points, the signed delta between
 * them, and the size of it as a percentage of where it started.
 *
 * ONE READING, TWO SENTENCES. `dayVerdict` puts it under a chart and
 * `derivedChangeCell` puts it in a table column; sharing the arithmetic is
 * what stops a card saying 3.4% while the row beside it says 3.5%. `pct` is
 * null off a zero baseline — a move up from a donated hour has a direction
 * and no percentage. */
function lastStep(points: readonly SeriesPoint[]): {
  prev: SeriesPoint;
  last: SeriesPoint;
  delta: number;
  pct: number | null;
} | null {
  if (points.length < 2) return null;
  const prev = points[points.length - 2];
  const last = points[points.length - 1];
  const delta = last.valueMzc - prev.valueMzc;
  return {
    prev,
    last,
    delta,
    pct:
      prev.valueMzc > 0 ? (Math.abs(delta) / prev.valueMzc) * 100 : null,
  };
}

/** One capability class the host has machines in, with everything the card
 * for it renders. */
export interface HostClassGroup {
  klass: string;
  displayName: string | null;
  /** Their machines in this class, in the order the caller listed them. */
  machines: HostMachine[];
  /** The class's board row — price, source stamp, 24 h cell. Null for a
   * class neither the coordinator nor the seed knows, which is a card that
   * says "—" rather than a card that is missing. */
  row: BoardRow | null;
  history: PriceHistoryData;
  verdict: DayVerdict;
  href: string;
}

/** The host's machines grouped into the classes they belong to.
 *
 * Group order is first-seen, so it follows `GET /v1alpha1/machines` and
 * does not reshuffle between refreshes. Machines the caller could not
 * resolve are simply absent from the input — an unresolvable machine has
 * no class to file under, and inventing one for it would be the guess this
 * whole module exists to avoid. */
export function hostClassGroups(
  view: PricesView | null,
  machines: readonly HostMachine[]
): HostClassGroup[] {
  const order: string[] = [];
  const byClass = new Map<string, HostMachine[]>();
  for (const machine of machines) {
    const bucket = byClass.get(machine.klass);
    if (bucket) {
      bucket.push(machine);
    } else {
      byClass.set(machine.klass, [machine]);
      order.push(machine.klass);
    }
  }

  return order.map((klass) => {
    const rung = view?.zc.find((r) => r.capability_class === klass) ?? null;
    const entry = referenceClass(klass);
    // The class is passed explicitly: a host can own a machine in a class the
    // board has not published a rung for, and that card should still draw the
    // derived line rather than the words "no history yet".
    const history = classHistory(rung, entry, klass);
    const row = classRow(view, klass);
    return {
      klass,
      displayName: row?.displayName ?? null,
      machines: byClass.get(klass) ?? [],
      row,
      history,
      verdict: dayVerdict(history),
      href: classHref(klass),
    };
  });
}
