import { describe, expect, it } from "vitest";

import { clearableJobs, describeClearedArtifacts } from "./job-artifact-cleanup";
import type { JobRecord } from "./cloud-api";

function job(over: Partial<JobRecord>): JobRecord {
  return {
    job_id: "job-abc",
    state: "SUCCEEDED",
    ...over,
  } as JobRecord;
}

describe("clearableJobs", () => {
  it("offers every terminal state the API accepts without a 409, including PARTIAL", () => {
    const jobs = [
      job({ job_id: "j-succeeded", state: "SUCCEEDED" }),
      job({ job_id: "j-partial", state: "PARTIAL" }),
      job({ job_id: "j-failed", state: "FAILED" }),
      job({ job_id: "j-cancelled", state: "CANCELLED" }),
    ];
    const result = clearableJobs(jobs);
    expect(result.map((r) => r.jobId)).toEqual([
      "j-succeeded",
      "j-partial",
      "j-failed",
      "j-cancelled",
    ]);
  });

  it("excludes a job that could still be writing into its own output directory", () => {
    const jobs = [
      job({ job_id: "j-pending", state: "PENDING" }),
      job({ job_id: "j-submitted", state: "SUBMITTED" }),
      job({ job_id: "j-running", state: "RUNNING" }),
      job({ job_id: "j-recovering", state: "RECOVERING" }),
    ];
    expect(clearableJobs(jobs)).toEqual([]);
  });

  it("labels a coordinator job by its spec name", () => {
    const [row] = clearableJobs([
      job({
        job_id: "job-abc",
        state: "SUCCEEDED",
        spec: {
          metadata: { name: "sweep" },
          spec: {
            image: { repository: "r", tag: "t" },
            workload: { type: "w", parameters: {} },
            resources: { minimumWorkers: 1, maximumWorkers: 1 },
            isolation: { tier: "t", allowFallback: false },
          },
        },
      }),
    ]);
    expect(row.label).toBe("sweep");
  });

  it("labels a federated job by its own name when it has no spec", () => {
    const [row] = clearableJobs([
      job({ job_id: "fed-1", state: "SUCCEEDED", name: "federated-mlp", mode: "federated" }),
    ]);
    expect(row.label).toBe("federated-mlp");
  });

  it("falls back to the job id when neither a spec name nor a name is present", () => {
    const [row] = clearableJobs([job({ job_id: "job-bare", state: "FAILED" })]);
    expect(row.label).toBe("job-bare");
  });
});

describe("describeClearedArtifacts", () => {
  it("reports bytes freed and a pluralised file count", () => {
    expect(
      describeClearedArtifacts({ deleted_files: 12, freed_bytes: 134217728 })
    ).toBe("Freed 128.0 MiB across 12 files.");
  });

  it("keeps a single file singular", () => {
    expect(describeClearedArtifacts({ deleted_files: 1, freed_bytes: 512 })).toBe(
      "Freed 512 B across 1 file."
    );
  });
});
