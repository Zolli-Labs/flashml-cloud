import { describe, expect, it } from "vitest";

import { groupArtifactsByTask } from "./task-artifacts";
import type { JobArtifactEntry, JobTask } from "./cloud-api";

/** A row of `GET /v1alpha1/jobs/{id}/artifacts`. The key is already relative
 * to the job's own prefix — that is what the listing route returns, and it is
 * the only artifact shape this console reads. */
function entry(key: string, sizeBytes = 100): JobArtifactEntry {
  return { key, size_bytes: sizeBytes };
}

function task(over: Partial<JobTask>): JobTask {
  return {
    task_id: "task-000",
    state: "COMPLETED",
    attempts: 1,
    max_attempts: 3,
    node_id: "node-0",
    deadline: null,
    ...over,
  };
}

describe("groupArtifactsByTask", () => {
  it("groups artifacts by the task-id prefix of their key", () => {
    const artifacts = [
      entry("task-000/stdout.log"),
      entry("task-000/result.json"),
      entry("task-001/result.json"),
    ];
    const tasks = [task({ task_id: "task-000" }), task({ task_id: "task-001" })];

    const groups = groupArtifactsByTask(artifacts, tasks);

    expect(groups.map((g) => g.groupId).sort()).toEqual(["task-000", "task-001"]);
    const t0 = groups.find((g) => g.groupId === "task-000")!;
    expect(t0.artifacts).toHaveLength(2);
  });

  it("recognises stdout and stderr regardless of extension or case", () => {
    const artifacts = [
      entry("task-000/stdout.log"),
      entry("task-000/STDERR.LOG"),
      entry("task-000/stdout.txt"),
      entry("task-000/stdout"),
      entry("task-000/metrics.json"),
    ];
    const groups = groupArtifactsByTask(artifacts, [task({})]);
    const kinds = groups[0].artifacts.map((a) => a.logKind);
    expect(kinds).toEqual(["stdout", "stderr", "stdout", "stdout", null]);
  });

  it("puts every recognised log in the group's own logs list, stdout before stderr", () => {
    const artifacts = [
      entry("task-000/stderr.log"),
      entry("task-000/stdout.log"),
      entry("task-000/result.json"),
    ];
    const groups = groupArtifactsByTask(artifacts, [task({})]);
    expect(groups[0].logs.map((l) => l.logKind)).toEqual(["stdout", "stderr"]);
  });

  it("does not assume every task has logs — a group with no log files gets an empty logs list, not an error", () => {
    const groups = groupArtifactsByTask(
      [entry("task-000/result.json")],
      [task({})]
    );
    expect(groups[0].logs).toEqual([]);
    expect(groups[0].hasFailureLog).toBe(false);
  });

  it("attaches the matching task's current state to its group", () => {
    const groups = groupArtifactsByTask(
      [entry("task-000/result.json")],
      [task({ task_id: "task-000", state: "FAILED" })]
    );
    expect(groups[0].taskState).toBe("FAILED");
  });

  it("leaves taskState null for a group whose id names no task — e.g. a reducer's own output bucket", () => {
    const groups = groupArtifactsByTask([entry("reduced/rows.jsonl")], [task({})]);
    expect(groups[0].groupId).toBe("reduced");
    expect(groups[0].taskState).toBeNull();
  });

  it("flags a FAILED task's group as having a failure log only when a log file actually exists", () => {
    const groups = groupArtifactsByTask(
      [entry("task-000/stderr.log")],
      [task({ task_id: "task-000", state: "FAILED" })]
    );
    expect(groups[0].hasFailureLog).toBe(true);
  });

  it("orders FAILED-task groups first, so the failed task's logs are the obvious thing to open", () => {
    const artifacts = [
      entry("task-000/result.json"),
      entry("task-001/stderr.log"),
      entry("task-002/result.json"),
    ];
    const tasks = [
      task({ task_id: "task-000", state: "COMPLETED" }),
      task({ task_id: "task-001", state: "FAILED" }),
      task({ task_id: "task-002", state: "COMPLETED" }),
    ];
    const groups = groupArtifactsByTask(artifacts, tasks);
    expect(groups[0].groupId).toBe("task-001");
  });

  it("carries each file's reported size through untouched", () => {
    const groups = groupArtifactsByTask(
      [entry("task-000/result.json", 4096), entry("task-000/stdout.log", 0)],
      [task({})]
    );
    expect(groups[0].artifacts.map((a) => a.sizeBytes)).toEqual([4096, 0]);
  });

  it("renders a size that is not a finite number as unknown rather than as zero", () => {
    // 0 B and "we could not read the size" are different facts, and a total
    // built from a fabricated 0 is wrong in a way nothing downstream can see.
    const groups = groupArtifactsByTask(
      [{ key: "task-000/result.json", size_bytes: null as unknown as number }],
      [task({})]
    );
    expect(groups[0].artifacts[0].sizeBytes).toBeNull();
  });

  it("returns no groups for a job whose listing is empty", () => {
    expect(groupArtifactsByTask([], [])).toEqual([]);
  });
});

