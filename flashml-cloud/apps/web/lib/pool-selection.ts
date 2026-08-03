import type { PoolSummary } from "./cloud-api";

/** The sentinel `app/(console)/submit/page.tsx`'s `Select` uses for "no
 * pool selected — public queue", the form's default and the value its
 * first `SelectItem` carries. Exported so the page and the predicates
 * below agree on exactly one meaning for "no pool" rather than each
 * hardcoding `""` and hoping the other stays in sync. */
export const NO_POOL = "";

/** Whether the "pool jobs run unsandboxed" notice should show under the
 * selector: true exactly when the form has an actual pool chosen, never
 * for the public-queue default. */
export function isPoolSelected(poolId: string): boolean {
  return poolId !== NO_POOL;
}

/** Whether the currently selected pool has nobody online to run this job
 * right now — the condition that gates the amber "0 workers online"
 * banner. `null` (nothing selected, or a stale id that no longer matches
 * any pool in the fetched list) is never "zero workers": there is no pool
 * to warn about, so the banner stays hidden rather than firing on absence
 * of a selection. */
export function hasNoWorkersOnline(pool: PoolSummary | null): boolean {
  return pool !== null && pool.machines_online === 0;
}
