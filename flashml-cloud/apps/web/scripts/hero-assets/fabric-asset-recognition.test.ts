import { readFile } from "node:fs/promises";

import { describe, expect, it } from "vitest";

import type {
  FabricBounds,
  FabricGlbInspection,
  FabricMaterialInspection,
} from "./inspect-fabric-glb.mjs";
import { inspectFabricGlb } from "./inspect-fabric-glb.mjs";

const ASSET_ROOT = new URL("../../public/models/hero/fabric/", import.meta.url);

async function inspectAsset(
  file: string,
  semantics: readonly string[],
): Promise<FabricGlbInspection> {
  return inspectFabricGlb(await readFile(new URL(file, ASSET_ROOT)), semantics);
}

function semanticBounds(
  inspected: FabricGlbInspection,
  semantic: string,
): FabricBounds {
  const bounds = inspected.semanticBounds[semantic];
  if (!bounds) throw new Error(`Missing semantic bounds for ${semantic}`);
  return bounds;
}

function centerXZ(bounds: FabricBounds): readonly [number, number] {
  return [
    (bounds.min[0] + bounds.max[0]) / 2,
    (bounds.min[2] + bounds.max[2]) / 2,
  ];
}

function distanceXZ(left: FabricBounds, right: FabricBounds): number {
  const [leftX, leftZ] = centerXZ(left);
  const [rightX, rightZ] = centerXZ(right);
  return Math.hypot(rightX - leftX, rightZ - leftZ);
}

function mobileGroundAxis(bounds: FabricBounds): number {
  const [x, z] = centerXZ(bounds);
  return x - z;
}

function height(bounds: FabricBounds): number {
  return bounds.max[1] - bounds.min[1];
}

function xGap(left: FabricBounds, right: FabricBounds): number {
  return right.min[0] - left.max[0];
}

function nodeForward(inspected: FabricGlbInspection, name: string) {
  const node = inspected.nodeTransforms.find((candidate) => candidate.name === name);
  if (!node?.forward) throw new Error(`Missing forward vector for ${name}`);
  return node.forward;
}

function inspectedMaterials(inspected: FabricGlbInspection): FabricMaterialInspection[] {
  const materials = Reflect.get(inspected, "materials");
  if (!Array.isArray(materials)) throw new Error("Missing inspected material metadata");
  return materials.filter((material): material is FabricMaterialInspection => {
    if (typeof material !== "object" || material === null) return false;
    const name = Reflect.get(material, "name");
    const baseColor = Reflect.get(material, "baseColor");
    const metalness = Reflect.get(material, "metalness");
    const roughness = Reflect.get(material, "roughness");
    return typeof name === "string"
      && Array.isArray(baseColor)
      && baseColor.length === 4
      && typeof metalness === "number"
      && typeof roughness === "number";
  });
}

