import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CountUp } from "@/components/motion/CountUp";
import { MotionConfig } from "@/components/motion/MotionConfig";
import { Reveal } from "@/components/motion/Reveal";
import { Stagger } from "@/components/motion/Stagger";
import { Trace } from "@/components/motion/Trace";

import {
  MAX_COUNT_DECIMALS,
  NOT_OBSERVED_LABEL,
  countUpValueAt,
  decimalsFor,
  formatCountValue,
  isVisibleTarget,
  resolveCountUp,
  resolveMotionMode,
  resolveRevealPlan,
  resolveStaggerDelays,
  resolveTarget,
  resolveTrace,
  resolveVariant,
  type CountUpPlan,
} from "./reduced";
import { COUNT_UP_MS, DURATIONS, EASINGS } from "./timing";
import { MOTION_VARIANTS, VARIANT_NAMES } from "./variants";

describe("resolveMotionMode", () => {
  it("reads the reader's preference", () => {
    expect(resolveMotionMode({ reduced: false })).toBe("animate");
    expect(resolveMotionMode({ reduced: true })).toBe("static");
  });

  /** The kill switch is not the same statement as the preference, which is
   * why the mode is two named values rather than a negated boolean. */
  it("lets the kill switch settle everything without touching a call site", () => {
    expect(resolveMotionMode({ reduced: false, enabled: false })).toBe("static");
    expect(resolveMotionMode({ reduced: true, enabled: true })).toBe("static");
  });
});

describe("reduced motion never hides anything", () => {
  /** THE BUG THIS WHOLE MODULE EXISTS TO PREVENT: a reveal that starts at
   * `opacity: 0` and waits for an animation that reduced motion cancelled.
   * The content is not slower. It is gone. */
  it("leaves every state of every variant visible under reduced motion", () => {
    for (const name of VARIANT_NAMES) {
      const resolved = resolveVariant(MOTION_VARIANTS[name], "static");
      for (const [state, target] of Object.entries(resolved)) {
        expect(
          isVisibleTarget(target),
          `${name}.${state} is invisible under reduced motion`
        ).toBe(true);
        expect(target.opacity ?? 1, `${name}.${state}`).toBe(1);
      }
    }
  });

  it("collapses the hidden state onto the visible one, so there is nothing left to wait for", () => {
    for (const name of VARIANT_NAMES) {
      const resolved = resolveVariant(MOTION_VARIANTS[name], "static");
      expect(resolved.hidden, name).toEqual(resolved.visible);
    }
  });

  it("resolves the trace drawn rather than at zero length", () => {
    const resolved = resolveVariant(MOTION_VARIANTS.trace, "static");
    expect(resolved.hidden.scaleX).toBe(1);
    expect(resolved.visible.scaleX).toBe(1);
  });

  it("zeroes the duration so the CSS reset and the JS layer agree", () => {
    const resolved = resolveVariant(MOTION_VARIANTS.reveal, "static");
    expect(resolved.visible.transition).toEqual({ duration: 0 });
  });

  it("changes nothing at all when motion is on", () => {
    for (const name of VARIANT_NAMES) {
      expect(resolveVariant(MOTION_VARIANTS[name], "animate")).toBe(
        MOTION_VARIANTS[name]
      );
    }
  });

  /** A resolver that helpfully adds a transform to an element whose variant
   * never asked for one is a resolver that will one day flatten a layout. */
  it("does not invent properties the variant never mentioned", () => {
    expect(resolveTarget({ opacity: 0 }, "static")).toEqual({
      opacity: 1,
      transition: { duration: 0 },
    });
    expect(resolveTarget({ opacity: 0, y: 12 }, "static")).toEqual({
      opacity: 1,
      y: 0,
      transition: { duration: 0 },
    });
  });
});

