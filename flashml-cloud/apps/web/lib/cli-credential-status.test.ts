import { describe, expect, it } from "vitest";
import { credentialBadge, credentialLabel } from "./cli-credential-status";
import type { CliCredential } from "./cloud-api";

const base: CliCredential = {
  id: "c1",
  label: "phong's laptop",
  status: "active",
  token_prefix: "fmu_abc12345",
  last_used_at: null,
  created_at: "2026-08-10T00:00:00Z",
  revoked_at: null,
};

describe("credentialLabel", () => {
  it("uses the label the CLI reported", () => {
    expect(credentialLabel(base)).toBe("phong's laptop");
  });

  it("falls back to the token prefix rather than showing an empty row", () => {
    expect(credentialLabel({ ...base, label: null })).toBe("fmu_abc12345…");
  });

  it("falls back again when even the prefix is missing", () => {
    expect(credentialLabel({ ...base, label: null, token_prefix: "" })).toBe(
      "unnamed credential"
    );
  });

  it("treats a whitespace-only label as no label at all", () => {
    expect(credentialLabel({ ...base, label: "   " })).toBe("fmu_abc12345…");
  });
});

describe("credentialBadge", () => {
  it("marks an active credential active", () => {
    expect(credentialBadge(base)).toEqual({ label: "Active", tone: "active" });
  });

  it("marks a revoked credential revoked", () => {
    expect(credentialBadge({ ...base, status: "revoked" })).toEqual({
      label: "Revoked",
      tone: "revoked",
    });
  });
});
