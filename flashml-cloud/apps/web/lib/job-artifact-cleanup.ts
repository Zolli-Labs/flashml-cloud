/** Picking which of an account's jobs are safe to offer a "clear artifacts"
 * action for, and describing what a clear-artifacts call actually did.
 *
 * Lives here rather than in account/page.tsx (where the account-wide list
 * is offered) or jobs/[jobId]/page.tsx (where a single job's own artifacts
 * are cleared) for the same reason every other `lib/` module in this app
 * does: it is the part worth testing, and `vitest.config.ts` only collects
 * `**\/*.test.ts` — a `.tsx` component gets no coverage at all, so any
 * decision that could be wrong belongs in a module a test can reach.
 */

import type { DeleteJobArtifactsResult, JobRecord } from "./cloud-api";
import { countOf } from "./plural";
import { formatBytes } from "./utils";

/** States `DELETE /v1alpha1/jobs/{id}/artifacts` accepts without a 409.
 *
 * Deliberately wider than `lib/job-scope.ts`'s `TERMINAL_STATES`, and not
 * imported from there: that set answers "should the workspace provider keep
 * polling this job" and leaves PARTIAL out on purpose — a partial job can
 * still be waiting on a straggling round. This set answers a different
 * question, the one the API's own 409 is actually asking: "has every task
 * that could still write into this job's output directory stopped
 * running". PARTIAL means yes just as much as SUCCEEDED does — the tasks
 * that were going to finish already have. A job in any other state might
 * still write into its own output directory, so offering the button for it
 * would only relay the API's 409 back at the user with extra steps. */
const CLEARABLE_STATES = new Set(["SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"]);

export interface ClearableJob {
  jobId: string;
  /** The job's display name when it has one, else its id — the same
   * fallback chain `jobs/[jobId]/page.tsx` and the workspace jobs list both
   * use for a heading, repeated here so this list reads as the same job. */
  label: string;
}

/** Jobs worth offering a "clear artifacts" action for, from the caller's
 * own job list. `listJobs()` already scopes to the caller, so nothing here
 * re-checks ownership; this only narrows by state. Order is preserved from
 * the input — the API's own order, not re-sorted here — so a caller that
 * lists newest-first stays newest-first. */
export function clearableJobs(jobs: JobRecord[]): ClearableJob[] {
  return jobs
    .filter((j) => CLEARABLE_STATES.has(j.state))
    .map((j) => ({
      jobId: j.job_id,
      label: j.spec?.metadata?.name ?? j.name ?? j.job_id,
    }));
}

/** Turns a successful delete's counts into the sentence a toast or inline
 * confirmation shows. Both counts are the API's own — never re-derived or
 * estimated here — because a client-side guess at "how much did that just
 * free" is exactly the kind of fabricated number this product's other
 * pages (see `lib/platform-metrics.ts`) refuse to show. */
export function describeClearedArtifacts(
  result: DeleteJobArtifactsResult
): string {
  return `Freed ${formatBytes(result.freed_bytes)} across ${countOf(result.deleted_files, "file")}.`;
}