describe("isVisibleTarget", () => {
  it("counts transparency and collapse as invisible", () => {
    expect(isVisibleTarget({ opacity: 0 })).toBe(false);
    expect(isVisibleTarget({ scaleX: 0 })).toBe(false);
    expect(isVisibleTarget({ scaleY: 0 })).toBe(false);
  });

  it("does not count being moved as being hidden", () => {
    expect(isVisibleTarget({ y: 12 })).toBe(true);
    expect(isVisibleTarget({})).toBe(true);
  });
});

describe("resolveStaggerDelays", () => {
  it("staggers when motion is on", () => {
    expect(resolveStaggerDelays(3, "animate")).toEqual([0, 45, 90]);
  });

  /** A staggered arrival with no animation is just content appearing late. */
  it("flattens to zero under reduced motion, keeping one delay per child", () => {
    expect(resolveStaggerDelays(3, "static")).toEqual([0, 0, 0]);
    expect(resolveStaggerDelays(0, "static")).toEqual([]);
  });
});

describe("resolveRevealPlan", () => {
  it("primes nothing under reduced motion", () => {
    const plan = resolveRevealPlan({ mode: "static", inViewAtMount: false });
    expect(plan).toEqual({
      prime: false,
      animate: false,
      waitForInView: false,
      reason: "reduced-motion",
    });
  });

  /** Hydration happens after the server markup has painted. Hiding something
   * the reader is already looking at, to fade it back in, is a flicker on the
   * most important content on the page. */
  it("never hides content that is already on screen", () => {
    const plan = resolveRevealPlan({ mode: "animate", inViewAtMount: true });
    expect(plan.prime).toBe(false);
    expect(plan.reason).toBe("already-read");
  });

  it("hides and waits for content that is still below the line", () => {
    const plan = resolveRevealPlan({ mode: "animate", inViewAtMount: false });
    expect(plan).toEqual({
      prime: true,
      animate: true,
      waitForInView: true,
      reason: "on-view",
    });
  });

  /** Never wait for machinery that is not there. */
  it("refuses to prime when there is no observer to un-prime it", () => {
    const plan = resolveRevealPlan({
      mode: "animate",
      inViewAtMount: false,
      observerAvailable: false,
    });
    expect(plan.prime).toBe(false);
    expect(plan.reason).toBe("no-observer");
  });

  it("animates a mount trigger wherever it is, without an observer", () => {
    const plan = resolveRevealPlan({
      mode: "animate",
      trigger: "mount",
      inViewAtMount: true,
      observerAvailable: false,
    });
    expect(plan).toEqual({
      prime: true,
      animate: true,
      waitForInView: false,
      reason: "on-mount",
    });
  });

  it("still refuses a mount trigger under reduced motion", () => {
    expect(
      resolveRevealPlan({
        mode: "static",
        trigger: "mount",
        inViewAtMount: false,
      }).prime
    ).toBe(false);
  });
});

