import { describe, expect, it } from "vitest";

import { summariseArtifactGroup } from "./artifact-summary";
import { groupArtifactsByTask } from "@/lib/task-artifacts";
import type { JobArtifactEntry, JobTask } from "@/lib/cloud-api";

// Built through `groupArtifactsByTask` rather than by hand: the summary line
// has to agree with the grouping the card actually renders, and a hand-built
// `ArtifactGroup` could disagree with it silently.
// `size_bytes` is typed as a number, and the null is the point of one of
// these tests: the listing can omit a size, and `lib/task-artifacts.ts`
// normalises anything non-finite to null. The cast is how a test reaches
// that path through a contract type that does not describe it.
function entry(key: string, size: number | null): JobArtifactEntry {
  return { key, size_bytes: size as number };
}

function task(taskId: string, state: JobTask["state"]): JobTask {
  return {
    task_id: taskId,
    state,
    attempts: 1,
    max_attempts: 3,
    node_id: null,
    deadline: null,
  };
}

describe("summariseArtifactGroup", () => {
  it("counts output files and checkpoints separately and sums every byte", () => {
    const [group] = groupArtifactsByTask(
      [
        entry("task-000/result.json", 1024),
        entry("task-000/stdout.txt", 2048),
        entry("task-000/stderr.txt", 512),
        entry("task-000/ckpt/step-1.json", 64),
        entry("task-000/ckpt/step-2.json", 64),
      ],
      [task("task-000", "COMPLETED")]
    );
    expect(summariseArtifactGroup(group)).toEqual({
      fileCount: 3,
      checkpointCount: 2,
      totalBytes: 1024 + 2048 + 512 + 64 + 64,
      totalIsPartial: false,
    });
  });

  it("counts a file whose size the listing did not report, and flags the total as a floor", () => {
    const [group] = groupArtifactsByTask(
      [entry("task-000/a.bin", 100), entry("task-000/b.bin", null)],
      []
    );
    expect(summariseArtifactGroup(group)).toEqual({
      fileCount: 2,
      checkpointCount: 0,
      totalBytes: 100,
      totalIsPartial: true,
    });
  });

  it("reports zeroes for a group of checkpoints and nothing else", () => {
    const [group] = groupArtifactsByTask(
      [entry("task-000/ckpt/step-1.json", 8)],
      []
    );
    expect(summariseArtifactGroup(group)).toMatchObject({
      fileCount: 0,
      checkpointCount: 1,
      totalBytes: 8,
    });
  });
});
