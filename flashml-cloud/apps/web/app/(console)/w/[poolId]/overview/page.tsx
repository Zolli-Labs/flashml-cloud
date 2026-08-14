"use client";

import Link from "next/link";
import { ArrowRight, Lightning } from "@phosphor-icons/react";
import { StateBadge } from "@/components/jobs/StateBadge";
import { PageShell } from "@/components/shell/PageShell";
import { StatePanel } from "@/components/shell/StatePanel";
import { StatStrip } from "@/components/shared/StatStrip";
import { WorkspaceHeader } from "@/components/workspace/WorkspaceHeader";
import { useWorkspace } from "@/components/workspace/WorkspaceProvider";
import {
  isEmptyList,
  resolvePanelState,
  type PanelRead,
} from "@/lib/console/panel-state";
import { isActiveJob } from "@/lib/job-scope";
import { isMachineOnline } from "@/lib/machine-scope";
import { workspacePath } from "@/lib/workspace-scope";
import type { JobRecord } from "@/lib/cloud-api";

// The console had no home: signing in dropped you on the marketing page.
// This is built entirely from data the layout's WorkspaceProvider already
// fetched, so it ships without waiting on P2. The pieces it CANNOT honestly
// show yet (recent activity from the event ledger, reliability metrics) are
// absent rather than stubbed, because a chart with no data behind it on a
// page about measurement is worse than no chart.

export default function WorkspaceOverviewPage() {
  const { pool, jobs, machines, reload } = useWorkspace();
  // The WorkspaceGate in the layout guarantees this never fires at
  // runtime — it exists to satisfy `Pool | null` at the type level.
  if (!pool) return null;

  const active = jobs.filter(isActiveJob);
  // `isMachineOnline`: heartbeat recency, not enrolment state — see
  // lib/machine-scope.ts. Must agree with WorkspaceHeader's own count.
  const online = machines.filter(isMachineOnline);

  // `"read"` because the provider's `Promise.all` already succeeded before
  // this page could mount — see `people/page.tsx` for the full note.
  const read: PanelRead<JobRecord[]> = { status: "read", data: active };
  const panel = resolvePanelState(read, isEmptyList);

  return (
    <PageShell width="wide">
      <WorkspaceHeader />

      {/* Every figure below is the length of an array the API returned, and
          "Jobs finished" is `jobs.length - active.length` where `active` is
          the complement of `TERMINAL_STATES` — a partition of the same
          array, so the subtraction cannot invent a job. No count-up
          animation on any of them: §1.1 and the motion spec both say a
          number is animated only when it was measured, and these are
          already exact. */}
      <StatStrip
        className="mt-6"
        items={[
          {
            label: "Machines online",
            value: <Ratio value={online.length} total={machines.length} />,
          },
          {
            label: "Jobs running",
            value: <Ratio value={active.length} total={jobs.length} />,
          },
          { label: "Jobs finished", value: jobs.length - active.length },
        ]}
      />

      <section className="mt-8">
        <div className="flex items-end justify-between gap-3">
          <h2 className="text-sm font-semibold">Active jobs</h2>
          <Link
            href={workspacePath(pool.id, "jobs")}
            className="text-xs text-brand-foreground hover:underline"
          >
            View all
          </Link>
        </div>

        {/* `.panel` rather than the hand-written `rounded-lg border
            border-border bg-surface`: identical three declarations, one
            name. This is the page's one level of boxing, which is why the
            empty state inside it no longer draws a bordered icon tile —
            `globals.css` is explicit that a panel never nests in a panel. */}
        <div className="panel mt-3 overflow-hidden">
          <StatePanel
            state={panel}
            label="this Workspace's active jobs"
            empty={
              jobs.length > 0
                ? {
                    // Same two sentences as before, split at the full stop
                    // they already had: the first is the state, the second
                    // is why. No new copy.
                    title: "Nothing running right now.",
                    description:
                      "Everything you have submitted has finished.",
                  }
                : {
                    title: "No jobs in this Workspace yet.",
                    action: (
                      <Link
                        href={workspacePath(pool.id, "submit")}
                        className="inline-flex items-center gap-1.5 text-sm text-brand-foreground hover:underline"
                      >
                        Submit a job
                        <ArrowRight size={13} weight="bold" />
                      </Link>
                    ),
                  }
            }
            unreadable={{ retry: reload }}
          >
            {(rows) => (
              <ul className="divide-y divide-border">
                {rows.map((j) => (
                  <li key={j.job_id}>
                    <Link
                      href={`/jobs/${j.job_id}`}
                      className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-surface-2/70"
                    >
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] bg-primary/15 text-brand-foreground">
                        <Lightning size={15} weight="fill" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-mono text-sm">
                          {j.spec?.metadata?.name ?? j.name ?? j.job_id}
                        </span>
                        <span className="block truncate font-mono text-xs text-muted-foreground">
                          {j.submitted_by ? `by ${j.submitted_by} · ` : ""}
                          {j.mode === "federated" ? "federated" : "independent"}
                          {j.created_at
                            ? ` · started ${new Date(j.created_at).toLocaleTimeString()}`
                            : ""}
                        </span>
                      </span>
                      <StateBadge state={j.state} />
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </StatePanel>
        </div>
      </section>
    </PageShell>
  );
}

/** A count, with its denominator shown only when it differs — "5" alone
 * when everything is online, "5/9" when it is not. The `/total` half reads
 * `text-muted-foreground`; the family (mono, tabular, weight) comes from
 * `StatStrip`'s own value styling, since this renders directly inside it. */
function Ratio({ value, total }: { value: number; total?: number }) {
  return (
    <>
      {value}
      {total !== undefined && total !== value && (
        <span className="text-muted-foreground">/{total}</span>
      )}
    </>
  );
}
