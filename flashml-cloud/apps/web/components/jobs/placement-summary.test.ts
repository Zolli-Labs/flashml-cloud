import { describe, expect, it } from "vitest";

import {
  firstSentence,
  hasMoreThanFirstSentence,
  planTableIsUnpriced,
  tradeoffTableIsUnobserved,
} from "./placement-summary";
import type { PlanRow } from "@/lib/job-routing";
import type { TradeoffRow } from "@/lib/job-tradeoff";

/** A plan with nothing measured — the shape a job that has never run
 * produces. Overrides go on top, so each test says only what it is about. */
function plan(over: Partial<PlanRow> = {}): PlanRow {
  return {
    name: "cheapest",
    recommended: false,
    tasksPlaced: 4,
    tasksUnplaced: 0,
    costs: [
      { currency: "ZC", amount: 0 },
      { currency: "USD", amount: 0 },
    ],
    totalUsdValue: 0,
    makespanSeconds: null,
    deadlineMet: null,
    achievableDeadlineSeconds: null,
    basis: null,
    n: null,
    dominatedBy: null,
    machines: 2,
    notes: [],
    ...over,
  };
}

function row(over: Partial<TradeoffRow> = {}): TradeoffRow {
  return {
    totalSlots: 2,
    ownedSlots: 1,
    rentedSlots: 1,
    finishSeconds: null,
    savedSeconds: null,
    zcCost: 0,
    usdCost: null,
    totalUsdValue: null,
    adviceCode: "no_marginal_gain",
    headline: "Costs more, finishes no sooner",
    meaning: "…",
    tone: "no-gain",
    ...over,
  };
}

describe("firstSentence", () => {
  it("takes the first sentence of a passage", () => {
    expect(
      firstSentence(
        "This venue physically cannot run this job. No price changes that."
      )
    ).toBe("This venue physically cannot run this job.");
  });

  it("does not break on a decimal point", () => {
    expect(firstSentence("Priced at 0.38 USD per hour. Captured today.")).toBe(
      "Priced at 0.38 USD per hour."
    );
  });

  it("does not break on an abbreviation followed by more of the same sentence", () => {
    expect(firstSentence("2 vCPU, 2 GB, no GPU — nothing to run this on")).toBe(
      "2 vCPU, 2 GB, no GPU — nothing to run this on"
    );
  });

  it("returns a passage with no terminator whole, rather than guessing where it ends", () => {
    const text = "A real fit for this work with no integration behind it";
    expect(firstSentence(text)).toBe(text);
  });

  it("ends a sentence at a question or exclamation mark too", () => {
    expect(firstSentence("Why not? Because nothing measured it.")).toBe(
      "Why not?"
    );
  });

  it("splits on a newline-separated sentence as readily as a space", () => {
    expect(firstSentence("First line.\nSecond line.")).toBe("First line.");
  });

  it("appends no ellipsis and adds no character of its own", () => {
    const text = "One sentence only.";
    expect(firstSentence(text)).toBe(text);
    expect(firstSentence("  padded.  ")).toBe("padded.");
  });

  it("returns the empty string for empty input", () => {
    expect(firstSentence("")).toBe("");
    expect(firstSentence("   ")).toBe("");
  });

  it("knows when a disclosure would add nothing", () => {
    expect(hasMoreThanFirstSentence("One sentence only.")).toBe(false);
    expect(hasMoreThanFirstSentence("One. And a second.")).toBe(true);
  });
});

describe("planTableIsUnpriced", () => {
  it("is true when every plan quotes nothing in both currencies and no finish time", () => {
    expect(planTableIsUnpriced([plan(), plan({ name: "fastest" })])).toBe(true);
  });

  it("is false as soon as one plan has a makespan", () => {
    expect(planTableIsUnpriced([plan(), plan({ makespanSeconds: 90 })])).toBe(
      false
    );
  });

  it("is false as soon as one plan costs something", () => {
    expect(
      planTableIsUnpriced([
        plan(),
        plan({
          costs: [
            { currency: "ZC", amount: 0 },
            { currency: "USD", amount: 0.24 },
          ],
        }),
      ])
    ).toBe(false);
  });

  it("never fires on a free plan alone — a zero cost is a measurement", () => {
    expect(planTableIsUnpriced([plan({ makespanSeconds: 600 })])).toBe(false);
  });

  it("is false for no plans at all — there is no table to collapse", () => {
    expect(planTableIsUnpriced([])).toBe(false);
  });
});

describe("tradeoffTableIsUnobserved", () => {
  it("is true when every buying figure on the curve is missing", () => {
    expect(
      tradeoffTableIsUnobserved([
        row({ adviceCode: "baseline", rentedSlots: 0 }),
        row(),
        row({ totalSlots: 3 }),
      ])
    ).toBe(true);
  });

  it("is false as soon as one row has a finish time", () => {
    expect(tradeoffTableIsUnobserved([row(), row({ finishSeconds: 300 })])).toBe(
      false
    );
  });

  it("is false as soon as one row is priced in USD", () => {
    expect(tradeoffTableIsUnobserved([row(), row({ usdCost: 0.24 })])).toBe(
      false
    );
  });

  it("is false as soon as one row settles anything in ZC", () => {
    expect(tradeoffTableIsUnobserved([row(), row({ zcCost: 1.5 })])).toBe(false);
  });

  it("is false for an empty curve — there is no table to collapse", () => {
    expect(tradeoffTableIsUnobserved([])).toBe(false);
  });
});
