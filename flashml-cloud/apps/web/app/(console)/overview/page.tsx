"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, Lightning, Plus, Warning } from "@phosphor-icons/react";
import { StateBadge } from "@/components/jobs/StateBadge";
import {
  NotAuthenticated,
  listJobs,
  listMachines,
  type JobRecord,
  type Machine,
} from "@/lib/cloud-api";

// The console had no home: signing in dropped you on the marketing page.
// This is built entirely from the two endpoints that already exist, so it
// ships without waiting on P2. The pieces it CANNOT honestly show yet
// (recent activity from the event ledger, reliability metrics) are absent
// rather than stubbed, because a chart with no data behind it on a page
// about measurement is worse than no chart.

const TERMINAL = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);
const POLL_MS = 5000;

type LoadState = "loading" | "ready" | "error";

export default function OverviewPage() {
  const router = useRouter();
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [machines, setMachines] = useState<Machine[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([listJobs(), listMachines()])
      .then(([j, m]) => {
        setJobs(j);
        setMachines(m);
        setState("ready");
        setError(null);
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push("/sign-in?next=/overview");
          return;
        }
        setError(err instanceof Error ? err.message : "Couldn't load your account.");
        setState("error");
      });
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  // Stop polling once nothing is in flight. A settled account changes only
  // when the user does something, and this page is the one most likely to
  // be left open in a background tab.
  useEffect(() => {
    const active = jobs.some((j) => !TERMINAL.has(j.state));
    if (state === "ready" && !active) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [jobs, state, load]);

  const active = jobs.filter((j) => !TERMINAL.has(j.state));
  const online = machines.filter((m) => m.status === "active");

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
        <Link
          href="/submit"
          className="interactive inline-flex items-center gap-2 rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110"
        >
          <Plus size={15} weight="bold" />
          New job
        </Link>
      </div>

      {state === "error" ? (
        <div className="mt-8 flex flex-col items-center gap-3 rounded-lg border border-border bg-surface py-10 text-center">
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
      ) : (
        <>
          <div className="mt-6 grid gap-3 sm:grid-cols-3">
            <Stat
              label="Machines online"
              value={online.length}
              total={machines.length}
              loading={state === "loading"}
            />
            <Stat
              label="Jobs running"
              value={active.length}
              total={jobs.length}
              loading={state === "loading"}
            />
            <Stat
              label="Jobs finished"
              value={jobs.length - active.length}
              loading={state === "loading"}
            />
          </div>

          <section className="mt-8">
            <div className="flex items-end justify-between gap-3">
              <h2 className="text-sm font-semibold">Active jobs</h2>
              <Link
                href="/jobs"
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                View all
              </Link>
            </div>

            <div className="mt-3 overflow-hidden rounded-lg border border-border bg-surface">
              {state === "loading" ? (
                <div className="space-y-2 p-4">
                  <div className="skeleton h-9" />
                  <div className="skeleton h-9" />
                </div>
              ) : active.length === 0 ? (
                <EmptyJobs hasAny={jobs.length > 0} />
              ) : (
                <ul className="divide-y divide-border">
                  {active.map((j) => (
                    <li key={j.job_id}>
                      <Link
                        href={`/jobs/${j.job_id}`}
                        className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-white/[0.03]"
                      >
                        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] bg-primary/15 text-primary">
                          <Lightning size={15} weight="fill" />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-mono text-sm">
                            {j.spec?.metadata?.name ?? j.name ?? j.job_id}
                          </span>
                          <span className="block truncate font-mono text-xs text-muted-foreground">
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
        </>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  total,
  loading,
}: {
  label: string;
  value: number;
  total?: number;
  loading: boolean;
}) {
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3.5">
      {loading ? (
        <div className="skeleton h-7 w-16" />
      ) : (
        <div className="metric-value text-2xl">
          {value}
          {total !== undefined && total !== value && (
            <span className="text-base text-muted-foreground">/{total}</span>
          )}
        </div>
      )}
      <div className="label-caps mt-1">{label}</div>
    </div>
  );
}

function EmptyJobs({ hasAny }: { hasAny: boolean }) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-10 text-center">
      <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-border">
        <Lightning size={17} className="text-muted-foreground" />
      </div>
      <p className="max-w-sm text-sm text-muted-foreground">
        {hasAny
          ? "Nothing running right now. Everything you have submitted has finished."
          : "No jobs yet. Point FlashML at a GitHub repo and it will run across whatever machines you have attached."}
      </p>
      <Link
        href="/submit"
        className="inline-flex items-center gap-1.5 text-sm text-primary hover:underline"
      >
        Submit a job
        <ArrowRight size={13} weight="bold" />
      </Link>
    </div>
  );
}
