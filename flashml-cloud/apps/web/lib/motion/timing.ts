/** The one duration/easing/cadence table for this app, in both dialects.
 *
 * WHY THIS MODULE EXISTS. Three motion systems already run here — GSAP +
 * `useGSAP` (`components/landing/motion/SectionReveal.tsx`, `SystemJourney`,
 * `CommitSignal`, `RecoveryStack`), `motion/react` (`WorkflowScene`,
 * `WorkloadRows`) and a bespoke rAF loop (`coordinator-map/useMapStory.ts`) —
 * and each one picked its own numbers. `"power2.out"` here, `[0.22, 1, 0.36,
 * 1]` there, `"easeOut"` next door; durations spread from 0.28s to 1s with no
 * rule behind any of them. Nothing was wrong; nothing agreed either, and two
 * screens animating at almost-but-not-quite the same rate is exactly what
 * reads as unfinished.
 *
 * So this file is a TABLE, not a helper library. It exports every value in
 * every form the two libraries need, from a single definition, so GSAP and
 * `motion/react` cannot drift apart:
 *
 *   - `bezier`    the canonical curve, and what `motion/react` takes
 *   - `css`       `cubic-bezier(...)` for stylesheets and inline transitions
 *   - `gsapName`  the nearest BUILT-IN GSAP ease, for call sites that will
 *                 not register a plugin (an approximation — see below)
 *   - `gsapPath`  the exact same curve as an SVG path, for
 *                 `CustomEase.create(name, path)` when parity must be exact
 *
 * VALUES ARE ANCHORED ON WHAT IS ALREADY IN THE WILD. Adopting this table
 * should be a small diff, not a re-tune: `settle` is the curve
 * `@keyframes workflow-scene-draw` already uses, `control` is the curve
 * `.interactive` already uses, `glide` is the one the flow canvas already
 * uses, `draw` is that keyframe's own 720ms, `state` is the 240ms the
 * workflow step transition already runs at, and the reveal trigger line is
 * `SectionReveal`'s `start: "top 82%"` expressed as a number.
 *
 * MILLISECONDS ARE CANONICAL. CSS speaks ms and `globals.css` is full of it;
 * GSAP and `motion/react` both speak seconds. Storing ms and converting with
 * `seconds()` means the stylesheet and the JS layer quote the same integer,
 * and a reviewer comparing them does not have to move a decimal point.
 *
 * THE SIX RULES THIS TABLE ENCODES (spec §2):
 *   1. Settle, never bounce — `EASINGS.settle` is the default; the one curve
 *      that overshoots is named `recovery` and is reserved for rule 5.
 *   2. Reveal is a readout printing — 45ms cadence, 8–16px of travel.
 *   3. Numbers resolve — `COUNT_UP_MS`, and only on values the API returned
 *      (`lib/motion/reduced.ts` owns that decision).
 *   4. The trace is the signature — `DURATIONS.draw`.
 *   5. Interruption is the one emotional beat — `EASINGS.recovery`.
 *   6. Reduced motion is a real path — `lib/motion/reduced.ts`.
 */

/** A CSS cubic-bezier's two control points, `[x1, y1, x2, y2]`. Deliberately
 * a mutable tuple rather than a `readonly` one: `motion/react`'s `Easing`
 * type takes `[number, number, number, number]`, and a readonly tuple is not
 * assignable to it, which would push every call site into a cast. */
export type CubicBezier = [number, number, number, number];

export type EasingName =
  | "settle"
  | "control"
  | "glide"
  | "exit"
  | "linear"
  | "recovery";

