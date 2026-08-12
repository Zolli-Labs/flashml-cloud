"use client";

/** One class's recent observations as an inline polyline — the board's
 * sparkline. Pure markup: `points` arrives already normalised from
 * `sparkPoints()` (oldest→newest, in a 0–100 box), and nothing here adds,
 * smooths or interpolates a point.
 *
 * `null` means fewer than two real observations, and renders as a dashed
 * baseline of the same visual weight — an honest "no history", never a
 * fabricated flat line. */
export function Sparkline({
  points,
}: {
  points: { x: number; y: number }[] | null;
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
      className="h-7 w-24"
      role="img"
      aria-label="recent price observations"
    >
      <polyline
        points={points.map((p) => `${p.x},${p.y}`).join(" ")}
        fill="none"
        stroke="var(--z-orange)"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
