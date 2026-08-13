/** What every motion primitive is allowed to do, given what the reader asked
 * for and what the API actually returned.
 *
 * WHY THIS MODULE EXISTS. Two rules meet here, and both of them are the kind
 * that get remembered right up until they get forgotten:
 *
 *   Spec §2 rule 6 — reduced motion is a REAL PATH, not a switch-off.
 *     "Nothing becomes invisible or unreachable." The failure mode is
 *     specific and common: a reveal that starts at `opacity: 0` and waits for
 *     an animation that reduced motion just cancelled. The content is not
 *     slower, it is GONE. `resolveVariant(spec, "static")` returns a variant
 *     whose `hidden` state is itself fully visible, so there is no state a
 *     reduced-motion reader can be left stranded in.
 *
 *   Spec §1.1 — never render a number the API did not return.
 *     `null` means NOT OBSERVED, never `0`. A count-up is where that rule is
 *     easiest to break, because zero is such a natural place for a counter to
 *     start: animate a null and the page shows a confident, wrong number,
 *     rising. `resolveCountUp` has no code path from `null` to a figure.
 *
 * Both are resolved HERE, as pure functions over plain values, because that
 * is the only way they can be tested — `vitest.config.ts` collects
 * `**\/*.test.{ts,tsx}` and `components/motion/*` are markup. Same split as
 * `lib/job-artifacts.ts` and `lib/job-routing.ts`, for the same reason.
 *
 * THIS COMPOSES WITH THE EXISTING PROVIDER, IT DOES NOT REPLACE IT.
 * `components/landing/motion/LandingMotionProvider.tsx` already measures
 * `prefers-reduced-motion` for the landing page and hands it out as
 * `useLandingMotion().reduced`. Everything here takes that boolean as an
 * input. Landing call sites keep their provider; the console gets the thin
 * equivalent in `components/motion/MotionConfig.tsx`; both feed the same
 * rules.
 *
 * AND IT COMPOSES WITH THE CSS RESET. `app/globals.css` already forces
 * `animation-duration: 0.01ms !important` under `prefers-reduced-motion`.
 * That reset governs CSS animations and transitions only — it cannot touch a
 * rAF loop or a WAAPI animation started from JS, which is exactly what
 * `motion/react` and GSAP run. So the JS layer must resolve reduced motion
 * itself. It does not fight the reset: a resolved static variant carries
 * `duration: 0`, so the two agree on the answer and simply enforce it in
 * different places.
 */

import {
  COUNT_UP_MS,
  DURATIONS,
  EASINGS,
  easeProgress,
  interpolate,
  staggerDelays,
  type CubicBezier,
  type StaggerOptions,
} from "./timing";
import {
  ANIMATABLE_PROPERTIES,
  SETTLED,
  type MotionTarget,
  type VariantSpec,
} from "./variants";

/** `animate` — run the motion. `static` — every element is already where it
 * belongs, and nothing moves.
 *
 * Two values, not a boolean, because `reduced === false` and "motion is on"
 * are different statements once a kill switch exists. */
export type MotionMode = "animate" | "static";

export interface MotionCapability {
  /** From `prefers-reduced-motion: reduce` — `useLandingMotion().reduced` on
   * the landing page, `useMotionCapability().reduced` in the console. */
  reduced: boolean;
  /** The global kill switch. `false` turns the whole system off without
   * touching a single call site — for a screenshot pass, a debugging
   * session, or a surface that decides scroll motion is fatigue (spec §2:
   * console motion is feedback only). Defaults to on. */
  enabled?: boolean;
}

export function resolveMotionMode({
  reduced,
  enabled = true,
}: MotionCapability): MotionMode {
  return reduced || !enabled ? "static" : "animate";
}

/** Is this target something a reader can actually see?
 *
 * Translation does not count against visibility — a shifted element is still
 * on the page. Zero opacity and zero scale do. This is the predicate
 * `reduced.test.ts` runs over every state of every variant under `static`. */
export function isVisibleTarget(target: MotionTarget): boolean {
  const opacity = target.opacity ?? SETTLED.opacity;
  const scaleX = target.scaleX ?? SETTLED.scaleX;
  const scaleY = target.scaleY ?? SETTLED.scaleY;
  return opacity > 0 && scaleX !== 0 && scaleY !== 0;
}

