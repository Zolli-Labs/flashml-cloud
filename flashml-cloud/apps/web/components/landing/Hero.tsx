"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ArrowRight } from "@phosphor-icons/react/dist/ssr";
import { gsap } from "gsap";
import { useGSAP } from "@gsap/react";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { CoordinatorMap } from "@/components/landing/coordinator-map/CoordinatorMap";
import { HeroMarketSwitch } from "@/components/landing/HeroMarketSwitch";
import { useMapStory } from "@/components/landing/coordinator-map/useMapStory";
import {
  gsapEase,
  useLandingMotion,
} from "@/components/landing/motion/LandingMotionProvider";
import {
  DURATIONS,
  SECTION_REVEAL_STAGGER_MS,
  TRAVEL,
  seconds,
} from "@/lib/motion/timing";
import { MAP_VIEWPORT_COMPACT, MAP_VIEWPORT_DESKTOP, type Viewport } from "@/lib/coordinator-map";
import { MARKETING } from "@/lib/marketing";

gsap.registerPlugin(useGSAP, ScrollTrigger);

/**
 * The window on `MAP_VIEWPORT_DESKTOP` this hero draws through.
 *
 * The desktop composition occupies x 417–1227 and y 43–568 of its 1240 × 620
 * frame: the prototype parked the hero copy in the empty left third and drew
 * the map across the rest. This hero keeps the copy in its own column, so that
 * third would arrive as 450 px of dead panel beside the headline. Cropping to
 * 900 × 570 starting 372 units in and 20 down leaves the map untouched — same
 * scale, same composition — with 45 units of air to its left and right and
 * 22 above and below.
 *
 * The frame is not made narrower than 900 on purpose: at 880 and below the
 * geometry module switches to the stacked composition, and cropping into that
 * would silently re-lay the whole map out.
 *
 * This is a frame, not geometry: nothing here projects anything, and every part
 * of the map still reads its coordinates out of `lib/coordinator-map`. It
 * belongs in that module the next time the viewports are touched.
 */
const HERO_MAP_CROP = { left: 372, top: 20, width: 900, height: 570 } as const;

const HERO_MAP_VIEWPORT: Viewport = {
  ...MAP_VIEWPORT_DESKTOP,
  width: HERO_MAP_CROP.width,
  height: HERO_MAP_CROP.height,
  originX: MAP_VIEWPORT_DESKTOP.originX - HERO_MAP_CROP.left,
  originY: MAP_VIEWPORT_DESKTOP.originY - HERO_MAP_CROP.top,
};

/** The width at which the geometry module stops composing the map as a diamond
 * and composes it as a vertical stack instead. It is a property of that module,
 * not a breakpoint of this page, which is why it is a media query string and not
 * a Tailwind variant. */
const COMPACT_QUERY = "(max-width: 880px)";

