"use client";

/** One class's recent observations as an inline polyline — the board's
 * sparkline. Pure markup: `points` arrives already normalised from
 * `sparkPoints()` (oldest→newest, in a 0–100 box), and nothing here adds,
 * smooths or interpolates a point.
 *
 * `null` means fewer than two real observations, and renders as a dashed
 * baseline of the same visual weight — an honest "no history", never a
 * fabricated flat line.
 *
 * `dashed` draws a real curve in the reference family: same geometry, muted,
 * broken stroke. THE TWO DASHES ARE NOT THE SAME DASH. The baseline is a
 * long 5-4 rule across the middle of the box; a derived curve is a fine 3-3
 * trace of a series that exists. A flat derived series would otherwise be
 * pixel-identical to "no data", which is the one confusion a dashed line was
 * introduced to prevent. */
export function Sparkline({
  points,
  dashed = false,
}: {
  points: { x: number; y: number }[] | null;
  dashed?: boolean;
}) {
  if (points === null) {
    return (
      <span className="text-muted-foreground">
        <svg
          viewBox="-2 -6 104 112"
          preserveAspectRatio="none"
          className="h-7 w-24"
          role="img"
          aria-label="no price history"
        >
          <line
            x1="0"
            y1="50"
            x2="100"
            y2="50"
            stroke="currentColor"
            strokeWidth={2}
            strokeDasharray="5 4"
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      </span>
    );
  }

  return (
    <svg
      viewBox="-2 -6 104 112"
      preserveAspectRatio="none"
      className={`h-7 w-24 ${dashed ? "text-muted-foreground" : ""}`}
      role="img"
      aria-label={
        dashed
          ? "derived price history, from a reference card"
          : "recent price observations"
      }
    >
      <polyline
        points={points.map((p) => `${p.x},${p.y}`).join(" ")}
        fill="none"
        stroke={dashed ? "currentColor" : "var(--z-orange)"}
        strokeWidth={2}
        strokeDasharray={dashed ? "3 3" : undefined}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
