import { describe, expect, it } from "vitest";
import {
  earlierJobs,
  isActiveJob,
  isEarlierJob,
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

describe("isEarlierJob", () => {
  it("treats both null and absent as having no workspace", () => {
    // null: an API that has the column and this job has no pool.
    // undefined: an API deployed before the field existed. Same answer.
    expect(isEarlierJob(job({ pool_id: null }))).toBe(true);
    expect(isEarlierJob(job())).toBe(true);
  });

  it("is false for a job in a workspace", () => {
    expect(isEarlierJob(job({ pool_id: "vision" }))).toBe(false);
  });
});

describe("partitioning", () => {
  const jobs = [
    job({ job_id: "a", pool_id: "vision" }),
    job({ job_id: "b", pool_id: "robotics" }),
    job({ job_id: "c", pool_id: null }),
    job({ job_id: "d" }),
  ];

  it("splits into this workspace and the earlier pile", () => {
    expect(jobsInWorkspace(jobs, "vision").map((j) => j.job_id)).toEqual(["a"]);
    expect(earlierJobs(jobs).map((j) => j.job_id)).toEqual(["c", "d"]);
  });

  it("never puts one job in both halves", () => {
    const inWs = new Set(jobsInWorkspace(jobs, "vision").map((j) => j.job_id));
    for (const j of earlierJobs(jobs)) expect(inWs.has(j.job_id)).toBe(false);
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