/** Resolve one target for a mode.
 *
 * Under `static`, every animatable property PRESENT on the target is replaced
 * by its settled value and the duration is zeroed. Properties that were not
 * there stay absent — the resolver must not start writing `transform` onto an
 * element whose variant never asked for one.
 *
 * This is the whole reduced-motion guarantee, in five lines: a resolved
 * target cannot be invisible, because the only values it can hold are the
 * settled ones. */
export function resolveTarget(
  target: MotionTarget,
  mode: MotionMode
): MotionTarget {
  if (mode === "animate") return target;

  const resolved: MotionTarget = {};
  for (const property of ANIMATABLE_PROPERTIES) {
    if (target[property] !== undefined) resolved[property] = SETTLED[property];
  }
  resolved.transition = { duration: 0 };
  return resolved;
}

/** Resolve a whole variant. Under `static` BOTH states settle, which is the
 * point: an element primed into `hidden` before a reader flips the OS setting
 * — or before an animation that never arrives — is still readable. */
export function resolveVariant(
  spec: VariantSpec,
  mode: MotionMode
): VariantSpec {
  if (mode === "animate") return spec;
  return {
    hidden: resolveTarget(spec.hidden, mode),
    visible: resolveTarget(spec.visible, mode),
  };
}

/** Per-child stagger delays, or all zeros under reduced motion. A staggered
 * arrival with no animation is just content appearing late. */
export function resolveStaggerDelays(
  count: number,
  mode: MotionMode,
  options: StaggerOptions = {}
): number[] {
  if (mode === "static") {
    if (!Number.isFinite(count) || count <= 0) return [];
    return new Array<number>(Math.floor(count)).fill(0);
  }
  return staggerDelays(count, options);
}

/** `in-view` — a scroll reveal: the element waits until it crosses the reveal
 * line. `mount` — an entrance: it animates as soon as it exists, wherever it
 * is. Landing heroes are `mount`; everything below the fold is `in-view`. */
export type RevealTrigger = "in-view" | "mount";

export type RevealPlanReason =
  | "reduced-motion"
  | "no-observer"
  | "already-read"
  | "on-mount"
  | "on-view";

export interface RevealPlan {
  /** Put the element into its `hidden` state. THE ONLY DANGEROUS FLAG IN
   * THIS FILE — everything else can only fail to animate, this one can hide
   * content. It is false in every uncertain case. */
  prime: boolean;
  animate: boolean;
  /** Whether the animation waits on an IntersectionObserver. */
  waitForInView: boolean;
  reason: RevealPlanReason;
}

export interface RevealEnvironment {
  mode: MotionMode;
  trigger?: RevealTrigger;
  /** Had the element already crossed the reveal line when it mounted?
   * `lib/motion/timing.ts#isPastRevealLine` answers this, and answers `true`
   * whenever it cannot tell. */
  inViewAtMount: boolean;
  /** Whether `IntersectionObserver` exists and could be constructed. */
  observerAvailable?: boolean;
}

/** Should this element be hidden and revealed, and when?
 *
 * THE RULE THAT MATTERS IS `already-read`. An element that is ALREADY ON
 * SCREEN when the component mounts is never primed. Hydration happens after
 * the server markup has painted, so priming an on-screen element means
 * taking away something the reader is already looking at and fading it back
 * in — a flicker on the exact content that mattered enough to be above the
 * fold. Off-screen elements are primed freely: nobody can see it happen, and
 * by the time they scroll there the reveal is real.
 *
 * That rule is also why `Reveal` never ships `opacity: 0` in server markup —
 * priming is a client-side effect, so the server renders the settled state
 * and a reader with no JS, a failed hydration, or a thrown observer keeps the
 * whole page. `lib/landing-cinematic.test.ts` holds the same line for the
 * landing components. */
export function resolveRevealPlan({
  mode,
  trigger = "in-view",
  inViewAtMount,
  observerAvailable = true,
}: RevealEnvironment): RevealPlan {
  if (mode === "static") {
    return {
      prime: false,
      animate: false,
      waitForInView: false,
      reason: "reduced-motion",
    };
  }

  if (trigger === "mount") {
    return {
      prime: true,
      animate: true,
      waitForInView: false,
      reason: "on-mount",
    };
  }

  if (!observerAvailable) {
    return {
      prime: false,
      animate: false,
      waitForInView: false,
      reason: "no-observer",
    };
  }

  if (inViewAtMount) {
    return {
      prime: false,
      animate: false,
      waitForInView: false,
      reason: "already-read",
    };
  }

  return { prime: true, animate: true, waitForInView: true, reason: "on-view" };
}