describe("checkpoints in a task's artifact list", () => {
  // Checkpointing is on for every job, so a task that died mid-run leaves
  // `ckpt/step-*.json` files at the same keys its results use. Rendering
  // them undifferentiated presents the machinery of recovery as if it were
  // the job's output.
  it("separates a task's checkpoints from its results, keeping both in `artifacts`", () => {
    const artifacts = [
      entry("task-000/ckpt/step-40.json"),
      entry("task-000/metrics.json"),
      entry("task-000/ckpt/step-20.json"),
    ];
    const groups = groupArtifactsByTask(artifacts, [task({})]);

    expect(groups[0].artifacts).toHaveLength(3);
    expect(groups[0].results.map((a) => a.filename)).toEqual(["metrics.json"]);
    // Oldest step first, so the last row is the furthest the task got.
    expect(groups[0].checkpoints.map((a) => a.checkpointStep)).toEqual([20, 40]);
  });

  it("leaves an ordinary artifact's checkpointStep null", () => {
    const groups = groupArtifactsByTask(
      [entry("task-000/model.json")],
      [task({})]
    );
    expect(groups[0].checkpoints).toEqual([]);
    expect(groups[0].artifacts[0].checkpointStep).toBeNull();
  });

  it("does not treat a result file that merely looks like a step as a checkpoint", () => {
    const groups = groupArtifactsByTask(
      [entry("task-000/step-3.json")],
      [task({})]
    );
    expect(groups[0].checkpoints).toEqual([]);
    expect(groups[0].results).toHaveLength(1);
  });

  it("keeps a stdout file a log, not a checkpoint, when both classifiers see it", () => {
    const groups = groupArtifactsByTask(
      [entry("task-000/logs/stdout.txt")],
      [task({})]
    );
    expect(groups[0].logs).toHaveLength(1);
    expect(groups[0].checkpoints).toEqual([]);
  });
});

describe("the flashnode seam", () => {
  it("recognises the exact artifact key the agent produces today", () => {
    // This console and the agent that writes these files live in DIFFERENT
    // REPOSITORIES, and nothing in either one binds them: flashnode writes
    // `out/logs/stdout.txt`, the executor uploads the output tree with
    // `rglob`, and the key that arrives here is therefore
    // `{task}/logs/stdout.txt` — a NESTED path, not a bare filename.
    //
    // The grouping was written against a guess, because the agent building
    // it was deliberately not shown the sibling repo. It happens to be
    // right. This test is what makes it stay right: if either side renames
    // the directory or the file, a failed task's logs would silently stop
    // being recognised as logs and would sink back into an undifferentiated
    // artifact list — the exact problem this feature exists to remove, and
    // one that no other test in either repository would catch.
    const groups = groupArtifactsByTask(
      [entry("task-000/logs/stdout.txt", 10), entry("task-000/logs/stderr.txt", 10)],
      [task({ task_id: "task-000", state: "FAILED" })]
    );

    const group = groups.find((g) => g.groupId === "task-000");
    expect(group?.hasFailureLog).toBe(true);
    expect(group?.logs.map((a) => a.logKind)).toEqual(["stdout", "stderr"]);
  });
});