describe("resolveCountUp — the honesty rules", () => {
  /** Spec §1.1: `null` means NOT OBSERVED, never `0`. A count-up on a value
   * the API did not return is a fabricated number, and one that rises is more
   * persuasive than one sitting still. */
  it("renders a null as words and does not animate it", () => {
    const plan = resolveCountUp({
      value: null,
      mode: "animate",
      inViewAtMount: false,
    });
    expect(plan).toEqual({ kind: "not-observed", label: NOT_OBSERVED_LABEL });
  });

  it("never turns a null into a zero, on any path", () => {
    for (const mode of ["animate", "static"] as const) {
      for (const inViewAtMount of [true, false]) {
        for (const trigger of ["in-view", "mount"] as const) {
          const plan = resolveCountUp({
            value: null,
            mode,
            trigger,
            inViewAtMount,
          });
          expect(plan.kind).toBe("not-observed");
          expect(JSON.stringify(plan)).not.toContain("0");
        }
      }
    }
  });

  /** What a broken read looks like after it has been through some
   * arithmetic. A NaN displayed as 0 is a fabricated measurement, and
   * afterwards it is indistinguishable from a real one. */
  it("takes NaN and Infinity out the same exit as null", () => {
    for (const value of [Number.NaN, Infinity, -Infinity]) {
      expect(resolveCountUp({ value, mode: "animate" }).kind).toBe(
        "not-observed"
      );
    }
  });

  it("carries the caller's words when the caller has better ones", () => {
    const plan = resolveCountUp({
      value: null,
      mode: "animate",
      notObservedLabel: "no runs yet",
    });
    expect(plan).toEqual({ kind: "not-observed", label: "no runs yet" });
  });

  it("renders the real figure immediately under reduced motion", () => {
    expect(
      resolveCountUp({ value: 1200, mode: "static", inViewAtMount: false })
    ).toEqual({ kind: "final", value: 1200, decimals: 0 });
  });

  it("renders the real figure immediately when it is already on screen", () => {
    expect(
      resolveCountUp({ value: 1200, mode: "animate", inViewAtMount: true })
    ).toEqual({ kind: "final", value: 1200, decimals: 0 });
  });

  it("counts a real value that is still below the line", () => {
    const plan = resolveCountUp({
      value: 1200,
      mode: "animate",
      inViewAtMount: false,
    });
    expect(plan).toEqual({
      kind: "count",
      from: 0,
      to: 1200,
      decimals: 0,
      durationMs: COUNT_UP_MS,
      easing: EASINGS.settle.bezier,
    });
  });

  /** An overshooting counter would display a figure larger than the one the
   * API returned. `interpolate` clamps it, but a counter designed to need
   * that clamp is one edit away from lying. */
  it("never counts on the overshooting curve", () => {
    const plan = resolveCountUp({
      value: 1200,
      mode: "animate",
      inViewAtMount: false,
    });
    expect(plan.kind === "count" && plan.easing).not.toEqual(
      EASINGS.recovery.bezier
    );
  });

  it("has nothing to count when the start is already the answer", () => {
    expect(
      resolveCountUp({
        value: 0,
        from: 0,
        mode: "animate",
        inViewAtMount: false,
      })
    ).toEqual({ kind: "final", value: 0, decimals: 0 });
  });

  /** Zero is a perfectly good measurement. It is `null` that is not one. */
  it("treats an observed zero as a real value", () => {
    const plan = resolveCountUp({
      value: 0,
      from: -5,
      mode: "animate",
      inViewAtMount: false,
    });
    expect(plan.kind).toBe("count");
  });
});

describe("countUpValueAt", () => {
  const plan: Extract<CountUpPlan, { kind: "count" }> = {
    kind: "count",
    from: 0,
    to: 1200,
    decimals: 0,
    durationMs: 560,
    easing: EASINGS.settle.bezier,
  };

  it("starts where the animation starts", () => {
    expect(countUpValueAt(plan, 0)).toBe(0);
    expect(countUpValueAt(plan, -40)).toBe(0);
  });

  /** Not "close to 1200". The identical number the API returned, so the
   * figure left on screen is the figure that was measured. */
  it("ends exactly on the measured value", () => {
    expect(countUpValueAt(plan, 560)).toBe(1200);
    expect(countUpValueAt(plan, 10_000)).toBe(1200);
    expect(countUpValueAt(plan, Number.NaN)).toBe(0);
  });

  it("never displays a number larger than the one that was measured", () => {
    for (let elapsed = 0; elapsed <= 560; elapsed += 7) {
      const value = countUpValueAt(plan, elapsed);
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThanOrEqual(1200);
    }
  });

  it("counts down as readily as up", () => {
    const falling: Extract<CountUpPlan, { kind: "count" }> = {
      ...plan,
      from: 100,
      to: 20,
    };
    expect(countUpValueAt(falling, 0)).toBe(100);
    expect(countUpValueAt(falling, 560)).toBe(20);
    expect(countUpValueAt(falling, 280)).toBeLessThan(100);
  });
});

