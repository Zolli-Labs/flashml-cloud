"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Cpu,
  ArrowsIn,
  HardDrive,
  Target,
  ArrowRight,
  CheckCircle,
  Clock,
  Gauge,
  WarningCircle,
  ChartLine,
  MagnifyingGlass,
  Shuffle,
} from "@phosphor-icons/react";
import { MetricCard } from "@/components/shared/MetricCard";
import { WorkerNodeCard } from "@/components/dashboard/WorkerNodeCard";
import { TerminalPanel } from "@/components/shared/TerminalPanel";
import { PipelineTimeline } from "@/components/dashboard/PipelineTimeline";
import { TrainingMetricsChart } from "@/components/dashboard/TrainingMetricsChart";
import { MapReduceTrainingVisualization } from "@/components/dashboard/MapReduceTrainingVisualization";
import { GradientSyncVisualization } from "@/components/dashboard/GradientSyncVisualization";
import { ParallelSearchVisualization } from "@/components/dashboard/ParallelSearchVisualization";
import Link from "next/link";
import { cn } from "@/lib/utils";
import {
  getJobStatus,
  JOB_ID_STORAGE_KEY,
  type JobStatusResponse,
} from "@/lib/api";
import {
  getModelByAlgorithm,
  PATTERN_LABELS,
  PATTERN_COLORS,
} from "@/lib/models";

const STAGE_FOR_STATUS: Record<string, number> = {
  queued: 0,
  vectorizing: 1,
  starting_flash: 2,
  running: 3,
  done: 6,
  error: 3,
};

const ARCH_ICON = {
  map_reduce:     Shuffle,
  gradient_sync:  ChartLine,
  parallel_search: MagnifyingGlass,
} as const;

