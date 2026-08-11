"use client";

import { FABRIC_ACCEPTED_EXIT_POINTS, type FabricQualityTier } from "@/lib/hero-fabric";

export function CheckpointBeacon({
  visible,
  quality,
}: {
  visible: boolean;
  quality: FabricQualityTier;
}) {
  return (
    <group name="CheckpointBeacon" position={[0, 0.48, 0.84]} visible={visible}>
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[0.13, 0.025, 10, quality === "high" ? 28 : 18]} />
        <meshBasicMaterial color="#f36b32" transparent opacity={0.96} toneMapped={false} />
      </mesh>
      <mesh rotation={[0, 0, Math.PI / 4]}>
        <octahedronGeometry args={[0.09, 0]} />
        <meshStandardMaterial
          color="#fff2e5"
          emissive="#f36b32"
          emissiveIntensity={0.92}
          metalness={0.16}
          roughness={0.22}
        />
      </mesh>
      {quality === "high" ? (
        <pointLight intensity={2.8} distance={1.2} color="#f36b32" />
      ) : null}
    </group>
  );
}

export function AcceptedMarker({ visible }: { visible: boolean }) {
  const position = FABRIC_ACCEPTED_EXIT_POINTS.at(-1)!;

  return (
    <group name="AcceptedMarker" position={position} visible={visible}>
      <mesh rotation={[Math.PI / 2, 0, 0]}>
        <torusGeometry args={[0.18, 0.035, 10, 28]} />
        <meshBasicMaterial color="#4ba77b" toneMapped={false} />
      </mesh>
      <mesh>
        <sphereGeometry args={[0.075, 14, 14]} />
        <meshBasicMaterial color="#4ba77b" toneMapped={false} />
      </mesh>
      <pointLight intensity={3.6} distance={1.45} color="#4ba77b" />
    </group>
  );
}
