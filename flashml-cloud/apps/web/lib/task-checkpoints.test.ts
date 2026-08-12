import { describe, expect, it } from "vitest";

import {
  MAX_CHECKPOINT_READS,
  checkpointStepFromKey,
  selectTasksForCheckpointRead,
  summariseCheckpoints,
} from "./task-checkpoints";
import type { CheckpointManifest, TaskCheckpointRead } from "./cloud-api";
import type { JobTask } from "./cloud-api";
import type { Attempt } from "./job-activity";

function task(over: Partial<JobTask>): JobTask {
  return {
    task_id: "task-000",
    state: "LEASED",
    attempts: 1,
    max_attempts: 3,
    node_id: "node-0",
    deadline: null,
    ...over,
  };
}

function manifest(over: Partial<CheckpointManifest>): CheckpointManifest {
  return {
    manifest_id: "m1",
    // The catalog's composite scope, deliberately: this field is NOT the
    // job id the console asked about, and nothing may match it against one.
    job_id: "job-abc::task-000",
    attempt_id: "lease-1",
    step: 1200,
    framework: "",
    strategy_family: "",
    world_size: 1,
    compatible_world_sizes: [],
    storage_prefix: "artifact://jobs/job-abc/task-000/ckpt/1200/",
    parts: [
      { key: "jobs/job-abc/task-000/ckpt/step-1200.json", sha256: "aa", size_bytes: 10 },
    ],
    validation: "hash_verified",
    created: "2026-08-11T10:00:00Z",
    checkpoint_duration_s: null,
    ...over,
  };
}

function attempt(over: Partial<Attempt>): Attempt {
  return {
    taskId: "task-000",
    nodeId: "node-0",
    startedAt: Date.parse("2026-08-11T10:00:00Z"),
    endedAt: null,
    outcome: "running",
    ...over,
  };
}

describe("checkpointStepFromKey", () => {
  it("reads the step out of the key flashnode's relay actually writes", () => {
    // The agent uploads each new `out/ckpt/step-*.json` to
    // `{output_prefix}ckpt/{filename}`, and the listing route returns keys
    // already relative to the job's own prefix — so what arrives here is
    // `{task_id}/ckpt/step-<n>.json`. This console and the agent that writes
    // these files live in different repositories with nothing binding them,
    // so this test is what makes the convention stay true.
    expect(checkpointStepFromKey("task-000/ckpt/step-1200.json")).toBe(1200);
  });

  it("tolerates zero padding and a different extension", () => {
    expect(checkpointStepFromKey("task-000/ckpt/step-000040.json")).toBe(40);
    expect(checkpointStepFromKey("task-000/ckpt/step-7.pt")).toBe(7);
    expect(checkpointStepFromKey("task-000/ckpt/step-7")).toBe(7);
  });

  it("accepts step 0, which is a real checkpoint and not an absence", () => {
    expect(checkpointStepFromKey("task-000/ckpt/step-0.json")).toBe(0);
  });

  it("does not relabel output that merely looks like a step file", () => {
    // Requires the `ckpt/` directory as well as the name. A workload writing
    // `step-3.json` among its results must keep reading as output: calling
    // someone's deliverable a checkpoint is worse than leaving a checkpoint
    // unlabelled.
    expect(checkpointStepFromKey("task-000/step-3.json")).toBeNull();
    expect(checkpointStepFromKey("task-000/ckpt/latest.json")).toBeNull();
    expect(checkpointStepFromKey("task-000/ckpt/step-abc.json")).toBeNull();
    expect(checkpointStepFromKey("ckpt")).toBeNull();
  });
});

describe("selectTasksForCheckpointRead", () => {
  it("asks about tasks that could still lose work, and no others", () => {
    const { taskIds } = selectTasksForCheckpointRead([
      task({ task_id: "leased", state: "LEASED", attempts: 1 }),
      task({ task_id: "requeued", state: "PENDING", attempts: 2 }),
      task({ task_id: "dead", state: "FAILED", attempts: 3 }),
      task({ task_id: "accepted", state: "COMPLETED", attempts: 1 }),
      task({ task_id: "cancelled", state: "CANCELLED", attempts: 1 }),
      task({ task_id: "never-claimed", state: "PENDING", attempts: 0 }),
    ]);
    expect(taskIds).toEqual(["dead", "leased", "requeued"]);
  });

  it("asks about nothing for a federated job, and says why", () => {
    // A federated run is one coordinator job PER ROUND, and the checkpoint
    // route is scoped to a coordinator job id — so the umbrella id this page
    // holds would 404 for every task. Reading nothing is right; reporting
    // those 404s as "no checkpoints" would not be.
    const selection = selectTasksForCheckpointRead([
      task({ task_id: "task-000", round: 0 }),
    ]);
    expect(selection.federated).toBe(true);
    expect(selection.taskIds).toEqual([]);
  });

  it("bounds the fan-out and reports what it left out", () => {
    const many = Array.from({ length: MAX_CHECKPOINT_READS + 4 }, (_, i) =>
      task({ task_id: `task-${String(i).padStart(3, "0")}`, state: "LEASED" })
    );
    const { taskIds, skipped } = selectTasksForCheckpointRead(many);
    expect(taskIds).toHaveLength(MAX_CHECKPOINT_READS);
    expect(skipped).toBe(4);
  });
});

