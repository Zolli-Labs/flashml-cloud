"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ArrowClockwise, Plus } from "@phosphor-icons/react";
import { StateBadge } from "@/components/jobs/StateBadge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PageShell } from "@/components/shell/PageShell";
import { StatePanel } from "@/components/shell/StatePanel";
import { WorkspaceHeader } from "@/components/workspace/WorkspaceHeader";
import { useWorkspace } from "@/components/workspace/WorkspaceProvider";
import {
  isEmptyList,
  resolvePanelState,
  type PanelRead,
} from "@/lib/console/panel-state";
import { isActiveJob } from "@/lib/job-scope";
import { cn } from "@/lib/utils";
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

const COLUMNS = ["Job", "Submitted by", "Mode", "Started", "State"];

function started(job: JobRecord): string {
  if (!job.created_at) return "—";
  const d = new Date(job.created_at);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
}

export default function WorkspaceJobsPage() {
  const { pool, jobs, reload, reloading } = useWorkspace();
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

  // `"read"` because the provider's `Promise.all` already succeeded before
  // this tab could mount — see `people/page.tsx` for the full note. What
  // this panel classifies is the FILTERED list, so `empty` here means "the
  // filter matched nothing", which is a different sentence from "this
  // workspace has no jobs" and is why the empty copy below branches.
  const read: PanelRead<JobRecord[]> = { status: "read", data: shown };
  const panel = resolvePanelState(read, isEmptyList);

  // A filter matching nothing is not the same as having no jobs, and telling
  // a workspace with 40 finished jobs that it has none would be a lie.
  const filtered = jobs.length > 0;

  return (
    <PageShell width="wide">
      {/* Same header as the other four tabs. This one used to hand-build its
          own `<h1>Jobs</h1>` plus a second "New job" button — a duplicate of
          the one `WorkspaceHeader` already renders, and the only tab of the
          five whose heading did not say which workspace you were in. */}
      <WorkspaceHeader />

      <div className="mt-8 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Jobs</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Everything submitted in this Workspace.
          </p>
        </div>
        {/* This used to carry a comment explaining that it deliberately does
            NOT spin, because the only flag available was `state === "loading"`
            — which WorkspaceGate makes unreachable here, so wiring the spin
            to it would have been a condition that could never fire. The
            provider now carries `reloading`, a flag for a reload somebody
            ASKED for, which is a condition that can fire and is exactly what
            this control means. The reason for not spinning is gone, so the
            spin is here. */}
        <Button
          variant="ghost"
          size="icon"
          onClick={reload}
          disabled={reloading}
          aria-label="Refresh"
          className="text-muted-foreground hover:text-foreground"
        >
          <ArrowClockwise
            size={15}
            className={cn(reloading && "animate-spin")}
          />
        </Button>
      </div>

      {jobs.length > 0 && (
        <div className="mt-4 flex gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => setFilter(f.id)}
              className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
                filter === f.id
                  ? "bg-surface-2 text-foreground"
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
        <StatePanel
          state={panel}
          label="this Workspace's jobs"
          empty={
            filtered
              ? {
                  // DEFECT FIX. This state was a bare `<p className="py-12
                  // text-center text-sm text-muted-foreground">` — one muted
                  // sentence, no heading, and no way out of a filter that
                  // matches nothing. The sentence itself is unchanged and is
                  // now the heading; the action is new, and it is new
                  // because the state was a dead end: the only escape from
                  // "Failed (0)" was to notice the filter chips above and
                  // work out that one of them was still selected.
                  title: `No jobs match the ${filter === "active" ? "running" : filter} filter.`,
                  action: (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setFilter("all")}
                    >
                      Show all jobs
                    </Button>
                  ),
                }
              : {
                  title: "No jobs in this workspace yet.",
                  action: (
                    <Link
                      href={workspacePath(pool.id, "submit")}
                      className={buttonVariants()}
                    >
                      <Plus size={15} weight="bold" />
                      Submit a job
                    </Link>
                  ),
                }
          }
          unreadable={{ retry: reload }}
        >
          {(rows) => (
            // Hairline rows on the page, not cards. A real <thead> so the
            // columns are announced rather than being visual-only labels.
            // `min-w-[640px]` is the floor that keeps five columns from
            // collapsing; the primitive supplies the `overflow-x-auto`
            // container this used to hand-write.
            <Table className="min-w-[640px]">
              <TableHeader>
                <TableRow>
                  {/* No `label-caps` here — `TableHead`'s own default now
                      supplies the identical machine-register treatment
                      (plus mono, which `.label-caps` alone did not); only
                      the per-column `text-right` alignment is still this
                      page's own decision. */}
                  {COLUMNS.map((h, i) => (
                    <TableHead
                      key={h}
                      className={cn(i === 4 && "text-right")}
                    >
                      {h}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((j) => (
                  // The one table in the console whose rows navigate, so the
                  // one that opts into a hover fill — see the note on
                  // `TableRow` in `components/ui/table.tsx`.
                  <TableRow key={j.job_id} className="group hover:bg-surface-2/70">
                    <TableCell>
                      <Link href={`/jobs/${j.job_id}`} className="block min-w-0">
                        <span className="block truncate font-mono text-sm group-hover:text-brand-foreground">
                          {j.spec?.metadata?.name ?? j.name ?? j.job_id}
                        </span>
                        <span className="meta block truncate">{j.job_id}</span>
                      </Link>
                    </TableCell>
                    {/* `?? "—"`: the API returning no submitter is a thing
                        we observed about an old row, not a gap in the read,
                        and a dash says that without inventing a name. */}
                    <TableCell className="meta">
                      {j.submitted_by ?? "—"}
                    </TableCell>
                    <TableCell className="meta">
                      {j.mode === "federated" ? "federated" : "independent"}
                    </TableCell>
                    <TableCell className="meta">{started(j)}</TableCell>
                    <TableCell className="text-right">
                      <StateBadge state={j.state} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </StatePanel>
      </div>
    </PageShell>
  );
}
