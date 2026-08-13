import { describe, expect, it } from "vitest";

import {
  groupTradeoffRows,
  type TradeoffCollapsedItem,
} from "./tradeoff-row-groups";
import type { AdviceCode, AdviceTone, TradeoffRow } from "./job-tradeoff";

// A minimal, typed row builder — the same pattern `point()` uses in
// `job-tradeoff.test.ts`: built at call time, nothing fixture-shaped sitting
// in the file. Only `adviceCode` and `totalSlots` vary across these tests;
// every other field is arithmetic-free filler that satisfies the interface,
// because the grouping function never looks at it.
function row(totalSlots: number, adviceCode: AdviceCode | string): TradeoffRow {
  const tone: AdviceTone =
    adviceCode === "helps"
      ? "gain"
      : adviceCode === "baseline"
        ? "neutral"
        : "no-gain";
  return {
    totalSlots,
    ownedSlots: 1,
    rentedSlots: Math.max(0, totalSlots - 1),
    finishSeconds: null,
    savedSeconds: null,
    zcCost: 0,
    usdCost: null,
    totalUsdValue: null,
    adviceCode,
    headline: adviceCode,
    meaning: adviceCode,
    tone,
  };
}

/** A curve from a compact list of advice codes, fleet sizes 1..N. */
function curve(codes: (AdviceCode | string)[]): TradeoffRow[] {
  return codes.map((code, i) => row(i + 1, code));
}

/** Every row the input carried, reconstructed from the grouped output — a
 * collapsed item's `.rows` and a plain item's `.row` are the only places a
 * row can be, so this is the "nothing dropped, nothing invented" check. */
function flatten(items: ReturnType<typeof groupTradeoffRows>): TradeoffRow[] {
  return items.flatMap((item) => (item.kind === "row" ? [item.row] : item.rows));
}

describe("groupTradeoffRows — no data is ever dropped or duplicated", () => {
  it("reconstructs exactly the input curve, in order, for a curve with collapsed runs", () => {
    const rows = curve([
      "baseline",
      ...Array(10).fill("helps"),
      ...Array(10).fill("no_marginal_gain"),
      "beyond_task_count",
    ]);
    const items = groupTradeoffRows(rows);
    expect(flatten(items)).toEqual(rows);
  });

  it("does the same for an empty curve", () => {
    expect(groupTradeoffRows([])).toEqual([]);
  });
});

describe("groupTradeoffRows — a short curve is untouched", () => {
  it("renders every row plainly when no run reaches three", () => {
    // The five-task fixture shape from job-tradeoff.test.ts: a wobble
    // (helps, helps, no_marginal_gain, helps) but never three of a kind in a
    // row that isn't already pinned by a transition or a first/last rule.
    const rows = curve([
      "baseline",
      "helps",
      "helps",
      "no_marginal_gain",
      "helps",
      "beyond_task_count",
    ]);
    const items = groupTradeoffRows(rows);
    expect(items).toHaveLength(rows.length);
    expect(items.every((item) => item.kind === "row")).toBe(true);
  });

  it("never collapses a run of exactly two rows", () => {
    // fleet sizes 7 and 8 (indices 6,7 below) are `beyond_task_count` but
    // NOT the first occurrence (index 5 is) and NOT the last row (index 8
    // is) — a genuine run of two hideable rows, and per the brief that must
    // stay exactly as it was: two plain rows, no control.
    const rows = curve([
      "baseline",
      "helps",
      "helps",
      "helps",
      "no_marginal_gain",
      "beyond_task_count",
      "beyond_task_count",
      "beyond_task_count",
      "beyond_task_count",
    ]);
    const items = groupTradeoffRows(rows);
    expect(items.some((item) => item.kind === "collapsed")).toBe(false);
    expect(items).toHaveLength(rows.length);
  });
});

