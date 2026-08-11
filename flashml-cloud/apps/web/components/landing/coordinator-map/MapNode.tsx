"use client";

// One compute source: its silhouette, its platform badges, its chip and its
// name.
//
// The silhouettes are the single biggest upgrade over the prototype, which drew
// four identical cubes and left the label doing all the recognising. At map
// scale the shape has to carry the meaning: a cluster of small volumes is a
// room full of laptops, a ruled tower is a rack, two squat slabs with fans are
// accelerator cards, a stepped group of uniform blocks is a data centre. Each
// is built from `spec.size`, so changing a source's footprint in the geometry
// module moves its silhouette with it.
//
// Furniture placement follows the prototype's two hard-won rules, and they are
// not stylistic:
//
//   1. A source *above* the coordinator wears its name, badges and chip above
//      its box; a source *below* wears them underneath. Either way the
//      furniture leans away from the middle of the frame, where the
//      coordinator and its caption live.
//   2. The one source sitting almost directly under the coordinator would drop
//      its name straight onto the coordinator's own caption, so that source
//      alone wears its name below its chip instead of above its box.
//
// Both compositions rely on this. The compact stack puts a source close under
// the core on purpose, so rule 2 is load-bearing there, not a desktop quirk.

import { useState, type KeyboardEvent } from "react";
import {
  corePoint,
  nodeAnchor,
  type MapNodeKey,
  type MapNodeSpec,
  type MapPhase,
  type RouteState,
  type Viewport,
} from "@/lib/coordinator-map";
import {
  IsoBox,
  IsoFaceDisc,
  IsoFaceLine,
  MAP_COLORS,
  MAP_MONO,
  clampCentre,
  isoAxes,
  isoOffset,
  monoWidth,
  r2,
  type IsoVolume,
} from "./iso";
import { PlatformBadge } from "./PlatformBadge";

/** Horizontal pitch between badge centres, and the padding on the invisible
 * rectangle that makes a node hoverable and focusable across its gaps. */
const BADGE_PITCH = 32;
const BADGE_DIAMETER = 28;
const HIT_PADDING = 12;

/** A source whose front face lands within this many pixels of the
 * coordinator's screen centre is "under the core" — see rule 2 above. */
const UNDER_CORE_PX = 70;

type Size = MapNodeSpec["size"];

/** Painter's order on the ground plane: increasing `x + z` runs from the far
 * corner to the near one, which is exactly back-to-front on screen. */
function farToNear(volumes: readonly IsoVolume[]): readonly IsoVolume[] {
  return [...volumes].sort((a, b) => a.x + a.z - (b.x + b.z));
}

/** Many small low machines, arranged as four units with a lane between them. */
function clusterVolumes({ w, h, d }: Size): readonly IsoVolume[] {
  const lane = 0.3;
  const uw = (w - lane) / 2;
  const ud = (d - lane) / 2;
  const heights = [
    [0.56, 0.4],
    [1.0, 0.68],
  ];
  const volumes: IsoVolume[] = [];
  for (let zi = 0; zi < 2; zi++) {
    for (let xi = 0; xi < 2; xi++) {
      volumes.push({
        x: -w / 2 + xi * (uw + lane),
        y: 0,
        z: -d / 2 + zi * (ud + lane),
        w: uw,
        h: h * heights[zi][xi],
        d: ud,
      });
    }
  }
  return farToNear(volumes);
}

/** One tall volume on a plinth, ruled into units. */
function rackVolumes({ w, h, d }: Size): { readonly volumes: readonly IsoVolume[]; readonly tower: IsoVolume } {
  const plinth: IsoVolume = { x: -w / 2, y: 0, z: -d / 2, w, h: h * 0.1, d };
  const tower: IsoVolume = {
    x: -w * 0.34,
    y: plinth.h,
    z: -d * 0.34,
    w: w * 0.68,
    h: h * 0.9,
    d: d * 0.68,
  };
  return { volumes: [plinth, tower], tower };
}

