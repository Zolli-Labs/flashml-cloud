"use client";

import { useGLTF } from "@react-three/drei";
import { Select } from "@react-three/postprocessing";
import { useEffect, useLayoutEffect, useMemo, useRef, type ReactNode } from "react";
import * as THREE from "three";
import {
  FABRIC_ASSET_URLS,
  FABRIC_ROUTE_POINTS,
  type FabricQualityTier,
} from "@/lib/hero-fabric";
import type { HeroSourceKey } from "@/lib/hero-story";
import { batchFabricAssetMeshes } from "./FabricAsset";

interface FabricControlPlaneProps {
  quality: FabricQualityTier;
  accepted: boolean;
  bloomEnabled: boolean;
  children?: ReactNode;
}

const SOURCE_ORDER = ["everyday", "owned", "rented", "cloud"] as const;

const SOCKET_NAMES = {
  everyday: "EverydaySocket",
  owned: "OwnedSocket",
  rented: "RentedSocket",
  cloud: "CloudSocket",
} as const satisfies Record<HeroSourceKey, string>;

function buildDisplayGrid() {
  const vertices: number[] = [];
  for (let column = 0; column <= 6; column += 1) {
    const x = -0.54 + column * 0.18;
    vertices.push(x, -0.16, 0, x, 0.16, 0);
  }
  for (let row = 0; row <= 4; row += 1) {
    const y = -0.16 + row * 0.08;
    vertices.push(-0.54, y, 0, 0.54, y, 0);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.name = "ControlPlaneGridGeometry";
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(vertices, 3));
  return geometry;
}

function ControlPlaneSocket({ source }: { source: HeroSourceKey }) {
  const position = FABRIC_ROUTE_POINTS[source].controlPlaneSocket;

  return (
    <group name={SOCKET_NAMES[source]} position={position}>
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[0.075, 0.018, 8, 20]} />
        <meshStandardMaterial
          color="#394142"
          emissive="#f36b32"
          emissiveIntensity={0.16}
          metalness={0.52}
          roughness={0.34}
        />
      </mesh>
      <mesh>
        <sphereGeometry args={[0.036, 12, 12]} />
        <meshBasicMaterial color="#d9d6cc" toneMapped={false} />
      </mesh>
    </group>
  );
}

function JobCells({ quality }: { quality: FabricQualityTier }) {
  const cells = quality === "high" ? 12 : quality === "balanced" ? 8 : 6;
  const mesh = useRef<THREE.InstancedMesh>(null);
  const geometry = useMemo(() => new THREE.BoxGeometry(0.15, 0.035, 0.012), []);
  const material = useMemo(
    () => new THREE.MeshBasicMaterial({
      color: "#f36b32",
      transparent: true,
      opacity: 0.72,
      toneMapped: false,
    }),
    [],
  );

  useLayoutEffect(() => {
    if (!mesh.current) return;
    const matrix = new THREE.Matrix4();
    for (let index = 0; index < cells; index += 1) {
      const column = index % 4;
      const row = Math.floor(index / 4);
      matrix.makeTranslation(-0.39 + column * 0.26, 0.38 + row * 0.1, 0.804);
      matrix.scale(new THREE.Vector3(index === 5 ? 1.28 : 1, 1, 1));
      mesh.current.setMatrixAt(index, matrix);
    }
    mesh.current.instanceMatrix.needsUpdate = true;
  }, [cells]);

  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
    },
    [geometry, material],
  );

  return (
    <instancedMesh
      ref={mesh}
      name="ControlPlaneJobCells"
      args={[geometry, material, cells]}
    />
  );
}

export function FabricControlPlane({
  quality,
  accepted,
  bloomEnabled,
  children,
}: FabricControlPlaneProps) {
  const { scene: loadedScene } = useGLTF(FABRIC_ASSET_URLS.controlPlane);
  const asset = useMemo(
    () => batchFabricAssetMeshes(
      "controlPlane",
      loadedScene,
      quality,
      (material) => {
        if (
          !accepted
          && material instanceof THREE.MeshStandardMaterial
          && material.name.includes("VerifiedGreen")
        ) {
          material.color.set("#d9682d");
          material.emissive.set("#f36b32");
        }
      },
    ),
    [accepted, loadedScene, quality],
  );
  const gridGeometry = useMemo(() => buildDisplayGrid(), []);

  useLayoutEffect(
    () => () => {
      asset.dispose();
      gridGeometry.dispose();
    },
    [asset, gridGeometry],
  );

  return (
    <group name="ZolliControlPlane">
      <primitive object={asset.scene} scale={1.31} />
      <group name="ControlPlaneDisplayOverlay" scale={1.31 / 1.18}>
        <lineSegments
          name="ControlPlaneEmissiveGrid"
          geometry={gridGeometry}
          position={[0, 0.48, 0.79]}
        >
          <lineBasicMaterial
            color="#f36b32"
            transparent
            opacity={quality === "high" ? 0.38 : 0.26}
            toneMapped={false}
          />
        </lineSegments>
        <Select enabled={bloomEnabled}>
          <JobCells quality={quality} />
          {children}
        </Select>
      </group>
      {SOURCE_ORDER.map((source) => (
        <ControlPlaneSocket key={source} source={source} />
      ))}
    </group>
  );
}
