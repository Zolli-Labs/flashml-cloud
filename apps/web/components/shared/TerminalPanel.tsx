"use client";

import { useEffect, useMemo, useRef } from "react";
import { Terminal } from "@phosphor-icons/react";
import { cn } from "@/lib/utils";
import type { JobStatusResponse } from "@/lib/api";

const levelStyles: Record<string, string> = {
  INFO: "text-cyan/60",
  WARN: "text-amber-400",
  ERROR: "text-red-400",
};

interface TerminalPanelProps {
  streaming?: boolean;
  className?: string;
  job?: JobStatusResponse;
}

type LogLine = {
  time: string;
  level: "INFO" | "WARN" | "ERROR";
  msg: string;
};

function formatLogTime(seconds: number) {
  const s = Math.max(0, Math.round(seconds));
  return `T+${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function datasetLabel(job: JobStatusResponse) {
  return job.dataset_filename ?? job.dataset;
}

function nodeLabel(job: JobStatusResponse) {
  return job.node_label ?? job.node_type ?? "Flash";
}

function buildNaiveBayesLogs(job: JobStatusResponse): LogLine[] {
  const logs: LogLine[] = [
    { time: "T+00:00", level: "INFO", msg: `FlashML job initialized — job_id=${job.job_id} · architecture=map_reduce` },
    { time: "T+00:00", level: "INFO", msg: `Algorithm: Gaussian Naive Bayes (MAP class stats → REDUCE merge + predict)` },
  ];

  if (job.status === "queued") {
    logs.push({ time: "T+00:00", level: "INFO", msg: "Job queued — loading sklearn wine dataset..." });
    return logs;
  }

  if (job.n_points > 0) {
    logs.push({ time: "T+00:00", level: "INFO", msg: `Dataset loaded: ${datasetLabel(job)} (${job.n_points.toLocaleString()} rows)` });
  }

  if (job.shard_sizes.length > 0) {
    logs.push({ time: "T+00:01", level: "INFO", msg: `Partitioning into ${job.shard_sizes.length} shards across ${job.workers_count} workers` });
    job.shard_sizes.forEach((s, i) => {
      logs.push({ time: "T+00:01", level: "INFO", msg: `Shard ${i}: ${s} samples → worker node-${i}` });
    });
    logs.push({ time: "T+00:01", level: "INFO", msg: `MAP phase: each worker computing class counts, feature sums, feature²` });
  }

  job.iterations_log.forEach((entry) => {
    const classEntries = Object.entries(entry.cluster_sizes);
    const classCounts = classEntries.map(([c, n]) => `class_${c}=${n}`).join(", ");
    logs.push({ time: formatLogTime(entry.elapsed_total), level: "INFO", msg: `MAP shard ${entry.iter} — ${classCounts}` });
    logs.push({ time: formatLogTime(entry.elapsed_total), level: "INFO", msg: `REDUCE: merging shard ${entry.iter} stats into global accumulators` });
  });

  if (job.status === "done") {
    logs.push({ time: formatLogTime(job.elapsed), level: "INFO", msg: `REDUCE complete — computing class probabilities, means, variances` });
    logs.push({ time: formatLogTime(job.elapsed), level: "INFO", msg: `Predicting with Gaussian log-likelihood on val set` });
    if (job.val_accuracy != null) {
      logs.push({ time: formatLogTime(job.elapsed), level: "INFO", msg: `Job complete — train_acc=${job.train_accuracy?.toFixed(4) ?? "—"}, val_acc=${job.val_accuracy.toFixed(4)}` });
    } else {
      logs.push({ time: formatLogTime(job.elapsed), level: "INFO", msg: `Job complete in ${job.elapsed.toFixed(2)}s` });
    }
  }
  if (job.status === "error") {
    logs.push({ time: formatLogTime(job.elapsed), level: "ERROR", msg: job.error ?? "Training failed" });
  }
  return logs;
}

function buildMapReduceLogs(job: JobStatusResponse): LogLine[] {
  if (job.algorithm === "naive_bayes") return buildNaiveBayesLogs(job);

  const logs: LogLine[] = [
    { time: "T+00:00", level: "INFO", msg: `FlashML job initialized — job_id=${job.job_id} · architecture=map_reduce` },
    { time: "T+00:00", level: "INFO", msg: `Node type: ${nodeLabel(job)} (${job.node_detail ?? job.compute_type})` },
  ];

  if (job.status === "queued") {
    logs.push({ time: "T+00:00", level: "INFO", msg: "Job queued — waiting for coordinator..." });
  }

  if (job.n_points > 0) {
    logs.push({ time: "T+00:01", level: "INFO", msg: `Dataset loaded: ${datasetLabel(job)} (${job.n_points.toLocaleString()} rows)` });
    if (job.embedding_model) {
      logs.push({
        time: "T+00:01",
        level: job.embedding_fallback ? "WARN" : "INFO",
        msg: job.embedding_fallback
          ? `Embedding fallback: ${job.embedding_model} (${job.embedding_error ?? "sentence model unavailable"})`
          : `Embedding model: ${job.embedding_model} (${job.embedding_raw_dim}D -> ${job.embedding_cluster_dim}D)`,
      });
      if (job.embedding_cache_hit) {
        logs.push({ time: "T+00:01", level: "INFO", msg: "Embedding/vector cache hit — skipped preprocessing" });
      }
    }
  } else if (job.status === "vectorizing") {
    logs.push({ time: "T+00:01", level: "INFO", msg: "Embedding dataset with SentenceTransformers..." });
  }

  if (job.shard_sizes.length > 0) {
    logs.push({ time: "T+00:02", level: "INFO", msg: `Partitioning dataset into ${job.shard_sizes.length} shards...` });
    let rowStart = 0;
    job.shard_sizes.forEach((size, i) => {
      logs.push({ time: formatLogTime(2 + i * 0.2), level: "INFO", msg: `Shard ${i}: rows ${rowStart}-${rowStart + size - 1} → worker node-${i % job.workers_count}` });
      rowStart += size;
    });
  }

  if (job.status === "starting_flash") {
    logs.push({ time: "T+00:03", level: "INFO", msg: "Starting RunPod Flash workers..." });
  }

  if (job.workers_count > 0 && job.status !== "queued" && job.status !== "vectorizing") {
    logs.push({ time: "T+00:04", level: "INFO", msg: `Launching ${job.workers_count} ${nodeLabel(job)} workers...` });
    Array.from({ length: job.workers_count }, (_, i) => {
      logs.push({ time: formatLogTime(5 + i * 0.3), level: "INFO", msg: `Worker node-${i} online` });
    });
  }

  job.iterations_log.forEach((entry) => {
    logs.push({ time: formatLogTime(entry.elapsed_total), level: "INFO", msg: `Iteration ${entry.iter} — movement=${entry.movement.toFixed(6)}` });
    logs.push({ time: formatLogTime(entry.elapsed_total), level: "INFO", msg: `MAP: ${job.workers_count} workers computed local assignments in ${entry.elapsed_iter.toFixed(2)}s` });
    logs.push({ time: formatLogTime(entry.elapsed_total), level: "INFO", msg: `REDUCE: ${Object.entries(entry.cluster_sizes).map(([c, n]) => `C${c}=${n}`).join(", ")}` });
  });

  if (job.status === "done") {
    logs.push({ time: formatLogTime(job.elapsed), level: "INFO", msg: job.converged_at ? `Convergence reached — movement < 0.0001 at iteration ${job.converged_at}` : `Max iterations reached — ${job.iteration}/${job.max_iter}` });
    logs.push({ time: formatLogTime(job.elapsed), level: "INFO", msg: `Job complete — ${job.k} clusters, final movement=${job.movement?.toFixed(6) ?? "unknown"}` });
  }
  if (job.status === "error") {
    logs.push({ time: formatLogTime(job.elapsed), level: "ERROR", msg: job.error ?? "Training failed" });
  }
  return logs;
}

function buildGradientSyncLogs(job: JobStatusResponse): LogLine[] {
  const isLogistic = job.algorithm === "logistic_regression";
  const algoDesc = isLogistic ? "logistic regression (binary classification)" : "linear regression (MSE gradient descent)";
  const logs: LogLine[] = [
    { time: "T+00:00", level: "INFO", msg: `FlashML job initialized — job_id=${job.job_id} · architecture=gradient_sync` },
    { time: "T+00:00", level: "INFO", msg: `Algorithm: distributed ${algoDesc} · ${job.workers_count} ${nodeLabel(job)} workers` },
  ];

  if (job.status === "queued") {
    logs.push({ time: "T+00:00", level: "INFO", msg: "Job queued — loading sklearn example dataset..." });
    return logs;
  }

  if (job.n_points > 0) {
    logs.push({ time: "T+00:00", level: "INFO", msg: `Dataset loaded: ${datasetLabel(job)} (${job.n_points.toLocaleString()} rows)` });
  }

  if (job.shard_sizes.length > 0) {
    logs.push({ time: "T+00:01", level: "INFO", msg: `Partitioning training set into ${job.shard_sizes.length} shards` });
    job.shard_sizes.forEach((s, i) => {
      logs.push({ time: "T+00:01", level: "INFO", msg: `Shard ${i}: ${s} samples → node-${i}` });
    });
  }

  if (job.workers_count > 0) {
    logs.push({ time: "T+00:01", level: "INFO", msg: `Initializing ${job.weights?.length ? `${job.weights.length}-dimensional` : "zero"} weight vector` });
    Array.from({ length: job.workers_count }, (_, i) => {
      logs.push({ time: "T+00:02", level: "INFO", msg: `Worker node-${i} online — ready for gradient computation` });
    });
  }

  (job.epochs_log ?? []).forEach((entry) => {
    logs.push({ time: formatLogTime(entry.elapsed_total), level: "INFO", msg: `Epoch ${entry.epoch} — broadcasting weights to ${job.workers_count} workers` });
    logs.push({ time: formatLogTime(entry.elapsed_total), level: "INFO", msg: `Epoch ${entry.epoch} — workers computed local gradients (‖∇‖=${entry.gradient_norm.toFixed(4)})` });
    const valLabel = isLogistic ? "val_loss" : "val_mse";
    logs.push({ time: formatLogTime(entry.elapsed_total), level: "INFO", msg: `Epoch ${entry.epoch} — avg gradient applied, train_loss=${entry.loss.toFixed(4)}, ${valLabel}=${entry.val_mse.toFixed(4)}` });
  });

  if (job.status === "done") {
    if (isLogistic && job.val_accuracy != null) {
      logs.push({ time: formatLogTime(job.elapsed), level: "INFO", msg: `Training complete — train_acc=${job.train_accuracy?.toFixed(4) ?? "—"}, val_acc=${job.val_accuracy.toFixed(4)}` });
    } else {
      logs.push({ time: formatLogTime(job.elapsed), level: "INFO", msg: `Training complete — final loss=${job.training_loss?.toFixed(6) ?? "—"}, val_mse=${job.validation_mse?.toFixed(6) ?? "—"}` });
    }
    logs.push({ time: formatLogTime(job.elapsed), level: "INFO", msg: `Final weights: [${(job.weights ?? []).map((v) => v.toFixed(3)).join(", ")}]` });
  }
  if (job.status === "error") {
    logs.push({ time: formatLogTime(job.elapsed), level: "ERROR", msg: job.error ?? "Training failed" });
  }
  return logs;
}

function buildParallelSearchLogs(job: JobStatusResponse): LogLine[] {
  const isForest = job.algorithm === "random_forest";
  const algoHeader = isForest
    ? `Algorithm: Random Forest · ${job.n_trees_total ?? "?"} trees embarrassingly parallel`
    : `Algorithm: hyperparameter search · Ridge + PolynomialFeatures`;

  const logs: LogLine[] = [
    { time: "T+00:00", level: "INFO", msg: `FlashML job initialized — job_id=${job.job_id} · architecture=parallel_search` },
    { time: "T+00:00", level: "INFO", msg: algoHeader },
    { time: "T+00:00", level: "INFO", msg: `Node type: ${nodeLabel(job)} (${job.node_detail ?? job.compute_type})` },
  ];

  if (job.status === "queued") {
    logs.push({ time: "T+00:00", level: "INFO", msg: isForest ? "Job queued — loading sklearn breast cancer dataset..." : "Job queued — loading sklearn diabetes dataset..." });
    return logs;
  }

  if (job.n_points > 0) {
    logs.push({ time: "T+00:01", level: "INFO", msg: `Dataset loaded: ${datasetLabel(job)} (${job.n_points.toLocaleString()} rows)` });
  }

  if (isForest) {
    if (job.n_trees_total && job.n_trees_total > 0) {
      logs.push({ time: "T+00:01", level: "INFO", msg: `Spawning ${job.n_trees_total} tree futures in ThreadPoolExecutor` });
      logs.push({ time: "T+00:01", level: "INFO", msg: `Each tree: bootstrap sample → DecisionTreeClassifier(max_features="sqrt", max_depth=5) → OOB accuracy` });
    }
    if (job.workers_count > 0) {
      logs.push({ time: "T+00:02", level: "INFO", msg: `${job.workers_count} parallel workers executing tree training` });
    }

    (job.worker_results ?? []).forEach((r) => {
      logs.push({
        time: formatLogTime(job.elapsed),
        level: "INFO",
        msg: `${r.worker_id} — tree complete, OOB acc=${r.score.toFixed(4)} (${r.runtime_ms.toFixed(0)}ms)`,
      });
    });

    if (job.status === "done") {
      const nDone = job.n_trees_done ?? job.total_configs ?? 0;
      logs.push({ time: formatLogTime(job.elapsed), level: "INFO", msg: `All ${nDone} trees complete — aggregating majority vote` });
      if (job.val_accuracy != null) {
        logs.push({ time: formatLogTime(job.elapsed), level: "INFO", msg: `Ensemble accuracy — train=${job.train_accuracy?.toFixed(4) ?? "—"}, val=${job.val_accuracy.toFixed(4)}` });
      }
      logs.push({ time: formatLogTime(job.elapsed), level: "INFO", msg: `Forest training complete in ${job.elapsed.toFixed(2)}s` });
    }
  } else {
    if (job.total_configs && job.total_configs > 0) {
      logs.push({ time: "T+00:00", level: "INFO", msg: `Config space generated: ${job.total_configs} configs` });
    }
    if (job.worker_tasks?.length) {
      logs.push({ time: "T+00:01", level: "INFO", msg: `Assigning ${job.worker_tasks.reduce((sum, group) => sum + group.tasks.length, 0)} configs round-robin to ${job.workers_count} workers` });
    }

    (job.worker_tasks ?? []).forEach((group) => {
      logs.push({ time: "T+00:01", level: "INFO", msg: `${group.worker_id} assigned tasks: ${group.tasks.join(", ")}` });
    });

    if (job.workers_count > 0) {
      logs.push({ time: "T+00:02", level: "INFO", msg: `${job.workers_count} workers started independently` });
    }

    (job.worker_results ?? []).forEach((r) => {
      logs.push({
        time: formatLogTime(job.elapsed),
        level: "INFO",
        msg: `${r.worker_id} completed deg=${r.config.degree} α=${r.config.alpha} → R²=${r.score.toFixed(4)} (${r.runtime_ms.toFixed(0)}ms)`,
      });
    });

    if (job.status === "done") {
      const best = job.best_config;
      logs.push({ time: formatLogTime(job.elapsed), level: "INFO", msg: `All ${job.total_configs} configs evaluated` });
      logs.push({ time: formatLogTime(job.elapsed), level: "INFO", msg: `Best config: degree=${best?.degree ?? "—"}, alpha=${best?.alpha ?? "—"} → R²=${job.best_score?.toFixed(4) ?? "—"}` });
      logs.push({ time: formatLogTime(job.elapsed), level: "INFO", msg: `Leaderboard aggregated — search complete in ${job.elapsed}s` });
    }
  }

  if (job.status === "error") {
    logs.push({ time: formatLogTime(job.elapsed), level: "ERROR", msg: job.error ?? "Search failed" });
  }
  return logs;
}

function buildJobLogs(job?: JobStatusResponse): LogLine[] {
  if (!job) {
    return [{ time: "T+00:00", level: "INFO", msg: "Waiting for active FlashML job..." }];
  }
  const arch = job.architecture ?? "map_reduce";
  if (arch === "gradient_sync") return buildGradientSyncLogs(job);
  if (arch === "parallel_search") return buildParallelSearchLogs(job);
  return buildMapReduceLogs(job);
}

function streamingStatus(status: JobStatusResponse["status"]) {
  return status !== "done" && status !== "error";
}

export function TerminalPanel({ streaming = true, className, job }: TerminalPanelProps) {
  const logScrollRef = useRef<HTMLDivElement>(null);
  const logs = useMemo(() => buildJobLogs(job), [job]);

  useEffect(() => {
    const logScroll = logScrollRef.current;
    if (!logScroll) return;
    logScroll.scrollTo({ top: logScroll.scrollHeight, behavior: "smooth" });
  }, [logs.length]);

  return (
    <div className={cn("rounded-lg border border-border/60 bg-[oklch(0.06_0.01_240)] overflow-hidden", className)}>
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-border/50 bg-surface/50">
        <Terminal className="w-3.5 h-3.5 text-muted-foreground" weight="bold" />
        <span className="text-xs font-mono text-muted-foreground">
          flashml — job {job?.job_id ?? "pending"}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-red-500/60" />
          <span className="w-2 h-2 rounded-full bg-amber-500/60" />
          <span className="w-2 h-2 rounded-full bg-node-green/60" />
        </div>
      </div>
      <div ref={logScrollRef} className="h-64 overflow-y-auto p-4 space-y-0.5 terminal-text">
        {logs.map((log, i) => (
          <div key={i} className="flex gap-3">
            <span className="text-muted-foreground/50 shrink-0">{log.time}</span>
            <span className={cn("shrink-0 w-8", levelStyles[log.level] ?? "text-muted-foreground")}>{log.level}</span>
            <span className="text-foreground/80">{log.msg}</span>
          </div>
        ))}
        {streaming && (!job || streamingStatus(job.status)) && (
          <div className="flex gap-3">
            <span className="text-muted-foreground/50">{formatLogTime(job?.elapsed ?? 0)}</span>
            <span className="text-cyan/60 animate-pulse">▋</span>
          </div>
        )}
      </div>
    </div>
  );
}
