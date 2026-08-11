import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  CORE_CELL,
  MAP_NODES,
  MAP_VIEWPORT_COMPACT,
  MAP_VIEWPORT_DESKTOP,
  RESCUER_KEY,
  VICTIM_KEY,
  corePoint,
  elbowPath,
  gridLines,
  nodeAnchor,
  phaseForProgress,
  project,
  readoutFor,
  routeStateFor,
} from "./coordinator-map";
import type { MapNodeSpec, MapPhase, Point, Viewport } from "./coordinator-map";
import { HERO_SOURCES } from "./hero-story";

/**
 * The map is the landing page's only argument, and it is made entirely of
 * coordinates. A node an inch off its cell, a route that leaves the core on
 * the same line as its neighbour, a grid that stops short of the hardware —
 * none of those throw, and all of them read as a broken diagram. So the
 * geometry is pinned here rather than checked by eye at one screen size.
 */

const COS30 = Math.cos(Math.PI / 6);
const SIN30 = Math.sin(Math.PI / 6);
const VIEWPORTS: [string, Viewport][] = [
  ["desktop", MAP_VIEWPORT_DESKTOP],
  ["compact", MAP_VIEWPORT_COMPACT],
];

const node = (key: string): MapNodeSpec => {
  const found = MAP_NODES.find((item) => item.key === key);
  if (!found) throw new Error(`no map node ${key}`);
  return found;
};

/** Every point in an "M x y L x y [L x y]" path, in order. */
function pathPoints(path: string): Point[] {
  const numbers = (path.match(/-?\d+(?:\.\d+)?/g) ?? []).map(Number);
  const points: Point[] = [];
  for (let i = 0; i + 1 < numbers.length; i += 2) {
    points.push({ x: numbers[i], y: numbers[i + 1] });
  }
  return points;
}

/** 0 when the segment runs along a ground axis; grows as it tilts off one. */
function offAxis(a: Point, b: Point): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  return Math.min(
    Math.abs(dx * SIN30 - dy * COS30),
    Math.abs(dx * SIN30 + dy * COS30),
  );
}

function anchors(vp: Viewport): Point[] {
  return MAP_NODES.map((spec) => nodeAnchor(spec, vp));
}

/** Half the screen width a node's box occupies, front corner to back corner. */
function boxHalfWidth(spec: MapNodeSpec, vp: Viewport): number {
  return ((spec.size.w + spec.size.d) / 2) * COS30 * vp.scale;
}

describe("project", () => {
  it("puts the origin cell on the viewport origin", () => {
    expect(project(0, 0, 0, MAP_VIEWPORT_DESKTOP)).toEqual({ x: 822, y: 304 });
  });

  // The one thing worth spelling out: a true isometric, not the 2:1 "isometric"
  // of pixel art. cos(30°) horizontally, sin(30°) vertically, on both axes.
  it("uses cos(30°) and sin(30°) on both ground axes", () => {
    const { scale, originX, originY } = MAP_VIEWPORT_DESKTOP;

    const alongX = project(1, 0, 0, MAP_VIEWPORT_DESKTOP);
    expect(alongX.x).toBeCloseTo(originX + COS30 * scale, 6);
    expect(alongX.y).toBeCloseTo(originY + SIN30 * scale, 6);

    const alongZ = project(0, 0, 1, MAP_VIEWPORT_DESKTOP);
    expect(alongZ.x).toBeCloseTo(originX - COS30 * scale, 6);
    expect(alongZ.y).toBeCloseTo(originY + SIN30 * scale, 6);
  });

  it("lifts +y up the screen", () => {
    const lifted = project(0, 1, 0, MAP_VIEWPORT_DESKTOP);
    expect(lifted.x).toBeCloseTo(MAP_VIEWPORT_DESKTOP.originX, 6);
    expect(lifted.y).toBeCloseTo(MAP_VIEWPORT_DESKTOP.originY - MAP_VIEWPORT_DESKTOP.scale, 6);
  });

  it("keeps equal x and z on the centre column", () => {
    for (const at of [-4, -1, 2.5, 7]) {
      expect(project(at, 0, at, MAP_VIEWPORT_DESKTOP).x).toBeCloseTo(822, 6);
    }
  });
});

