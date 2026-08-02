"use client";

import { motion, useReducedMotion } from "motion/react";

// Storytelling motion: the ring performs the sentence next to it. A lease is
// a countdown that a live worker keeps pushing back, so the ring drains and
// snaps back while the machine is healthy, drains to nothing when it goes
// quiet, and starts clean on the machine that picks the work up.
//
// This is the one animation on the page that would be worth keeping if every
// other one were cut, because it is the only one carrying an idea the copy
// cannot show on its own.

export type LeaseState = "renewing" | "expiring" | "fresh";

const R = 15;
const C = 2 * Math.PI * R;

const STROKE: Record<LeaseState, string> = {
  renewing: "var(--primary)",
  expiring: "var(--warning)",
  fresh: "var(--node-green)",
};

const CAPTION: Record<LeaseState, string> = {
  renewing: "lease renewing",
  expiring: "lease expired",
  fresh: "lease claimed",
};

export function LeaseRing({ state }: { state: LeaseState }) {
  const reduce = useReducedMotion();

  // Drain to ~65% and snap back: the heartbeat arriving before the deadline.
  const renewing = {
    strokeDashoffset: [0, C * 0.65, 0],
    transition: { duration: 2.6, times: [0, 0.8, 0.82], repeat: Infinity },
  };
  // Drain all the way and stay empty. No repeat: it does not come back.
  const expiring = {
    strokeDashoffset: [0, C],
    transition: { duration: 1.8, ease: "linear" as const },
  };
  const fresh = {
    strokeDashoffset: [C, 0],
    transition: { duration: 1, ease: [0.16, 1, 0.3, 1] as const },
  };

  const anim =
    state === "renewing" ? renewing : state === "expiring" ? expiring : fresh;

  return (
    <div className="flex items-center gap-3">
      <svg width="38" height="38" viewBox="0 0 38 38" aria-hidden>
        <circle
          cx="19"
          cy="19"
          r={R}
          fill="none"
          stroke="oklch(1 0 0 / 0.09)"
          strokeWidth="2"
        />
        <motion.circle
          cx="19"
          cy="19"
          r={R}
          fill="none"
          stroke={STROKE[state]}
          strokeWidth="2"
          strokeLinecap="round"
          strokeDasharray={C}
          transform="rotate(-90 19 19)"
          initial={false}
          // Reduced motion gets the end state, not a slower countdown.
          animate={
            reduce
              ? { strokeDashoffset: state === "expiring" ? C : 0 }
              : anim
          }
        />
      </svg>
      <span className="font-mono text-[11px] text-muted-foreground">
        {CAPTION[state]}
      </span>
    </div>
  );
}