/** Two squat slabs, side by side along the ground x-axis. */
function cardVolumes({ w, h, d }: Size): readonly IsoVolume[] {
  const gap = 0.16;
  const cw = (w - gap) / 2;
  return [0, 1].map((index) => ({
    x: -w / 2 + index * (cw + gap),
    y: 0,
    z: -d / 2,
    w: cw,
    h: h * 0.7,
    d,
  }));
}

/** Uniform blocks stepping up towards the viewer. */
function steppedVolumes({ w, h, d }: Size): readonly IsoVolume[] {
  const gapX = w * 0.06;
  const gapZ = d * 0.06;
  const bw = (w - gapX) / 2;
  const bd = (d - gapZ) / 2;
  const heights = [
    [0.42, 0.66],
    [0.66, 1.0],
  ];
  const volumes: IsoVolume[] = [];
  for (let zi = 0; zi < 2; zi++) {
    for (let xi = 0; xi < 2; xi++) {
      volumes.push({
        x: -w / 2 + xi * (bw + gapX),
        y: 0,
        z: -d / 2 + zi * (bd + gapZ),
        w: bw,
        h: h * heights[zi][xi],
        d: bd,
      });
    }
  }
  return farToNear(volumes);
}

function Silhouette({ spec, vp }: { readonly spec: MapNodeSpec; readonly vp: Viewport }) {
  const { size, silhouette } = spec;

  if (silhouette === "rack") {
    const { volumes, tower } = rackVolumes(size);
    const inset = tower.w * 0.14;
    const units = 5;
    return (
      <g>
        {volumes.map((volume, index) => (
          <IsoBox key={`rack-${index}`} vp={vp} {...volume} />
        ))}
        {Array.from({ length: units }, (_, index) => {
          const y = tower.y + ((index + 1) * tower.h) / (units + 1);
          return (
            <IsoFaceLine
              key={`rule-${index}`}
              vp={vp}
              from={[tower.x + inset, y, tower.z + tower.d]}
              to={[tower.x + tower.w - inset, y, tower.z + tower.d]}
              opacity={0.6}
            />
          );
        })}
      </g>
    );
  }

  if (silhouette === "cards") {
    const volumes = cardVolumes(size);
    const radius = Math.min(volumes[0].w, volumes[0].h) * 0.26;
    return (
      <g>
        {volumes.map((volume, index) => (
          <g key={`card-${index}`}>
            <IsoBox vp={vp} {...volume} />
            {[0.3, 0.7].map((fraction) => (
              <IsoFaceDisc
                key={`fan-${index}-${fraction}`}
                vp={vp}
                at={[volume.x + volume.w * fraction, volume.h * 0.5, volume.z + volume.d]}
                radius={radius}
                opacity={0.7}
              />
            ))}
          </g>
        ))}
      </g>
    );
  }

  const volumes = silhouette === "cluster" ? clusterVolumes(size) : steppedVolumes(size);
  return (
    <g>
      {volumes.map((volume, index) => (
        <IsoBox key={`${silhouette}-${index}`} vp={vp} {...volume} />
      ))}
    </g>
  );
}

function Chip({ x, y, text }: { readonly x: number; readonly y: number; readonly text: string }) {
  const width = text.length * 6.9 + 18;
  return (
    <g>
      <rect
        x={r2(x - width / 2)}
        y={r2(y - 12)}
        width={r2(width)}
        height={21}
        rx={2}
        fill={MAP_COLORS.bg}
        stroke={MAP_COLORS.lineStrong}
        strokeWidth={1}
      />
      <text
        x={r2(x)}
        y={r2(y + 2.5)}
        fill={MAP_COLORS.dim}
        fontSize={10.5}
        fontFamily={MAP_MONO}
        letterSpacing="0.09em"
        textAnchor="middle"
      >
        {text}
      </text>
    </g>
  );
}