describe("viewports", () => {
  it("keeps the measured desktop frame", () => {
    expect(MAP_VIEWPORT_DESKTOP).toEqual({
      width: 1240,
      height: 620,
      scale: 26,
      originX: 822,
      originY: 304,
    });
  });

  it("gives the compact frame a portrait aspect", () => {
    expect(MAP_VIEWPORT_COMPACT.width).toBeLessThanOrEqual(880);
    expect(MAP_VIEWPORT_COMPACT.height).toBeGreaterThan(MAP_VIEWPORT_COMPACT.width);
  });
});

describe("MAP_NODES", () => {
  // Derived from HERO_SOURCES rather than retyped, so the map cannot draw a
  // source the rest of the hero has never heard of.
  it("carries exactly the hero's four sources, in the hero's order", () => {
    expect(MAP_NODES).toHaveLength(4);
    expect(MAP_NODES.map((item) => item.key)).toEqual(
      HERO_SOURCES.map((source) => source.key),
    );
  });

  it("names each source the way the hero labels it", () => {
    for (const source of HERO_SOURCES) {
      expect(node(source.key).name).toBe(source.label.toUpperCase());
    }
  });

  it("gives each source its own silhouette, chip and platform badges", () => {
    expect(
      MAP_NODES.map((item) => [item.key, item.silhouette, item.chip, item.badges]),
    ).toEqual([
      ["cloud", "stepped", "BURST", ["googlecloud", "AWS"]],
      ["rented", "cards", "2 GPU · H100", ["nvidia"]],
      ["owned", "rack", "1 RACK · 4 GPU", ["ubuntu", "docker"]],
      ["everyday", "cluster", "12 NODES", ["apple", "windows", "linux"]],
    ]);
    expect(new Set(MAP_NODES.map((item) => item.silhouette)).size).toBe(4);
  });

  it("puts the four sources on the corners of a square around the core", () => {
    expect(CORE_CELL).toEqual({ x: 0, z: 0 });
    expect(MAP_NODES.map((item) => [item.cell.x, item.cell.z])).toEqual([
      [6.2, -6.2],
      [6.2, 6.2],
      [-6.2, -6.2],
      [-6.2, 6.2],
    ]);
  });

  it("loses the everyday machine and recovers on rented capacity", () => {
    expect(VICTIM_KEY).toBe("everyday");
    expect(RESCUER_KEY).toBe("rented");
    expect(VICTIM_KEY).not.toBe(RESCUER_KEY);
  });
});

describe("nodeAnchor and corePoint", () => {
  it.each(VIEWPORTS)("keeps sources 40px apart in the %s frame", (_name, vp) => {
    const points = anchors(vp);
    for (let i = 0; i < points.length; i++) {
      for (let j = i + 1; j < points.length; j++) {
        expect(Math.hypot(points[i].x - points[j].x, points[i].y - points[j].y)).toBeGreaterThan(40);
      }
    }
  });

  it.each(VIEWPORTS)("keeps every source box inside the %s frame", (_name, vp) => {
    for (const spec of MAP_NODES) {
      const anchor = nodeAnchor(spec, vp);
      const half = boxHalfWidth(spec, vp);
      expect(anchor.x - half).toBeGreaterThan(0);
      expect(anchor.x + half).toBeLessThan(vp.width);
      expect(anchor.y).toBeGreaterThan(0);
      expect(anchor.y).toBeLessThan(vp.height);
    }
  });

  it("draws the diamond wide on desktop and the stack tall when compact", () => {
    const spread = (vp: Viewport) => {
      const points = anchors(vp);
      const xs = points.map((point) => point.x);
      const ys = points.map((point) => point.y);
      return (Math.max(...xs) - Math.min(...xs)) / (Math.max(...ys) - Math.min(...ys));
    };

    // An isometric diamond is always ~1.73 : 1. A compact frame that merely
    // scaled it would land on the same ratio; a stack cannot.
    expect(spread(MAP_VIEWPORT_DESKTOP)).toBeGreaterThan(1.5);
    expect(spread(MAP_VIEWPORT_COMPACT)).toBeLessThan(0.9);
  });

  it("anchors each source at mid-height of its own box", () => {
    const spec = node("owned");
    expect(nodeAnchor(spec, MAP_VIEWPORT_DESKTOP)).toEqual(
      project(spec.cell.x, spec.size.h / 2, spec.cell.z, MAP_VIEWPORT_DESKTOP),
    );
  });

  it.each(VIEWPORTS)("holds the core on the centre column of the %s frame", (_name, vp) => {
    const core = corePoint(vp);
    expect(core.x).toBeCloseTo(vp.originX, 6);
    // Above the ground cell it sits on: routes meet the top of the plate stack.
    expect(core.y).toBeLessThan(project(0, 0, 0, vp).y);
  });
});

