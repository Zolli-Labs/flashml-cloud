"use client";

import { motion } from "motion/react";
import { useLandingMotion } from "@/components/landing/motion/LandingMotionProvider";
import { WORKLOADS } from "@/lib/landing/workloads";

export function WorkloadRows() {
  const { reduced, desktop, documentVisible } = useLandingMotion();
  const animated = desktop && !reduced && documentVisible;

  return (
    <ul
      aria-label="Supported workloads"
      className="mt-12 grid border-t border-[var(--z-border-strong)] lg:mt-16 lg:grid-cols-12"
    >
      {WORKLOADS.map(({ number, title, body, layout }, index) => (
        <motion.li
          key={number}
          data-workload-row={number}
          className={layout}
          initial={false}
          whileInView={animated
            ? { x: [0, index % 2 === 0 ? -7 : 7, 0], y: [0, -4, 0] }
            : { x: 0, y: 0 }}
          viewport={{ once: true, amount: 0.46, margin: "-12% 0px -12% 0px" }}
          transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
        >
          <article className="relative grid gap-5 py-8 sm:grid-cols-[76px_1fr] sm:gap-7">
            <span className="font-mono text-[11px] text-[var(--z-text-dim)]">{number}</span>
            <div>
              <h3 className="text-2xl font-semibold tracking-[-0.035em]">{title}</h3>
              <p className="mt-3 max-w-[48ch] text-[15px] leading-relaxed text-muted-foreground">{body}</p>
            </div>
            <span
              data-workload-rule
              data-animated={animated ? "true" : "false"}
              aria-hidden="true"
              className="absolute inset-x-0 bottom-0 h-px overflow-hidden bg-border"
            >
              <motion.i
                className="block h-full w-full origin-left bg-[var(--z-border-strong)]"
                initial={false}
                whileInView={animated ? { scaleX: [0, 1] } : { scaleX: 1 }}
                viewport={{ once: true, amount: 0.7, margin: "-10% 0px -10% 0px" }}
                transition={{ duration: 0.8, ease: [0.22, 1, 0.36, 1] }}
              />
            </span>
          </article>
        </motion.li>
      ))}
    </ul>
  );
}