function FailureMark({ x, y, opacity }: { readonly x: number; readonly y: number; readonly opacity: number }) {
  return (
    <g opacity={opacity} data-mark="failed">
      <circle cx={r2(x)} cy={r2(y)} r={13} fill={MAP_COLORS.bg} stroke={MAP_COLORS.failed} strokeWidth={1.4} />
      <path
        d={`M${r2(x - 5)},${r2(y - 5)} L${r2(x + 5)},${r2(y + 5)} M${r2(x + 5)},${r2(y - 5)} L${r2(x - 5)},${r2(y + 5)}`}
        stroke={MAP_COLORS.failed}
        strokeWidth={1.6}
        strokeLinecap="round"
      />
    </g>
  );
}

function AcceptedMark({ x, y }: { readonly x: number; readonly y: number }) {
  return (
    <g data-mark="accepted">
      <circle cx={r2(x)} cy={r2(y)} r={13} fill={MAP_COLORS.bg} stroke={MAP_COLORS.ok} strokeWidth={1.4} />
      <path
        d={`M${r2(x - 5)},${r2(y)} L${r2(x - 1)},${r2(y + 4.5)} L${r2(x + 5.5)},${r2(y - 4.5)}`}
        fill="none"
        stroke={MAP_COLORS.ok}
        strokeWidth={1.8}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </g>
  );
}

export interface MapNodeProps {
  readonly spec: MapNodeSpec;
  readonly vp: Viewport;
  readonly phase: MapPhase;
  readonly routeState: RouteState;
  /** 0 for the source furthest back, 1 for the one nearest the viewer. */
  readonly depth: number;
  /** True when this source is the one being hovered or focused. */
  readonly lifted: boolean;
  /** True when some *other* source is being hovered or focused. */
  readonly muted: boolean;
  readonly onHighlight: (key: MapNodeKey | null) => void;
  readonly onSelect?: (key: MapNodeKey) => void;
}