describe("elbowPath", () => {
  it("bends once, and both legs follow a ground axis", () => {
    for (const spec of MAP_NODES) {
      const path = elbowPath(corePoint(MAP_VIEWPORT_DESKTOP), nodeAnchor(spec, MAP_VIEWPORT_DESKTOP));
      const points = pathPoints(path);
      expect(points).toHaveLength(3);
      expect(offAxis(points[0], points[1])).toBeLessThan(0.05);
      expect(offAxis(points[1], points[2])).toBeLessThan(0.05);
    }
  });

  it("starts and ends exactly where it was told to", () => {
    const from = { x: 120.5, y: 90.25 };
    const to = { x: 400.75, y: 310.5 };
    const points = pathPoints(elbowPath(from, to));
    expect(points[0]).toEqual(from);
    expect(points[points.length - 1]).toEqual(to);
  });

  // The corner case the plan flagged: when the two ends already share an axis
  // one leg is zero long, and a "L" onto the point you are already standing on
  // is a rendering artefact waiting to happen.
  it("goes straight when the two ends already share an axis", () => {
    const from = { x: 300, y: 200 };
    for (const axis of [
      { x: COS30, y: SIN30 },
      { x: -COS30, y: SIN30 },
    ]) {
      for (const run of [180, -180]) {
        const to = { x: from.x + axis.x * run, y: from.y + axis.y * run };
        expect(pathPoints(elbowPath(from, to))).toHaveLength(2);
      }
    }
  });

  it("never emits a zero-length segment, at any bearing", () => {
    const from = { x: 500, y: 300 };
    for (let degrees = 0; degrees < 360; degrees += 5) {
      const radians = (degrees * Math.PI) / 180;
      const to = { x: from.x + Math.cos(radians) * 200, y: from.y + Math.sin(radians) * 200 };
      const points = pathPoints(elbowPath(from, to));
      expect(points.length).toBeGreaterThanOrEqual(2);
      for (let i = 1; i < points.length; i++) {
        expect(Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y)).toBeGreaterThan(0);
      }
    }
  });

  // Two routes leaving the core along the same line draw as one line — and
  // then a failed red route and a live orange one are indistinguishable.
  it.each(VIEWPORTS)("gives every route its own axis out of the core in the %s frame", (_name, vp) => {
    const core = corePoint(vp);
    const bearings = MAP_NODES.map((spec) => {
      const [, corner] = pathPoints(elbowPath(core, nodeAnchor(spec, vp)));
      return `${Math.sign(corner.x - core.x)}:${Math.sign(corner.y - core.y)}`;
    });
    expect(new Set(bearings).size).toBe(MAP_NODES.length);
  });

  it.each(VIEWPORTS)("keeps every route corner inside the %s frame", (_name, vp) => {
    const core = corePoint(vp);
    for (const spec of MAP_NODES) {
      for (const point of pathPoints(elbowPath(core, nodeAnchor(spec, vp)))) {
        expect(point.x).toBeGreaterThan(0);
        expect(point.x).toBeLessThan(vp.width);
        expect(point.y).toBeGreaterThan(0);
        expect(point.y).toBeLessThan(vp.height);
      }
    }
  });
});

