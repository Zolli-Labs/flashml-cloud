import { describe, expect, it } from "vitest";

import {
  COUNT_UP_MS,
  DEFAULT_EASING,
  DURATIONS,
  EASINGS,
  MAX_TRAVEL_PX,
  MIN_TRAVEL_PX,
  REVEAL_TRIGGER_PERCENT,
  STAGGER_BUDGET_MS,
  STAGGER_CADENCE_MS,
  TRAVEL,
  cssCubicBezier,
  customEasePath,
  easeProgress,
  interpolate,
  intersectionRootMargin,
  isPastRevealLine,
  scrollTriggerStart,
  seconds,
  staggerDelays,
  type CubicBezier,
  type Easing,
} from "./timing";

const ALL_EASINGS: Easing[] = Object.values(EASINGS);

/** These tests deliberately do NOT read `components/landing/*` to check that
 * the values here still match the ones in the wild, even though this table
 * was derived from them. Those files are being rewritten by another session,
 * and a test that fails when a peer renames a component is a test that
 * teaches people to delete tests. Provenance is documented in `timing.ts`;
 * what is asserted here is that the table is internally coherent and that its
 * ceilings hold. */

describe("the easing table", () => {
  it("carries every curve in every dialect, from one definition", () => {
    for (const easing of ALL_EASINGS) {
      expect(easing.bezier).toHaveLength(4);
      for (const value of easing.bezier) expect(Number.isFinite(value)).toBe(true);

      // The CSS and GSAP forms are DERIVED, never written by hand — that is
      // the whole point of the module, so it is the first thing asserted.
      expect(easing.css).toBe(cssCubicBezier(easing.bezier));
      expect(easing.gsapPath).toBe(customEasePath(easing.bezier));
      expect(easing.gsapName.length).toBeGreaterThan(0);
    }
  });

  it("keeps both control points inside the time axis, as CSS requires", () => {
    for (const easing of ALL_EASINGS) {
      const [x1, , x2] = easing.bezier;
      expect(x1).toBeGreaterThanOrEqual(0);
      expect(x1).toBeLessThanOrEqual(1);
      expect(x2).toBeGreaterThanOrEqual(0);
      expect(x2).toBeLessThanOrEqual(1);
    }
  });

  /** Spec §2 rule 1: settle, never bounce. Overshoot is RESERVED for the
   * recovery beat (rule 5), and "reserved" has to be enforced somewhere or it
   * becomes "available". */
  it("lets exactly one curve overshoot, and it is named recovery", () => {
    const overshooting = ALL_EASINGS.filter(
      (easing) => easing.bezier[1] > 1 || easing.bezier[3] > 1
    );
    expect(overshooting.map((easing) => easing.name)).toEqual(["recovery"]);
  });

  it("defaults to settle", () => {
    expect(DEFAULT_EASING).toBe(EASINGS.settle);
    expect(DEFAULT_EASING.name).toBe("settle");
  });

  it("formats the two derived dialects the way each parser expects", () => {
    const bezier: CubicBezier = [0.2, 0.75, 0.25, 1];
    expect(cssCubicBezier(bezier)).toBe("cubic-bezier(0.2, 0.75, 0.25, 1)");
    // CustomEase.create(name, path) — endpoints pinned exactly as CSS pins
    // them, so the two describe one curve.
    expect(customEasePath(bezier)).toBe("M0,0 C0.2,0.75 0.25,1 1,1");
  });
});

