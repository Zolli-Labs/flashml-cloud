// The four routes between the coordinator and its sources.
//
// Every route is drawn from the core outward, which is what makes `elbowPath`
// hand each of them a different axis out of the middle: four routes, four
// directions, so a red failed route and a live orange one can never collapse
// onto the same line.
//
// Only one route is ever orange. `routeStateFor` decides which, and this
// component does nothing but dress its answer.

import {
  MAP_NODES,
  corePoint,
  elbowPath,
  nodeAnchor,
  routeStateFor,
  type MapNodeKey,
  type MapPhase,
  type RouteState,
  type Viewport,
} from "@/lib/coordinator-map";
import { MAP_COLORS } from "./iso";

interface RouteStyle {
  readonly stroke: string;
  readonly width: number;
  readonly dash?: string;
  readonly opacity: number;
}

/**
 * `idle` is deliberately quiet: a connected source with no work on it is part
 * of the fabric, not part of the story. `dimmed` is the victim's route after
 * the work has moved on — kept visible as history, kept out of the way of the
 * live one.
 */
const ROUTE_STYLES: Readonly<Record<RouteState, RouteStyle>> = {
  // Idle routes use the strong border token, not the grid's. The grid is
  // already drawn in `--z-border`, and a route the same weight as the floor it
  // sits on disappears — which reads as three sources that are not connected
  // at all, the opposite of what the map claims.
  idle: { stroke: MAP_COLORS.lineStrong, width: 1.4, dash: "4 6", opacity: 0.72 },
  active: { stroke: MAP_COLORS.job, width: 1.9, opacity: 1 },
  failed: { stroke: MAP_COLORS.failed, width: 1.8, dash: "5 5", opacity: 0.92 },
  dimmed: { stroke: MAP_COLORS.lineStrong, width: 1.2, dash: "3 9", opacity: 0.3 },
};

export interface MapRoutesProps {
  readonly vp: Viewport;
  readonly phase: MapPhase;
  /** The source under the pointer or holding focus, if any. */
  readonly highlight?: MapNodeKey | null;
}

export function MapRoutes({ vp, phase, highlight = null }: MapRoutesProps) {
  const core = corePoint(vp);

  return (
    <g data-map-layer="routes" aria-hidden="true" fill="none" strokeLinejoin="round" strokeLinecap="round">
      {MAP_NODES.map((spec) => {
        const state = routeStateFor(spec.key, phase);
        const style = ROUTE_STYLES[state];
        const lit = highlight === spec.key;
        const muted = highlight !== null && !lit;

        return (
          <path
            key={spec.key}
            data-route={spec.key}
            data-route-state={state}
            d={elbowPath(core, nodeAnchor(spec, vp))}
            // Highlighting lifts an idle route to secondary text, not to
            // cream: the brightest line on the map has to stay the one
            // carrying the job, or hovering a bystander rewrites the story.
            stroke={lit && state === "idle" ? MAP_COLORS.dim : style.stroke}
            strokeWidth={lit ? style.width + 0.4 : style.width}
            strokeDasharray={style.dash}
            opacity={muted ? style.opacity * 0.35 : lit ? Math.min(0.9, style.opacity + 0.18) : style.opacity}
          />
        );
      })}
    </g>
  );
}
