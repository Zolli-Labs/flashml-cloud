// The ground plane.
//
// Without it the sources are objects floating in void and the elbow routes read
// as arbitrary kinks. With it they read as a map: the routes visibly follow the
// same two axes the floor does, which is the whole argument for elbow routing.
//
// The grid is drawn faintly and faded out at the frame edges so it never
// competes with anything on top of it, and it is emitted as a single `<path>`
// because `gridLines` already returns clipped, frame-safe segments and one path
// of many subpaths costs a fraction of the DOM that thirty `<line>`s do.

import { useId, useMemo } from "react";
import { corePoint, gridLines, type Viewport } from "@/lib/coordinator-map";
import { MAP_COLORS, r2 } from "./iso";

export function MapGrid({ vp }: { readonly vp: Viewport }) {
  const id = useId();
  const fadeId = `${id}-grid-fade`;
  const washId = `${id}-grid-wash`;
  const maskId = `${id}-grid-mask`;
  // Hovering a source re-renders the whole map. The grid never changes with it,
  // so its ~30 clipped segments are built once per frame geometry.
  const { width, height, scale, originX, originY } = vp;
  const path = useMemo(
    () => gridLines({ width, height, scale, originX, originY }).join(" "),
    [width, height, scale, originX, originY],
  );
  const core = corePoint(vp);
  const centreX = `${r2((core.x / vp.width) * 100)}%`;
  const centreY = `${r2((core.y / vp.height) * 100)}%`;

  return (
    <g data-map-layer="grid" aria-hidden="true">
      <defs>
        {/* Fades in the frame's own aspect ratio, so the grid dies out at the
            same rate on every edge of a 2:1 desktop frame and a portrait one. */}
        <radialGradient id={fadeId} cx={centreX} cy={centreY} r="62%">
          <stop offset="0%" stopColor="#ffffff" stopOpacity="1" />
          <stop offset="58%" stopColor="#ffffff" stopOpacity="0.62" />
          <stop offset="100%" stopColor="#ffffff" stopOpacity="0" />
        </radialGradient>
        <radialGradient id={washId} cx={centreX} cy={centreY} r="48%">
          <stop offset="0%" stopColor={MAP_COLORS.raised} stopOpacity="0.55" />
          <stop offset="100%" stopColor={MAP_COLORS.raised} stopOpacity="0" />
        </radialGradient>
        <mask id={maskId}>
          <rect x={0} y={0} width={vp.width} height={vp.height} fill={`url(#${fadeId})`} />
        </mask>
      </defs>

      {/* A neutral lift under the coordinator. The prototype put an orange glow
          here; orange now means the job's path and nothing else, so the ground
          gets lifted with the surface ramp instead of tinted. */}
      <rect x={0} y={0} width={vp.width} height={vp.height} fill={`url(#${washId})`} />

      <path
        d={path}
        fill="none"
        stroke={MAP_COLORS.line}
        strokeWidth={1}
        strokeDasharray="3 7"
        mask={`url(#${maskId})`}
      />
    </g>
  );
}
