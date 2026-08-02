"use client";

import { useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { LedgerRow } from "@/components/landing/EventLedger";
import { LeaseRing, type LeaseState } from "@/components/landing/LeaseRing";
import { Reveal } from "@/components/motion/Reveal";
import { SAMPLE_LEDGER, RECOVERY_BEATS } from "@/lib/landing/sample-ledger";

/** One per beat, in order. The ring performs what the beat says. */
const RING_STATE: LeaseState[] = ["renewing", "expiring", "fresh"];

// The page's argument, and the only place motion is doing real work: the
// ledger on the right changes because the reader moved through the story on
// the left. Scroll position is read with Motion's viewport callbacks
// (IntersectionObserver underneath), never a scroll listener.

const BEATS = [
  {
    title: "Work is claimed, not pushed",
    body: "A machine asks for a task and gets a lease it has to keep renewing. Nothing is ever handed to a machine that might already be gone.",
  },
  {
    title: "The machine goes quiet",
    body: "No shutdown signal, no cleanup call. The heartbeat stops, the lease passes its deadline, and the sweep expires it. Nobody had to notice.",
  },
  {
    title: "Another machine picks it up",
    body: "The task requeues on attempt two and resumes from the last checkpoint whose parts all verified. Only the work after that checkpoint is lost, and we can tell you exactly how much.",
  },
];

export function RecoveryDemo() {
  const [active, setActive] = useState(0);
  const reduce = useReducedMotion();

  const beat = RECOVERY_BEATS[active];
  const rows = SAMPLE_LEDGER.slice(beat.from, beat.to);

  return (
    <section
      id="recover"
      className="border-y border-border bg-white/[0.015] py-20 md:py-28"
    >
      <div className="mx-auto max-w-7xl px-4 sm:px-6">
        <Reveal>
          <p className="font-mono text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
            Recovery
          </p>
          <h2 className="mt-4 max-w-3xl text-3xl font-semibold tracking-[-0.025em] md:text-5xl">
            A machine leaves.{" "}
            <span className="text-accent-text">The run does not.</span>
          </h2>
        </Reveal>

        <div className="mt-16 grid gap-12 lg:grid-cols-12 lg:gap-16">
          <div className="lg:col-span-6">
            {BEATS.map((b, i) => (
              <motion.div
                key={b.title}
                onViewportEnter={() => setActive(i)}
                viewport={{ margin: "-45% 0px -45% 0px" }}
                className="border-l-2 py-9 pl-6 transition-colors duration-300 lg:py-12"
                style={{
                  borderColor:
                    active === i
                      ? "var(--primary)"
                      : "oklch(1 0 0 / 0.09)",
                }}
              >
                {/* Inactive beats dim, but not out of legibility. At 0.35 on
                    muted-foreground the body text fell under AA against this
                    background, which turns a focus effect into a contrast
                    failure for anyone reading ahead. */}
                <h3
                  className="text-xl font-semibold transition-opacity duration-300 md:text-2xl"
                  style={{ opacity: active === i ? 1 : 0.62 }}
                >
                  {b.title}
                </h3>
                <p
                  className="mt-3 max-w-[52ch] text-sm leading-relaxed text-muted-foreground transition-opacity duration-300 md:text-base"
                  style={{ opacity: active === i ? 1 : 0.6 }}
                >
                  {b.body}
                </p>
              </motion.div>
            ))}
          </div>

          <div className="lg:col-span-6">
            <div className="lg:sticky lg:top-28">
              <div className="overflow-hidden rounded-lg border border-border bg-surface">
                <div className="flex items-center justify-between border-b border-border px-4 py-3">
                  {/* Remounts with the beat so the countdown restarts. */}
                  <LeaseRing key={active} state={RING_STATE[active]} />
                  <span className="font-mono text-[10px] text-white/30">
                    sample run
                  </span>
                </div>
                <motion.ul
                  key={active}
                  initial={reduce ? false : { opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ duration: 0.25 }}
                  className="min-h-[132px] px-4 py-3"
                >
                  {rows.map((e, i) => (
                    <LedgerRow key={`${e.type}-${i}`} event={e} />
                  ))}
                </motion.ul>
              </div>

              <p className="mt-4 max-w-[46ch] text-xs leading-relaxed text-muted-foreground">
                Every type above is a real member of the runtime&apos;s event
                ledger. The values are sample data until a captured run
                replaces them.
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
