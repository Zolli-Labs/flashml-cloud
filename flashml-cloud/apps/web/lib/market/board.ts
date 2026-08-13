/** What the price board is allowed to say, once the seed sits beside it.
 *
 * WHY THIS MODULE EXISTS. `lib/market-prices.ts` shapes one rung at a time
 * and knows nothing about the reference table. The board now interleaves two
 * sources with different guarantees — the coordinator's own book, and a
 * generated snapshot of vendor prices — and every rule for keeping them
 * apart is a decision rather than markup: which rows exist, which of them
 * lead, what a row says when it has no number, and which source stamp it
 * carries. Those live here where a test can reach them, and the components
 * render what comes back without re-deciding any of it.
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
  referenceClass,
  referenceKlassFor,
  referenceSeries,
  referenceSpecLine,
  referenceZcPerHour,
  type ReferenceClass,
} from "./reference";

/** Where a row's numbers came from. `observations` is the count behind a
 * live row — the honest denominator for "LIVE", including when it is 0. */
export type RowSource =
  | { kind: "live"; observations: number }
  | { kind: "reference" };

/** The 24-hour move as a ticker cell. `direction` is purely directional
 * (market convention colours it) and the text never contains a number the
 * API did not report: a null change is the words, not a zero. */
export interface ChangeCell {
  direction: "up" | "down" | "none";
  text: string;
}

export function changeCell(changeZc: number | null): ChangeCell {
  // The only divergence from `deltaCell`: it renders null as an em dash,
  // which on a board full of em-dashed empty books reads as "no data of any
  // kind". Here the reason is specific — there is no observation older than
  // 24 h to subtract — so it gets its own words. The direction is still
  // its decision, not one taken twice.
  if (changeZc === null) return { direction: "none", text: "no history" };
  const { direction } = deltaCell(changeZc);
  const arrow = direction === "up" ? "▲ " : direction === "down" ? "▼ " : "";
  return { direction, text: `${arrow}${zcAmountText(Math.abs(changeZc))} ZC` };
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
   * text because the rankings below sort on it. */
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
   */
  spark: { x: number; y: number }[] | null;
  /** Open asks behind the price. Null for a reference row: it has no book,
   * which is not the same as a book with nothing in it. */
  depth: number | null;
  source: RowSource;
  href: string;
}

function classHref(klass: string): string {
  return `/market/prices/${encodeURIComponent(klass)}`;
}

function liveRow(rung: ZcRung): BoardRow {
  const entry = referenceClass(rung.capability_class);
  return {
    klass: rung.capability_class,
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
    depth: rung.depth,
    source: { kind: "live", observations: rung.history.length },
    href: classHref(rung.capability_class),
  };
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
    change: { direction: "none", text: "—" },
    spark: null,
    depth: null,
    source: { kind: "reference" },
    href: classHref(entry.klass),
  };
}

/** Every row on the board: the coordinator's ladder in its own order, then
 * every seed class no rung already stands for.
 *
 * The ladder keeps its order rather than being sorted by price — it is a
 * capability ladder, and reading it as one is why `gpu-8gb` sits under
 * `cpu-large`. Ranking happens in `tickerRows`, on top of this. */
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
  const entry = referenceClass(klass);
  return entry ? referenceRow(entry) : null;
}

/** The head of the board — the classes worth putting in a ticker strip.
 *
 * Ranked by activity: depth first, then how many observations are behind
 * it. The third key is the one worth stating: a row with a PRICE outranks
 * one without. Eight rungs exist from the moment the API is up, and on a
 * quiet day all eight have depth 0, no observations and no last ask — so
 * without that key the strip is four em dashes while twenty-eight priced
 * reference rows sit under it. A row with nothing on it is not activity. */
export function tickerRows(rows: BoardRow[], limit = 4): BoardRow[] {
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => {
      const depth = (b.row.depth ?? 0) - (a.row.depth ?? 0);
      if (depth !== 0) return depth;
      const obs = observationsOf(b.row) - observationsOf(a.row);
      if (obs !== 0) return obs;
      const priced =
        Number(b.row.lastAskMzc !== null) - Number(a.row.lastAskMzc !== null);
      if (priced !== 0) return priced;
      return a.index - b.index;
    })
    .slice(0, limit)
    .map((entry) => entry.row);
}

function observationsOf(row: BoardRow): number {
  return row.source.kind === "live" ? row.source.observations : 0;
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
 * LIVE RUNGS ONLY. The seed cannot answer any of these questions — it has
 * no depth, no movement and no ask — so a quiet market returns fewer lines,
 * or none, rather than lines about vendor prices dressed as our own.
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
 * The order of the three answers is the whole point: OUR observations if
 * there are two of them, the seed only when there are not, and null when
 * neither source has anything — never a seed line quietly standing in for a
 * live one on a class that has started trading. */
export type PriceHistoryData =
  | { source: "live"; points: { at: string; valueMzc: number }[] }
  | { source: "reference"; points: { at: string; valueMzc: number }[] }
  | null;

export function classHistory(
  rung: ZcRung | null,
  entry: ReferenceClass | null
): PriceHistoryData {
  const observed = livePoints(rung?.history ?? []);
  if (observed.length >= 2) return { source: "live", points: observed };
  if (entry && entry.history.length >= 2) {
    return { source: "reference", points: referenceSeries(entry) };
  }
  return null;
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
