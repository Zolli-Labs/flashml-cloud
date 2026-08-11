// Isometric drawing primitives shared by the map's components.
//
// Nothing here decides *where* anything is — that is `lib/coordinator-map.ts`
// and only `lib/coordinator-map.ts`. This module reads that module's `project`
// and turns its output into the two things every drawn part of the map needs:
// a palette expressed in brand tokens, and a box.
//
// It exists as its own file because `MapCore` and `MapNode` both draw boxes and
// both need the palette. Putting either in `CoordinatorMap.tsx` would make the
// children import their parent.

import { project, type Point, type Viewport } from "@/lib/coordinator-map";

/**
 * Colour by role, never by hue.
 *
 * `job` is the only warm entry and it belongs to exactly one thing: the path a
 * task takes out of the coordinator and the state it sends back. No hardware,
 * no plinth, no grid line and no focus ring may use it — that single rule is
 * most of the difference between this map and the reference art it replaces.
 *
 * The three surface entries are the map's light model. `raised`, `surface` and
 * `bg` are the existing dark-theme surface ramp, reused here as the top, the
 * down-left face and the down-right face of every solid. Three real tokens
 * three steps apart do the job the prototype did by shading a literal hex, so
 * a theme change moves the hardware with the rest of the page.
 */
export const MAP_COLORS = {
  bg: "var(--z-bg)",
  surface: "var(--z-surface)",
  raised: "var(--z-surface-raised)",
  line: "var(--z-border)",
  lineStrong: "var(--z-border-strong)",
  text: "var(--z-text)",
  dim: "var(--z-text-secondary)",
  faint: "var(--z-text-dim)",
  job: "var(--z-orange)",
  jobHot: "var(--z-orange-bright)",
  ok: "var(--z-healthy)",
  failed: "var(--z-failure)",
} as const;

/** Map type is monospaced throughout: these are instrument readings, not prose. */
export const MAP_MONO =
  'var(--font-geist-mono), ui-monospace, SFMono-Regular, Menlo, monospace';

/** Two decimals is under a tenth of a pixel at every scale the map ships at,
 * and the hero's whole markup budget is 60 KB. */
export function r2(value: number): number {
  return Number(value.toFixed(2));
}

/**
 * How wide a run of map type will be, in user units.
 *
 * SVG cannot lay text out before it paints, and the map has to decide *before*
 * painting whether a label fits between a machine and the frame edge. Every
 * string on the map is monospaced, so an estimate is exact enough: one advance
 * is 0.6em in Geist Mono and in every fallback named in `MAP_MONO`, and the
 * tracking adds a fixed fraction on top.
 */
export function monoWidth(text: string, fontSize: number, tracking = 0.1): number {
  return text.length * fontSize * (0.6 + tracking);
}

/**
 * Keep a centred label inside the frame.
 *
 * The compact composition puts one source close to the left edge, and its name
 * is the widest string on the map. Nudging the label a few pixels off its
 * machine is invisible; letting it run off the viewBox is not.
 */
export function clampCentre(centre: number, width: number, vp: Viewport, margin = 10): number {
  const half = width / 2;
  if (half * 2 + margin * 2 > vp.width) return vp.width / 2;
  return Math.min(vp.width - margin - half, Math.max(margin + half, centre));
}

/**
 * The screen displacement of a one-unit step along each axis of the ground
 * space. `x` and `z` are the two ground axes; `y` points up the screen, so its
 * screen `y` is negative.
 *
 * Components use these instead of writing `cos(30°)` anywhere. A silhouette
 * that needs a half-width, a bounding box or the plane of a fan circle can
 * derive all of it from the same projection the geometry module uses, which is
 * what keeps the two compositions from drifting apart.
 */
export interface IsoAxes {
  readonly x: Point;
  readonly y: Point;
  readonly z: Point;
  /** Pixels per ground unit — `vp.scale`, read back through the projection. */
  readonly unit: number;
}

export function isoAxes(vp: Viewport): IsoAxes {
  return {
    x: isoOffset(1, 0, 0, vp),
    y: isoOffset(0, 1, 0, vp),
    z: isoOffset(0, 0, 1, vp),
    unit: vp.scale,
  };
}

/**
 * A point in ground space expressed as a screen displacement from the origin
 * cell rather than an absolute position.
 *
 * The projection is affine, so displacements are position-independent — which
 * is the only reason a node can be drawn once in local coordinates and then
 * translated to wherever its anchor happens to be. That matters because the
 * compact composition's cells are deliberately not exported: `nodeAnchor` is
 * the only way to learn where a source sits, and everything else has to be
 * drawn relative to it.
 */
export function isoOffset(x: number, y: number, z: number, vp: Viewport): Point {
  const origin = project(0, 0, 0, vp);
  const at = project(x, y, z, vp);
  return { x: at.x - origin.x, y: at.y - origin.y };
}

