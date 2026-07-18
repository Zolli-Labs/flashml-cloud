"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ClusterVisualization } from "@/components/viz/ClusterVisualization";
import { MapReduceTrainingVisualization } from "@/components/dashboard/MapReduceTrainingVisualization";
import { MetricCard } from "@/components/shared/MetricCard";
import { Badge } from "@/components/ui/badge";
import {
  CheckCircle,
  ArrowsIn,
  ChartBar,
  Clock,
  WarningCircle,
  ArrowRight,
} from "@phosphor-icons/react";
import { getResults, JOB_ID_STORAGE_KEY, type ResultsResponse } from "@/lib/api";

const CLUSTER_STYLES = [
  { color: "bg-cyan", border: "border-cyan/30", text: "text-cyan" },
  { color: "bg-violet-400", border: "border-violet-400/30", text: "text-violet-400" },
  { color: "bg-node-green", border: "border-node-green/30", text: "text-node-green" },
  { color: "bg-amber-400", border: "border-amber-400/30", text: "text-amber-400" },
  { color: "bg-red-400", border: "border-red-400/30", text: "text-red-400" },
  { color: "bg-pink-400", border: "border-pink-400/30", text: "text-pink-400" },
];

export function VisualizeResults() {
  const [jobId, setJobId] = useState<string | null>(null);
  const [results, setResults] = useState<ResultsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setJobId(localStorage.getItem(JOB_ID_STORAGE_KEY));
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (!jobId) return;
    getResults(jobId)
      .then(setResults)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Failed to load results")
      );
  }, [jobId]);

  if (!jobId) {
    return (
      <div className="max-w-2xl mx-auto px-4 py-20 text-center">
        <WarningCircle className="w-10 h-10 text-amber-400 mx-auto mb-4" weight="duotone" />
        <h1 className="text-xl font-bold text-foreground mb-2">No completed job</h1>
        <p className="text-sm text-muted-foreground mb-6">
          Launch a training job first to see results here.
        </p>
        <Link
          href="/launch"
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-cyan text-background text-sm font-semibold hover:bg-cyan/90 transition-all"
        >
          Go to Launch <ArrowRight weight="bold" className="w-4 h-4" />
        </Link>
      </div>
    );
  }

  if (error) {
    return (
      <div className="max-w-2xl mx-auto px-4 py-20 text-center">
        <WarningCircle className="w-10 h-10 text-red-400 mx-auto mb-4" weight="duotone" />
        <h1 className="text-xl font-bold text-foreground mb-2">Can&apos;t load results</h1>
        <p className="text-sm text-muted-foreground font-mono mb-4">{error}</p>
        <Link
          href="/dashboard"
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-cyan text-background text-sm font-semibold hover:bg-cyan/90 transition-all"
        >
          Check Job Status <ArrowRight weight="bold" className="w-4 h-4" />
        </Link>
      </div>
    );
  }

  if (!results) {
    return (
      <div className="max-w-2xl mx-auto px-4 py-20 text-center text-sm text-muted-foreground">
        Loading results for {jobId}...
      </div>
    );
  }

  const clusters = results.cluster_info ?? [];

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 py-8 space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-foreground mb-1">
          Model Results
        </h1>
        <p className="text-sm text-muted-foreground">
          Job {results.job_id} — K-Means clustering, {results.n_points} customer support tickets,{" "}
          {results.converged_at ?? results.iteration} iterations
        </p>
      </div>

      {/* Result metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricCard
          label="Clusters"
          value={String(results.k)}
          unit="final"
          accent="cyan"
          icon={<ChartBar weight="duotone" />}
        />
        <MetricCard
          label="Final Movement"
          value={results.movement?.toFixed(4) ?? "—"}
          trendValue="converged"
          trend="up"
          accent="green"
          icon={<CheckCircle weight="duotone" />}
        />
        <MetricCard
          label="Iterations"
          value={String(results.converged_at ?? results.iteration)}
          unit={`/ ${results.max_iter}`}
          accent="cyan"
          icon={<ArrowsIn weight="duotone" />}
        />
        <MetricCard
          label="Total Time"
          value={`${results.elapsed}s`}
          accent="amber"
          icon={<Clock weight="duotone" />}
        />
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <ClusterVisualization
          points2d={results.X_2d}
          assignments={results.assignments}
          k={results.k}
          nPoints={results.n_points}
        />
        <div className="rounded-lg border border-border/60 bg-surface p-4">
          <div className="mb-3 text-xs font-mono uppercase tracking-wider text-muted-foreground">
            Training snapshots
          </div>
          <div className="space-y-2 text-xs font-mono text-muted-foreground">
            <div className="flex justify-between">
              <span>Recorded iterations</span>
              <span className="text-foreground/80">{results.training_iterations?.length ?? 0}</span>
            </div>
            <div className="flex justify-between">
              <span>Iteration logs</span>
              <span className="text-foreground/80">{results.iterations_log.length}</span>
            </div>
            <div className="flex justify-between">
              <span>Projected points</span>
              <span className="text-foreground/80">{results.X_2d.length.toLocaleString()}</span>
            </div>
          </div>
        </div>
      </div>

      <MapReduceTrainingVisualization job={results} />

      {/* Cluster breakdown */}
      <div className="p-5 rounded-xl border border-border/60 bg-surface">
        <h2 className="text-sm font-semibold text-foreground mb-4">Cluster Breakdown</h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {clusters.map((c) => {
            const style = CLUSTER_STYLES[c.id % CLUSTER_STYLES.length];
            const centroidPreview = c.centroid
              .slice(0, 3)
              .map((v) => v.toFixed(2))
              .join(", ");
            return (
              <div key={c.id} className={`p-4 rounded-lg border ${style.border} bg-surface-elevated`}>
                <div className="flex items-center gap-2 mb-3">
                  <span className={`w-3 h-3 rounded-full ${style.color}`} />
                  <span className="text-sm font-semibold text-foreground">Cluster {c.id}</span>
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between text-xs font-mono">
                    <span className="text-muted-foreground">Samples</span>
                    <span className={style.text}>{c.count.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between text-xs font-mono">
                    <span className="text-muted-foreground">Coverage</span>
                    <span className="text-foreground/80">{c.pct.toFixed(1)}%</span>
                  </div>

                  {c.top_terms.length > 0 && (
                    <div className="pt-2 border-t border-border/40">
                      <div className="text-[10px] font-mono text-muted-foreground mb-1.5">Top terms</div>
                      <div className="flex flex-wrap gap-1">
                        {c.top_terms.map((term) => (
                          <Badge key={term} variant="outline" className={style.text}>
                            {term}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}

                  {c.sample_categories.length > 0 && (
                    <div className="pt-2 border-t border-border/40">
                      <div className="text-[10px] font-mono text-muted-foreground mb-1">
                        Sample categories
                      </div>
                      <div className="text-[10px] font-mono text-foreground/60">
                        {c.sample_categories.join(", ")}
                      </div>
                    </div>
                  )}

                  <div className="pt-2 border-t border-border/40">
                    <div className="text-[10px] font-mono text-muted-foreground mb-1">Centroid</div>
                    <div className="text-[10px] font-mono text-foreground/60">
                      [{centroidPreview}, ...]
                    </div>
                  </div>
                </div>
                {/* Mini bar */}
                <div className="mt-3 w-full h-1 rounded-full bg-white/5">
                  <div
                    className={`h-full rounded-full ${style.color} opacity-70`}
                    style={{ width: `${c.pct}%` }}
                  />
                </div>
              </div>
            );
          })}
          {clusters.length === 0 && (
            <div className="col-span-3 text-xs font-mono text-muted-foreground p-4 border border-border/40 rounded-lg">
              No cluster info available for this job.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
