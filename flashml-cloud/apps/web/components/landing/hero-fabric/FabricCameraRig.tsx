"use client";

/* R3F owns one imperative Three.js camera; useFrame is its supported mutation API. */
/* eslint-disable react-hooks/immutability */

import { useFrame, useThree } from "@react-three/fiber";
import { useCallback, useEffect, useLayoutEffect, useRef, type ReactNode } from "react";
import * as THREE from "three";
import {
  FABRIC_ENTRANCE_SECONDS,
  advanceFabricEntrance,
  createFabricEntranceState,
  dampFabricFocusValue,
  getFabricCameraPose,
  getFabricFocusTransition,
  getFabricParallaxTarget,
  isFabricFocusSettled,
  type FabricEntranceState,
  type FabricQualityTier,
} from "@/lib/hero-fabric";
import type { HeroSourceKey } from "@/lib/hero-story";

interface FabricCameraRigProps {
  continuous: boolean;
  motionEnabled: boolean;
  children: ReactNode;
  entranceCompleted: boolean;
  finePointer: boolean;
  focusedSource: HeroSourceKey | null;
  onEntranceComplete: () => void;
  quality: FabricQualityTier;
}

const ENTRANCE_OFFSET = new THREE.Vector3(0, 0.62, 0);

function dampVector3(
  current: THREE.Vector3,
  target: THREE.Vector3,
  deltaSeconds: number,
) {
  current.set(
    dampFabricFocusValue(current.x, target.x, deltaSeconds),
    dampFabricFocusValue(current.y, target.y, deltaSeconds),
    dampFabricFocusValue(current.z, target.z, deltaSeconds),
  );
}

export function FabricCameraRig({
  continuous,
  motionEnabled,
  children,
  entranceCompleted,
  finePointer,
  focusedSource,
  onEntranceComplete,
  quality,
}: FabricCameraRigProps) {
  const root = useRef<THREE.Group>(null);
  const entrance = useRef<FabricEntranceState>(
    createFabricEntranceState(quality, entranceCompleted),
  );
  const parallax = useRef({ pitch: 0, yaw: 0 });
  const entranceReported = useRef(entranceCompleted);
  const initialized = useRef(false);
  const cameraPosition = useRef(new THREE.Vector3());
  const cameraLookAt = useRef(new THREE.Vector3());
  const targetPosition = useRef(new THREE.Vector3());
  const targetLookAt = useRef(new THREE.Vector3());
  const entrancePosition = useRef(new THREE.Vector3());
  const targetFov = useRef(38);
  const { camera, invalidate, pointer, size } = useThree();

  const applyEntranceTarget = useCallback((state: FabricEntranceState) => {
    const progress = state.complete ? 1 : state.elapsedSeconds / FABRIC_ENTRANCE_SECONDS;
    const eased = 1 - Math.pow(1 - progress, 3);
    entrancePosition.current
      .copy(targetPosition.current)
      .multiplyScalar(1.08)
      .add(ENTRANCE_OFFSET)
      .lerp(targetPosition.current, eased);

    if (root.current) {
      root.current.position.y = THREE.MathUtils.lerp(-0.22, 0, eased);
      root.current.scale.setScalar(THREE.MathUtils.lerp(0.965, 1, eased));
    }
  }, []);

  useLayoutEffect(() => {
    const perspective = camera as THREE.PerspectiveCamera;
    const viewportWidth = typeof window === "undefined" ? size.width : window.innerWidth;
    const pose = getFabricCameraPose({ viewportWidth, focusedSource });
    targetPosition.current.set(...pose.position);
    targetLookAt.current.set(...pose.lookAt);
    targetFov.current = pose.fov;

    if (entranceCompleted && !entrance.current.complete) {
      entrance.current = createFabricEntranceState(quality, true);
    } else {
      entrance.current = advanceFabricEntrance(entrance.current, 0, quality);
    }
    applyEntranceTarget(entrance.current);

    const transition = getFabricFocusTransition(quality, motionEnabled);
    if (!initialized.current || transition === "snap") {
      cameraPosition.current.copy(entrancePosition.current);
      cameraLookAt.current.copy(targetLookAt.current);
      perspective.fov = targetFov.current;
      perspective.updateProjectionMatrix();
    }
    initialized.current = true;
    perspective.position.copy(cameraPosition.current);
    perspective.lookAt(cameraLookAt.current);
    parallax.current.pitch = 0;
    parallax.current.yaw = 0;
    if (!entrance.current.complete || transition === "damp") invalidate();
  }, [
    applyEntranceTarget,
    camera,
    continuous,
    entranceCompleted,
    focusedSource,
    invalidate,
    motionEnabled,
    quality,
    size.width,
  ]);

  useEffect(() => {
    if (entrance.current.complete && !entranceReported.current) {
      entranceReported.current = true;
      onEntranceComplete();
    }
  }, [onEntranceComplete, quality]);

  useFrame((_, delta) => {
    const perspective = camera as THREE.PerspectiveCamera;
    const wasComplete = entrance.current.complete;
    entrance.current = advanceFabricEntrance(entrance.current, delta, quality);
    applyEntranceTarget(entrance.current);
    if (entrance.current.complete && !wasComplete && !entranceReported.current) {
      entranceReported.current = true;
      onEntranceComplete();
    }

    const transition = getFabricFocusTransition(quality, motionEnabled);
    if (transition === "damp") {
      dampVector3(cameraPosition.current, entrancePosition.current, delta);
      dampVector3(cameraLookAt.current, targetLookAt.current, delta);
    } else {
      cameraPosition.current.copy(entrancePosition.current);
      cameraLookAt.current.copy(targetLookAt.current);
    }

    const nextFov = transition === "damp"
      ? dampFabricFocusValue(perspective.fov, targetFov.current, delta, 0.01)
      : targetFov.current;
    if (Math.abs(nextFov - perspective.fov) > Number.EPSILON) {
      perspective.fov = nextFov;
      perspective.updateProjectionMatrix();
    }
    perspective.position.copy(cameraPosition.current);

    const target = getFabricParallaxTarget(
      pointer.x,
      pointer.y,
      continuous && quality === "high" && finePointer,
    );
    if (continuous) {
      parallax.current.yaw = THREE.MathUtils.damp(parallax.current.yaw, target.yaw, 5, delta);
      parallax.current.pitch = THREE.MathUtils.damp(parallax.current.pitch, target.pitch, 5, delta);
    } else {
      parallax.current.pitch = 0;
      parallax.current.yaw = 0;
    }

    perspective.lookAt(cameraLookAt.current);
    perspective.rotation.y += parallax.current.yaw;
    perspective.rotation.x += parallax.current.pitch;

    const focusSettled = isFabricFocusSettled(
      cameraPosition.current.distanceTo(targetPosition.current),
      0,
    )
      && isFabricFocusSettled(cameraLookAt.current.distanceTo(targetLookAt.current), 0)
      && isFabricFocusSettled(perspective.fov, targetFov.current, 0.01);
    if (!entrance.current.complete || (transition === "damp" && !focusSettled)) {
      invalidate();
    }
  });

  return (
    <group ref={root} name="FabricCameraRig">
      {children}
    </group>
  );
}
