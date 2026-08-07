import { describe, expect, it } from "vitest";
import { isMachineOnline } from "./machine-scope";

function machine(overrides: Partial<{ status: string; last_seen_at: string | null }> = {}) {
  return {
    status: "active",
    last_seen_at: new Date().toISOString(),
    ...overrides,
  };
}

describe("isMachineOnline", () => {
  it("is online: active status, seen a few seconds ago", () => {
    const recentlySeen = new Date(Date.now() - 5_000).toISOString();
    expect(
      isMachineOnline(machine({ status: "active", last_seen_at: recentlySeen }))
    ).toBe(true);
  });

  it("is NOT online when revoked, even with a fresh last_seen_at", () => {
    const recentlySeen = new Date(Date.now() - 5_000).toISOString();
    expect(
      isMachineOnline(machine({ status: "revoked", last_seen_at: recentlySeen }))
    ).toBe(false);
  });

  it("is NOT online when last_seen_at is null", () => {
    expect(isMachineOnline(machine({ status: "active", last_seen_at: null }))).toBe(
      false
    );
  });

  it("is NOT online when last_seen_at is long stale", () => {
    const staleSeen = new Date(Date.now() - 60 * 60 * 1000).toISOString();
    expect(
      isMachineOnline(machine({ status: "active", last_seen_at: staleSeen }))
    ).toBe(false);
  });
});
