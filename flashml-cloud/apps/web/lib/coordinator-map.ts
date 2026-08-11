// Geometry and story state for the coordinator map hero.
//
// The hero draws the coordinator doing its real job — leasing work out, hearing
// heartbeats, holding checkpoints, and re-leasing when a machine disappears.
// Everything the map needs to know about *where things are* and *what beat it
// is on* lives here, pure: no DOM, no clock, no randomness. Components read
// coordinates from this module and do no maths of their own, which is what lets
// the composition be pinned in tests instead of eyeballed in a browser.
//
// Two compositions exist. The desktop one is the diamond: four sources on the
// corners of a square on the ground plane, coordinator in the middle. The
// compact one is a vertical stack for narrow frames, and it is a genuinely
// different arrangement rather than the diamond shrunk — an isometric diamond
// is always ~1.7× wider than tall, so scaling it into a phone frame leaves the
// labels unreadable.

import { HERO_SOURCES } from "./hero-story";
import type { HeroSourceKey } from "./hero-story";

export type MapNodeKey = HeroSourceKey;
export type MapPhase = "running" | "lost" | "resumed" | "accepted";
export type RouteState = "idle" | "active" | "failed" | "dimmed";

export interface Point {
  readonly x: number;
  readonly y: number;
}

export interface Viewport {
  readonly width: number;
  readonly height: number;
  readonly scale: number;
  readonly originX: number;
  readonly originY: number;
}

/* ------------------------------------------------------------------ */
/* projection                                                          */
/* ------------------------------------------------------------------ */

const COS30 = Math.cos(Math.PI / 6);
const SIN30 = Math.sin(Math.PI / 6);

/**
 * True isometric projection of a ground-plane cell.
 *
 * `x` and `z` run along the two ground axes, `y` is height. +y is UP on
 * screen, which is the opposite of the SVG axis and the reason this is worth
 * a function: getting the sign wrong buries every node in the floor.
 */
export function project(x: number, y: number, z: number, vp: Viewport): Point {
  return {
    x: vp.originX + (x - z) * COS30 * vp.scale,
    y: vp.originY + ((x + z) * SIN30 - y) * vp.scale,
  };
}

/** The two ground axes as screen directions. Both are unit vectors. */
const AXIS_X: Point = { x: COS30, y: SIN30 };
const AXIS_Z: Point = { x: -COS30, y: SIN30 };

/** Two decimals is well under a tenth of a pixel at every scale we ship and
 * keeps emitted path data small — the whole hero payload budget is 60 KB. */
function px(value: number): number {
  return Number(value.toFixed(2));
}

/* ------------------------------------------------------------------ */
/* viewports and layouts                                               */
/* ------------------------------------------------------------------ */

/** The approved prototype's frame. Every value here is measured, not chosen:
 * this scale and origin are what make the diamond, its labels and its badges
 * compose inside 1240×620 without clipping. */
export const MAP_VIEWPORT_DESKTOP: Viewport = {
  width: 1240,
  height: 620,
  scale: 26,
  originX: 822,
  originY: 304,
};

/**
 * The narrow frame, used at 880px and below.
 *
 * Portrait (420×660) because the composition is a stack, not a diamond: the
 * coordinator sits in the middle, one source above it, one below, and the
 * remaining two flanking it close in. That shape is forced by two constraints
 * rather than taste —
 *
 *  1. Elbow routes swing sideways by roughly 0.87× their vertical run (see
 *     `elbowPath`), so sources more than ~200px above or below the core would
 *     push their own corners off the side of a 420-wide frame.
 *  2. Every route has to leave the core on its own axis or two of them overlap
 *     into one line, which makes a red failed route and a live orange one
 *     indistinguishable. Four routes, four axes — so one source is steeply
 *     above, one steeply below, and two sit shallow to the left and right.
 *
 * 420 wide keeps 11.5px map type legible when the SVG is scaled into a 390px
 * phone; a wider viewBox would shrink it below readable.
 */
export const MAP_VIEWPORT_COMPACT: Viewport = {
  width: 420,
  height: 660,
  scale: 20,
  originX: 210,
  originY: 375,
};

/** The breakpoint the hero CSS already uses. §3.1 fixes `Viewport` at five
 * numbers, so there is no layout flag to read; width is the honest
 * discriminator, because the compact composition exists precisely because the
 * frame is narrow. */
