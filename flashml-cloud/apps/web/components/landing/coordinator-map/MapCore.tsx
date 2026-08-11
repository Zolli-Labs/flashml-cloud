// The coordinator.
//
// It is the subject of the map — the one component of Zolli no competitor has —
// so it is the only object drawn with an interior. A base plate carries a
// checkpoint; four hairline posts hold a narrow top deck above it; the gap
// between the two is open on every side, and the checkpoint sits in that gap,
// faintly lit. "State lives here" made literal rather than captioned.
//
// The deck is deliberately narrower than the base. A lid as wide as its plinth
// projects *over* anything inside the stack at this camera angle and hides it,
// which is the trap that makes most isometric interiors read as solid blocks.
// The numbers below keep the checkpoint's front faces clear of the deck's lower
// silhouette at both viewport scales; changing either span reopens that.
//
// No part of the coordinator is orange except the single token sitting on its
// deck, which is a lease about to leave. Orange is the job's path and nothing
// else — chassis, plates, posts and glow are all surface and border tokens.

import { useId } from "react";
import { CORE_CELL, project, type Viewport } from "@/lib/coordinator-map";
import { IsoBox, MAP_COLORS, MAP_MONO, isoOffset, monoWidth, r2, type IsoVolume } from "./iso";

/** Height at which the deck's top face sits — the same `2.35` the geometry
 * module anchors routes and tokens to, so nothing enters the coordinator
 * through the floor or floats above its lid. */
const DECK_TOP = 2.35;
const DECK_HEIGHT = 0.4;
const DECK_HALF = 0.95;
const POST_SECTION = 0.18;
const CHECKPOINT_FLOOR = 0.62;

const CORE_NAME = "ZOLLI COORDINATOR";
const CORE_SUB = "leases · heartbeats · checkpoints";

/**
 * Widest a caption line may be before it is dropped, as a share of the frame.
 *
 * The caption is centred on the coordinator and the coordinator sits among the
 * sources, so a long line reaches sideways into whichever source is nearest.
 * At the desktop frame the second line is 18% of the width and reaches nothing;
 * in the compact frame it is over half the width and lands under the badges of
 * the source beside it. Rather than sniff a breakpoint, measure the line.
 */
const CAPTION_WIDTH_LIMIT = 0.45;

const BASE_PLATE: IsoVolume = { x: -1.85, y: 0, z: -1.85, w: 3.7, h: 0.4, d: 3.7 };
const TIER_PLATE: IsoVolume = { x: -1.45, y: 0.4, z: -1.45, w: 2.9, h: 0.22, d: 2.9 };
const CHECKPOINT: IsoVolume = {
  x: -0.44,
  y: CHECKPOINT_FLOOR,
  z: -0.44,
  w: 0.88,
  h: 0.5,
  d: 0.88,
};
const DECK: IsoVolume = {
  x: -DECK_HALF,
  y: DECK_TOP - DECK_HEIGHT,
  z: -DECK_HALF,
  w: DECK_HALF * 2,
  h: DECK_HEIGHT,
  d: DECK_HALF * 2,
};

/** The four posts, back to front. Painter's order on the ground plane is
 * increasing `x + z`, and the near post has to arrive after the checkpoint so
 * it crosses in front of it — that overlap is what says "inside" rather than
 * "on top of". */
const POST_CORNERS: readonly (readonly [number, number])[] = [
  [-DECK_HALF, -DECK_HALF],
  [-DECK_HALF, DECK_HALF - POST_SECTION],
  [DECK_HALF - POST_SECTION, -DECK_HALF],
  [DECK_HALF - POST_SECTION, DECK_HALF - POST_SECTION],
];

function post(x: number, z: number): IsoVolume {
  return {
    x,
    y: CHECKPOINT_FLOOR,
    z,
    w: POST_SECTION,
    h: DECK_TOP - DECK_HEIGHT - CHECKPOINT_FLOOR,
    d: POST_SECTION,
  };
}

