import { describe, expect, it } from "vitest";
import { NO_POOL, hasNoWorkersOnline, isPoolSelected } from "./pool-selection";
import type { PoolSummary } from "./cloud-api";

function pool(overrides: Partial<PoolSummary> = {}): PoolSummary {
  return {
    id: "pool-1",
    name: "Lab",
    owner_id: "user-1",
    created_at: "2026-08-03T00:00:00Z",
    member_count: 2,
    machines_online: 1,
    ...overrides,
  };
}

describe("isPoolSelected", () => {
  it("is false for the NO_POOL sentinel — the public-queue default", () => {
    expect(isPoolSelected(NO_POOL)).toBe(false);
  });

  it("is false for a bare empty string, the same value as NO_POOL", () => {
    expect(isPoolSelected("")).toBe(false);
  });

  it("is true once an actual pool id is selected", () => {
    expect(isPoolSelected("pool-1")).toBe(true);
  });
});

describe("hasNoWorkersOnline", () => {
  it("is false when nothing is selected", () => {
    expect(hasNoWorkersOnline(null)).toBe(false);
  });

  it("is true when the selected pool has zero machines online", () => {
    expect(hasNoWorkersOnline(pool({ machines_online: 0 }))).toBe(true);
  });

  it("is false when the selected pool has exactly one machine online", () => {
    expect(hasNoWorkersOnline(pool({ machines_online: 1 }))).toBe(false);
  });

  it("is false for a pool with several machines online", () => {
    expect(hasNoWorkersOnline(pool({ machines_online: 5 }))).toBe(false);
  });
});
