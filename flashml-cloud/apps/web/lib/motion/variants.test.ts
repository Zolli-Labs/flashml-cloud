import { describe, expect, it } from "vitest";

import {
  DURATIONS,
  EASINGS,
  MAX_TRAVEL_PX,
  MIN_TRAVEL_PX,
  STAGGER_BUDGET_MS,
  TRAVEL,
  seconds,
} from "./timing";
import {
  ANIMATABLE_PROPERTIES,
  MOTION_VARIANTS,
  SETTLED,
  VARIANT_NAMES,
  buildReveal,
  clampTravel,
  getVariant,
  staggerContainer,
  type VariantName,
} from "./variants";

const ENTRIES = VARIANT_NAMES.map(
  (name) => [name, MOTION_VARIANTS[name]] as const
);

const ALLOWED_BEZIERS = Object.values(EASINGS).map((easing) =>
  JSON.stringify(easing.bezier)
);

describe("the variant vocabulary", () => {
  it("names every entry and nothing else", () => {
    expect(VARIANT_NAMES.length).toBeGreaterThan(0);
    expect(new Set(VARIANT_NAMES).size).toBe(VARIANT_NAMES.length);
    for (const name of VARIANT_NAMES) {
      expect(getVariant(name)).toBe(MOTION_VARIANTS[name]);
    }
  });

  it("gives every variant the same two states, so any of them can be resolved blind", () => {
    for (const [, spec] of ENTRIES) {
      expect(Object.keys(spec).sort()).toEqual(["hidden", "visible"]);
    }
  });

  /** The one thing that actually has to be true of all of them: whatever a
   * variant does on the way in, it must end up fully present. */
  it("settles every variant's visible state at its resting value", () => {
    for (const [name, spec] of ENTRIES) {
      for (const property of ANIMATABLE_PROPERTIES) {
        const value = spec.visible[property];
        if (value === undefined) continue;
        expect(
          value,
          `${name}.visible.${property} must rest at ${SETTLED[property]}`
        ).toBe(SETTLED[property]);
      }
    }
  });

  it("never leaves a visible state transparent or collapsed", () => {
    for (const [name, spec] of ENTRIES) {
      expect(spec.visible.opacity ?? 1, name).toBeGreaterThan(0);
      expect(spec.visible.scaleX ?? 1, name).not.toBe(0);
      expect(spec.visible.scaleY ?? 1, name).not.toBe(0);
    }
  });

  /** Spec §2 rule 2. A 40px slide-up cannot be typed into this table without
   * this test going red. */
  it("keeps every travel distance inside 8-16px", () => {
    for (const [name, spec] of ENTRIES) {
      for (const axis of ["x", "y"] as const) {
        const distance = spec.hidden[axis];
        if (distance === undefined) continue;
        expect(Math.abs(distance), `${name}.hidden.${axis}`).toBeGreaterThanOrEqual(
          MIN_TRAVEL_PX
        );
        expect(Math.abs(distance), `${name}.hidden.${axis}`).toBeLessThanOrEqual(
          MAX_TRAVEL_PX
        );
      }
    }
  });

  it("animates nothing but opacity and transforms", () => {
    const allowed = new Set<string>([...ANIMATABLE_PROPERTIES, "transition"]);
    for (const [name, spec] of ENTRIES) {
      for (const state of [spec.hidden, spec.visible]) {
        for (const key of Object.keys(state)) {
          expect(allowed.has(key), `${name} animates ${key}`).toBe(true);
        }
      }
    }
  });

  it("takes every curve and every duration from the table", () => {
    const durations = new Set(Object.values(DURATIONS).map(seconds));
    for (const [name, spec] of ENTRIES) {
      const transition = spec.visible.transition;
      expect(transition, `${name} has no transition`).toBeDefined();
      expect(
        ALLOWED_BEZIERS.includes(JSON.stringify(transition?.ease)),
        `${name} uses an off-table curve`
      ).toBe(true);
      expect(durations.has(transition?.duration ?? -1), `${name}`).toBe(true);
    }
  });

  /** Spec §2 rule 5. Overshoot is reserved for the recovery beat — a machine
   * dies, its tasks go loose, completed progress does not reset — and it only
   * lands because nothing else in the system bounces. */
  it("reaches for the overshoot curve in exactly one variant", () => {
    const overshooting = ENTRIES.filter(
      ([, spec]) =>
        JSON.stringify(spec.visible.transition?.ease) ===
        JSON.stringify(EASINGS.recovery.bezier)
    ).map(([name]) => name);

    expect(overshooting).toEqual<VariantName[]>(["recovery"]);
  });

  it("draws the trace from nothing to full length", () => {
    expect(MOTION_VARIANTS.trace.hidden.scaleX).toBe(0);
    expect(MOTION_VARIANTS.trace.visible.scaleX).toBe(1);
    expect(MOTION_VARIANTS.traceVertical.hidden.scaleY).toBe(0);
    expect(MOTION_VARIANTS.traceVertical.visible.scaleY).toBe(1);
    // The same 720ms the CSS keyframe on the landing page already uses.
    expect(MOTION_VARIANTS.trace.visible.transition?.duration).toBe(
      seconds(DURATIONS.draw)
    );
  });

  it("moves nothing at all in the fade variant", () => {
    expect(MOTION_VARIANTS.fade.hidden).toEqual({ opacity: 0 });
    expect(MOTION_VARIANTS.fade.visible.x).toBeUndefined();
    expect(MOTION_VARIANTS.fade.visible.y).toBeUndefined();
  });
});

