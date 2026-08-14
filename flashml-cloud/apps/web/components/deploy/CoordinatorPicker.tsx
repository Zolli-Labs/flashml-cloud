"use client";

import { Label } from "@/components/ui/label";
import {
  COORDINATOR_PICKER_OPTIONS,
  type CoordinatorVenue,
} from "@/lib/job-coordinator";

/**
 * Which control plane tracks this job — Render, the incumbent private
 * service, or the Alibaba Function Compute deployment running the same
 * coordinator in Singapore.
 *
 * Labelled for what a submitter is actually choosing ("where this job is
 * coordinated"), not for the API's `coordinator` enum — a person recognises
 * "Render (private service)" and "Function Compute (Singapore)", not
 * `"render"` / `"fc"`. `COORDINATOR_PICKER_OPTIONS` is the one place those
 * two facts are paired, so this component and any other picker stay unable
 * to disagree about what the labels say.
 *
 * Two plain buttons in a `radiogroup`, not `Select`: two mutually exclusive
 * options are cheaper to compare side by side than behind a click, and the
 * repo form beside this one (`Templates` / `flashml.yaml` / `GitHub repo`)
 * already reads that way — this reuses the same shape rather than pulling
 * in a fourth control pattern for one more binary choice.
 */
export function CoordinatorPicker({
  value,
  onChange,
  disabled,
}: {
  value: CoordinatorVenue;
  onChange: (next: CoordinatorVenue) => void;
  disabled?: boolean;
}) {
  return (
    <div>
      <Label>Coordinator</Label>
      <div
        role="radiogroup"
        aria-label="Coordinator"
        className="mt-1.5 grid gap-2 sm:grid-cols-2"
      >
        {COORDINATOR_PICKER_OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={value === option.value}
            disabled={disabled}
            onClick={() => onChange(option.value)}
            className={`rounded-lg border px-3 py-2 text-left text-sm transition-colors disabled:opacity-50 ${
              value === option.value
                ? "border-brand/50 bg-surface-2 font-medium text-foreground"
                : "border-border bg-surface text-muted-foreground hover:text-foreground"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}
