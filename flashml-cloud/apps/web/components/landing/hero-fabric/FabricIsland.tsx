"use client";

import { RoundedBox } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import {
  FABRIC_SOURCE_LAYOUT,
  dampFabricFocusValue,
  getFabricFocusTransition,
  getFabricIslandEmphasis,
  isFabricFocusSettled,
  settleFabricSelectionValue,
  type FabricQualityTier,
} from "@/lib/hero-fabric";
import { getHeroSource, type HeroSourceKey } from "@/lib/hero-story";
import { FabricAsset } from "./FabricAsset";

interface FabricIslandProps {
  source: HeroSourceKey;
  selected: boolean;
  continuous: boolean;
  motionEnabled: boolean;
  focusedSource: HeroSourceKey | null;
  quality: FabricQualityTier;
  onSelectSource: (source: HeroSourceKey) => void;
}

const ISLAND_NAMES = {
  everyday: "EverydayIsland",
  owned: "OwnedIsland",
  rented: "RentedIsland",
  cloud: "CloudIsland",
} as const satisfies Record<HeroSourceKey, string>;

function IslandDecal({ source, platformHeight }: { source: HeroSourceKey; platformHeight: number }) {
  const texture = useMemo(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 512;
    canvas.height = 96;
    const context = canvas.getContext("2d");
    if (!context) return null;

    context.fillStyle = "rgba(12, 16, 17, 0.88)";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "#f36b32";
    context.fillRect(0, 0, 10, canvas.height);
    context.fillStyle = "#f2efe6";
    context.font = "600 28px ui-monospace, SFMono-Regular, Menlo, monospace";
    context.textBaseline = "middle";
    context.fillText(getHeroSource(source).label.toUpperCase(), 34, canvas.height / 2);

    const decal = new THREE.CanvasTexture(canvas);
    decal.name = `${ISLAND_NAMES[source]}Decal`;
    decal.colorSpace = THREE.SRGBColorSpace;
    decal.needsUpdate = true;
    return decal;
  }, [source]);

  useEffect(() => () => texture?.dispose(), [texture]);
  if (!texture) return null;

  return (
    <mesh
      name={`${ISLAND_NAMES[source]}Label`}
      position={[0, platformHeight / 2 + 0.012, 0.68]}
      rotation={[-Math.PI / 2, 0, 0]}
    >
      <planeGeometry args={[1.72, 0.32]} />
      <meshBasicMaterial map={texture} transparent toneMapped={false} />
    </mesh>
  );
}

export function FabricIsland({
  source,
  selected,
  continuous,
  motionEnabled,
  focusedSource,
  quality,
  onSelectSource,
}: FabricIslandProps) {
  const group = useRef<THREE.Group>(null);
  const platformMaterial = useRef<THREE.MeshStandardMaterial>(null);
  const layout = FABRIC_SOURCE_LAYOUT[source];
  const [width, height, depth] = layout.islandSize;
  const groupY = useRef<number>(layout.position[1]);
  const groupScale = useRef<number>(1);
  const targetY = useRef<number>(layout.position[1]);
  const targetScale = useRef<number>(1);
  const initialized = useRef(false);
  const { invalidate, size } = useThree();

  useLayoutEffect(() => {
    const viewportWidth = typeof window === "undefined" ? size.width : window.innerWidth;
    const emphasis = getFabricIslandEmphasis(source, focusedSource, viewportWidth);
    targetY.current = emphasis.y;
    targetScale.current = emphasis.scale;

    const transition = getFabricFocusTransition(quality, motionEnabled);
    if (!initialized.current || transition === "snap") {
      groupY.current = emphasis.y;
      groupScale.current = emphasis.scale;
      if (group.current) {
        group.current.position.y = emphasis.y;
        group.current.scale.setScalar(emphasis.scale);
      }
    }
    initialized.current = true;
    if (transition === "damp") invalidate();
  }, [focusedSource, invalidate, motionEnabled, quality, size.width, source]);

  useFrame((_, delta) => {
    if (!group.current || !platformMaterial.current) return;
    const targetGlow = selected ? 0.18 : 0.012;
    const transition = getFabricFocusTransition(quality, motionEnabled);
    groupY.current = transition === "damp"
      ? dampFabricFocusValue(groupY.current, targetY.current, delta)
      : targetY.current;
    groupScale.current = transition === "damp"
      ? dampFabricFocusValue(groupScale.current, targetScale.current, delta)
      : targetScale.current;
    group.current.position.y = groupY.current;
    group.current.scale.setScalar(groupScale.current);
    platformMaterial.current.emissiveIntensity = settleFabricSelectionValue(
      platformMaterial.current.emissiveIntensity,
      targetGlow,
      quality,
      continuous,
      delta,
    );
    if (
      transition === "damp"
      && (
        !isFabricFocusSettled(groupY.current, targetY.current)
        || !isFabricFocusSettled(groupScale.current, targetScale.current)
      )
    ) {
      invalidate();
    }
  });

  useEffect(
    () => () => {
      if (document.body.style.cursor === "pointer") document.body.style.cursor = "default";
    },
    [],
  );

  return (
    <group
      ref={group}
      name={ISLAND_NAMES[source]}
      position={layout.position}
      onClick={(event) => {
        event.stopPropagation();
        onSelectSource(source);
      }}
      onPointerOver={(event) => {
        event.stopPropagation();
        document.body.style.cursor = "pointer";
      }}
      onPointerOut={() => {
        document.body.style.cursor = "default";
      }}
    >
      <RoundedBox
        name={`${ISLAND_NAMES[source]}Platform`}
        args={[width, height, depth]}
        radius={0.12}
        smoothness={3}
        castShadow
        receiveShadow
      >
        <meshStandardMaterial
          ref={platformMaterial}
          color="#293031"
          emissive="#f36b32"
          emissiveIntensity={selected ? 0.18 : 0.012}
          metalness={0.34}
          roughness={0.54}
        />
      </RoundedBox>
      <mesh
        name={`${ISLAND_NAMES[source]}HitTarget`}
        position={[0, 0.72, 0]}
        renderOrder={-1}
      >
        <boxGeometry args={[width + 0.34, 1.72, depth + 0.34]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} colorWrite={false} />
      </mesh>
      <group name={`${ISLAND_NAMES[source]}AssetCluster`} position={[0, height / 2 + 0.035, 0]}>
        <FabricAsset source={source} quality={quality} />
      </group>
      <IslandDecal source={source} platformHeight={height} />
    </group>
  );
}
