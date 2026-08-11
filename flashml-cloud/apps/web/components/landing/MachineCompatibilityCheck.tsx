"use client";

import { useState } from "react";
import {
  MACHINE_HINTS,
  inferPlatformFamily,
  type MachineHint,
} from "@/lib/landing/platform";

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
