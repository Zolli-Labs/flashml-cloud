import { describe, expect, it } from "vitest";

import { VERDICT_LABEL, summariseVerifications } from "./job-verifications";
import type { Verification } from "./cloud-api";

// Built at call time, nothing fixture-shaped sitting in the source — same
// house rule `lib/job-tradeoff.test.ts` and `lib/task-checkpoints.test.ts`
// document. `over` supplies only the fields a given test cares about.
function verification(over: Partial<Verification>): Verification {
  return {
    id: "v-1",
    machine_id: "m-1",
    job_id: "job-1",
    task_id: "task-1",
    slice: "timing",
    verdict: "pass",
    detail: null,
    created_at: "2026-08-13T10:00:00Z",
    ...over,
  };
}

describe("summariseVerifications — empty input", () => {
  it("returns the empty state for zero rows, never a pass", () => {
    const panel = summariseVerifications([]);
    expect(panel.state).toBe("empty");
    expect(panel.tasks).toEqual([]);
    expect(panel.totalRows).toBe(0);
  });

  it("gives an empty job a rollup of real zeros, not an omitted field", () => {
    // The zeros must be present and inspectable — a missing rollup would
    // force a caller to guess, and a caller filling that gap with "0" by
    // convention is exactly the kind of accidental fabrication this module
    // exists to prevent one level up.
    const panel = summariseVerifications([]);
    expect(panel.rollup).toEqual({ pass: 0, flag: 0, unknown: 0 });
  });
});

describe("summariseVerifications — the honesty rule", () => {
  it("never labels an unknown verdict as passed", () => {
    const rows = [
      verification({ id: "v-1", task_id: "task-1", slice: "timing", verdict: "unknown" }),
      verification({ id: "v-2", task_id: "task-1", slice: "evidence", verdict: "unknown" }),
      verification({ id: "v-3", task_id: "task-2", slice: "redundancy", verdict: "unknown" }),
    ];
    const panel = summariseVerifications(rows);
    const allRows = panel.tasks.flatMap((t) => t.rows);
    expect(allRows).toHaveLength(3);
    for (const row of allRows) {
      expect(row.verdict).toBe("unknown");
      expect(row.label).not.toBe("passed");
      expect(row.label).toBe("could not tell");
    }
    expect(panel.rollup.pass).toBe(0);
    expect(panel.rollup.unknown).toBe(3);
  });

  it("maps each verdict to its exact fixed label", () => {
    expect(VERDICT_LABEL.pass).toBe("passed");
    expect(VERDICT_LABEL.flag).toBe("flagged");
    expect(VERDICT_LABEL.unknown).toBe("could not tell");

    const rows = [
      verification({ id: "v-1", verdict: "pass" }),
      verification({ id: "v-2", verdict: "flag" }),
      verification({ id: "v-3", verdict: "unknown" }),
    ];
    const panel = summariseVerifications(rows);
    const byId = new Map(
      panel.tasks.flatMap((t) => t.rows).map((r) => [r.id, r])
    );
    expect(byId.get("v-1")?.label).toBe("passed");
    expect(byId.get("v-2")?.label).toBe("flagged");
    expect(byId.get("v-3")?.label).toBe("could not tell");
  });

  it("carries a flag through as a flag, never rounded up to a pass", () => {
    const rows = [verification({ id: "v-1", verdict: "flag" })];
    const panel = summariseVerifications(rows);
    expect(panel.tasks[0].rows[0].verdict).toBe("flag");
    expect(panel.rollup.pass).toBe(0);
    expect(panel.rollup.flag).toBe(1);
  });
});