/** What the console says instead of a number nobody measured. Lowercase and
 * flat on purpose: it is a statement about our evidence, not an error. */
export const NOT_OBSERVED_LABEL = "not observed";

/** More precision than this is noise from a float, not a measurement. */
export const MAX_COUNT_DECIMALS = 4;

/** How many decimals the API's own figure carries.
 *
 * The display must not invent precision (`12` is not `12.00`) and must not
 * drop it either (`0.5` is not `1`). So the number's own representation
 * decides, capped — `0.1 + 0.2` arrives here as 17 decimal places of float
 * residue and none of them are evidence. */
export function decimalsFor(value: number, max = MAX_COUNT_DECIMALS): number {
  if (!Number.isFinite(value)) return 0;
  const text = Math.abs(value).toString();
  if (text.includes("e") || text.includes("E")) return 0;
  const dot = text.indexOf(".");
  if (dot === -1) return 0;
  return Math.min(max, text.length - dot - 1);
}

/** Fixed locale, deliberately: this console is English-only, and a format
 * that changes with the machine's locale is a format a test cannot pin. */
export function formatCountValue(
  value: number,
  decimals: number,
  { grouping = true }: { grouping?: boolean } = {}
): string {
  if (!Number.isFinite(value)) return NOT_OBSERVED_LABEL;
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
    useGrouping: grouping,
  }).format(value);
}

export type CountUpPlan =
  | { kind: "not-observed"; label: string }
  | { kind: "final"; value: number; decimals: number }
  | {
      kind: "count";
      from: number;
      to: number;
      decimals: number;
      /** ms */
      durationMs: number;
      easing: CubicBezier;
    };

export interface CountUpRequest {
  /** The API's value. `null` means NOT OBSERVED and is not a number this
   * console is willing to guess at. */
  value: number | null;
  mode: MotionMode;
  /** Where the count starts. Zero by default. A start value is an ANIMATION
   * ORIGIN, not a reading — the only figure this component ever asserts is
   * `value`, and it asserts it before the animation begins (server markup)
   * and after it ends (exactly, see `countUpValueAt`). */
  from?: number;
  /** ms */
  durationMs?: number;
  notObservedLabel?: string;
  trigger?: RevealTrigger;
  /** Defaults to `true` — "assume it is on screen", which resolves to
   * rendering the final figure immediately. Every uncertain path in this
   * module ends at the real number. */
  inViewAtMount?: boolean;
  observerAvailable?: boolean;
  /** Override the precision derived from the value itself. */
  decimals?: number;
}

/** Spec §2 rule 3, in one function: a measured value counts to its figure; a
 * `null` renders "not observed" and does not move.
 *
 * THERE IS NO PATH FROM `null` TO A NUMBER. Not through a default, not
 * through a fallback, not through `Number(x) || 0`. `NaN` and `Infinity` take
 * the same exit as `null`: they are what a broken read looks like after it
 * has been through arithmetic, and a `NaN` displayed as `0` is a fabricated
 * measurement that is indistinguishable from a real one afterwards.
 *
 * The easing is `settle` and is not configurable. `EASINGS.recovery`
 * overshoots, and a number that overshoots displays a figure larger than the
 * one the API returned. `interpolate` clamps, so it would stall rather than
 * lie — but a counter designed to need that clamp is a counter one edit away
 * from lying. */
export function resolveCountUp({
  value,
  mode,
  from = 0,
  durationMs = COUNT_UP_MS,
  notObservedLabel = NOT_OBSERVED_LABEL,
  trigger = "in-view",
  inViewAtMount = true,
  observerAvailable = true,
  decimals,
}: CountUpRequest): CountUpPlan {
  if (value === null || typeof value !== "number" || !Number.isFinite(value)) {
    return { kind: "not-observed", label: notObservedLabel };
  }

  const precision = decimals ?? decimalsFor(value);
  const plan = resolveRevealPlan({
    mode,
    trigger,
    inViewAtMount,
    observerAvailable,
  });

  if (!plan.animate) return { kind: "final", value, decimals: precision };
  if (!Number.isFinite(from) || from === value) {
    return { kind: "final", value, decimals: precision };
  }

  return {
    kind: "count",
    from,
    to: value,
    decimals: precision,
    durationMs,
    easing: EASINGS.settle.bezier,
  };
}

