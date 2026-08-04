"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { Plus } from "@phosphor-icons/react";
import { StateBadge } from "@/components/jobs/StateBadge";
import { useWorkspace } from "@/components/workspace/WorkspaceProvider";
import { isActiveJob } from "@/lib/job-scope";
import { workspacePath } from "@/lib/workspace-scope";
import type { JobRecord } from "@/lib/cloud-api";

// A list of jobs is a table, not a stack of cards. Cards were costing a
// third of the vertical space per row and giving every job the same visual
// weight as every other, which is the opposite of what a list is for.

const FILTERS = [
  { id: "all", label: "All" },
  { id: "active", label: "Running" },
  { id: "failed", label: "Failed" },
] as const;
type Filter = (typeof FILTERS)[number]["id"];

function started(job: JobRecord): string {
  if (!job.created_at) return "—";
  const d = new Date(job.created_at);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

export default function WorkspaceJobsPage() {
  const { pool, jobs } = useWorkspace();
  const [filter, setFilter] = useState<Filter>("all");

  // Hooks stay above the `pool` guard below: it never actually fires at
  // runtime (WorkspaceGate only mounts this tab once the workspace is
  // "ready"), but a conditional return above a hook call would still be a
  // Rules-of-Hooks violation regardless of whether that branch is ever
  // reached.
  const shown = useMemo(() => {
    if (filter === "active") return jobs.filter(isActiveJob);
    if (filter === "failed") return jobs.filter((j) => j.state === "FAILED");
    return jobs;
  }, [jobs, filter]);

  const counts = useMemo(
    () => ({
      all: jobs.length,
      active: jobs.filter(isActiveJob).length,
      failed: jobs.filter((j) => j.state === "FAILED").length,
    }),
    [jobs]
  );

  // The WorkspaceGate in the layout guarantees this never fires at
  // runtime — it exists to satisfy `Pool | null` at the type level.
  if (!pool) return null;

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="title">Jobs</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Everything submitted in this workspace.
          </p>
        </div>
        <Link
          href={workspacePath(pool.id, "submit")}
          className="interactive inline-flex items-center gap-2 rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110"
        >
          <Plus size={15} weight="bold" />
          New job
        </Link>
      </div>

      {jobs.length > 0 && (
        <div className="mt-6 flex gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => setFilter(f.id)}
              className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
                filter === f.id
                  ? "bg-white/[0.09] text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {f.label}
              <span className="ml-1.5 font-mono tabular-nums opacity-60">
                {counts[f.id]}
              </span>
            </button>
          ))}
        </div>
      )}

      <div className="mt-4">
        {shown.length === 0 ? (
          <Empty hasAny={jobs.length > 0} filter={filter} poolId={pool.id} />
        ) : (
          // Hairline rows on the page, not cards. A real <thead> so the
          // columns are announced rather than being visual-only labels.
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left">
              <thead>
                <tr className="border-b border-border">
                  {["Job", "Submitted by", "Mode", "Started", "State"].map(
                    (h, i) => (
                      <th
                        key={h}
                        className={`label-caps px-3 py-2 font-medium ${i === 4 ? "text-right" : ""}`}
                      >
                        {h}
                      </th>
                    )
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {shown.map((j) => (
                  <tr
                    key={j.job_id}
                    className="group transition-colors hover:bg-white/[0.03]"
                  >
                    <td className="px-3 py-3">
                      <Link href={`/jobs/${j.job_id}`} className="block min-w-0">
                        <span className="block truncate font-mono text-sm group-hover:text-primary">
                          {j.spec?.metadata?.name ?? j.name ?? j.job_id}
                        </span>
                        <span className="meta block truncate">{j.job_id}</span>
                      </Link>
                    </td>
                    <td className="meta px-3 py-3">
                      {j.submitted_by ?? "—"}
                    </td>
                    <td className="meta px-3 py-3">
                      {j.mode === "federated" ? "federated" : "independent"}
                    </td>
                    <td className="meta px-3 py-3 whitespace-nowrap">
                      {started(j)}
                    </td>
                    <td className="px-3 py-3 text-right">
                      <StateBadge state={j.state} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function Empty({
  hasAny,
  filter,
  poolId,
}: {
  hasAny: boolean;
  filter: Filter;
  poolId: string;
}) {
  // A filter matching nothing is not the same as having no jobs, and telling
  // a workspace with 40 finished jobs that it has none would be a lie.
  if (hasAny) {
    return (
      <p className="py-12 text-center text-sm text-muted-foreground">
        No jobs match the {filter === "active" ? "running" : filter} filter.
      </p>
    );
  }
  return (
    <div className="flex flex-col items-center gap-3 py-16 text-center">
      <p className="max-w-sm text-sm text-muted-foreground">
        No jobs in this workspace yet.
      </p>
      <Link
        href={workspacePath(poolId, "submit")}
        className="interactive inline-flex items-center gap-2 rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110"
      >
        <Plus size={15} weight="bold" />
        Submit a job
      </Link>
    </div>
  );
}