const COMPACT_MAX_WIDTH = 880;

function isCompact(vp: Viewport): boolean {
  return vp.width <= COMPACT_MAX_WIDTH;
}

interface MapCell {
  readonly x: number;
  readonly z: number;
}

/** Compact cells. Read them through `nodeAnchor`, never directly: `spec.cell`
 * is the desktop cell, and these replace it in the narrow frame. */
const COMPACT_CELLS: Readonly<Record<MapNodeKey, MapCell>> = {
  // steeply above the core
  everyday: { x: -13.5, z: -9 },
  // shallow left, tucked beside the core
  owned: { x: -7.5, z: -0.2 },
  // shallow right, mirroring owned
  cloud: { x: 4.5, z: -2.8 },
  // steeply below the core
  rented: { x: 10.1, z: 5.5 },
};

interface GridSpec {
  /** Half-extent of the grid on each ground axis, in cells. */
  readonly half: number;
  readonly step: number;
}

const DESKTOP_GRID: GridSpec = { half: 9, step: 1.5 };
const COMPACT_GRID: GridSpec = { half: 15, step: 1.5 };

/* ------------------------------------------------------------------ */
/* the nodes                                                           */
/* ------------------------------------------------------------------ */

export interface MapNodeSpec {
  readonly key: MapNodeKey;
  readonly name: string;
  readonly chip: string;
  readonly badges: readonly string[];
  readonly cell: { readonly x: number; readonly z: number };
  readonly size: { readonly w: number; readonly h: number; readonly d: number };
  readonly silhouette: "cluster" | "rack" | "cards" | "stepped";
}

type NodePresentation = Omit<MapNodeSpec, "key" | "name">;

/**
 * How each source is drawn. Four distinct silhouettes, not four cubes: the
 * shape has to carry the recognition, because at map scale the label is the
 * only other thing telling you what you are looking at.
 */
const NODE_PRESENTATION = {
  everyday: {
    chip: "12 NODES",
    badges: ["apple", "windows", "linux"],
    cell: { x: -6.2, z: 6.2 },
    size: { w: 2.7, h: 1.25, d: 2.3 },
    silhouette: "cluster",
  },
  owned: {
    chip: "1 RACK · 4 GPU",
    badges: ["ubuntu", "docker"],
    cell: { x: -6.2, z: -6.2 },
    size: { w: 2.5, h: 1.5, d: 2.1 },
    silhouette: "rack",
  },
  cloud: {
    chip: "BURST",
    badges: ["googlecloud", "AWS"],
    cell: { x: 6.2, z: -6.2 },
    size: { w: 2.4, h: 1.8, d: 2.0 },
    silhouette: "stepped",
  },
  rented: {
    // "2 × H100" and not "2 GPU · H100" would read better, but the landing
    // page's claim guard (`lib/landing-expansion.test.ts`) rejects a digit
    // followed by × or x anywhere in the visible text, because that is the
    // shape every unverified "3× faster" claim takes. A hardware count is not
    // that claim, but the guard cannot tell them apart and the guard is right
    // to be blunt.
    chip: "2 GPU · H100",
    badges: ["nvidia"],
    cell: { x: 6.2, z: 6.2 },
    size: { w: 2.6, h: 1.35, d: 2.2 },
    silhouette: "cards",
  },
} as const satisfies Record<MapNodeKey, NodePresentation>;

/**
 * The four sources, derived from `HERO_SOURCES` rather than retyped.
 *
 * The map and the rest of the hero have to name the same four things: a fifth
 * key here, or a renamed one, would draw a source no other surface knows
 * about. Deriving both the key and the name makes that impossible instead of
 * merely tested.
 */
export const MAP_NODES: readonly MapNodeSpec[] = HERO_SOURCES.map((source) => {
  const drawn = NODE_PRESENTATION[source.key];
  return {
    key: source.key,
    name: source.label.toUpperCase(),
    chip: drawn.chip,
    badges: drawn.badges,
    cell: drawn.cell,
    size: drawn.size,
    silhouette: drawn.silhouette,
  };
});

export const CORE_CELL = { x: 0, z: 0 } as const;

/** Height at which routes and tokens meet the coordinator: the top of its
 * plate stack, so nothing enters through the floor. */
