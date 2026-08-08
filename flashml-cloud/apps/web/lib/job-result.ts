/** Turning a coordinator reduction into something a card can render.
 *
 * The logic lives here rather than in the component for the same reason
 * every other `lib/` module in this app does: it is the part worth testing,
 * and the component around it is markup.
 *
 * One rule shapes all of it — say what the reduction actually found, and
 * say when it found nothing. A blank where a task id belongs, or a mean
 * presented without the coverage it was computed from, reads as an answer
 * and is not one.
 */

import type { JobResult } from "./cloud-api";

export interface JobResultRow {
  label: string;
  value: string;
}

export interface JobResultSummary {
  /** The one thing worth reading first: the winning task, the mean, the
   *  merged file. */
  headline: string;
  /** Supporting context for the headline, or null when it stands alone. */
  detail: string | null;
  rows: JobResultRow[];
  /** "2 of 3 tasks accepted so far", or just "3 tasks" once complete. */
  coverage: string;
  partial: boolean;
}

function num(value: unknown): string {
  return typeof value === "number" ? String(value) : "—";
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function summariseJobResult(
  payload: JobResult | null
): JobResultSummary | null {
  // Two different causes — a federated job (409 → null) and a job that
  // declared no reducer — with the same correct outcome. An empty card
  // would imply the answer was computed and came back blank.
  if (!payload || payload.reducer === "none" || payload.result === null) {
    return null;
  }

  const data = payload.result;
  const partial = !payload.complete;
  const coverage = partial
    ? `${payload.accepted} of ${payload.total} tasks accepted so far`
    : `${payload.total} tasks`;

  let headline = payload.reducer;
  let detail: string | null = null;
  const rows: JobResultRow[] = [];

  if (payload.reducer === "rank") {
    const metric = str(data.metric) ?? "metric";
    const best = record(data.best);
    if (best) {
      headline = str(best.task_id) ?? "—";
      detail = `${metric} ${num(best.value)}`;
    } else {
      // Every task committed and none carried the metric. Naming the
      // metric is the whole value of this message: it is almost always a
      // typo in the job's own `reduce.metric`.
      headline = "No ranked result";
      detail = `no task reported ${metric}`;
    }
  } else if (payload.reducer === "aggregate") {
    headline = num(data.mean);
    detail = "mean";
    rows.push(
      { label: "metric", value: str(data.metric) ?? "—" },
      { label: "tasks", value: num(data.count) },
      { label: "min", value: num(data.min) },
      { label: "max", value: num(data.max) }
    );
  } else if (payload.reducer === "concat") {
    const destination = str(data.destination) ?? "";
    headline = destination.split("/").pop() || destination || "merged";
    const parts = Array.isArray(data.parts) ? data.parts.length : 0;
    detail = destination || null;
    rows.push(
      { label: "merged from", value: `${parts} tasks` },
      { label: "size", value: `${num(data.bytes)} bytes` }
    );
  } else if (payload.reducer === "collect") {
    headline = `${num(data.count)} artifacts`;
  }
  // Any other reducer keeps `headline = payload.reducer`: the runtime may
  // ship one before this console learns to render it, and naming it beats
  // taking the job page down.

  return { headline, detail, rows, coverage, partial };
}
