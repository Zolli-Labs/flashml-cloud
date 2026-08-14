"use client";

/**
 * A battery: how full one provider's CPU or memory is, beside the numbers
 * that say it exactly.
 *
 * IT IS NOT THE MEASUREMENT, it is an aid to scanning one. Every cell that
 * carries this glyph also carries `used / total` in figures, because a 22×10
 * pixel bar cannot be read to better than about a fifth and nobody should
 * have to. What it buys is the column: twelve rows of "12 / 64" are twelve
 * fractions to divide in your head, and twelve little bars are a shape you
 * take in at once.
 *
 * NULL IS AN EMPTY CELL, NOT AN EMPTY BATTERY. A machine that did not report
 * its core count and a machine with nothing running look identical as a bar
 * at 0%, so the unmeasured case renders nothing at all and the cell says
 * "not reported" in words.
 */
export function MeterGlyph({
  fraction,
  label,
}: {
  /** 0–1. Clamped, because a server that reports more used than total is a
   * server with a bug, not a battery at 140%. */
  fraction: number;
  /** Read out to a screen reader in place of the drawing. */
  label: string;
}) {
  const f = Math.min(1, Math.max(0, fraction));
  const fill = WIDTH_INNER * f;

  return (
    <svg
      viewBox="0 0 26 12"
      className="h-3 w-[26px] shrink-0"
      role="img"
      aria-label={label}
    >
      <rect
        x="0.6"
        y="0.6"
        width="21.8"
        height="10.8"
        rx="2.2"
        fill="none"
        stroke="var(--border)"
        strokeWidth="1.2"
      />
      {/* The terminal nub. Without it this is a progress bar; with it, it is
          a battery, and the difference is the whole reason the glyph reads
          as "how much is left" at this size. */}
      <rect x="23.4" y="4" width="2" height="4" rx="0.8" fill="var(--border)" />
      {f > 0 && (
        <rect
          x={2}
          y={2.6}
          width={Math.max(1.2, fill)}
          height={6.8}
          rx="1.1"
          fill="var(--z-orange)"
        />
      )}
    </svg>
  );
}

const WIDTH_INNER = 19;
