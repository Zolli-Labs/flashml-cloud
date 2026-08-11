"use client";

import { type RefObject } from "react";
import * as THREE from "three";
import {
  type FabricCanvasConfig,
  type FabricQualityTier,
} from "@/lib/hero-fabric";

interface FabricLightingProps {
  bloomLight: RefObject<THREE.DirectionalLight | null>;
  quality: FabricQualityTier;
  shadowMapSize: FabricCanvasConfig["shadowMapSize"];
}

export function FabricLighting({ bloomLight, quality, shadowMapSize }: FabricLightingProps) {
  const dynamicShadows = shadowMapSize > 0;

  return (
    <group name="FabricLighting">
      <ambientLight intensity={quality === "static" ? 0.88 : 0.68} color="#c9d0cb" />
      <hemisphereLight args={["#dbe6df", "#101415", quality === "high" ? 1.04 : 0.92]} />
      <directionalLight
        ref={bloomLight}
        name="FabricKeyLight"
        position={[5, 9, 6]}
        intensity={quality === "high" ? 2.18 : 1.9}
        color="#fff2dd"
        castShadow={dynamicShadows}
        shadow-mapSize-width={shadowMapSize}
        shadow-mapSize-height={shadowMapSize}
        shadow-camera-near={1}
        shadow-camera-far={28}
        shadow-camera-left={-7}
        shadow-camera-right={7}
        shadow-camera-top={7}
        shadow-camera-bottom={-7}
        shadow-bias={-0.00035}
      />
      <directionalLight
        name="FabricFillLight"
        position={[-6, 2, -4]}
        intensity={0.92}
        color="#739caa"
      />
      <spotLight
        name="FabricRimLight"
        position={[0, 4, -6]}
        intensity={1.45}
        color="#91b8c4"
        angle={0.42}
        penumbra={0.72}
      />
      {quality !== "static" ? (
        <pointLight
          position={[0, 2.5, 3]}
          intensity={quality === "high" ? 1.8 : 1.1}
          distance={9}
          color="#f36b32"
        />
      ) : null}
      <gridHelper
        args={[22, 44, "#253031", "#172021"]}
        position={[0, -0.96, 0]}
        material-opacity={quality === "static" ? 0.2 : 0.3}
        material-transparent
      />
    </group>
  );
}
