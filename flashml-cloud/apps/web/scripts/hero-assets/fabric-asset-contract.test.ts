import { describe, expect, it } from "vitest";

import { validateFabricManifest } from "./fabric-asset-contract.mjs";
import type {
  FabricBounds,
  FabricGlbInspection,
  FabricVector3,
} from "./inspect-fabric-glb.mjs";

const COMPLETE_MANIFEST = {
  schemaVersion: 2,
  licenseFile: "LICENSES.md",
  totalByteLength: 1_500_000,
  assets: [
    {
      key: "everyday",
      file: "everyday-machines.glb",
      ownership: "first-party",
      byteLength: 300_000,
      triangleCount: 4_200,
      meshNames: [
        "Everyday_Laptop",
        "Everyday_Workstation",
        "Everyday_Tower",
        "Everyday_HomeServer",
      ],
      boundingBox: { min: [-1.8, 0, -1.1], max: [1.8, 1.4, 1.1] },
      semanticBounds: {
        EverydayLaptopAssembly: { min: [-1.8, 0, 0], max: [-0.8, 0.8, 1] },
        EverydayWorkstationAssembly: { min: [-0.5, 0, 0], max: [0.5, 1.4, 1] },
        EverydayTowerAssembly: { min: [0.8, 0, 0], max: [1.4, 1.2, 0.8] },
        EverydayHomeServerAssembly: { min: [-0.5, 0, -1.1], max: [0.5, 0.5, -0.4] },
      },
      forward: [0, 0, 1],
      sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    },
    {
      key: "owned",
      file: "owned-infrastructure.glb",
      ownership: "first-party",
      byteLength: 300_000,
      triangleCount: 3_100,
      meshNames: [
        "Owned_Workstation",
        "Owned_Rack",
        "Owned_Workstation_GPU_Bay_01",
        "Owned_Workstation_GPU_Bay_02",
        "Owned_Workstation_Fan_01",
        "Owned_Workstation_Fan_02",
        "Owned_Rack_LeftRail",
        "Owned_Rack_RightRail",
        "Owned_Rack_Bay_01",
        "Owned_Rack_Bay_02",
        "Owned_Rack_Bay_03",
        "Owned_Rack_Bay_04",
        "Owned_Rack_Bay_05",
        "Owned_Rack_Bay_06",
      ],
      boundingBox: { min: [-1.3, 0, -0.9], max: [1.3, 1.8, 0.9] },
      semanticBounds: {
        OwnedWorkstationAssembly: { min: [-1.3, 0, -0.5], max: [-0.3, 1.4, 0.5] },
        OwnedRackAssembly: { min: [0.3, 0, -0.5], max: [1.3, 1.6, 0.5] },
      },
      forward: [0, 0, 1],
      sha256: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    },
    {
      key: "rented",
      file: "rented-gpu.glb",
      ownership: "first-party",
      byteLength: 300_000,
      triangleCount: 3_800,
      meshNames: [
        "Rented_GPU_Sled",
        "Rented_GPU_Chassis_A",
        "Rented_GPU_Chassis_B",
        "Rented_GPU_Chassis_A_Fan_01",
        "Rented_GPU_Chassis_A_Fan_02",
        "Rented_GPU_Chassis_A_Fan_03",
        "Rented_GPU_Chassis_B_Fan_01",
        "Rented_GPU_Chassis_B_Fan_02",
        "Rented_GPU_Chassis_B_Fan_03",
        "Rented_GPU_ProviderPlate",
        "Rented_GPU_Interconnect",
      ],
      boundingBox: { min: [-1.3, 0, -0.9], max: [1.3, 1.7, 0.9] },
      semanticBounds: {
        RentedGPUAssembly: { min: [-1.2, 0, -0.5], max: [1.2, 1, 0.5] },
      },
      forward: [0, 0, 1],
      sha256: "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
    },
    {
      key: "cloud",
      file: "cloud-hpc.glb",
      ownership: "first-party",
      byteLength: 300_000,
      triangleCount: 3_600,
      meshNames: [
        "Cloud_Rack_A_LeftRail",
        "Cloud_Rack_A_RightRail",
        "Cloud_Rack_A_Bay_01",
        "Cloud_Rack_A_Bay_02",
        "Cloud_Rack_A_Bay_03",
        "Cloud_Rack_A_Bay_04",
        "Cloud_Rack_A_Bay_05",
        "Cloud_Rack_A_Bay_06",
        "Cloud_Rack_A_Bay_07",
        "Cloud_Rack_A_Bay_08",
        "Cloud_Rack_B_LeftRail",
        "Cloud_Rack_B_RightRail",
        "Cloud_Rack_B_Bay_01",
        "Cloud_Rack_B_Bay_02",
        "Cloud_Rack_B_Bay_03",
        "Cloud_Rack_B_Bay_04",
        "Cloud_Rack_B_Bay_05",
        "Cloud_Rack_B_Bay_06",
        "Cloud_Rack_B_Bay_07",
        "Cloud_Rack_B_Bay_08",
        "Cloud_Rack_C_LeftRail",
        "Cloud_Rack_C_RightRail",
        "Cloud_Rack_C_Bay_01",
        "Cloud_Rack_C_Bay_02",
        "Cloud_Rack_C_Bay_03",
        "Cloud_Rack_C_Bay_04",
        "Cloud_Rack_C_Bay_05",
        "Cloud_Rack_C_Bay_06",
        "Cloud_Rack_C_Bay_07",
        "Cloud_Rack_C_Bay_08",
        "Cloud_HPC_TopologySpine",
      ],
      boundingBox: { min: [-1.5, 0, -0.9], max: [1.5, 2.4, 0.9] },
      semanticBounds: {
        CloudRackBank: { min: [-1.5, 0, -0.5], max: [1.5, 2.4, 0.5] },
        CloudRackAAssembly: { min: [-1.5, 0, -0.5], max: [-0.5, 2.2, 0.5] },
        CloudRackBAssembly: { min: [-0.5, 0, -0.5], max: [0.5, 2.2, 0.5] },
        CloudRackCAssembly: { min: [0.5, 0, -0.5], max: [1.5, 2.2, 0.5] },
      },
      forward: [0, 0, 1],
      sha256: "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
    },
    {
      key: "controlPlane",
      file: "control-plane.glb",
      ownership: "first-party",
      byteLength: 300_000,
      triangleCount: 2_900,
      meshNames: [
        "FabricControlPlane_Chassis",
        "FabricControlPlane_DisplayGlass",
        "FabricControlPlane_DisplayRecess",
        "FabricControlPlane_Port_1",
        "FabricControlPlane_Port_2",
        "FabricControlPlane_Port_3",
        "FabricControlPlane_Port_4",
      ],
      boundingBox: { min: [-1.1, 0, -0.8], max: [1.1, 1.2, 0.8] },
      semanticBounds: {
        FabricControlPlane_Chassis: { min: [-0.9, 0, -0.6], max: [0.9, 0.9, 0.6] },
      },
      forward: [0, 0, 1],
      sha256: "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    },
  ],
};

function fixtureVector(values: readonly number[]): FabricVector3 {
  if (values.length !== 3) throw new Error("Expected a three-component vector");
  const [x, y, z] = values;
  return [x, y, z];
}

function fixtureBounds(bounds: {
  min: readonly number[];
  max: readonly number[];
}): FabricBounds {
  return {
    min: fixtureVector(bounds.min),
    max: fixtureVector(bounds.max),
  };
}

function inspectedFixture(asset: (
  typeof COMPLETE_MANIFEST.assets
)[number]): FabricGlbInspection {
  const semantics = Object.keys(asset.semanticBounds);
  const semanticBounds: Record<string, FabricBounds> = {};
  const semanticForwards: Record<string, FabricVector3> = {};
  for (const [semantic, bounds] of Object.entries(asset.semanticBounds)) {
    if (!bounds) continue;
    semanticBounds[semantic] = fixtureBounds(bounds);
    semanticForwards[semantic] = [0, 0, 1];
  }
  return {
    nodeNames: [...semantics, ...asset.meshNames].sort(),
    meshNames: [...asset.meshNames],
    triangleCount: asset.triangleCount,
    boundingBox: fixtureBounds(asset.boundingBox),
    semanticBounds,
    semanticForwards,
    forward: [0, 0, 1],
    nodeTransforms: [],
    materials: [],
    meshMaterials: {},
  };
}

const COMPLETE_STATS = {
  files: Object.fromEntries(
    COMPLETE_MANIFEST.assets.map((asset) => [
      asset.file,
      {
        byteLength: asset.byteLength,
        sha256: asset.sha256,
        inspection: inspectedFixture(asset),
      },
    ]),
  ),
  licenseFiles: ["LICENSES.md"],
};

describe("fabric asset manifest validation", () => {
  it("accepts the complete schema-version-2 manifest within the byte budget", () => {
    expect(validateFabricManifest(COMPLETE_MANIFEST, COMPLETE_STATS)).toEqual({
      valid: true,
      failures: [],
    });
  });

  it("reports an optimized GLB triangle count that differs from its manifest", () => {
    const stats = structuredClone(COMPLETE_STATS);
    stats.files["rented-gpu.glb"].inspection.triangleCount = 3_799;

    expect(validateFabricManifest(COMPLETE_MANIFEST, stats).failures).toContainEqual({
      code: "artifact-triangle-count-mismatch",
      asset: "rented-gpu.glb",
      expected: 3_800,
      actual: 3_799,
    });
  });

  it("reports an asset whose ownership is not first-party", () => {
    const manifest = structuredClone(COMPLETE_MANIFEST);
    manifest.assets[2].ownership = "third-party";

    expect(validateFabricManifest(manifest, COMPLETE_STATS).failures).toContainEqual({
      code: "invalid-asset-ownership",
      asset: "rented-gpu.glb",
      expected: "first-party",
      actual: "third-party",
    });
  });

  it("reports a manifest aggregate that differs from measured GLB bytes", () => {
    const manifest = structuredClone(COMPLETE_MANIFEST);
    manifest.totalByteLength = 1;

    expect(validateFabricManifest(manifest, COMPLETE_STATS).failures).toContainEqual({
      code: "total-byte-length-mismatch",
      expected: 1,
      actual: 1_500_000,
    });
  });

  it("reports an optimized semantic root that does not face positive Z", () => {
    const stats = structuredClone(COMPLETE_STATS);
    stats.files["cloud-hpc.glb"].inspection.forward = [0, 0, -1];
    stats.files["cloud-hpc.glb"].inspection.semanticForwards.CloudRackBank = [
      0, 0, -1,
    ];

    expect(validateFabricManifest(COMPLETE_MANIFEST, stats).failures).toContainEqual({
      code: "invalid-artifact-forward-orientation",
      asset: "cloud-hpc.glb",
      semantic: "CloudRackBank",
      actual: [0, 0, -1],
    });
  });

  it("rejects a manifest using the obsolete schema version", () => {
    const manifest = structuredClone(COMPLETE_MANIFEST);
    manifest.schemaVersion = 1;

    expect(validateFabricManifest(manifest, COMPLETE_STATS).failures).toContainEqual({
      code: "unsupported-schema-version",
      expected: 2,
      actual: 1,
    });
  });

  it("reports a required semantic group when its bounds are absent", () => {
    const stats = structuredClone(COMPLETE_STATS);
    Reflect.deleteProperty(
      stats.files["rented-gpu.glb"].inspection.semanticBounds,
      "RentedGPUAssembly",
    );
    stats.files["rented-gpu.glb"].inspection.nodeNames = stats.files[
      "rented-gpu.glb"
    ].inspection.nodeNames.filter((node) => node !== "RentedGPUAssembly");

    expect(validateFabricManifest(COMPLETE_MANIFEST, stats).failures).toContainEqual({
      code: "missing-semantic-bounds",
      asset: "rented-gpu.glb",
      semantic: "RentedGPUAssembly",
    });
  });

  it("reports a rented GPU assembly whose width-to-height ratio is below 2.2", () => {
    const stats = structuredClone(COMPLETE_STATS);
    stats.files["rented-gpu.glb"].inspection.semanticBounds.RentedGPUAssembly = {
      min: [-0.9, 0, -0.5],
      max: [0.9, 1, 0.5],
    };

    expect(validateFabricManifest(COMPLETE_MANIFEST, stats).failures).toContainEqual({
      code: "silhouette-ratio-below-minimum",
      asset: "rented-gpu.glb",
      ratio: "RentedGPUAssembly:x/y",
      actual: 1.8,
      min: 2.2,
    });
  });

  it.each([
    [0, "non-positive"],
    [Number.NaN, "non-finite"],
    [Number.POSITIVE_INFINITY, "non-finite"],
  ] as const)(
    "rejects a rented GPU ratio with invalid denominator %s",
    (invalidHeight, reason) => {
      const manifest = structuredClone(COMPLETE_MANIFEST);
      const stats = structuredClone(COMPLETE_STATS);
      const manifestBounds = manifest.assets[2]?.semanticBounds.RentedGPUAssembly;
      const inspectedBounds =
        stats.files["rented-gpu.glb"].inspection.semanticBounds.RentedGPUAssembly;
      if (!manifestBounds || !inspectedBounds) {
        throw new Error("Rented GPU assembly fixture is missing");
      }
      manifestBounds.max[1] = invalidHeight;
      inspectedBounds.max[1] = invalidHeight;

      const result = validateFabricManifest(manifest, stats);

      expect(result.valid).toBe(false);
      expect(result.failures).toContainEqual({
        code: "invalid-silhouette-ratio-denominator",
        asset: "rented-gpu.glb",
        ratio: "RentedGPUAssembly:x/y",
        actual: invalidHeight,
        reason,
      });
    },
  );

  it("rejects a non-positive rented GPU ratio numerator as invalid, not merely below minimum", () => {
    const manifest = structuredClone(COMPLETE_MANIFEST);
    const stats = structuredClone(COMPLETE_STATS);
    const manifestBounds = manifest.assets[2]?.semanticBounds.RentedGPUAssembly;
    const inspectedBounds =
      stats.files["rented-gpu.glb"].inspection.semanticBounds.RentedGPUAssembly;
    if (!manifestBounds || !inspectedBounds) {
      throw new Error("Rented GPU assembly fixture is missing");
    }
    manifestBounds.max[0] = -1.2;
    inspectedBounds.max[0] = -1.2;

    const result = validateFabricManifest(manifest, stats);

    expect(result.valid).toBe(false);
    expect(result.failures).toContainEqual({
      code: "invalid-silhouette-ratio-numerator",
      asset: "rented-gpu.glb",
      ratio: "RentedGPUAssembly:x/y",
      actual: 0,
      reason: "non-positive",
    });
    expect(result.failures).not.toContainEqual(
      expect.objectContaining({
        code: "silhouette-ratio-below-minimum",
        asset: "rented-gpu.glb",
        ratio: "RentedGPUAssembly:x/y",
      }),
    );
  });

  it("reports a cloud rack bank whose height is less than 1.35 times the owned rack", () => {
    const stats = structuredClone(COMPLETE_STATS);
    const cloudRackBank =
      stats.files["cloud-hpc.glb"].inspection.semanticBounds.CloudRackBank;
    if (!cloudRackBank) throw new Error("Cloud rack bank fixture is missing");
    cloudRackBank.max[1] = 1.92;

    expect(validateFabricManifest(COMPLETE_MANIFEST, stats).failures).toContainEqual({
      code: "relative-silhouette-ratio-below-minimum",
      asset: "cloud-hpc.glb",
      ratio: "CloudRackBank:y/owned:OwnedRackAssembly:y",
      actual: 1.2,
      min: 1.35,
    });
  });

  it("reports a cloud rack bank whose width is less than 1.25 times the owned rack", () => {
    const stats = structuredClone(COMPLETE_STATS);
    stats.files["cloud-hpc.glb"].inspection.semanticBounds.CloudRackBank = {
      min: [-0.6, 0, -0.5],
      max: [0.6, 2.4, 0.5],
    };

    expect(validateFabricManifest(COMPLETE_MANIFEST, stats).failures).toContainEqual({
      code: "relative-silhouette-ratio-below-minimum",
      asset: "cloud-hpc.glb",
      ratio: "CloudRackBank:x/owned:OwnedRackAssembly:x",
      actual: 1.2,
      min: 1.25,
    });
  });

  it.each([
    [0, "non-positive"],
    [Number.NaN, "non-finite"],
    [Number.POSITIVE_INFINITY, "non-finite"],
  ] as const)(
    "rejects a cloud-to-owned cross-ratio with invalid subject %s",
    (invalidWidth, reason) => {
      const manifest = structuredClone(COMPLETE_MANIFEST);
      const stats = structuredClone(COMPLETE_STATS);
      const manifestBounds = manifest.assets[3]?.semanticBounds.CloudRackBank;
      const inspectedBounds =
        stats.files["cloud-hpc.glb"].inspection.semanticBounds.CloudRackBank;
      if (!manifestBounds || !inspectedBounds) {
        throw new Error("Cloud rack bank fixture is missing");
      }
      manifestBounds.max[0] = -1.5 + invalidWidth;
      inspectedBounds.max[0] = -1.5 + invalidWidth;

      const result = validateFabricManifest(manifest, stats);

      expect(result.valid).toBe(false);
      expect(result.failures).toContainEqual({
        code: "invalid-relative-silhouette-ratio-subject",
        asset: "cloud-hpc.glb",
        ratio: "CloudRackBank:x/owned:OwnedRackAssembly:x",
        actual: invalidWidth,
        reason,
      });
    },
  );

  it.each([
    [0, "non-positive"],
    [Number.NaN, "non-finite"],
    [Number.POSITIVE_INFINITY, "non-finite"],
  ] as const)(
    "rejects a cloud-to-owned cross-ratio with invalid reference %s",
    (invalidWidth, reason) => {
      const manifest = structuredClone(COMPLETE_MANIFEST);
      const stats = structuredClone(COMPLETE_STATS);
      const manifestBounds = manifest.assets[1]?.semanticBounds.OwnedRackAssembly;
      const inspectedBounds =
        stats.files["owned-infrastructure.glb"].inspection.semanticBounds
          .OwnedRackAssembly;
      if (!manifestBounds || !inspectedBounds) {
        throw new Error("Owned rack assembly fixture is missing");
      }
      manifestBounds.max[0] = 0.3 + invalidWidth;
      inspectedBounds.max[0] = 0.3 + invalidWidth;

      const result = validateFabricManifest(manifest, stats);

      expect(result.valid).toBe(false);
      expect(result.failures).toContainEqual({
        code: "invalid-relative-silhouette-ratio-reference",
        asset: "cloud-hpc.glb",
        ratio: "CloudRackBank:x/owned:OwnedRackAssembly:x",
        actual: invalidWidth,
        reason,
      });
    },
  );

  it("reports an asset that does not face positive Z", () => {
    const manifest = structuredClone(COMPLETE_MANIFEST);
    manifest.assets[3].forward = [0, 0, -1];

    expect(validateFabricManifest(manifest, COMPLETE_STATS).failures).toContainEqual({
      code: "invalid-forward-orientation",
      asset: "cloud-hpc.glb",
      actual: [0, 0, -1],
    });
  });

  it("reports the missing third cloud rack", () => {
    const stats = structuredClone(COMPLETE_STATS);
    stats.files["cloud-hpc.glb"].inspection.meshNames = stats.files[
      "cloud-hpc.glb"
    ].inspection.meshNames.filter(
      (mesh) => mesh !== "Cloud_Rack_C_LeftRail",
    );

    expect(validateFabricManifest(COMPLETE_MANIFEST, stats).failures).toContainEqual({
      code: "missing-required-mesh",
      asset: "cloud-hpc.glb",
      mesh: "Cloud_Rack_C_LeftRail",
    });
  });

  it("reports a missing owned workstation GPU bay", () => {
    const stats = structuredClone(COMPLETE_STATS);
    stats.files["owned-infrastructure.glb"].inspection.meshNames = stats.files[
      "owned-infrastructure.glb"
    ].inspection.meshNames.filter(
      (mesh) => mesh !== "Owned_Workstation_GPU_Bay_02",
    );

    expect(validateFabricManifest(COMPLETE_MANIFEST, stats).failures).toContainEqual({
      code: "missing-required-mesh",
      asset: "owned-infrastructure.glb",
      mesh: "Owned_Workstation_GPU_Bay_02",
    });
  });

  it("reports a missing rented GPU fan", () => {
    const stats = structuredClone(COMPLETE_STATS);
    stats.files["rented-gpu.glb"].inspection.meshNames = stats.files[
      "rented-gpu.glb"
    ].inspection.meshNames.filter(
      (mesh) => mesh !== "Rented_GPU_Chassis_A_Fan_03",
    );

    expect(validateFabricManifest(COMPLETE_MANIFEST, stats).failures).toContainEqual({
      code: "missing-required-mesh",
      asset: "rented-gpu.glb",
      mesh: "Rented_GPU_Chassis_A_Fan_03",
    });
  });

  it("reports the measured aggregate when the combined GLBs exceed 1,800,000 bytes", () => {
    const stats = structuredClone(COMPLETE_STATS);
    stats.files["control-plane.glb"].byteLength = 600_001;

    expect(validateFabricManifest(COMPLETE_MANIFEST, stats).failures).toContainEqual({
      code: "combined-budget-exceeded",
      actualBytes: 1_800_001,
      maxBytes: 1_800_000,
    });
  });

  it("reports absent first-party licensing evidence", () => {
    expect(
      validateFabricManifest(COMPLETE_MANIFEST, {
        ...COMPLETE_STATS,
        licenseFiles: [],
      }).failures,
    ).toContainEqual({
      code: "missing-license-file",
      file: "LICENSES.md",
    });
  });

  it("reports a required GLB missing from measured files", () => {
    const stats = structuredClone(COMPLETE_STATS);
    Reflect.deleteProperty(stats.files, "cloud-hpc.glb");

    expect(validateFabricManifest(COMPLETE_MANIFEST, stats).failures).toContainEqual({
      code: "missing-required-file",
      file: "cloud-hpc.glb",
    });
  });

  it("reports when a checked-in GLB hash differs from its manifest", () => {
    const stats = structuredClone(COMPLETE_STATS);
    stats.files["owned-infrastructure.glb"].sha256 =
      "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff";

    expect(validateFabricManifest(COMPLETE_MANIFEST, stats).failures).toContainEqual({
      code: "content-hash-mismatch",
      file: "owned-infrastructure.glb",
      expected: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      actual: "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    });
  });

  it("reports when a checked-in GLB size differs from its manifest", () => {
    const stats = structuredClone(COMPLETE_STATS);
    stats.files["everyday-machines.glb"].byteLength = 299_999;

    expect(validateFabricManifest(COMPLETE_MANIFEST, stats).failures).toContainEqual({
      code: "byte-length-mismatch",
      file: "everyday-machines.glb",
      expected: 300_000,
      actual: 299_999,
    });
  });

  it("reports a required asset omitted from the manifest", () => {
    const manifest = structuredClone(COMPLETE_MANIFEST);
    manifest.assets = manifest.assets.filter((asset) => asset.key !== "controlPlane");

    expect(validateFabricManifest(manifest, COMPLETE_STATS).failures).toContainEqual({
      code: "missing-manifest-asset",
      file: "control-plane.glb",
    });
  });

  it("reports an asset whose bounding box does not meet the island-contact origin", () => {
    const stats = structuredClone(COMPLETE_STATS);
    stats.files["cloud-hpc.glb"].inspection.boundingBox.min[1] = 0.5;

    expect(validateFabricManifest(COMPLETE_MANIFEST, stats).failures).toContainEqual({
      code: "invalid-contact-origin",
      file: "cloud-hpc.glb",
      minY: 0.5,
    });
  });
});