export interface Easing {
  readonly name: EasingName;
  /** The curve. What `motion/react` takes directly. */
  readonly bezier: CubicBezier;
  /** `cubic-bezier(...)`, for stylesheets and inline `transition` strings. */
  readonly css: string;
  /** The nearest ease GSAP ships built in. AN APPROXIMATION, and named as
   * one: GSAP's power curves are not cubic-beziers, so this is within a few
   * percent, not identical. Fine for a 180ms hover; use `gsapPath` with
   * `CustomEase` where the same element also animates under `motion/react`
   * and the two must match exactly. */
  readonly gsapName: string;
  /** The exact curve as an SVG path, for `CustomEase.create(name, path)`.
   * `gsap/CustomEase` ships in the free package (verified in 3.15.0), so
   * exact parity costs one `registerPlugin` call and no licence. */
  readonly gsapPath: string;
}

export function cssCubicBezier([x1, y1, x2, y2]: CubicBezier): string {
  return `cubic-bezier(${x1}, ${y1}, ${x2}, ${y2})`;
}

/** A cubic-bezier's control points as the path `CustomEase` parses. The two
 * endpoints are fixed at 0,0 and 1,1 exactly as CSS fixes them, so the two
 * forms describe one curve and cannot disagree. */
export function customEasePath([x1, y1, x2, y2]: CubicBezier): string {
  return `M0,0 C${x1},${y1} ${x2},${y2} 1,1`;
}

function easing(
  name: EasingName,
  bezier: CubicBezier,
  gsapName: string
): Easing {
  return {
    name,
    bezier,
    css: cssCubicBezier(bezier),
    gsapName,
    gsapPath: customEasePath(bezier),
  };
}

/** Every curve this app is allowed to animate on.
 *
 * `settle`, `control` and `glide` are lifted VERBATIM from `app/globals.css`
 * — they are already the house feel, they were just never named. */
export const EASINGS: Readonly<Record<EasingName, Easing>> = Object.freeze({
  /** THE DEFAULT. Fast out, long slow settle — a readout coming to rest.
   * Verbatim from `@keyframes workflow-scene-draw`. */
  settle: easing("settle", [0.2, 0.75, 0.25, 1], "power2.out"),
  /** Short feedback on something the pointer or keyboard is touching:
   * hover, press, focus. Verbatim from `.interactive`. */
  control: easing("control", [0.2, 0.7, 0.3, 1], "power2.out"),
  /** Longer transform moves that must not look flung. Verbatim from
   * `.flashml-flow .react-flow__node`. */
  glide: easing("glide", [0.2, 0.8, 0.2, 1], "power3.out"),
  /** Leaving. Accelerates away rather than easing out, so a dismissal does
   * not linger and read as hesitation. CSS `ease-in`. */
  exit: easing("exit", [0.4, 0, 1, 1], "power2.in"),
  /** No easing at all. The trace advancing under a REAL progress value uses
   * this: an eased progress bar is a bar that lies about pace. */
  linear: easing("linear", [0, 0, 1, 1], "none"),
  /** THE ONE CURVE THAT OVERSHOOTS (`y2 > 1` on the way through), reserved
   * for the recovery beat in spec §2 rule 5 — a machine dies, its tasks go
   * loose, and completed progress does NOT reset. Everything else in this
   * table stays quiet so that moment lands. `lib/motion/variants.test.ts`
   * asserts exactly one variant reaches for it. */
  recovery: easing("recovery", [0.34, 1.56, 0.64, 1], "back.out(1.7)"),
});

export const DEFAULT_EASING: Easing = EASINGS.settle;

export type DurationName =
  | "tap"
  | "control"
  | "state"
  | "enter"
  | "settle"
  | "reveal"
  | "draw";

/** Milliseconds. Seven steps, and no eighth — a scale with a value for every
 * occasion is not a scale.
 *
 * `control`, `state` and `draw` are the exact numbers already in
 * `globals.css` (`.interactive` 180ms, the workflow step transition 240ms,
 * `workflow-scene-draw` 720ms). `reveal` is 560ms against `SectionReveal`'s
 * existing 0.55s content / 0.6s line, so adopting it moves those by 10-40ms.
 * `.card-hover`'s 220ms folds into `state` (240ms) — a 20ms re-tune, taken
 * deliberately to avoid carrying 220 and 240 as separate rungs. */
