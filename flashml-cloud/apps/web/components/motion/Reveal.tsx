"use client";

/** Content arriving: a small move plus an opacity resolve, once.
 *
 * THE ONLY THING THAT REALLY MATTERS HERE IS WHAT HAPPENS WHEN NOTHING
 * HAPPENS. A scroll reveal is a component that hides your content and
 * promises to un-hide it later, and every failure of that promise — no JS, a
 * hydration error, an observer that never fires, an OS preference flipped
 * mid-scroll — is a blank page that looks like a broken deploy. So:
 *
 *   1. THE SERVER RENDERS NOTHING HIDDEN. No `opacity: 0` in the markup, no
 *      `style` attribute at all. `lib/landing-cinematic.test.ts` asserts that
 *      about the landing page already; `lib/motion/reduced.test.ts` asserts
 *      it about this component. A reader with no JS gets the whole page.
 *   2. HIDING IS A CLIENT-SIDE EFFECT, AND ONLY OFF SCREEN. An element that
 *      has already crossed the reveal line when this mounts is never touched
 *      — taking away something the reader is already looking at, to fade it
 *      back in, is a flicker on the most important content on the page. The
 *      rule lives in `resolveRevealPlan`; `trigger="mount"` opts out of it
 *      deliberately, for a hero that should animate on load.
 *   3. UNDER REDUCED MOTION NOTHING IS EVER PRIMED, and an element primed by
 *      an earlier pass is restored. Flipping the OS setting mid-scroll must
 *      not strand a paragraph at `opacity: 0` forever.
 *   4. A THROWN OR MISSING `IntersectionObserver` REVEALS IMMEDIATELY rather
 *      than waiting for an event that will never arrive.
 *
 * MARKUP ONLY. Every duration, curve, distance and threshold comes from
 * `lib/motion/timing.ts` and `lib/motion/variants.ts`; every yes/no comes
 * from `lib/motion/reduced.ts`. This file decides nothing.
 */

import { animate } from "motion/react";
import { useCallback, useEffect, useRef, type ReactNode } from "react";

import {
  resolveRevealPlan,
  resolveVariant,
  type RevealTrigger,
} from "@/lib/motion/reduced";
import {
  intersectionRootMargin,
  isPastRevealLine,
  seconds,
} from "@/lib/motion/timing";
import {
  getVariant,
  type MotionTarget,
  type VariantName,
} from "@/lib/motion/variants";

import { useMotionMode } from "./MotionConfig";
import { useStaggerDelay } from "./Stagger";

/** `motion`'s imperative `animate` takes the properties and the timing as two
 * arguments; a variant carries them as one object. */
function split(target: MotionTarget) {
  const { transition, ...properties } = target;
  return { properties, transition };
}

export interface RevealProps {
  children?: ReactNode;
  className?: string;
  /** Which entry from the vocabulary. See `MOTION_VARIANTS`. */
  variant?: VariantName;
  /** `in-view` (default) waits for the reveal line. `mount` animates as soon
   * as the element exists — for a hero, and accepting that the settled
   * content paints first and is then taken away for the length of one
   * animation. */
  trigger?: RevealTrigger;
  /** ms. Overrides the delay inherited from an enclosing `Stagger`. */
  delayMs?: number;
  /** Overrides the measured preference. Landing call sites pass
   * `useLandingMotion().reduced` here. */
  reduced?: boolean;
  /** Render a `span` instead of a `div`, for revealing inside a paragraph. */
  inline?: boolean;
}

export function Reveal({
  children,
  className,
  variant = "reveal",
  trigger = "in-view",
  delayMs,
  reduced,
  inline = false,
}: RevealProps) {
  const mode = useMotionMode(reduced);
  const inheritedDelay = useStaggerDelay();
  const delay = delayMs ?? inheritedDelay;

  const elementRef = useRef<HTMLElement | null>(null);
  const setElement = useCallback((node: HTMLElement | null) => {
    elementRef.current = node;
  }, []);

  /** Whether a previous pass of the effect put this element into its hidden
   * state. The restore path in the effect depends on it: without it, a
   * preference change mid-scroll leaves the element hidden with nothing left
   * to reveal it. */
  const primed = useRef(false);

  useEffect(() => {
    const element = elementRef.current;
    if (!element) return;

    const spec = resolveVariant(getVariant(variant), mode);
    const observerAvailable =
      typeof window !== "undefined" && typeof IntersectionObserver !== "undefined";
    const inViewAtMount = isPastRevealLine(
      element.getBoundingClientRect().top,
      typeof window === "undefined" ? 0 : window.innerHeight
    );

    const plan = resolveRevealPlan({
      mode,
      trigger,
      inViewAtMount,
      observerAvailable,
    });
    element.dataset.revealReason = plan.reason;

    if (!plan.prime) {
      if (primed.current) {
        void animate(element, split(spec.visible).properties, { duration: 0 });
        primed.current = false;
      }
      element.dataset.revealed = "true";
      return;
    }

    primed.current = true;
    element.dataset.revealed = "pending";
    void animate(element, split(spec.hidden).properties, { duration: 0 });

    let cancelled = false;
    const reveal = () => {
      if (cancelled) return;
      const { properties, transition } = split(spec.visible);
      element.dataset.revealed = "true";
      void animate(element, properties, {
        duration: transition?.duration ?? 0,
        ease: transition?.ease,
        delay: seconds(delay),
      });
    };

    if (!plan.waitForInView) {
      reveal();
      return () => {
        cancelled = true;
      };
    }

    let observer: IntersectionObserver | null = null;
    try {
      observer = new IntersectionObserver(
        (entries) => {
          if (!entries.some((entry) => entry.isIntersecting)) return;
          observer?.disconnect();
          reveal();
        },
        { rootMargin: intersectionRootMargin(), threshold: 0 }
      );
      observer.observe(element);
    } catch {
      // Never leave an element hidden waiting on machinery that failed.
      reveal();
    }

    return () => {
      cancelled = true;
      observer?.disconnect();
    };
  }, [delay, mode, trigger, variant]);

  if (inline) {
    return (
      <span
        ref={setElement}
        className={className}
        data-motion="reveal"
        data-reveal-variant={variant}
      >
        {children}
      </span>
    );
  }

  return (
    <div
      ref={setElement}
      className={className}
      data-motion="reveal"
      data-reveal-variant={variant}
    >
      {children}
    </div>
  );
}
