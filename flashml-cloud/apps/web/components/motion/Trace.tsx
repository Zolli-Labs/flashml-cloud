"use client";

/** The advancing hairline. The one recurring motif in this system.
 *
 * Spec §2 rule 4: "a hairline orange rule that advances is the one recurring
 * motif, because a job advancing is what the product is". It appears as
 * section underscores, as progress, and as the connective line in the system
 * diagram — and nowhere else, per §2's "one accessory to remove".
 *
 * TWO ROLES, AND THE DIFFERENCE IS NOT DECORATIVE.
 *
 *   `role="rule"`      an underscore or a connector. It measures nothing, so
 *                      it always draws to full length and claims nothing.
 *   `role="progress"`  the length of the line IS A CLAIM ABOUT A JOB. It is
 *                      drawn only from a number the API returned.
 *
 * A PROGRESS TRACE WITH NOTHING TO REPORT DRAWS NOTHING — IT DOES NOT DRAW
 * ZERO. `null`, `NaN`, and any value outside 0-1 render the track alone, with
 * `data-not-observed`, and the caller says why in words. A zero-length fill
 * is the sentence "this job has made no progress", and rendering it without
 * evidence is exactly the fabrication spec §1.1 forbids. `resolveTrace` in
 * `lib/motion/reduced.ts` makes that call; `lib/motion/reduced.test.ts` holds
 * it.
 *
 * COLOUR COMES FROM EXISTING TOKENS ONLY — `bg-brand` (`--z-orange`, the
 * single accent) on `bg-border` (the surface's own hairline colour, which
 * every surface class in `globals.css` already remaps). No new token, no
 * inline hex.
 *
 * REDUCED MOTION RENDERS IT DRAWN, at its true extent, immediately.
 */

import { animate } from "motion/react";
import { useCallback, useEffect, useRef } from "react";

import {
  resolveTrace,
  type RevealTrigger,
  type TraceRole,
} from "@/lib/motion/reduced";
import {
  intersectionRootMargin,
  isPastRevealLine,
  seconds,
} from "@/lib/motion/timing";
import { cn } from "@/lib/utils";

import { useMotionMode } from "./MotionConfig";

export interface TraceProps {
  role?: TraceRole;
  /** `0`-`1`, and only meaningful for `role="progress"`. `null` means the API
   * did not report it. */
  progress?: number | null;
  orientation?: "horizontal" | "vertical";
  /** Classes for the track. `h-px w-full` (or `w-px h-full`) by default —
   * override to change the length or thickness. */
  className?: string;
  /** Classes for the advancing line itself, e.g. a different tone. */
  lineClassName?: string;
  /** Required for `role="progress"`: a progress bar with no name is a bar
   * screen readers announce as a percentage of nothing. */
  label?: string;
  reduced?: boolean;
  trigger?: RevealTrigger;
  /** ms */
  durationMs?: number;
}

export function Trace({
  role = "rule",
  progress = null,
  orientation = "horizontal",
  className,
  lineClassName,
  label,
  reduced,
  trigger = "in-view",
  durationMs,
}: TraceProps) {
  const mode = useMotionMode(reduced);
  const horizontal = orientation === "horizontal";

  const elementRef = useRef<HTMLElement | null>(null);
  const setElement = useCallback((node: HTMLElement | null) => {
    elementRef.current = node;
  }, []);
  const primed = useRef(false);

  useEffect(() => {
    const element = elementRef.current;
    if (!element) return;

    const observerAvailable =
      typeof window !== "undefined" &&
      typeof IntersectionObserver !== "undefined";
    const inViewAtMount = isPastRevealLine(
      element.getBoundingClientRect().top,
      typeof window === "undefined" ? 0 : window.innerHeight
    );

    const plan = resolveTrace({
      role,
      progress,
      mode,
      trigger,
      inViewAtMount,
      observerAvailable,
      durationMs,
    });

    if (plan.kind === "not-observed") return;

    const scaled = (extent: number) =>
      horizontal ? { scaleX: extent } : { scaleY: extent };

    if (plan.kind === "drawn") {
      // Restore, in case an earlier pass primed this to zero and the reader
      // has since asked for reduced motion.
      if (primed.current) {
        void animate(element, scaled(plan.extent), { duration: 0 });
        primed.current = false;
      }
      return;
    }

    primed.current = true;
    void animate(element, scaled(0), { duration: 0 });

    let cancelled = false;
    const draw = () => {
      if (cancelled) return;
      void animate(element, scaled(plan.extent), {
        duration: seconds(plan.durationMs),
        ease: plan.easing,
      });
    };

    let observer: IntersectionObserver | null = null;
    if (!inViewAtMount && observerAvailable && trigger === "in-view") {
      try {
        observer = new IntersectionObserver(
          (entries) => {
            if (!entries.some((entry) => entry.isIntersecting)) return;
            observer?.disconnect();
            draw();
          },
          { rootMargin: intersectionRootMargin(), threshold: 0 }
        );
        observer.observe(element);
      } catch {
        draw();
      }
    } else {
      draw();
    }

    return () => {
      cancelled = true;
      observer?.disconnect();
    };
  }, [durationMs, horizontal, mode, progress, role, trigger]);

  /** Resolved at `static` on purpose: this is what the SERVER renders, and
   * the server has no preference to read. It is also the honest render —
   * fully drawn, at the real extent — so a page whose JS never arrives is
   * still telling the truth. */
  const settled = resolveTrace({ role, progress, mode: "static" });
  const observed = settled.kind !== "not-observed";
  const extent = settled.kind === "not-observed" ? 0 : settled.extent;

  const progressBar = role === "progress" && observed;

  return (
    <div
      className={cn(
        "relative overflow-hidden bg-border",
        horizontal ? "h-px w-full" : "h-full w-px",
        className
      )}
      data-motion="trace"
      data-trace-role={role}
      data-not-observed={observed ? undefined : "true"}
      aria-hidden={progressBar ? undefined : true}
      role={progressBar ? "progressbar" : undefined}
      aria-label={progressBar ? label : undefined}
      aria-valuemin={progressBar ? 0 : undefined}
      aria-valuemax={progressBar ? 100 : undefined}
      aria-valuenow={progressBar ? Math.round(extent * 100) : undefined}
    >
      {observed ? (
        <span
          ref={setElement}
          className={cn(
            "absolute inset-0 block bg-brand",
            horizontal ? "origin-left" : "origin-top",
            lineClassName
          )}
          style={{
            transform: horizontal ? `scaleX(${extent})` : `scaleY(${extent})`,
          }}
        />
      ) : null}
    </div>
  );
}