export function MapNode({
  spec,
  vp,
  phase,
  routeState,
  depth,
  lifted,
  muted,
  onHighlight,
  onSelect,
}: MapNodeProps) {
  const [focused, setFocused] = useState(false);
  const axes = isoAxes(vp);
  const anchor = nodeAnchor(spec, vp);
  const core = corePoint(vp);
  const { w, h, d } = spec.size;

  // Screen positions of the box's top, its near face and its far face, all
  // derived from the anchor. The compact composition's cells are not exported,
  // so the anchor is the only honest starting point.
  const top = { x: anchor.x, y: anchor.y + isoOffset(0, h / 2, 0, vp).y };
  const front = { x: anchor.x + isoOffset(0, 0, d / 2, vp).x, y: anchor.y + isoOffset(0, -h / 2, d / 2, vp).y };
  const back = { x: anchor.x + isoOffset(0, 0, -d / 2, vp).x, y: anchor.y + isoOffset(0, -h / 2, -d / 2, vp).y };

  const above = anchor.y < core.y;
  const underCore = Math.abs(front.x - core.x) < UNDER_CORE_PX;
  const badgeStart = (centre: number) => centre - ((spec.badges.length - 1) * BADGE_PITCH) / 2;

  const nameAt = above
    ? { x: top.x, y: back.y - 74 }
    : underCore
      ? { x: front.x, y: front.y + 86 }
      : { x: top.x, y: top.y - 34 };
  const badgeAt = above ? { x: top.x, y: back.y - 54 } : { x: front.x, y: front.y + 24 };
  const chipAt = above ? { x: top.x, y: back.y - 30 } : { x: front.x, y: front.y + 58 };

  // The compact stack seats one source hard against the left edge, and a name
  // is wider than the machine it names. Nudge, do not clip.
  const nameX = clampCentre(nameAt.x, monoWidth(spec.name, 12), vp);
  const badgeRowX = clampCentre(
    badgeAt.x,
    (spec.badges.length - 1) * BADGE_PITCH + BADGE_DIAMETER,
    vp,
  );
  const chipX = clampCentre(chipAt.x, spec.chip.length * 6.9 + 18, vp);

  // Depth cues. Marginal on purpose — enough to seat the far sources into the
  // frame, not enough to read as a size difference between machine classes.
  const scale = 0.94 + 0.06 * depth;
  const opacity = (0.86 + 0.14 * depth) * (muted ? 0.5 : 1);
  // A lost machine goes dark, but its name does not: the point of the beat is
  // that you can still see *which* machine went away.
  const dark = routeState === "failed" || routeState === "dimmed";

  const spanX = ((w + d) / 2) * axes.x.x + HIT_PADDING;
  const spanY = ((w + d) / 2) * axes.x.y + (h / 2) * vp.scale + HIT_PADDING;

  const activate = () => onSelect?.(spec.key);
  const onKeyDown = (event: KeyboardEvent<SVGGElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    activate();
  };

  return (
    <g
      data-map-node={spec.key}
      data-route-state={routeState}
      data-lifted={lifted ? "true" : "false"}
      role="button"
      tabIndex={0}
      aria-label={`${spec.name}. ${spec.chip}.`}
      style={{ cursor: onSelect ? "pointer" : "default", outline: "none" }}
      onPointerEnter={() => onHighlight(spec.key)}
      onPointerLeave={() => onHighlight(null)}
      onFocus={() => {
        setFocused(true);
        onHighlight(spec.key);
      }}
      onBlur={() => {
        setFocused(false);
        onHighlight(null);
      }}
      onClick={activate}
      onKeyDown={onKeyDown}
    >
      {/* Pure translation, so the transform origin never comes into it — which
          is what makes the lift safe to hand to CSS and therefore transitionable
          on an SVG group. */}
      <g
        style={{
          transform: lifted ? "translateY(-7px)" : "none",
          transition: "transform 220ms ease, opacity 220ms ease",
          opacity,
        }}
      >
        {/* One rectangle doing two jobs: it makes the gaps between a cluster's
            units hoverable, and it draws the focus ring. The ring is cream, not
            orange — orange is reserved for the job's path, including here. */}
        <rect
          x={r2(anchor.x - spanX)}
          y={r2(anchor.y - spanY)}
          width={r2(spanX * 2)}
          height={r2(spanY * 2)}
          rx={6}
          fill="transparent"
          stroke={focused ? MAP_COLORS.text : "none"}
          strokeWidth={1}
          strokeDasharray="3 3"
          opacity={focused ? 0.8 : 1}
        />

        <g
          opacity={dark ? 0.3 : 1}
          transform={`translate(${r2(anchor.x)} ${r2(anchor.y)}) scale(${r2(scale)})`}
        >
          <g transform={`translate(0 ${r2((h / 2) * vp.scale)})`}>
            <Silhouette spec={spec} vp={vp} />
          </g>
        </g>

        <g aria-hidden="true" opacity={dark ? 0.72 : 1}>
          {spec.badges.map((badge, index) => (
            <PlatformBadge
              key={badge}
              badge={badge}
              x={badgeStart(badgeRowX) + index * BADGE_PITCH}
              y={badgeAt.y}
              tint={lifted ? MAP_COLORS.text : MAP_COLORS.dim}
            />
          ))}
          <Chip x={chipX} y={chipAt.y} text={spec.chip} />
          <text
            x={r2(nameX)}
            y={r2(nameAt.y)}
            fill={MAP_COLORS.text}
            fontSize={12}
            fontFamily={MAP_MONO}
            letterSpacing="0.1em"
            textAnchor="middle"
          >
            {spec.name}
          </text>
        </g>

        {/* Both marks land on the anchor, which is the middle of the machine.
            The prototype put the tick on the near face and it collided with the
            badge row underneath — and a mark on the machine says "this machine"
            more plainly than a mark beside it. */}
        {routeState === "failed" ? <FailureMark x={anchor.x} y={anchor.y} opacity={1} /> : null}
        {routeState === "dimmed" ? <FailureMark x={anchor.x} y={anchor.y} opacity={0.45} /> : null}
        {phase === "accepted" && routeState === "active" ? (
          <AcceptedMark x={anchor.x} y={anchor.y} />
        ) : null}
      </g>
    </g>
  );
}
