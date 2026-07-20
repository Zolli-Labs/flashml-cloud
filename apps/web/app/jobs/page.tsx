"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { fetchJobs, type JobRecord, type JobState } from "@/lib/poc-api";

const stateStyles: Record<JobState, string> = {
  PENDING: "text-muted-foreground border-muted",
  SUBMITTED: "text-cyan border-cyan/40",
  RUNNING: "text-cyan border-cyan/40",
  RECOVERING: "text-amber-400 border-amber-400/40",
  SUCCEEDED: "text-node-green border-node-green/40",
  FAILED: "text-red-400 border-red-400/40",
  CANCELLED: "text-muted-foreground border-muted",
};

export function StateBadge({ state }: { state: JobState }) {
  return (
    <Badge variant="outline" className={`font-mono text-xs ${stateStyles[state]}`}>
      {state}
    </Badge>
  );
}

export default function JobsPage() {
  const [jobs, setJobs] = useState<JobRecord[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const tick = () =>
      fetchJobs()
        .then((j) => live && (setJobs(j), setError(null)))
        .catch((e) => live && setError(String(e)));
    tick();
    const t = setInterval(tick, 3000);
    return () => { live = false; clearInterval(t); };
  }, []);

  return (
    <main className="max-w-7xl mx-auto px-4 sm:px-6 py-8 space-y-6">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold font-mono">Jobs</h1>
          <p className="text-sm text-muted-foreground mt-1">
            FlashRuntime executions and their recovery history
          </p>
        </div>
        <Link
          href="/submit"
          className="px-4 py-2 rounded-md bg-cyan/10 text-cyan border border-cyan/30 hover:bg-cyan/20 text-sm font-medium"
        >
          Submit job
        </Link>
      </div>

      {error && (
        <Card className="border-red-500/30">
          <CardContent className="py-4 text-sm text-red-400 font-mono">
            cloud API unreachable: {error}
          </CardContent>
        </Card>
      )}

      <div className="space-y-2">
        {jobs?.map((j) => (
          <Link key={j.job_id} href={`/jobs/${j.job_id}`} className="block">
            <Card className="hover:border-cyan/40 transition-colors">
              <CardContent className="py-3 flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <div className="font-mono text-sm">{j.spec.metadata.name}</div>
                  <div className="text-xs text-muted-foreground font-mono truncate">
                    {j.job_id} · {j.backend} · {j.deployment_profile} ·{" "}
                    {new Date(j.created_at).toLocaleString()}
                  </div>
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  {j.artifacts.length > 0 && (
                    <span className="text-xs text-muted-foreground font-mono">
                      {j.artifacts.length} artifacts
                    </span>
                  )}
                  <StateBadge state={j.state} />
                </div>
              </CardContent>
            </Card>
          </Link>
        ))}
        {jobs && jobs.length === 0 && (
          <p className="text-muted-foreground text-sm font-mono">
            no jobs yet — submit the K-Means demo from the Submit page
          </p>
        )}
      </div>
    </main>
  );
}
