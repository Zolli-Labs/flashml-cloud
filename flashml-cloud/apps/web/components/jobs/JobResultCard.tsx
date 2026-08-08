"use client";

import type { JobResult } from "@/lib/cloud-api";
import { summariseJobResult } from "@/lib/job-result";

/** The answer the job exists to produce.
 *
 * Renders nothing when there is nothing to say — a federated job, or one
 * that declared no reducer. An empty card would imply the reduction ran
 * and came back blank.
 */
export function JobResultCard({ result }: { result: JobResult | null }) {
  const summary = summariseJobResult(result);
  if (!summary) return null;

  return (
    <section className="rounded-lg border border-border bg-surface p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold">Result</h2>
        <span
          className={`font-mono text-xs ${
            summary.partial ? "text-warning" : "text-muted-foreground"
          }`}
        >
          {summary.coverage}
        </span>
      </div>

      <p className="mt-3 font-mono text-2xl font-semibold break-all">
        {summary.headline}
      </p>
      {summary.detail && (
        <p className="mt-1 font-mono text-sm text-muted-foreground break-all">
          {summary.detail}
        </p>
      )}

      {summary.rows.length > 0 && (
        <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
          {summary.rows.map((row) => (
            <div key={row.label}>
              <dt className="text-xs text-muted-foreground">{row.label}</dt>
              <dd className="font-mono text-sm">{row.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {summary.partial && (
        <p className="mt-4 text-xs text-muted-foreground">
          Tasks are still running. This answer covers the work accepted so
          far and will change as more commits land.
        </p>
      )}
    </section>
  );
}
