"use client";

import { motion } from "motion/react";
import { useLandingMotion } from "@/components/landing/motion/LandingMotionProvider";
import { WORKLOADS, type WorkloadMode } from "@/lib/landing/workloads";

const MODES: readonly { mode: WorkloadMode; label: string; hint: string }[] = [
  {
    mode: "divide",
    label: "Divide",
    hint: "Work that splits into independent pieces.",
  },
  {
    mode: "resume",
    label: "Resume",
    hint: "Work that saves its progress while running.",
  },
] as const;

export function WorkloadRows() {
  const { reduced, desktop, documentVisible } = useLandingMotion();
  const animated = desktop && !reduced && documentVisible;

  return (
    <div className="mt-12 grid gap-12 lg:mt-16 lg:grid-cols-2 lg:gap-16">
      {MODES.map(({ mode, label, hint }) => (
        <div key={mode} data-mode={mode} className="min-w-0">
          <div className="flex items-baseline justify-between gap-5 border-t border-[var(--z-border-strong)] pt-5">
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
              Mode / {label}
            </p>
            <p className="text-sm leading-relaxed text-muted-foreground">{hint}</p>
          </div>
          <ul aria-label={`Supported workloads — ${label} mode`} className="mt-2">
            {WORKLOADS.filter((workload) => workload.mode === mode).map(
              ({ number, title, body, machineContext }) => (
                <motion.li
                  key={number}
                  data-workload-row={number}
                  initial={false}
                  whileInView={animated ? { y: [0, -4, 0] } : { y: 0 }}
                  viewport={{ once: true, amount: 0.46, margin: "-12% 0px -12% 0px" }}
                  transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
                >
                  <article className="grid grid-cols-[3rem_1fr] gap-5 border-b border-border py-7">
                    <span className="pt-1 font-mono text-[11px] text-[var(--z-text-dim)]">{number}</span>
                    <div className="min-w-0">
                      <h3 className="text-xl font-semibold tracking-[-0.03em] sm:text-2xl">{title}</h3>
                      <p className="mt-2 max-w-[48ch] text-[15px] leading-relaxed text-muted-foreground">{body}</p>
                      <p data-workload-machines className="mt-2 text-sm leading-relaxed text-muted-foreground">
                        Suitable machines: {machineContext}
                      </p>
                    </div>
                  </article>
                </motion.li>
              ),
            )}
          </ul>
        </div>
      ))}
    </div>
  );
}