/** One axis-aligned volume, in ground units, relative to the group origin. */
export interface IsoVolume {
  readonly x: number;
  readonly y: number;
  readonly z: number;
  readonly w: number;
  readonly h: number;
  readonly d: number;
}

export interface IsoBoxProps extends IsoVolume {
  readonly vp: Viewport;
  /** Top face. Defaults to the lightest surface token. */
  readonly top?: string;
  /** The `z + d` face, which points down-left. */
  readonly left?: string;
  /** The `x + w` face, which points down-right. */
  readonly right?: string;
  readonly stroke?: string;
  readonly strokeWidth?: number;
  readonly opacity?: number;
}

/**
 * The three visible faces of a box.
 *
 * Order is left, right, top. They are the faces of a convex solid seen from one
 * side so they never overlap each other; the order only fixes which one wins a
 * shared edge, and drawing the top last keeps the brightest plane crisp.
 */
export function IsoBox({
  vp,
  x,
  y,
  z,
  w,
  h,
  d,
  top = MAP_COLORS.raised,
  left = MAP_COLORS.surface,
  right = MAP_COLORS.bg,
  stroke = MAP_COLORS.lineStrong,
  strokeWidth = 1.2,
  opacity,
}: IsoBoxProps) {
  const face = (corners: readonly (readonly [number, number, number])[]) =>
    corners
      .map(([cx, cy, cz]) => {
        const point = isoOffset(cx, cy, cz, vp);
        return `${r2(point.x)},${r2(point.y)}`;
      })
      .join(" ");

  return (
    <g opacity={opacity} stroke={stroke} strokeWidth={strokeWidth} strokeLinejoin="round">
      <polygon
        fill={left}
        points={face([
          [x, y, z + d],
          [x, y + h, z + d],
          [x + w, y + h, z + d],
          [x + w, y, z + d],
        ])}
      />
      <polygon
        fill={right}
        points={face([
          [x + w, y, z],
          [x + w, y + h, z],
          [x + w, y + h, z + d],
          [x + w, y, z + d],
        ])}
      />
      <polygon
        fill={top}
        points={face([
          [x, y + h, z],
          [x + w, y + h, z],
          [x + w, y + h, z + d],
          [x, y + h, z + d],
        ])}
      />
    </g>
  );
}

/**
 * A hairline drawn *on* the down-left face of a volume, used for rack ruling.
 *
 * Takes ground coordinates so the caller never has to know the face's screen
 * slope; both ends project through the same function everything else does.
 */
export function IsoFaceLine({
  vp,
  from,
  to,
  stroke = MAP_COLORS.lineStrong,
  strokeWidth = 1,
  opacity = 0.75,
}: {
  readonly vp: Viewport;
  readonly from: readonly [number, number, number];
  readonly to: readonly [number, number, number];
  readonly stroke?: string;
  readonly strokeWidth?: number;
  readonly opacity?: number;
}) {
  const a = isoOffset(from[0], from[1], from[2], vp);
  const b = isoOffset(to[0], to[1], to[2], vp);
  return (
    <line
      x1={r2(a.x)}
      y1={r2(a.y)}
      x2={r2(b.x)}
      y2={r2(b.y)}
      stroke={stroke}
      strokeWidth={strokeWidth}
      opacity={opacity}
      strokeLinecap="round"
    />
  );
}

/**
 * A circle lying in the down-left face of a volume — a cooling fan, seen at the
 * angle the rest of the map is seen at.
 *
 * A circle in that plane projects to a sheared ellipse, and the shear is
 * exactly the pair of screen vectors the ground x-axis and the up-axis already
 * give us. Feeding those two vectors in as the columns of an SVG matrix maps
 * the unit circle onto the right ellipse without a single trigonometric
 * constant living in a component. `non-scaling-stroke` keeps the outline a
 * hairline after that matrix stretches it.
 */
export function IsoFaceDisc({
  vp,
  at,
  radius,
  stroke = MAP_COLORS.lineStrong,
  strokeWidth = 1,
  fill = "none",
  opacity,
}: {
  readonly vp: Viewport;
  readonly at: readonly [number, number, number];
  readonly radius: number;
  readonly stroke?: string;
  readonly strokeWidth?: number;
  readonly fill?: string;
  readonly opacity?: number;
}) {
  const axes = isoAxes(vp);
  const centre = isoOffset(at[0], at[1], at[2], vp);
  const matrix = [
    r2(axes.x.x * radius),
    r2(axes.x.y * radius),
    r2(axes.y.x * radius),
    r2(axes.y.y * radius),
    r2(centre.x),
    r2(centre.y),
  ].join(" ");

  return (
    <circle
      r={1}
      transform={`matrix(${matrix})`}
      fill={fill}
      stroke={stroke}
      strokeWidth={strokeWidth}
      opacity={opacity}
      vectorEffect="non-scaling-stroke"
    />
  );
}