describe("groupTradeoffRows — a long curve collapses", () => {
  const rows = curve([
    "baseline",
    ...Array(10).fill("helps"),
    ...Array(10).fill("no_marginal_gain"),
    "beyond_task_count",
  ]);
  const items = groupTradeoffRows(rows);

  it("replaces each long same-code run with one collapsed control", () => {
    const collapsed = items.filter(
      (item): item is TradeoffCollapsedItem => item.kind === "collapsed"
    );
    expect(collapsed).toHaveLength(2);
    expect(collapsed.map((c) => c.adviceCode)).toEqual([
      "helps",
      "no_marginal_gain",
    ]);
  });

  it("shrinks 22 rows to a handful of display items", () => {
    expect(rows).toHaveLength(22);
    expect(items.length).toBeLessThan(10);
  });

  it("collapses a run of exactly three rows — the stated minimum", () => {
    const flat = curve([
      "baseline",
      "helps",
      "no_marginal_gain",
      "no_marginal_gain",
      "no_marginal_gain",
      "no_marginal_gain",
      "beyond_task_count",
    ]);
    const grouped = groupTradeoffRows(flat);
    const collapsed = grouped.filter(
      (item): item is TradeoffCollapsedItem => item.kind === "collapsed"
    );
    expect(collapsed).toHaveLength(1);
    expect(collapsed[0].rows).toHaveLength(3);
    expect(collapsed[0].rows.map((r) => r.totalSlots)).toEqual([4, 5, 6]);
  });
});

describe("groupTradeoffRows — the transition survives collapsing", () => {
  it("keeps the last `helps` row and the first `no_marginal_gain` row visible", () => {
    const rows = curve([
      "baseline",
      ...Array(10).fill("helps"),
      ...Array(10).fill("no_marginal_gain"),
      "beyond_task_count",
    ]);
    const items = groupTradeoffRows(rows);

    // Fleet size 11 is the last `helps` row (index 10, 0-based), fleet size
    // 12 is the first `no_marginal_gain` row (index 11) — the single most
    // important pair in the table, per the brief.
    const lastHelps = rows[10];
    const firstFlat = rows[11];
    expect(lastHelps.adviceCode).toBe("helps");
    expect(firstFlat.adviceCode).toBe("no_marginal_gain");

    const visibleSlots = items
      .filter((item) => item.kind === "row")
      .map((item) => (item.kind === "row" ? item.row.totalSlots : -1));
    expect(visibleSlots).toContain(lastHelps.totalSlots);
    expect(visibleSlots).toContain(firstFlat.totalSlots);

    // And neither one is buried inside a collapsed group's hidden rows.
    for (const item of items) {
      if (item.kind !== "collapsed") continue;
      expect(item.rows.map((r) => r.totalSlots)).not.toContain(
        lastHelps.totalSlots
      );
      expect(item.rows.map((r) => r.totalSlots)).not.toContain(
        firstFlat.totalSlots
      );
    }
  });

  it("preserves every helps -> no_marginal_gain transition on a curve that wobbles more than once", () => {
    // helps, dips to no_marginal_gain, recovers to helps, dips again — the
    // shape `fiveTaskCurve` in job-tradeoff.test.ts documents as real.
    const rows = curve([
      "baseline",
      ...Array(6).fill("helps"),
      ...Array(4).fill("no_marginal_gain"),
      ...Array(4).fill("helps"),
      ...Array(4).fill("no_marginal_gain"),
      "beyond_task_count",
    ]);
    const items = groupTradeoffRows(rows);
    const visible = new Set(
      items
        .filter((item) => item.kind === "row")
        .map((item) => (item.kind === "row" ? item.row.totalSlots : -1))
    );

    // First wobble: helps run is indices 1..6 (slots 2..7), flat run is
    // indices 7..10 (slots 8..11). Last helps = slot 7, first flat = slot 8.
    expect(visible.has(7)).toBe(true);
    expect(visible.has(8)).toBe(true);

    // Second wobble: helps run resumes at indices 11..14 (slots 12..15),
    // flat run resumes at indices 15..18 (slots 16..19). Last helps = slot
    // 15, first flat = slot 16.
    expect(visible.has(15)).toBe(true);
    expect(visible.has(16)).toBe(true);
  });
});

