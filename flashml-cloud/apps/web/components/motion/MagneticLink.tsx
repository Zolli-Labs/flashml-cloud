"use client";

import Link from "next/link";
import { motion, useMotionValue, useSpring, useReducedMotion } from "motion/react";
import type { PointerEvent, ReactNode } from "react";
import { FOLLOW } from "@/lib/motion";

const MotionLink = motion.create(Link);

/** Primary CTA with a magnetic pull toward the cursor. Justification:
 * feedback. The control acknowledges the pointer before it is clicked, which
 * is the cheapest way to make a button feel like an object rather than a
 * rectangle.
 *
 * Position lives in motion values, never React state. `useState` here would
 * re-render the tree on every pointermove and collapse on a trackpad. */
export function MagneticLink({
  href,
  children,
  className,
  strength = 0.28,
}: {
  href: string;
  children: ReactNode;
  className?: string;
  strength?: number;
}) {
  const reduce = useReducedMotion();
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const sx = useSpring(x, FOLLOW);
  const sy = useSpring(y, FOLLOW);

  function onMove(e: PointerEvent<HTMLAnchorElement>) {
    // Coarse pointers get nothing: there is no hover on a touchscreen, and
    // the tap would land offset from where the finger went down.
    if (reduce || e.pointerType !== "mouse") return;
    const r = e.currentTarget.getBoundingClientRect();
    x.set((e.clientX - (r.left + r.width / 2)) * strength);
    y.set((e.clientY - (r.top + r.height / 2)) * strength);
  }

  function reset() {
    x.set(0);
    y.set(0);
  }

  return (
    <MotionLink
      href={href}
      className={className}
      style={reduce ? undefined : { x: sx, y: sy }}
      onPointerMove={onMove}
      onPointerLeave={reset}
      onBlur={reset}
      whileTap={reduce ? undefined : { scale: 0.97 }}
    >
      {children}
    </MotionLink>
  );
}
