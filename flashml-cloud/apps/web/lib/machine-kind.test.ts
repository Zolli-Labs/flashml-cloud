import { describe, expect, it } from "vitest";
import { machineKind } from "./machine-kind";

function machine(
  overrides: Partial<{
    platform: string | null;
    capabilities: Record<string, unknown> | null;
  }> = {}
) {
  return {
    platform: null,
    capabilities: null,
    ...overrides,
  };
}

describe("machineKind", () => {
  it("reads a macOS platform string as laptop", () => {
    expect(
      machineKind(machine({ platform: "macOS-26.5.1-arm64" }))
    ).toBe("laptop");
  });

  it("reads a non-empty capabilities.gpus array as gpu", () => {
    expect(
      machineKind(
        machine({
          platform: "Linux-6.8.0-90-generic-x86_64",
          capabilities: { gpus: [{ model: "unused-by-this-function" }] },
        })
      )
    ).toBe("gpu");
  });

  it("reads a Linux platform with no gpus as server", () => {
    expect(
      machineKind(
        machine({ platform: "Linux-6.8.0-90-generic-x86_64" })
      )
    ).toBe("server");
  });

  it("reads absent platform and absent capabilities as unknown", () => {
    expect(machineKind(machine())).toBe("unknown");
  });

  // Precedence, asserted deliberately: a Mac with GPUs listed is still a
  // laptop in the fleet's eye, not a GPU rig — see the doc comment on why.
  it("prefers laptop over gpu when a macOS machine also reports gpus", () => {
    expect(
      machineKind(
        machine({
          platform: "macOS-26.5.1-arm64",
          capabilities: { gpus: [{ model: "unused-by-this-function" }] },
        })
      )
    ).toBe("laptop");
  });

  it("treats an empty gpus array as not-gpu, falling through to server", () => {
    expect(
      machineKind(
        machine({
          platform: "Linux-6.8.0-90-generic-x86_64",
          capabilities: { gpus: [] },
        })
      )
    ).toBe("server");
  });

  it("treats a malformed (non-array) gpus field as absent, not a crash", () => {
    expect(
      machineKind(
        machine({
          platform: "Linux-6.8.0-90-generic-x86_64",
          capabilities: { gpus: "not-an-array" },
        })
      )
    ).toBe("server");
  });
});
