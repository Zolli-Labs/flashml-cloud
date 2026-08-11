"use client";

// The coordinator map.
//
// A live isometric map of the coordinator doing its job: leasing work out to
// four classes of machine, listening for their heartbeats, holding their
// checkpoints, and re-leasing when one disappears. The coordinator — not the
// hardware — is the subject, because it is the only component of Zolli that no
// competitor has.
//
// This component owns the `<svg>` and nothing else owns any of it. Every
// coordinate on screen comes from `lib/coordinator-map.ts`; no projection maths
// happens here or in any child.
//
// ## Painter's order
//
// Solids are drawn far to near, and the coordinator takes its turn in that
// order alongside the four sources rather than sitting in a fixed layer. It has
// to: the compact composition puts one source visually behind the coordinator
// and another in front of it, and a fixed core layer would let the far source
// punch through the coordinator's deck. Depth is the *ground* position, not the
// anchor — a taller machine is not a nearer one.
//
// Routes go under everything because they lie on the floor. Tokens and
// heartbeat rings go over everything because they are light, not matter.
//
// ## Interaction
//
// Hovering or keyboard-focusing a source lifts it, brightens its route and dims
// the other three. Focus and hover feed the same single piece of state, so a
// keyboard user gets exactly the map a mouse user gets.

import { useCallback, useMemo, useState } from "react";
import { useLandingMotion } from "@/components/landing/motion/LandingMotionProvider";
import {
  CORE_CELL,
  MAP_NODES,
  nodeAnchor,
  project,
  routeStateFor,
  type MapNodeKey,
  type MapNodeSpec,
  type MapPhase,
  type Viewport,
} from "@/lib/coordinator-map";
import { MapCore } from "./MapCore";
import { MapGrid } from "./MapGrid";
import { MapNode } from "./MapNode";
import { MapParticles } from "./MapParticles";
import { MapReadout } from "./MapReadout";
import { MapRoutes } from "./MapRoutes";

const MAP_DESCRIPTION =
  "Isometric map: four machine sources — everyday machines, owned infrastructure, "
  + "rented GPU and cloud or HPC — exchanging leases, heartbeats and checkpoints with "
  + "the Zolli coordinator at the centre. A machine fails and its work is re-leased elsewhere.";

type Drawable =
  | { readonly kind: "core"; readonly id: "core"; readonly ground: number; readonly depth: number }
  | {
      readonly kind: "node";
      readonly id: MapNodeKey;
      readonly ground: number;
      readonly depth: number;
      readonly spec: MapNodeSpec;
    };

export interface CoordinatorMapProps {
  readonly phase: MapPhase;
  readonly viewport: Viewport;
  /** Optional: called when a source is clicked or activated from the keyboard. */
  readonly onSelect?: (key: MapNodeKey) => void;
}

export function CoordinatorMap({ phase, viewport, onSelect }: CoordinatorMapProps) {
  const { reduced, documentVisible } = useLandingMotion();
  const [highlight, setHighlight] = useState<MapNodeKey | null>(null);
  const onHighlight = useCallback((key: MapNodeKey | null) => setHighlight(key), []);

  const drawables = useMemo<readonly Drawable[]>(() => {
    // A source's ground position, not its anchor: the anchor sits at half the
    // machine's height, so sorting on it would push a tall machine backwards.
    const grounds = MAP_NODES.map((spec) => ({
      spec,
      ground: nodeAnchor(spec, viewport).y + (spec.size.h / 2) * viewport.scale,
    }));
    const values = grounds.map((entry) => entry.ground);
    const nearest = Math.max(...values);
    const furthest = Math.min(...values);
    const span = nearest - furthest || 1;

    const items: Drawable[] = grounds.map(({ spec, ground }) => ({
      kind: "node",
      id: spec.key,
      ground,
      depth: (ground - furthest) / span,
      spec,
    }));
    items.push({
      kind: "core",
      id: "core",
      ground: project(CORE_CELL.x, 0, CORE_CELL.z, viewport).y,
      depth: 0,
    });

    return items.sort((a, b) => a.ground - b.ground);
  }, [viewport]);

  return (
    <figure
      data-coordinator-map={phase}
      className="m-0 overflow-hidden rounded-[10px] border border-[var(--z-border)] bg-[var(--z-surface)]"
    >
      <svg
        viewBox={`0 0 ${viewport.width} ${viewport.height}`}
        role="group"
        aria-label={MAP_DESCRIPTION}
        className="block h-auto w-full"
      >
        <MapGrid vp={viewport} />
        <MapRoutes vp={viewport} phase={phase} highlight={highlight} />

        {drawables.map((item) =>
          item.kind === "core" ? (
            <MapCore key="core" vp={viewport} />
          ) : (
            <MapNode
              key={item.id}
              spec={item.spec}
              vp={viewport}
              phase={phase}
              routeState={routeStateFor(item.id, phase)}
              depth={item.depth}
              lifted={highlight === item.id}
              muted={highlight !== null && highlight !== item.id}
              onHighlight={onHighlight}
              onSelect={onSelect}
            />
          ),
        )}

        <MapParticles
          vp={viewport}
          phase={phase}
          reduced={reduced}
          paused={!documentVisible}
        />
      </svg>

      <MapReadout phase={phase} />
    </figure>
  );
}
