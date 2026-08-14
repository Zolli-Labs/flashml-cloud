/** The strip's one judgement — show the figures, or say the sentence — and
 * the two formatters under it.
 *
 * The failure this guards is a table of dashes: five cards each reading
 * `0.00 · 0.00 · not observed`, which is the shape a job with no measured
 * duration produces and which says one thing five times.
 */

import { describe, expect, it } from "vitest";

import { NOT_OBSERVED, type PlanRow, type RoutingPanel } from "@/lib/job-routing";
import {
  NO_DURATION_YET,
  NO_PLAN_QUOTED,
  formatAmount,
  formatFinish,
  planStripView,
} from "./plan-strip";

function plan(over: Partial<PlanRow> & { name: string }): PlanRow {
  return {
    recommended: false,
    tasksPlaced: 6,
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

function routed(over: Partial<RoutingPanel> = {}): RoutingPanel {
  return {
    state: "routed",
    detail: null,
    kind: "hpo",
    kindEvidence: "sweep over lr, hidden expands to 6 independent trials",
    tasks: 6,
    venues: [],
    candidates: [],
    plans: [],
    recommended: null,
    duration: null,
    canary: null,
    fleet: { eligible: 2, excluded: 0, venueExcluded: 0, unplannable: 0 },
    notes: [],
    quotedNothing: false,
    ...over,
  };
}

describe("what the strip draws", () => {
  it("is loading while the read is in flight", () => {
    const view = planStripView(routed({ state: "loading" }));
    expect(view.kind).toBe("loading");
  });

  it("carries the API's own words when the read failed", () => {
    const view = planStripView(
      routed({ state: "unavailable", detail: "this job has no stored spec" })
    );
    expect(view).toEqual({
      kind: "unavailable",
      detail: "this job has no stored spec",
    });
  });

  it("says one sentence, and keeps the notes, when the router did not run", () => {
    // `unrouted` is the API's `_degraded` answer: routing not configured,
    // a spec that expands to nothing, an account with no capacity at all.
    // The notes ARE the reason, so they survive the collapse.
    const view = planStripView(
      routed({
        state: "unrouted",
        notes: ["routing is not configured on this deployment"],
      })
    );
    expect(view).toEqual({
      kind: "sentence",
      text: NO_PLAN_QUOTED,
      notes: ["routing is not configured on this deployment"],
    });
  });

  it("says one sentence when the router ran and quoted nothing", () => {
    const view = planStripView(routed({ plans: [], notes: ["no eligible fleet"] }));
    expect(view.kind).toBe("sentence");
    if (view.kind !== "sentence") throw new Error("unreachable");
    expect(view.text).toBe(NO_PLAN_QUOTED);
    expect(view.notes).toEqual(["no eligible fleet"]);
  });

  it("collapses a plan set with no figure in it to one sentence", () => {
    // Zero in both currencies AND no makespan is what a job with no measured
    // duration looks like. Three cards of that is the table of dashes.
    const view = planStripView(
      routed({
        plans: [
          plan({ name: "cheapest", recommended: true }),
          plan({ name: "fastest" }),
        ],
      })
    );
    expect(view.kind).toBe("sentence");
    if (view.kind !== "sentence") throw new Error("unreachable");
    expect(view.text).toBe(NO_DURATION_YET);
  });

  it("draws the cards as soon as one figure is real", () => {
    // A zero cost on its own is a MEASUREMENT, not a gap — workspace
    // machines are free to their own members — so it takes the missing
    // makespan as well to collapse. One makespan is enough to keep the
    // cards.
    const view = planStripView(
      routed({
        plans: [
          plan({ name: "cheapest", recommended: true, makespanSeconds: 300 }),
          plan({ name: "fastest" }),
        ],
      })
    );
    expect(view.kind).toBe("plans");
    if (view.kind !== "plans") throw new Error("unreachable");
    expect(view.plans.map((p) => p.name)).toEqual(["cheapest", "fastest"]);
  });

  it("keeps the plans in the order the route returned them", () => {
    const view = planStripView(
      routed({
        plans: [
          plan({ name: "cheapest", makespanSeconds: 900 }),
          plan({ name: "balanced", makespanSeconds: 600, recommended: true }),
          plan({ name: "fastest", makespanSeconds: 300 }),
        ],
      })
    );
    if (view.kind !== "plans") throw new Error("unreachable");
    // Never re-sorted by cost or by recommendation: `cheapest`, `balanced`,
    // `fastest` is the route's own ordering and the reader compares them
    // against a scale that runs that way.
    expect(view.plans.map((p) => p.name)).toEqual([
      "cheapest",
      "balanced",
      "fastest",
    ]);
  });
});

describe("finish times", () => {
  it("never renders an unobserved duration as a zero", () => {
    expect(formatFinish(null)).toBe(NOT_OBSERVED);
    expect(formatFinish(0)).toBe("0s");
  });

  it("uses the same buckets the job page does", () => {
    expect(formatFinish(45)).toBe("45s");
    expect(formatFinish(89)).toBe("89s");
    expect(formatFinish(90)).toBe("2m");
    expect(formatFinish(5399)).toBe("90m");
    expect(formatFinish(5400)).toBe("1.5h");
  });
});

describe("amounts", () => {
  it("always shows two decimals, so a column aligns on the point", () => {
    expect(formatAmount(0)).toBe("0.00");
    expect(formatAmount(1)).toBe("1.00");
    expect(formatAmount(12.5)).toBe("12.50");
    expect(formatAmount(3.456)).toBe("3.46");
  });
});
