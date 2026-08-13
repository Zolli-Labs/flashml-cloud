"use client";

import { useRef, type ReactNode } from "react";
import { gsap } from "gsap";
import { useGSAP } from "@gsap/react";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import {
  DURATIONS,
  TRAVEL,
  scrollTriggerStart,
  seconds,
} from "@/lib/motion/timing";
import { gsapEase, useLandingMotion } from "./LandingMotionProvider";

gsap.registerPlugin(useGSAP, ScrollTrigger);

export function SectionReveal({
  children,
  className,
  lineClassName,
  bottomLineClassName,
}: {
  children: ReactNode;
  className?: string;
  /**
   * No longer rendered. `SectionReveal` used to draw a hairline alongside
   * its content on a clip-path wipe; that read as more motion than the page
   * needed, so the reveal below is a plain fade-up now and this prop is a
   * no-op. Still accepted so call sites this pass did not touch — they keep
   * their own static hairline in markup instead — typecheck without an edit.
   */
  lineClassName?: string;
  /** Same shim as `lineClassName`, for the line below the content. */
  bottomLineClassName?: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const { reduced, desktop } = useLandingMotion();

  useGSAP(
    () => {
      const rootElement = root.current;
      if (!rootElement) return;

      const content = rootElement.querySelectorAll("[data-reveal-content]");

      if (reduced || !desktop) {
        gsap.set(content, { y: 0, opacity: 1 });
        return;
      }

      // One subtle fade-up, once: `TRAVEL.tight` (8px) over `DURATIONS.enter`
      // (320ms — under the 400ms ceiling) on the house `settle` curve, the
      // same reveal every other first-sight arrival on this page uses.
      gsap
        .timeline({
          scrollTrigger: {
            trigger: rootElement,
            start: scrollTriggerStart(),
            once: true,
          },
        })
        .from(content, {
          y: TRAVEL.tight,
          opacity: 0,
          duration: seconds(DURATIONS.enter),
          ease: gsapEase("settle"),
        });
    },
    { scope: root, dependencies: [reduced, desktop], revertOnUpdate: true },
  );

  return (
    <div ref={root} className={className} data-motion="section-reveal">
      <div data-reveal-content>{children}</div>
    </div>
  );
}
