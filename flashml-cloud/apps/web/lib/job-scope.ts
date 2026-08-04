import type { JobRecord } from "./cloud-api";

/** Job states that mean "this is over". Lived inline in
 * `overview/page.tsx` and was about to be copied into the workspace
 * provider; one definition instead, since the provider's polling rule and
 * the overview's active list must agree on what "still running" means. */
export const TERMINAL_STATES = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);

export function isActiveJob(job: JobRecord): boolean {
  return !TERMINAL_STATES.has(job.state);
}

/** Whether `job` belongs to the workspace `poolId`.
 *
 * A row with no `pool_id` — a pre-pools orphan, or a response from an API
 * deployed before the field existed — is NEVER "in this workspace".
 * Defaulting the other way is the dangerous direction: it would render one
 * member's private pre-pools jobs to their entire team. */
export function isInWorkspace(job: JobRecord, poolId: string): boolean {
  return job.pool_id === poolId;
}

/** Jobs with no workspace at all: the pre-pools rows that surface read-only
 * under My account. `null` and `undefined` both count — `null` is an API
 * that has the field and a job with no pool, `undefined` is an API that
 * predates the field — and neither is a workspace job. */
export function isEarlierJob(job: JobRecord): boolean {
  return job.pool_id === null || job.pool_id === undefined;
}

export function jobsInWorkspace(
  jobs: JobRecord[],
  poolId: string
): JobRecord[] {
  return jobs.filter((j) => isInWorkspace(j, poolId));
}

export function earlierJobs(jobs: JobRecord[]): JobRecord[] {
  return jobs.filter(isEarlierJob);
}