describe("summariseCheckpoints — the happy path", () => {
  it("reports the committed step, when it was committed, and the runtime's own validation verdict", () => {
    const panel = summariseCheckpoints({
      tasks: [task({ task_id: "task-000", state: "LEASED", attempts: 1 })],
      reads: {
        "task-000": { status: "committed", manifest: manifest({ step: 1200 }) },
      },
      attempts: [],
    });

    expect(panel.state).toBe("rows");
    expect(panel.rows).toHaveLength(1);
    expect(panel.rows[0]).toMatchObject({
      taskId: "task-000",
      kind: "committed",
      step: 1200,
      committedAt: "2026-08-11T10:00:00Z",
      validation: "hash_verified",
      errorDetail: null,
    });
    expect(panel.ambiguityNote).toBeNull();
  });

  it("passes an unfamiliar validation verdict through rather than dropping it", () => {
    const panel = summariseCheckpoints({
      tasks: [task({})],
      reads: {
        "task-000": {
          status: "committed",
          manifest: manifest({ validation: "restore_verified" }),
        },
      },
      attempts: [],
    });
    expect(panel.rows[0].validation).toBe("restore_verified");
  });
});

describe("summariseCheckpoints — the 404 empty state", () => {
  it("says no checkpoint yet, and refuses to say which of the two reasons it is", () => {
    const panel = summariseCheckpoints({
      tasks: [task({})],
      reads: { "task-000": { status: "none" } },
      attempts: [],
    });

    expect(panel.rows[0].kind).toBe("none");
    // Nothing fabricated in place of the answer that did not come.
    expect(panel.rows[0].step).toBeNull();
    expect(panel.rows[0].committedAt).toBeNull();
    // A 404 means "no valid checkpoint" and nothing more: it cannot tell a
    // task that has not reached its first checkpoint from a workload that
    // never writes one, so the note must name both and pick neither.
    expect(panel.ambiguityNote).toMatch(/not written its first one yet/);
    expect(panel.ambiguityNote).toMatch(/does not checkpoint/);
    expect(panel.ambiguityNote).toMatch(/does not guess/);
  });
});

describe("summariseCheckpoints — the error state", () => {
  it("reports a failed read as unknown, never as 'no checkpoints'", () => {
    // The distinction this whole module exists for. A 502 rendered as "no
    // checkpoints" tells someone their run has no resume point when the only
    // thing that happened is that a gateway blipped.
    const panel = summariseCheckpoints({
      tasks: [task({})],
      reads: {
        "task-000": { status: "unknown", detail: "502 Bad Gateway" },
      },
      attempts: [],
    });

    expect(panel.rows[0].kind).toBe("unknown");
    expect(panel.rows[0].step).toBeNull();
    expect(panel.rows[0].errorDetail).toBe("502 Bad Gateway");
    // No 404 happened, so the "might not checkpoint at all" note must not
    // appear and lend the failure a benign explanation.
    expect(panel.ambiguityNote).toBeNull();
  });

  it("keeps a task that has not answered yet apart from one that answered 404", () => {
    const panel = summariseCheckpoints({
      tasks: [
        task({ task_id: "task-000" }),
        task({ task_id: "task-001" }),
      ],
      reads: { "task-001": { status: "none" } },
      attempts: [],
    });
    expect(panel.rows.find((r) => r.taskId === "task-000")!.kind).toBe("reading");
    expect(panel.rows.find((r) => r.taskId === "task-001")!.kind).toBe("none");
  });

  it("collapses a credential refusal into one statement about the console, not per-task rows", () => {
    // 401 from these routes is a property of the API — they are declared
    // `tags=["agent"]` and refuse a browser JWT by design — so it is the
    // same answer for every task and says nothing about the job.
    const panel = summariseCheckpoints({
      tasks: [task({ task_id: "task-000" }), task({ task_id: "task-001" })],
      reads: { "task-000": { status: "unreadable" } },
      attempts: [],
    });
    expect(panel.state).toBe("unreadable");
    expect(panel.rows).toEqual([]);
  });

  it("stays unreadable after the refused task drops out of the selection", () => {
    // The page stops asking once refused, so no newly at-risk task will ever
    // get an answer. Were the refusal scoped to the current selection, the
    // panel would quietly become a table of tasks reading forever.
    const panel = summariseCheckpoints({
      tasks: [
        task({ task_id: "task-000", state: "COMPLETED" }),
        task({ task_id: "task-001", state: "LEASED" }),
      ],
      reads: { "task-000": { status: "unreadable" } },
      attempts: [],
    });
    expect(panel.state).toBe("unreadable");
  });
});