describe("summariseVerifications — grouping by task", () => {
  it("groups multiple slices recorded for the same task under one entry", () => {
    const rows = [
      verification({ id: "v-1", task_id: "task-1", slice: "timing", verdict: "pass" }),
      verification({ id: "v-2", task_id: "task-1", slice: "evidence", verdict: "flag" }),
      verification({ id: "v-3", task_id: "task-1", slice: "redundancy", verdict: "unknown" }),
    ];
    const panel = summariseVerifications(rows);
    expect(panel.tasks).toHaveLength(1);
    expect(panel.tasks[0].taskId).toBe("task-1");
    expect(panel.tasks[0].rows).toHaveLength(3);
    expect(panel.tasks[0].rows.map((r) => r.slice).sort()).toEqual([
      "evidence",
      "redundancy",
      "timing",
    ]);
  });

  it("keeps different tasks in different groups", () => {
    const rows = [
      verification({ id: "v-1", task_id: "task-1", verdict: "pass" }),
      verification({ id: "v-2", task_id: "task-2", verdict: "flag" }),
    ];
    const panel = summariseVerifications(rows);
    expect(panel.tasks.map((t) => t.taskId).sort()).toEqual([
      "task-1",
      "task-2",
    ]);
    expect(panel.totalRows).toBe(2);
  });

  it("counts distinct tasks and total rows separately", () => {
    const rows = [
      verification({ id: "v-1", task_id: "task-1", slice: "timing" }),
      verification({ id: "v-2", task_id: "task-1", slice: "evidence" }),
      verification({ id: "v-3", task_id: "task-2", slice: "timing" }),
    ];
    const panel = summariseVerifications(rows);
    expect(panel.tasks).toHaveLength(2);
    expect(panel.totalRows).toBe(3);
  });
});

describe("summariseVerifications — the rollup", () => {
  it("counts every verdict across the whole job, not just one task", () => {
    const rows = [
      verification({ id: "v-1", task_id: "task-1", verdict: "pass" }),
      verification({ id: "v-2", task_id: "task-1", verdict: "pass" }),
      verification({ id: "v-3", task_id: "task-2", verdict: "flag" }),
      verification({ id: "v-4", task_id: "task-3", verdict: "unknown" }),
      verification({ id: "v-5", task_id: "task-3", verdict: "unknown" }),
    ];
    const panel = summariseVerifications(rows);
    expect(panel.rollup).toEqual({ pass: 2, flag: 1, unknown: 2 });
    expect(panel.totalRows).toBe(5);
  });
});

describe("summariseVerifications — field passthrough", () => {
  it("carries machine_id and detail through verbatim, including null", () => {
    const rows = [
      verification({
        id: "v-1",
        machine_id: null,
        detail: null,
      }),
      verification({
        id: "v-2",
        task_id: "task-2",
        machine_id: "m-42",
        detail: { peer_median_s: 12.5, this_task_s: 90.1 },
      }),
    ];
    const panel = summariseVerifications(rows);
    const byId = new Map(
      panel.tasks.flatMap((t) => t.rows).map((r) => [r.id, r])
    );
    expect(byId.get("v-1")?.machineId).toBeNull();
    expect(byId.get("v-1")?.detail).toBeNull();
    expect(byId.get("v-2")?.machineId).toBe("m-42");
    expect(byId.get("v-2")?.detail).toEqual({
      peer_median_s: 12.5,
      this_task_s: 90.1,
    });
  });

  it("never invents a detail object where the API sent null", () => {
    const rows = [verification({ id: "v-1", detail: null })];
    const panel = summariseVerifications(rows);
    expect(panel.tasks[0].rows[0].detail).toBeNull();
  });
});

describe("summariseVerifications — presence never implies a clean sweep", () => {
  it("a job present-state panel with only flags has zero passes, not a fabricated one", () => {
    const rows = [
      verification({ id: "v-1", task_id: "task-1", verdict: "flag" }),
      verification({ id: "v-2", task_id: "task-2", verdict: "flag" }),
    ];
    const panel = summariseVerifications(rows);
    expect(panel.state).toBe("present");
    expect(panel.rollup.pass).toBe(0);
    expect(panel.rollup.flag).toBe(2);
  });
});
