/** The FlashML mark.
 *
 * Three lanes of work. The middle one breaks and resumes, offset, on the
 * other side of the gap — a task whose machine went away and was picked up
 * elsewhere. That is the entire product in four rectangles, and it beats the
 * lightning-bolt-in-a-rounded-square that every generated app ships.
 *
 * Deliberately geometric and drawn on an 8px grid: it stays legible at 16px
 * in a browser tab and does not need a second "simplified" version. The one
 * asymmetry (the gap) is the whole idea, so it must survive scaling — hence
 * a gap wider than the bar height rather than a hairline.
 */
export function Mark({
  size = 24,
  className = "",
}: {
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      aria-hidden
    >
      {/* lane 1: uninterrupted */}
      <rect x="2" y="4" width="20" height="3.5" rx="1.75" fill="currentColor" />
      {/* lane 2: claimed, lost at the gap, resumed after it */}
      <rect x="2" y="10.25" width="8" height="3.5" rx="1.75" fill="currentColor" opacity="0.55" />
      <rect x="14" y="10.25" width="8" height="3.5" rx="1.75" fill="currentColor" />
      {/* lane 3: uninterrupted, shorter, so the block is not a solid square */}
      <rect x="2" y="16.5" width="14" height="3.5" rx="1.75" fill="currentColor" />
    </svg>
  );
}

/** Mark plus wordmark. `ML` carries the accent so the lockup has one
 * coloured element rather than two competing ones. */
export function Wordmark({
  size = 22,
  className = "",
}: {
  size?: number;
  className?: string;
}) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <Mark size={size} className="text-foreground" />
      <span className="font-mono text-sm font-bold tracking-tight">
        Flash<span className="text-primary">ML</span>
      </span>
    </span>
  );
}
