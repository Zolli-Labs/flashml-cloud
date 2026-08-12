import { describe, expect, it } from "vitest";

import { MANY_FILES_THRESHOLD, planBulkDownload } from "./bulk-download";
import { groupArtifactsByTask } from "./task-artifacts";
import type { JobArtifactEntry, JobTask } from "./cloud-api";

/** Groups exactly as the card builds them — from a real listing through the
 * real grouping — rather than hand-assembled, so a change to how the listing
 * is grouped cannot leave this suite testing a shape nothing produces. */
function groupsOf(entries: JobArtifactEntry[], tasks: JobTask[] = []) {
  return groupArtifactsByTask(entries, tasks);
}

function entry(key: string, sizeBytes = 100): JobArtifactEntry {
  return { key, size_bytes: sizeBytes };
}

describe("planBulkDownload", () => {
  it("plans nothing for a job with no artifacts", () => {
    const plan = planBulkDownload([]);
    expect(plan.files).toEqual([]);
    expect(plan.totalBytes).toBe(0);
    expect(plan.sizeIsPartial).toBe(false);
  });

  it("carries every listed key through, in group order", () => {
    const plan = planBulkDownload(
      groupsOf([
        entry("task-000/stdout", 100),
        entry("task-001/result.json", 200),
      ])
    );
    expect(plan.files).toHaveLength(2);
    expect(plan.files[0].key).toBe("task-000/stdout");
    expect(plan.files[1].key).toBe("task-001/result.json");
    expect(plan.totalBytes).toBe(300);
    expect(plan.sizeIsPartial).toBe(false);
  });

  it("includes a task's checkpoints, not only its results", () => {
    // The card separates them so nobody mistakes recovery machinery for job
    // output. A button labelled "all" that then skipped them would be the
    // same misrepresentation pointed the other way.
    const plan = planBulkDownload(
      groupsOf([
        entry("task-000/result.json"),
        entry("task-000/ckpt/step-20.json"),
      ])
    );
    expect(plan.files.map((f) => f.key)).toEqual([
      "task-000/result.json",
      "task-000/ckpt/step-20.json",
    ]);
  });

  it("flattens a key's path separators into a collision-safe filename", () => {
    // Two different tasks both writing a file named `stdout` must not save
    // to the same filename — that is exactly the "which task was this"
    // information a person opens the file to find, and a browser silently
    // appending `(1)` to the second one erases it. (The browser only honours
    // this for a same-origin url — see `DownloadableArtifact`.)
    const plan = planBulkDownload(
      groupsOf([entry("task-000/stdout"), entry("task-001/stdout")])
    );
    expect(plan.files[0].filename).toBe("task-000__stdout");
    expect(plan.files[1].filename).toBe("task-001__stdout");
    expect(plan.files[0].filename).not.toBe(plan.files[1].filename);
  });

  it("marks the total as partial when any artifact's size is unknown", () => {
    const plan = planBulkDownload(
      groupsOf([
        entry("task-000/a", 100),
        { key: "task-001/x", size_bytes: null as unknown as number },
      ])
    );
    // Only the known sizes are summed — an unknown size must not silently
    // become 0 and be added in as if it were measured.
    expect(plan.totalBytes).toBe(100);
    expect(plan.sizeIsPartial).toBe(true);
  });

  it("does not mark the total as partial when every size is known", () => {
    const plan = planBulkDownload(
      groupsOf([entry("task-000/a", 100), entry("task-000/b", 50)])
    );
    expect(plan.sizeIsPartial).toBe(false);
    expect(plan.totalBytes).toBe(150);
  });
});

describe("MANY_FILES_THRESHOLD", () => {
  it("is a real cutoff a caller can compare a file count against", () => {
    // The exact number is a judgment call (see its doc comment in
    // bulk-download.ts) — this test only pins that it exists as a usable
    // positive integer, so a caller's `files.length > MANY_FILES_THRESHOLD`
    // check has something meaningful to compare against.
    expect(MANY_FILES_THRESHOLD).toBeGreaterThan(0);
    expect(Number.isInteger(MANY_FILES_THRESHOLD)).toBe(true);
  });
});
