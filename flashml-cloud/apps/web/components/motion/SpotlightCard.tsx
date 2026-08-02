"use client";

import {
  motion,
  useMotionTemplate,
  useMotionValue,
  useReducedMotion,
} from "motion/react";
import type { PointerEvent, ReactNode } from "react";

/** A card whose border and surface light where the cursor is. Justification:
 * feedback. It replaces the usual lift-and-shadow hover, which on a dark
 * page mostly reads as the card detaching from the layout.
 *
 * Two layers, both driven by the same pointer motion values: a wide soft
 * wash on the surface, and a tight bright ring masked to the 1px border.
 * The border one is what sells it; a wash on its own just looks like a
 * flashlight. */
export function SpotlightCard({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  const reduce = useReducedMotion();
  const mx = useMotionValue(-500);
  const my = useMotionValue(-500);
  const hover = useMotionValue(0);

  const wash = useMotionTemplate`radial-gradient(340px circle at ${mx}px ${my}px, oklch(0.55 0.21 285 / 0.10), transparent 68%)`;
  const ring = useMotionTemplate`radial-gradient(220px circle at ${mx}px ${my}px, oklch(0.70 0.19 285 / 0.55), transparent 65%)`;

  function onMove(e: PointerEvent<HTMLDivElement>) {
    if (reduce || e.pointerType !== "mouse") return;
    const r = e.currentTarget.getBoundingClientRect();
    mx.set(e.clientX - r.left);
    my.set(e.clientY - r.top);
    hover.set(1);
  }

  return (
    <div
      onPointerMove={onMove}
      onPointerLeave={() => hover.set(0)}
      className={`group relative isolate overflow-hidden rounded-lg border border-border bg-surface ${className}`}
    >
      {!reduce && (
        <>
          {/* The lit border. `mask-composite: exclude` punches the interior
              out of the gradient, leaving only a 1px rim. */}
          <motion.span
            aria-hidden
            className="pointer-events-none absolute inset-0 -z-10 rounded-lg opacity-0 transition-opacity duration-300 group-hover:opacity-100"
            style={{
              background: ring,
              padding: 1,
              WebkitMask:
                "linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0)",
              WebkitMaskComposite: "xor",
              maskComposite: "exclude",
            }}
          />
          <motion.span
            aria-hidden
            className="pointer-events-none absolute inset-0 -z-10 opacity-0 transition-opacity duration-300 group-hover:opacity-100"
            style={{ background: wash }}
          />
        </>
      )}
      {children}
    </div>
  );
}
