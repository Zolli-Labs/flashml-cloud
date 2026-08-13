"use client";

/** Capability measurement for the console half of the motion system.
 *
 * THIS IS THE CONSOLE'S EQUIVALENT OF `LandingMotionProvider`, NOT A
 * REPLACEMENT FOR IT. `components/landing/motion/LandingMotionProvider.tsx`
 * already measures exactly this for `app/(marketing)` and every animated
 * landing component gates on `useLandingMotion()`. Rewriting those call sites
 * onto a second provider would be churn on files another session is actively
 * rewriting, for no behavioural gain. So the two providers coexist, expose the
 * same four measurements under the same names, and — this is the part that
 * matters — both feed the SAME resolution rules in `lib/motion/reduced.ts`.
 * The duplicated `matchMedia` boilerplate is the price of not touching
 * contended files; the decisions are not duplicated.
 *
 * THREE WAYS A PRIMITIVE LEARNS THE PREFERENCE, in precedence order:
 *
 *   1. An explicit `reduced` prop. This is how a LANDING call site composes:
 *      `<Reveal reduced={useLandingMotion().reduced}>`. No second provider,
 *      no second listener, one source of truth per surface.
 *   2. This provider, through context.
 *   3. Self-measurement. A primitive used with neither still behaves
 *      correctly — it measures `prefers-reduced-motion` itself.
 *
 * Rule 3 exists because the alternative is worse. A context default of "not
 * reduced" would animate for people who asked not to; a default of "reduced"
 * would silently disable motion for anyone who forgot the provider, which is
 * a bug that looks like the feature simply not being built. Measuring is the
 * only answer that is right in both directions.
 *
 * WHAT IT REPORTS BEFORE IT HAS MEASURED. `reduced: true`, so `mode` is
 * `static`. `matchMedia` does not exist during a server render and the first
 * client render must match the server's markup, so for one render every
 * primitive believes motion is off — which means no element is ever primed
 * into a hidden state before the preference is known. Same conservative
 * default `LandingMotionProvider` already uses.
 *
 * IT ALSO CONFIGURES `motion/react` ITSELF. The wrapped `MotionConfig` from
 * `motion/react` carries `reducedMotion="user"`, which makes every `motion`
 * component in the tree respect the OS setting whether or not it went through
 * one of this repo's primitives — and `"always"` when the kill switch is off,
 * so `enabled={false}` really is global rather than advisory.
 */

import { MotionConfig as MotionRuntimeConfig } from "motion/react";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { resolveMotionMode, type MotionMode } from "@/lib/motion/reduced";

/** The same four measurements `LandingMotionState` carries, plus the kill
 * switch and the mode they resolve to. Field names match deliberately: a
 * component can be moved between the two surfaces without a rename. */
export interface MotionCapabilityState {
  reduced: boolean;
  desktop: boolean;
  finePointer: boolean;
  documentVisible: boolean;
  enabled: boolean;
  mode: MotionMode;
}

type Measurements = Pick<
  MotionCapabilityState,
  "reduced" | "desktop" | "finePointer" | "documentVisible"
>;

const UNMEASURED: Measurements = {
  reduced: true,
  desktop: false,
  finePointer: false,
  documentVisible: true,
};

const MotionCapabilityContext = createContext<MotionCapabilityState | null>(
  null
);

const DESKTOP_QUERY = "(min-width: 1024px)";
const FINE_POINTER_QUERY = "(pointer: fine)";
const REDUCED_QUERY = "(prefers-reduced-motion: reduce)";

/** Subscribe to the media queries, or do nothing at all when `active` is
 * false — which is the case for every consumer that already has a provider or
 * an explicit prop, so the listeners exist once per surface rather than once
 * per animated element. */
function useMeasurements(active: boolean): Measurements {
  const [measurements, setMeasurements] = useState<Measurements>(UNMEASURED);

  useEffect(() => {
    if (!active) return;
    if (typeof window === "undefined" || !window.matchMedia) return;

    const reduced = window.matchMedia(REDUCED_QUERY);
    const desktop = window.matchMedia(DESKTOP_QUERY);
    const finePointer = window.matchMedia(FINE_POINTER_QUERY);

    const sync = () =>
      setMeasurements({
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
  }, [active]);

  return measurements;
}

export function MotionConfig({
  children,
  enabled = true,
}: {
  children?: ReactNode;
  /** The global kill switch. `false` settles every primitive in the tree and
   * puts `motion/react` into `reducedMotion="always"`. */
  enabled?: boolean;
}) {
  const measurements = useMeasurements(true);

  const value = useMemo<MotionCapabilityState>(
    () => ({
      ...measurements,
      enabled,
      mode: resolveMotionMode({ reduced: measurements.reduced, enabled }),
    }),
    [measurements, enabled]
  );

  return (
    <MotionCapabilityContext.Provider value={value}>
      <MotionRuntimeConfig reducedMotion={enabled ? "user" : "always"}>
        {children}
      </MotionRuntimeConfig>
    </MotionCapabilityContext.Provider>
  );
}

/** The provider's measurements, or this component's own if there is no
 * provider above it. */
export function useMotionCapability(): MotionCapabilityState {
  const provided = useContext(MotionCapabilityContext);
  const measurements = useMeasurements(provided === null);

  return useMemo<MotionCapabilityState>(() => {
    if (provided) return provided;
    return {
      ...measurements,
      enabled: true,
      mode: resolveMotionMode({ reduced: measurements.reduced, enabled: true }),
    };
  }, [provided, measurements]);
}

/** What every primitive calls. `reduced`, when given, wins over everything —
 * that is the seam a landing call site passes `useLandingMotion().reduced`
 * through. */
export function useMotionMode(reduced?: boolean): MotionMode {
  const capability = useMotionCapability();
  if (reduced === undefined) return capability.mode;
  return resolveMotionMode({ reduced, enabled: capability.enabled });
}