const CORE_ANCHOR_Y = 2.35;

/** The machine that dies. Everyday hardware is the honest victim — it is the
 * tier the product exists to make usable despite being unreliable. */
export const VICTIM_KEY: MapNodeKey = "everyday";

/** The machine the work moves to. Deliberately a different class from the
 * victim: recovery across tiers is the claim. */
export const RESCUER_KEY: MapNodeKey = "rented";

/** Where a source's routes, heartbeats and tokens attach: mid-height of its
 * box, so a token lands on the machine rather than beside it. */
export function nodeAnchor(spec: MapNodeSpec, vp: Viewport): Point {
  const cell = isCompact(vp) ? COMPACT_CELLS[spec.key] : spec.cell;
  return project(cell.x, spec.size.h / 2, cell.z, vp);
}

export function corePoint(vp: Viewport): Point {
  return project(CORE_CELL.x, CORE_ANCHOR_Y, CORE_CELL.z, vp);
}

/* ------------------------------------------------------------------ */
/* routing                                                             */
/* ------------------------------------------------------------------ */

/** Below half a pixel a corner is not drawable, and rounding would print the
 * two ends of the segment at the same coordinates anyway. */
const ELBOW_EPSILON = 0.5;

/**
 * A route from `from` to `to` as two segments, each parallel to a ground axis,
 * meeting at a mitred corner.
 *
 * Straight diagonals out of the core read as a starburst — decoration. An
 * axis-aligned pair reads as a map, because it obeys the same grid the ground
 * plane does.
 *
 * Any displacement decomposes uniquely into `alongX · AXIS_X + alongZ · AXIS_Z`,
 * which leaves exactly two elbows to choose from: take the x-axis leg first, or
 * the z-axis leg first. The two corners are mirror images about the midpoint,
 * so length cannot choose between them — the sign of the two legs does. When
 * they share a sign the displacement is steeper than either axis and the route
 * is a V; taking the z-leg first then bends it right on the way up and left on
 * the way down. When the signs differ the displacement is shallower than the
 * axes and the x-leg first bends it the matching way. Applied to a ring of
 * sources around one core, that rule hands every route its own axis out of the
 * core, so no two routes leave along the same line — which matters the moment
 * one of them turns red and another is live orange.
 *
 * When the two ends already share an axis one leg is zero, and the path is
 * emitted straight rather than with a zero-length segment.
 */
export function elbowPath(from: Point, to: Point): string {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const alongX = dy + dx / (2 * COS30);
  const alongZ = dy - dx / (2 * COS30);

  const head = `M ${px(from.x)} ${px(from.y)}`;
  const tail = `L ${px(to.x)} ${px(to.y)}`;

  if (Math.abs(alongX) < ELBOW_EPSILON || Math.abs(alongZ) < ELBOW_EPSILON) {
    return `${head} ${tail}`;
  }

  const [leg, axis] =
    alongX * alongZ > 0
      ? ([alongZ, AXIS_Z] as const)
      : ([alongX, AXIS_X] as const);
  const cornerX = from.x + leg * axis.x;
  const cornerY = from.y + leg * axis.y;

  return `${head} L ${px(cornerX)} ${px(cornerY)} ${tail}`;
}

/* ------------------------------------------------------------------ */
/* the ground grid                                                     */
/* ------------------------------------------------------------------ */

/**
 * Clip a screen segment to the frame, Liang–Barsky. Returns null when the
 * segment misses the frame entirely or only grazes a corner.
 *
 * The grid is generated in cells and then trimmed rather than sized to fit,
 * because in the compact frame a ground plane wide enough to sit under a
 * vertical stack is far wider than the frame. Trimming keeps the emitted paths
 * short and lets the component fade the grid out at edges it knows the lines
 * actually reach.
 */
function clipToFrame(a: Point, b: Point, vp: Viewport): [Point, Point] | null {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const limits: readonly (readonly [number, number])[] = [
    [-dx, a.x],
    [dx, vp.width - a.x],
    [-dy, a.y],
    [dy, vp.height - a.y],
  ];

  let enter = 0;
  let exit = 1;
  for (const [p, q] of limits) {
    if (p === 0) {
      if (q < 0) return null; // parallel to this edge and outside it
      continue;
    }
    const t = q / p;
    if (p < 0) {
      if (t > exit) return null;
      if (t > enter) enter = t;
    } else {
      if (t < enter) return null;
      if (t < exit) exit = t;
    }
  }

  const length = Math.hypot(dx, dy) * (exit - enter);
  if (length < 1) return null;

  return [
    { x: a.x + dx * enter, y: a.y + dy * enter },
    { x: a.x + dx * exit, y: a.y + dy * exit },
  ];
}

