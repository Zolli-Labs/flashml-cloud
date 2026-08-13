"use client";

/** A group whose children arrive one after another, 45ms apart.
 *
 * Spec §2 rule 2: "grouped elements stagger along the reading axis at a tight
 * 45ms cadence". This component owns the group; `Reveal` owns each child's
 * animation. They meet through context, which is the whole design:
 *
 *   - No `cloneElement`, so children keep their own props and a child that is
 *     not a `Reveal` (a heading, a divider, an `<hr>`) sits in the group
 *     without being wrapped in machinery it does not use.
 *   - Delays are computed from the child COUNT, which only the group knows.
 *     That is what lets the cadence compress for long lists — see
 *     `STAGGER_BUDGET_MS` in `lib/motion/timing.ts`. A thirty-row table at a
 *     fixed 45ms takes 1.3 seconds to finish arriving, and the last row
 *     animates long after the reader has reached it.
 *   - The delay a child receives is a number in context, not a variant. So a
 *     child can use it for something else entirely — a GSAP timeline, a CSS
 *     `transition-delay` — and still be on the same cadence.
 *
 * `staggerContainer()` in `lib/motion/variants.ts` is the same cadence for
 * call sites already inside a `motion/react` variant tree. Both routes read
 * the same table.
 *
 * MARKUP ONLY. Every number here comes from `lib/motion/*`.
 */

import {
  Children,
  createContext,
  isValidElement,
  useContext,
  useMemo,
  type ReactNode,
} from "react";

import { resolveStaggerDelays } from "@/lib/motion/reduced";
import { STAGGER_BUDGET_MS, STAGGER_CADENCE_MS } from "@/lib/motion/timing";

import { useMotionMode } from "./MotionConfig";

/** Milliseconds. `0` for a child with no `Stagger` above it, which is the
 * correct answer: no group, no cadence, no delay. */
const StaggerDelayContext = createContext(0);

export function useStaggerDelay(): number {
  return useContext(StaggerDelayContext);
}

export interface StaggerProps {
  children?: ReactNode;
  className?: string;
  /** No ref is taken and no motion is applied to the wrapper itself, so a
   * union of tags is safe here. `ul`/`ol` matter: a staggered list that is
   * not a list is a staggered list screen readers cannot count. */
  as?: "div" | "ul" | "ol" | "section" | "dl";
  /** ms between children. Defaults to the house cadence; raise it only with
   * a reason, and know that `STAGGER_BUDGET_MS` still caps the total. */
  cadenceMs?: number;
  /** ms before the first child. For letting a heading land first. */
  startMs?: number;
  /** Overrides the measured preference — the seam a landing call site uses to
   * pass `useLandingMotion().reduced` straight through. */
  reduced?: boolean;
}

export function Stagger({
  children,
  className,
  as: Tag = "div",
  cadenceMs = STAGGER_CADENCE_MS,
  startMs = 0,
  reduced,
}: StaggerProps) {
  const mode = useMotionMode(reduced);
  const items = useMemo(() => Children.toArray(children), [children]);

  const delays = useMemo(
    () =>
      resolveStaggerDelays(items.length, mode, {
        cadenceMs,
        budgetMs: STAGGER_BUDGET_MS,
        startMs,
      }),
    [items.length, mode, cadenceMs, startMs]
  );

  return (
    <Tag className={className} data-motion="stagger">
      {items.map((child, index) => (
        <StaggerDelayContext.Provider
          key={isValidElement(child) && child.key !== null ? child.key : index}
          value={delays[index] ?? 0}
        >
          {child}
        </StaggerDelayContext.Provider>
      ))}
    </Tag>
  );
}
