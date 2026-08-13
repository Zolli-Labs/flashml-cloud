"use client";

import { useState, type CSSProperties } from "react";
import {
  MACHINE_HINTS,
  inferPlatformFamily,
  type MachineHint,
} from "@/lib/landing/platform";
import { DURATIONS, EASINGS } from "@/lib/motion/timing";

/** `transition-colors` supplies the property list; the table supplies the
 * timing, so this control agrees with the 180ms house curve rather than with
 * Tailwind's stock 150ms `cubic-bezier(0.4, 0, 0.2, 1)`. */
const CONTROL_TRANSITION: CSSProperties = {
  transitionDuration: `${DURATIONS.control}ms`,
  transitionTimingFunction: EASINGS.control.css,
};

export function MachineCompatibilityCheck() {
  const [result, setResult] = useState<MachineHint | null>(null);

  const checkThisBrowser = () => {
    const family = inferPlatformFamily({
      userAgent: navigator.userAgent,
      platform: navigator.platform,
      maxTouchPoints: navigator.maxTouchPoints,
    });

    setResult(MACHINE_HINTS[family]);
  };

  return (
    <div className="rounded-[10px] border border-border bg-card p-5 sm:p-6">
      <p className="text-[15px] font-semibold">Not sure where you stand?</p>
      <p className="mt-1.5 max-w-[52ch] text-sm leading-relaxed text-muted-foreground">
        The browser can only guess the operating system it runs on. It cannot
        verify CPU architecture, Docker, or GPU availability.
      </p>
      <button
        type="button"
        onClick={checkThisBrowser}
        style={CONTROL_TRANSITION}
        className="mt-4 inline-flex h-10 items-center rounded-full border border-brand-foreground px-4 text-sm font-medium text-brand-foreground transition-colors hover:bg-brand-foreground hover:text-background"
      >
        Check this browser
      </button>

      {result ? (
        <div
          role="status"
          aria-live="polite"
          data-machine-result
          className="mt-4 rounded-[8px] border border-border bg-[var(--z-app-bg)] p-4 text-sm leading-relaxed"
        >
          <p className="font-semibold text-foreground">{result.headline}</p>
          <p className="mt-1 text-muted-foreground">{result.body}</p>
          <p className="mt-1 text-muted-foreground">{result.nextStep}</p>
        </div>
      ) : null}
    </div>
  );
}
