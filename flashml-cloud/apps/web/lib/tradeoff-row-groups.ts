/** Collapsing long runs of same-verdict rows in the trade-off table, without
 * dropping any data.
 *
 * WHY THIS MODULE EXISTS. `apps/api` commit `daaa918` raised the trade-off
 * sweep cap from 16 rented machines to 64. `TradeoffCard.tsx` renders one
 * row per fleet size, so a curve that topped out at 17 rows can now be 65 —
 * readable at 16, a very long table on a job page at 65. On a demo screen
 * the rows the panel exists to show — the `helps` -> `no_marginal_gain`
 * transition, the first `beyond_task_count` row — scroll out of view first,
 * because they sit in the middle of the curve, not at either end.
 *
 * THE RULE THIS MODULE EXISTS TO PROTECT: collapsing changes what is SHOWN,
 * never what is TRUE. A collapsed group carries its hidden rows verbatim —
 * the same `TradeoffRow` objects `lib/job-tradeoff.ts` produced — and
 * nothing here computes a new number, a new claim, or a summary sentence
 * about them. "Saved 4h across these 12 rows" is exactly the kind of
 * invented figure this repo's honesty rules forbid: the API never returned
 * it. The only new information a collapsed group states is how many rows it
 * hides and which fleet sizes they span — both directly readable off the
 * rows themselves, not computed from them.
 *
 * ALWAYS VISIBLE, never inside a collapsed group:
 *   - the `baseline` row
 *   - the first and last row of the whole curve
 *   - the first `beyond_task_count` row
 *   - the last `helps` row and the first `no_marginal_gain` row of EVERY
 *     helps -> no_marginal_gain transition — a curve can wobble (dip flat,
 *     recover, dip again; see `fiveTaskCurve` in job-tradeoff.test.ts), and
 *     every occurrence is the pair the panel exists to show, not just the
 *     first
 *   - any row whose advice code differs from the row directly above it
 *
 * Everything else is a candidate. A maximal run of three or more CONSECUTIVE
 * candidate rows collapses into a single group. Because any code change
 * already makes a row an anchor (the last rule above), a run of candidates
 * can never mix advice codes — a collapsed group is always monochromatic
 * without this module having to check for it separately. A run of one or
 * two candidate rows is left exactly as it was: rendered plainly, no
 * control — "do not add a control that collapses two rows."
 *
 * Pure and presentation-only. This module decides WHICH rows to hide;
 * `TradeoffCard.tsx` decides how a hidden group looks and reveals it.
 */

import type { TradeoffRow } from "./job-tradeoff";

export interface TradeoffRowItem {
  kind: "row";
  row: TradeoffRow;
}

export interface TradeoffCollapsedItem {
  kind: "collapsed";
  /** Stable key: `totalSlots` is unique per row on a curve, so keying off
   * the first hidden row's fleet size never collides across groups. */
  id: string;
  /** The advice code every hidden row shares — see the module docblock on
   * why a collapsed run is always monochromatic. */
  adviceCode: string;
  /** Every row this group hides, in original order. Nothing summarised,
   * nothing dropped: revealing a group renders exactly this array and
   * nothing else. */
  rows: TradeoffRow[];
}

export type TradeoffDisplayItem = TradeoffRowItem | TradeoffCollapsedItem;

function isHelpsToNoMarginalGain(a: TradeoffRow, b: TradeoffRow): boolean {
  return a.adviceCode === "helps" && b.adviceCode === "no_marginal_gain";
}

/** Row indices that may never be hidden inside a collapsed group. */
function anchorIndices(rows: TradeoffRow[]): Set<number> {
  const anchors = new Set<number>();
  if (rows.length === 0) return anchors;

  anchors.add(0);
  anchors.add(rows.length - 1);

  let sawBeyondTaskCount = false;
  rows.forEach((row, i) => {
    if (row.adviceCode === "baseline") anchors.add(i);

    if (row.adviceCode === "beyond_task_count" && !sawBeyondTaskCount) {
      anchors.add(i);
      sawBeyondTaskCount = true;
    }

    if (i > 0 && row.adviceCode !== rows[i - 1].adviceCode) {
      anchors.add(i);
    }

    if (i + 1 < rows.length && isHelpsToNoMarginalGain(row, rows[i + 1])) {
      anchors.add(i);
      anchors.add(i + 1);
    }
  });

  return anchors;
}

/** Groups a curve's rows for display: every always-visible row stays its
 * own item, in place, and a run of three or more consecutive non-anchor
 * rows becomes one collapsed item. Pure — the same rows always produce the
 * same grouping, and every input row appears in exactly one output item. */
export function groupTradeoffRows(rows: TradeoffRow[]): TradeoffDisplayItem[] {
  const anchors = anchorIndices(rows);
  const items: TradeoffDisplayItem[] = [];
  let run: TradeoffRow[] = [];

  const flushRun = () => {
    if (run.length === 0) return;
    if (run.length >= 3) {
      items.push({
        kind: "collapsed",
        id: `collapsed-${run[0].totalSlots}`,
        adviceCode: run[0].adviceCode,
        rows: run,
      });
    } else {
      for (const row of run) items.push({ kind: "row", row });
    }
    run = [];
  };

  rows.forEach((row, i) => {
    if (anchors.has(i)) {
      flushRun();
      items.push({ kind: "row", row });
      return;
    }
    run.push(row);
  });
  flushRun();

  return items;
}
