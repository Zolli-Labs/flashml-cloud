/** How much of the Placement view's prose and arithmetic a reader has to be
 * shown before they ask for more.
 *
 * WHY THIS MODULE EXISTS. The Placement view answered every question at full
 * length at once: five venues, each with its verdict definition, the router's
 * own reason, a currency line and sometimes a second refusal — roughly a
 * screen of prose before the first table. Under it sat two tables whose every
 * data cell read `not observed`, because a job that has never run has no
 * per-task duration to price or time anything with. Nothing there was wrong.
 * All of it was there before the reader had decided which venue they cared
 * about.
 *
 * So the decisions about what may stand in for what live here, where a test
 * can reach them — the same split `lib/job-routing.ts` and
 * `lib/job-tradeoff.ts` document, and for the same reason: `vitest.config.ts`
 * collects `**\/*.test.{ts,tsx}` but a `.tsx` component has no test of its own
 * in this repo, so any judgement that could be wrong belongs in a `.ts`.
 *
 * NOTHING HERE DELETES ANYTHING. `firstSentence` picks what to show FIRST;
 * the card renders the full text underneath in a disclosure, verbatim. The
 * two table predicates decide whether a table leads or sits behind a caret —
 * never whether it is rendered at all. A collapsed table is one click away
 * and identical when it opens.
 */

import type { PlanRow } from "@/lib/job-routing";
import type { TradeoffRow } from "@/lib/job-tradeoff";

/** The first sentence of a passage, for the line that stands in for it.
 *
 * A sentence ends at `.`, `?` or `!` FOLLOWED BY WHITESPACE OR THE END of the
 * text. The trailing test is what keeps "2 vCPU, 2 GB, no GPU" and a figure
 * like "0.38x of the pooled rate" intact: the period inside a decimal is
 * followed by a digit, so it ends nothing. A passage with no terminator at
 * all is returned whole — a router reason is the API's own words, and
 * truncating one at a guessed boundary would put a sentence in the product's
 * mouth that nobody wrote.
 *
 * Never appends an ellipsis: the disclosure beside it is what says there is
 * more, and a "…" on a passage that turned out to be one sentence long
 * promises text that does not exist. */
export function firstSentence(text: string): string {
  const trimmed = text.trim();
  const match = /^[\s\S]*?[.?!](?=\s|$)/.exec(trimmed);
  return match ? match[0] : trimmed;
}

/** True when a passage carries more than the line standing in for it — the
 * only thing a "why" disclosure has to add. */
export function hasMoreThanFirstSentence(text: string): boolean {
  return firstSentence(text).length < text.trim().length;
}

/** The sentence that replaces a table whose every data cell would read
 * `not observed`. One sentence, and it says both what is missing and what
 * would supply it — an empty table says only the first, and says it seven
 * times. */
export const NO_DURATION_YET =
  "No per-task duration observed yet — the first run measures it; fleet advice appears after that.";

/** Whether the "ways to run it" table has any figure in it.
 *
 * Every plan quoting 0.00 in both currencies with no finish time is what a
 * job with no measured duration looks like: the planner placed the tasks —
 * which is real, and is why the table stays reachable — but had nothing to
 * price the wall-clock with. A zero cost on its OWN is a measurement and not
 * a gap (workspace machines are free to their own members; see `costsOf` in
 * `lib/job-routing.ts`), so cost alone can never trigger this. It takes the
 * missing makespan as well.
 *
 * False for an empty plan list: there is no table to collapse, and the card
 * already has its own sentence for a job nothing was quoted for. */
export function planTableIsUnpriced(plans: PlanRow[]): boolean {
  if (plans.length === 0) return false;
  return plans.every(
    (plan) =>
      plan.makespanSeconds == null &&
      plan.costs.every((cost) => cost.amount === 0)
  );
}

/** Whether the "what each machine buys" table has any figure in it.
 *
 * The fleet sizes and the verdicts are real either way — `ceil(tasks/slots)`
 * needs no duration to say that the eleventh machine is past the task count —
 * which is exactly why the table is kept behind the disclosure rather than
 * dropped. What is missing is every number a buyer would decide on: finish
 * time, time saved, and both currencies. */
export function tradeoffTableIsUnobserved(rows: TradeoffRow[]): boolean {
  if (rows.length === 0) return false;
  return rows.every(
    (row) =>
      row.finishSeconds == null &&
      row.savedSeconds == null &&
      row.usdCost == null &&
      row.totalUsdValue == null &&
      row.zcCost === 0
  );
}