export function TrainingDashboard() {
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobStatusResponse | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setJobId(localStorage.getItem(JOB_ID_STORAGE_KEY));
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  const poll = useCallback(async () => {
    if (!jobId) return;
    try {
      const data = await getJobStatus(jobId);
      setJob(data);
      setPollError(null);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to reach coordinator";
      if (message.startsWith("404 ")) {
        localStorage.removeItem(JOB_ID_STORAGE_KEY);
        setJobId(null);
        setJob(null);
        setPollError(null);
        return;
      }
      setPollError(message);
    }
  }, [jobId]);

  useEffect(() => {
    if (!jobId) return;
    const immediate = window.setTimeout(poll, 0);
    const interval = setInterval(poll, 1500);
    return () => {
      window.clearTimeout(immediate);
      clearInterval(interval);
    };
  }, [jobId, poll]);

  if (!jobId) {
    return (
      <div className="max-w-2xl mx-auto px-4 py-20 text-center">
        <WarningCircle className="w-10 h-10 text-amber-400 mx-auto mb-4" weight="duotone" />
        <h1 className="text-xl font-bold text-foreground mb-2">No active job</h1>
        <p className="text-sm text-muted-foreground mb-6">Launch a training job first to see live progress here.</p>
        <Link href="/launch" className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-cyan text-background text-sm font-semibold hover:bg-cyan/90 transition-all">
          Go to Launch <ArrowRight weight="bold" className="w-4 h-4" />
        </Link>
      </div>
    );
  }

  if (pollError && !job) {
    return (
      <div className="max-w-2xl mx-auto px-4 py-20 text-center">
        <WarningCircle className="w-10 h-10 text-red-400 mx-auto mb-4" weight="duotone" />
        <h1 className="text-xl font-bold text-foreground mb-2">Can&apos;t reach coordinator</h1>
        <p className="text-sm text-muted-foreground font-mono">{pollError}</p>
        <p className="text-xs text-muted-foreground mt-3">
          Start it with: <code className="text-cyan">cd legacy/coordinator &amp;&amp; ../../.venv/bin/python server.py</code>
        </p>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="max-w-2xl mx-auto px-4 py-20 text-center text-sm text-muted-foreground">
        Loading job fml-{jobId.replace("fml-", "")}...
      </div>
    );
  }

  const arch = job.architecture ?? "map_reduce";
  const mlModel = getModelByAlgorithm(job.algorithm);
  const patternKey = mlModel?.executionPattern ?? "MAP_REDUCE";
  const patternColors = PATTERN_COLORS[patternKey];
  const patternLabel = PATTERN_LABELS[patternKey];
  const modelName = mlModel?.name ?? job.algorithm;
  const ArchIcon = ARCH_ICON[arch] ?? Shuffle;

  const done = job.status === "done";
  const errored = job.status === "error";
  const preparing = job.status === "queued" || job.status === "vectorizing" || job.status === "starting_flash";

  let progress = 0;
  if (done) progress = 100;
  else if (preparing) progress = 5;
  else if (arch === "map_reduce") progress = Math.min(95, (job.iteration / job.max_iter) * 100);
  else if (arch === "gradient_sync") progress = Math.min(95, ((job.epoch ?? 0) / Math.max(1, job.max_epochs ?? job.max_iter)) * 100);
  else if (arch === "parallel_search") progress = Math.min(95, ((job.completed_configs ?? 0) / Math.max(1, job.total_configs ?? 0)) * 100);

  const statusLabel = errored ? "ERROR" : done ? "COMPLETE" : preparing ? job.status.toUpperCase() : "RUNNING";
  const computeType = job.compute_type ?? "cpu";
  const nodeLabel = job.node_label ?? job.node_type ?? "Flash node";

  const workerNodes = job.shard_sizes.map((size, i) => ({
    id: `node-${i}`,
    status: (errored ? "offline" : "running") as "running" | "offline",
    computeType,
    samplesProcessed: size,
    partitionSize: size,
    latencyMs: Math.round((job.iterations_log[job.iterations_log.length - 1]?.elapsed_iter ?? 0) * 1000),
    iteration: arch === "gradient_sync" ? (job.epoch ?? 0) : arch === "parallel_search" ? (job.completed_configs ?? 0) : job.iteration,
    shards: [`shard-${i}`],
  }));

  const movements = job.iterations_log.map((l) => l.movement);
  const throughput = job.iteration > 0 && job.elapsed > 0
    ? Math.round((job.n_points * job.iteration) / job.elapsed) : 0;

  const progressColor =
    arch === "gradient_sync" ? "from-violet-400/80 to-violet-400"
    : arch === "parallel_search" ? "from-node-green/80 to-node-green"
    : "from-cyan/80 to-cyan";
  const progressGlow =
    arch === "gradient_sync" ? "0 0 10px oklch(0.65 0.20 290 / 0.50)"
    : arch === "parallel_search" ? "0 0 10px oklch(0.76 0.18 145 / 0.50)"
    : "0 0 10px oklch(0.80 0.16 200 / 0.50)";
  const completionAccent = patternColors.text;
  const completionBorder = cn(patternColors.border, patternColors.bg);
  const datasetName = job.dataset_filename ?? job.dataset;

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 py-8">
      {/* Header */}
      <div className="flex items-start justify-between mb-8">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <h1 className="text-xl font-bold tracking-tight text-foreground">Job {job.job_id}</h1>
            <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-mono ${errored ? "bg-red-500/10 text-red-400 border border-red-500/30" : done ? "bg-node-green/10 text-node-green border border-node-green/30" : "bg-cyan/10 text-cyan border border-cyan/30"}`}>
              <span className={`w-1.5 h-1.5 rounded-full ${errored ? "bg-red-400" : done ? "bg-node-green" : "bg-cyan pulse-dot"}`} />
              {statusLabel}
            </span>
          </div>
          <div className="flex items-center gap-2 text-sm text-muted-foreground font-mono">
            <ArchIcon className={`w-3.5 h-3.5 ${patternColors.text}`} weight="duotone" />
            <span className={patternColors.text}>{modelName}</span>
            <span className="text-muted-foreground/50">·</span>
            <span className={cn("text-xs", patternColors.text)}>{patternLabel}</span>
            <span>· {job.workers_count} {nodeLabel} nodes</span>
          </div>
          {mlModel && (
            <p className="text-xs text-muted-foreground/60 mt-1.5 max-w-xl leading-relaxed">
              {mlModel.distributedStrategy}
            </p>
          )}
        </div>
        {done && arch === "map_reduce" && (
          <Link href="/visualize" className="hidden sm:inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-cyan text-background text-sm font-semibold hover:bg-cyan/90 active:scale-[0.98] transition-all glow-cyan">
            View Results <ArrowRight weight="bold" className="w-4 h-4" />
          </Link>
        )}
      </div>

      {errored && (
        <div className="mb-6 p-4 rounded-lg border border-red-500/30 bg-red-500/5 text-sm text-red-400 font-mono">{job.error}</div>
      )}

      {/* Progress bar */}
      <div className="mb-6 p-4 rounded-lg border border-border/60 bg-surface">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs font-mono text-muted-foreground">{preparing ? `Preparing — ${job.status.replace("_", " ")}` : "Job Progress"}</span>
          <span className="text-sm font-mono font-bold text-foreground metric-value">{progress.toFixed(1)}%</span>
        </div>
        <div className="w-full h-2 rounded-full bg-white/5">
          <div className={`h-full rounded-full bg-gradient-to-r transition-all duration-500 ${progressColor}`} style={{ width: `${progress}%`, boxShadow: progressGlow }} />
        </div>
      </div>

      {/* Metrics — architecture + model specific */}
      {arch === "gradient_sync" ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
          <MetricCard label="Samples" value={job.n_points || "—"} trendValue={datasetName} trend="up" icon={<HardDrive weight="duotone" />} accent="cyan" />
          <MetricCard label="Epoch" value={job.epoch ?? 0} unit={`/ ${job.max_epochs ?? job.max_iter}`} icon={<ArrowsIn weight="duotone" />} accent="cyan" />
          <MetricCard label="Train Loss" value={job.training_loss != null ? job.training_loss.toFixed(4) : "—"} icon={<Target weight="duotone" />} accent="amber" />
          {job.val_accuracy != null
            ? <MetricCard label="Val Accuracy" value={`${(job.val_accuracy * 100).toFixed(1)}%`} icon={<Gauge weight="duotone" />} accent="green" />
            : <MetricCard label="Val MSE" value={job.validation_mse != null ? job.validation_mse.toFixed(4) : "—"} icon={<Gauge weight="duotone" />} accent="amber" />}
          <MetricCard label="Elapsed" value={`${job.elapsed}s`} icon={<Clock weight="duotone" />} accent="amber" />
          <MetricCard label="Workers" value={job.workers_count} unit={nodeLabel} icon={<Cpu weight="duotone" />} accent="cyan" />
        </div>
      ) : arch === "parallel_search" ? (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
          <MetricCard label={job.algorithm === "random_forest" ? "Total Trees" : "Total Configs"} value={job.total_configs || "—"} trendValue="search space" trend="up" icon={<MagnifyingGlass weight="duotone" />} accent="green" />
          <MetricCard label={job.algorithm === "random_forest" ? "Trees Done" : "Completed"} value={job.completed_configs ?? 0} unit={job.total_configs ? `/ ${job.total_configs}` : undefined} icon={<ArrowsIn weight="duotone" />} accent="cyan" />
          {job.val_accuracy != null
            ? <MetricCard label="Forest Accuracy" value={`${(job.val_accuracy * 100).toFixed(1)}%`} icon={<Target weight="duotone" />} accent="green" />
            : <MetricCard label="Best R²" value={job.best_score != null ? job.best_score.toFixed(4) : "—"} icon={<Target weight="duotone" />} accent="green" />}
          {job.algorithm === "random_forest"
            ? <MetricCard label="Best OOB" value={job.best_score != null ? `${(job.best_score * 100).toFixed(1)}%` : "—"} icon={<Gauge weight="duotone" />} accent="amber" />
            : <MetricCard label="Best Config" value={job.best_config ? `d=${job.best_config.degree}` : "—"} unit={job.best_config ? `α=${job.best_config.alpha}` : undefined} icon={<Gauge weight="duotone" />} accent="amber" />}
          <MetricCard label="Elapsed" value={`${job.elapsed}s`} icon={<Clock weight="duotone" />} accent="amber" />
          <MetricCard label="Workers" value={job.workers_count} unit={nodeLabel} icon={<Cpu weight="duotone" />} accent="cyan" />
        </div>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
          <MetricCard label="Samples" value={job.n_points || "—"} trendValue={datasetName} trend="up" icon={<HardDrive weight="duotone" />} accent="cyan" />
          <MetricCard label="Iteration" value={job.iteration} unit={`/ ${job.max_iter}`} icon={<ArrowsIn weight="duotone" />} accent="cyan" />
          <MetricCard label="Throughput" value={throughput.toLocaleString()} unit="pts/s" icon={<Gauge weight="duotone" />} accent="green" />
          <MetricCard label="Elapsed" value={`${job.elapsed}s`} icon={<Clock weight="duotone" />} accent="amber" />
          <MetricCard label="Workers" value={job.workers_count} unit={nodeLabel} icon={<Cpu weight="duotone" />} accent="cyan" />
          <MetricCard label="Movement" value={job.movement?.toFixed(4) ?? "—"} unit={job.converged_at ? `converged @ ${job.converged_at}` : undefined} icon={<Target weight="duotone" />} accent="cyan" />
        </div>
      )}

      {/* Architecture visualization */}
      <div className="mb-6">
        {arch === "gradient_sync" && <GradientSyncVisualization job={job} />}
        {arch === "parallel_search" && <ParallelSearchVisualization job={job} />}
        {arch === "map_reduce" && <MapReduceTrainingVisualization job={job} />}
      </div>

      {/* Charts + terminal */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2 space-y-5">
          {arch === "map_reduce" && (
            <>
              <TrainingMetricsChart movements={movements} maxIter={job.max_iter} />
              <PipelineTimeline currentStage={STAGE_FOR_STATUS[job.status] ?? 0} />
            </>
          )}

          {arch === "gradient_sync" && (
            <div className="p-4 rounded-lg border border-border/60 bg-surface">
              <div className="text-xs font-mono text-muted-foreground uppercase tracking-wider mb-3">Epoch log</div>
              <div className="space-y-1.5 max-h-64 overflow-y-auto">
                {(job.epochs_log ?? []).slice().reverse().map((e) => (
                  <div key={e.epoch} className="flex items-center gap-3 text-[11px] font-mono">
                    <span className="text-muted-foreground/50 w-16 shrink-0">epoch {e.epoch}</span>
                    <span className="text-violet-400">loss={e.loss.toFixed(4)}</span>
                    <span className="text-amber-400">val={e.val_mse.toFixed(4)}</span>
                    <span className="text-node-green">‖∇‖={e.gradient_norm.toFixed(4)}</span>
                  </div>
                ))}
                {!(job.epochs_log?.length) && <div className="text-xs font-mono text-muted-foreground/40">waiting for first epoch...</div>}
              </div>
            </div>
          )}

          {arch === "parallel_search" && (
            <div className="p-4 rounded-lg border border-border/60 bg-surface">
              <div className="text-xs font-mono text-muted-foreground uppercase tracking-wider mb-3">Completed tasks</div>
              <div className="space-y-1.5 max-h-64 overflow-y-auto">
                {(job.worker_results ?? []).slice().reverse().map((r) => (
                  <div key={r.task_id} className="flex items-center gap-3 text-[11px] font-mono">
                    <span className="text-muted-foreground/50 w-14 shrink-0">{r.worker_id}</span>
                    <span className="text-foreground/70">deg={r.config.degree} α={r.config.alpha}</span>
                    <span className="text-node-green ml-auto">R²={r.score.toFixed(4)}</span>
                    <span className="text-muted-foreground/50">{r.runtime_ms.toFixed(0)}ms</span>
                  </div>
                ))}
                {!(job.worker_results?.length) && <div className="text-xs font-mono text-muted-foreground/40">waiting for first result...</div>}
              </div>
            </div>
          )}

          <div>
            <div className="text-xs font-mono text-muted-foreground uppercase tracking-wider mb-3">
              Worker Nodes ({job.workers_count} {nodeLabel} nodes)
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              {workerNodes.map((w) => (
                <WorkerNodeCard key={w.id} {...w} />
              ))}
              {workerNodes.length === 0 && (
                <div className="col-span-2 text-xs font-mono text-muted-foreground p-4 border border-border/40 rounded-lg">Provisioning workers...</div>
              )}
            </div>
          </div>
        </div>

        <div className="space-y-5">
          <TerminalPanel streaming={!done && !errored} job={job} />

          <div className="p-4 rounded-lg border border-border/60 bg-surface space-y-3">
            <div className="text-xs font-mono text-muted-foreground uppercase tracking-wider mb-2">Job Config</div>
            {(arch === "map_reduce" ? [
              { label: "Model", value: modelName + (job.algorithm === "kmeans" ? ` (k=${job.k})` : "") },
              { label: "Architecture", value: patternLabel },
              ...(job.algorithm === "kmeans" ? [{ label: "Embedding", value: job.embedding_model ? `${job.embedding_model}${job.embedding_fallback ? " (fallback)" : ""}` : "initializing" }] : []),
              { label: "Dataset", value: job.n_points ? `${job.n_points.toLocaleString()} samples` : (datasetName ?? "generating") },
              { label: "Workers", value: `${job.workers_count} ${nodeLabel} nodes` },
              { label: "Job ID", value: job.job_id },
            ] : arch === "gradient_sync" ? [
              { label: "Model", value: modelName },
              { label: "Architecture", value: patternLabel },
              { label: "Dataset", value: job.n_points ? `${job.n_points.toLocaleString()} samples · ${datasetName}` : "loading sklearn dataset" },
              { label: "Max Epochs", value: String(job.max_epochs ?? job.max_iter) },
              { label: "Workers", value: `${job.workers_count} ${nodeLabel} nodes` },
              { label: "Job ID", value: job.job_id },
            ] : [
              { label: "Model", value: modelName },
              { label: "Architecture", value: patternLabel },
              { label: "Dataset", value: job.n_points ? `${job.n_points.toLocaleString()} samples · ${datasetName}` : "loading sklearn dataset" },
              { label: "Total Tasks", value: job.total_configs ? String(job.total_configs) : "generating" },
              { label: "Workers", value: `${job.workers_count} ${nodeLabel} nodes` },
              { label: "Job ID", value: job.job_id },
            ]).map(({ label, value }) => (
              <div key={label} className="flex justify-between items-center">
                <span className="text-xs font-mono text-muted-foreground">{label}</span>
                <span className="text-xs font-mono text-foreground/80 truncate max-w-[160px] text-right">{value}</span>
              </div>
            ))}
          </div>

          {done && (
            <div className={`p-4 rounded-lg border ${completionBorder}`}>
              <div className="flex items-center gap-2 mb-3">
                <CheckCircle className={`w-4 h-4 ${completionAccent}`} weight="fill" />
                <span className={`text-sm font-semibold ${completionAccent}`}>Training Complete</span>
              </div>
              <div className="space-y-2">
                {(arch === "map_reduce" ? [
                  ...(job.algorithm === "naive_bayes" ? [
                    { label: "Train Accuracy", value: job.train_accuracy != null ? `${(job.train_accuracy * 100).toFixed(1)}%` : "—" },
                    { label: "Val Accuracy", value: job.val_accuracy != null ? `${(job.val_accuracy * 100).toFixed(1)}%` : "—" },
                  ] : [
                    { label: "Final Movement", value: job.movement?.toFixed(6) ?? "—" },
                    { label: "Clusters", value: String(job.k) },
                    { label: "Convergence", value: `Iter ${job.converged_at ?? job.iteration}` },
                  ]),
                  { label: "Total Time", value: `${job.elapsed}s` },
                ] : arch === "gradient_sync" ? [
                  { label: "Final Loss", value: job.training_loss?.toFixed(6) ?? "—" },
                  ...(job.val_accuracy != null ? [
                    { label: "Val Accuracy", value: `${(job.val_accuracy * 100).toFixed(1)}%` },
                  ] : [
                    { label: "Val MSE", value: job.validation_mse?.toFixed(6) ?? "—" },
                  ]),
                  { label: "Epochs", value: String(job.epoch ?? "—") },
                  { label: "Total Time", value: `${job.elapsed}s` },
                ] : [
                  ...(job.val_accuracy != null ? [
                    { label: "Forest Accuracy", value: `${(job.val_accuracy * 100).toFixed(1)}%` },
                    { label: "Trees Trained", value: String(job.completed_configs ?? "—") },
                  ] : [
                    { label: "Best R²", value: job.best_score?.toFixed(6) ?? "—" },
                    { label: "Best Config", value: job.best_config ? `d=${job.best_config.degree}, α=${job.best_config.alpha}` : "—" },
                    { label: "Configs Tested", value: String(job.completed_configs ?? "—") },
                  ]),
                  { label: "Total Time", value: `${job.elapsed}s` },
                ]).map(({ label, value }) => (
                  <div key={label} className="flex justify-between">
                    <span className="text-xs font-mono text-muted-foreground">{label}</span>
                    <span className={`text-xs font-mono font-semibold ${completionAccent}`}>{value}</span>
                  </div>
                ))}
              </div>
              {arch === "map_reduce" && (
                <Link href="/visualize" className="mt-4 w-full py-2 rounded-lg bg-node-green/15 border border-node-green/30 text-node-green text-sm font-semibold flex items-center justify-center gap-2 hover:bg-node-green/20 transition-colors">
                  View Clusters <ArrowRight weight="bold" className="w-3.5 h-3.5" />
                </Link>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
