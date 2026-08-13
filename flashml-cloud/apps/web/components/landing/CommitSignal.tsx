"use client";

import { useRef } from "react";
import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import {
  gsapEase,
  useLandingMotion,
} from "@/components/landing/motion/LandingMotionProvider";
import {
  DURATIONS,
  TRAVEL,
  scrollTriggerStart,
  seconds,
} from "@/lib/motion/timing";

gsap.registerPlugin(useGSAP, ScrollTrigger);

export function CommitSignal({ event }: { event: "TASK_COMMIT_ACCEPTED" }) {
  const root = useRef<HTMLDivElement>(null);
  const { reduced, desktop } = useLandingMotion();

  useGSAP(
    () => {
      const rootElement = root.current;
      if (!rootElement) return;

      const line = rootElement.querySelector("[data-commit-line]");
      const label = rootElement.querySelector("[data-commit-event]");

      if (reduced || !desktop) {
        gsap.set(line, { scaleX: 1 });
        gsap.set(label, { y: 0, opacity: 1 });
        return;
      }

      // The rule is the trace (spec §2 rule 4), and it is the same shape as
      // the `trace` variant and `@keyframes workflow-scene-draw`: scale from
      // the leading edge over `draw`. The event name then lands exactly as
      // the trace completes — one `enter` back from the end.
      gsap
        .timeline({
          scrollTrigger: {
            trigger: rootElement,
            start: scrollTriggerStart(),
            once: true,
          },
        })
        .from(line, {
          scaleX: 0,
          transformOrigin: "left center",
          duration: seconds(DURATIONS.draw),
          ease: gsapEase("settle"),
        })
        .from(
          label,
          {
            y: TRAVEL.base,
            opacity: 0,
            duration: seconds(DURATIONS.enter),
            ease: gsapEase("settle"),
          },
          `-=${seconds(DURATIONS.enter)}`,
        );
    },
    { scope: root, dependencies: [reduced, desktop], revertOnUpdate: true },
  );

  return (
    <div ref={root} data-motion="commit-signal" className="mt-12 md:mt-16">
      <div
        data-commit-line
        aria-hidden
        className="h-px w-full bg-[var(--landing-graphite)]"
      />
      <p
        data-commit-event
        className="mt-4 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-foreground"
      >
        {event}
      </p>
    </div>
  );
}
