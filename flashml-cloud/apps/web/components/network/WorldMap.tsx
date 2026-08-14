"use client";

import { useState } from "react";

import {
  COORDINATOR_HUB,
  MAP_VIEW,
  project,
  type MapMarker,
} from "@/lib/network/providers";
import { WORLD_LAND_PATH } from "@/lib/network/world-map-path";

/**
 * Where the network is, and what it is all connected to.
 *
 * PURE AND PRESENTATIONAL. Markers arrive already projected
 * (`mapMarkers()`); this file fetches nothing, sorts nothing and decides no
 * number. The only state it owns is which dot the pointer is over.
 *
 * WHY NOT THE LANDING PAGE'S MAP. `components/landing/coordinator-map/` is an
 * ISOMETRIC diagram — a camera, a ground plane, and four abstract machines
 * around a coordinator. It carries no geography at all: no latitude, no
 * longitude, no coastline, and `lib/coordinator-map.ts` projects a made-up
 * `x/y/z` cell grid rather than a globe. There is nothing in it a map of real
 * provider locations can reuse except the idea, so this is its own component
 * and that one is untouched.
 *
 * THE ARCS ARE THE POINT, not decoration. Every provider on this map holds a
 * lease from one coordinator, and the coordinator is a real place — Render's
 * `oregon` region, named by all six services in `render.yaml`. Drawing the
 * connection is drawing the system: a scatter of dots would say "we have
 * machines", and this says "we have machines and they are all talking to the
 * same thing".
 *
 * THE PALETTE IS WRITTEN OUT, deliberately. The graphite ramp
 * (`--z-bg`/`--z-surface`/`--z-border`) is defined only inside
 * `.marketing-dark` and the `.landing-surface-*` scopes in `app/globals.css`,
 * so a component that read those tokens would render as four invisible shapes
 * anywhere else — including inside a test's `renderToStaticMarkup`. The map is
 * a dark instrument wherever it is placed, so it carries the four graphite
 * values itself and reads only the accents (`--z-orange`, `--z-healthy`) that
 * `:root` actually defines. The values are the ramp's, verbatim.
 */
const MAP = {
  land: "#171a1d",
  coast: "#333b41",
  graticule: "#1c2124",
  hub: "#e8e4dc",
} as const;

export interface WorldMapProps {
  markers: MapMarker[];
  /** Override the crop. The detail page hands in a tight box around one
   * provider; the network page takes the default whole-world view. */
  view?: { x: number; y: number; width: number; height: number };
  /** Draw the connection to the coordinator. Off for the detail page's
   * mini-map, where a single arc leaving the frame is a stray line. */
  arcs?: boolean;
  /** The marker this map is ABOUT — drawn emphasised, with rings. The detail
   * page's mini-map passes the provider whose page it is.
   *
   * Emphasis only: it does not open the tooltip. A tooltip pinned open sits
   * over the very dot it describes and turns a map into a callout, which is
   * what the detail page's mini-map looked like the first time it was
   * rendered. The tooltip is a hover affordance and nothing else. */
  focusId?: string;
  /** Larger rings on the focused marker — for a tight crop, where the
   * default emphasis is lost against a dot at the same scale. */
  emphasiseFocus?: boolean;
  className?: string;
}

