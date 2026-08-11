"use client";

import { Canvas, useThree } from "@react-three/fiber";
import { Suspense, useCallback, useEffect, useMemo } from "react";
import * as THREE from "three";
import {
  createFabricInspectionOwner,
  getFabricCanvasConfig,
  getFabricRuntimeMode,
  type FabricInspectionTarget,
  type FabricRenderDecision,
} from "@/lib/hero-fabric";
import type { HeroJobStepKey, HeroSourceKey } from "@/lib/hero-story";
import { FabricCanvasBoundary } from "./FabricCanvasBoundary";
import { FabricFallback } from "./FabricFallback";
import { FabricHeroScene, FabricRuntime } from "./FabricHeroScene";

interface HeroFabricCanvasProps {
  selectedSource: HeroSourceKey;
  focusedSource: HeroSourceKey | null;
  jobStep: HeroJobStepKey;
  reducedMotion: boolean;
  documentVisible: boolean;
  fabricDecision: FabricRenderDecision | null;
  fabricEntranceComplete: boolean;
  fabricFailed: boolean;
  finePointer: boolean;
  storyPlaying: boolean;
  onFabricEntranceComplete: () => void;
  onFabricFailure: () => void;
  onSelectSource: (source: HeroSourceKey) => void;
}

function FabricContextLossGuard({ onFailure }: { onFailure: () => void }) {
  const gl = useThree((state) => state.gl);

  useEffect(() => {
    const canvas = gl.domElement;
    const handleContextLoss = (event: Event) => {
      event.preventDefault();
      onFailure();
    };

    canvas.addEventListener("webglcontextlost", handleContextLoss, false);
    return () => canvas.removeEventListener("webglcontextlost", handleContextLoss, false);
  }, [gl, onFailure]);

  return null;
}

function configureRenderer(gl: THREE.WebGLRenderer) {
  gl.outputColorSpace = THREE.SRGBColorSpace;
  gl.toneMapping = THREE.ACESFilmicToneMapping;
  gl.toneMappingExposure = 1.28;
}

export function HeroFabricCanvas(props: HeroFabricCanvasProps) {
  const onFabricFailure = props.onFabricFailure;
  const inspectionOwner = useMemo(
    () => typeof window === "undefined"
      ? null
      : createFabricInspectionOwner(window as unknown as FabricInspectionTarget),
    [],
  );
  const explicitFailure = props.fabricFailed || props.fabricDecision?.mode === "poster";
  const baseQuality = props.fabricDecision?.mode === "canvas"
    ? props.fabricDecision.quality
    : "static";
  const { quality, continuous, motionEnabled } = getFabricRuntimeMode({
    baseQuality,
    storyPlaying: props.storyPlaying,
    focusedSource: props.focusedSource,
    entranceCompleted: props.fabricEntranceComplete,
    focusMotionAllowed: props.finePointer,
    reducedMotion: props.reducedMotion,
    documentVisible: props.documentVisible,
  });
  const handleFailure = useCallback(() => {
    inspectionOwner?.clear();
    onFabricFailure();
  }, [inspectionOwner, onFabricFailure]);

  useEffect(() => () => inspectionOwner?.clear(), [inspectionOwner]);

  useEffect(() => {
    if (props.fabricDecision === null || explicitFailure) inspectionOwner?.clear();
  }, [explicitFailure, inspectionOwner, props.fabricDecision]);

  if (props.fabricDecision === null) {
    return (
      <FabricFallback reason="loading">
        <span>Source and story controls remain available beside this poster.</span>
      </FabricFallback>
    );
  }

  const canvasConfig = getFabricCanvasConfig({
    quality,
    continuous,
    finePointer: props.finePointer,
  });

  return (
    <FabricCanvasBoundary
      controls={<span>Source and story controls remain available beside this poster.</span>}
      failure={explicitFailure ? "webgl" : undefined}
      onFailure={handleFailure}
    >
      <Canvas
        dpr={canvasConfig.dpr}
        frameloop={canvasConfig.frameloop}
        shadows={canvasConfig.shadows}
        camera={{ position: [8.1, 6.6, 8.6], fov: 38, near: 0.1, far: 80 }}
        gl={{ antialias: quality !== "static", alpha: true, powerPreference: "high-performance" }}
        onCreated={({ camera, gl, scene }) => {
          configureRenderer(gl);
          inspectionOwner?.replace({ camera, renderer: gl, scene });
        }}
      >
        <FabricContextLossGuard onFailure={handleFailure} />
        <Suspense fallback={null}>
          <FabricRuntime
            continuous={continuous}
            motionEnabled={motionEnabled}
            entranceCompleted={props.fabricEntranceComplete}
            finePointer={props.finePointer}
            onEntranceComplete={props.onFabricEntranceComplete}
            quality={quality}
          >
            <FabricHeroScene
              selectedSource={props.selectedSource}
              focusedSource={props.focusedSource}
              jobStep={props.jobStep}
              reducedMotion={props.reducedMotion}
              onSelectSource={props.onSelectSource}
            />
          </FabricRuntime>
        </Suspense>
      </Canvas>
    </FabricCanvasBoundary>
  );
}