describe("evaluating a curve", () => {
  it("pins both endpoints", () => {
    for (const easing of ALL_EASINGS) {
      expect(easeProgress(easing.bezier, 0)).toBe(0);
      expect(easeProgress(easing.bezier, 1)).toBe(1);
    }
  });

  it("clamps time outside 0-1 rather than extrapolating", () => {
    expect(easeProgress(EASINGS.settle.bezier, -3)).toBe(0);
    expect(easeProgress(EASINGS.settle.bezier, 4)).toBe(1);
    expect(easeProgress(EASINGS.settle.bezier, Number.NaN)).toBe(0);
  });

  it("solves the linear curve as the identity", () => {
    for (let t = 0; t <= 1.0001; t += 0.05) {
      expect(easeProgress(EASINGS.linear.bezier, t)).toBeCloseTo(
        Math.min(1, t),
        6
      );
    }
  });

  /** Every curve but `recovery` — which is the one that is SUPPOSED to go
   * backwards, on its way down from the overshoot. */
  it("rises without ever going backwards, on every quiet curve", () => {
    for (const easing of ALL_EASINGS) {
      if (easing.name === "recovery") continue;
      let previous = -Infinity;
      for (let step = 0; step <= 100; step += 1) {
        const value = easeProgress(easing.bezier, step / 100);
        expect(value).toBeGreaterThanOrEqual(previous - 1e-9);
        previous = value;
      }
    }
  });

  it("front-loads settle — fast out, slow settle", () => {
    // Half the time, well past half the distance. This is the shape of a
    // readout coming to rest rather than a curtain being pulled.
    expect(easeProgress(EASINGS.settle.bezier, 0.5)).toBeGreaterThan(0.75);
  });

  it("carries recovery past its target before it settles", () => {
    const peak = Math.max(
      ...Array.from({ length: 99 }, (_, index) =>
        easeProgress(EASINGS.recovery.bezier, (index + 1) / 100)
      )
    );
    expect(peak).toBeGreaterThan(1);
  });

  it("brings recovery back down to rest rather than leaving it high", () => {
    expect(easeProgress(EASINGS.recovery.bezier, 0.98)).toBeLessThan(1.02);
    expect(easeProgress(EASINGS.recovery.bezier, 1)).toBe(1);
  });
});

describe("interpolate", () => {
  it("lands exactly on both ends", () => {
    expect(interpolate(0, 1200, 0)).toBe(0);
    expect(interpolate(0, 1200, 1)).toBe(1200);
  });

  /** The honesty guard. `EASINGS.recovery` returns progress above 1; a value
   * interpolated on it would display a number LARGER than the one the API
   * returned. A fabricated number that shows for 90ms is still fabricated. */
  it("never carries a value past its target, even on an overshooting curve", () => {
    expect(interpolate(0, 1200, 1.14)).toBe(1200);
    expect(interpolate(0, 1200, -0.4)).toBe(0);
  });

  it("returns the target when progress is not a number", () => {
    expect(interpolate(0, 1200, Number.NaN)).toBe(1200);
  });
});

describe("durations", () => {
  it("rises monotonically through seven steps and no eighth", () => {
    const values = Object.values(DURATIONS);
    expect(values).toHaveLength(7);
    expect([...values].sort((a, b) => a - b)).toEqual(values);
  });

  it("keeps every step inside the range a reader will sit through", () => {
    for (const value of Object.values(DURATIONS)) {
      expect(value).toBeGreaterThanOrEqual(120);
      expect(value).toBeLessThanOrEqual(720);
    }
  });

  it("resolves a number in the same time a group takes to arrive", () => {
    expect(COUNT_UP_MS).toBe(DURATIONS.reveal);
  });

  it("converts to the seconds GSAP and motion/react take, without float dust", () => {
    expect(seconds(DURATIONS.reveal)).toBe(0.56);
    expect(seconds(DURATIONS.settle)).toBe(0.42);
    expect(seconds(DURATIONS.draw)).toBe(0.72);
    expect(seconds(0)).toBe(0);
    expect(seconds(Number.NaN)).toBe(0);
  });
});

