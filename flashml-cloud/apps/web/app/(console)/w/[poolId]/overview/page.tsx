"use client";

import Link from "next/link";
import { ArrowRight, Lightning } from "@phosphor-icons/react";
import { StateBadge } from "@/components/jobs/StateBadge";
import { WorkspaceHeader } from "@/components/workspace/WorkspaceHeader";
import { useWorkspace } from "@/components/workspace/WorkspaceProvider";
import { isActiveJob } from "@/lib/job-scope";
import { isMachineOnline } from "@/lib/machine-scope";
import { workspacePath } from "@/lib/workspace-scope";

// The console had no home: signing in dropped you on the marketing page.
// This is built entirely from data the layout's WorkspaceProvider already
// fetched, so it ships without waiting on P2. The pieces it CANNOT honestly
// show yet (recent activity from the event ledger, reliability metrics) are
// absent rather than stubbed, because a chart with no data behind it on a
// page about measurement is worse than no chart.

export default function WorkspaceOverviewPage() {
  const { pool, jobs, machines } = useWorkspace();
  // The WorkspaceGate in the layout guarantees this never fires at
  // runtime — it exists to satisfy `Pool | null` at the type level.
  if (!pool) return null;

  const active = jobs.filter(isActiveJob);
  // `isMachineOnline`: heartbeat recency, not enrolment state — see
  // lib/machine-scope.ts. Must agree with WorkspaceHeader's own count.
  const online = machines.filter(isMachineOnline);

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <WorkspaceHeader />

      <div className="mt-6 grid gap-3 sm:grid-cols-3">
        <Stat
          label="Machines online"
          value={online.length}
          total={machines.length}
        />
        <Stat label="Jobs running" value={active.length} total={jobs.length} />
        <Stat label="Jobs finished" value={jobs.length - active.length} />
      </div>

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

        <div className="mt-3 overflow-hidden rounded-lg border border-border bg-surface">
          {active.length === 0 ? (
            <EmptyJobs hasAny={jobs.length > 0} poolId={pool.id} />
          ) : (
            <ul className="divide-y divide-border">
              {active.map((j) => (
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
        </div>
      </section>
    </div>
  );
}

function Stat({
  label,
  value,
  total,
}: {
  label: string;
  value: number;
  total?: number;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3.5">
      <div className="metric-value text-2xl">
        {value}
        {total !== undefined && total !== value && (
          <span className="text-base text-muted-foreground">/{total}</span>
        )}
      </div>
      <div className="label-caps mt-1">{label}</div>
    </div>
  );
}

function EmptyJobs({ hasAny, poolId }: { hasAny: boolean; poolId: string }) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-10 text-center">
      <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-border">
        <Lightning size={17} className="text-muted-foreground" />
      </div>
      <p className="max-w-sm text-sm text-muted-foreground">
        {hasAny
          ? "Nothing running right now. Everything you have submitted has finished."
          : "No jobs in this Workspace yet."}
      </p>
      <Link
        href={workspacePath(poolId, "submit")}
        className="inline-flex items-center gap-1.5 text-sm text-brand-foreground hover:underline"
      >
        Submit a job
        <ArrowRight size={13} weight="bold" />
      </Link>
    </div>
  );
}
