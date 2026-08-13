/** Turning `GET /v1alpha1/jobs/{id}/verifications` into what the job page's
 * verification panel renders.
 *
 * THE RULE THIS MODULE EXISTS TO ENFORCE. `db.record_verification`'s own
 * docstring is explicit: these verdicts are OBSERVATIONS, never gates.
 * "Nothing in this system reads what this writes to refuse a lease, withhold
 * a credit, fail a commit or change placement… it is not a gate and must
 * never become one by accident." A `flag` withheld nothing and refused no
 * one. This module's job is to carry that property all the way to the
 * screen: nothing here computes an overall pass/fail for a task or a job,
 * and there is no field a caller could mistake for one.
 *
 * THE ONE MISTAKE THIS LAYER MUST NEVER MAKE. `unknown` means "could not
 * tell" and must never render as a pass, and never as a blank either — a
 * verdict the coordinator genuinely could not settle is a different, weaker
 * claim than one it checked and cleared, and collapsing the two is exactly
 * the "tolerant writer" failure the API-side docstring warns against. Every
 * row keeps its own verdict; nothing here rounds `unknown` up to `pass`, and
 * nothing here rounds it down to a fabricated `flag` either.
 *
 * EMPTY IS ITS OWN STATE, NOT A ROLLUP OF ZEROS DRESSED AS A PASS. A job with
 * no verification rows has not been cleared by anything — only the
 * settlement path that calls `verify.timing_verdict` writes a row today, so
 * most tasks on most jobs will carry none. `summariseVerifications([])`
 * returns `{ state: "empty", … }`, and there is no code path from an empty
 * array to a rollup that looks like a clean bill of health.
 *
 * Lives here rather than inlined in `components/jobs/VerificationCard.tsx`
 * for the reason every sibling module in this family gives:
 * `vitest.config.ts` collects only `**\/*.test.ts`, so a `.tsx` component
 * gets no coverage at all. Every decision — the label for a verdict, how
 * rows are grouped, what "empty" means — is taken here where a test can
 * reach it; the component is markup and formatting.
 */

import type { Verification } from "./cloud-api";

export type VerificationVerdict = Verification["verdict"];
export type VerificationSlice = Verification["slice"];

/** The one place a verdict is turned into a word. `unknown` reads as
 * "could not tell" — not "unknown", which reads as a data-quality complaint
 * about the panel rather than a statement about what was observed; not
 * "pass" or a blank, which is the whole failure this module exists to
 * prevent. */
export const VERDICT_LABEL: Record<VerificationVerdict, string> = {
  pass: "passed",
  flag: "flagged",
  unknown: "could not tell",
};

/** One row, formatted. Every field is a value the API returned; nothing here
 * is computed except `label`, which is `VERDICT_LABEL[verdict]` and nothing
 * else. */
export interface VerificationRow {
  id: string;
  slice: VerificationSlice;
  verdict: VerificationVerdict;
  /** `VERDICT_LABEL[verdict]`, carried here so the component keeps no
   * second copy of the mapping. */
  label: string;
  machineId: string | null;
  /** The slice's own evidence, verbatim. Never summarised or paraphrased —
   * a paraphrase would be more confident than the evidence behind it. */
  detail: Record<string, unknown> | null;
  createdAt: string;
}

/** Every verdict recorded for one task, in the order they were written. */
export interface VerificationTaskGroup {
  taskId: string;
  rows: VerificationRow[];
}

/** How many rows landed on each verdict, across the whole job. A COUNT, not
 * a verdict on the job — there is deliberately no combined field (a
 * percentage, an overall status) that a caller could read as a pass/fail
 * summary. Three independent counters is the shape with nowhere to put one. */
export interface VerificationRollup {
  pass: number;
  flag: number;
  unknown: number;
}

export type VerificationPanelState = "present" | "empty";

export interface VerificationPanel {
  /** `empty` — the read succeeded and returned no rows at all. Distinct
   * from a failed read, which this module never sees: that classification
   * happens one layer up, the same way `lib/contributions.ts` leaves
   * loading/unreadable to `lib/console/panel-state.ts`. */
  state: VerificationPanelState;
  tasks: VerificationTaskGroup[];
  rollup: VerificationRollup;
  /** `tasks.length` is how many DISTINCT tasks have at least one row;
   * `totalRows` is how many rows there are in total — a task with three
   * slices checked contributes 1 to the first count and 3 to the second.
   * Kept separate so a caller never has to infer one from the other. */
  totalRows: number;
}

const EMPTY_ROLLUP: VerificationRollup = { pass: 0, flag: 0, unknown: 0 };

const EMPTY_PANEL: VerificationPanel = {
  state: "empty",
  tasks: [],
  rollup: EMPTY_ROLLUP,
  totalRows: 0,
};

function verificationRow(v: Verification): VerificationRow {
  return {
    id: v.id,
    slice: v.slice,
    verdict: v.verdict,
    label: VERDICT_LABEL[v.verdict],
    machineId: v.machine_id,
    detail: v.detail,
    createdAt: v.created_at,
  };
}

/** Turn the raw verdict rows for one job into what the panel renders. Pure:
 * the same rows always produce the same panel, with no clock and no
 * guessing.
 *
 * Rows are sorted by task id, then by `created_at` within a task, so the
 * panel reads the same way twice regardless of the order the API happened
 * to return them in — the API's contract makes no promise about row order. */
export function summariseVerifications(
  rows: readonly Verification[]
): VerificationPanel {
  if (rows.length === 0) return EMPTY_PANEL;

  const sorted = [...rows].sort((a, b) => {
    if (a.task_id !== b.task_id) return a.task_id < b.task_id ? -1 : 1;
    if (a.created_at !== b.created_at) {
      return a.created_at < b.created_at ? -1 : 1;
    }
    return a.slice < b.slice ? -1 : a.slice > b.slice ? 1 : 0;
  });

  const rollup: VerificationRollup = { pass: 0, flag: 0, unknown: 0 };
  const byTask = new Map<string, VerificationRow[]>();

  for (const v of sorted) {
    rollup[v.verdict] += 1;
    const row = verificationRow(v);
    const existing = byTask.get(v.task_id);
    if (existing) {
      existing.push(row);
    } else {
      byTask.set(v.task_id, [row]);
    }
  }

  const tasks: VerificationTaskGroup[] = Array.from(
    byTask.entries(),
    ([taskId, taskRows]) => ({ taskId, rows: taskRows })
  );

  return {
    state: "present",
    tasks,
    rollup,
    totalRows: rows.length,
  };
}
