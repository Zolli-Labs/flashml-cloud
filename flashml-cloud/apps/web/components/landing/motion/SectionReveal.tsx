"use client";

import { useRef, type ReactNode } from "react";
import { gsap } from "gsap";
import { useGSAP } from "@gsap/react";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import {
  SECTION_REVEAL_DURATION_MS,
  SECTION_REVEAL_STAGGER_MS,
  SECTION_REVEAL_TRAVEL_PX,
  scrollTriggerStart,
  seconds,
} from "@/lib/motion/timing";
import { gsapEase, useLandingMotion } from "./LandingMotionProvider";

gsap.registerPlugin(useGSAP, ScrollTrigger);

export function SectionReveal({
  children,
  className,
  lineClassName,
  bottomLineClassName,
}: {
  children: ReactNode;
  className?: string;
  /**
   * No longer rendered. `SectionReveal` used to draw a hairline alongside
   * its content on a clip-path wipe; that read as more motion than the page
   * needed, so the reveal below is a plain fade-up now and this prop is a
   * no-op. Still accepted so call sites this pass did not touch — they keep
   * their own static hairline in markup instead — typecheck without an edit.
   */
  lineClassName?: string;
  /** Same shim as `lineClassName`, for the line below the content. */
  bottomLineClassName?: string;
}) {
  const root = useRef<HTMLDivElement>(null);
  const { reduced, desktop } = useLandingMotion();

  useGSAP(
    () => {
      const rootElement = root.current;
      if (!rootElement) return;

      // Callers mark each logical group inside — headline, copy, visual —
      // with `data-reveal-content`, in DOM order, and that order becomes the
      // stagger order. `SectionReveal` used to auto-wrap ALL of `children`
      // in one such div, which meant a caller could never get more than a
      // single block arriving at once; callers now place the attribute
      // themselves, on however many groups make sense for that section.
      //
      // A caller that marks nothing still gets ONE entrance rather than
      // none — `content` falls back to the root itself — so a section can
      // never silently ship unanimated just because nobody remembered the
      // attribute, the same "resolve toward content being on screen"
      // default `lib/motion/timing.ts`'s `isPastRevealLine` uses.
      const marked = rootElement.querySelectorAll("[data-reveal-content]");
      const content = marked.length > 0 ? marked : [rootElement];

      if (reduced || !desktop) {
        gsap.set(content, { y: 0, opacity: 1 });
        return;
      }

      // A whole section arriving, once: `SECTION_REVEAL_TRAVEL_PX` (30px)
      // over `SECTION_REVEAL_DURATION_MS` (700ms) on the house `settle`
      // curve, with each marked group offset by `SECTION_REVEAL_STAGGER_MS`
      // (70ms) so headline → copy → visual reads as an order, not a pop.
      gsap
        .timeline({
          scrollTrigger: {
            trigger: rootElement,
            start: scrollTriggerStart(),
            once: true,
          },
        })
        .from(content, {
          y: SECTION_REVEAL_TRAVEL_PX,
          opacity: 0,
          duration: seconds(SECTION_REVEAL_DURATION_MS),
          ease: gsapEase("settle"),
          stagger: seconds(SECTION_REVEAL_STAGGER_MS),
        });
    },
    { scope: root, dependencies: [reduced, desktop], revertOnUpdate: true },
  );

  return (
    <div ref={root} className={className} data-motion="section-reveal">
      {children}
    </div>
  );
}
