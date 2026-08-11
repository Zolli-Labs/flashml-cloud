export type FabricVector3 = [number, number, number];

export type FabricMatrix4 = [
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
  number,
];

export interface FabricBounds {
  min: FabricVector3;
  max: FabricVector3;
}

export interface FabricNodeTransform {
  name: string;
  worldMatrix: FabricMatrix4;
  forward: FabricVector3 | null;
}

export interface FabricMaterialInspection {
  name: string;
  baseColor: [number, number, number, number];
  metalness: number;
  roughness: number;
}

export interface FabricGlbInspection {
  nodeNames: string[];
  meshNames: string[];
  triangleCount: number;
  boundingBox: FabricBounds;
  semanticBounds: Partial<Record<string, FabricBounds>>;
  semanticForwards: Partial<Record<string, FabricVector3 | null>>;
  forward: FabricVector3 | null;
  nodeTransforms: FabricNodeTransform[];
  materials: FabricMaterialInspection[];
  meshMaterials: Partial<Record<string, string>>;
}

export function inspectFabricGlb(
  bytes: Uint8Array,
  semanticNames: readonly string[],
): Promise<FabricGlbInspection>;