/**
 * The ground plane, as SVG path strings.
 *
 * Faint dashed lines on the floor are what stop the sources reading as objects
 * floating in void; they also make the elbow routes legible as axis-aligned,
 * because the viewer can see the axes they follow.
 */
export function gridLines(vp: Viewport): readonly string[] {
  const { half, step } = isCompact(vp) ? COMPACT_GRID : DESKTOP_GRID;
  const count = Math.round((half * 2) / step);
  const paths: string[] = [];

  for (let i = 0; i <= count; i++) {
    const at = -half + i * step;
    const segments: readonly (readonly [Point, Point])[] = [
      [project(at, 0, -half, vp), project(at, 0, half, vp)],
      [project(-half, 0, at, vp), project(half, 0, at, vp)],
    ];
    for (const [a, b] of segments) {
      const clipped = clipToFrame(a, b, vp);
      if (!clipped) continue;
      paths.push(
        `M ${px(clipped[0].x)} ${px(clipped[0].y)} L ${px(clipped[1].x)} ${px(clipped[1].y)}`,
      );
    }
  }

  return paths;
}

/* ------------------------------------------------------------------ */
/* the story                                                           */
/* ------------------------------------------------------------------ */

/**
 * Scroll progress to story beat.
 *
 * Total over the reals and clamped at both ends: the caller is a scroll
 * listener, and scroll containers overshoot on both sides on every rubber-band
 * platform. A phase of `undefined` at the top of a bounce would blank the map.
 */
export function phaseForProgress(progress: number): MapPhase {
  // NaN is the one input clamping cannot handle: every comparison against it
  // is false, so it would fall through to the last beat rather than the first.
  const clamped = Number.isNaN(progress) ? 0 : Math.min(1, Math.max(0, progress));
  if (clamped < 0.3) return "running";
  if (clamped < 0.5) return "lost";
  if (clamped < 0.75) return "resumed";
  return "accepted";
}

/**
 * How each source's route is drawn on a given beat.
 *
 * Only the job's own route is ever orange — the single rule that separates
 * this from the reference art, where orange was hardware, plinths and route at
 * once and so meant nothing. Idle routes stay grey; the victim's route fails
 * red and then dims once its work has moved on, so the failure stays visible
 * as history without competing with the live one.
 */
export function routeStateFor(key: MapNodeKey, phase: MapPhase): RouteState {
  if (phase === "running") return key === VICTIM_KEY ? "active" : "idle";
  if (phase === "lost") return key === VICTIM_KEY ? "failed" : "idle";
  if (key === VICTIM_KEY) return "dimmed";
  return key === RESCUER_KEY ? "active" : "idle";
}

export interface Readout {
  readonly leases: number;
  readonly checkpoint: string;
  readonly lost: number;
  readonly goodput: number;
  readonly state: string;
}

/**
 * The live value strip under the map.
 *
 * These are the numbers that make a diagram read as a running system rather
 * than an illustration, so they have to move the way the real ones do: losing
 * a node costs a lease and dents goodput, the checkpoint counter never goes
 * backwards, and `lost` never returns to zero once it has happened — attempted
 * work is not accepted work, and the strip is where that shows.
 */
const READOUTS: Readonly<Record<MapPhase, Readout>> = {
  running: { leases: 12, checkpoint: "4 / 7", lost: 0, goodput: 94, state: "RUNNING" },
  lost: { leases: 11, checkpoint: "4 / 7", lost: 1, goodput: 88, state: "NODE LOST" },
  resumed: { leases: 12, checkpoint: "5 / 7", lost: 1, goodput: 90, state: "RESUMED ELSEWHERE" },
  accepted: { leases: 12, checkpoint: "7 / 7", lost: 1, goodput: 94, state: "RESULT ACCEPTED" },
};

export function readoutFor(phase: MapPhase): Readout {
  return READOUTS[phase];
}
