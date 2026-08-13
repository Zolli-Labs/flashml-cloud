"use client";

/** A measured value resolving to its figure.
 *
 * Spec §2 rule 3 — "numbers resolve" — sits directly on top of spec §1.1 —
 * "never render a number the API did not return", and `null` means NOT
 * OBSERVED, never `0`. A count-up is the single easiest place in a UI to
 * break that rule, because zero is such a natural place for a counter to
 * start: animate a `null` and the page shows a confident, wrong number,
 * rising, which is more persuasive than a wrong number sitting still.
 *
 * SO THERE IS NO PATH FROM `null` TO A DIGIT. `resolveCountUp` in
 * `lib/motion/reduced.ts` makes that decision and `lib/motion/reduced.test.ts`
 * holds it, including for the `NaN` and `Infinity` a broken read turns into
 * after some arithmetic. This file renders whatever that function returns.
 *
 * THREE MORE THINGS THIS COMPONENT OWES THE READER:
 *
 *   - THE SERVER RENDERS THE FINAL FIGURE. Not zero, not blank. With no JS,
 *     with a failed hydration, with a thrown observer, the real number is on
 *     the page. The count is an enhancement on top of a correct render.
 *   - TABULAR NUMERALS, so the digits do not reflow while counting. Both
 *     `data-numeric` (which `app/globals.css` already styles) and the
 *     explicit utility, because a metric whose width jitters every frame
 *     reads as instability in the system being measured — `globals.css` says
 *     exactly that about polled values, and a count-up is the same problem at
 *     60fps.
 *   - SCREEN READERS GET THE FINAL VALUE ONLY. The animating digits are
 *     `aria-hidden`; a parallel `sr-only` span states the figure the API
 *     returned. An intermediate frame is not a measurement and must never be
 *     announced as one.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  NOT_OBSERVED_LABEL,
  countUpValueAt,
  decimalsFor,
  formatCountValue,
  resolveCountUp,
  type RevealTrigger,
} from "@/lib/motion/reduced";
import {
  COUNT_UP_MS,
  intersectionRootMargin,
  isPastRevealLine,
} from "@/lib/motion/timing";
import { cn } from "@/lib/utils";

import { useMotionMode } from "./MotionConfig";

export interface CountUpProps {
  /** The API's value. `null` renders `notObservedLabel` and does not animate. */
  value: number | null;
  /** What to say when the value was not observed. Override it when the
   * surrounding sentence needs different words — "no runs yet" is a different
   * claim from "not observed" and only the caller knows which is true. */
  notObservedLabel?: string;
  /** Where the count starts. An animation origin, never a reading. */
  from?: number;
  /** ms */
  durationMs?: number;
  /** Override the precision taken from the value itself. */
  decimals?: number;
  /** Thousands separators. */
  grouping?: boolean;
  prefix?: string;
  suffix?: string;
  className?: string;
  /** Overrides the measured preference (`useLandingMotion().reduced` on the
   * landing page). */
  reduced?: boolean;
  trigger?: RevealTrigger;
}

export function CountUp({
  value,
  notObservedLabel = NOT_OBSERVED_LABEL,
  from = 0,
  durationMs = COUNT_UP_MS,
  decimals,
  grouping = true,
  prefix = "",
  suffix = "",
  className,
  reduced,
  trigger = "in-view",
}: CountUpProps) {
  const mode = useMotionMode(reduced);

  const elementRef = useRef<HTMLElement | null>(null);
  const setElement = useCallback((node: HTMLElement | null) => {
    elementRef.current = node;
  }, []);

  /** Seeded with the real value so the server, the first client render and
   * every failure path all show the figure that was measured. */
  const [displayed, setDisplayed] = useState<number | null>(value);

  useEffect(() => {
    const element = elementRef.current;
    const observerAvailable =
      typeof window !== "undefined" &&
      typeof IntersectionObserver !== "undefined";
    const inViewAtMount =
      element === null ||
      isPastRevealLine(
        element.getBoundingClientRect().top,
        typeof window === "undefined" ? 0 : window.innerHeight
      );

    const plan = resolveCountUp({
      value,
      mode,
      from,
      durationMs,
      decimals,
      trigger,
      inViewAtMount,
      observerAvailable,
    });

    if (plan.kind !== "count") {
      setDisplayed(value);
      return;
    }

    let cancelled = false;
    let frame = 0;
    let startedAt = -1;

    const step = (now: number) => {
      if (cancelled) return;
      if (startedAt < 0) startedAt = now;
      const elapsed = now - startedAt;
      setDisplayed(countUpValueAt(plan, elapsed));
      if (elapsed < plan.durationMs) frame = requestAnimationFrame(step);
    };

    const begin = () => {
      if (cancelled) return;
      setDisplayed(plan.from);
      frame = requestAnimationFrame(step);
    };

    let observer: IntersectionObserver | null = null;

    if (inViewAtMount || !observerAvailable) {
      begin();
    } else {
      try {
        observer = new IntersectionObserver(
          (entries) => {
            if (!entries.some((entry) => entry.isIntersecting)) return;
            observer?.disconnect();
            begin();
          },
          { rootMargin: intersectionRootMargin(), threshold: 0 }
        );
        observer.observe(element as Element);
      } catch {
        begin();
      }
    }

    return () => {
      cancelled = true;
      if (frame) cancelAnimationFrame(frame);
      observer?.disconnect();
    };
  }, [decimals, durationMs, from, mode, trigger, value]);

  const observed = typeof value === "number" && Number.isFinite(value);

  if (!observed) {
    return (
      <span
        className={cn("text-muted-foreground", className)}
        data-motion="count-up"
        data-not-observed="true"
      >
        {notObservedLabel}
      </span>
    );
  }

  const precision = decimals ?? decimalsFor(value);
  const shown =
    typeof displayed === "number" && Number.isFinite(displayed)
      ? displayed
      : value;
  const frameText = formatCountValue(shown, precision, { grouping });
  const finalText = formatCountValue(value, precision, { grouping });

  return (
    <span
      ref={setElement}
      className={cn("tabular-nums", className)}
      data-numeric
      data-motion="count-up"
    >
      <span aria-hidden="true">
        {prefix}
        {frameText}
        {suffix}
      </span>
      <span className="sr-only">
        {prefix}
        {finalText}
        {suffix}
      </span>
    </span>
  );
}
