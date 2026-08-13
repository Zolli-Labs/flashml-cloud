/** The named motion vocabulary: what "reveal" or "trace" actually means, as
 * data.
 *
 * WHY THIS MODULE EXISTS. `lib/motion/timing.ts` says how fast and on what
 * curve; this says which properties move and how far. Together they are the
 * whole decision, which is why they are here in `lib/` and unit tested rather
 * than inline in a `.tsx` — `vitest.config.ts` collects only
 * `**\/*.test.{ts,tsx}`, and the house split (`lib/job-artifacts.ts`,
 * `lib/job-routing.ts`, `lib/task-checkpoints.ts`) puts every decision
 * somewhere a test can reach it. `components/motion/*` render these; they do
 * not choose them.
 *
 * SHAPE. Every variant is `{ hidden, visible }` and nothing else. Two states,
 * always the same two names, so `lib/motion/reduced.ts` can resolve any
 * variant without knowing which one it is, and so a reviewer can check the
 * one thing that actually matters: THAT `visible` IS FULLY VISIBLE, in every
 * variant, with no exceptions.
 *
 * UNITS. `duration` and `delay` are SECONDS here, not milliseconds — that is
 * what `motion/react` and GSAP both take, and a variant is consumed directly
 * by them. The millisecond originals live in `timing.ts`; `seconds()` is the
 * only conversion and it happens here, once per value.
 *
 * THE CEILING THIS FILE HOLDS. Spec §2 rule 2 caps travel at 8-16px. Nothing
 * in this file, and nothing `buildReveal` will produce, travels further —
 * `variants.test.ts` asserts it against every variant, so the next person to
 * add one cannot quietly ship a 40px slide-up.
 */

import {
  DURATIONS,
  EASINGS,
  MAX_TRAVEL_PX,
  MIN_TRAVEL_PX,
  STAGGER_BUDGET_MS,
  STAGGER_CADENCE_MS,
  TRAVEL,
  seconds,
  type CubicBezier,
} from "./timing";

/** A subset of `motion/react`'s target shape — the properties this app is
 * allowed to animate, and no others.
 *
 * DELIBERATELY NARROW. Compositor-friendly properties only: opacity and
 * transforms. Animating `height`, `top` or `filter` is how a page starts
 * dropping frames on the machines this product is aimed at, and none of the
 * six rules in spec §2 needs them. Structurally assignable to
 * `motion/react`'s `TargetAndTransition`, which `tsc` checks at the call
 * sites in `components/motion/*`; `lib/` itself stays framework-free so the
 * GSAP call sites can import the same table. */
export interface MotionTarget {
  opacity?: number;
  /** px */
  x?: number;
  /** px */
  y?: number;
  scaleX?: number;
  scaleY?: number;
  transition?: MotionTransition;
}

export interface MotionTransition {
  /** SECONDS. */
  duration?: number;
  ease?: CubicBezier;
  /** SECONDS. */
  delay?: number;
  /** SECONDS. */
  staggerChildren?: number;
  /** SECONDS. */
  delayChildren?: number;
}

/** Two states, fixed names. `hidden` is where an element waits; `visible` is
 * where it must always end up. */
export interface VariantSpec {
  hidden: MotionTarget;
  visible: MotionTarget;
}

export type Axis = "y" | "x";

export interface RevealOptions {
  axis?: Axis;
  /** px. CLAMPED to 8-16 (spec §2 rule 2). Passing 40 gets you 16, not 40,
   * and not an exception — the cap is a design rule, and a build that fails
   * at runtime because someone typed a bigger number is worse than a build
   * that quietly obeys the rule it documents. `variants.test.ts` pins the
   * clamp so the behaviour is discoverable. */
  distancePx?: number;
  /** ms */
  durationMs?: number;
  easing?: CubicBezier;
  /** ms */
  delayMs?: number;
}

export function clampTravel(distancePx: number): number {
  if (!Number.isFinite(distancePx)) return TRAVEL.base;
  const magnitude = Math.min(MAX_TRAVEL_PX, Math.max(MIN_TRAVEL_PX, Math.abs(distancePx)));
  return distancePx < 0 ? -magnitude : magnitude;
}

/** Build a reveal: a small move along one axis plus an opacity resolve.
 *
 * A POSITIVE DISTANCE MEANS THE ELEMENT STARTS BELOW (or right of) ITS
 * RESTING PLACE and rises into it, which is the direction a readout prints. */
export function buildReveal({
  axis = "y",
  distancePx = TRAVEL.base,
  durationMs = DURATIONS.reveal,
  easing = EASINGS.settle.bezier,
  delayMs = 0,
}: RevealOptions = {}): VariantSpec {
  const travel = clampTravel(distancePx);
  const transition: MotionTransition = {
    duration: seconds(durationMs),
    ease: easing,
  };
  if (delayMs > 0) transition.delay = seconds(delayMs);

  const hidden: MotionTarget = { opacity: 0 };
  const visible: MotionTarget = { opacity: 1, transition };
  if (axis === "x") {
    hidden.x = travel;
    visible.x = 0;
  } else {
    hidden.y = travel;
    visible.y = 0;
  }

  return { hidden, visible };
}

