"use client";

import { useRef } from "react";
import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useLandingMotion } from "@/components/landing/motion/LandingMotionProvider";

gsap.registerPlugin(useGSAP, ScrollTrigger);

export function WorkloadVelocityRail({ labels }: { labels: readonly string[] }) {
  const root = useRef<HTMLDivElement>(null);
  const strip = useRef<HTMLDivElement>(null);
  const { reduced, desktop, documentVisible } = useLandingMotion();
  const animated = desktop && !reduced && documentVisible;

  useGSAP(
    () => {
      const rootElement = root.current;
      const stripElement = strip.current;
      if (!rootElement || !stripElement) return;

      if (!animated) {
        gsap.set(stripElement, { xPercent: 0 });
        return;
      }

      gsap.fromTo(
        stripElement,
        { xPercent: 0 },
        {
          xPercent: -28,
          ease: "none",
          scrollTrigger: {
            trigger: rootElement,
            start: "top bottom",
            end: "bottom top",
            scrub: 0.6,
            invalidateOnRefresh: true,
          },
        },
      );
    },
    { scope: root, dependencies: [animated], revertOnUpdate: true },
  );

  return (
    <div
      ref={root}
      data-motion="velocity-rail"
      data-animated={animated ? "true" : "false"}
      aria-hidden="true"
      className="overflow-hidden border-y border-[var(--z-border-strong)] py-4"
    >
      <div
        ref={strip}
        className={animated
          ? "flex w-max items-center gap-5 whitespace-nowrap"
          : "flex flex-wrap items-center gap-x-5 gap-y-2"}
      >
        {[...labels, ...labels].map((label, index) => (
          <span key={`${label}-${index}`} className="inline-flex items-center gap-5 font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
            {label}
            <i aria-hidden className="h-1 w-1 rounded-full bg-[var(--landing-orange)]" />
          </span>
        ))}
      </div>
    </div>
  );
}
