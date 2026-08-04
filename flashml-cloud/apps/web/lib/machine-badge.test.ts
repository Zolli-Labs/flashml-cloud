import { describe, expect, it } from "vitest";
import { machineBadge } from "./machine-badge";

function caps(
  overrides: Partial<{
    sandbox_capable: boolean;
    argv_capable: boolean;
    unsandboxed_argv_capable: boolean;
  }> = {}
) {
  return {
    sandbox_capable: false,
    argv_capable: false,
    unsandboxed_argv_capable: false,
    ...overrides,
  };
}

describe("machineBadge", () => {
  it("reads argv_capable alone as sandboxed", () => {
    expect(machineBadge(caps({ argv_capable: true }))).toBe("sandboxed");
  });

  it("reads sandbox_capable alone (no argv) as sandboxed", () => {
    expect(machineBadge(caps({ sandbox_capable: true }))).toBe("sandboxed");
  });

  it("reads unsandboxed_argv_capable alone as trusted", () => {
    expect(machineBadge(caps({ unsandboxed_argv_capable: true }))).toBe(
      "trusted"
    );
  });

  it("reads none of the three flags as modules-only", () => {
    expect(machineBadge(caps())).toBe("modules-only");
  });

  // Precedence, asserted deliberately: Docker hosts never run trusted work.
  // An agent claiming both argv (sandboxed) and unsandboxed argv (trusted)
  // capability at once must read as sandboxed, not trusted — the safer
  // path wins whenever it is available at all.
  it("prefers sandboxed over trusted when an agent claims both argv_capable and unsandboxed_argv_capable", () => {
    expect(
      machineBadge(caps({ argv_capable: true, unsandboxed_argv_capable: true }))
    ).toBe("sandboxed");
  });

  it("prefers sandboxed over trusted for sandbox_capable + unsandboxed_argv_capable too, same precedence rule", () => {
    expect(
      machineBadge(
        caps({ sandbox_capable: true, unsandboxed_argv_capable: true })
      )
    ).toBe("sandboxed");
  });
});
