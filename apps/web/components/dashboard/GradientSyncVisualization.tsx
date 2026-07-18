"use client";

import { useEffect, useMemo, useRef, useState } from "react";
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
import {
  ArrowsClockwise,
  Broadcast,
  CaretLeft,
  CaretRight,
  ChartLine,
  Pause,
  Play,
} from "@phosphor-icons/react";
import { cn } from "@/lib/utils";
import type { EpochLog, GradientSyncSnapshot, GradientSyncWorkerSnap, JobStatusResponse } from "@/lib/api";

const PHASES = [
  "weights_initialized",
  "broadcast_weights",
  "workers_computing_gradients",
  "gradients_returned",
  "gradients_averaged",
  "weights_updated",
  "epoch_completed",
  "completed",
] as const;
type Phase = (typeof PHASES)[number];

const PHASE_LABEL: Record<Phase, string> = {
  weights_initialized: "Init Weights",
  broadcast_weights: "Broadcast",
  workers_computing_gradients: "Compute Grad",
  gradients_returned: "Grads Return",
  gradients_averaged: "Avg Grads",
  weights_updated: "Update Weights",
  epoch_completed: "Epoch Done",
  completed: "Complete",
};

type FlowNodeData = {
  title: string;
  subtitle: string;
  tone: "cyan" | "green" | "violet" | "amber";
  active: boolean;
  metrics?: { label: string; value: string }[];
};