function useCompactMap() {
  const [compact, setCompact] = useState(false);

  useEffect(() => {
    const query = window.matchMedia(COMPACT_QUERY);
    const sync = () => setCompact(query.matches);

    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  return compact;
}

export function Hero() {
  const { phase } = useMapStory();
  const compact = useCompactMap();
  const sectionRef = useRef<HTMLElement>(null);
  const { reduced, desktop } = useLandingMotion();

  // ON-LOAD CHOREOGRAPHY, NOT A SCROLL REVEAL. The hero is above the fold at
  // first paint, so it must never start hidden waiting for a `ScrollTrigger`
  // threshold the way `SectionReveal` gates every section below it —
  // `SectionReveal`'s own doc comment on `useLandingMotion` calls out
  // exactly this risk. This plays immediately on mount instead, and then
  // hands off to a second, independent effect: a cheap scroll-OUT as the
  // next section covers the hero, tied to scroll position via `scrub`
  // rather than a fixed duration.
  useGSAP(
    () => {
      const section = sectionRef.current;
      if (!section) return;
      const content = section.querySelectorAll("[data-hero-reveal]");

      // Same universal gate every other new trigger in this pass uses:
      // reduced motion AND non-desktop both settle immediately, no timeline.
      if (reduced || !desktop) {
        gsap.set(content, { y: 0, opacity: 1 });
        return;
      }

      gsap.timeline().from(content, {
        y: TRAVEL.loose,
        opacity: 0,
        duration: seconds(DURATIONS.reveal),
        ease: gsapEase("settle"),
        stagger: seconds(SECTION_REVEAL_STAGGER_MS),
      });

      // The cheap scroll-out: transform/opacity only, scrubbed directly to
      // scroll position (`ease: "none"` — scrub already tracks the
      // scrollbar, so an eased curve on top would fight it) as the section
      // below scrolls up over the hero. No pin: this is the hero LEAVING,
      // not a story being told while the reader holds still.
      gsap.timeline({
        scrollTrigger: {
          trigger: section,
          start: "top top",
          end: "bottom top",
          scrub: 0.5,
        },
      }).to(content, {
        y: -TRAVEL.loose,
        opacity: 0.25,
        ease: gsapEase("linear"),
      });
    },
    { scope: sectionRef, dependencies: [reduced, desktop], revertOnUpdate: true },
  );

  // At `xl` the hero fills exactly one viewport and centres itself in it, so
  // its bottom border lands on the bottom of the frame rather than leaving a
  // seam of bare space under it. Nothing pins or measures this section any
  // more — `app/(marketing)/page.tsx` renders it once, like every other
  // section, and `useMapStory`'s timer just loops the map's beats forever on
  // its own clock (see the comment above `useMapStory` for the retired
  // scroll-driven mechanics and why they went).
  return (
    <section
      ref={sectionRef}
      id="hero"
      data-surface="dark"
      className="relative isolate overflow-hidden border-b border-border pt-20 xl:flex xl:min-h-svh xl:flex-col xl:justify-center"
    >
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10"
        style={{
          backgroundImage:
            "radial-gradient(circle at 78% 8%, rgb(243 107 50 / 0.08), transparent 23rem)",
        }}
      />
      {/* The `xl` paddings are smaller than the ones below it because the pinned
          section is already a whole viewport tall and centres its content: the
          air is there whether or not the padding is, and every pixel spent on it
          is a pixel the map's own frame loses off the bottom of the screen. */}
      <div className="mx-auto w-full max-w-[1440px] px-5 pb-10 sm:px-6 xl:px-12 xl:pb-6">
        <div className="grid min-w-0 items-center gap-10 py-8 xl:grid-cols-[minmax(30rem,1fr)_minmax(0,1fr)] xl:gap-10 xl:py-4 2xl:gap-14">
          <div className="min-w-0">
            <p data-hero-reveal className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
              The open compute network
            </p>
            <h1 data-hero-reveal className="mt-5 max-w-[78rem] text-[clamp(2.5rem,4.9vw,5rem)] font-semibold leading-[0.93] tracking-[-0.058em]">
              <span className="block lg:whitespace-nowrap">Computing power,</span>
              <span className="block text-muted-foreground lg:whitespace-nowrap">without the lock-in.</span>
            </h1>
            <p data-hero-reveal className="mt-7 max-w-[58ch] text-[15px] leading-[1.62] tracking-[-0.006em] text-muted-foreground sm:mt-8">
              One open network connecting people who need compute with machines ready to work.
            </p>
            <div data-hero-reveal className="mt-7 max-w-[58ch] sm:mt-8">
              <HeroMarketSwitch />
            </div>
            <div data-hero-reveal className="mt-7 flex flex-wrap gap-2.5">
              <Link
                href={MARKETING.consolePath}
                title="Get early access"
                className="interactive inline-flex min-h-10 items-center gap-2 rounded-[7px] border border-primary bg-primary px-4 text-[13px] font-semibold text-primary-foreground hover:bg-[var(--z-orange-bright)]"
              >
                Get early access
                <ArrowRight weight="bold" className="h-4 w-4" />
              </Link>
              <Link
                href={MARKETING.machinesPath}
                className="interactive inline-flex min-h-10 items-center gap-2 rounded-[7px] border border-[var(--z-border-strong)] bg-surface px-4 text-[13px] font-semibold hover:bg-[var(--z-surface-hover)]"
              >
                Provide compute
                <ArrowRight weight="bold" className="h-4 w-4" />
              </Link>
            </div>
          </div>

          {/* Capped, because both viewBoxes have a designed size. Stretching the
              420-unit portrait frame across a 700 px tablet would make it a
              thousand pixels tall, and the landscape frame drawn at the full
              width of a stacked 1024 px layout is a 650 px-tall diagram sitting
              under three lines of copy. Only the pinned two-column layout at
              `xl` gets the map at its column's full width. */}
          <div
            className={
              compact
                ? "mx-auto w-full max-w-[26rem]"
                : "mx-auto w-full max-w-[46rem] xl:max-w-none"
            }
          >
            <CoordinatorMap
              phase={phase}
              viewport={compact ? MAP_VIEWPORT_COMPACT : HERO_MAP_VIEWPORT}
            />
          </div>
        </div>
      </div>
    </section>
  );
}