export const DURATIONS: Readonly<Record<DurationName, number>> = Object.freeze({
  /** A press acknowledging itself. Anything slower feels laggy. */
  tap: 120,
  /** Hover, focus ring, small colour changes. `.interactive`, verbatim. */
  control: 180,
  /** A state badge changing, a row switching emphasis. Verbatim from the
   * workflow-step transition. */
  state: 240,
  /** Something arriving that the reader was not already looking at. */
  enter: 320,
  /** A value or a panel coming to rest after a change. */
  settle: 420,
  /** A group of content printing in on first sight. */
  reveal: 560,
  /** The trace drawing its full length. `workflow-scene-draw`, verbatim. */
  draw: 720,
});

/** How long a measured number takes to resolve to its figure (spec §2 rule
 * 3). Same as `reveal`, because a count-up that outlasts the block it sits
 * in is still moving after the reader has finished reading it. */
export const COUNT_UP_MS = DURATIONS.reveal;

/** Spec §2 rule 2: "grouped elements stagger along the reading axis at a
 * tight 45ms cadence". */
export const STAGGER_CADENCE_MS = 45;

/** The whole stagger, first child to last, may not exceed this. 45ms × 8
 * gaps. Past that the cadence compresses (see `staggerDelays`) — a 30-row
 * table at a fixed 45ms would take 1.3 seconds to finish arriving, and the
 * last row would animate long after the reader reached it. */
export const STAGGER_BUDGET_MS = 360;

export interface StaggerOptions {
  cadenceMs?: number;
  budgetMs?: number;
  /** Delay applied to every child, e.g. to let a heading land first. */
  startMs?: number;
}

/** The delay, in ms, for each of `count` siblings.
 *
 * Returns integers: a delay of 44.99999999999999 is the same delay and a
 * worse thing to read in a test failure. */
export function staggerDelays(
  count: number,
  {
    cadenceMs = STAGGER_CADENCE_MS,
    budgetMs = STAGGER_BUDGET_MS,
    startMs = 0,
  }: StaggerOptions = {}
): number[] {
  if (!Number.isFinite(count) || count <= 0) return [];
  const total = Math.floor(count);
  const start = Number.isFinite(startMs) ? Math.max(0, startMs) : 0;
  if (total === 1) return [Math.round(start)];

  const gaps = total - 1;
  const cadence = Math.min(cadenceMs, budgetMs / gaps);
  const delays: number[] = [];
  for (let i = 0; i < total; i += 1) {
    delays.push(Math.round(start + i * cadence));
  }
  return delays;
}

export type TravelName = "tight" | "base" | "loose";

/** Pixels of travel on a reveal. Spec §2 rule 2 caps this at 8–16px: "big
 * 40px slide-ups read as a template". `lib/motion/variants.test.ts` holds
 * that ceiling against every variant. */
export const TRAVEL: Readonly<Record<TravelName, number>> = Object.freeze({
  /** Dense rows, table lines, list items. */
  tight: 8,
  /** The default. */
  base: 12,
  /** A whole section or a hero block, where 12px would not register. */
  loose: 16,
});

export const MIN_TRAVEL_PX = TRAVEL.tight;
export const MAX_TRAVEL_PX = TRAVEL.loose;

/** Where the reveal line sits, as a percentage of viewport height from the
 * top. 82 because `SectionReveal` already triggers at `"top 82%"`, and two
 * reveal thresholds on one page is a page where things arrive twice. */
export const REVEAL_TRIGGER_PERCENT = 82;

/** The GSAP ScrollTrigger form. */
export function scrollTriggerStart(percent = REVEAL_TRIGGER_PERCENT): string {
  return `top ${percent}%`;
}

/** The IntersectionObserver form of the SAME line: pull the root's bottom
 * edge up by the remaining 18% and any intersection means "past the line".
 * `motion/react`'s `viewport.margin` takes this string too. */