function GradSyncNode({ data }: NodeProps<Node<FlowNodeData>>) {
  const toneClass = {
    cyan: data.active
      ? "border-cyan/70 bg-cyan/12 shadow-[0_0_28px_oklch(0.80_0.16_200_/_0.18)]"
      : "border-cyan/25 bg-cyan/5",
    green: data.active
      ? "border-node-green/70 bg-node-green/12 shadow-[0_0_28px_oklch(0.76_0.18_145_/_0.16)]"
      : "border-node-green/25 bg-node-green/5",
    violet: data.active
      ? "border-violet-400/70 bg-violet-400/12 shadow-[0_0_28px_oklch(0.65_0.20_290_/_0.18)]"
      : "border-violet-400/25 bg-violet-400/5",
    amber: data.active
      ? "border-amber-400/70 bg-amber-400/12 shadow-[0_0_28px_oklch(0.80_0.18_60_/_0.16)]"
      : "border-amber-400/25 bg-amber-400/5",
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
        <span className={cn("h-2 w-2 rounded-full", data.active ? `${dotColor} pulse-dot` : "bg-white/20")} />
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

function buildGradFlow(
  snapshot: GradientSyncSnapshot | null,
  currentPhase: string,
  workerCount: number,
  shardSizes: number[]
): { nodes: Node<FlowNodeData>[]; edges: Edge[] } {
  const phase = currentPhase as Phase;
  const n = Math.max(1, workerCount);
  const startY = Math.max(20, 160 - n * 36);
  const gapY = n > 1 ? 300 / (n - 1) : 0;

  const isBroadcast = phase === "broadcast_weights";
  const isComputing = phase === "workers_computing_gradients";
  const isGradsReturned = phase === "gradients_returned" || phase === "gradients_averaged";
  const isUpdating = phase === "weights_updated" || phase === "epoch_completed";
  const isDone = phase === "completed";

  const nodes: Node<FlowNodeData>[] = [
    {
      id: "param-server",
      type: "gradSyncNode",
      position: { x: 600, y: 140 },
      data: {
        title: "Parameter Server",
        subtitle: "gradient aggregator",
        tone: "violet",
        active: isGradsReturned || isUpdating || phase === "weights_initialized",
        metrics: [
          {
            label: "weights",
            value: snapshot ? `[${snapshot.weights.slice(0, 2).map((v) => v.toFixed(2)).join(", ")}…]` : "zeros",
          },
          {
            label: "grad norm",
            value: snapshot ? snapshot.gradient_norm.toFixed(4) : "—",
          },
        ],
      },
    },
    {
      id: "weights-state",
      type: "gradSyncNode",
      position: { x: 360, y: 430 },
      data: {
        title: "Global Weights",
        subtitle: snapshot ? `${snapshot.weights.length}-dim parameter vector` : "parameter vector",
        tone: "amber",
        active: isUpdating || isDone,
        metrics: [
          {
            label: "train loss",
            value: snapshot ? snapshot.loss.toFixed(4) : "—",
          },
          {
            label: "val mse",
            value: snapshot ? snapshot.val_mse.toFixed(4) : "—",
          },
        ],
      },
    },
  ];

  const workers: GradientSyncWorkerSnap[] = snapshot?.workers ?? Array.from({ length: n }, (_, i) => ({
    id: `node-${i}`,
    shardId: `shard-${i}`,
    status: "idle" as const,
    sampleCount: shardSizes[i] ?? 0,
    localLoss: null,
    gradientNorm: null,
  }));

  workers.forEach((w, i) => {
    const y = startY + i * gapY;
    nodes.push(
      {
        id: `shard-${i}`,
        type: "gradSyncNode",
        position: { x: 190, y },
        data: {
          title: `shard-${i}`,
          subtitle: "data partition",
          tone: "cyan",
          active: isBroadcast,
          metrics: [
            { label: "samples", value: (w.sampleCount || shardSizes[i] || 0).toLocaleString() },
            { label: "target", value: w.id },
          ],
        },
      },
      {
        id: w.id,
        type: "gradSyncNode",
        position: { x: 395, y },
        data: {
          title: w.id,
          subtitle: `RunPod Flash · ${isComputing ? "computing" : isGradsReturned ? "syncing" : "idle"}`,
          tone: "green",
          active: isComputing || isBroadcast,
          metrics: [
            { label: "local loss", value: w.localLoss != null ? w.localLoss.toFixed(4) : "waiting" },
            { label: "grad norm", value: w.gradientNorm != null ? w.gradientNorm.toFixed(4) : "—" },
          ],
        },
      }
    );
  });

  const arrow = { markerEnd: { type: MarkerType.ArrowClosed, color: "oklch(0.80 0.16 200 / 0.65)" } };
  const style = (active: boolean, tone: "cyan" | "violet" | "green" | "amber" = "cyan") => ({
    animated: active,
    style: {
      stroke: active
        ? tone === "violet" ? "oklch(0.65 0.20 290)" : tone === "green" ? "oklch(0.76 0.18 145)" : tone === "amber" ? "oklch(0.80 0.18 60)" : "oklch(0.80 0.16 200)"
        : "oklch(1 0 0 / 0.12)",
      strokeWidth: active ? 2 : 1,
    },
  });

  const edges: Edge[] = workers.flatMap((w, i) => [
    { id: `ds-sh${i}`, source: "param-server", target: `shard-${i}`, type: "smoothstep", ...arrow, ...style(false, "cyan") },
    { id: `sh${i}-w${i}`, source: `shard-${i}`, target: w.id, type: "smoothstep", ...arrow, ...style(isBroadcast, "cyan") },
    { id: `ps-w${i}`, source: "param-server", target: w.id, type: "smoothstep", ...arrow, ...style(isBroadcast, "violet") },
    { id: `w${i}-ps`, source: w.id, target: "param-server", type: "smoothstep", ...arrow, ...style(isGradsReturned, "green") },
  ]);
  edges.push({ id: "ps-wt", source: "param-server", target: "weights-state", type: "smoothstep", ...arrow, ...style(isUpdating, "amber") });

  return { nodes, edges };
}

function LossCurveCanvas({ epochsLog }: { epochsLog: EpochLog[] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const draw = () => {
      const w = canvas.offsetWidth;
      const h = canvas.offsetHeight;
      const ratio = window.devicePixelRatio || 1;
      if (canvas.width !== w * ratio || canvas.height !== h * ratio) {
        canvas.width = w * ratio;
        canvas.height = h * ratio;
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      }

      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "oklch(0.055 0.012 240)";
      ctx.fillRect(0, 0, w, h);

      ctx.strokeStyle = "oklch(1 0 0 / 0.04)";
      ctx.lineWidth = 1;
      for (let gx = 36; gx < w; gx += 48) { ctx.beginPath(); ctx.moveTo(gx, 0); ctx.lineTo(gx, h); ctx.stroke(); }
      for (let gy = 20; gy < h; gy += 32) { ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(w, gy); ctx.stroke(); }

      if (epochsLog.length < 2) {
        ctx.fillStyle = "oklch(1 0 0 / 0.20)";
        ctx.font = `11px monospace`;
        ctx.textAlign = "center";
        ctx.fillText("waiting for epoch data...", w / 2, h / 2);
        return;
      }

      const pad = 36;
      const maxLoss = Math.max(...epochsLog.map((e) => Math.max(e.loss, e.val_mse))) * 1.1;
      const minLoss = Math.max(0, Math.min(...epochsLog.map((e) => Math.min(e.loss, e.val_mse))) * 0.9);
      const epochs = epochsLog.length;

      const toX = (i: number) => pad + ((i) / (epochs - 1)) * (w - pad * 2);
      const toY = (v: number) => h - pad - ((v - minLoss) / (maxLoss - minLoss || 1)) * (h - pad * 2);

      const drawLine = (vals: number[], color: string) => {
        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5;
        ctx.lineJoin = "round";
        vals.forEach((v, i) => {
          if (i === 0) {
            ctx.moveTo(toX(i), toY(v));
          } else {
            ctx.lineTo(toX(i), toY(v));
          }
        });
        ctx.stroke();

        vals.forEach((v, i) => {
          ctx.beginPath();
          ctx.fillStyle = color;
          ctx.arc(toX(i), toY(v), 2.5, 0, Math.PI * 2);
          ctx.fill();
        });
      };

      drawLine(epochsLog.map((e) => e.loss), "oklch(0.65 0.20 290)");
      drawLine(epochsLog.map((e) => e.val_mse), "oklch(0.80 0.18 60)");

      ctx.fillStyle = "oklch(1 0 0 / 0.35)";
      ctx.font = "9px monospace";
      ctx.textAlign = "left";
      ctx.fillText(`epoch 1`, pad, h - 8);
      ctx.textAlign = "right";
      ctx.fillText(`epoch ${epochs}`, w - pad, h - 8);
      ctx.textAlign = "left";
      ctx.fillStyle = "oklch(0.65 0.20 290)";
      ctx.fillText("train loss", pad, 14);
      ctx.fillStyle = "oklch(0.80 0.18 60)";
      ctx.fillText("val mse", pad + 64, 14);
    };

    draw();
  }, [epochsLog]);

  return (
    <div className="rounded-lg border border-border/50 bg-[oklch(0.06_0.012_240)] p-3">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <div className="text-xs font-mono uppercase tracking-wider text-muted-foreground">Loss curve</div>
          <div className="mt-0.5 text-[10px] font-mono text-muted-foreground/70">
            training loss (violet) · validation MSE (amber) per epoch
          </div>
        </div>
        <ChartLine className="h-4 w-4 text-violet-400" weight="duotone" />
      </div>
      <div className="aspect-[1.8/1] overflow-hidden rounded-md border border-border/30">
        <canvas ref={canvasRef} className="h-full w-full" />
      </div>
    </div>
  );
}

export function GradientSyncVisualization({ job }: { job: JobStatusResponse }) {
  const snapshots: GradientSyncSnapshot[] = job.training_snapshots ?? [];
  const epochsLog: EpochLog[] = job.epochs_log ?? [];

  const [selectedIdx, setSelectedIdx] = useState(() => Math.max(0, snapshots.length - 1));
  const [isPlaying, setIsPlaying] = useState(false);

  useEffect(() => {
    if (isPlaying) return;
    const timer = window.setTimeout(() => {
      setSelectedIdx(Math.max(0, snapshots.length - 1));
    }, 0);
    return () => window.clearTimeout(timer);
  }, [snapshots.length, isPlaying]);

  useEffect(() => {
    if (!isPlaying) return;
    if (selectedIdx >= snapshots.length - 1 && job.status === "done") {
      const timer = window.setTimeout(() => {
        setIsPlaying(false);
      }, 0);
      return () => window.clearTimeout(timer);
    }
    const t = setTimeout(() => {
      setSelectedIdx((prev) => Math.min(prev + 1, Math.max(0, snapshots.length - 1)));
    }, 900);
    return () => clearTimeout(t);
  }, [isPlaying, selectedIdx, snapshots.length, job.status]);

  const safeIdx = Math.min(selectedIdx, Math.max(0, snapshots.length - 1));
  const snapshot = snapshots[safeIdx] ?? null;
  const currentPhase = snapshot ? snapshot.phase : (job.current_phase ?? "weights_initialized");

  const { nodes, edges } = useMemo(
    () => buildGradFlow(snapshot, currentPhase, job.workers_count, job.shard_sizes),
    [snapshot, currentPhase, job.workers_count, job.shard_sizes]
  );

  const lossForDisplay = epochsLog.slice(0, safeIdx + 1);

  const maxEpochs = job.max_epochs ?? job.max_iter;
  const currentEpoch = snapshot?.epoch ?? job.epoch ?? 0;

  const phaseIndex = PHASES.indexOf(currentPhase as Phase);
  const getPhaseStatus = (p: Phase) => {
    const pi = PHASES.indexOf(p);
    if (currentPhase === "completed") return "done";
    if (pi < phaseIndex) return "done";
    if (pi === phaseIndex) return "active";
    return "pending";
  };

  return (
    <section className="rounded-xl border border-violet-400/15 bg-surface p-4 shadow-[0_0_80px_oklch(0.65_0.20_290_/_0.05)] sm:p-5">
      <div className="mb-5 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2">
            <ChartLine className="h-4 w-4 text-violet-400" weight="duotone" />
            <h2 className="text-base font-semibold text-foreground">Gradient Sync training visualization</h2>
          </div>
          <p className="max-w-2xl text-xs font-mono leading-relaxed text-muted-foreground">
            Distributed linear regression: workers compute local gradients on their shards, the parameter
            server averages them and broadcasts updated weights each epoch.
          </p>
        </div>
        <div className="grid grid-cols-3 gap-2 text-right font-mono">
          <div className="rounded-md border border-border/50 bg-surface-elevated px-3 py-2">
            <div className="text-[9px] uppercase text-muted-foreground">epoch</div>
            <div className="text-sm font-bold text-violet-400 metric-value">{currentEpoch}/{maxEpochs}</div>
          </div>
          <div className="rounded-md border border-border/50 bg-surface-elevated px-3 py-2">
            <div className="text-[9px] uppercase text-muted-foreground">train loss</div>
            <div className="text-sm font-bold text-node-green metric-value">
              {snapshot ? snapshot.loss.toFixed(4) : "—"}
            </div>
          </div>
          <div className="rounded-md border border-border/50 bg-surface-elevated px-3 py-2">
            <div className="text-[9px] uppercase text-muted-foreground">val mse</div>
            <div className="text-sm font-bold text-amber-400 metric-value">
              {snapshot ? snapshot.val_mse.toFixed(4) : "—"}
            </div>
          </div>
        </div>
      </div>

      {/* Phase breadcrumb */}
      <div className="mb-5 grid grid-cols-2 gap-2 md:grid-cols-4 lg:grid-cols-8">
        {PHASES.map((phase) => {
          const status = getPhaseStatus(phase);
          return (
            <div
              key={phase}
              className={cn(
                "rounded-md border px-2.5 py-2 transition-all",
                status === "active"
                  ? "border-violet-400/60 bg-violet-400/10 text-violet-400 shadow-[0_0_20px_oklch(0.65_0.20_290_/_0.12)]"
                  : status === "done"
                  ? "border-node-green/25 bg-node-green/5 text-node-green"
                  : "border-border/40 bg-white/[0.015] text-muted-foreground"
              )}
            >
              <div className="flex items-center gap-2">
                <span className={cn(
                  "h-1.5 w-1.5 rounded-full",
                  status === "active" ? "bg-violet-400 pulse-dot" : status === "done" ? "bg-node-green" : "bg-white/20"
                )} />
                <span className="text-[10px] font-mono uppercase tracking-wider">{PHASE_LABEL[phase]}</span>
              </div>
            </div>
          );
        })}
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
                violet → workers = weight broadcast · green → param server = gradient return
              </div>
            </div>
            <Broadcast className="h-4 w-4 text-violet-400/70" weight="duotone" />
          </div>
          <div className="flashml-flow h-[480px] overflow-hidden rounded-md border border-border/30 bg-background/80">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={{ gradSyncNode: GradSyncNode }}
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
          <LossCurveCanvas epochsLog={lossForDisplay} />

          {/* Worker cards */}
          <div className="rounded-lg border border-border/50 bg-surface-elevated p-4">
            <div className="mb-3 text-xs font-mono uppercase tracking-wider text-muted-foreground">
              Worker gradient stats
            </div>
            <div className="grid grid-cols-1 gap-2">
              {(snapshot?.workers ?? Array.from({ length: job.workers_count }, (_, i) => ({
                id: `node-${i}`, shardId: `shard-${i}`, status: "idle" as const,
                sampleCount: job.shard_sizes[i] ?? 0, localLoss: null, gradientNorm: null,
              }))).map((w) => (
                <div key={w.id} className="rounded-md border border-white/10 bg-white/[0.025] p-3">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="text-xs font-semibold text-foreground">{w.id}</span>
                    <span className={cn(
                      "rounded border px-1.5 py-0.5 text-[9px] font-mono uppercase",
                      w.status === "done" ? "border-node-green/30 text-node-green" : "border-border text-muted-foreground"
                    )}>
                      {w.status}
                    </span>
                  </div>
                  <div className="space-y-1 text-[10px] font-mono">
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">shard</span>
                      <span className="text-violet-400">{w.shardId}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">samples</span>
                      <span className="text-foreground/80">{w.sampleCount.toLocaleString()}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">local loss</span>
                      <span className="text-amber-400">{w.localLoss != null ? w.localLoss.toFixed(4) : "—"}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">grad norm</span>
                      <span className="text-node-green">{w.gradientNorm != null ? w.gradientNorm.toFixed(4) : "—"}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Playback controls */}
      <div className="mt-5 rounded-lg border border-border/50 bg-background/50 p-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => {
                if (isPlaying) { setIsPlaying(false); return; }
                if (safeIdx >= snapshots.length - 1) setSelectedIdx(0);
                setIsPlaying(true);
              }}
              className="inline-flex h-9 items-center gap-2 rounded-md bg-violet-400 px-3 text-sm font-semibold text-background transition-all hover:bg-violet-400/90 active:scale-[0.98]"
            >
              {isPlaying ? <Pause className="h-4 w-4" weight="bold" /> : <Play className="h-4 w-4" weight="bold" />}
              {isPlaying ? "Pause" : "Play"}
            </button>
            <button
              type="button"
              onClick={() => { setSelectedIdx(Math.max(0, safeIdx - 1)); setIsPlaying(false); }}
              disabled={safeIdx === 0}
              className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border/60 px-3 text-sm text-foreground transition-all hover:border-violet-400/40 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <CaretLeft className="h-4 w-4" weight="bold" /> Previous
            </button>
            <button
              type="button"
              onClick={() => { setSelectedIdx(Math.min(safeIdx + 1, snapshots.length - 1)); setIsPlaying(false); }}
              disabled={safeIdx >= snapshots.length - 1}
              className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border/60 px-3 text-sm text-foreground transition-all hover:border-violet-400/40 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Next <CaretRight className="h-4 w-4" weight="bold" />
            </button>
            <button
              type="button"
              onClick={() => { setSelectedIdx(0); setIsPlaying(false); }}
              className="inline-flex h-9 items-center justify-center rounded-md border border-border/60 px-3 text-sm text-muted-foreground transition-all hover:border-violet-400/40 hover:text-foreground"
            >
              <ArrowsClockwise className="h-4 w-4" weight="bold" />
            </button>
          </div>

          <div className="min-w-0 flex-1">
            <div className="mb-1 flex items-center justify-between text-[10px] font-mono text-muted-foreground">
              <span>Epoch 1</span>
              <span>{snapshots.length > 0 ? `${snapshots.length} / ${maxEpochs} available` : "waiting..."}</span>
            </div>
            <input
              type="range"
              min={0}
              max={Math.max(0, snapshots.length - 1)}
              value={safeIdx}
              onChange={(e) => { setSelectedIdx(Number(e.target.value)); setIsPlaying(false); }}
              className="h-2 w-full accent-violet-400"
            />
          </div>

          <div className="min-w-[160px] rounded-md border border-border/50 bg-surface px-3 py-2 text-xs font-mono">
            <div className="flex justify-between gap-3">
              <span className="text-muted-foreground">phase</span>
              <span className="text-violet-400">{currentPhase}</span>
            </div>
            <div className="mt-1 flex justify-between gap-3">
              <span className="text-muted-foreground">workers</span>
              <span className="text-node-green">{job.workers_count} online</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
