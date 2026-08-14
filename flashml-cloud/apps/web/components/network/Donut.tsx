"use client";

/**
 * One resource's committed share, as a ring.
 *
 * INLINE SVG, NO CHART LIBRARY — the same call `components/market/
 * PriceHistory.tsx` documents for its own plot. A donut is one circle with a
 * dash pattern; a charting dependency would arrive with an axis engine, a
 * tooltip layer and a theme of its own, and this page needs none of them.
 *
 * THE CENTRE LABEL IS TWO NUMBERS, NOT A PERCENTAGE. `197 GPU / 425 GPU` says
 * what the ring is a picture of; `46%` says only how full the picture looks. A
 * reader deciding whether the network can take their job needs the second
 * number, and a percentage throws it away.
 *
 * A TOTAL OF ZERO DRAWS NO ARC. Not a zero-length one — none. Nothing is
 * committed when there is nothing to commit, and a hairline at twelve
 * o'clock reads as a very small amount rather than as an absence. The centre
 * says `0` because the API said 0, which is a measurement; the ring stays
 * muted because there is no proportion to draw.
 */
export function Donut({
  title,
  used,
  total,
  usedText,
  totalText,
  pctLabel = "committed",
  size = 116,
}: {
  title: string;
  used: number;
  total: number;
  usedText: string;
  totalText: string;
  /** What the filled arc IS, in one or two words, for the footnote under the
   * ring. The network row's arc is capacity `committed`; a provider page's
   * arc is that machine's share `of the network`. Same drawing, different
   * sentence — and a footnote that said "committed" under both would be
   * wrong on one of them. */
  pctLabel?: string;
  size?: number;
}) {
  const frac = total > 0 ? Math.min(1, Math.max(0, used / total)) : 0;
  const dash = CIRCUMFERENCE * frac;
  const pct = total > 0 ? Math.round(frac * 100) : null;

  return (
    <figure className="flex min-w-0 flex-col items-center">
      <figcaption className="label-caps mb-2.5">{title}</figcaption>

      <div className="relative" style={{ width: size, height: size }}>
        <svg
          viewBox="0 0 100 100"
          className="h-full w-full -rotate-90"
          role="img"
          aria-label={`${title}: ${usedText} committed of ${totalText}`}
        >
          <circle
            cx="50"
            cy="50"
            r={R}
            fill="none"
            stroke="var(--border)"
            strokeWidth={STROKE}
          />
          {total > 0 && frac > 0 && (
            <circle
              cx="50"
              cy="50"
              r={R}
              fill="none"
              stroke="var(--z-orange)"
              strokeWidth={STROKE}
              strokeLinecap="round"
              strokeDasharray={`${dash.toFixed(2)} ${CIRCUMFERENCE.toFixed(2)}`}
            />
          )}
        </svg>

        <div className="absolute inset-0 flex flex-col items-center justify-center px-2 text-center">
          <span className="metric-value text-[0.9rem] leading-tight font-medium text-foreground">
            {usedText}
          </span>
          <span className="metric-value mt-0.5 text-[0.68rem] leading-tight text-muted-foreground">
            / {totalText}
          </span>
        </div>
      </div>

      {/* The percentage is a footnote under the two real numbers, not a
          replacement for them. Absent rather than "0%" when there is no
          denominator — see the note above. */}
      <p className="meta mt-2">
        {pct === null ? "nothing reported" : `${pct}% ${pctLabel}`}
      </p>
    </figure>
  );
}

const R = 42;
const STROKE = 9;
const CIRCUMFERENCE = 2 * Math.PI * R;
