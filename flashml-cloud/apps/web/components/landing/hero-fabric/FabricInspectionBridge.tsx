"use client";

import { useThree } from "@react-three/fiber";
import { useLayoutEffect } from "react";
import {
  publishFabricInspectionScene,
  type FabricInspectionTarget,
} from "@/lib/hero-fabric";

export function FabricInspectionBridge() {
  const camera = useThree((state) => state.camera);
  const renderer = useThree((state) => state.gl);
  const scene = useThree((state) => state.scene);

  useLayoutEffect(
    () => publishFabricInspectionScene(
      window as unknown as FabricInspectionTarget,
      { camera, renderer, scene },
    ),
    [camera, renderer, scene],
  );

  return null;
}