describe("gridLines", () => {
  it.each(VIEWPORTS)("draws only ground-axis segments in the %s frame", (_name, vp) => {
    const lines = gridLines(vp);
    expect(lines.length).toBeGreaterThan(8);
    for (const line of lines) {
      const points = pathPoints(line);
      expect(points).toHaveLength(2);
      expect(offAxis(points[0], points[1])).toBeLessThan(0.05);
    }
  });

  it.each(VIEWPORTS)("keeps every grid line inside the %s frame", (_name, vp) => {
    for (const line of gridLines(vp)) {
      for (const point of pathPoints(line)) {
        expect(point.x).toBeGreaterThanOrEqual(0);
        expect(point.x).toBeLessThanOrEqual(vp.width);
        expect(point.y).toBeGreaterThanOrEqual(0);
        expect(point.y).toBeLessThanOrEqual(vp.height);
      }
    }
  });

  // Hardware standing on bare background is the "objects floating in void"
  // problem the grid exists to fix, so it has to reach past every source.
  it.each(VIEWPORTS)("reaches past every source in the %s frame", (_name, vp) => {
    const points = gridLines(vp).flatMap(pathPoints);
    const left = Math.min(...points.map((point) => point.x));
    const right = Math.max(...points.map((point) => point.x));
    const top = Math.min(...points.map((point) => point.y));
    const bottom = Math.max(...points.map((point) => point.y));

    for (const anchor of [...anchors(vp), corePoint(vp)]) {
      expect(anchor.x).toBeGreaterThanOrEqual(left);
      expect(anchor.x).toBeLessThanOrEqual(right);
      expect(anchor.y).toBeGreaterThanOrEqual(top);
      expect(anchor.y).toBeLessThanOrEqual(bottom);
    }
  });
});

describe("phaseForProgress", () => {
  it("walks the four beats in order", () => {
    expect(phaseForProgress(0)).toBe("running");
    expect(phaseForProgress(0.29)).toBe("running");
    expect(phaseForProgress(0.3)).toBe("lost");
    expect(phaseForProgress(0.49)).toBe("lost");
    expect(phaseForProgress(0.5)).toBe("resumed");
    expect(phaseForProgress(0.74)).toBe("resumed");
    expect(phaseForProgress(0.75)).toBe("accepted");
    expect(phaseForProgress(1)).toBe("accepted");
  });

  // Scroll containers overshoot on both ends on every rubber-band platform.
  it("clamps outside [0, 1] instead of leaving the map without a phase", () => {
    expect(phaseForProgress(-0.4)).toBe("running");
    expect(phaseForProgress(-1e6)).toBe("running");
    expect(phaseForProgress(1.4)).toBe("accepted");
    expect(phaseForProgress(1e6)).toBe("accepted");
    expect(phaseForProgress(Number.NEGATIVE_INFINITY)).toBe("running");
    expect(phaseForProgress(Number.POSITIVE_INFINITY)).toBe("accepted");
    expect(phaseForProgress(Number.NaN)).toBe("running");
  });

  it("is total across a full sweep", () => {
    const seen = new Set<MapPhase>();
    for (let progress = -2; progress <= 3; progress += 0.01) {
      const phase = phaseForProgress(progress);
      expect(["running", "lost", "resumed", "accepted"]).toContain(phase);
      seen.add(phase);
    }
    expect(seen.size).toBe(4);
  });
});

