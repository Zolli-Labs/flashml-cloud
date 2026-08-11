"use client";

// Which beat of the coordinator's story the map is drawing.
//
// Three sources, in order of preference:
//
//  1. **Scroll.** The hero is pinned inside a taller track — the element
//     carrying `data-hero-scroll` in `app/(marketing)/page.tsx` — and the
//     distance that track still has to travel is the story's timeline. This is
//     the Apple pattern without the rendered frame sequence: the beats are
//     element state changes, so there is nothing to pre-render and no
//     resolution to pick.
//  2. **A timer**, wherever the track gives the hero no distance to spend. It
//     runs the sixteen-second loop ONCE and then rests on the last beat until a
//     pointer or the keyboard asks for it again. A hero that loops forever
//     reads as a banner advertisement.
//  3. **Nothing**, under `prefers-reduced-motion: reduce`: one representative
//     frame, with no `requestAnimationFrame` and no timer ever scheduled. That
//     is an acceptance criterion of the design, so it is an early return before
//     any scheduling happens rather than an animation that has been slowed to a
//     stop.
//
// Which of the three is in force is never asked as a media query. The track
// either has spare height or it does not, and measuring that answers the
// question for every breakpoint, every zoom level and every layout at once.

import { useEffect, useRef, useState, type RefObject } from "react";
import { useLandingMotion } from "@/components/landing/motion/LandingMotionProvider";
import { phaseForProgress, type MapPhase } from "@/lib/coordinator-map";

/** The tall container whose scroll distance is the story's timeline. Set by
 * `app/(marketing)/page.tsx`; the hero finds it by walking up from itself. */
export const HERO_SCROLL_TRACK = "[data-hero-scroll]";

/**
 * The beat drawn when nothing is driving the story — under reduced motion, and
 * on the server.
 *
 * It is the frame with the whole claim in it: a machine has died, its work is
 * already running somewhere else, and the checkpoint counter moved. That makes
 * it the right thing to serve to a crawler and to anyone whose JavaScript has
 * not arrived yet, not only to a visitor who asked for stillness.
 */
const STATIC_PHASE: MapPhase = "resumed";

/** One pass of the timer fallback, and how finely it is sampled out of
 * `phaseForProgress`. 200 samples resolves every boundary in the design's phase
 * table exactly — 0.30, 0.50 and 0.75 are all multiples of 0.005. */
const STORY_DURATION_MS = 16_000;
const STORY_SAMPLES = 200;

interface Beat {
  readonly phase: MapPhase;
  readonly durationMs: number;
}

/**
 * The four beats and how long each holds.
 *
 * Sampled out of `phaseForProgress` rather than restated, so the timer and the
 * scroll story cannot disagree about where a beat begins: moving a boundary in
 * the geometry module moves this with it. Computed once, at module load, from a
 * pure function — there is no clock and no DOM in it.
 */
const STORY_BEATS: readonly Beat[] = (() => {
  const sample = STORY_DURATION_MS / STORY_SAMPLES;
  const beats: Beat[] = [];
  for (let index = 0; index < STORY_SAMPLES; index++) {
    const phase = phaseForProgress((index + 0.5) / STORY_SAMPLES);
    const last = beats[beats.length - 1];
    if (last && last.phase === phase) {
      beats[beats.length - 1] = { phase, durationMs: last.durationMs + sample };
    } else {
      beats.push({ phase, durationMs: sample });
    }
  }
  return beats;
})();

export interface MapStory {
  /** The beat the map should draw. */
  readonly phase: MapPhase;
  /** Attach to the hero's own root element. The scroll track is found from it,
   * and it is the element hover and focus are read from. */
  readonly ref: RefObject<HTMLElement | null>;
}

export function useMapStory(): MapStory {
  const { reduced, documentVisible } = useLandingMotion();
  const ref = useRef<HTMLElement | null>(null);
  // The animated story starts where the story starts. `STATIC_PHASE` is the
  // frame for people who asked for stillness, and serving it here too made the
  // hero visibly rewind on load — `resumed` for a beat, then a snap back to
  // `running` as the timer took over. Reduced motion still gets `resumed`, on
  // the way out of this hook.
  const [phase, setPhase] = useState<MapPhase>("running");
  // `null` until the track has been measured once. `false` means it leaves the
  // hero no distance to spend, and the timer has to tell the story instead.
  const [scrollLinked, setScrollLinked] = useState<boolean | null>(null);

  useEffect(() => {
    // `useLandingMotion` reports `reduced` until its own effect has run, so the
    // first render of every visit — server and client — takes this branch and
    // schedules nothing. Motion starts a frame later, for the people who want
    // it.
    if (reduced) return;

    const hero = ref.current;
    const track = hero?.closest<HTMLElement>(HERO_SCROLL_TRACK) ?? null;
    if (!hero || !track) {
      setScrollLinked(false);
      return;
    }

    let frame = 0;
    let listening = false;

    const measure = () => {
      frame = 0;
      // What the track has left once the pinned hero has taken its share. Zero
      // whenever the hero is not pinned — which is the honest test for "this
      // layout is not going to drive anything", and costs no restated
      // breakpoint.
      const travel = track.offsetHeight - hero.offsetHeight;
      if (travel <= 0) {
        setScrollLinked(false);
        return;
      }
      setScrollLinked(true);
      setPhase(phaseForProgress(-track.getBoundingClientRect().top / travel));
    };

    const schedule = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(measure);
    };

    // `scroll` fires for the whole document on every wheel notch; the hero only
    // has an opinion while it is on screen.
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting === listening) return;
      listening = entry.isIntersecting;
      if (listening) {
        window.addEventListener("scroll", schedule, { passive: true });
        schedule();
      } else {
        window.removeEventListener("scroll", schedule);
      }
    });

    observer.observe(track);
    window.addEventListener("resize", schedule);
    schedule();

    return () => {
      observer.disconnect();
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [reduced]);

  useEffect(() => {
    if (reduced || scrollLinked !== false || !documentVisible) return;

    const hero = ref.current;
    let index = 0;
    let timer = 0;
    let resting = false;

    const step = () => {
      const beat = STORY_BEATS[index];
      setPhase(beat.phase);
      // One pass, then stop. The last beat is the one worth resting on: the
      // result was accepted and goodput recovered.
      if (index === STORY_BEATS.length - 1) {
        resting = true;
        return;
      }
      index += 1;
      timer = window.setTimeout(step, beat.durationMs);
    };

    // Asking to see it again, from rest only — restarting mid-story would
    // punish the reader for moving the mouse across the hero.
    const replay = () => {
      if (!resting) return;
      resting = false;
      index = 0;
      step();
    };

    step();
    hero?.addEventListener("pointerenter", replay);
    hero?.addEventListener("focusin", replay);

    return () => {
      window.clearTimeout(timer);
      hero?.removeEventListener("pointerenter", replay);
      hero?.removeEventListener("focusin", replay);
    };
  }, [documentVisible, reduced, scrollLinked]);

  return { phase: reduced ? STATIC_PHASE : phase, ref };
}
