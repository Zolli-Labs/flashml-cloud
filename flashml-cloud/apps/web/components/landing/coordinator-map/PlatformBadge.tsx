// One circular platform badge on a source.
//
// Badges are what let the map say "this runs on your machines" without a
// sentence of prose. They resolve icon-first and fall back to type, because
// Windows, Microsoft and AWS were removed from simple-icons at those companies'
// request — see the header of `lib/coordinator-map-icons.ts`. A missing mark is
// a deliberate state here, not a bug to route around.

import { PLATFORM_ICONS, PLATFORM_LABELS } from "@/lib/coordinator-map-icons";
import { MAP_COLORS, MAP_MONO, r2 } from "./iso";

/** simple-icons draws in a 24×24 box; 13px of glyph inside a 14px-radius disc
 * is the proportion the approved prototype settled on. */
const ICON_BOX = 24;
const ICON_SIZE = 13;

export interface PlatformBadgeProps {
  /** A badge slug from `MapNodeSpec.badges`. */
  readonly badge: string;
  readonly x: number;
  readonly y: number;
  readonly r?: number;
  /** Ink for the mark. Lifts with the rest of the node on hover and focus. */
  readonly tint?: string;
}

export function PlatformBadge({
  badge,
  x,
  y,
  r = 14,
  tint = MAP_COLORS.dim,
}: PlatformBadgeProps) {
  // `MAP_NODES` writes the AWS badge as "AWS" while the icon and label maps are
  // keyed by lowercase slug. Normalising here rather than in either module
  // keeps both usable with whatever casing a spec happens to carry.
  const slug = badge.toLowerCase();
  const icon = PLATFORM_ICONS[slug];
  const label = PLATFORM_LABELS[slug] ?? badge.toUpperCase();
  const scale = ICON_SIZE / ICON_BOX;

  return (
    <g data-platform={slug}>
      <circle
        cx={r2(x)}
        cy={r2(y)}
        r={r}
        fill={MAP_COLORS.bg}
        stroke={MAP_COLORS.lineStrong}
        strokeWidth={1}
      />
      {icon ? (
        <g
          transform={`translate(${r2(x - ICON_SIZE / 2)} ${r2(y - ICON_SIZE / 2)}) scale(${r2(scale)})`}
        >
          <path d={icon} fill={tint} />
        </g>
      ) : (
        <text
          x={r2(x)}
          y={r2(y + 3.4)}
          fill={tint}
          fontSize={8.5}
          fontFamily={MAP_MONO}
          letterSpacing="0.04em"
          textAnchor="middle"
        >
          {label}
        </text>
      )}
    </g>
  );
}
