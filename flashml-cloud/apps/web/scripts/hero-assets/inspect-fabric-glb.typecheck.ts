import type {
  FabricBounds,
  FabricGlbInspection,
  FabricVector3,
} from "./inspect-fabric-glb.mjs";

type Equal<Left, Right> =
  (<Value>() => Value extends Left ? 1 : 2) extends
  (<Value>() => Value extends Right ? 1 : 2)
    ? true
    : false;

type Expect<Value extends true> = Value;

export type MissingSemanticBoundsIsExplicit = Expect<
  Equal<
    FabricGlbInspection["semanticBounds"][string],
    FabricBounds | undefined
  >
>;

export type MissingSemanticForwardIsExplicit = Expect<
  Equal<
    FabricGlbInspection["semanticForwards"][string],
    FabricVector3 | null | undefined
  >
>;
