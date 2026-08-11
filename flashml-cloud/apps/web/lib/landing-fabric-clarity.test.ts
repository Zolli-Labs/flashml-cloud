import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const componentRoot = new URL("../components/landing/hero-fabric/", import.meta.url);

const componentSource = (file: string) =>
  readFileSync(new URL(file, componentRoot), "utf8");

const sources = {
  camera: componentSource("FabricCameraRig.tsx"),
  controlPlane: componentSource("FabricControlPlane.tsx"),
  island: componentSource("FabricIsland.tsx"),
  lighting: componentSource("FabricLighting.tsx"),
  scene: componentSource("FabricHeroScene.tsx"),
} as const;

function openingTag(source: string, component: string): string {
  const match = source.match(new RegExp(`<${component}\\b[\\s\\S]*?>`));
  if (!match) throw new Error(`Missing <${component}> prop block`);
  return match[0];
}

function openingTagWith(source: string, component: string, marker: string): string {
  const tags = [...source.matchAll(new RegExp(`<${component}\\b[\\s\\S]*?>`, "g"))]
    .map((match) => match[0]);
  const tag = tags.find((candidate) => candidate.includes(marker));
  if (!tag) throw new Error(`Missing <${component}> prop block containing ${marker}`);
  return tag;
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

function hookBody(source: string, hook: "useFrame" | "useLayoutEffect"): string {
  const hookIndex = source.indexOf(`${hook}(`);
  if (hookIndex < 0) throw new Error(`Missing ${hook} hook`);
  const callbackIndex = source.indexOf("=>", hookIndex);
  const bodyIndex = source.indexOf("{", callbackIndex);
  return balancedBlock(source, bodyIndex, "{", "}");
}

function elementBody(source: string, component: string): string {
  const match = source.match(new RegExp(`<${component}\\b[^>]*>([\\s\\S]*?)</${component}>`));
  if (!match) throw new Error(`Missing <${component}> element body`);
  return match[1];
}

function selectionBodies(source: string): string[] {
  return [...source.matchAll(/<Select\b[^>]*>([\s\S]*?)<\/Select>/g)]
    .map((match) => match[1]);
}

describe("landing fabric clarity component wiring", () => {
  it("forwards focusedSource through the scene to the camera and every source island", () => {
    const scene = namedFunctionBody(sources.scene, "FabricHeroScene");
    expect(openingTag(scene, "FabricCameraRig")).toContain(
      "focusedSource={focusedSource}",
    );
    expect(openingTag(scene, "FabricIsland")).toContain(
      "focusedSource={focusedSource}",
    );
  });

  it("derives camera targets and transition mode from the shared focus contracts", () => {
    const cameraRig = namedFunctionBody(sources.camera, "FabricCameraRig");
    const layoutEffect = hookBody(cameraRig, "useLayoutEffect");
    const frame = hookBody(cameraRig, "useFrame");
    expect(layoutEffect).toMatch(/getFabricCameraPose\s*\(\s*\{/);
    expect(layoutEffect).toMatch(
      /getFabricFocusTransition\s*\(\s*quality\s*,\s*motionEnabled\s*\)/,
    );
    expect(frame).toMatch(
      /getFabricFocusTransition\s*\(\s*quality\s*,\s*motionEnabled\s*\)/,
    );
  });

  it("applies shared island emphasis to the complete island group pose", () => {
    const island = namedFunctionBody(sources.island, "FabricIsland");
    const layoutEffect = hookBody(island, "useLayoutEffect");
    const frame = hookBody(island, "useFrame");
    expect(layoutEffect).toMatch(
      /getFabricIslandEmphasis\s*\(\s*source\s*,\s*focusedSource\s*,/,
    );
    expect(frame).toContain(
      "group.current.position.y = groupY.current",
    );
    expect(frame).toContain(
      "group.current.scale.setScalar(groupScale.current)",
    );
  });

  it("feeds postprocessing from the effective Canvas configuration", () => {
    const scene = namedFunctionBody(sources.scene, "FabricHeroScene");
    expect(scene).toMatch(/getFabricCanvasConfig\s*\(\s*\{/);
    expect(openingTag(scene, "EffectComposer")).toContain(
      "resolutionScale={config.bloomResolutionScale}",
    );

    const bloom = openingTag(scene, "SelectiveBloom");
    expect(bloom).toContain("intensity={config.bloomIntensity}");
    expect(bloom).toContain(
      "luminanceSmoothing={config.bloomLuminanceSmoothing}",
    );
    expect(bloom).not.toMatch(/(?:intensity|luminanceSmoothing)=\{\d/);
  });

  it("keeps device bodies outside bloom while selecting only orchestration objects", () => {
    const scene = namedFunctionBody(sources.scene, "FabricHeroScene");
    const sceneSelections = selectionBodies(scene);
    expect(sceneSelections.some((body) => body.includes("<FabricIsland"))).toBe(false);
    expect(sceneSelections.some((body) => body.includes("<FabricControlPlane"))).toBe(false);
    for (const orchestrationObject of ["FabricExecutionRoutes", "AcceptedMarker"]) {
      expect(
        sceneSelections.some((body) => body.includes(`<${orchestrationObject}`)),
        `${orchestrationObject} should be inside a Select block`,
      ).toBe(true);
    }

    const controlPlaneTag = openingTag(scene, "FabricControlPlane");
    expect(controlPlaneTag).toContain("bloomEnabled={config.postprocessing}");
    expect(elementBody(scene, "FabricControlPlane")).toContain("<CheckpointBeacon");

    const controlPlane = namedFunctionBody(sources.controlPlane, "FabricControlPlane");
    const controlPlaneSelections = selectionBodies(controlPlane);
    expect(controlPlaneSelections.some((body) => body.includes("<primitive"))).toBe(false);
    expect(controlPlaneSelections.some((body) => body.includes("<JobCells"))).toBe(true);
    expect(controlPlaneSelections.some((body) => body.includes("{children}"))).toBe(true);

    const jobCells = namedFunctionBody(sources.controlPlane, "JobCells");
    expect(openingTag(jobCells, "instancedMesh")).toContain(
      'name="ControlPlaneJobCells"',
    );
  });

  it("enlarges the control-plane GLB without changing its semantic group", () => {
    expect(openingTagWith(sources.controlPlane, "group", 'name="ZolliControlPlane"')).toContain(
      'name="ZolliControlPlane"',
    );
    const primitive = openingTag(sources.controlPlane, "primitive");
    const scale = Number(primitive.match(/\bscale=\{([\d.]+)\}/)?.[1]);
    expect(scale).toBeGreaterThanOrEqual(1.29);
    expect(scale).toBeLessThanOrEqual(1.36);
  });
});