describe("precision", () => {
  it("takes its precision from the figure the API returned", () => {
    expect(decimalsFor(1200)).toBe(0);
    expect(decimalsFor(12.5)).toBe(1);
    expect(decimalsFor(0.125)).toBe(3);
    expect(decimalsFor(-4.25)).toBe(2);
  });

  it("does not mistake float residue for evidence", () => {
    expect(decimalsFor(0.1 + 0.2)).toBe(MAX_COUNT_DECIMALS);
    expect(decimalsFor(Number.NaN)).toBe(0);
    expect(decimalsFor(1e-9)).toBe(0);
  });

  it("formats in a fixed locale so the output is the same everywhere", () => {
    expect(formatCountValue(1200, 0)).toBe("1,200");
    expect(formatCountValue(1200, 0, { grouping: false })).toBe("1200");
    expect(formatCountValue(12.5, 1)).toBe("12.5");
    expect(formatCountValue(12, 0)).toBe("12");
  });

  it("says the words rather than printing a broken number", () => {
    expect(formatCountValue(Number.NaN, 0)).toBe(NOT_OBSERVED_LABEL);
  });
});

describe("resolveTrace", () => {
  it("draws a decorative rule to full length, measuring nothing", () => {
    expect(
      resolveTrace({ role: "rule", mode: "animate", inViewAtMount: false })
    ).toEqual({
      kind: "draw",
      extent: 1,
      durationMs: DURATIONS.draw,
      easing: EASINGS.settle.bezier,
    });
  });

  /** A zero-length fill is the sentence "this job has made no progress", and
   * we would be saying it without evidence. */
  it("draws nothing at all for a progress value nobody reported", () => {
    expect(resolveTrace({ role: "progress", progress: null, mode: "animate" })).toEqual({
      kind: "not-observed",
    });
  });

  it("refuses a progress value it does not understand rather than clamping it", () => {
    for (const progress of [Number.NaN, Infinity, -0.4, 1.7]) {
      expect(
        resolveTrace({ role: "progress", progress, mode: "animate" }).kind,
        `${progress}`
      ).toBe("not-observed");
    }
  });

  it("tolerates float residue at the ends of the range", () => {
    expect(
      resolveTrace({
        role: "progress",
        progress: 1 + 1e-9,
        mode: "static",
      })
    ).toEqual({ kind: "drawn", extent: 1 });
  });

  /** An eased progress bar sprints and then crawls, which is a bar telling
   * you something about pace that the job did not do. */
  it("advances a real progress value linearly", () => {
    const plan = resolveTrace({
      role: "progress",
      progress: 0.42,
      mode: "animate",
      inViewAtMount: false,
    });
    expect(plan).toEqual({
      kind: "draw",
      extent: 0.42,
      durationMs: DURATIONS.settle,
      easing: EASINGS.linear.bezier,
    });
  });

  it("renders drawn, at the true extent, under reduced motion", () => {
    expect(
      resolveTrace({ role: "progress", progress: 0.42, mode: "static" })
    ).toEqual({ kind: "drawn", extent: 0.42 });
    expect(resolveTrace({ role: "rule", mode: "static" })).toEqual({
      kind: "drawn",
      extent: 1,
    });
  });

  it("ignores a progress value on a decorative rule instead of drawing it", () => {
    expect(
      resolveTrace({ role: "rule", progress: null, mode: "static" })
    ).toEqual({ kind: "drawn", extent: 1 });
  });
});

/** The server render is the last line of defence: no JS, a failed hydration
 * and a thrown observer all land here. `lib/landing-cinematic.test.ts` holds
 * the same line for the landing page — `expect(markup).not.toContain('style=
 * "opacity:0"')` — and these primitives have to clear the same bar before
 * they can be used there. */
