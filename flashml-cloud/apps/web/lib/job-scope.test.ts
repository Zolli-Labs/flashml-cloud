import { describe, expect, it } from "vitest";
import {
  isActiveJob,
  isInWorkspace,
  jobsInWorkspace,
} from "./job-scope";
import type { JobRecord, JobState } from "./cloud-api";

function job(overrides: Partial<JobRecord> = {}): JobRecord {
  return { job_id: "j1", state: "RUNNING", ...overrides };
}

describe("isInWorkspace", () => {
  it("matches on pool_id", () => {
    expect(isInWorkspace(job({ pool_id: "vision" }), "vision")).toBe(true);
    expect(isInWorkspace(job({ pool_id: "robotics" }), "vision")).toBe(false);
  });

  it("never claims a job with no pool_id", () => {
    // The dangerous default. If absence read as "belongs here", one member's
    // pre-pools jobs would render to their whole team.
    expect(isInWorkspace(job({ pool_id: null }), "vision")).toBe(false);
    expect(isInWorkspace(job(), "vision")).toBe(false);
  });
});

describe("jobsInWorkspace", () => {
  const jobs = [
    job({ job_id: "a", pool_id: "vision" }),
    job({ job_id: "b", pool_id: "robotics" }),
    job({ job_id: "c", pool_id: null }),
    job({ job_id: "d" }),
  ];

  it("selects only jobs matching the given pool_id", () => {
    expect(jobsInWorkspace(jobs, "vision").map((j) => j.job_id)).toEqual(["a"]);
  });

  it("never includes a job with no pool_id", () => {
    const inWs = jobsInWorkspace(jobs, "vision").map((j) => j.job_id);
    expect(inWs).not.toContain("c");
    expect(inWs).not.toContain("d");
  });
});

describe("isActiveJob", () => {
  it("is false for every terminal state", () => {
    for (const state of ["SUCCEEDED", "FAILED", "CANCELLED"] satisfies JobState[]) {
      expect(isActiveJob(job({ state }))).toBe(false);
    }
  });

  it("is true for anything still in flight", () => {
    for (const state of ["PENDING", "SUBMITTED", "RUNNING"] satisfies JobState[]) {
      expect(isActiveJob(job({ state }))).toBe(true);
    }
  });
});
