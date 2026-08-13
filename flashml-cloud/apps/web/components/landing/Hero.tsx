"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight } from "@phosphor-icons/react/dist/ssr";
import { CoordinatorMap } from "@/components/landing/coordinator-map/CoordinatorMap";
import { HeroMarketSwitch } from "@/components/landing/HeroMarketSwitch";
import { useMapStory } from "@/components/landing/coordinator-map/useMapStory";
import { MAP_VIEWPORT_COMPACT, MAP_VIEWPORT_DESKTOP, type Viewport } from "@/lib/coordinator-map";
import { MARKETING } from "@/lib/marketing";

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
  const { phase, ref } = useMapStory();
  const compact = useCompactMap();

  // At `xl` the hero is pinned by the scroll track in `app/(marketing)/page.tsx`,
  // so it takes exactly one viewport and centres itself in it: its bottom border
  // then lands on the bottom of the frame instead of leaving a seam of bare
  // track under it. Below `xl` nothing pins and the section stays an ordinary
  // block, which is what tells `useMapStory` to run the timer instead.
  return (
    <section
      ref={ref}
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
        <div className="grid min-w-0 items-center gap-10 py-8 xl:grid-cols-[minmax(28rem,.9fr)_minmax(0,1.1fr)] xl:gap-10 xl:py-4 2xl:gap-14">
          <div className="min-w-0">
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
              The open compute network
            </p>
            <h1 className="mt-5 max-w-[78rem] text-[clamp(2.75rem,5.6vw,5.7rem)] font-semibold leading-[0.93] tracking-[-0.058em]">
              <span className="block lg:whitespace-nowrap">Computing power,</span>
              <span className="block text-muted-foreground lg:whitespace-nowrap">without the lock-in.</span>
            </h1>
            <p className="mt-7 max-w-[58ch] text-[15px] leading-[1.62] tracking-[-0.006em] text-muted-foreground sm:mt-8">
              Zolli is building a network that connects people who need compute with machines ready to work—across personal hardware, community hosts, and cloud providers.
            </p>
            <div className="mt-7 max-w-[58ch] sm:mt-8">
              <HeroMarketSwitch />
            </div>
            <div className="mt-7 flex flex-wrap gap-2.5">
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