describe("server markup is never hidden", () => {
  it("renders a reveal's content with no inline style at all", () => {
    const markup = renderToStaticMarkup(
      createElement(Reveal, null, "Accepted work only")
    );
    expect(markup).toContain("Accepted work only");
    expect(markup).not.toContain("opacity:0");
    expect(markup).not.toContain("style=");
  });

  it("renders every variant visible from the server", () => {
    for (const variant of VARIANT_NAMES) {
      const markup = renderToStaticMarkup(
        createElement(Reveal, { variant }, `content-${variant}`)
      );
      expect(markup, variant).toContain(`content-${variant}`);
      expect(markup, variant).not.toContain("style=");
    }
  });

  it("renders a whole stagger group, every child present", () => {
    const markup = renderToStaticMarkup(
      createElement(
        Stagger,
        { as: "ul" },
        createElement(Reveal, { key: "a" }, "first"),
        createElement(Reveal, { key: "b" }, "second"),
        createElement(Reveal, { key: "c" }, "third")
      )
    );
    expect(markup).toContain("<ul");
    for (const text of ["first", "second", "third"]) {
      expect(markup).toContain(text);
    }
    expect(markup).not.toContain("style=");
  });

  it("renders the same through the provider", () => {
    const markup = renderToStaticMarkup(
      createElement(
        MotionConfig,
        null,
        createElement(Reveal, null, "through the provider")
      )
    );
    expect(markup).toContain("through the provider");
    expect(markup).not.toContain("style=");
  });

  it("renders the provider's kill switch without swallowing the tree", () => {
    const markup = renderToStaticMarkup(
      createElement(
        MotionConfig,
        { enabled: false },
        createElement(Reveal, null, "still here")
      )
    );
    expect(markup).toContain("still here");
  });
});

describe("server markup tells the truth about numbers", () => {
  it("renders the measured figure, not a zero the count would start from", () => {
    const markup = renderToStaticMarkup(
      createElement(CountUp, { value: 1200 })
    );
    expect(markup).toContain("1,200");
    expect(markup).toContain("tabular-nums");
    expect(markup).toContain("data-numeric");
  });

  it("renders a null as words, with no digits anywhere", () => {
    const markup = renderToStaticMarkup(createElement(CountUp, { value: null }));
    expect(markup).toContain(NOT_OBSERVED_LABEL);
    expect(markup).toContain('data-not-observed="true"');
    expect(markup).not.toMatch(/>[^<]*\d[^<]*</);
  });

  it("keeps the caller's prefix and suffix on both the seen and spoken copies", () => {
    const markup = renderToStaticMarkup(
      createElement(CountUp, { value: 99.4, suffix: "%" })
    );
    expect(markup).toContain("99.4%");
    // The animating digits are hidden from screen readers; the figure the API
    // returned is what gets announced.
    expect(markup).toContain('aria-hidden="true"');
    expect(markup).toContain("sr-only");
  });
});

describe("server markup of the trace", () => {
  it("renders a decorative rule fully drawn, on existing tokens", () => {
    const markup = renderToStaticMarkup(createElement(Trace, {}));
    expect(markup).toContain("scaleX(1)");
    expect(markup).toContain("bg-brand");
    expect(markup).toContain("bg-border");
    expect(markup).toContain('aria-hidden="true"');
  });

  it("renders a real progress value as a named progress bar", () => {
    const markup = renderToStaticMarkup(
      createElement(Trace, {
        role: "progress",
        progress: 0.42,
        label: "Tasks accepted",
      })
    );
    expect(markup).toContain('role="progressbar"');
    expect(markup).toContain('aria-valuenow="42"');
    expect(markup).toContain('aria-label="Tasks accepted"');
    expect(markup).toContain("scaleX(0.42)");
  });

  it("renders no fill and claims nothing when progress was not observed", () => {
    const markup = renderToStaticMarkup(
      createElement(Trace, { role: "progress", progress: null })
    );
    expect(markup).toContain('data-not-observed="true"');
    expect(markup).not.toContain("progressbar");
    expect(markup).not.toContain("aria-valuenow");
    expect(markup).not.toContain("scaleX");
    expect(markup).not.toContain("bg-brand");
  });
});
