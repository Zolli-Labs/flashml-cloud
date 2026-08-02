"use client";

import { motion, useReducedMotion } from "motion/react";
import type { ReactNode } from "react";
import { BASE, VIEWPORT, riseChild, staggerParent } from "@/lib/motion";

/** Scroll-reveal for a single block. Justification: hierarchy. Content
 * arrives when the reader reaches it rather than all at once, so the eye is
 * led down the page instead of choosing an entry point. */
export function Reveal({
  children,
  delay = 0,
  className,
}: {
  children: ReactNode;
  delay?: number;
  className?: string;
}) {
  const reduce = useReducedMotion();
  if (reduce) return <div className={className}>{children}</div>;

  return (
    <motion.div
      className={className}
      initial={{ opacity: 0, y: 18 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={VIEWPORT}
      transition={{ ...BASE, delay }}
    >
      {children}
    </motion.div>
  );
}

/** Staggered group. Children must be `RevealItem`, and both halves have to
 * live in the same client tree for `staggerChildren` to reach them. */
export function RevealGroup({
  children,
  className,
  stagger,
}: {
  children: ReactNode;
  className?: string;
  stagger?: number;
}) {
  const reduce = useReducedMotion();
  if (reduce) return <div className={className}>{children}</div>;

  return (
    <motion.div
      className={className}
      variants={staggerParent(stagger)}
      initial="hidden"
      whileInView="show"
      viewport={VIEWPORT}
    >
      {children}
    </motion.div>
  );
}

export function RevealItem({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  const reduce = useReducedMotion();
  if (reduce) return <div className={className}>{children}</div>;

  return (
    <motion.div className={className} variants={riseChild}>
      {children}
    </motion.div>
  );
}