/** Where the counter is at `elapsedMs`.
 *
 * THE LAST FRAME IS EXACTLY `to`. Not `to` within a rounding error, not `to`
 * after the formatter tidies it up — the identical number the API returned,
 * so the figure left on screen is the figure that was measured. */
export function countUpValueAt(
  plan: Extract<CountUpPlan, { kind: "count" }>,
  elapsedMs: number
): number {
  if (!Number.isFinite(elapsedMs) || elapsedMs <= 0) return plan.from;
  if (plan.durationMs <= 0 || elapsedMs >= plan.durationMs) return plan.to;
  return interpolate(
    plan.from,
    plan.to,
    easeProgress(plan.easing, elapsedMs / plan.durationMs)
  );
}

/** `rule` — a section underscore or a diagram connector. It always draws to
 * full, because it is measuring nothing. `progress` — a REAL fraction the API
 * returned, where the length of the line is a claim about the world. */
export type TraceRole = "rule" | "progress";

export type TracePlan =
  | { kind: "not-observed" }
  /** Render at `extent` immediately, no animation. */
  | { kind: "drawn"; extent: number }
  /** Animate from nothing to `extent`. */
  | {
      kind: "draw";
      extent: number;
      /** ms */
      durationMs: number;
      easing: CubicBezier;
    };

export interface TraceRequest {
  role?: TraceRole;
  /** `0`-`1`. Required and meaningful only for `role: "progress"`; `null`
   * means the API did not report it. */
  progress?: number | null;
  mode: MotionMode;
  trigger?: RevealTrigger;
  inViewAtMount?: boolean;
  observerAvailable?: boolean;
  /** ms */
  durationMs?: number;
}

/** Float residue on a value that came out of a division. Anything further
 * outside `0`-`1` than this is not a rounding artefact, it is a value this
 * console does not understand. */
const PROGRESS_TOLERANCE = 1e-6;

/** Spec §2 rule 4: the trace is the signature — and when it is carrying a
 * real number it is also a claim.
 *
 * AN UNREADABLE PROGRESS VALUE DRAWS NOTHING, IT DOES NOT DRAW ZERO. `null`,
 * `NaN`, `-0.4` and `1.7` all resolve to `not-observed`, so the caller
 * renders a track with no fill and says so. A fill of zero length is a
 * statement — "this job has made no progress" — and it is one we would be
 * making without evidence. Out-of-range is included on purpose: a `1.7` is a
 * bug somewhere upstream, and clamping it to a confident full bar is how that
 * bug reaches the reader looking like a finished job. */
export function resolveTrace({
  role = "rule",
  progress = null,
  mode,
  trigger = "in-view",
  inViewAtMount = true,
  observerAvailable = true,
  durationMs,
}: TraceRequest): TracePlan {
  let extent = 1;

  if (role === "progress") {
    if (
      progress === null ||
      typeof progress !== "number" ||
      !Number.isFinite(progress) ||
      progress < -PROGRESS_TOLERANCE ||
      progress > 1 + PROGRESS_TOLERANCE
    ) {
      return { kind: "not-observed" };
    }
    extent = Math.min(1, Math.max(0, progress));
  }

  const plan = resolveRevealPlan({
    mode,
    trigger,
    inViewAtMount,
    observerAvailable,
  });

  if (!plan.animate) return { kind: "drawn", extent };

  return {
    kind: "draw",
    extent,
    durationMs:
      durationMs ?? (role === "progress" ? DURATIONS.settle : DURATIONS.draw),
    /** A progress trace advances linearly. An eased progress bar sprints and
     * then crawls, which is a bar telling you something about pace that the
     * job did not do. A decorative rule has no pace to misreport. */
    easing:
      role === "progress" ? EASINGS.linear.bezier : EASINGS.settle.bezier,
  };
}
