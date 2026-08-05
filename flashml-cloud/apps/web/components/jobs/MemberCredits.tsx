"use client";

import type { JobContribution } from "@/lib/cloud-api";
import { fmtDuration } from "./RoundProgress";

/**
 * The per-machine credit view for a pool job: who ran it, on whose
 * machine, and how much of it they did.
 *
 * Lives here, not inlined in `app/(console)/jobs/[jobId]/page.tsx`, for the
 * same reason `StateBadge` does — see that file's header. A `page.tsx` may
 * export only a default component plus route config; a stray named export
 * makes the route module invalid at runtime with no build-time error.
 *
 * `total_duration_s` comes from the API in seconds; `fmtDuration` (also
 * used by `RoundProgress`, imported rather than reimplemented) takes
 * milliseconds, hence the `* 1000` below — this is the same formatter a
 * round's "Took" column uses, so a duration reads the same wherever it
 * appears on this page.
 *
 * Renders nothing for a job with no recorded contributions — every
 * independent (non-pool) job, which is most jobs today. The empty case is
 * not an error; there is simply nobody to credit.
 */
export function MemberCredits({
  contributions,
}: {
  contributions: JobContribution[];
}) {
  if (contributions.length === 0) return null;

  return (
    <section className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="border-b border-border px-4 py-2.5">
        <h2 className="text-sm font-semibold">Member credits</h2>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] text-left">
          <thead>
            <tr className="border-b border-border">
              {["Member", "Zolli", "Tasks credited", "Time"].map((h) => (
                <th key={h} className="label-caps px-4 py-2 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {contributions.map((c) => (
              <tr key={c.node_id}>
                <td className="px-4 py-2.5 font-mono text-xs">
                  {c.member_display_name ?? "—"}
                </td>
                <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                  {c.machine_name ?? c.node_id}
                </td>
                <td className="px-4 py-2.5 font-mono text-xs tabular-nums">
                  {c.tasks_credited}
                </td>
                <td className="px-4 py-2.5 font-mono text-xs tabular-nums text-muted-foreground">
                  {fmtDuration(c.total_duration_s * 1000)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
