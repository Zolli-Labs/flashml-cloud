"use client";

import { useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { MagnifyingGlass, Trophy } from "@phosphor-icons/react";
import { cn } from "@/lib/utils";
import type { JobStatusResponse, LeaderboardEntry, WorkerTaskResult } from "@/lib/api";

type FlowNodeData = {
  title: string;
  subtitle: string;
  tone: "cyan" | "green" | "violet" | "amber";
  active: boolean;
  badge?: string;
  metrics?: { label: string; value: string }[];
};

function ParallelNode({ data }: NodeProps<Node<FlowNodeData>>) {
  const toneClass = {
    cyan: data.active ? "border-cyan/70 bg-cyan/12 shadow-[0_0_28px_oklch(0.80_0.16_200_/_0.18)]" : "border-cyan/25 bg-cyan/5",
    green: data.active ? "border-node-green/70 bg-node-green/12 shadow-[0_0_28px_oklch(0.76_0.18_145_/_0.16)]" : "border-node-green/25 bg-node-green/5",
    violet: data.active ? "border-violet-400/70 bg-violet-400/12 shadow-[0_0_28px_oklch(0.65_0.20_290_/_0.18)]" : "border-violet-400/25 bg-violet-400/5",
    amber: data.active ? "border-amber-400/70 bg-amber-400/12 shadow-[0_0_28px_oklch(0.80_0.18_60_/_0.16)]" : "border-amber-400/25 bg-amber-400/5",
  }[data.tone];

  const dotColor = { cyan: "bg-cyan", green: "bg-node-green", violet: "bg-violet-400", amber: "bg-amber-400" }[data.tone];

  return (
    <div className={cn("min-w-[148px] rounded-lg border px-3 py-2.5 transition-all", toneClass)}>
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-0 !bg-cyan/70" />
      <Handle type="target" position={Position.Top} className="!h-2 !w-2 !border-0 !bg-cyan/70" />
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-[12px] font-semibold text-foreground">{data.title}</div>
          <div className="mt-0.5 truncate text-[9px] font-mono text-muted-foreground">{data.subtitle}</div>
        </div>
        <div className="flex items-center gap-1">
          {data.badge && (
            <span className="rounded border border-node-green/30 bg-node-green/10 px-1 text-[8px] font-mono text-node-green">
              {data.badge}
            </span>
          )}
          <span className={cn("h-2 w-2 rounded-full", data.active ? `${dotColor} pulse-dot` : "bg-white/20")} />
        </div>
      </div>
      {data.metrics && (
        <div className="mt-2 grid grid-cols-2 gap-1.5 border-t border-white/10 pt-2">
          {data.metrics.map((m) => (
            <div key={m.label}>
              <div className="text-[8px] font-mono uppercase text-muted-foreground/70">{m.label}</div>
              <div className="truncate text-[10px] font-mono text-foreground/80">{m.value}</div>
            </div>
          ))}
        </div>
      )}
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-0 !bg-cyan/70" />
      <Handle type="source" position={Position.Bottom} className="!h-2 !w-2 !border-0 !bg-cyan/70" />
    </div>
  );
}

function buildParallelFlow(job: JobStatusResponse): { nodes: Node<FlowNodeData>[]; edges: Edge[] } {
  const n = Math.max(1, job.workers_count);
  const startY = Math.max(10, 160 - n * 40);
  const gapY = n > 1 ? 300 / (n - 1) : 0;
  const centerY = startY + ((n - 1) * gapY) / 2;

  const completed = job.completed_configs ?? 0;
  const total = job.total_configs ?? 0;
  const isDone = job.status === "done";

  const workerResults: WorkerTaskResult[] = job.worker_results ?? [];
  const workerDoneMap: Record<string, number> = {};
  workerResults.forEach((r) => {
    workerDoneMap[r.worker_id] = (workerDoneMap[r.worker_id] ?? 0) + 1;
  });

  const nodes: Node<FlowNodeData>[] = [
    {
      id: "config-space",
      type: "parallelNode",
      position: { x: 0, y: centerY - 30 },
      data: {
        title: "Config Space",
        subtitle: "degree × alpha = 9 configs",
        tone: "cyan",
        active: completed < total,
        metrics: [
          { label: "total", value: String(total || 9) },
          { label: "done", value: String(completed) },
        ],
      },
    },
    {
      id: "job-queue",
      type: "parallelNode",
      position: { x: 195, y: centerY - 30 },
      data: {
        title: "Job Queue",
        subtitle: "round-robin assignment",
        tone: "cyan",
        active: completed < total && completed === 0,
        metrics: [
          { label: "queued", value: String(Math.max(0, (total || 9) - completed)) },
          { label: "running", value: job.status === "running" ? String(n) : "0" },
        ],
      },
    },
    {
      id: "aggregator",
      type: "parallelNode",
      position: { x: 590, y: centerY - 30 },
      data: {
        title: "Aggregator",
        subtitle: "collects independent results",
        tone: "violet",
        active: completed > 0,
        metrics: [
          { label: "collected", value: String(completed) },
          { label: "best R²", value: job.best_score != null ? job.best_score.toFixed(4) : "—" },
        ],
      },
    },
  ];

  for (let i = 0; i < n; i++) {
    const y = startY + i * gapY;
    const doneCount = workerDoneMap[`node-${i}`] ?? 0;
    const isActive = job.status === "running" && doneCount < 3;
    nodes.push({
      id: `worker-${i}`,
      type: "parallelNode",
      position: { x: 395, y },
      data: {
        title: `node-${i}`,
        subtitle: `RunPod Flash · ${isActive ? "running" : doneCount > 0 ? "done" : "idle"}`,
        tone: "green",
        active: isActive,
        badge: doneCount > 0 ? `${doneCount}/3` : undefined,
        metrics: [
          { label: "configs", value: `${doneCount}/3` },
          {
            label: "best score",
            value:
              workerResults
                .filter((r) => r.worker_id === `node-${i}`)
                .reduce((best, r) => Math.max(best, r.score), -Infinity) > -Infinity
                ? workerResults
                    .filter((r) => r.worker_id === `node-${i}`)
                    .reduce((best, r) => Math.max(best, r.score), -Infinity)
                    .toFixed(4)
                : "—",
          },
        ],
      },
    });
  }

  const arrow = { markerEnd: { type: MarkerType.ArrowClosed, color: "oklch(0.80 0.16 200 / 0.65)" } };
  const styleFor = (active: boolean, tone: "cyan" | "green" | "violet" = "cyan") => ({
    animated: active,
    style: {
      stroke: active
        ? tone === "violet" ? "oklch(0.65 0.20 290)" : tone === "green" ? "oklch(0.76 0.18 145)" : "oklch(0.80 0.16 200)"
        : "oklch(1 0 0 / 0.12)",
      strokeWidth: active ? 2 : 1,
    },
  });

  const edges: Edge[] = [
    { id: "cs-jq", source: "config-space", target: "job-queue", type: "smoothstep", ...arrow, ...styleFor(completed === 0 && job.status === "running", "cyan") },
    ...Array.from({ length: n }, (_, i) => ([
      { id: `jq-w${i}`, source: "job-queue", target: `worker-${i}`, type: "smoothstep", ...arrow, ...styleFor(isDone || job.status === "running", "cyan") },
      { id: `w${i}-agg`, source: `worker-${i}`, target: "aggregator", type: "smoothstep", ...arrow, ...styleFor((workerDoneMap[`node-${i}`] ?? 0) > 0, "green") },
    ])).flat(),
  ];

  return { nodes, edges };
}

function LeaderboardTable({ leaderboard }: { leaderboard: LeaderboardEntry[] }) {
  if (leaderboard.length === 0) {
    return (
      <div className="flex h-32 items-center justify-center rounded-lg border border-border/40 bg-surface-elevated text-xs font-mono text-muted-foreground">
        waiting for results...
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border/50 bg-surface-elevated overflow-hidden">
      <div className="grid grid-cols-[32px_1fr_80px_80px_72px] border-b border-border/40 bg-white/[0.02] px-3 py-2 text-[9px] font-mono uppercase text-muted-foreground/70">
        <span>#</span>
        <span>Config</span>
        <span className="text-right">R² Score</span>
        <span className="text-right">Runtime</span>
        <span className="text-right">Worker</span>
      </div>
      {leaderboard.map((entry, i) => (
        <div
          key={entry.task_id}
          className={cn(
            "grid grid-cols-[32px_1fr_80px_80px_72px] border-b border-border/20 px-3 py-2 text-[11px] font-mono last:border-b-0",
            i === 0 ? "bg-node-green/5" : "bg-transparent"
          )}
        >
          <span className={cn("font-bold", i === 0 ? "text-node-green" : i === 1 ? "text-amber-400" : i === 2 ? "text-cyan/70" : "text-muted-foreground/40")}>
            {i === 0 ? "1" : i + 1}
          </span>
          <span className="text-foreground/80 truncate">
            deg={entry.config.degree} α={entry.config.alpha}
          </span>
          <span className={cn("text-right font-semibold", i === 0 ? "text-node-green" : "text-foreground/70")}>
            {entry.score.toFixed(4)}
          </span>
          <span className="text-right text-muted-foreground">{entry.runtime_ms.toFixed(0)}ms</span>
          <span className="text-right text-muted-foreground">{entry.worker_id}</span>
        </div>
      ))}
    </div>
  );
}

export function ParallelSearchVisualization({ job }: { job: JobStatusResponse }) {
  const leaderboard: LeaderboardEntry[] = job.leaderboard ?? [];
  const workerResults: WorkerTaskResult[] = job.worker_results ?? [];

  const { nodes, edges } = useMemo(() => buildParallelFlow(job), [job]);

  const workerIds = Array.from({ length: job.workers_count }, (_, i) => `node-${i}`);

  const completed = job.completed_configs ?? 0;
  const total = job.total_configs ?? 0;

  return (
    <section className="rounded-xl border border-node-green/15 bg-surface p-4 shadow-[0_0_80px_oklch(0.76_0.18_145_/_0.05)] sm:p-5">
      <div className="mb-5 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2">
            <MagnifyingGlass className="h-4 w-4 text-node-green" weight="duotone" />
            <h2 className="text-base font-semibold text-foreground">Embarrassingly Parallel search</h2>
          </div>
          <p className="max-w-2xl text-xs font-mono leading-relaxed text-muted-foreground">
            Independent hyperparameter search: each worker tests polynomial regression configs (degree × Ridge alpha)
            in isolation. No coordination until final aggregation — pure embarrassingly parallel execution.
          </p>
        </div>
        <div className="grid grid-cols-3 gap-2 text-right font-mono">
          <div className="rounded-md border border-border/50 bg-surface-elevated px-3 py-2">
            <div className="text-[9px] uppercase text-muted-foreground">completed</div>
            <div className="text-sm font-bold text-node-green metric-value">
              {total > 0 ? `${completed}/${total}` : "—"}
            </div>
          </div>
          <div className="rounded-md border border-border/50 bg-surface-elevated px-3 py-2">
            <div className="text-[9px] uppercase text-muted-foreground">best R²</div>
            <div className="text-sm font-bold text-amber-400 metric-value">
              {job.best_score != null ? job.best_score.toFixed(4) : "—"}
            </div>
          </div>
          <div className="rounded-md border border-border/50 bg-surface-elevated px-3 py-2">
            <div className="text-[9px] uppercase text-muted-foreground">best config</div>
            <div className="text-sm font-bold text-cyan metric-value truncate">
              {job.best_config ? `d=${job.best_config.degree} α=${job.best_config.alpha}` : "—"}
            </div>
          </div>
        </div>
      </div>

      {/* Progress bar */}
      <div className="mb-5 rounded-lg border border-border/40 bg-surface p-3">
        <div className="mb-2 flex items-center justify-between text-[10px] font-mono text-muted-foreground">
          <span>Search progress</span>
          <span>{total > 0 ? Math.round((completed / total) * 100) : 0}%</span>
        </div>
        <div className="h-1.5 w-full rounded-full bg-white/5">
          <div
            className="h-full rounded-full bg-gradient-to-r from-node-green/80 to-node-green transition-all duration-500"
            style={{ width: `${total > 0 ? (completed / total) * 100 : 0}%`, boxShadow: "0 0 10px oklch(0.76 0.18 145 / 0.50)" }}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(340px,0.8fr)]">
        {/* React Flow */}
        <div className="rounded-lg border border-border/50 bg-[oklch(0.055_0.012_240)] p-3">
          <div className="mb-3 flex items-center justify-between">
            <div>
              <div className="text-xs font-mono uppercase tracking-wider text-muted-foreground">
                React Flow infrastructure graph
              </div>
              <div className="mt-0.5 text-[10px] font-mono text-muted-foreground/70">
                workers are fully independent — no inter-worker communication needed
              </div>
            </div>
            <MagnifyingGlass className="h-4 w-4 text-node-green/70" weight="duotone" />
          </div>
          <div className="flashml-flow h-[440px] overflow-hidden rounded-md border border-border/30 bg-background/80">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={{ parallelNode: ParallelNode }}
              fitView
              fitViewOptions={{ padding: 0.14 }}
              minZoom={0.4}
              maxZoom={1.4}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable={false}
              proOptions={{ hideAttribution: true }}
            >
              <Background color="oklch(1 0 0 / 0.10)" gap={28} />
              <Controls showInteractive={false} position="bottom-right" />
            </ReactFlow>
          </div>
        </div>

        <div className="space-y-4">
          {/* Leaderboard */}
          <div className="rounded-lg border border-border/50 bg-[oklch(0.06_0.012_240)] p-3">
            <div className="mb-3 flex items-center justify-between">
              <div>
                <div className="text-xs font-mono uppercase tracking-wider text-muted-foreground">
                  Leaderboard
                </div>
                <div className="mt-0.5 text-[10px] font-mono text-muted-foreground/70">
                  ranked by R² on validation set — updates as results arrive
                </div>
              </div>
              <Trophy className="h-4 w-4 text-amber-400" weight="duotone" />
            </div>
            <LeaderboardTable leaderboard={leaderboard} />
          </div>

          {/* Worker task cards */}
          <div className="rounded-lg border border-border/50 bg-surface-elevated p-4">
            <div className="mb-3 text-xs font-mono uppercase tracking-wider text-muted-foreground">
              Worker task status
            </div>
            <div className="grid grid-cols-1 gap-2">
              {workerIds.map((wid) => {
                const doneResults = workerResults.filter((r) => r.worker_id === wid);
                const isDoneWorker = doneResults.length >= 3;
                const isRunning = !isDoneWorker && job.status === "running";
                return (
                  <div key={wid} className="rounded-md border border-white/10 bg-white/[0.025] p-3">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold text-foreground">{wid}</span>
                      <span className={cn(
                        "rounded border px-1.5 py-0.5 text-[9px] font-mono uppercase",
                        isDoneWorker ? "border-node-green/30 text-node-green" : isRunning ? "border-amber-400/30 text-amber-400" : "border-border text-muted-foreground"
                      )}>
                        {isDoneWorker ? "done" : isRunning ? "running" : "idle"}
                      </span>
                    </div>
                    <div className="space-y-1">
                      {doneResults.map((r) => (
                        <div key={r.task_id} className="flex items-center justify-between text-[10px] font-mono">
                          <span className="text-muted-foreground">deg={r.config.degree} α={r.config.alpha}</span>
                          <span className="text-node-green">R²={r.score.toFixed(4)}</span>
                        </div>
                      ))}
                      {Array.from({ length: 3 - doneResults.length }, (_, i) => (
                        <div key={`pending-${i}`} className="flex items-center justify-between text-[10px] font-mono">
                          <span className="text-muted-foreground/40">task pending...</span>
                          <span className="text-muted-foreground/30">—</span>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
