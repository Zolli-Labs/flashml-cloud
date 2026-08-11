import { readFile } from "node:fs/promises";

import { describe, expect, it } from "vitest";

import { inspectFabricGlb } from "./inspect-fabric-glb.mjs";

describe("optimized fabric GLB inspection", () => {
  it("derives geometry, transforms, semantic bounds, and forward from GLB bytes", async () => {
    const bytes = await readFile(
      new URL(
        "../../public/models/hero/fabric/rented-gpu.glb",
        import.meta.url,
      ),
    );

    const inspected = await inspectFabricGlb(bytes, ["RentedGPUAssembly"]);

    expect(inspected.nodeNames).toContain("RentedGPUAssembly");
    expect(inspected.meshNames).toContain("Rented_GPU_Chassis_A_Fan_03");
    expect(inspected.triangleCount).toBe(3_372);
    expect(inspected.boundingBox).toEqual({
      min: [-1.61, 0.000032, -0.459951],
      max: [1.61, 1.425019, 0.501],
    });
    expect(inspected.semanticBounds.RentedGPUAssembly).toEqual({
      min: [-1.61, 0.000032, -0.459951],
      max: [1.61, 1.425019, 0.501],
    });
    expect(inspected.forward).toEqual([0, 0, 1]);
    expect(inspected.semanticForwards.RentedGPUAssembly).toEqual([0, 0, 1]);
    expect(
      inspected.nodeTransforms.find(
        (node) => node.name === "Rented_GPU_Chassis_A",
      ),
    ).toEqual({
      name: "Rented_GPU_Chassis_A",
      worldMatrix: [
        1.52, 0, 0, 0,
        0, 1.52, 0, 0,
        0, 0, 1.52, 0,
        0, 0.39, 0, 1,
      ],
      forward: [0, 0, 1],
    });
  });
});
