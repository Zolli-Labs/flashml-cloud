"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { gsap } from "gsap";
import { CustomEase } from "gsap/CustomEase";
import { EASINGS, type EasingName } from "@/lib/motion/timing";

/* ─── The GSAP half of `lib/motion/timing.ts` ──────────────────────────
 *
 * The table is the decision; this is the one place GSAP is taught to speak
 * it. Registered here, at module scope, because every GSAP call site on the
 * landing page (`SectionReveal`, `CommitSignal`, `RecoveryStack`) already
 * imports `useLandingMotion` from this file — so the eases exist before any
 * of them can ask for one, and they are created exactly once.
 *
 * EXACT, NOT APPROXIMATE. `timing.ts` offers both `gsapName` (the nearest
 * built-in) and `gsapPath` (the identical curve via `CustomEase`). Measured
 * against the real curves, the built-ins are not close enough to share a
 * page with `motion/react`: `power2.out` diverges from `settle` by up to
 * 14.6% of progress, which on a 560ms reveal is a 34ms skew at the halfway
 * point and 1.75px on a 12px travel. `CustomEase` reproduces the same curves
 * to within 0.08%. `gsap/CustomEase` ships in the free 3.15.0 package, so
 * the exact form costs one `registerPlugin` and no licence. */
gsap.registerPlugin(CustomEase);

const EASE_PREFIX = "zolli-";

for (const easing of Object.values(EASINGS)) {
  CustomEase.create(`${EASE_PREFIX}${easing.name}`, easing.gsapPath);
}

/** The GSAP `ease` string for a curve in the table. Same curve, to the
 * fourth decimal, as the `bezier` a `motion/react` call site would pass. */
export function gsapEase(name: EasingName): string {
  return `${EASE_PREFIX}${name}`;
}

export type LandingMotionState = {
  reduced: boolean;
  desktop: boolean;
  finePointer: boolean;
  documentVisible: boolean;
};

const INITIAL_MOTION_STATE: LandingMotionState = {
  reduced: true,
  desktop: false,
  finePointer: false,
  documentVisible: true,
};

const LandingMotionContext = createContext<LandingMotionState>(INITIAL_MOTION_STATE);

export function LandingMotionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<LandingMotionState>(INITIAL_MOTION_STATE);

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
    const desktop = window.matchMedia("(min-width: 1024px)");
    const finePointer = window.matchMedia("(pointer: fine)");
    const sync = () =>
      setState({
        reduced: reduced.matches,
        desktop: desktop.matches,
        finePointer: finePointer.matches,
        documentVisible: document.visibilityState === "visible",
      });

    sync();
    reduced.addEventListener("change", sync);
    desktop.addEventListener("change", sync);
    finePointer.addEventListener("change", sync);
    document.addEventListener("visibilitychange", sync);

    return () => {
      reduced.removeEventListener("change", sync);
      desktop.removeEventListener("change", sync);
      finePointer.removeEventListener("change", sync);
      document.removeEventListener("visibilitychange", sync);
    };
  }, []);

  const value = useMemo(() => state, [state]);

  return (
    <LandingMotionContext.Provider value={value}>
      {children}
    </LandingMotionContext.Provider>
  );
}

export function useLandingMotion() {
  return useContext(LandingMotionContext);
}