describe("the stagger cadence", () => {
  it("is the 45ms the spec asks for", () => {
    expect(STAGGER_CADENCE_MS).toBe(45);
    expect(staggerDelays(4)).toEqual([0, 45, 90, 135]);
  });

  it("has nothing to delay when there is nothing to stagger", () => {
    expect(staggerDelays(0)).toEqual([]);
    expect(staggerDelays(-2)).toEqual([]);
    expect(staggerDelays(Number.NaN)).toEqual([]);
    expect(staggerDelays(1)).toEqual([0]);
  });

  /** A thirty-row table at a fixed 45ms takes 1.3 seconds to finish arriving,
   * and its last row animates long after the reader has reached it. */
  it("compresses the cadence rather than exceeding the budget", () => {
    for (const count of [2, 5, 9, 13, 30, 200]) {
      const delays = staggerDelays(count);
      expect(delays).toHaveLength(count);
      expect(delays[0]).toBe(0);
      expect(delays[delays.length - 1]).toBeLessThanOrEqual(STAGGER_BUDGET_MS);
    }
  });

  it("holds the full cadence right up to the budget, then splits it", () => {
    // 9 children = 8 gaps = exactly the budget.
    expect(staggerDelays(9)[8]).toBe(STAGGER_BUDGET_MS);
    // 13 children = 12 gaps, so 30ms each.
    const thirteen = staggerDelays(13);
    expect(thirteen[1]).toBe(30);
    expect(thirteen[12]).toBe(STAGGER_BUDGET_MS);
  });

  it("never goes backwards", () => {
    const delays = staggerDelays(20);
    for (let i = 1; i < delays.length; i += 1) {
      expect(delays[i]).toBeGreaterThanOrEqual(delays[i - 1]);
    }
  });

  it("offsets the whole group by startMs", () => {
    expect(staggerDelays(3, { startMs: 100 })).toEqual([100, 145, 190]);
    expect(staggerDelays(3, { startMs: -50 })).toEqual([0, 45, 90]);
  });

  it("returns whole milliseconds", () => {
    for (const delay of staggerDelays(7, { cadenceMs: 33.7 })) {
      expect(Number.isInteger(delay)).toBe(true);
    }
  });
});

describe("travel", () => {
  /** Spec §2 rule 2: "distance travelled is small (8-16px). Big 40px
   * slide-ups read as a template." */
  it("stays between 8 and 16 pixels", () => {
    for (const distance of Object.values(TRAVEL)) {
      expect(distance).toBeGreaterThanOrEqual(MIN_TRAVEL_PX);
      expect(distance).toBeLessThanOrEqual(MAX_TRAVEL_PX);
    }
    expect(MIN_TRAVEL_PX).toBe(8);
    expect(MAX_TRAVEL_PX).toBe(16);
  });
});

describe("the reveal line", () => {
  it("expresses one threshold in both libraries' dialects", () => {
    expect(REVEAL_TRIGGER_PERCENT).toBe(82);
    expect(scrollTriggerStart()).toBe("top 82%");
    expect(intersectionRootMargin()).toBe("0px 0px -18% 0px");
  });

  it("derives both forms from the same number", () => {
    expect(scrollTriggerStart(60)).toBe("top 60%");
    expect(intersectionRootMargin(60)).toBe("0px 0px -40% 0px");
  });

  it("knows which side of the line an element is on", () => {
    // 1000px tall viewport, line at 820px.
    expect(isPastRevealLine(400, 1000)).toBe(true);
    expect(isPastRevealLine(820, 1000)).toBe(true);
    expect(isPastRevealLine(900, 1000)).toBe(false);
    // Scrolled above the viewport entirely.
    expect(isPastRevealLine(-2000, 1000)).toBe(true);
  });

  /** Every uncertain answer in this module resolves towards content being on
   * screen, because "past the line" is what stops an element being hidden. */
  it("answers past-the-line whenever it cannot tell", () => {
    expect(isPastRevealLine(900, 0)).toBe(true);
    expect(isPastRevealLine(900, Number.NaN)).toBe(true);
    expect(isPastRevealLine(Number.NaN, 1000)).toBe(true);
  });
});
