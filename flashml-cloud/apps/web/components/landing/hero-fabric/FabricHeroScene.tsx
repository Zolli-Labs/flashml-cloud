"use client";

import { EffectComposer, Select, Selection, SelectiveBloom } from "@react-three/postprocessing";
import { createContext, useContext, useMemo, useRef, type ReactNode } from "react";
import * as THREE from "three";
import {
  FABRIC_SOURCE_LAYOUT,
  getFabricCanvasConfig,
  getFabricStorySnapshot,
  type FabricQualityTier,
} from "@/lib/hero-fabric";
import type { HeroJobStepKey, HeroSourceKey } from "@/lib/hero-story";
import { FabricCameraRig } from "./FabricCameraRig";
import { FabricControlPlane } from "./FabricControlPlane";
import { FabricExecutionRoutes } from "./FabricExecutionRoutes";
import { FabricInspectionBridge } from "./FabricInspectionBridge";
import { FabricIsland } from "./FabricIsland";
import { FabricLighting } from "./FabricLighting";
import { AcceptedMarker, CheckpointBeacon } from "./FabricStoryObjects";

interface FabricHeroSceneProps {
  selectedSource: HeroSourceKey;
  focusedSource: HeroSourceKey | null;
  jobStep: HeroJobStepKey;
  reducedMotion: boolean;
  onSelectSource: (source: HeroSourceKey) => void;
  quality?: FabricQualityTier;
}

interface FabricRuntimeState {
  continuous: boolean;
  motionEnabled: boolean;
  entranceCompleted: boolean;
  finePointer: boolean;
  onEntranceComplete: () => void;
  quality: FabricQualityTier;
}

const FabricRuntimeContext = createContext<FabricRuntimeState | null>(null);

export function FabricRuntime({
  continuous,
  motionEnabled,
  children,
  entranceCompleted,
  finePointer,
  onEntranceComplete,
  quality,
}: FabricRuntimeState & { children: ReactNode }) {
  const value = useMemo(
    () => ({
      continuous,
      motionEnabled,
      entranceCompleted,
      finePointer,
      onEntranceComplete,
      quality,
    }),
    [continuous, entranceCompleted, finePointer, motionEnabled, onEntranceComplete, quality],
  );

  return (
    <FabricRuntimeContext.Provider value={value}>
      {children}
    </FabricRuntimeContext.Provider>
  );
}

const SOURCES = Object.keys(FABRIC_SOURCE_LAYOUT) as HeroSourceKey[];

export function FabricHeroScene({
  selectedSource,
  focusedSource,
  jobStep,
  reducedMotion,
  onSelectSource,
  quality,
}: FabricHeroSceneProps) {
  const runtime = useContext(FabricRuntimeContext);
  const effectiveQuality = runtime?.quality ?? quality ?? (reducedMotion ? "static" : "high");
  const continuous = runtime?.continuous ?? !reducedMotion;
  const motionEnabled = runtime?.motionEnabled ?? continuous;
  const entranceCompleted = runtime?.entranceCompleted ?? reducedMotion;
  const finePointer = runtime?.finePointer ?? false;
  const onEntranceComplete = runtime?.onEntranceComplete ?? (() => undefined);
  const bloomLight = useRef<THREE.DirectionalLight>(null);
  const snapshot = getFabricStorySnapshot(jobStep);
  const config = getFabricCanvasConfig({
    quality: effectiveQuality,
    continuous,
    finePointer,
  });

  return (
    <FabricCameraRig
      continuous={continuous}
      motionEnabled={motionEnabled}
      entranceCompleted={entranceCompleted}
      finePointer={finePointer}
      focusedSource={focusedSource}
      onEntranceComplete={onEntranceComplete}
      quality={effectiveQuality}
    >
      <FabricInspectionBridge />
      <Selection enabled={config.postprocessing}>
        <group name="FabricHeroScene" scale={1} position={[0, -0.34, 0]}>
          <FabricLighting
            bloomLight={bloomLight}
            quality={effectiveQuality}
            shadowMapSize={config.shadowMapSize}
          />
          <Select enabled={config.postprocessing}>
            <FabricExecutionRoutes snapshot={snapshot} quality={effectiveQuality} />
          </Select>
          <FabricControlPlane
            quality={effectiveQuality}
            accepted={snapshot.acceptedVisible}
            bloomEnabled={config.postprocessing}
          >
            <CheckpointBeacon visible={snapshot.checkpointVisible} quality={effectiveQuality} />
          </FabricControlPlane>
          <Select enabled={config.postprocessing}>
            <AcceptedMarker visible={snapshot.acceptedVisible} />
          </Select>
          {SOURCES.map((source) => (
            <FabricIsland
              key={source}
              source={source}
              selected={selectedSource === source}
              continuous={continuous}
              motionEnabled={motionEnabled}
              focusedSource={focusedSource}
              quality={effectiveQuality}
              onSelectSource={onSelectSource}
            />
          ))}
        </group>
        {config.postprocessing ? (
          <EffectComposer
            enableNormalPass={false}
            multisampling={0}
            resolutionScale={config.bloomResolutionScale}
          >
            <SelectiveBloom
              lights={[bloomLight]}
              intensity={config.bloomIntensity}
              luminanceThreshold={0.72}
              luminanceSmoothing={config.bloomLuminanceSmoothing}
              mipmapBlur
              selectionLayer={10}
            />
          </EffectComposer>
        ) : null}
      </Selection>
    </FabricCameraRig>
  );
}
