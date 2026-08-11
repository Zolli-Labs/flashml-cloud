"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

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