describe("mobile-readable fabric asset authoring", () => {
  it("keeps four Everyday devices spatially separate and biases their visible faces toward the mobile camera", async () => {
    const semantics = [
      "EverydayLaptopAssembly",
      "EverydayWorkstationAssembly",
      "EverydayTowerAssembly",
      "EverydayHomeServerAssembly",
    ] as const;
    const inspected = await inspectAsset("everyday-machines.glb", semantics);
    const bounds = semantics.map((semantic) => semanticBounds(inspected, semantic));

    const pairDistances = bounds.flatMap((left, leftIndex) =>
      bounds.slice(leftIndex + 1).map((right) => distanceXZ(left, right)),
    );
    expect(Math.min(...pairDistances)).toBeGreaterThanOrEqual(1.4);
    const projectedSeparations = bounds.flatMap((left, leftIndex) =>
      bounds.slice(leftIndex + 1).map((right) =>
        Math.abs(mobileGroundAxis(right) - mobileGroundAxis(left)),
      ),
    );
    expect(Math.min(...projectedSeparations)).toBeGreaterThanOrEqual(0.95);

    for (const presentation of [
      "EverydayLaptopPresentation",
      "EverydayWorkstationPresentation",
      "EverydayTowerPresentation",
      "EverydayHomeServerPresentation",
    ]) {
      const forward = nodeForward(inspected, presentation);
      expect(forward[0]).toBeGreaterThanOrEqual(0.32);
      expect(forward[2]).toBeGreaterThanOrEqual(0.85);
    }
  });

  it("separates the Owned workstation from the rack and presents both front faces", async () => {
    const inspected = await inspectAsset("owned-infrastructure.glb", [
      "OwnedWorkstationAssembly",
      "OwnedRackAssembly",
    ]);
    const workstation = semanticBounds(inspected, "OwnedWorkstationAssembly");
    const rack = semanticBounds(inspected, "OwnedRackAssembly");

    expect(height(rack) - height(workstation)).toBeGreaterThanOrEqual(0.42);
    expect(distanceXZ(workstation, rack)).toBeGreaterThanOrEqual(1.45);
    expect(inspected.meshNames).toContain("Owned_Workstation_Fan_01");
    expect(inspected.meshNames).toContain("Owned_Workstation_Fan_02");

    for (const presentation of [
      "OwnedWorkstationPresentation",
      "OwnedRackPresentation",
    ]) {
      const forward = nodeForward(inspected, presentation);
      expect(forward[0]).toBeGreaterThanOrEqual(0.72);
      expect(forward[2]).toBeGreaterThanOrEqual(0.6);
    }
  });

  it("keeps three Cloud racks visibly separated while their authored faces turn toward the mobile camera", async () => {
    const semantics = [
      "CloudRackBank",
      "CloudRackAAssembly",
      "CloudRackBAssembly",
      "CloudRackCAssembly",
    ] as const;
    const inspected = await inspectAsset("cloud-hpc.glb", semantics);
    const rackA = semanticBounds(inspected, "CloudRackAAssembly");
    const rackB = semanticBounds(inspected, "CloudRackBAssembly");
    const rackC = semanticBounds(inspected, "CloudRackCAssembly");

    expect(xGap(rackA, rackB)).toBeGreaterThanOrEqual(0.22);
    expect(xGap(rackB, rackC)).toBeGreaterThanOrEqual(0.22);

    for (const presentation of [
      "CloudRackAPresentation",
      "CloudRackBPresentation",
      "CloudRackCPresentation",
    ]) {
      const forward = nodeForward(inspected, presentation);
      expect(forward[0]).toBeGreaterThanOrEqual(0.35);
      expect(forward[2]).toBeGreaterThanOrEqual(0.85);
    }
  });

  it("keeps affected exterior and front-face materials bright enough to separate from dark gaps", async () => {
    for (const [file, semantics] of [
      ["everyday-machines.glb", ["EverydayLaptopAssembly"]],
      ["owned-infrastructure.glb", ["OwnedRackAssembly"]],
      ["cloud-hpc.glb", ["CloudRackBank"]],
    ] as const) {
      const materials = inspectedMaterials(await inspectAsset(file, semantics));
      const exterior = materials.find((material) => material.name === "Mat_GraphiteMetal");
      const face = materials.find((material) => material.name === "Mat_InfrastructureFace");

      expect(exterior?.baseColor[0]).toBeGreaterThanOrEqual(0.075);
      expect(face?.baseColor[0]).toBeGreaterThanOrEqual(0.11);
      expect(face?.metalness).toBeLessThanOrEqual(0.5);
      expect(face?.roughness).toBeGreaterThanOrEqual(0.38);
    }

  });

  it("keeps Everyday screens visibly cool instead of black at landing scale", async () => {
    const everyday = await inspectAsset("everyday-machines.glb", []);
    const screen = inspectedMaterials(everyday)
      .find((material) => material.name === "Mat_MutedScreen");
    expect(screen?.baseColor[0]).toBeGreaterThanOrEqual(0.08);
  });

  it("binds the Everyday home-server shell to the visible graphite exterior", async () => {
    const everyday = await inspectAsset("everyday-machines.glb", [
      "EverydayHomeServerAssembly",
    ]);
    const meshMaterials = Reflect.get(everyday, "meshMaterials");
    if (typeof meshMaterials !== "object" || meshMaterials === null) {
      throw new Error("Missing inspected mesh material bindings");
    }
    expect(Reflect.get(meshMaterials, "Everyday_HomeServer")).toBe(
      "Mat_GraphiteMetal",
    );
  });
});
