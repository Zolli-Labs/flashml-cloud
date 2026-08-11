"use client";

import { useFrame } from "@react-three/fiber";
import { useGLTF } from "@react-three/drei";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { FABRIC_ASSET_URLS, type FabricQualityTier } from "@/lib/hero-fabric";
import type { HeroSourceKey } from "@/lib/hero-story";

interface FabricAssetProps {
  source: HeroSourceKey;
  quality: FabricQualityTier;
}

export interface BatchedFabricAsset {
  scene: THREE.Group;
  dispose: () => void;
}

type FabricAssetKey = HeroSourceKey | "controlPlane";

const ASSET_NAMES = {
  everyday: "EverydayMachinesAsset",
  owned: "OwnedInfrastructureAsset",
  rented: "RentedGpuAsset",
  cloud: "CloudHpcAsset",
  controlPlane: "FabricControlPlaneAsset",
} as const satisfies Record<FabricAssetKey, string>;

interface MaterialGeometryBucket {
  material: THREE.Material;
  geometries: THREE.BufferGeometry[];
}

export function batchFabricAssetMeshes(
  source: FabricAssetKey,
  loadedScene: THREE.Group,
  quality: FabricQualityTier,
  transformMaterial?: (material: THREE.Material) => void,
): BatchedFabricAsset {
  const scene = new THREE.Group();
  const buckets = new Map<string, MaterialGeometryBucket>();
  const materials: THREE.Material[] = [];
  const geometries: THREE.BufferGeometry[] = [];

  scene.name = ASSET_NAMES[source];
  loadedScene.updateWorldMatrix(true, true);
  const rootWorldInverse = loadedScene.matrixWorld.clone().invert();

  loadedScene.traverse((object) => {
    if (!(object instanceof THREE.Mesh)) return;
    if (Array.isArray(object.material)) {
      throw new Error(`Fabric asset ${source} must use one material per generated mesh.`);
    }

    const geometry = object.geometry.clone();
    const relativeMatrix = rootWorldInverse.clone().multiply(object.matrixWorld);
    geometry.applyMatrix4(relativeMatrix);

    const key = object.material.uuid;
    const bucket = buckets.get(key);
    if (bucket) {
      bucket.geometries.push(geometry);
    } else {
      buckets.set(key, { material: object.material, geometries: [geometry] });
    }
  });

  for (const [materialKey, bucket] of buckets) {
    const merged = bucket.geometries.length === 1
      ? bucket.geometries[0]
      : mergeGeometries(bucket.geometries, false);
    if (!merged) {
      for (const geometry of bucket.geometries) geometry.dispose();
      throw new Error(`Unable to batch ${source} material bucket ${materialKey}.`);
    }
    if (bucket.geometries.length > 1) {
      for (const geometry of bucket.geometries) geometry.dispose();
    }

    merged.computeBoundingBox();
    merged.computeBoundingSphere();
    geometries.push(merged);

    const material = bucket.material.clone();
    material.name = `${bucket.material.name || "FabricMaterial"}_${source}`;
    if (material instanceof THREE.MeshStandardMaterial) material.envMapIntensity = 0.72;
    transformMaterial?.(material);
    materials.push(material);

    const mesh = new THREE.Mesh(merged, material);
    mesh.name = `${ASSET_NAMES[source]}_${bucket.material.name || materialKey}`;
    mesh.castShadow = quality === "high";
    mesh.receiveShadow = true;
    scene.add(mesh);
  }

  return {
    scene,
    dispose: () => {
      for (const geometry of geometries) geometry.dispose();
      for (const material of materials) material.dispose();
    },
  };
}

export function FabricAsset({ source, quality }: FabricAssetProps) {
  const microMotion = useRef<THREE.Group>(null);
  const { scene: loadedScene } = useGLTF(FABRIC_ASSET_URLS[source]);
  const asset = useMemo(
    () => batchFabricAssetMeshes(source, loadedScene, quality),
    [loadedScene, quality, source],
  );

  useEffect(() => () => asset.dispose(), [asset]);

  useFrame(({ clock }) => {
    if (!microMotion.current) return;
    microMotion.current.position.y =
      quality === "high" ? Math.sin(clock.elapsedTime * 0.72 + source.length) * 0.012 : 0;
  });

  return (
    <group ref={microMotion}>
      <primitive object={asset.scene} />
    </group>
  );
}