describe("routeStateFor", () => {
  it("fails the victim's route and lights the rescuer's", () => {
    expect(routeStateFor(VICTIM_KEY, "lost")).toBe("failed");
    expect(routeStateFor(RESCUER_KEY, "resumed")).toBe("active");
  });

  // Orange means one thing: the job's path. Exactly one route is active on
  // every beat, or the colour stops carrying the story.
  it("keeps exactly one route active on every beat", () => {
    for (const phase of ["running", "lost", "resumed", "accepted"] as const) {
      const active = MAP_NODES.filter((spec) => routeStateFor(spec.key, phase) === "active");
      expect(active.length).toBeLessThanOrEqual(1);
    }
    expect(routeStateFor(VICTIM_KEY, "running")).toBe("active");
    expect(routeStateFor(RESCUER_KEY, "accepted")).toBe("active");
  });

  it("leaves the dead node's route visible as history once work has moved", () => {
    expect(routeStateFor(VICTIM_KEY, "resumed")).toBe("dimmed");
    expect(routeStateFor(VICTIM_KEY, "accepted")).toBe("dimmed");
  });

  it("leaves the uninvolved sources idle throughout", () => {
    for (const key of ["cloud", "owned"] as const) {
      for (const phase of ["running", "lost", "resumed", "accepted"] as const) {
        expect(routeStateFor(key, phase)).toBe("idle");
      }
    }
  });
});

describe("readoutFor", () => {
  it("reads out the exact strip values for every beat", () => {
    expect(readoutFor("running")).toEqual({
      leases: 12,
      checkpoint: "4 / 7",
      lost: 0,
      goodput: 94,
      state: "RUNNING",
    });
    expect(readoutFor("lost")).toEqual({
      leases: 11,
      checkpoint: "4 / 7",
      lost: 1,
      goodput: 88,
      state: "NODE LOST",
    });
    expect(readoutFor("resumed")).toEqual({
      leases: 12,
      checkpoint: "5 / 7",
      lost: 1,
      goodput: 90,
      state: "RESUMED ELSEWHERE",
    });
    expect(readoutFor("accepted")).toEqual({
      leases: 12,
      checkpoint: "7 / 7",
      lost: 1,
      goodput: 94,
      state: "RESULT ACCEPTED",
    });
  });

  it("costs a lease and dents goodput when a node is lost", () => {
    expect(readoutFor("lost").lost).toBe(1);
    expect(readoutFor("lost").goodput).toBeLessThan(readoutFor("running").goodput);
    expect(readoutFor("lost").leases).toBeLessThan(readoutFor("running").leases);
  });

  // Attempted work is not accepted work: the loss stays on the board after
  // recovery, and the checkpoint counter never walks backwards.
  it("never forgets the loss or rewinds the checkpoint", () => {
    const order: MapPhase[] = ["running", "lost", "resumed", "accepted"];
    let previous = 0;
    for (const phase of order.slice(1)) {
      expect(readoutFor(phase).lost).toBe(1);
    }
    for (const phase of order) {
      const done = Number(readoutFor(phase).checkpoint.split(" / ")[0]);
      expect(done).toBeGreaterThanOrEqual(previous);
      previous = done;
    }
    expect(readoutFor("accepted").checkpoint).toBe("7 / 7");
  });
});

describe("module purity", () => {
  // Every export is called from a server render and from tests with no DOM.
  it("reads no browser globals and no clock", () => {
    const source = readFileSync(`${process.cwd()}/lib/coordinator-map.ts`, "utf8");
    expect(source).not.toMatch(/\bwindow\b/);
    expect(source).not.toMatch(/\bdocument\b/);
    expect(source).not.toMatch(/\bglobalThis\b/);
    expect(source).not.toMatch(/Date\.now/);
    expect(source).not.toMatch(/Math\.random/);
    expect(source).not.toMatch(/performance\./);
  });

  it("returns the same geometry every call", () => {
    const spec = node("everyday");
    expect(nodeAnchor(spec, MAP_VIEWPORT_DESKTOP)).toEqual(
      nodeAnchor(spec, MAP_VIEWPORT_DESKTOP),
    );
    expect(gridLines(MAP_VIEWPORT_COMPACT)).toEqual(gridLines(MAP_VIEWPORT_COMPACT));
  });
});
