import { describe, expect, it } from "vitest";
import { placementAt, significantMoments } from "./placement-at";
import type { Attempt } from "./job-activity";

/**
 * The scrubber is only trustworthy if these are. If placementAt is wrong the
 * topology shows work on a machine that never ran it, which is a worse lie
 * than showing nothing — the whole point of this console is an honest audit
 * trail.
 */

const a = (
  taskId: string,
  nodeId: string,
  startedAt: number,
  endedAt: number | null,
  outcome: Attempt["outcome"] = "accepted"
): Attempt => ({ taskId, nodeId, startedAt, endedAt, outcome });

describe("placementAt", () => {
  it("shows nothing before anything started", () => {
    const snap = placementAt([a("t1", "n1", 100, 200)], 50, ["n1"]);
    expect(snap.activeCount).toBe(0);
    expect(snap.idleNodes).toEqual(["n1"]);
  });

  it("shows a task on the node holding it mid-attempt", () => {
    const snap = placementAt([a("t1", "n1", 100, 200)], 150, ["n1"]);
    expect(snap.byNode.get("n1")).toHaveLength(1);
    expect(snap.byNode.get("n1")![0].taskId).toBe("t1");
    expect(snap.activeCount).toBe(1);
  });

  // Half-open [start, end) exists for exactly this: a lease expiring and
  // being re-claimed at the same instant must not put one task on two
  // machines, and that instant is the one a viewer scrubs to.
  it("never shows one task on two nodes at a handover instant", () => {
    const attempts = [a("t1", "dead", 100, 200, "expired"), a("t1", "alive", 200, 300)];
    const snap = placementAt(attempts, 200, ["dead", "alive"]);
    expect(snap.byNode.get("dead")).toHaveLength(0);
    expect(snap.byNode.get("alive")).toHaveLength(1);
    expect(snap.activeCount).toBe(1);
  });

  it("keeps a finished node present but idle rather than dropping it", () => {
    // Dropping it would make the canvas re-layout as you scrub, so the
    // machine you were looking at jumps somewhere else.
    const snap = placementAt([a("t1", "n1", 100, 200)], 250, ["n1", "n2"]);
    expect(snap.idleNodes.sort()).toEqual(["n1", "n2"]);
    expect(snap.activeCount).toBe(0);
  });

  it("treats an unfinished attempt as still held", () => {
    const snap = placementAt([a("t1", "n1", 100, null, "running")], 9_999, ["n1"]);
    expect(snap.byNode.get("n1")).toHaveLength(1);
  });

  it("places concurrent tasks on their own nodes", () => {
    const snap = placementAt(
      [a("t1", "n1", 100, 300), a("t2", "n2", 150, 400)],
      200,
      ["n1", "n2"]
    );
    expect(snap.activeCount).toBe(2);
    expect(snap.byNode.get("n1")![0].taskId).toBe("t1");
    expect(snap.byNode.get("n2")![0].taskId).toBe("t2");
  });
});

describe("significantMoments", () => {
  it("returns every distinct edge, sorted, deduped", () => {
    const moments = significantMoments([
      a("t1", "n1", 100, 200),
      a("t1", "n2", 200, 300),
      a("t2", "n1", 100, null),
    ]);
    expect(moments).toEqual([100, 200, 300]);
  });
});
