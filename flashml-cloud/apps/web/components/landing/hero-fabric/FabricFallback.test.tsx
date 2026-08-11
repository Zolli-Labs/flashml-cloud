import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FabricFallback } from "./FabricFallback";

function renderFallback(reason: "loading" | "webgl") {
  return renderToStaticMarkup(
    <FabricFallback reason={reason}>
      <button type="button">Everyday Machines</button>
    </FabricFallback>,
  );
}

describe("FabricFallback", () => {
  it("keeps the compute sources operable while the 3D fabric is loading", () => {
    const markup = renderFallback("loading");

    expect(markup).toContain(
      'alt="Compute from everyday machines, owned infrastructure, rented GPUs, and cloud HPC connected by the Zolli control plane"',
    );
    expect(markup).toContain("Preparing the compute fabric…");
    expect(markup).toContain("<button");
    expect(markup).toContain("Everyday Machines");
  });

  it("explains the WebGL fallback without removing the compute sources", () => {
    const markup = renderFallback("webgl");

    expect(markup).toContain("Interactive 3D is unavailable");
    expect(markup).toContain("<button");
    expect(markup).toContain("Everyday Machines");
  });
});
