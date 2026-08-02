"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowClockwise, Plus, Warning } from "@phosphor-icons/react";
import { StateBadge } from "@/components/jobs/StateBadge";
import {
  NotAuthenticated,
  listJobs,
  type JobRecord,
  type JobState,
} from "@/lib/cloud-api";

// A list of jobs is a table, not a stack of cards. Cards were costing a
// third of the vertical space per row and giving every job the same visual
// weight as every other, which is the opposite of what a list is for.

const TERMINAL: ReadonlySet<JobState> = new Set([
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
]);
const POLL_MS = 3000;

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

export default function JobsPage() {
  const router = useRouter();
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");

  const load = useCallback(() => {
    listJobs()
      .then((r) => {
        setJobs(r);
        setState("ready");
        setError(null);
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push("/sign-in?next=/jobs");
          return;
        }
        setError(err instanceof Error ? err.message : "Couldn't load your jobs.");
        setState("error");
      });
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const allTerminal =
      jobs.length > 0 && jobs.every((j) => TERMINAL.has(j.state));
    if (allTerminal) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [jobs, load]);

  const shown = useMemo(() => {
    if (filter === "active") return jobs.filter((j) => !TERMINAL.has(j.state));
    if (filter === "failed") return jobs.filter((j) => j.state === "FAILED");
    return jobs;
  }, [jobs, filter]);

  const counts = useMemo(
    () => ({
      all: jobs.length,
      active: jobs.filter((j) => !TERMINAL.has(j.state)).length,
      failed: jobs.filter((j) => j.state === "FAILED").length,
    }),
    [jobs]
  );

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="title">Jobs</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Everything you have submitted.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={load}
            aria-label="Refresh"
            className="rounded-md p-2 text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
          >
            <ArrowClockwise
              size={15}
              className={state === "loading" ? "animate-spin" : ""}
            />
          </button>
          <Link
            href="/submit"
            className="interactive inline-flex items-center gap-2 rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110"
          >
            <Plus size={15} weight="bold" />
            New job
          </Link>
        </div>
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
        {state === "loading" && jobs.length === 0 ? (
          <div className="space-y-px">
            <div className="skeleton h-12" />
            <div className="skeleton h-12" />
            <div className="skeleton h-12" />
          </div>
        ) : state === "error" ? (
          <div className="flex flex-col items-center gap-3 py-12 text-center">
            <Warning className="h-5 w-5 text-destructive" weight="fill" />
            <p className="text-sm text-muted-foreground">{error}</p>
            <button
              type="button"
              onClick={load}
              className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-white/[0.06]"
            >
              Try again
            </button>
          </div>
        ) : shown.length === 0 ? (
          <Empty hasAny={jobs.length > 0} filter={filter} />
        ) : (
          // Hairline rows on the page, not cards. A real <thead> so the
          // columns are announced rather than being visual-only labels.
          <div className="overflow-x-auto">
            <table className="w-full min-w-[560px] text-left">
              <thead>
                <tr className="border-b border-border">
                  {["Job", "Mode", "Started", "State"].map((h, i) => (
                    <th
                      key={h}
                      className={`label-caps px-3 py-2 font-medium ${i === 3 ? "text-right" : ""}`}
                    >
                      {h}
                    </th>
                  ))}
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

function Empty({ hasAny, filter }: { hasAny: boolean; filter: Filter }) {
  // A filter matching nothing is not the same as having no jobs, and telling
  // a user with 40 finished jobs that they have none would be a lie.
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
        No jobs yet. Point FlashML at a GitHub repo and it will run across
        whatever machines you have attached.
      </p>
      <Link
        href="/submit"
        className="interactive inline-flex items-center gap-2 rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110"
      >
        <Plus size={15} weight="bold" />
        Submit a job
      </Link>
    </div>
  );
}
