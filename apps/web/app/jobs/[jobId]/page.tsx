"use client";

import { use, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  fetchEvents,
  fetchJob,
  formatBytes,
  type FlashEvent,
  type JobRecord,
} from "@/lib/poc-api";
import { StateBadge } from "../page";

const eventAccent: Record<string, string> = {
  RAY_WORKER_LOST: "border-red-400/60 text-red-400",
  NODE_HEARTBEAT_LOST: "border-red-400/60 text-red-400",
  TASK_ATTEMPT_RETRIED: "border-amber-400/60 text-amber-400",
  RAY_WORKER_REPLACED: "border-node-green/60 text-node-green",
  JOB_SUCCEEDED: "border-node-green/60 text-node-green",
  ARTIFACT_COMMITTED: "border-node-green/60 text-node-green",
  JOB_FAILED: "border-red-400/60 text-red-400",
};

export default function JobDetailPage({
  params,
}: {
  params: Promise<{ jobId: string }>;
}) {
  const { jobId } = use(params);
  const [job, setJob] = useState<JobRecord | null>(null);
  const [events, setEvents] = useState<FlashEvent[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const tick = () =>
      Promise.all([fetchJob(jobId), fetchEvents(jobId)])
        .then(([j, e]) => {
          if (!live) return;
          setJob(j);
          setEvents(e);
          setError(null);
        })
        .catch((e) => live && setError(String(e)));
    tick();
    const t = setInterval(tick, 2500);
    return () => { live = false; clearInterval(t); };
  }, [jobId]);

  const retries = events.filter((e) => e.type === "TASK_ATTEMPT_RETRIED").length;
  const workersLost = events.filter((e) => e.type === "RAY_WORKER_LOST").length;
  const iterations = events.filter((e) => e.type === "ITERATION_COMPLETED").length;

  return (
    <main className="max-w-7xl mx-auto px-4 sm:px-6 py-8 space-y-6">
      {error && (
        <Card className="border-red-500/30">
          <CardContent className="py-4 text-sm text-red-400 font-mono">{error}</CardContent>
        </Card>
      )}

      {job && (
        <>
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold font-mono">{job.spec.metadata.name}</h1>
              <p className="text-xs text-muted-foreground font-mono mt-1">
                {job.job_id} · backend {job.backend} · profile {job.deployment_profile}
                {job.runtime_execution_id && <> · {job.runtime_execution_id}</>}
              </p>
            </div>
            <StateBadge state={job.state} />
          </div>

          <div className="grid gap-3 grid-cols-2 md:grid-cols-4">
            <Stat label="iterations" value={iterations} />
            <Stat label="workers lost" value={workersLost} warn={workersLost > 0} />
            <Stat label="task retries" value={retries} warn={retries > 0} />
            <Stat label="artifacts" value={job.artifacts.length} />
          </div>

          {job.error && (
            <Card className="border-red-500/30">
              <CardContent className="py-3 text-sm font-mono text-red-400">
                {job.error}
              </CardContent>
            </Card>
          )}

          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-mono">Event timeline</CardTitle>
              </CardHeader>
              <CardContent>
                <ol className="space-y-2 max-h-[32rem] overflow-y-auto pr-2">
                  {events.map((e, i) => (
                    <li key={i} className="flex gap-3 text-xs">
                      <span className="font-mono text-muted-foreground shrink-0 w-20">
                        {new Date(e.timestamp).toLocaleTimeString()}
                      </span>
                      <Badge
                        variant="outline"
                        className={`shrink-0 font-mono text-[10px] ${
                          eventAccent[e.type] ?? "text-cyan border-cyan/30"
                        }`}
                      >
                        {e.type}
                      </Badge>
                      <span className="text-muted-foreground">{e.message}</span>
                    </li>
                  ))}
                  {events.length === 0 && (
                    <p className="text-xs font-mono text-muted-foreground">no events yet</p>
                  )}
                </ol>
              </CardContent>
            </Card>

            <div className="space-y-6">
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm font-mono">Artifacts</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  {job.artifacts.map((a) => (
                    <div key={a.uri} className="flex justify-between text-xs font-mono">
                      <span className="text-cyan truncate">{a.uri}</span>
                      <span className="text-muted-foreground shrink-0 ml-3">
                        {a.backend} · {formatBytes(a.size_bytes)}
                      </span>
                    </div>
                  ))}
                  {job.artifacts.length === 0 && (
                    <p className="text-xs font-mono text-muted-foreground">
                      artifacts appear after the job succeeds
                    </p>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="text-sm font-mono">Spec</CardTitle>
                </CardHeader>
                <CardContent className="text-xs font-mono space-y-1.5">
                  <Row k="image" v={`${job.spec.spec.image.repository}:${job.spec.spec.image.tag}`} />
                  <Row k="workload" v={job.spec.spec.workload.type} />
                  <Row
                    k="workers"
                    v={`${job.spec.spec.resources.minimumWorkers}–${job.spec.spec.resources.maximumWorkers}`}
                  />
                  <Row k="isolation" v={job.spec.spec.isolation.tier} />
                  <Row
                    k="parameters"
                    v={JSON.stringify(job.spec.spec.workload.parameters)}
                  />
                </CardContent>
              </Card>
            </div>
          </div>
        </>
      )}
    </main>
  );
}

function Stat({ label, value, warn }: { label: string; value: number; warn?: boolean }) {
  return (
    <Card>
      <CardContent className="py-3">
        <div className={`text-2xl font-bold font-mono ${warn ? "text-amber-400" : "text-cyan"}`}>
          {value}
        </div>
        <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      </CardContent>
    </Card>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-4">
      <span className="text-muted-foreground uppercase text-[10px] tracking-wide shrink-0">{k}</span>
      <span className="truncate">{v}</span>
    </div>
  );
}
