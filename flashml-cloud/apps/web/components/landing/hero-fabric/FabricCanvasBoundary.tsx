"use client";

import { Component, type ReactNode } from "react";
import { FabricFallback } from "./FabricFallback";

interface FabricCanvasBoundaryProps {
  children: ReactNode;
  controls: ReactNode;
  failure?: "webgl";
  onFailure?: () => void;
}

interface FabricCanvasBoundaryState {
  failed: boolean;
}

export class FabricCanvasBoundary extends Component<
  FabricCanvasBoundaryProps,
  FabricCanvasBoundaryState
> {
  state: FabricCanvasBoundaryState = { failed: false };

  static getDerivedStateFromError(): FabricCanvasBoundaryState {
    return { failed: true };
  }

  componentDidCatch() {
    this.props.onFailure?.();
  }

  render() {
    if (this.props.failure === "webgl" || this.state.failed) {
      return <FabricFallback reason="webgl">{this.props.controls}</FabricFallback>;
    }

    return this.props.children;
  }
}
