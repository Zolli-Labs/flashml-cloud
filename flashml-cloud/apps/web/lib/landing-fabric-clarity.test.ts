import { readFileSync, readdirSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { MAP_NODES } from "./coordinator-map";
import { PLATFORM_ICONS, PLATFORM_LABELS } from "./coordinator-map-icons";

/**
 * The map's components, checked at the seams rather than at the pixels.
 *
 * Every claim the hero makes is made by one component handing another a value
 * it did not invent: the frame, the beat, the route state, the highlight. None
 * of those wires shows up in a screenshot when it breaks — a map drawn from a
 * second copy of the projection, or a route dressed from a restated phase
 * table, renders perfectly and says the wrong thing. So the wiring is pinned
 * here, and `lib/coordinator-map.test.ts` pins the geometry it carries.
 */

const componentRoot = new URL("../components/landing/coordinator-map/", import.meta.url);

const componentSource = (file: string) =>
  readFileSync(new URL(file, componentRoot), "utf8");

const sources = {
  map: componentSource("CoordinatorMap.tsx"),
  core: componentSource("MapCore.tsx"),
  grid: componentSource("MapGrid.tsx"),
  node: componentSource("MapNode.tsx"),
  particles: componentSource("MapParticles.tsx"),
  readout: componentSource("MapReadout.tsx"),
  routes: componentSource("MapRoutes.tsx"),
  badge: componentSource("PlatformBadge.tsx"),
  iso: componentSource("iso.tsx"),
  story: componentSource("useMapStory.ts"),
} as const;

/** Every file in the map's directory, so a new component cannot quietly opt
 * itself out of the rules the rest of them keep. */
const MAP_FILES: readonly [string, string][] = readdirSync(componentRoot)
  .filter((file) => /\.tsx?$/.test(file) && !file.includes(".test."))
  .sort()
  .map((file) => [file, componentSource(file)]);

function openingTag(source: string, component: string): string {
  const match = source.match(new RegExp(`<${component}\\b[\\s\\S]*?>`));
  if (!match) throw new Error(`Missing <${component}> prop block`);
  return match[0];
}

function balancedBlock(
  source: string,
  openingIndex: number,
  openingCharacter: "(" | "{",
  closingCharacter: ")" | "}",
): string {
  let depth = 0;
  for (let index = openingIndex; index < source.length; index += 1) {
    if (source[index] === openingCharacter) depth += 1;
    if (source[index] !== closingCharacter) continue;
    depth -= 1;
    if (depth === 0) return source.slice(openingIndex + 1, index);
  }
  throw new Error(`Unclosed ${openingCharacter} block`);
}

function namedFunctionBody(source: string, name: string): string {
  const functionIndex = source.indexOf(`function ${name}`);
  if (functionIndex < 0) throw new Error(`Missing ${name} function`);
  const parametersIndex = source.indexOf("(", functionIndex);
  const parameters = balancedBlock(source, parametersIndex, "(", ")");
  const bodyIndex = source.indexOf("{", parametersIndex + parameters.length + 2);
  return balancedBlock(source, bodyIndex, "{", "}");
}

/** Order of appearance, so a painter's-order claim can be stated as a
 * comparison rather than as a fragile snapshot of the whole render. */
function drawnAt(source: string, marker: string): number {
  const index = source.indexOf(marker);
  if (index < 0) throw new Error(`Missing ${marker}`);
  return index;
}

/**
 * The isometric basis, re-derived. `cos(30°)` and `sin(30°)` belong to
 * `lib/coordinator-map.ts` and to nothing else; a component that computes its
 * own copy drifts from the module the moment a viewport changes.
 *
 * Deliberately narrower than "no trigonometry": `MapParticles` uses `atan2` to
 * point a token along the segment it is already travelling, and a sine to ease
 * its opacity. Neither touches the projection.
 */
const ISO_BASIS = /Math\.(?:cos|sin|tan)\s*\(\s*(?:Math\.PI\s*\/\s*6|30\b)|0\.866|1\.732|\bCOS30\b|\bSIN30\b/;

/** Colour arrives as a brand token or not at all. `#ffffff` is exempt in one
 * place and one place only: a mask's stops are luminance, not colour, and white
 * there means "keep this pixel". */
const RAW_COLOUR = /#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b|\brgba?\(/;

describe("coordinator map component wiring", () => {
  it("forwards the frame and the beat from the map to every layer", () => {
    const map = namedFunctionBody(sources.map, "CoordinatorMap");

    for (const layer of ["MapGrid", "MapRoutes", "MapNode", "MapParticles", "MapCore"]) {
      expect(openingTag(map, layer), layer).toContain("vp={viewport}");
    }
    for (const layer of ["MapRoutes", "MapNode", "MapParticles", "MapReadout"]) {
      expect(openingTag(map, layer), layer).toContain("phase={phase}");
    }
  });

  it("derives every route's state from the shared story contract", () => {
    const routes = namedFunctionBody(sources.routes, "MapRoutes");

    expect(routes).toMatch(/routeStateFor\s*\(\s*spec\.key\s*,\s*phase\s*\)/);
    expect(openingTag(namedFunctionBody(sources.map, "CoordinatorMap"), "MapNode")).toContain(
      "routeState={routeStateFor(item.id, phase)}",
    );
    // No component re-states the beat table. `routeStateFor` is the only place
    // that knows which machine dies and which one picks the work up.
    expect(sources.routes).not.toMatch(/"(?:running|lost|resumed|accepted)"/);
    expect(sources.particles).not.toMatch(/===\s*"(?:lost|accepted)"/);
  });

  it("reads the live strip out of the geometry module rather than restating it", () => {
    const readout = namedFunctionBody(sources.readout, "MapReadout");

    expect(readout).toMatch(/readoutFor\s*\(\s*phase\s*\)/);
    for (const cell of ["leases", "checkpoint", "lost", "goodput", "state"]) {
      expect(readout, cell).toContain(`readout.${cell}`);
    }
    expect(sources.readout).not.toMatch(/RUNNING|NODE LOST|RESUMED ELSEWHERE|RESULT ACCEPTED/);
  });

  it.each(MAP_FILES)("keeps the isometric basis out of %s", (_file, source) => {
    expect(source).not.toMatch(ISO_BASIS);
  });

  it.each(MAP_FILES)("takes every coordinate in %s from the geometry module", (_file, source) => {
    expect(source).toMatch(/from "(?:@\/lib\/coordinator-map(?:-icons)?|\.\/iso|\.\/[A-Z]\w+)"/);
  });

  it.each(MAP_FILES)("spends only brand tokens on colour in %s", (file, source) => {
    // The mask's white stops are the documented exception; see RAW_COLOUR.
    const colours = file === "MapGrid.tsx" ? source.replaceAll("#ffffff", "") : source;
    expect(colours).not.toMatch(RAW_COLOUR);
  });

  it("keeps every map colour in a brand token", () => {
    const palette = balancedBlock(
      sources.iso,
      sources.iso.indexOf("{", sources.iso.indexOf("export const MAP_COLORS")),
      "{",
      "}",
    );
    const values = [...palette.matchAll(/:\s*"([^"]+)"/g)].map(([, value]) => value);

    expect(values.length).toBeGreaterThanOrEqual(10);
    for (const value of values) expect(value).toMatch(/^var\(--z-[a-z-]+\)$/);
  });

  it("keeps orange for the job's path and nothing else", () => {
    // Hardware, ground, badges and the focus ring are all cool. This single
    // rule is most of the difference between the map and the art it replaces.
    for (const [name, source] of [
      ["node", sources.node],
      ["grid", sources.grid],
      ["badge", sources.badge],
    ] as const) {
      expect(source, name).not.toMatch(/MAP_COLORS\.job|--z-orange/);
    }

    // Where it is spent: the live route, the tokens on it, the lease sitting on
    // the coordinator's deck, and the strip cell that reports the move.
    expect(namedFunctionBody(sources.routes, "MapRoutes")).not.toContain("MAP_COLORS.job");
    expect(sources.routes).toMatch(/active:\s*\{\s*stroke:\s*MAP_COLORS\.job/);
    expect(sources.particles).toContain("MAP_COLORS.job");
    expect(sources.core).toContain("fill={MAP_COLORS.job}");
    expect(sources.readout).toContain("job: \"text-[var(--z-orange)]\"");
  });

  it.each(MAP_NODES.map((spec) => [spec.key, spec.silhouette]))(
    "builds the %s source from its own %s silhouette",
    (_key, silhouette) => {
      const drawn = namedFunctionBody(sources.node, "Silhouette");
      const builders: Record<string, string> = {
        cluster: "clusterVolumes",
        rack: "rackVolumes",
        cards: "cardVolumes",
        stepped: "steppedVolumes",
      };
      const builder = builders[silhouette];

      // Four sources, four builders, no shared cube. At map scale the shape has
      // to carry the recognition the label alone cannot.
      expect(drawn).toContain(`${builder}(size)`);
      // Sized from the spec's own footprint, so moving a source in the geometry
      // module moves its silhouette with it instead of leaving a stale box.
      expect(sources.node).toContain(`function ${builder}({ w, h, d }: Size)`);
      expect(new Set(Object.values(builders)).size).toBe(4);
    },
  );

  it("lifts, brightens and dims from one piece of highlight state", () => {
    const map = namedFunctionBody(sources.map, "CoordinatorMap");
    const node = openingTag(map, "MapNode");

    expect(map).toContain("useState<MapNodeKey | null>(null)");
    expect(node).toContain("lifted={highlight === item.id}");
    expect(node).toContain("muted={highlight !== null && highlight !== item.id}");
    expect(openingTag(map, "MapRoutes")).toContain("highlight={highlight}");

    // Pointer and keyboard feed the same state, so a keyboard reader gets
    // exactly the map a mouse reader gets rather than a silent approximation.
    const drawn = namedFunctionBody(sources.node, "MapNode");
    expect(drawn).toContain("onPointerEnter={() => onHighlight(spec.key)}");
    expect(drawn).toMatch(/onFocus=\{\(\)\s*=>\s*\{[\s\S]*?onHighlight\(spec\.key\)/);
    expect(drawn).toContain("onPointerLeave={() => onHighlight(null)}");
  });

  it("sorts solids far to near with the coordinator in the same queue", () => {
    const map = namedFunctionBody(sources.map, "CoordinatorMap");

    expect(map).toContain('kind: "core"');
    expect(map).toContain("items.push(");
    expect(map).toContain("items.sort((a, b) => a.ground - b.ground)");
    // Ground position, not the anchor: a taller machine is not a nearer one.
    expect(map).toMatch(/ground:\s*nodeAnchor\(spec, viewport\)\.y \+ \(spec\.size\.h \/ 2\)/);
  });

  it("draws routes under the solids and light over them", () => {
    const map = namedFunctionBody(sources.map, "CoordinatorMap");

    expect(drawnAt(map, "<MapGrid")).toBeLessThan(drawnAt(map, "<MapRoutes"));
    expect(drawnAt(map, "<MapRoutes")).toBeLessThan(drawnAt(map, "drawables.map"));
    expect(drawnAt(map, "drawables.map")).toBeLessThan(drawnAt(map, "<MapParticles"));
  });

  it("holds the checkpoint inside the coordinator instead of on top of it", () => {
    const core = namedFunctionBody(sources.core, "MapCore");

    // Three posts, then the held object, then the near post crossing in front
    // of it, then the deck. That crossing is what reads as "inside".
    expect(drawnAt(core, "posts.slice(0, 3)")).toBeLessThan(drawnAt(core, "{...CHECKPOINT}"));
    expect(drawnAt(core, "{...CHECKPOINT}")).toBeLessThan(drawnAt(core, "{...posts[3]}"));
    expect(drawnAt(core, "{...posts[3]}")).toBeLessThan(drawnAt(core, "{...DECK}"));
    expect(core).toContain("radialGradient");
  });

  it("emits the ground grid as one clipped path and fades it at the edges", () => {
    const grid = namedFunctionBody(sources.grid, "MapGrid");

    expect(grid).toMatch(/gridLines\(\{[^}]*\}\)\.join\(" "\)/);
    expect(grid.match(/<path\b/g) ?? []).toHaveLength(1);
    expect(grid).toContain("mask={`url(#${maskId})`}");
    // Built once per frame geometry: hovering a source re-renders the map and
    // the floor underneath it never changes.
    expect(grid).toContain("useMemo(");
  });

  it("starts no animation loop when the reader asked for stillness", () => {
    const particles = namedFunctionBody(sources.particles, "MapParticles");

    expect(particles).toMatch(/if \(reduced\) return <StaticParticles/);
    expect(namedFunctionBody(sources.particles, "StaticParticles")).not.toContain(
      "requestAnimationFrame",
    );
    // The one loop in the map, and every mention of it lives behind that
    // branch: the two that start it, and the one that cancels it on unmount.
    const animated = namedFunctionBody(sources.particles, "AnimatedParticles");
    expect(sources.particles.match(/\brequestAnimationFrame\(/g) ?? []).toHaveLength(2);
    expect(animated.match(/\brequestAnimationFrame\(/g) ?? []).toHaveLength(2);
    expect(animated).toContain("cancelAnimationFrame(raf)");
    expect(animated).toContain("if (paused) return");
  });

  it("walks tokens along the route polyline instead of cutting its corner", () => {
    const tracks = namedFunctionBody(sources.particles, "tracksFor");

    // Read back out of the very path string `elbowPath` emitted, so a token can
    // never disagree with the line it is supposed to be travelling on.
    expect(tracks).toMatch(/elbowPath\(core, nodeAnchor\(spec, vp\)\)\.match\(NUMBER\)/);
    expect(namedFunctionBody(sources.particles, "sample")).toContain("track.steps");
    expect(sources.particles).toContain("function sampleDirected");
  });

  it("pools tokens and rings instead of building nodes per frame", () => {
    const animated = namedFunctionBody(sources.particles, "AnimatedParticles");

    expect(sources.particles).toMatch(/const TOKEN_SLOTS = \d+/);
    expect(sources.particles).toMatch(/const RING_SLOTS = \d+/);
    expect(animated).toContain("freeTokens.pop()");
    expect(animated).toContain("freeRings.pop()");
    expect(animated).toContain("freeTokens.push(token.slot)");
    expect(animated).toContain("freeRings.push(ring.slot)");
  });

  it("keeps the running loop from restarting when the beat changes", () => {
    const animated = namedFunctionBody(sources.particles, "AnimatedParticles");

    // A beat change through a dependency would reset the clock and stutter
    // every token in flight, so the loop reads the phase through a ref.
    expect(animated).toContain("const phaseRef = useRef(phase)");
    expect(animated).toContain("phaseRef.current");
    expect(animated).toMatch(/\}, \[anchors, paused, tracks\]\)/);
  });

  it("names every source for a screen reader and puts it in the tab order", () => {
    const node = namedFunctionBody(sources.node, "MapNode");
    const map = namedFunctionBody(sources.map, "CoordinatorMap");

    expect(node).toContain('role="button"');
    expect(node).toContain("tabIndex={0}");
    expect(node).toContain("aria-label={`${spec.name}. ${spec.chip}.`}");
    expect(node).toMatch(/event\.key !== "Enter" && event\.key !== " "/);
    expect(openingTag(map, "svg")).toContain("aria-label={MAP_DESCRIPTION}");
    expect(sources.map).toMatch(/MAP_DESCRIPTION\s*=\s*\n?\s*"Isometric map/);

    // Decoration announces nothing. Only the group's own label and the four
    // sources inside it are reachable, so the map reads as five things rather
    // than as several hundred polygons.
    for (const [name, source] of [
      ["grid", sources.grid],
      ["routes", sources.routes],
      ["particles", sources.particles],
      ["core", sources.core],
    ] as const) {
      expect(source, name).toContain('aria-hidden="true"');
    }
    expect(node).toContain('<g aria-hidden="true"');
  });

  it.each(MAP_NODES.flatMap((spec) => spec.badges.map((badge) => [spec.key, badge])))(
    "resolves the %s source's %s badge to a mark or a type badge",
    (_key, badge) => {
      const slug = badge.toLowerCase();
      const mark = PLATFORM_ICONS[slug] ?? PLATFORM_LABELS[slug];

      expect(mark, slug).toBeTruthy();
      expect(mark.length, slug).toBeGreaterThan(0);
    },
  );

  it("falls back to type when a platform has no mark to draw", () => {
    const badge = namedFunctionBody(sources.badge, "PlatformBadge");

    // Windows, Microsoft and AWS were withdrawn from simple-icons at those
    // companies' request. A missing mark is a state, not a bug to route around.
    expect(badge).toContain("PLATFORM_ICONS[slug]");
    expect(badge).toContain("PLATFORM_LABELS[slug]");
    expect(badge).toContain("badge.toLowerCase()");
    expect(badge).toMatch(/icon \? \(/);
    expect(PLATFORM_ICONS.aws).toBeUndefined();
    expect(PLATFORM_LABELS.aws).toBe("AWS");
  });
});