export type VariantName =
  | "reveal"
  | "revealTight"
  | "revealLoose"
  | "revealInline"
  | "fade"
  | "trace"
  | "traceVertical"
  | "recovery";

/** The whole vocabulary. Eight entries, and adding a ninth should require an
 * argument — a motion system with a variant per screen is the ad-hoc state
 * this file was written to end. */
export const MOTION_VARIANTS: Readonly<Record<VariantName, VariantSpec>> =
  Object.freeze({
    /** The default. A paragraph, a card, a panel arriving. */
    reveal: buildReveal(),

    /** Dense repeating rows — table lines, list items, event log entries.
     * Shorter and closer, because twenty of these run at once. */
    revealTight: buildReveal({
      distancePx: TRAVEL.tight,
      durationMs: DURATIONS.enter,
    }),

    /** A whole section or a hero block, where 12px does not register. */
    revealLoose: buildReveal({ distancePx: TRAVEL.loose }),

    /** Along the reading axis rather than up the page: a rail, a breadcrumb,
     * a value sliding in beside its label. */
    revealInline: buildReveal({ axis: "x", distancePx: TRAVEL.base }),

    /** No travel at all. For anything whose position is information — a
     * number in a table, a status dot — where a 12px lift would read as the
     * value itself moving. */
    fade: {
      hidden: { opacity: 0 },
      visible: {
        opacity: 1,
        transition: {
          duration: seconds(DURATIONS.enter),
          ease: EASINGS.settle.bezier,
        },
      },
    },

    /** THE SIGNATURE (spec §2 rule 4): a hairline that advances. Scales from
     * its leading edge, so it reads as drawing rather than growing. Same
     * 720ms as `@keyframes workflow-scene-draw`, which is the same motif
     * rendered in CSS on the landing page. */
    trace: {
      hidden: { scaleX: 0 },
      visible: {
        scaleX: 1,
        transition: {
          duration: seconds(DURATIONS.draw),
          ease: EASINGS.settle.bezier,
        },
      },
    },

    /** The same trace turned ninety degrees, for the connective line down a
     * timeline or between diagram nodes. */
    traceVertical: {
      hidden: { scaleY: 0 },
      visible: {
        scaleY: 1,
        transition: {
          duration: seconds(DURATIONS.draw),
          ease: EASINGS.settle.bezier,
        },
      },
    },

    /** THE ONE PLACE OVERSHOOT IS ALLOWED (spec §2 rule 5). A machine dies,
     * the tasks it held go loose, and they land again — completed progress
     * does not reset. That single beat is the product's whole claim, and it
     * only lands because nothing else in this table bounces.
     *
     * `variants.test.ts` asserts that this is the only variant reaching for
     * `EASINGS.recovery`, so "reserved" is enforced rather than remembered. */
    recovery: {
      hidden: { opacity: 0, y: TRAVEL.loose },
      visible: {
        opacity: 1,
        y: 0,
        transition: {
          duration: seconds(DURATIONS.reveal),
          ease: EASINGS.recovery.bezier,
        },
      },
    },
  });

export const VARIANT_NAMES = Object.keys(MOTION_VARIANTS) as VariantName[];

export function getVariant(name: VariantName): VariantSpec {
  return MOTION_VARIANTS[name];
}

/** A `motion/react` container variant that staggers its children through
 * variant propagation.
 *
 * `components/motion/Stagger.tsx` does NOT use this — it hands each child an
 * explicit delay through context, which survives children that are not
 * `motion` elements and children that animate on their own schedule. This
 * exists for call sites already inside a `motion.div` variant tree (the
 * landing components are), so they can adopt the 45ms cadence without
 * restructuring their markup. Both routes compute the cadence from
 * `staggerDelays`' rule, so they cannot disagree.
 *
 * `count` is required for exactly that reason: the cadence compresses for
 * long lists (see `STAGGER_BUDGET_MS`), and a container that does not know
 * how many children it has cannot honour the budget. */
export function staggerContainer(
  count: number,
  { startMs = 0 }: { startMs?: number } = {}
): VariantSpec {
  const gaps = Number.isFinite(count) ? Math.max(1, Math.floor(count) - 1) : 1;
  const cadenceMs = Math.min(STAGGER_CADENCE_MS, STAGGER_BUDGET_MS / gaps);

  return {
    hidden: {},
    visible: {
      transition: {
        staggerChildren: seconds(cadenceMs),
        delayChildren: seconds(startMs),
      },
    },
  };
}

/** Every property this vocabulary is allowed to move. Used by
 * `lib/motion/reduced.ts` to resolve a variant it does not otherwise need to
 * understand, and by `variants.test.ts` to prove nothing has crept in. */
export const ANIMATABLE_PROPERTIES = [
  "opacity",
  "x",
  "y",
  "scaleX",
  "scaleY",
] as const;

export type AnimatableProperty = (typeof ANIMATABLE_PROPERTIES)[number];

/** The resting value of each property: what "fully present, not moved" is.
 * `lib/motion/reduced.ts` resolves to exactly this under reduced motion, and
 * `variants.test.ts` asserts every variant's `visible` state matches it. */
export const SETTLED: Readonly<Record<AnimatableProperty, number>> =
  Object.freeze({
    opacity: 1,
    x: 0,
    y: 0,
    scaleX: 1,
    scaleY: 1,
  });
