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
  lineClassName?: string;
  bottomLineClassName?: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const { reduced, desktop } = useLandingMotion();

  useGSAP(
    () => {
      const rootElement = root.current;
      if (!rootElement) return;

      const line = rootElement.querySelectorAll("[data-reveal-line]");
      const content = rootElement.querySelectorAll("[data-reveal-content]");

      if (reduced || !desktop) {
        gsap.set(line, { clipPath: "inset(0 0% 0 0)" });
        gsap.set(content, { y: 0, opacity: 1 });
        return;
      }

      // WRITTEN IN THE ORDER IT PLAYS. The previous form put the rule first
      // and the content at `"<-0.2"`, but GSAP does not clamp a negative
      // relative position — it rebases the timeline on the earliest child.
      // Measured, that made the CONTENT the thing that started at zero and
      // the rule trail it by 200ms. Same picture, so it is preserved; the
      // source now says so, and the 200ms is the table's `state` beat.
      gsap
        .timeline({
          scrollTrigger: {
            trigger: rootElement,
            start: scrollTriggerStart(),
            once: true,
          },
        })
        .from(content, {
          // Was `yPercent: 10` — a percentage of the section's own height,
          // which on a tall block is the 40px slide-up spec §2 rule 2 calls
          // a template. A fixed 12px travels the same distance everywhere.
          y: TRAVEL.base,
          opacity: 0,
          duration: seconds(DURATIONS.reveal),
          ease: gsapEase("settle"),
        })
        .from(
          line,
          {
            clipPath: "inset(0 100% 0 0)",
            duration: seconds(DURATIONS.reveal),
            ease: gsapEase("settle"),
          },
          seconds(DURATIONS.state),
        );
    },
    { scope: root, dependencies: [reduced, desktop], revertOnUpdate: true },
  );

  return (
    <div ref={root} className={className} data-motion="section-reveal">
      <div data-reveal-line aria-hidden className={lineClassName} />
      <div data-reveal-content>{children}</div>
      {bottomLineClassName ? (
        <div data-reveal-line aria-hidden className={bottomLineClassName} />
      ) : null}
    </div>
  );
}
