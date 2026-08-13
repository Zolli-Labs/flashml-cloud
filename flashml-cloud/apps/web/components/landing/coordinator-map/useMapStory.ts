"use client";

// Which beat of the coordinator's story the map is drawing.
//
// One driver only: a timer, walking `STORY_BEATS` and looping forever. This
// hero used to have a second driver — a scroll-track reader that read the
// remaining travel of a pinned `220svh` wrapper in `app/(marketing)/page.tsx`
// and turned it into a beat, Apple-style, with the timer only stepping in
// where the track left the hero no distance to spend. That gave a reader who
// scrolled past the hero a story timed to their own hand, but it also meant
// the story quietly changed shape by breakpoint and by entry point: anyone
// below the `xl` pin, on a phone, or arriving straight at `/#hero` got the
// timer's cut, and anyone scrolling past the pinned section on desktop got a
// different one. The owner cut the scroll track entirely and asked for one
// behavior everywhere: the story just plays, on its own, forever. So there is
// nothing left to measure and nothing left to prefer between — the timer is
// the whole driver, unconditionally.
//
// Looping forever also retires the old "replay on hover" escape hatch. That
// existed because a single pass followed by permanent rest could not be
// resumed any other way, and letting it loop unprompted read as a banner ad.
// Both premises are gone: the story never permanently rests any more (it
// holds on the last beat only as long as that beat's own duration, then
// continues), and playing without being asked is now the explicit design,
// not the thing being guarded against. A pointer or keyboard visitor sees
// whatever beat is currently up, exactly like anyone else.
//
// The only remaining branch is `prefers-reduced-motion: reduce`, which is not
// a second driver so much as the absence of one: one representative frame,
// no `requestAnimationFrame`, no timer ever scheduled. That is an acceptance
// criterion of the design, so it is an early return before any scheduling
// happens rather than an animation that has been slowed to a stop.

import { useEffect, useState } from "react";
import { useLandingMotion } from "@/components/landing/motion/LandingMotionProvider";
import { phaseForProgress, type MapPhase } from "@/lib/coordinator-map";

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

/** One lap of the loop, and how finely it is sampled out of
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
 * Sampled out of `phaseForProgress` rather than restated, so the timer cannot
 * disagree with the geometry module about where a beat begins: moving a
 * boundary there moves this with it. Computed once, at module load, from a
 * pure function — there is no clock and no DOM in it.
 *
 * The last beat's `durationMs` doubles as the loop's hold: looping is "run
 * `step` again instead of stopping," not a second timing constant that could
 * drift from the first. At the current table that is 4s on `accepted` (the
 * final quarter of `STORY_DURATION_MS`), inside the ~3–4s the design calls
 * for.
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
}

export function useMapStory(): MapStory {
  const { reduced, documentVisible } = useLandingMotion();
  // The animated story starts where the story starts. `STATIC_PHASE` is the
  // frame for people who asked for stillness, and serving it here too made the
  // hero visibly rewind on load — `resumed` for a beat, then a snap back to
  // `running` as the timer took over. Reduced motion still gets `resumed`, on
  // the way out of this hook.
  const [phase, setPhase] = useState<MapPhase>("running");

  useEffect(() => {
    // `useLandingMotion` reports `reduced` until its own effect has run, so the
    // first render of every visit — server and client — takes this branch and
    // schedules nothing. Motion starts a frame later, for the people who want
    // it. `documentVisible` gates the same way: a backgrounded tab holds the
    // last beat it drew rather than racing the clock while nobody can see it.
    if (reduced || !documentVisible) return;

    let index = 0;
    let timer = 0;

    const step = () => {
      const beat = STORY_BEATS[index];
      setPhase(beat.phase);
      index = (index + 1) % STORY_BEATS.length;
      timer = window.setTimeout(step, beat.durationMs);
    };

    step();

    return () => window.clearTimeout(timer);
  }, [documentVisible, reduced]);

  return { phase: reduced ? STATIC_PHASE : phase };
}
