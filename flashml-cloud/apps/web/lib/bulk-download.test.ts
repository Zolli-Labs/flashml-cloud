import { describe, expect, it } from "vitest";

import { MANY_FILES_THRESHOLD, planBulkDownload } from "./bulk-download";
import type { ArtifactRecord } from "./cloud-api";

function artifact(over: Partial<ArtifactRecord>): ArtifactRecord {
  return {
    uri: "artifact://jobs/job-abc/task-000/stdout",
    backend: "minio",
    bucket: "flashml",
    object_key: "jobs/job-abc/task-000/stdout",
    etag: null,
    sha256: null,
    size_bytes: 100,
    created_at: "2026-08-01T00:00:00Z",
    ...over,
  };
}

describe("planBulkDownload", () => {
  it("plans nothing for a job with no artifacts", () => {
    const plan = planBulkDownload("job-abc", []);
    expect(plan.files).toEqual([]);
    expect(plan.totalBytes).toBe(0);
    expect(plan.sizeIsPartial).toBe(false);
  });

  it("resolves every artifact under the job's own key, in order", () => {
    const artifacts = [
      artifact({ uri: "artifact://jobs/job-abc/task-000/stdout", size_bytes: 100 }),
      artifact({ uri: "artifact://jobs/job-abc/task-001/result.json", size_bytes: 200 }),
    ];
    const plan = planBulkDownload("job-abc", artifacts);
    expect(plan.files).toHaveLength(2);
    expect(plan.files[0].key).toBe("task-000/stdout");
    expect(plan.files[1].key).toBe("task-001/result.json");
    expect(plan.totalBytes).toBe(300);
    expect(plan.sizeIsPartial).toBe(false);
  });

  it("excludes an artifact that does not resolve under this job's own prefix", () => {
    // The same case `jobArtifactKey` documents: a staged input-code upload
    // is not a result, and there is no browser-readable route for it at
    // all — "download all" must not try and silently fail on it.
    const artifacts = [
      artifact({ uri: "artifact://jobs/job-abc/task-000/stdout" }),
      artifact({ uri: "artifact://staged/some-other-thing" }),
    ];
    const plan = planBulkDownload("job-abc", artifacts);
    expect(plan.files).toHaveLength(1);
  });

  it("flattens a key's path separators into a collision-safe filename", () => {
    // Two different tasks both writing a file named `stdout` must not save
    // to the same filename — that is exactly the "which task was this"
    // information a person opens the file to find, and a browser silently
    // appending `(1)` to the second one erases it.
    const artifacts = [
      artifact({ uri: "artifact://jobs/job-abc/task-000/stdout" }),
      artifact({ uri: "artifact://jobs/job-abc/task-001/stdout" }),
    ];
    const plan = planBulkDownload("job-abc", artifacts);
    expect(plan.files[0].filename).toBe("task-000__stdout");
    expect(plan.files[1].filename).toBe("task-001__stdout");
    expect(plan.files[0].filename).not.toBe(plan.files[1].filename);
  });

  it("marks the total as partial when any artifact's size is unknown", () => {
    const artifacts = [
      artifact({ size_bytes: 100 }),
      artifact({ uri: "artifact://jobs/job-abc/task-001/x", size_bytes: null }),
    ];
    const plan = planBulkDownload("job-abc", artifacts);
    // Only the known sizes are summed — an unknown size must not silently
    // become 0 and be added in as if it were measured.
    expect(plan.totalBytes).toBe(100);
    expect(plan.sizeIsPartial).toBe(true);
  });

  it("does not mark the total as partial when every size is known", () => {
    const artifacts = [artifact({ size_bytes: 100 }), artifact({ size_bytes: 50 })];
    const plan = planBulkDownload("job-abc", artifacts);
    expect(plan.sizeIsPartial).toBe(false);
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
