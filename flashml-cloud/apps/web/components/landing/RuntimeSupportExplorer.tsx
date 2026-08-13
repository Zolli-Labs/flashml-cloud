"use client";

import { useState, type CSSProperties } from "react";
import {
  siDocker,
  siGithub,
  siNumpy,
  siNvidia,
  siPandas,
  siPython,
  siPytorch,
  siScikitlearn,
  siScipy,
} from "simple-icons";
import {
  RUNTIME_SUPPORT,
  type RuntimeIconKey,
} from "@/lib/landing/platform";
import { DURATIONS, EASINGS } from "@/lib/motion/timing";

/** `transition-colors` supplies the property list; the table supplies the
 * timing. Tailwind's stock default is 150ms on `cubic-bezier(0.4, 0, 0.2, 1)`
 * — a second curve competing with the 180ms house control curve on 30 other
 * elements of this page. Same properties, one curve. */
const CONTROL_TRANSITION: CSSProperties = {
  transitionDuration: `${DURATIONS.control}ms`,
  transitionTimingFunction: EASINGS.control.css,
};

const RUNTIME_ICONS = {
  python: siPython,
  numpy: siNumpy,
  pandas: siPandas,
  scikitlearn: siScikitlearn,
  scipy: siScipy,
  pytorch: siPytorch,
  nvidia: siNvidia,
  docker: siDocker,
  github: siGithub,
} as const satisfies Record<RuntimeIconKey, { path: string; title: string }>;

function RuntimeIcon({ icon }: { icon: RuntimeIconKey }) {
  const data = RUNTIME_ICONS[icon];

  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width={16}
      height={16}
      fill="currentColor"
      className="shrink-0"
    >
      <path d={data.path} />
    </svg>
  );
}

export function RuntimeSupportExplorer() {
  const [selectedIndex, setSelectedIndex] = useState(0);
  const selected = RUNTIME_SUPPORT[selectedIndex];
  const selectedImageAlias = "imageAlias" in selected ? selected.imageAlias : undefined;

  return (
    <div data-runtime-explorer>
      <div
        role="group"
        aria-label="Runtime support"
        className="flex flex-wrap gap-2"
      >
        {RUNTIME_SUPPORT.map((runtime, index) => {
          const active = index === selectedIndex;

          return (
            <button
              key={runtime.label}
              type="button"
              data-runtime-button={runtime.icon}
              aria-pressed={active}
              onClick={() => setSelectedIndex(index)}
              style={CONTROL_TRANSITION}
              className={`inline-flex items-center gap-2 rounded-full border px-3.5 py-2 text-sm font-medium transition-colors ${
                active
                  ? "border-brand-foreground bg-card text-foreground"
                  : "border-border text-muted-foreground hover:border-brand-foreground hover:text-foreground"
              }`}
            >
              <RuntimeIcon icon={runtime.icon} />
              <span>{runtime.label}</span>
            </button>
          );
        })}
      </div>

      <div
        data-runtime-detail
        aria-live="polite"
        className="mt-6 min-h-[4.5rem] rounded-[10px] border border-border bg-card p-5"
      >
        <div className="flex items-center gap-2.5">
          <RuntimeIcon icon={selected.icon} />
          <p className="text-[15px] font-semibold">{selected.label}</p>
        </div>
        {selectedImageAlias && selectedImageAlias.length > 0 ? (
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            Curated image: {selectedImageAlias.join(", ")}
          </p>
        ) : (
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
            No curated image is registered for this runtime.
          </p>
        )}
      </div>
    </div>
  );
}
