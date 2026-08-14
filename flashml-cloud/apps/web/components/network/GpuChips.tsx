"use client";

import type { GpuChip } from "@/lib/network/providers";

/**
 * The GPU models a provider has, as chips.
 *
 * MONO, BECAUSE A MACHINE WROTE IT. `rtx3090` is a string a driver reported,
 * not a name a person chose, and `app/globals.css` already encodes that rule
 * for `.meta`, `.metric-value` and `.terminal-text`. Nothing here tidies the
 * string: a console that rewrote "NVIDIA GeForce RTX 3090" into something
 * prettier would be deciding what hardware somebody owns.
 *
 * `limit` folds the tail into a `+2` chip rather than wrapping a table cell to
 * four lines. The full list is always one page away on the provider's own
 * page, so nothing is lost — and the count is stated rather than the list
 * silently truncated.
 *
 * No GPUs is NOT an empty row of chips. `chips` empty renders the CPU-only
 * sentence, because "this machine has no GPU" is a fact worth a word.
 */
export function GpuChips({
  chips,
  limit,
  showVram = true,
}: {
  chips: GpuChip[];
  limit?: number;
  /** The table hides VRAM (the column is already five wide); the specs card
   * shows it, because `rtx3090 24Gi` is the whole answer there. */
  showVram?: boolean;
}) {
  if (chips.length === 0) {
    return <span className="text-muted-foreground">CPU only</span>;
  }

  const shown = limit ? chips.slice(0, limit) : chips;
  const hidden = chips.length - shown.length;

  return (
    <span className="flex flex-wrap items-center gap-1">
      {shown.map((chip) => (
        <span
          key={chip.model}
          className="inline-flex items-center gap-1 rounded border border-border bg-surface-2 px-1.5 py-px font-mono text-[10.5px] leading-[1.5] whitespace-nowrap"
        >
          {chip.model}
          {showVram && chip.vramText && (
            <span className="text-muted-foreground">{chip.vramText}</span>
          )}
          {chip.count > 1 && (
            <span className="tabular-nums text-brand-foreground">
              ×{chip.count}
            </span>
          )}
        </span>
      ))}
      {hidden > 0 && (
        <span className="meta whitespace-nowrap">+{hidden}</span>
      )}
    </span>
  );
}
