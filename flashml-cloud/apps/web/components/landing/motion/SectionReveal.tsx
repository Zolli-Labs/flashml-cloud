"use client";

import { useRef, type ReactNode } from "react";
import { gsap } from "gsap";
import { useGSAP } from "@gsap/react";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useLandingMotion } from "./LandingMotionProvider";

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
        gsap.set(content, { yPercent: 0, opacity: 1 });
        return;
      }

      gsap
        .timeline({
          scrollTrigger: {
            trigger: root.current,
            start: "top 82%",
            once: true,
          },
        })
        .from(line, {
          clipPath: "inset(0 100% 0 0)",
          duration: 0.6,
          ease: "power2.out",
        })
        .from(
          content,
          {
            yPercent: 10,
            opacity: 0,
            duration: 0.55,
            ease: "power2.out",
          },
          "<-0.2",
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