describe("buildReveal", () => {
  it("moves along the requested axis and nothing else", () => {
    const vertical = buildReveal();
    expect(vertical.hidden).toEqual({ opacity: 0, y: TRAVEL.base });
    expect(vertical.hidden.x).toBeUndefined();

    const horizontal = buildReveal({ axis: "x" });
    expect(horizontal.hidden.x).toBe(TRAVEL.base);
    expect(horizontal.hidden.y).toBeUndefined();
  });

  /** The cap is a design rule, so it is enforced rather than validated: a
   * caller who asks for 40px gets 16px, and the build does not fall over at
   * runtime over a number. */
  it("clamps travel to the 8-16px band instead of trusting the caller", () => {
    expect(buildReveal({ distancePx: 40 }).hidden.y).toBe(MAX_TRAVEL_PX);
    expect(buildReveal({ distancePx: 1 }).hidden.y).toBe(MIN_TRAVEL_PX);
    expect(clampTravel(-40)).toBe(-MAX_TRAVEL_PX);
    expect(clampTravel(-1)).toBe(-MIN_TRAVEL_PX);
    expect(clampTravel(Number.NaN)).toBe(TRAVEL.base);
  });

  it("converts milliseconds to the seconds both libraries take", () => {
    const spec = buildReveal({ durationMs: DURATIONS.enter, delayMs: 90 });
    expect(spec.visible.transition?.duration).toBe(0.32);
    expect(spec.visible.transition?.delay).toBe(0.09);
  });

  it("omits a delay rather than writing a zero", () => {
    expect(buildReveal().visible.transition?.delay).toBeUndefined();
  });

  it("defaults to the settle curve", () => {
    expect(buildReveal().visible.transition?.ease).toEqual(
      EASINGS.settle.bezier
    );
  });
});

describe("staggerContainer", () => {
  it("hands motion/react the same cadence Stagger computes", () => {
    expect(staggerContainer(4).visible.transition?.staggerChildren).toBe(0.045);
  });

  it("compresses for long lists, exactly as staggerDelays does", () => {
    const many = staggerContainer(13).visible.transition?.staggerChildren ?? 0;
    expect(many).toBe(0.03);
    expect(many * 12 * 1000).toBeLessThanOrEqual(STAGGER_BUDGET_MS);
  });

  it("hides nothing — a container is a schedule, not a state", () => {
    expect(staggerContainer(4).hidden).toEqual({});
  });

  it("survives a nonsense count rather than emitting NaN", () => {
    const spec = staggerContainer(Number.NaN);
    expect(spec.visible.transition?.staggerChildren).toBe(0.045);
  });
});

describe("SETTLED", () => {
  it("names a resting value for every animatable property", () => {
    for (const property of ANIMATABLE_PROPERTIES) {
      expect(SETTLED[property]).toBeTypeOf("number");
    }
    expect(SETTLED.opacity).toBe(1);
    expect(SETTLED.scaleX).toBe(1);
    expect(SETTLED.scaleY).toBe(1);
    expect(SETTLED.x).toBe(0);
    expect(SETTLED.y).toBe(0);
  });
});