export function WorldMap({
  markers,
  view = MAP_VIEW,
  arcs = true,
  focusId,
  emphasiseFocus = false,
  className,
}: WorldMapProps) {
  const [hoverId, setHoverId] = useState<string | null>(null);
  // Emphasis follows the hover OR the focus; the tooltip follows the hover
  // alone — see `focusId`.
  const activeId = hoverId ?? focusId ?? null;
  const active = markers.find((m) => m.id === hoverId) ?? null;

  const hub = project(COORDINATOR_HUB.lat, COORDINATOR_HUB.lon);

  // NOTHING IS SCALED BY THE CROP, and the first version of this file got
  // that exactly backwards. It multiplied every radius and stroke by
  // `view.width / MAP_VIEW.width`, on the reasoning that a zoomed-in map
  // should shrink its marks to keep them the same apparent size. That holds
  // only if both maps are drawn at the same pixel width — and they are not:
  // the mini-map is a third of the network map's width, so the correction
  // ran the wrong way twice over and produced a 1.4-pixel dot that could not
  // be found on the card it was the subject of.
  //
  // Marks are therefore plain user units, so a tighter crop makes them
  // slightly larger on screen, which is what zooming in should do. Lines
  // carry `vector-effect="non-scaling-stroke"` and their widths are PIXELS,
  // so a hairline is a hairline at every crop and every size.

  return (
    <div className={`relative ${className ?? ""}`}>
      <svg
        viewBox={`${view.x} ${view.y} ${view.width} ${view.height}`}
        className="block h-full w-full"
        role="img"
        aria-label={`${markers.length} providers on a world map, each connected to the coordinator`}
      >
        {/* The graticule: 30° of longitude, 20° of latitude. Just enough to
            say the plane is a globe, faint enough that no line competes with
            a coastline. */}
        {/* `vector-effect` is NOT an inherited property, so it goes on each
            line rather than on the group — on the group it silently does
            nothing and the graticule thickens with the crop. */}
        <g stroke={MAP.graticule} strokeWidth={1} fill="none">
          {[-150, -120, -90, -60, -30, 0, 30, 60, 90, 120, 150].map((lon) => {
            const { x } = project(0, lon);
            return (
              <line
                key={`m${lon}`}
                x1={x}
                y1={0}
                x2={x}
                y2={500}
                vectorEffect="non-scaling-stroke"
              />
            );
          })}
          {[60, 40, 20, 0, -20, -40].map((lat) => {
            const { y } = project(lat, 0);
            return (
              <line
                key={`p${lat}`}
                x1={0}
                y1={y}
                x2={1000}
                y2={y}
                vectorEffect="non-scaling-stroke"
              />
            );
          })}
        </g>

        <path
          d={WORLD_LAND_PATH}
          fill={MAP.land}
          stroke={MAP.coast}
          strokeWidth={1}
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />

        {arcs && (
          <g fill="none" strokeLinecap="round">
            {markers.flatMap((m) =>
              arcsTo(m.x, m.y, hub.x, hub.y).map((d, i) => (
                <path
                  key={`arc-${m.id}-${i}`}
                  d={d}
                  stroke="var(--z-orange)"
                  strokeWidth={m.id === activeId ? 1.8 : 1}
                  vectorEffect="non-scaling-stroke"
                  opacity={m.id === activeId ? 0.85 : m.online ? 0.28 : 0.12}
                />
              ))
            )}
          </g>
        )}

        {markers.map((m) => {
          const on = m.id === activeId;
          const colour = m.online ? "var(--z-orange)" : "#6d706f";
          return (
            <g
              key={m.id}
              onPointerEnter={() => setHoverId(m.id)}
              onPointerLeave={() => setHoverId((at) => (at === m.id ? null : at))}
              style={{ cursor: "pointer" }}
            >
              {/* An own machine wears a ring; the fill still says online.
                  Two facts, two channels — a fourth colour for "mine and
                  offline" would need a legend nobody reads. */}
              {(m.own || on || emphasiseFocus) && (
                <circle
                  cx={m.x}
                  cy={m.y}
                  r={emphasiseFocus && on ? 11 : on ? 7.5 : 6}
                  fill="none"
                  stroke={colour}
                  strokeWidth={1.2}
                  vectorEffect="non-scaling-stroke"
                  opacity={on ? 0.7 : 0.42}
                />
              )}
              <circle
                cx={m.x}
                cy={m.y}
                r={on ? 3.6 : 2.9}
                fill={colour}
                stroke="#0b0d0e"
                strokeWidth={1}
                vectorEffect="non-scaling-stroke"
              />
              {/* A generous invisible target: the visible dot is under 3
                  units across and a pointer cannot reliably find it. */}
              <circle cx={m.x} cy={m.y} r={11} fill="transparent" />
            </g>
          );
        })}

        {/* The coordinator, drawn LAST so it is on top. A diamond, not a dot:
            it is not one more provider, and the shape says so before the
            colour does. Painting it before the markers hid it completely
            behind whichever provider happened to sit nearest Oregon — which
            is exactly where a host who runs a machine near the coordinator
            puts theirs. */}
        {arcs && (
          <path
            d="M0 -5.4 5.4 0 0 5.4 -5.4 0Z"
            transform={`translate(${hub.x} ${hub.y})`}
            fill={MAP.hub}
            stroke="#0b0d0e"
            strokeWidth={1.2}
            vectorEffect="non-scaling-stroke"
          >
            <title>{COORDINATOR_HUB.label}</title>
          </path>
        )}
      </svg>

      {active && (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full pb-2"
          style={{
            left: `${((active.x - view.x) / view.width) * 100}%`,
            top: `${((active.y - view.y) / view.height) * 100}%`,
          }}
        >
          <div className="min-w-[9rem] rounded-md border border-[#333b41] bg-[#0e1113]/95 px-2.5 py-1.5 shadow-lg">
            <p className="truncate font-mono text-[11px] font-medium text-[#f3f1ec]">
              {active.label}
            </p>
            <p className="mt-0.5 text-[10px] text-[#a5a39e]">
              {active.place ?? "location not stated"}
            </p>
            <p className="mt-1 font-mono text-[10px] tabular-nums text-[var(--z-orange)]">
              {active.activeLeases}{" "}
              <span className="text-[#a5a39e]">
                active {active.activeLeases === 1 ? "lease" : "leases"}
              </span>
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * The connection from a provider to the hub — ONE arc, or two when the short
 * way round the world crosses the map's edge.
 *
 * WHY THIS IS NOT ONE PATH. On a flat equirectangular map, Sydney is at the
 * right edge and Oregon is at the left, so a straight chord between them
 * runs the entire width of the map, over Africa and the Atlantic. That is
 * the LONG way: the real link crosses the Pacific, which on this projection
 * leaves one edge and re-enters the other. Drawn as a single chord it is not
 * a stylisation, it is a wrong picture of where the traffic goes — and it
 * was the first thing visible in the render.
 *
 * So when the hub is more than half a world away in x, the same curve is
 * drawn twice: once to a hub shifted a full map width toward the provider,
 * and once from a provider shifted a full map width toward the hub. Both are
 * clipped by the viewBox, and because the second is the first translated by
 * exactly one map width, the two halves meet at the seam with no kink.
 */
function arcsTo(x1: number, y1: number, x2: number, y2: number): string[] {
  const dx = x2 - x1;
  if (Math.abs(dx) <= MAP_WIDTH / 2) return [arcTo(x1, y1, x2, y2)];
  // The hub is closer the other way round. `shift` is the direction the hub
  // has to move to become the near one.
  const shift = dx > 0 ? -MAP_WIDTH : MAP_WIDTH;
  return [
    arcTo(x1, y1, x2 + shift, y2),
    arcTo(x1 - shift, y1, x2, y2),
  ];
}

const MAP_WIDTH = 1000;

/**
 * A quadratic arc, bowed perpendicular to the chord.
 *
 * The bow is proportional to the chord's own length, so a Frankfurt→Oregon
 * arc and a Toronto→Oregon arc are the same shape at different sizes rather
 * than one flat line and one balloon. It always bows the same way round the
 * chord, which is what stops two providers in one city from drawing two
 * arcs on top of each other.
 */
function arcTo(x1: number, y1: number, x2: number, y2: number): string {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len = Math.hypot(dx, dy) || 1;
  const bow = Math.min(len * 0.22, 90);
  const mx = (x1 + x2) / 2;
  const my = (y1 + y2) / 2;
  // Perpendicular, normalised. `-dy, dx` bows one consistent way.
  const cx = mx + (-dy / len) * bow;
  const cy = my + (dx / len) * bow;
  return `M${r(x1)} ${r(y1)}Q${r(cx)} ${r(cy)} ${r(x2)} ${r(y2)}`;
}

const r = (n: number) => Math.round(n * 10) / 10;