export function MapCore({ vp }: { readonly vp: Viewport }) {
  const id = useId();
  const glowId = `${id}-core-glow`;
  const ground = project(CORE_CELL.x, 0, CORE_CELL.z, vp);
  const held = isoOffset(0, CHECKPOINT_FLOOR + CHECKPOINT.h / 2, 0, vp);
  const emitter = isoOffset(0, DECK_TOP, 0, vp);
  // Only the vertical position matters: the caption is centred on the core's
  // own screen x, which is the group origin, so its x is simply zero.
  const caption = isoOffset(0, 0, 1.85, vp);
  const posts = POST_CORNERS.map(([x, z]) => post(x, z));
  const nameWidth = monoWidth(CORE_NAME, 13);
  const showSub = monoWidth(CORE_SUB, 10, 0.08) <= vp.width * CAPTION_WIDTH_LIMIT;
  const ruleHalf = nameWidth / 2;
  // With the second line gone the caption is a single short row, so it can sit
  // lower without running into whatever is below the coordinator — and the
  // extra drop is exactly what buys clearance from the badge row of the source
  // beside it in the compact stack.
  const nameOffset = showSub ? 36 : 50;

  return (
    <g
      data-map-core="coordinator"
      aria-hidden="true"
      transform={`translate(${r2(ground.x)} ${r2(ground.y)})`}
    >
      <defs>
        <radialGradient id={glowId} gradientUnits="userSpaceOnUse" cx={r2(held.x)} cy={r2(held.y)} r={r2(vp.scale * 1.9)}>
          <stop offset="0%" stopColor={MAP_COLORS.dim} stopOpacity="0.22" />
          <stop offset="100%" stopColor={MAP_COLORS.dim} stopOpacity="0" />
        </radialGradient>
      </defs>

      <IsoBox vp={vp} {...BASE_PLATE} />
      <IsoBox vp={vp} {...TIER_PLATE} />
      {posts.slice(0, 3).map((volume, index) => (
        <IsoBox key={`post-${index}`} vp={vp} {...volume} strokeWidth={0.9} />
      ))}

      {/* The cavity light. It reads as the checkpoint being lit from inside the
          stack, and it is the reason the held object is legible at 20px scale. */}
      <circle cx={r2(held.x)} cy={r2(held.y)} r={r2(vp.scale * 1.9)} fill={`url(#${glowId})`} />

      <IsoBox
        vp={vp}
        {...CHECKPOINT}
        top={MAP_COLORS.dim}
        left={MAP_COLORS.faint}
        right={MAP_COLORS.lineStrong}
        stroke={MAP_COLORS.dim}
        strokeWidth={0.9}
      />

      <IsoBox vp={vp} {...posts[3]} strokeWidth={0.9} />
      <IsoBox vp={vp} {...DECK} />

      {/* A lease sitting on the deck, about to leave. The only orange on the
          coordinator, and it is the job, not the machine. */}
      <rect
        x={r2(emitter.x - 3.6)}
        y={r2(emitter.y - 3.6)}
        width={7.2}
        height={7.2}
        fill={MAP_COLORS.job}
        transform={`rotate(45 ${r2(emitter.x)} ${r2(emitter.y)})`}
      />

      {/* Type hierarchy: the coordinator's own label is larger than a source's
          and sits under a hairline rule, because it is the thing being named. */}
      <line
        x1={r2(-ruleHalf)}
        y1={r2(caption.y + (showSub ? 16 : 30))}
        x2={r2(ruleHalf)}
        y2={r2(caption.y + (showSub ? 16 : 30))}
        stroke={MAP_COLORS.line}
        strokeWidth={1}
      />
      <text
        x={0}
        y={r2(caption.y + nameOffset)}
        fill={MAP_COLORS.text}
        fontSize={13}
        fontFamily={MAP_MONO}
        letterSpacing="0.1em"
        textAnchor="middle"
      >
        {CORE_NAME}
      </text>
      {showSub ? (
        <text
          x={0}
          y={r2(caption.y + 53)}
          fill={MAP_COLORS.faint}
          fontSize={10}
          fontFamily={MAP_MONO}
          letterSpacing="0.08em"
          textAnchor="middle"
        >
          {CORE_SUB}
        </text>
      ) : null}
    </g>
  );
}