export function intersectionRootMargin(
  percent = REVEAL_TRIGGER_PERCENT
): string {
  return `0px 0px -${100 - percent}% 0px`;
}

/** Has this element already crossed the reveal line?
 *
 * VISIBLE-SAFE ON NONSENSE INPUT. A zero or non-finite viewport height — a
 * headless render, a hidden tab, a browser that has not laid out yet —
 * answers `true`, which means "do not prime this element hidden". Every
 * uncertain answer in this module resolves towards content being on screen. */
export function isPastRevealLine(
  rectTop: number,
  viewportHeight: number,
  percent = REVEAL_TRIGGER_PERCENT
): boolean {
  if (!Number.isFinite(viewportHeight) || viewportHeight <= 0) return true;
  if (!Number.isFinite(rectTop)) return true;
  return rectTop <= viewportHeight * (percent / 100);
}

/** ms → s, for GSAP and `motion/react`. Rounded first so 420ms is 0.42 and
 * not 0.42000000000000004. */
export function seconds(ms: number): number {
  if (!Number.isFinite(ms)) return 0;
  return Math.round(ms) / 1000;
}

/** The value of a cubic-bezier at `x` (linear time in `[0, 1]`), returning
 * eased progress.
 *
 * `CountUp` needs this: it interpolates a number over real time and cannot
 * hand the job to CSS. Newton-Raphson with a bisection fallback, which is
 * how browsers solve the same curve.
 *
 * The result is NOT clamped to `[0, 1]` — `EASINGS.recovery` genuinely
 * passes above 1 before settling, and clamping would silently flatten the
 * one beat this whole system reserves overshoot for. */
export function easeProgress(bezier: CubicBezier, x: number): number {
  const [x1, y1, x2, y2] = bezier;
  if (!Number.isFinite(x) || x <= 0) return 0;
  if (x >= 1) return 1;
  return bezierAxis(y1, y2, solveBezierT(x1, x2, x));
}

/** One axis of a cubic bezier whose endpoints are pinned at 0 and 1. */
function bezierAxis(a1: number, a2: number, t: number): number {
  const inv = 1 - t;
  return 3 * inv * inv * t * a1 + 3 * inv * t * t * a2 + t * t * t;
}

function bezierAxisSlope(a1: number, a2: number, t: number): number {
  const inv = 1 - t;
  return 3 * inv * inv * a1 + 6 * inv * t * (a2 - a1) + 3 * t * t * (1 - a2);
}

function solveBezierT(x1: number, x2: number, x: number): number {
  let t = x;
  for (let i = 0; i < 8; i += 1) {
    const error = bezierAxis(x1, x2, t) - x;
    if (Math.abs(error) < 1e-7) return t;
    const slope = bezierAxisSlope(x1, x2, t);
    if (Math.abs(slope) < 1e-7) break;
    t -= error / slope;
  }

  // Newton-Raphson can wander off a nearly flat section of the curve.
  // Bisection cannot, so it finishes the job.
  let low = 0;
  let high = 1;
  t = x;
  for (let i = 0; i < 24; i += 1) {
    const value = bezierAxis(x1, x2, t);
    if (Math.abs(value - x) < 1e-7) break;
    if (value > x) high = t;
    else low = t;
    t = (low + high) / 2;
  }
  return t;
}

/** Linear interpolation, clamped at both ends.
 *
 * THE CLAMP IS AN HONESTY GUARD, not tidiness. `EASINGS.recovery` returns
 * progress above 1, and a count-up eased on it would otherwise display a
 * number LARGER than the one the API returned before settling back. A
 * fabricated number that appears for 90ms is still a fabricated number. */
export function interpolate(from: number, to: number, progress: number): number {
  if (!Number.isFinite(progress)) return to;
  const p = Math.min(1, Math.max(0, progress));
  return from + (to - from) * p;
}
