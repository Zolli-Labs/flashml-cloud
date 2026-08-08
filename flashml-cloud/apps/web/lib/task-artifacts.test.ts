import { describe, expect, it } from "vitest";

import { groupArtifactsByTask } from "./task-artifacts";
import type { ArtifactRecord, JobTask } from "./cloud-api";

const JOB_ID = "job-abc";

function artifact(over: Partial<ArtifactRecord>): ArtifactRecord {
  return {
    uri: `artifact://jobs/${JOB_ID}/task-000/file.bin`,
    backend: "minio",
    bucket: "flashml",
    object_key: "x",
    etag: null,
    sha256: null,
    size_bytes: 100,
    created_at: "2026-08-01T00:00:00Z",
    ...over,
  };
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
      artifact({ uri: `artifact://jobs/${JOB_ID}/task-000/stdout.log` }),
      artifact({ uri: `artifact://jobs/${JOB_ID}/task-000/result.json` }),
      artifact({ uri: `artifact://jobs/${JOB_ID}/task-001/result.json` }),
    ];
    const tasks = [task({ task_id: "task-000" }), task({ task_id: "task-001" })];

    const { groups } = groupArtifactsByTask(JOB_ID, artifacts, tasks);

    expect(groups.map((g) => g.groupId).sort()).toEqual(["task-000", "task-001"]);
    const t0 = groups.find((g) => g.groupId === "task-000")!;
    expect(t0.artifacts).toHaveLength(2);
  });

  it("recognises stdout and stderr regardless of extension or case", () => {
    const artifacts = [
      artifact({ uri: `artifact://jobs/${JOB_ID}/task-000/stdout.log` }),
      artifact({ uri: `artifact://jobs/${JOB_ID}/task-000/STDERR.LOG` }),
      artifact({ uri: `artifact://jobs/${JOB_ID}/task-000/stdout.txt` }),
      artifact({ uri: `artifact://jobs/${JOB_ID}/task-000/stdout` }),
      artifact({ uri: `artifact://jobs/${JOB_ID}/task-000/metrics.json` }),
    ];
    const { groups } = groupArtifactsByTask(JOB_ID, artifacts, [task({})]);
    const kinds = groups[0].artifacts.map((a) => a.logKind);
    expect(kinds).toEqual(["stdout", "stderr", "stdout", "stdout", null]);
  });

  it("puts every recognised log in the group's own logs list, stdout before stderr", () => {
    const artifacts = [
      artifact({ uri: `artifact://jobs/${JOB_ID}/task-000/stderr.log` }),
      artifact({ uri: `artifact://jobs/${JOB_ID}/task-000/stdout.log` }),
      artifact({ uri: `artifact://jobs/${JOB_ID}/task-000/result.json` }),
    ];
    const { groups } = groupArtifactsByTask(JOB_ID, artifacts, [task({})]);
    expect(groups[0].logs.map((l) => l.logKind)).toEqual(["stdout", "stderr"]);
  });

  it("does not assume every task has logs — a group with no log files gets an empty logs list, not an error", () => {
    const artifacts = [artifact({ uri: `artifact://jobs/${JOB_ID}/task-000/result.json` })];
    const { groups } = groupArtifactsByTask(JOB_ID, artifacts, [task({})]);
    expect(groups[0].logs).toEqual([]);
    expect(groups[0].hasFailureLog).toBe(false);
  });

  it("attaches the matching task's current state to its group", () => {
    const artifacts = [artifact({ uri: `artifact://jobs/${JOB_ID}/task-000/result.json` })];
    const { groups } = groupArtifactsByTask(JOB_ID, artifacts, [
      task({ task_id: "task-000", state: "FAILED" }),
    ]);
    expect(groups[0].taskState).toBe("FAILED");
  });

  it("leaves taskState null for a group whose id names no task — e.g. a reducer's own output bucket", () => {
    const artifacts = [
      artifact({ uri: `artifact://jobs/${JOB_ID}/reduced/rows.jsonl` }),
    ];
    const { groups } = groupArtifactsByTask(JOB_ID, artifacts, [task({})]);
    expect(groups[0].groupId).toBe("reduced");
    expect(groups[0].taskState).toBeNull();
  });

  it("flags a FAILED task's group as having a failure log only when a log file actually exists", () => {
    const artifacts = [
      artifact({ uri: `artifact://jobs/${JOB_ID}/task-000/stderr.log` }),
    ];
    const { groups } = groupArtifactsByTask(JOB_ID, artifacts, [
      task({ task_id: "task-000", state: "FAILED" }),
    ]);
    expect(groups[0].hasFailureLog).toBe(true);
  });

  it("orders FAILED-task groups first, so the failed task's logs are the obvious thing to open", () => {
    const artifacts = [
      artifact({ uri: `artifact://jobs/${JOB_ID}/task-000/result.json` }),
      artifact({ uri: `artifact://jobs/${JOB_ID}/task-001/stderr.log` }),
      artifact({ uri: `artifact://jobs/${JOB_ID}/task-002/result.json` }),
    ];
    const tasks = [
      task({ task_id: "task-000", state: "COMPLETED" }),
      task({ task_id: "task-001", state: "FAILED" }),
      task({ task_id: "task-002", state: "COMPLETED" }),
    ];
    const { groups } = groupArtifactsByTask(JOB_ID, artifacts, tasks);
    expect(groups[0].groupId).toBe("task-001");
  });

  it("keeps an artifact whose uri is not under this job's own prefix rather than dropping it", () => {
    const artifacts = [
      artifact({ uri: `artifact://jobs/${JOB_ID}/task-000/result.json` }),
      artifact({ uri: "artifact://staged/some-other-thing.tar" }),
    ];
    const { groups, unresolved } = groupArtifactsByTask(JOB_ID, artifacts, [task({})]);
    expect(groups).toHaveLength(1);
    expect(unresolved).toHaveLength(1);
    expect(unresolved[0].uri).toBe("artifact://staged/some-other-thing.tar");
  });

  it("returns no groups and no unresolved artifacts for a job with none", () => {
    expect(groupArtifactsByTask(JOB_ID, [], [])).toEqual({
      groups: [],
      unresolved: [],
    });
  });
});

