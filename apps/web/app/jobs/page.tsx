"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowClockwise, Warning } from "@phosphor-icons/react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { NotAuthenticated, listJobs, type JobRecord, type JobState } from "@/lib/cloud-api";

const stateStyles: Record<JobState, string> = {
  PENDING: "text-muted-foreground border-muted",
  SUBMITTED: "text-cyan border-cyan/40",
  RUNNING: "text-cyan border-cyan/40",
  RECOVERING: "text-amber-400 border-amber-400/40",
  SUCCEEDED: "text-node-green border-node-green/40",
  FAILED: "text-destructive border-destructive/40",
  CANCELLED: "text-muted-foreground border-muted",
};

export function StateBadge({ state }: { state: JobState }) {
  return (
    <Badge
      variant="outline"
      className={`font-mono text-xs ${stateStyles[state] ?? "text-muted-foreground border-muted"}`}
    >
      {state}
    </Badge>
  );
}

// Terminal states: once a job lands here it will never change again, so
// polling it is just load with no payoff. `/jobs` polls anyway (the list as
// a whole can always gain a new job or a state transition among the
// non-terminal ones) but stops entirely once every job on the page is
// terminal.
const TERMINAL_STATES: ReadonlySet<JobState> = new Set([
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
]);

const POLL_MS = 3000;

type LoadState = "loading" | "ready" | "error";

export default function JobsPage() {
  const router = useRouter();
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [state, setState] = useState<LoadState>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const load = useCallback(() => {
    listJobs()
      .then((result) => {
        setJobs(result);
        setState("ready");
        setErrorMessage(null);
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push("/sign-in?next=/jobs");
          return;
        }
        setErrorMessage(
          err instanceof Error ? err.message : "Couldn't load your jobs."
        );
        setState("error");
      });
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const allTerminal =
      jobs.length > 0 && jobs.every((j) => TERMINAL_STATES.has(j.state));
    if (allTerminal) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [jobs, load]);

  function refresh() {
    setState("loading");
    setErrorMessage(null);
    load();
  }

  return (
    <main className="max-w-4xl mx-auto px-4 sm:px-6 py-8 space-y-6">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold font-mono">Jobs</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Everything you've submitted.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={refresh}
            disabled={state === "loading"}
            aria-label="Refresh"
          >
            <ArrowClockwise className={state === "loading" ? "animate-spin" : ""} />
          </Button>
          <Link
            href="/submit"
            className="px-4 py-2 rounded-md bg-cyan/10 text-cyan border border-cyan/30 hover:bg-cyan/20 text-sm font-medium"
          >
            Submit job
          </Link>
        </div>
      </div>

      {state === "loading" && jobs.length === 0 ? (
        <div className="space-y-3">
          {[0, 1].map((i) => (
            <Card key={i}>
              <CardContent className="h-16 animate-pulse bg-muted/30 rounded-lg" />
            </Card>
          ))}
        </div>
      ) : state === "error" ? (
        <Card>
          <CardContent className="flex flex-col items-center text-center gap-3 py-8">
            <Warning className="w-6 h-6 text-destructive" weight="fill" />
            <p className="text-sm text-muted-foreground">{errorMessage}</p>
            <Button type="button" variant="outline" size="sm" onClick={refresh}>
              Try again
            </Button>
          </CardContent>
        </Card>
      ) : jobs.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center text-center gap-2 py-10">
            <p className="text-sm text-muted-foreground">
              No jobs yet.{" "}
              <Link href="/submit" className="text-cyan underline underline-offset-2">
                Submit a repo
              </Link>{" "}
              to get started.
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {jobs.map((j) => (
            <Link key={j.job_id} href={`/jobs/${j.job_id}`} className="block">
              <Card className="hover:border-cyan/40 transition-colors">
                <CardContent className="py-3 flex items-center justify-between gap-4">
                  <div className="min-w-0">
                    <div className="font-mono text-sm truncate">
                      {j.spec.metadata.name}
                    </div>
                    <div className="text-xs text-muted-foreground font-mono truncate">
                      {j.job_id} · {new Date(j.created_at).toLocaleString()}
                    </div>
                  </div>
                  <StateBadge state={j.state} />
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </main>
  );
}
