"use client";

import { useRef } from "react";
import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useLandingMotion } from "@/components/landing/motion/LandingMotionProvider";

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

      gsap
        .timeline({
          scrollTrigger: {
            trigger: rootElement,
            start: "top 86%",
            once: true,
          },
        })
        .from(line, {
          scaleX: 0,
          transformOrigin: "left center",
          duration: 0.55,
          ease: "power2.out",
        })
        .from(
          label,
          { y: 10, opacity: 0, duration: 0.4, ease: "power2.out" },
          "-=0.2",
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
