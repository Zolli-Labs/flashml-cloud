import { describe, expect, it } from "vitest";
import {
  resolveWorkspace,
  workspaceIdFromPath,
  workspacePath,
} from "./workspace-scope";
import type { PoolSummary } from "./cloud-api";

function pool(id: string, name: string): PoolSummary {
  return {
    id,
    name,
    owner_id: "owner",
    created_at: "2026-08-01T00:00:00Z",
    member_count: 1,
    machines_online: 0,
  };
}

const VISION = pool("vision", "Vision Lab");
const ROBOTICS = pool("robotics", "Almanac Robotics");

describe("workspaceIdFromPath", () => {
  it("reads the id out of a workspace route", () => {
    expect(workspaceIdFromPath("/w/vision")).toBe("vision");
    expect(workspaceIdFromPath("/w/vision/jobs")).toBe("vision");
    expect(workspaceIdFromPath("/w/vision/jobs/abc-123")).toBe("vision");
  });

  it("is null for anything not workspace-scoped", () => {
    expect(workspaceIdFromPath("/account/machines")).toBeNull();
    expect(workspaceIdFromPath("/w")).toBeNull();
    expect(workspaceIdFromPath("/w/")).toBeNull();
    expect(workspaceIdFromPath("/")).toBeNull();
  });

  it("decodes an escaped segment", () => {
    expect(workspaceIdFromPath("/w/a%2Fb/jobs")).toBe("a/b");
  });
});

describe("resolveWorkspace", () => {
  const pools = [VISION, ROBOTICS];

  it("prefers the URL over the cookie", () => {
    expect(resolveWorkspace("/w/vision/jobs", pools, "robotics")).toEqual({
      kind: "workspace",
      poolId: "vision",
    });
  });

  it("ignores a URL naming a workspace you are not in", () => {
    expect(resolveWorkspace("/w/someone-elses", pools, "robotics")).toEqual({
      kind: "workspace",
      poolId: "robotics",
    });
  });

  it("falls back to the cookie when the path carries no workspace", () => {
    expect(resolveWorkspace("/overview", pools, "vision")).toEqual({
      kind: "workspace",
      poolId: "vision",
    });
  });

  it("ignores a cookie naming a workspace you were removed from", () => {
    // Alphabetical, so "Almanac Robotics" wins over "Vision Lab".
    expect(resolveWorkspace("/overview", pools, "left-this-one")).toEqual({
      kind: "workspace",
      poolId: "robotics",
    });
  });

  it("falls back to the first workspace by NAME, not by list order", () => {
    expect(resolveWorkspace("/overview", pools, null)).toEqual({
      kind: "workspace",
      poolId: "robotics",
    });
  });

  it("sends a user with no workspaces to onboarding", () => {
    expect(resolveWorkspace("/overview", [], "vision")).toEqual({
      kind: "onboarding",
    });
  });
});

describe("workspacePath", () => {
  it("builds a tab URL", () => {
    expect(workspacePath("vision", "jobs")).toBe("/w/vision/jobs");
  });

  it("escapes an id that would otherwise break the path", () => {
    expect(workspacePath("a/b", "people")).toBe("/w/a%2Fb/people");
  });
});
