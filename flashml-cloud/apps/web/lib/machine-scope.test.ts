import { describe, expect, it } from "vitest";
import { isMachineOnline, poolFleetCounts } from "./machine-scope";

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

describe("poolFleetCounts", () => {
  const recentlySeen = new Date(Date.now() - 5_000).toISOString();
  const staleSeen = new Date(Date.now() - 60 * 60 * 1000).toISOString();

  it("is all zeroes for an empty fleet, a real answer and not a gap", () => {
    expect(poolFleetCounts([])).toEqual({
      total: 0,
      online: 0,
      pending: 0,
      revoked: 0,
    });
  });

  it("counts total as the array length regardless of status mix", () => {
    const fleet = [
      machine({ status: "active", last_seen_at: recentlySeen }),
      machine({ status: "pending", last_seen_at: null }),
      machine({ status: "revoked", last_seen_at: recentlySeen }),
    ];
    expect(poolFleetCounts(fleet).total).toBe(3);
  });

  it("counts online using the same rule isMachineOnline enforces — never a revoked machine, even freshly seen", () => {
    const fleet = [
      machine({ status: "active", last_seen_at: recentlySeen }),
      machine({ status: "active", last_seen_at: staleSeen }),
      machine({ status: "revoked", last_seen_at: recentlySeen }),
    ];
    const counts = poolFleetCounts(fleet);
    expect(counts.online).toBe(1);
    expect(counts.revoked).toBe(1);
  });

  it("counts pending and revoked from status, independently of online", () => {
    const fleet = [
      machine({ status: "pending", last_seen_at: null }),
      machine({ status: "pending", last_seen_at: recentlySeen }),
      machine({ status: "revoked", last_seen_at: null }),
    ];
    const counts = poolFleetCounts(fleet);
    expect(counts.pending).toBe(2);
    expect(counts.revoked).toBe(1);
    // A pending machine that happens to be heartbeating counts as online
    // too — the two axes (enrolment status, heartbeat recency) are not
    // mutually exclusive, and this must not silently pick one.
    expect(counts.online).toBe(1);
  });
});
