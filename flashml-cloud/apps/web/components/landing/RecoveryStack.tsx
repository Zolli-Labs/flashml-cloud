"use client";

import { useRef } from "react";
import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import {
  gsapEase,
  useLandingMotion,
} from "@/components/landing/motion/LandingMotionProvider";
import {
  DURATIONS,
  TRAVEL,
  scrollTriggerStart,
  seconds,
  staggerDelays,
} from "@/lib/motion/timing";

gsap.registerPlugin(useGSAP, ScrollTrigger);

/** THE ONE PLACE THIS SITE IS ALLOWED TO OVERSHOOT (spec §2 rule 5).
 *
 * The rows under "Lost machine. Verified recovery." are a proof in three
 * parts: what broke, what survived, what it cost. Only the middle one is the
 * product's claim — the checkpoint is the thing a dead machine did not take
 * with it — so only the middle one gets `EASINGS.recovery`, and it is the
 * only curve in `lib/motion/timing.ts` that passes above its resting value
 * before settling. `lib/motion/variants.test.ts` holds the same reservation
 * on the variant table; this is the component half of it.
 *
 * A prop rather than a constant because the row order is the caller's, not
 * ours. The default is the middle claim of a proof, which is what every
 * caller has today. */
function defaultBeatIndex(count: number): number {
  return count > 1 ? 1 : 0;
}

export function RecoveryStack({
  items,
  beatIndex,
  active = true,
}: {
  items: readonly string[];
  /** Which row is the survivor. Defaults to the second — see above. */
  beatIndex?: number;
  /** `false` when `#recover`'s pinned scroll-scrub is driving these same
   * `[data-recovery-row]` elements itself — see the identical prop on
   * `SectionReveal`. Skips this component's own reveal outright so the two
   * timelines never fight over the same rows' opacity/transform. */
  active?: boolean;
}) {
  const root = useRef<HTMLOListElement>(null);
  const { reduced, desktop } = useLandingMotion();
  const beat = Math.min(
    Math.max(0, beatIndex ?? defaultBeatIndex(items.length)),
    Math.max(0, items.length - 1),
  );

  useGSAP(
    () => {
      const rootElement = root.current;
      if (!rootElement || !active) return;

      const rows = gsap.utils.toArray<HTMLElement>(
        rootElement.querySelectorAll("[data-recovery-row]"),
      );

      if (reduced || !desktop) {
        gsap.set(rows, { y: 0, opacity: 1 });
        return;
      }

      // The cadence every grouped reveal on this page runs at (45ms), plus
      // ONE HELD BEAT once the loss has been stated. That pause is the whole
      // point: the machine is gone, and for a quarter of a second nothing
      // replaces it. What arrives after it is the checkpoint, and it arrives
      // on the overshoot curve — landing, settling, intact — while the row
      // that follows only reports the cost and stays quiet.
      const cadence = staggerDelays(rows.length);

      const timeline = gsap.timeline({
        scrollTrigger: {
          trigger: rootElement,
          start: scrollTriggerStart(),
          once: true,
        },
      });

      rows.forEach((row, index) => {
        const isBeat = index === beat;
        const startMs =
          cadence[index] + (index >= beat ? DURATIONS.state : 0);

        timeline.from(
          row,
          {
            // 16px, the ceiling spec §2 rule 2 allows, on the one row that
            // has to register. Everything else travels the default 12.
            y: isBeat ? TRAVEL.loose : TRAVEL.base,
            opacity: 0,
            duration: seconds(DURATIONS.reveal),
            ease: gsapEase(isBeat ? "recovery" : "settle"),
          },
          seconds(startMs),
        );
      });
    },
    { scope: root, dependencies: [reduced, desktop, beat, active], revertOnUpdate: true },
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
          data-recovery-beat={index === beat ? "true" : undefined}
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