describe("the flashnode seam", () => {
  it("recognises the exact artifact key the agent produces today", () => {
    // This console and the agent that writes these files live in DIFFERENT
    // REPOSITORIES, and nothing in either one binds them: flashnode writes
    // `out/logs/stdout.txt`, the executor uploads the output tree with
    // `rglob`, and the key that arrives here is therefore
    // `jobs/{job}/{task}/logs/stdout.txt` — a NESTED path, not a bare
    // filename.
    //
    // The grouping was written against a guess, because the agent building
    // it was deliberately not shown the sibling repo. It happens to be
    // right. This test is what makes it stay right: if either side renames
    // the directory or the file, a failed task's logs would silently stop
    // being recognised as logs and would sink back into an undifferentiated
    // artifact list — the exact problem this feature exists to remove, and
    // one that no other test in either repository would catch.
    const groups = groupArtifactsByTask(
      "job1",
      [
        {
          uri: "artifact://jobs/job1/task-000/logs/stdout.txt",
          key: "jobs/job1/task-000/logs/stdout.txt",
          size_bytes: 10,
        },
        {
          uri: "artifact://jobs/job1/task-000/logs/stderr.txt",
          key: "jobs/job1/task-000/logs/stderr.txt",
          size_bytes: 10,
        },
      ] as never,
      [{ task_id: "task-000", state: "FAILED" }] as never
    );

    const group = groups.groups.find((g) => g.groupId === "task-000");
    expect(group?.hasFailureLog).toBe(true);
    expect(group?.logs.map((a) => a.logKind)).toEqual(["stdout", "stderr"]);
  });
});
