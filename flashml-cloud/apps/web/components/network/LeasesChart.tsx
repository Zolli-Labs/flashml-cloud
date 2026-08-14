"use client";

import type { LeaseDay } from "@/lib/network/api";

/**
 * Thirty days of lease activity for one provider.
 *
 * TWO SERIES, BECAUSE THE REPO'S FOURTH HARD RULE IS THAT ATTEMPTED WORK AND
 * ACCEPTED WORK ARE DIFFERENT THINGS ("distinguish attempted from accepted
 * everywhere money, credits, or metrics are involved"). `resolved` is what
 * this machine finished; `accepted` is what was taken and paid for. On a good
 * provider the two lines sit on top of each other and the chart says so at a
 * glance; where they part is the whole story of the page, and one line would
 * hide it.
 *
 * DOTS ON EVERY REAL DAY. A day the API sent is a day somebody measured, and
 * the marker is what distinguishes a measured zero from a line passing
 * through zero on its way somewhere. Days the API did not send are not drawn
 * at all — the axis spans the data, never a padded month.
 *
 * INLINE SVG, `preserveAspectRatio="none"`, every stroke with
 * `vector-effect="non-scaling-stroke"` — the same construction
 * `components/market/PriceHistory.tsx` documents, so a wide column stretches
 * the plot without thickening a line, and both axes are HTML so no arithmetic
 * here assumes a font size.
 */
export function LeasesChart({ days }: { days: LeaseDay[] }) {
  if (days.length < 2) {
    return (
      <p className="mt-2 text-sm text-muted-foreground">
        {days.length === 0
          ? "No lease activity recorded for this provider yet."
          : "One day of activity so far — a line needs two."}
      </p>
    );
  }

  const peak = Math.max(
    1,
    ...days.map((d) => Math.max(d.resolved, d.accepted))
  );
  const step = 100 / (days.length - 1);
  const at = (value: number, i: number) => ({
    x: i * step,
    y: 100 - (value / peak) * 100,
  });
  const resolved = days.map((d, i) => at(d.resolved, i));
  const accepted = days.map((d, i) => at(d.accepted, i));
  const line = (pts: { x: number; y: number }[]) =>
    pts.map((p) => `${round(p.x)},${round(p.y)}`).join(" ");
  const area = `M 0,100 ${resolved
    .map((p) => `L ${round(p.x)},${round(p.y)}`)
    .join(" ")} L 100,100 Z`;

  return (
    <figure className="mt-3">
      <div className="grid grid-cols-[2.5rem_minmax(0,1fr)] gap-x-2 pr-1">
        {/* Three levels on the value axis. Integers, because a count of
            leases has no halves. */}
        <div className="relative">
          <div className="absolute inset-x-0 inset-y-2">
            {[peak, Math.round(peak / 2), 0].map((value, i) => (
              <span
                key={i}
                className="meta absolute right-0 -translate-y-1/2 tabular-nums"
                style={{ top: `${i * 50}%` }}
              >
                {value}
              </span>
            ))}
          </div>
        </div>

        <div className="relative h-44 min-w-0">
          <div className="absolute inset-x-0 inset-y-2">
            <svg
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
              className="h-full w-full overflow-visible"
              role="img"
              aria-label="leases resolved and accepted per day over the last 30 days"
            >
              {[0, 50, 100].map((y) => (
                <line
                  key={y}
                  x1="0"
                  y1={y}
                  x2="100"
                  y2={y}
                  stroke="var(--border)"
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
              ))}

              <path d={area} fill="var(--z-orange)" className="opacity-10" />

              <polyline
                points={line(accepted)}
                fill="none"
                stroke="currentColor"
                className="text-muted-foreground"
                strokeWidth={1.5}
                strokeDasharray="4 3"
                strokeLinecap="round"
                strokeLinejoin="round"
                vectorEffect="non-scaling-stroke"
              />
              <polyline
                points={line(resolved)}
                fill="none"
                stroke="var(--z-orange)"
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                vectorEffect="non-scaling-stroke"
              />
            </svg>

            {/* Markers as positioned elements rather than SVG circles: in a
                `preserveAspectRatio="none"` box a circle stretches into an
                ellipse, and a squashed dot on a stretched chart is the
                giveaway that the plot is not drawn to scale. */}
            {resolved.map((p, i) => (
              <span
                key={days[i].date}
                aria-hidden
                title={`${days[i].date}: ${days[i].resolved} resolved, ${days[i].accepted} accepted`}
                className="absolute h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[var(--z-orange)]"
                style={{ left: `${p.x}%`, top: `${p.y}%` }}
              />
            ))}
          </div>
        </div>

        <div />
        <div className="mt-1 flex justify-between gap-2">
          {axisLabels(days).map((label, i) => (
            <span key={i} className="meta tabular-nums">
              {label}
            </span>
          ))}
        </div>
      </div>

      <figcaption className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="h-0.5 w-4 rounded bg-[var(--z-orange)]"
          />
          <span className="meta">resolved</span>
        </span>
        <span className="flex items-center gap-1.5">
          <span
            aria-hidden
            className="h-0.5 w-4 rounded border-t border-dashed border-muted-foreground"
          />
          <span className="meta">accepted</span>
        </span>
      </figcaption>
    </figure>
  );
}

/** Four dates, taken from days that exist rather than from a generated tick
 * sequence — a label under a position where nothing was measured is a label
 * for a measurement that does not exist. Same rule `PriceHistory` follows. */
function axisLabels(days: LeaseDay[]): string[] {
  const wanted = Math.min(4, days.length);
  const picked = new Set<number>();
  for (let i = 0; i < wanted; i += 1) {
    picked.add(Math.round((i * (days.length - 1)) / (wanted - 1 || 1)));
  }
  return [...picked]
    .sort((a, b) => a - b)
    .map((i) => shortDate(days[i].date));
}

/** `Aug 3`. An unparseable date is quoted back rather than rendered as
 * "Invalid Date". */
function shortDate(iso: string): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  return new Date(ms).toLocaleDateString([], {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

const round = (n: number) => Math.round(n * 100) / 100;