describe("summariseCheckpoints — resumed rather than restarted", () => {
  const committed = "2026-08-11T10:00:00Z";
  const claimedAfter = Date.parse("2026-08-11T10:00:30Z");
  const claimedBefore = Date.parse("2026-08-11T09:59:30Z");

  it("claims a resume only when a retry was claimed after a checkpoint existed", () => {
    const panel = summariseCheckpoints({
      tasks: [task({ task_id: "task-000", attempts: 2 })],
      reads: {
        "task-000": {
          status: "committed",
          manifest: manifest({ step: 900, created: committed }),
        },
      },
      attempts: [
        attempt({ startedAt: claimedBefore, nodeId: "node-dead" }),
        attempt({ startedAt: claimedAfter, nodeId: "node-live" }),
      ],
    });

    expect(panel.rows[0].resumedFromStep).toBe(900);
    expect(panel.rows[0].latestAttemptStartedAt).toBe(claimedAfter);
    expect(panel.resumeNote).toMatch(/handed that checkpoint/);
  });

  it("makes no resume claim on a first attempt, however good the checkpoint", () => {
    const panel = summariseCheckpoints({
      tasks: [task({ task_id: "task-000", attempts: 1 })],
      reads: {
        "task-000": { status: "committed", manifest: manifest({ created: committed }) },
      },
      attempts: [attempt({ startedAt: claimedAfter })],
    });
    expect(panel.rows[0].resumedFromStep).toBeNull();
    expect(panel.resumeNote).toBeNull();
  });

  it("makes no resume claim when the checkpoint postdates the attempt — that is this attempt's own work", () => {
    const panel = summariseCheckpoints({
      tasks: [task({ task_id: "task-000", attempts: 2 })],
      reads: {
        "task-000": { status: "committed", manifest: manifest({ created: committed }) },
      },
      attempts: [
        attempt({ startedAt: claimedBefore - 60_000, nodeId: "node-dead" }),
        attempt({ startedAt: claimedBefore, nodeId: "node-live" }),
      ],
    });
    expect(panel.rows[0].resumedFromStep).toBeNull();
  });

  it("makes no resume claim when the ledger records no lease for the task", () => {
    const panel = summariseCheckpoints({
      tasks: [task({ task_id: "task-000", attempts: 2 })],
      reads: {
        "task-000": { status: "committed", manifest: manifest({ created: committed }) },
      },
      attempts: [],
    });
    expect(panel.rows[0].resumedFromStep).toBeNull();
    expect(panel.rows[0].latestAttemptStartedAt).toBeNull();
  });
});

describe("summariseCheckpoints — nothing to say", () => {
  it("renders nothing at all when the job has no task breakdown", () => {
    const panel = summariseCheckpoints({ tasks: [], reads: {}, attempts: [] });
    expect(panel.state).toBe("absent");
  });

  it("renders nothing for a federated job rather than an empty checkpoint table", () => {
    const panel = summariseCheckpoints({
      tasks: [task({ round: 0 })],
      reads: {},
      attempts: [],
    });
    expect(panel.state).toBe("absent");
  });

  it("says the job is settled when no task can lose work", () => {
    const panel = summariseCheckpoints({
      tasks: [
        task({ task_id: "task-000", state: "COMPLETED" }),
        task({ task_id: "task-001", state: "COMPLETED" }),
      ],
      reads: {},
      attempts: [],
    });
    expect(panel.state).toBe("settled");
    expect(panel.rows).toEqual([]);
  });

  it("reports truncation rather than quietly showing a subset", () => {
    const many = Array.from({ length: MAX_CHECKPOINT_READS + 2 }, (_, i) =>
      task({ task_id: `task-${String(i).padStart(3, "0")}`, state: "LEASED" })
    );
    const reads: Record<string, TaskCheckpointRead> = {};
    for (const t of many) reads[t.task_id] = { status: "none" };

    const panel = summariseCheckpoints({ tasks: many, reads, attempts: [] });
    expect(panel.rows).toHaveLength(MAX_CHECKPOINT_READS);
    expect(panel.truncationNote).toBe(
      `Showing ${MAX_CHECKPOINT_READS} of ${MAX_CHECKPOINT_READS + 2} tasks that could still lose work.`
    );
  });
});
