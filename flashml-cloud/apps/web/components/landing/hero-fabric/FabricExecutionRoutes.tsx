"use client";

import { useFrame } from "@react-three/fiber";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import {
  getFabricPacketDirection,
  getFabricPacketProgress,
  getFabricRouteSegments,
  getFabricRoutePresentation,
  type FabricPoint3,
  type FabricPacketDirection,
  type FabricQualityTier,
  type FabricRouteMode,
  type FabricRouteSegment,
  type FabricStorySnapshot,
} from "@/lib/hero-fabric";

interface FabricExecutionRoutesProps {
  snapshot: FabricStorySnapshot;
  quality: FabricQualityTier;
}

function buildCurve(points: readonly FabricPoint3[]) {
  return new THREE.CatmullRomCurve3(
    points.map((point) => new THREE.Vector3(...point)),
    false,
    "catmullrom",
    0.24,
  );
}

function buildDashTexture() {
  const data = new Uint8Array([
    255, 255, 255, 255,
    255, 255, 255, 255,
    255, 255, 255, 255,
    255, 255, 255, 255,
    255, 255, 255, 0,
    255, 255, 255, 0,
    255, 255, 255, 0,
    255, 255, 255, 0,
  ]);
  const texture = new THREE.DataTexture(data, 8, 1, THREE.RGBAFormat);
  texture.name = "FailureRouteDashTexture";
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.ClampToEdgeWrapping;
  texture.repeat.set(12, 1);
  texture.needsUpdate = true;
  return texture;
}

function RoutePackets({
  curve,
  direction,
  mode,
  quality,
}: {
  curve: THREE.CatmullRomCurve3;
  direction: FabricPacketDirection;
  mode: FabricRouteMode;
  quality: FabricQualityTier;
}) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const count = quality === "high" ? 4 : quality === "balanced" ? 2 : 1;
  const moving = mode === "active" || mode === "verified";
  const presentation = getFabricRoutePresentation(mode);
  const geometry = useMemo(() => new THREE.ConeGeometry(0.055, 0.14, 8), []);
  const material = useMemo(
    () => new THREE.MeshBasicMaterial({
      color: mode === "verified" ? "#4ba77b" : "#f36b32",
      depthTest: presentation.depthTest,
      depthWrite: false,
      toneMapped: false,
    }),
    [mode, presentation.depthTest],
  );
  const elapsedSeconds = useRef(0);
  const scratch = useMemo(() => ({
    matrix: new THREE.Matrix4(),
    point: new THREE.Vector3(),
    rotation: new THREE.Quaternion(),
    scale: new THREE.Vector3(1, 1, 1),
    tangent: new THREE.Vector3(),
    up: new THREE.Vector3(0, 1, 0),
  }), []);
  const writePacketMatrices = useCallback((elapsed: number) => {
    if (!mesh.current) return;

    for (let index = 0; index < count; index += 1) {
      const animatedProgress = getFabricPacketProgress({
        offset: (index + 1) / (count + 1),
        elapsedSeconds: elapsed,
        quality,
      });
      const progress = direction === "reverse" ? 1 - animatedProgress : animatedProgress;
      curve.getPointAt(progress, scratch.point);
      curve.getTangentAt(progress, scratch.tangent).normalize();
      if (direction === "reverse") scratch.tangent.multiplyScalar(-1);
      scratch.rotation.setFromUnitVectors(scratch.up, scratch.tangent);
      scratch.matrix.compose(scratch.point, scratch.rotation, scratch.scale);
      mesh.current.setMatrixAt(index, scratch.matrix);
    }
    mesh.current.instanceMatrix.needsUpdate = true;
  }, [count, curve, direction, quality, scratch]);

  useLayoutEffect(() => {
    elapsedSeconds.current = 0;
    if (moving) writePacketMatrices(0);
  }, [moving, writePacketMatrices]);

  useFrame((_, delta) => {
    if (!moving || quality === "static") return;
    elapsedSeconds.current += Math.max(0, delta);
    writePacketMatrices(elapsedSeconds.current);
  });

  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
    },
    [geometry, material],
  );

  if (!moving) return null;

  return (
    <instancedMesh
      ref={mesh}
      name="RoutePackets"
      args={[geometry, material, count]}
      frustumCulled={false}
      renderOrder={presentation.renderOrder + 1}
    />
  );
}

function FailureGlyph({ curve }: { curve: THREE.CatmullRomCurve3 }) {
  const point = curve.getPointAt(0.2);
  point.y += 0.2;
  return (
    <group name="FailureBreak" position={point}>
      <mesh rotation={[Math.PI / 2, 0, Math.PI / 4]} renderOrder={31}>
        <boxGeometry args={[0.42, 0.075, 0.075]} />
        <meshBasicMaterial color="#ff554b" depthTest={false} depthWrite={false} toneMapped={false} />
      </mesh>
      <mesh rotation={[Math.PI / 2, 0, -Math.PI / 4]} renderOrder={31}>
        <boxGeometry args={[0.42, 0.075, 0.075]} />
        <meshBasicMaterial color="#ff554b" depthTest={false} depthWrite={false} toneMapped={false} />
      </mesh>
    </group>
  );
}

function ExecutionRoute({
  segment,
  quality,
}: {
  segment: FabricRouteSegment;
  quality: FabricQualityTier;
}) {
  const style = getFabricRoutePresentation(segment.mode);
  const packetDirection = getFabricPacketDirection(segment);
  const curve = useMemo(() => buildCurve(segment.points), [segment.points]);
  const dashTexture = useMemo(() => (segment.mode === "failed" ? buildDashTexture() : null), [segment.mode]);
  const name = segment.mode === "failed"
    ? "FailureBranch"
    : segment.kind === "verified-exit"
      ? "AcceptedExitRoute"
      : `${segment.source[0].toUpperCase()}${segment.source.slice(1)}ExecutionRoute`;

  useEffect(() => () => dashTexture?.dispose(), [dashTexture]);

  return (
    <group name={name}>
      <mesh name={`${segment.id}-body`} renderOrder={style.renderOrder}>
        <tubeGeometry
          args={[curve, quality === "high" ? 48 : 32, style.radius, quality === "high" ? 10 : 7, false]}
        />
        <meshBasicMaterial
          color={style.color}
          map={dashTexture ?? undefined}
          transparent
          opacity={style.opacity}
          depthTest={style.depthTest}
          depthWrite={style.depthWrite}
          toneMapped={false}
        />
      </mesh>
      <RoutePackets
        curve={curve}
        direction={packetDirection}
        mode={segment.mode}
        quality={quality}
      />
      {segment.mode === "failed" ? <FailureGlyph curve={curve} /> : null}
    </group>
  );
}

export function FabricExecutionRoutes({ snapshot, quality }: FabricExecutionRoutesProps) {
  const segments = getFabricRouteSegments(snapshot);

  return (
    <group name="FabricExecutionRoutes">
      {segments.map((segment) => (
        <ExecutionRoute key={segment.id} segment={segment} quality={quality} />
      ))}
    </group>
  );
}
