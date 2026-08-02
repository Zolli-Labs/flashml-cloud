// One timing system for the whole landing page.
//
// The single biggest difference between expensive-feeling motion and
// generated-feeling motion is not the effects, it is that every transition
// on the page shares an easing curve and a duration family. When one section
// eases at 0.3s linear and the next springs at stiffness 400, the page reads
// as assembled from parts.
//
// Rules encoded here:
//   - Text and layout use EXPO_OUT. It leaves fast and settles slowly, which
//     reads as confident. Never a spring: bounce on typography reads playful,
//     not premium.
//   - Springs are only for things that follow a pointer, where the physics is
//     the point.
//   - Stagger is small (60-70ms). Large stagger looks like a slideshow.

import type { Transition, Variants } from "motion/react";

export const EXPO_OUT = [0.16, 1, 0.3, 1] as const;

/** Headline and hero-scale moments. */
export const SLOW: Transition = { duration: 0.85, ease: EXPO_OUT };
/** Section content, cards, list items. */
export const BASE: Transition = { duration: 0.6, ease: EXPO_OUT };
/** Hover and state feedback, fast enough to feel connected to the input. */
export const QUICK: Transition = { duration: 0.28, ease: EXPO_OUT };

/** Pointer-following only. Overdamped so it never overshoots. */
export const FOLLOW: Transition = {
  type: "spring",
  stiffness: 150,
  damping: 20,
  mass: 0.6,
};

export const VIEWPORT = { once: true, amount: 0.35 } as const;

/** Parent for any staggered group. Children opt in with `riseChild`. */
export const staggerParent = (stagger = 0.065): Variants => ({
  hidden: {},
  show: { transition: { staggerChildren: stagger } },
});

export const riseChild: Variants = {
  hidden: { opacity: 0, y: 18 },
  show: { opacity: 1, y: 0, transition: BASE },
};

/** A line of display type wiping up behind its own overflow box. The
 * parent must be `overflow-hidden`, which is why this ships as a pair. */
export const wipeLine: Variants = {
  hidden: { y: "110%" },
  show: { y: "0%", transition: SLOW },
};
