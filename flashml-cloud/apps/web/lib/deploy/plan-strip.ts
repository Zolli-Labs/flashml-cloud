/** What the "Ways to run it" strip is allowed to show, and when it collapses
 * to one sentence instead.
 *
 * WHY THIS IS A `.ts` AND NOT PART OF THE COMPONENT. `vitest.config.ts`
 * collects `**\/*.test.{ts,tsx}` and this repo has no DOM environment
 * configured, so a `.tsx` component gets no coverage. Every judgement that
 * could be wrong — which of the four things to render, whether a figure was
 * observed — lives here where a test can reach it; the component beside it
 * is markup. The same split `lib/job-routing.ts` and
 * `components/jobs/placement-summary.ts` already document.
 *
 * THE RULE THIS FILE EXISTS TO ENFORCE. A strip whose every cell reads
 * `0.00 · 0.00 · not observed` has told the reader one thing — nothing has
 * been measured — six cells at a time. It says it once instead, in the
 * route's own terms. This is the same rule the job page's Routing card
 * applies through `planTableIsUnpriced`, and it calls that same predicate
 * rather than re-deciding it: the two surfaces must not disagree about
 * whether a plan set is priced.
 */

import {
  NO_DURATION_YET,
  planTableIsUnpriced,
} from "@/components/jobs/placement-summary";
import { NOT_OBSERVED, type PlanRow, type RoutingPanel } from "@/lib/job-routing";

// Re-exported so the strip's component imports one module rather than three,
// and so the sentence shown for an unpriced plan set is provably the same
// string the job page shows for it rather than a second copy of the words.
export { NO_DURATION_YET };

/** Said when the router ran and quoted no plan at all. The panel's notes
 * carry the reason and are rendered under it; this is the headline, not a
 * replacement for them. */
export const NO_PLAN_QUOTED =
  "No way to run this was quoted for this job.";

/** Said when the read itself failed. Deliberately not split by status code —
 * a 409, a 502 and a dead network all leave the console equally unable to
 * price this job, and pretending to tell them apart would mean guessing. */
export const PLANS_UNREADABLE = "Couldn't read what this job will cost.";

/** The four things the strip can be. */
export type PlanStripView =
  | { kind: "loading" }
  | { kind: "unavailable"; detail: string | null }
  /** One sentence in place of a table. `notes` still render under it —
   * collapsing the figures never drops the route's own words. */
  | { kind: "sentence"; text: string; notes: string[] }
  | { kind: "plans"; plans: PlanRow[]; notes: string[] };

/** One routing panel, reduced to what the strip may draw.
 *
 * Pure: no clock, no network. The same panel always produces the same view.
 */
export function planStripView(panel: RoutingPanel): PlanStripView {
  if (panel.state === "loading") return { kind: "loading" };
  if (panel.state === "unavailable") {
    return { kind: "unavailable", detail: panel.detail };
  }
  if (panel.state === "unrouted") {
    // The router did not run. Its notes say why (routing not configured on
    // this deployment, a spec that expands to nothing, an account with no
    // capacity at all) and they are the answer — there is nothing this
    // module could add that would not be a guess about which case it is.
    return { kind: "sentence", text: NO_PLAN_QUOTED, notes: panel.notes };
  }
  if (panel.plans.length === 0) {
    return { kind: "sentence", text: NO_PLAN_QUOTED, notes: panel.notes };
  }
  if (planTableIsUnpriced(panel.plans)) {
    return { kind: "sentence", text: NO_DURATION_YET, notes: panel.notes };
  }
  return { kind: "plans", plans: panel.plans, notes: panel.notes };
}

/** A finish time, or the fact that there is not one.
 *
 * `null` is NOT OBSERVED and never "0s": a plan nobody could time and a plan
 * that finishes instantly are different claims, and only one of them is ever
 * true here. The buckets match the job page's own formatter so the same
 * makespan never reads as `90s` on one screen and `2m` on the next. */
export function formatFinish(seconds: number | null): string {
  if (seconds == null) return NOT_OBSERVED;
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

/** A settlement figure, to the cent, in its own currency.
 *
 * Two decimals ALWAYS, including on a whole number, so a column of these
 * aligns on the point under `tabular-nums`. A zero is a real measurement
 * here — workspace machines are free to their own members — and is rendered
 * as `0.00`, never as a dash. */
export function formatAmount(amount: number): string {
  return amount.toFixed(2);
}
