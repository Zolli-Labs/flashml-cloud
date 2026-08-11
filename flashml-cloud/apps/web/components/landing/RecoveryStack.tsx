"use client";

import { useRef } from "react";
import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useLandingMotion } from "@/components/landing/motion/LandingMotionProvider";

gsap.registerPlugin(useGSAP, ScrollTrigger);

export function RecoveryStack({ items }: { items: readonly string[] }) {
  const root = useRef<HTMLOListElement>(null);
  const { reduced, desktop } = useLandingMotion();

  useGSAP(
    () => {
      const rootElement = root.current;
      if (!rootElement) return;

      const rows = rootElement.querySelectorAll("[data-recovery-row]");

      if (reduced || !desktop) {
        gsap.set(rows, { y: 0, opacity: 1 });
        return;
      }

      gsap.timeline({
        scrollTrigger: {
          trigger: rootElement,
          start: "top 84%",
          once: true,
        },
      }).from(rows, {
        y: (index) => 28 * (index + 1),
        opacity: 0,
        duration: 0.6,
        stagger: 0.08,
        ease: "power2.out",
      });
    },
    { scope: root, dependencies: [reduced, desktop], revertOnUpdate: true },
  );

  return (
    <ol
      ref={root}
      data-motion="recovery-stack"
      className="mt-8 grid gap-3 border-y border-[var(--z-border-strong)] py-5 sm:grid-cols-3 sm:gap-0"
    >
      {items.map((item, index) => (
        <li
          key={item}
          data-recovery-row={index + 1}
          className="flex items-center gap-3 sm:px-4 sm:first:pl-0 sm:not-first:border-l sm:not-first:border-border"
        >
          <span className="font-mono text-[10px] text-warning-foreground">
            0{index + 1}
          </span>
          <span className="text-sm font-medium leading-snug">{item}</span>
        </li>
      ))}
    </ol>
  );
}
