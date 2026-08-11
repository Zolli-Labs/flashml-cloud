import { describe, expect, it } from "vitest";
import * as THREE from "three";
import { batchFabricAssetMeshes } from "./FabricAsset";

function countMeshes(root: THREE.Object3D): THREE.Mesh[] {
  const meshes: THREE.Mesh[] = [];
  root.traverse((object) => {
    if (object instanceof THREE.Mesh) meshes.push(object);
  });
  return meshes;
}

describe("fabric asset draw-call batching", () => {
  it("merges repeated device parts by shared material and keeps Balanced assets out of the shadow pass", () => {
    const source = new THREE.Group();
    const graphite = new THREE.MeshStandardMaterial({ color: "#202425" });
    graphite.name = "Graphite";
    const accent = new THREE.MeshStandardMaterial({ color: "#f36b32" });
    accent.name = "Accent";

    const first = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), graphite);
    first.position.x = -1;
    const second = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), graphite);
    second.position.x = 1;
    const third = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.5, 0.5), accent);
    third.position.y = 1;
    source.add(first, second, third);

    const batched = batchFabricAssetMeshes("everyday", source, "balanced");
    const meshes = countMeshes(batched.scene);

    expect(meshes).toHaveLength(2);
    expect(meshes.map((mesh) => {
      expect(Array.isArray(mesh.material)).toBe(false);
      return Array.isArray(mesh.material) ? "" : mesh.material.name;
    }).sort()).toEqual([
      "Accent_everyday",
      "Graphite_everyday",
    ]);
    expect(meshes.every((mesh) => mesh.castShadow === false)).toBe(true);
    expect(meshes.every((mesh) => mesh.receiveShadow)).toBe(true);

    batched.dispose();
    graphite.dispose();
    accent.dispose();
  });

  it("retains one shadow caster per material bucket for High quality", () => {
    const source = new THREE.Group();
    const material = new THREE.MeshStandardMaterial({ color: "#202425" });
    material.name = "Graphite";
    source.add(
      new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), material),
      new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.5, 0.5), material),
    );

    const batched = batchFabricAssetMeshes("owned", source, "high");
    const meshes = countMeshes(batched.scene);

    expect(meshes).toHaveLength(1);
    expect(meshes[0]?.castShadow).toBe(true);

    batched.dispose();
    material.dispose();
  });

  it("batches the control-plane model and applies its accepted-state material transform once", () => {
    const source = new THREE.Group();
    const material = new THREE.MeshStandardMaterial({ color: "#4ba77b" });
    material.name = "VerifiedGreen";
    source.add(
      new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), material),
      new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.5, 0.5), material),
    );

    let transforms = 0;
    const batched = batchFabricAssetMeshes(
      "controlPlane",
      source,
      "balanced",
      (runtimeMaterial) => {
        transforms += 1;
        if (runtimeMaterial instanceof THREE.MeshStandardMaterial) {
          runtimeMaterial.color.set("#d9682d");
        }
      },
    );
    const meshes = countMeshes(batched.scene);

    expect(batched.scene.name).toBe("FabricControlPlaneAsset");
    expect(meshes).toHaveLength(1);
    expect(transforms).toBe(1);
    expect((meshes[0]?.material as THREE.MeshStandardMaterial).color.getHexString()).toBe("d9682d");

    batched.dispose();
    material.dispose();
  });
});
