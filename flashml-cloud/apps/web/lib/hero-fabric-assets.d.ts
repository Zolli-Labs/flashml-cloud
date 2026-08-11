export type FabricAssetKey = "everyday" | "owned" | "rented" | "cloud" | "controlPlane";
export type FabricAssetAxis = "x" | "y" | "z";

export interface FabricSilhouetteRatio {
  readonly numerator: readonly [string, FabricAssetAxis];
  readonly denominator: readonly [string, FabricAssetAxis];
  readonly min: number;
}

export interface FabricRelativeSilhouetteRatio {
  readonly subject: readonly [string, FabricAssetAxis];
  readonly reference: readonly [FabricAssetKey, string, FabricAssetAxis];
  readonly min: number;
}

export interface FabricAssetSilhouette {
  readonly requiredMeshes: readonly string[];
  readonly requiredSemanticBounds: readonly string[];
  readonly ratios: readonly FabricSilhouetteRatio[];
  readonly relativeRatios: readonly FabricRelativeSilhouetteRatio[];
}

export const FABRIC_ASSET_SILHOUETTES: Readonly<Record<FabricAssetKey, FabricAssetSilhouette>>;
