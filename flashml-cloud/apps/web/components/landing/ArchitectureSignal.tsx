"use client";

import { useRef } from "react";
import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { useLandingMotion } from "@/components/landing/motion/LandingMotionProvider";

gsap.registerPlugin(useGSAP, ScrollTrigger);

export const ARCHITECTURE_SIGNALS = [
  {
    id: "lease",
    label: "Bounded ownership",
    event: "LEASE_CLAIMED",
    path: "M18 28 C190 28 218 70 370 70 S650 30 942 30",
    tone: "orange",
  },
  {
    id: "checkpoint",
    label: "Durable progress",
    event: "CHECKPOINT_MANIFEST_COMMITTED",
    path: "M18 82 C230 82 280 116 476 116 S724 80 942 82",
    tone: "green",
  },
  {
    id: "recovery",
    label: "Recoverable execution",
    event: "TASK_REQUEUED",
    path: "M18 136 C212 136 354 48 564 48 S746 132 942 136",
    tone: "sand",
  },
] as const;

export function ArchitectureSignal() {
  const root = useRef<HTMLDivElement>(null);
  const { reduced, desktop, documentVisible } = useLandingMotion();
  const animated = desktop && !reduced && documentVisible;

  useGSAP(
    () => {
      const rootElement = root.current;
      if (!rootElement) return;

      const paths = gsap.utils.toArray<SVGPathElement>(
        rootElement.querySelectorAll("[data-signal-path]"),
      );

      if (!animated) {
        gsap.set(paths, { strokeDasharray: "none", strokeDashoffset: 0 });
        return;
      }

      paths.forEach((path) => {
        const length = path.getTotalLength();
        gsap.set(path, { strokeDasharray: length, strokeDashoffset: length });
      });

      const timeline = gsap.timeline({
        scrollTrigger: {
          trigger: rootElement,
          start: "top 82%",
          once: true,
        },
      });
      timeline.to(paths, {
        strokeDashoffset: 0,
        duration: 1,
        ease: "power2.out",
        stagger: 0.12,
      });

      return () => timeline.scrollTrigger?.kill();
    },
    { scope: root, dependencies: [animated], revertOnUpdate: true },
  );

  return (
    <div
      ref={root}
      data-motion="architecture-signal"
      data-animated={animated ? "true" : "false"}
      className="mt-12 overflow-hidden border-y border-white/14 md:mt-16"
    >
      <div className="flex items-center justify-between gap-5 py-4">
        <p className="font-mono text-[10px] font-medium uppercase tracking-[0.13em] text-white/50">
          Runtime signal
        </p>
        <p className="font-mono text-[9px] uppercase tracking-[0.1em] text-[var(--landing-green)]">
          Ownership · progress · recovery
        </p>
      </div>

      <svg
        aria-hidden="true"
        viewBox="0 0 960 164"
        preserveAspectRatio="none"
        className="h-28 w-full border-y border-white/10 md:h-36"
      >
        {ARCHITECTURE_SIGNALS.map((signal) => (
          <path
            key={signal.id}
            data-signal-path={signal.id}
            data-tone={signal.tone}
            d={signal.path}
            fill="none"
            vectorEffect="non-scaling-stroke"
            className="architecture-signal-path"
          />
        ))}
      </svg>

      <ol className="grid gap-px bg-white/10 sm:grid-cols-3">
        {ARCHITECTURE_SIGNALS.map((signal, index) => (
          <li key={signal.id} className="min-w-0 bg-[#0f1213] px-4 py-4 sm:px-5">
            <div className="flex items-center gap-3">
              <span className="font-mono text-[9px] text-white/38">0{index + 1}</span>
              <span className="text-[13px] font-semibold text-white/86">{signal.label}</span>
            </div>
            <p className="mt-2 break-words font-mono text-[8px] leading-relaxed text-white/42">
              {signal.event}
            </p>
          </li>
        ))}
      </ol>
    </div>
  );
}