describe("groupTradeoffRows — the other always-visible rows", () => {
  it("keeps the baseline row visible even at the front of a long run", () => {
    const rows = curve(["baseline", ...Array(20).fill("helps")]);
    const items = groupTradeoffRows(rows);
    expect(items[0]).toEqual({ kind: "row", row: rows[0] });
  });

  it("keeps the first and last row of the curve visible", () => {
    const rows = curve(["baseline", ...Array(20).fill("helps")]);
    const items = groupTradeoffRows(rows);
    const first = items[0];
    const last = items[items.length - 1];
    expect(first.kind).toBe("row");
    expect(last.kind).toBe("row");
    if (first.kind === "row") expect(first.row).toBe(rows[0]);
    if (last.kind === "row") expect(last.row).toBe(rows[rows.length - 1]);
  });

  it("keeps only the first `beyond_task_count` row visible, collapsing the rest of a long run", () => {
    const rows = curve([
      "baseline",
      ...Array(3).fill("helps"),
      ...Array(8).fill("beyond_task_count"),
    ]);
    const items = groupTradeoffRows(rows);

    // First beyond_task_count row is at index 4 (slot 5) — visible.
    const visibleSlots = items
      .filter((item) => item.kind === "row")
      .map((item) => (item.kind === "row" ? item.row.totalSlots : -1));
    expect(visibleSlots).toContain(5);

    // The rest of that run (slots 6..11, six rows) minus the very last row
    // of the curve (slot 12, also always visible) collapses into one group.
    const collapsed = items.filter(
      (item): item is TradeoffCollapsedItem => item.kind === "collapsed"
    );
    expect(collapsed).toHaveLength(1);
    expect(collapsed[0].adviceCode).toBe("beyond_task_count");
    expect(collapsed[0].rows.map((r) => r.totalSlots)).toEqual([
      6, 7, 8, 9, 10, 11,
    ]);
    expect(visibleSlots).toContain(12); // the curve's own last row
  });

  it("marks any row whose advice code differs from the row above it as visible", () => {
    const rows = curve([
      "baseline",
      ...Array(5).fill("helps"),
      ...Array(5).fill("no_marginal_gain"),
      ...Array(5).fill("beyond_task_count"),
    ]);
    const items = groupTradeoffRows(rows);
    const visibleSlots = new Set(
      items
        .filter((item) => item.kind === "row")
        .map((item) => (item.kind === "row" ? item.row.totalSlots : -1))
    );
    // Every boundary row (first of each new code) is visible.
    expect(visibleSlots.has(2)).toBe(true); // first `helps` (slot 2, index 1)
    expect(visibleSlots.has(7)).toBe(true); // first `no_marginal_gain`
    expect(visibleSlots.has(12)).toBe(true); // first `beyond_task_count`
  });
});

describe("groupTradeoffRows — a collapsed group is monochromatic and exhaustive", () => {
  it("carries only rows of its own advice code, contiguous and in order", () => {
    const rows = curve([
      "baseline",
      ...Array(10).fill("helps"),
      ...Array(10).fill("no_marginal_gain"),
      "beyond_task_count",
    ]);
    const items = groupTradeoffRows(rows);
    for (const item of items) {
      if (item.kind !== "collapsed") continue;
      expect(item.rows.every((r) => r.adviceCode === item.adviceCode)).toBe(
        true
      );
      expect(item.rows.length).toBeGreaterThanOrEqual(3);
      // Contiguous fleet sizes, since the underlying curve is dense.
      const slots = item.rows.map((r) => r.totalSlots);
      for (let i = 1; i < slots.length; i += 1) {
        expect(slots[i]).toBe(slots[i - 1] + 1);
      }
    }
  });

  it("gives every collapsed group a stable, unique id", () => {
    const rows = curve([
      "baseline",
      ...Array(10).fill("helps"),
      ...Array(10).fill("no_marginal_gain"),
      "beyond_task_count",
    ]);
    const items = groupTradeoffRows(rows);
    const collapsed = items.filter(
      (item): item is TradeoffCollapsedItem => item.kind === "collapsed"
    );
    const ids = collapsed.map((c) => c.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
