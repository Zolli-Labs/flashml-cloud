import { describe, expect, it } from "vitest";

import {
  ARTIFACTS_UNREADABLE_MESSAGE,
  summariseJobArtifacts,
  type ArtifactsRead,
} from "./job-artifacts";
import type { JobArtifactEntry, JobArtifactListing, JobTask } from "./cloud-api";

function listing(over: Partial<JobArtifactListing> = {}): JobArtifactListing {
  return {
    artifacts: [],
    storage: "coordinator",
    mirrored_at: null,
    ...over,
  };
}

function listed(
  artifacts: JobArtifactEntry[],
  over: Partial<JobArtifactListing> = {}
): ArtifactsRead {
  return { status: "listed", listing: listing({ artifacts, ...over }) };
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

/** What a finished two-shard run actually leaves behind: a result and logs
 * per task, checkpoints under `ckpt/`, and a reducer's merged output at a
 * job-level bucket no task owns. */
const REAL_KEYS: JobArtifactEntry[] = [
  { key: "task-000/result.json", size_bytes: 2048 },
  { key: "task-000/logs/stdout.txt", size_bytes: 512 },
  { key: "task-000/logs/stderr.txt", size_bytes: 64 },
  { key: "task-000/ckpt/step-20.json", size_bytes: 1_048_576 },
  { key: "task-000/ckpt/step-40.json", size_bytes: 1_048_576 },
  { key: "task-001/result.json", size_bytes: 4096 },
  { key: "reduced/rows.jsonl", size_bytes: 8192 },
];

describe("summariseJobArtifacts — the happy path", () => {
  it("lists what the API returned, grouped by producing task", () => {
    const panel = summariseJobArtifacts({
      read: listed(REAL_KEYS),
      jobState: "SUCCEEDED",
      tasks: [task({ task_id: "task-000" }), task({ task_id: "task-001" })],
    });

    expect(panel.state).toBe("files");
    expect(panel.fileCount).toBe(7);
    expect(panel.groups.map((g) => g.groupId)).toEqual([
      "task-000",
      "task-001",
      "reduced",
    ]);
  });

  it("totals every reported size and does not call it partial", () => {
    const panel = summariseJobArtifacts({
      read: listed(REAL_KEYS),
      jobState: "SUCCEEDED",
      tasks: [],
    });
    expect(panel.totalBytes).toBe(2048 + 512 + 64 + 1_048_576 * 2 + 4096 + 8192);
    expect(panel.totalIsPartial).toBe(false);
  });

  it("sums only readable sizes and marks the total a floor when one is not", () => {
    const panel = summariseJobArtifacts({
      read: listed([
        { key: "task-000/a.bin", size_bytes: 100 },
        { key: "task-000/b.bin", size_bytes: null as unknown as number },
      ]),
      jobState: "SUCCEEDED",
      tasks: [],
    });
    // An unknown size is left out entirely rather than counted as 0: a total
    // that silently absorbed it would be indistinguishable from a real one.
    expect(panel.totalBytes).toBe(100);
    expect(panel.totalIsPartial).toBe(true);
  });

  it("offers the clear action only for a job that has stopped writing", () => {
    const forRunning = summariseJobArtifacts({
      read: listed(REAL_KEYS),
      jobState: "RUNNING",
      tasks: [],
    });
    const forFinished = summariseJobArtifacts({
      read: listed(REAL_KEYS),
      jobState: "PARTIAL",
      tasks: [],
    });
    expect(forRunning.canClear).toBe(false);
    // PARTIAL counts as stopped — see `CLEARABLE_STATES`' own doc.
    expect(forFinished.canClear).toBe(true);
  });
});

describe("summariseJobArtifacts — checkpoints are not results", () => {
  it("keeps a task's checkpoints out of its results while still listing them", () => {
    const panel = summariseJobArtifacts({
      read: listed(REAL_KEYS),
      jobState: "SUCCEEDED",
      tasks: [task({ task_id: "task-000" })],
    });
    const t0 = panel.groups.find((g) => g.groupId === "task-000")!;

    expect(t0.results.map((a) => a.key)).toEqual([
      "task-000/result.json",
      "task-000/logs/stdout.txt",
      "task-000/logs/stderr.txt",
    ]);
    expect(t0.checkpoints.map((a) => a.checkpointStep)).toEqual([20, 40]);
    // Both halves still count towards the file count and the total: they are
    // real files somebody's quota is paying for.
    expect(panel.fileCount).toBe(7);
  });

  it("puts a failed task's group first, so its logs are the obvious thing to open", () => {
    const panel = summariseJobArtifacts({
      read: listed([
        ...REAL_KEYS,
        // task-001 failed, and unlike in REAL_KEYS it left a log behind —
        // which is the only thing that earns the group its promotion.
        { key: "task-001/logs/stderr.txt", size_bytes: 900 },
      ]),
      jobState: "PARTIAL",
      tasks: [
        task({ task_id: "task-000", state: "COMPLETED" }),
        task({ task_id: "task-001", state: "FAILED" }),
      ],
    });
    expect(panel.groups[0].groupId).toBe("task-001");
    expect(panel.groups[0].hasFailureLog).toBe(true);
  });

  it("does not claim a failure log for a failed task that wrote none", () => {
    const panel = summariseJobArtifacts({
      read: listed([{ key: "task-001/result.json", size_bytes: 10 }]),
      jobState: "FAILED",
      tasks: [task({ task_id: "task-001", state: "FAILED" })],
    });
    expect(panel.groups[0].hasFailureLog).toBe(false);
  });
});

describe("summariseJobArtifacts — where the bytes are", () => {
  it("says so, with the reported time, when the job is mirrored to OSS", () => {
    const panel = summariseJobArtifacts({
      read: listed(REAL_KEYS, {
        storage: "oss",
        mirrored_at: "2026-08-11T10:00:00Z",
      }),
      jobState: "SUCCEEDED",
      tasks: [],
    });
    expect(panel.storage).toBe("oss");
    expect(panel.mirroredAt).toBe("2026-08-11T10:00:00Z");
    expect(panel.storageNote).toBe("Mirrored to Alibaba OSS.");
  });

  it("does not invent a mirror time the API did not report", () => {
    const panel = summariseJobArtifacts({
      read: listed(REAL_KEYS, { storage: "oss", mirrored_at: null }),
      jobState: "SUCCEEDED",
      tasks: [],
    });
    expect(panel.mirroredAt).toBeNull();
    expect(panel.storageNote).toBe(
      "Mirrored to Alibaba OSS. The API did not report when."
    );
  });

  it("claims no durability for a job still only on the coordinator", () => {
    const panel = summariseJobArtifacts({
      read: listed(REAL_KEYS, { storage: "coordinator" }),
      jobState: "SUCCEEDED",
      tasks: [],
    });
    expect(panel.storageNote).toBe(
      "On the coordinator only — not mirrored to object storage."
    );
    expect(panel.storageNote).not.toMatch(/backed up|durable|safe/i);
  });

  it("quotes back a storage value it does not recognise instead of guessing", () => {
    const panel = summariseJobArtifacts({
      read: listed(REAL_KEYS, { storage: "r2" }),
      jobState: "SUCCEEDED",
      tasks: [],
    });
    expect(panel.storage).toBe("r2");
    expect(panel.storageNote).toContain('"r2"');
    expect(panel.storageNote).toContain("does not recognise");
  });
});

describe("summariseJobArtifacts — empty is not the same as unreadable", () => {
  it("says a finished job wrote nothing when the listing is genuinely empty", () => {
    const panel = summariseJobArtifacts({
      read: listed([]),
      jobState: "SUCCEEDED",
      tasks: [],
    });
    expect(panel.state).toBe("empty");
    expect(panel.emptyMessage).toBe(
      "This job finished without writing any artifacts."
    );
    expect(panel.errorMessage).toBeNull();
  });

  it("treats a running job with nothing yet as in flight, not as an error or a verdict", () => {
    const panel = summariseJobArtifacts({
      read: listed([]),
      jobState: "RUNNING",
      tasks: [task({ state: "LEASED" })],
    });
    expect(panel.state).toBe("empty");
    expect(panel.emptyMessage).toBe(
      "No artifacts yet. Files appear here as tasks commit their output."
    );
    expect(panel.emptyMessage).not.toMatch(/no artifacts were produced/i);
  });

  it("says nothing about where bytes are when there are none", () => {
    const panel = summariseJobArtifacts({
      read: listed([], { storage: "coordinator" }),
      jobState: "SUCCEEDED",
      tasks: [],
    });
    expect(panel.storageNote).toBeNull();
    // The values the API did send are still carried, unaltered.
    expect(panel.storage).toBe("coordinator");
  });

  it("renders a failed read as unreadable — never as a job with no artifacts", () => {
    const panel = summariseJobArtifacts({
      read: { status: "unreadable", detail: "unknown job" },
      jobState: "SUCCEEDED",
      tasks: [],
    });
    expect(panel.state).toBe("unreadable");
    expect(panel.errorMessage).toBe(ARTIFACTS_UNREADABLE_MESSAGE);
    expect(panel.emptyMessage).toBeNull();
    expect(panel.groups).toEqual([]);
  });

  it("carries the API's own words for the failure rather than paraphrasing them", () => {
    const panel = summariseJobArtifacts({
      read: { status: "unreadable", detail: "502 Bad Gateway" },
      jobState: "RUNNING",
      tasks: [],
    });
    expect(panel.errorDetail).toBe("502 Bad Gateway");
  });

  it("never offers an irreversible clear on the strength of a listing nobody could read", () => {
    const panel = summariseJobArtifacts({
      read: { status: "unreadable", detail: "unknown job" },
      jobState: "SUCCEEDED",
      tasks: [],
    });
    expect(panel.canClear).toBe(false);
  });

  it("is loading until an answer arrives, and says neither empty nor failed meanwhile", () => {
    const panel = summariseJobArtifacts({
      read: { status: "loading" },
      jobState: "RUNNING",
      tasks: [],
    });
    expect(panel.state).toBe("loading");
    expect(panel.emptyMessage).toBeNull();
    expect(panel.errorMessage).toBeNull();
    expect(panel.canClear).toBe(false);
  });
});
