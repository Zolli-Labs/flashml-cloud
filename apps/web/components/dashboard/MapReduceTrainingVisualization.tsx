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
  Funnel,
  Pause,
  Play,
  SquaresFour,
} from "@phosphor-icons/react";
import { cn } from "@/lib/utils";
import type {
  JobStatusResponse,
  TrainingIteration,
  TrainingPhase,
} from "@/lib/api";

const PHASES: TrainingPhase[] = [
  "upload",
  "partition",
  "map",
  "shuffle",
  "reduce",
  "aggregate",
  "broadcast",
  "complete",
];

const PHASE_LABEL: Record<TrainingPhase, string> = {
  upload: "Upload",
  partition: "Partition",
  map: "Map",
  shuffle: "Shuffle",
  reduce: "Reduce",
  aggregate: "Aggregate",
  broadcast: "Broadcast",
  complete: "Complete",
};

const CLUSTER_COLORS = [
  "oklch(0.80 0.16 200)",
  "oklch(0.65 0.20 290)",
  "oklch(0.76 0.18 145)",
  "oklch(0.80 0.18 60)",
  "oklch(0.70 0.20 20)",
  "oklch(0.75 0.15 320)",
];

type FlowNodeData = {
  title: string;
  subtitle: string;
  kind: "dataset" | "shard" | "worker" | "reducer" | "model";
  active: boolean;
  tone: "cyan" | "green" | "violet";
  metrics?: { label: string; value: string }[];
};

function phaseForLiveJob(job: JobStatusResponse): TrainingPhase {
  if (job.status === "queued" || job.status === "vectorizing") return "upload";
  if (job.status === "starting_flash") return "partition";
  if (job.status === "running") return "map";
  if (job.status === "done") return "complete";
  return "complete";
}

function getPhaseStatus(phase: TrainingPhase, activePhase: TrainingPhase) {
  const phaseIndex = PHASES.indexOf(phase);
  const activeIndex = PHASES.indexOf(activePhase);
  if (phaseIndex < activeIndex || activePhase === "complete") return "done";
  if (phaseIndex === activeIndex) return "active";
  return "pending";
}

function formatPhase(phase: TrainingPhase) {
  return PHASE_LABEL[phase] ?? phase;
}

function coerceTrainingPhase(phase: string | undefined, fallback: TrainingPhase): TrainingPhase {
  return PHASES.includes(phase as TrainingPhase) ? (phase as TrainingPhase) : fallback;
}

function workerStatusForPhase(
  phase: TrainingPhase,
  storedStatus: TrainingIteration["workers"][number]["status"]
) {
  if (phase === "map") return "mapping";
  if (phase === "shuffle" || phase === "reduce" || phase === "aggregate") return "waiting";
  if (phase === "broadcast" || phase === "complete") return "done";
  if (phase === "upload" || phase === "partition") return "idle";
  return storedStatus;
}

function statusForLivePhase(phase: TrainingPhase): TrainingIteration["workers"][number]["status"] {
  if (phase === "map") return "mapping";
  if (phase === "shuffle" || phase === "reduce" || phase === "aggregate") return "waiting";
  if (phase === "broadcast" || phase === "complete") return "done";
  return "idle";
}

function finiteNumber(value: unknown, fallback = 0) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function buildLiveScaffoldIteration(job: JobStatusResponse): TrainingIteration {
  const phase = coerceTrainingPhase(job.current_phase, phaseForLiveJob(job));
  const workerCount = Math.max(1, job.workers_count || job.shard_sizes.length || 1);
  const knownSamples = job.n_points || job.shard_sizes.reduce((sum, size) => sum + size, 0);
  const fallbackShardSize = knownSamples > 0 ? Math.ceil(knownSamples / workerCount) : 0;
  const shardSizes =
    job.shard_sizes.length > 0
      ? job.shard_sizes
      : Array.from({ length: workerCount }, () => fallbackShardSize);
  const zeroCounts = Object.fromEntries(
    Array.from({ length: Math.max(1, job.k) }, (_, cluster) => [String(cluster), 0])
  );

  return {
    iteration: Math.max(0, job.iteration),
    phase,
    movement: job.movement ?? 0,
    inertia: job.iterations_log.at(-1)?.inertia ?? 0,
    workers: Array.from({ length: workerCount }, (_, index) => ({
      id: `node-${index}`,
      shardId: `shard-${index}`,
      status: statusForLivePhase(phase),
      sampleCount: shardSizes[index] ?? fallbackShardSize,
      localInertia: undefined,
      clusterCounts: { ...zeroCounts },
    })),
    points2d: [],
    centroids2d: [],
  };
}

function normalizeTrainingIteration(
  job: JobStatusResponse,
  iteration: TrainingIteration | undefined,
  fallback?: TrainingIteration
): TrainingIteration {
  const scaffold = fallback ?? buildLiveScaffoldIteration(job);
  const source = iteration ?? scaffold;
  const workerCount = Math.max(1, job.workers_count || scaffold.workers.length || 1);
  const zeroCounts = Object.fromEntries(
    Array.from({ length: Math.max(1, job.k) }, (_, cluster) => [String(cluster), 0])
  );
  const sourceWorkers = Array.isArray(source.workers) && source.workers.length > 0
    ? source.workers
    : scaffold.workers;

  const workers = Array.from({ length: Math.max(workerCount, sourceWorkers.length) }, (_, index) => {
    const worker = sourceWorkers[index] ?? scaffold.workers[index] ?? {
      id: `node-${index}`,
      shardId: `shard-${index}`,
      status: statusForLivePhase(coerceTrainingPhase(source.phase, phaseForLiveJob(job))),
      sampleCount: job.shard_sizes[index] ?? 0,
      clusterCounts: zeroCounts,
    };
    return {
      id: worker.id || `node-${index}`,
      shardId: worker.shardId || `shard-${index}`,
      status: worker.status ?? "idle",
      sampleCount: finiteNumber(worker.sampleCount, job.shard_sizes[index] ?? 0),
      localInertia: worker.localInertia == null ? undefined : finiteNumber(worker.localInertia),
      clusterCounts: { ...zeroCounts, ...(worker.clusterCounts ?? {}) },
    };
  });

  return {
    iteration: Math.max(0, finiteNumber(source.iteration, job.iteration ?? 0)),
    phase: coerceTrainingPhase(source.phase, coerceTrainingPhase(job.current_phase, phaseForLiveJob(job))),
    movement: finiteNumber(source.movement, job.movement ?? 0),
    inertia: finiteNumber(source.inertia, job.iterations_log.at(-1)?.inertia ?? 0),
    workers,
    points2d: Array.isArray(source.points2d)
      ? source.points2d.filter((point) =>
          Number.isFinite(point.x) &&
          Number.isFinite(point.y) &&
          Number.isFinite(point.cluster)
        )
      : [],
    centroids2d: Array.isArray(source.centroids2d)
      ? source.centroids2d.filter((point) =>
          Number.isFinite(point.x) &&
          Number.isFinite(point.y) &&
          Number.isFinite(point.cluster)
        )
      : [],
  };
}

function buildLiveTrainingIterations(
  job: JobStatusResponse,
  realIterations: TrainingIteration[]
) {
  const phase = coerceTrainingPhase(job.current_phase, phaseForLiveJob(job));
  const scaffold = buildLiveScaffoldIteration(job);
  const normalizedReal = realIterations.map((iteration, index) =>
    normalizeTrainingIteration(job, iteration, index > 0 ? realIterations[index - 1] : scaffold)
  );

  if (normalizedReal.length === 0) {
    return [scaffold];
  }

  if (job.status !== "running") {
    return normalizedReal;
  }

  const latest = normalizedReal.at(-1);
  if (!latest || job.iteration <= latest.iteration) {
    return normalizedReal;
  }

  return [
    ...normalizedReal,
    normalizeTrainingIteration(job, {
      ...latest,
      iteration: job.iteration,
      phase,
      workers: latest.workers.map((worker) => ({
        ...worker,
        status: statusForLivePhase(phase),
      })),
    }, latest),
  ];
}

function FlowTrainingNode({ data }: NodeProps<Node<FlowNodeData>>) {
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
  }[data.tone];

  return (
    <div className={cn("min-w-[150px] rounded-lg border px-3 py-2.5 transition-all", toneClass)}>
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-0 !bg-cyan/70" />
      <Handle type="target" position={Position.Top} className="!h-2 !w-2 !border-0 !bg-cyan/70" />
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-[12px] font-semibold text-foreground">{data.title}</div>
          <div className="mt-0.5 truncate text-[9px] font-mono text-muted-foreground">
            {data.subtitle}
          </div>
        </div>
        <span
          className={cn(
            "h-2 w-2 rounded-full",
            data.active ? "bg-cyan pulse-dot" : "bg-white/20"
          )}
        />
      </div>
      {data.metrics && (
        <div className="mt-2 grid grid-cols-2 gap-1.5 border-t border-white/10 pt-2">
          {data.metrics.map((metric) => (
            <div key={metric.label}>
              <div className="text-[8px] font-mono uppercase text-muted-foreground/70">
                {metric.label}
              </div>
              <div className="truncate text-[10px] font-mono text-foreground/80">
                {metric.value}
              </div>
            </div>
          ))}
        </div>
      )}
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-0 !bg-cyan/70" />
      <Handle type="source" position={Position.Bottom} className="!h-2 !w-2 !border-0 !bg-cyan/70" />
    </div>
  );
}

function getActiveEdgeIds(phase: TrainingPhase, workers: TrainingIteration["workers"]) {
  const edgeIds = new Set<string>();
  if (phase === "upload" || phase === "partition") {
    workers.forEach((worker) => edgeIds.add(`dataset-${worker.shardId}`));
  }
  if (phase === "partition" || phase === "map") {
    workers.forEach((worker) => edgeIds.add(`${worker.shardId}-${worker.id}`));
  }
  if (phase === "shuffle" || phase === "reduce") {
    workers.forEach((worker) => edgeIds.add(`${worker.id}-reducer`));
  }
  if (phase === "aggregate") edgeIds.add("reducer-model");
  if (phase === "broadcast") {
    workers.forEach((worker) => edgeIds.add(`model-${worker.id}`));
  }
  return edgeIds;
}

function buildFlow(
  iteration: TrainingIteration,
  activePhase: TrainingPhase,
  k: number
): { nodes: Node<FlowNodeData>[]; edges: Edge[] } {
  const workers = iteration.workers;
  const activeEdgeIds = getActiveEdgeIds(activePhase, workers);
  const workerCount = Math.max(1, workers.length);
  const startY = Math.max(20, 170 - workerCount * 33);
  const gapY = workerCount > 1 ? 320 / (workerCount - 1) : 0;
  const reducerReceived =
    activePhase === "reduce" || activePhase === "aggregate" || activePhase === "broadcast" || activePhase === "complete"
      ? workers.length
      : activePhase === "shuffle"
      ? 0
      : 0;
  const datasetX = activePhase === "upload" ? 24 : 0;
  const shardX = activePhase === "partition" ? 220 : 195;
  const workerX = activePhase === "map" ? 420 : activePhase === "broadcast" ? 375 : 395;
  const reducerX = activePhase === "shuffle" || activePhase === "reduce" ? 650 : 620;
  const modelX = activePhase === "broadcast" ? 500 : 620;
  const modelY = activePhase === "aggregate" ? 292 : activePhase === "broadcast" ? 325 : 340;

  const nodes: Node<FlowNodeData>[] = [
    {
      id: "dataset",
      type: "trainingNode",
      position: { x: datasetX, y: 160 },
      data: {
        title: "Dataset",
        subtitle: `${iteration.points2d.length} projected samples`,
        kind: "dataset",
        tone: "cyan",
        active: activePhase === "upload" || activePhase === "partition",
        metrics: [
          { label: "shards", value: String(workerCount) },
          { label: "phase", value: formatPhase(activePhase) },
        ],
      },
    },
    {
      id: "reducer",
      type: "trainingNode",
      position: { x: reducerX, y: 150 },
      data: {
        title: "Reducer",
        subtitle: "centroid sums + counts",
        kind: "reducer",
        tone: "violet",
        active: activePhase === "shuffle" || activePhase === "reduce",
        metrics: [
          { label: "received", value: `${reducerReceived}/${workers.length}` },
          { label: "inertia", value: iteration.inertia.toFixed(1) },
        ],
      },
    },
    {
      id: "model",
      type: "trainingNode",
      position: { x: modelX, y: modelY },
      data: {
        title: "Centroid state",
        subtitle: `k=${k} model broadcast`,
        kind: "model",
        tone: "green",
        active: activePhase === "aggregate" || activePhase === "broadcast" || activePhase === "complete",
        metrics: [
          { label: "movement", value: iteration.movement.toFixed(4) },
          { label: "iter", value: String(iteration.iteration) },
        ],
      },
    },
  ];

  workers.forEach((worker, index) => {
    const y = startY + index * gapY;
    const mappedCount = Object.values(worker.clusterCounts).reduce((sum, count) => sum + count, 0);
    nodes.push(
      {
        id: worker.shardId,
        type: "trainingNode",
        position: { x: shardX, y },
        data: {
          title: worker.shardId,
          subtitle: "partitioned shard",
          kind: "shard",
          tone: "cyan",
          active: activePhase === "partition",
          metrics: [
            { label: "samples", value: worker.sampleCount.toLocaleString() },
            { label: "target", value: worker.id },
          ],
        },
      },
      {
        id: worker.id,
        type: "trainingNode",
        position: { x: workerX, y },
        data: {
          title: worker.id,
          subtitle: `RunPod Flash · ${workerStatusForPhase(activePhase, worker.status)}`,
          kind: "worker",
          tone: "green",
          active: activePhase === "map" || activePhase === "broadcast",
          metrics: [
            { label: "assign", value: mappedCount.toLocaleString() },
            { label: "local loss", value: worker.localInertia?.toFixed(1) ?? "waiting" },
          ],
        },
      }
    );
  });

  const edgeBase = {
    markerEnd: { type: MarkerType.ArrowClosed, color: "oklch(0.80 0.16 200 / 0.65)" },
  };

  const styleFor = (edgeId: string, tone: "cyan" | "green" | "violet" = "cyan") => {
    const active = activeEdgeIds.has(edgeId);
    const color =
      tone === "violet"
        ? "oklch(0.65 0.20 290)"
        : tone === "green"
        ? "oklch(0.76 0.18 145)"
        : "oklch(0.80 0.16 200)";
    return {
      animated: active,
      style: {
        stroke: active ? color : "oklch(1 0 0 / 0.12)",
        strokeWidth: active ? 2 : 1,
      },
    };
  };

  const edges: Edge[] = workers.flatMap((worker) => {
    const datasetEdge = `dataset-${worker.shardId}`;
    const shardEdge = `${worker.shardId}-${worker.id}`;
    const reducerEdge = `${worker.id}-reducer`;
    const broadcastEdge = `model-${worker.id}`;
    return [
      {
        id: datasetEdge,
        source: "dataset",
        target: worker.shardId,
        type: "smoothstep",
        ...edgeBase,
        ...styleFor(datasetEdge),
      },
      {
        id: shardEdge,
        source: worker.shardId,
        target: worker.id,
        type: "smoothstep",
        ...edgeBase,
        ...styleFor(shardEdge),
      },
      {
        id: reducerEdge,
        source: worker.id,
        target: "reducer",
        type: "smoothstep",
        ...edgeBase,
        ...styleFor(reducerEdge, "violet"),
      },
      {
        id: broadcastEdge,
        source: "model",
        target: worker.id,
        type: "smoothstep",
        ...edgeBase,
        ...styleFor(broadcastEdge, "green"),
      },
    ];
  });

  edges.push({
    id: "reducer-model",
    source: "reducer",
    target: "model",
    type: "smoothstep",
    ...edgeBase,
    ...styleFor("reducer-model", "green"),
  });

  return { nodes, edges };
}

function ClusterPlaybackCanvas({
  iteration,
  previousIteration,
  k,
}: {
  iteration: TrainingIteration;
  previousIteration: TrainingIteration | null;
  k: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let raf = 0;
    const started = performance.now();
    const duration = 520;
    const previousPoints = new Map(previousIteration?.points2d.map((point) => [point.id, point]));
    const previousCentroids = new Map(previousIteration?.centroids2d.map((point) => [point.id, point]));
    const allX = [...iteration.points2d, ...iteration.centroids2d].map((point) => point.x);
    const allY = [...iteration.points2d, ...iteration.centroids2d].map((point) => point.y);
    const minX = Math.min(...allX, -1);
    const maxX = Math.max(...allX, 1);
    const minY = Math.min(...allY, -1);
    const maxY = Math.max(...allY, 1);
    const pad = 0.12;

    const draw = () => {
      const width = canvas.offsetWidth;
      const height = canvas.offsetHeight;
      const ratio = window.devicePixelRatio || 1;
      if (canvas.width !== width * ratio || canvas.height !== height * ratio) {
        canvas.width = width * ratio;
        canvas.height = height * ratio;
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      }

      const elapsed = performance.now() - started;
      const t = Math.min(1, elapsed / duration);
      const eased = 1 - Math.pow(1 - t, 3);

      const toScreen = (x: number, y: number) => ({
        x: (pad + ((x - minX) / (maxX - minX || 1)) * (1 - pad * 2)) * width,
        y: (pad + ((y - minY) / (maxY - minY || 1)) * (1 - pad * 2)) * height,
      });

      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = "oklch(0.055 0.012 240)";
      ctx.fillRect(0, 0, width, height);

      ctx.strokeStyle = "oklch(1 0 0 / 0.04)";
      ctx.lineWidth = 1;
      for (let x = 24; x < width; x += 48) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
      }
      for (let y = 24; y < height; y += 48) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
      }

      iteration.points2d.forEach((point) => {
        const previous = previousPoints.get(point.id) ?? point;
        const sx = previous.x + (point.x - previous.x) * eased;
        const sy = previous.y + (point.y - previous.y) * eased;
        const screen = toScreen(sx, sy);
        const changed = previous.cluster !== point.cluster;
        const color = CLUSTER_COLORS[point.cluster % CLUSTER_COLORS.length];

        ctx.beginPath();
        ctx.arc(screen.x, screen.y, changed ? 4.2 : 3.2, 0, Math.PI * 2);
        ctx.fillStyle = color.replace(")", changed ? " / 0.95)" : " / 0.72)");
        ctx.fill();
      });

      iteration.centroids2d.forEach((centroid) => {
        const previous = previousCentroids.get(centroid.id) ?? centroid;
        const sx = previous.x + (centroid.x - previous.x) * eased;
        const sy = previous.y + (centroid.y - previous.y) * eased;
        const screen = toScreen(sx, sy);
        const color = CLUSTER_COLORS[centroid.cluster % CLUSTER_COLORS.length];

        const glow = ctx.createRadialGradient(screen.x, screen.y, 0, screen.x, screen.y, 24);
        glow.addColorStop(0, color.replace(")", " / 0.32)"));
        glow.addColorStop(1, "transparent");
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(screen.x, screen.y, 24, 0, Math.PI * 2);
        ctx.fill();

        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(screen.x - 8, screen.y);
        ctx.lineTo(screen.x + 8, screen.y);
        ctx.moveTo(screen.x, screen.y - 8);
        ctx.lineTo(screen.x, screen.y + 8);
        ctx.stroke();

        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(screen.x, screen.y, 5, 0, Math.PI * 2);
        ctx.fill();
      });

      if (t < 1) raf = requestAnimationFrame(draw);
    };

    draw();
    return () => cancelAnimationFrame(raf);
  }, [iteration, previousIteration]);

  const counts = Array.from({ length: k }, (_, cluster) =>
    iteration.points2d.filter((point) => point.cluster === cluster).length
  );

  return (
    <div className="rounded-lg border border-border/50 bg-[oklch(0.06_0.012_240)] p-3">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <div className="text-xs font-mono uppercase tracking-wider text-muted-foreground">
            2D cluster assignment playback
          </div>
          <div className="mt-0.5 text-[10px] font-mono text-muted-foreground/70">
            points recolor as workers remap samples; centroids drift after reduce
          </div>
        </div>
        <div className="hidden items-center gap-3 text-[10px] font-mono sm:flex">
          {Array.from({ length: k }, (_, cluster) => (
            <span key={cluster} className="flex items-center gap-1 text-muted-foreground">
              <span
                className="h-2.5 w-2.5 rounded-full"
                style={{ background: CLUSTER_COLORS[cluster % CLUSTER_COLORS.length] }}
              />
              C{cluster}
            </span>
          ))}
        </div>
      </div>
      <div className="aspect-[1.55/1] overflow-hidden rounded-md border border-border/30">
        <canvas ref={canvasRef} className="h-full w-full" />
      </div>
      <div
        className="mt-3 grid gap-2"
        style={{ gridTemplateColumns: `repeat(${Math.max(1, k)}, minmax(0, 1fr))` }}
      >
        {counts.map((count, cluster) => (
          <div key={cluster} className="rounded border border-white/10 bg-white/[0.025] px-2 py-1.5">
            <div className="text-[9px] font-mono text-muted-foreground">C{cluster}</div>
            <div className="text-xs font-mono text-foreground">{count}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function MapReduceTrainingVisualization({ job }: { job: JobStatusResponse }) {
  const hasRealSnapshots = Boolean(job.training_iterations?.length);
  const iterations = useMemo(
    () => buildLiveTrainingIterations(job, job.training_iterations ?? []),
    [job]
  );

  return (
    <RealMapReduceTrainingVisualization
      job={job}
      iterations={iterations}
      hasRealSnapshots={hasRealSnapshots}
    />
  );
}

function RealMapReduceTrainingVisualization({
  job,
  iterations,
  hasRealSnapshots,
}: {
  job: JobStatusResponse;
  iterations: TrainingIteration[];
  hasRealSnapshots: boolean;
}) {
  const [selectedIndex, setSelectedIndex] = useState(() => Math.max(0, iterations.length - 1));
  const [phaseIndex, setPhaseIndex] = useState(() => PHASES.indexOf(iterations.at(-1)?.phase ?? "partition"));
  const [isPlaying, setIsPlaying] = useState(false);

  useEffect(() => {
    if (isPlaying) return;
    const timer = window.setTimeout(() => {
      const nextIndex = Math.max(0, iterations.length - 1);
      setSelectedIndex(nextIndex);
      const defaultPhase = coerceTrainingPhase(
        job.current_phase,
        iterations[nextIndex]?.phase ?? phaseForLiveJob(job)
      );
      setPhaseIndex(Math.max(0, PHASES.indexOf(defaultPhase)));
    }, 0);
    return () => window.clearTimeout(timer);
  }, [iterations, job, isPlaying]);

  useEffect(() => {
    if (!isPlaying) return;
    const timer = setTimeout(() => {
      setPhaseIndex((currentPhaseIndex) => {
        if (currentPhaseIndex < PHASES.length - 1) return currentPhaseIndex + 1;
        setSelectedIndex((currentIterationIndex) => {
          if (currentIterationIndex >= iterations.length - 1) {
            setIsPlaying(false);
            return currentIterationIndex;
          }
          return currentIterationIndex + 1;
        });
        return PHASES.indexOf("partition");
      });
    }, 950);
    return () => clearTimeout(timer);
  }, [isPlaying, iterations.length, phaseIndex, selectedIndex]);

  const safeSelectedIndex = Math.min(selectedIndex, Math.max(0, iterations.length - 1));
  const iteration = iterations[safeSelectedIndex] ?? iterations[0];
  const previousIteration = iterations[Math.max(0, safeSelectedIndex - 1)] ?? null;
  const activePhase = PHASES[phaseIndex] ?? iteration.phase;

  const { nodes, edges } = useMemo(
    () => buildFlow(iteration, activePhase, job.k),
    [iteration, activePhase, job.k]
  );
  const plannedIterationEnd = job.converged_at ?? job.max_iter ?? Math.max(0, iterations.length - 1);
  const firstIterationNumber = iterations[0]?.iteration ?? 0;
  const lastAvailableIteration = iterations.at(-1)?.iteration ?? 0;
  const timelineStatusLabel = job.converged_at
    ? `converged @ ${job.converged_at}`
    : `${lastAvailableIteration} available / ${plannedIterationEnd} planned`;

  const clusterTotals = iteration.workers.reduce<Record<string, number>>((totals, worker) => {
    Object.entries(worker.clusterCounts).forEach(([cluster, count]) => {
      totals[cluster] = (totals[cluster] ?? 0) + count;
    });
    return totals;
  }, {});

  const moveToIteration = (index: number) => {
    setSelectedIndex(Math.min(Math.max(0, index), iterations.length - 1));
    setPhaseIndex(PHASES.indexOf("upload"));
    setIsPlaying(false);
  };

  const togglePlayback = () => {
    if (isPlaying) {
      setIsPlaying(false);
      return;
    }
    if (activePhase === "complete" && safeSelectedIndex >= iterations.length - 1) {
      setSelectedIndex(0);
      setPhaseIndex(PHASES.indexOf("upload"));
    }
    setIsPlaying(true);
  };

  return (
    <section className="rounded-xl border border-cyan/15 bg-surface p-4 shadow-[0_0_80px_oklch(0.80_0.16_200_/_0.05)] sm:p-5">
      <div className="mb-5 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2">
            <SquaresFour className="h-4 w-4 text-cyan" weight="duotone" />
            <h2 className="text-base font-semibold text-foreground">
              MapReduce training visualization
            </h2>
          </div>
          <p className="max-w-2xl text-xs font-mono leading-relaxed text-muted-foreground">
            Distributed K-Means across RunPod Flash workers: shard upload, local map assignments,
            reducer aggregation, centroid update, and broadcast for the next iteration.
          </p>
          <div className="mt-2 text-[10px] font-mono text-muted-foreground/70">
            {hasRealSnapshots
              ? "source: live coordinator training_iterations"
              : "source: live job metadata until first map result"}
          </div>
        </div>

        <div className="grid grid-cols-3 gap-2 text-right font-mono">
          <div className="rounded-md border border-border/50 bg-surface-elevated px-3 py-2">
            <div className="text-[9px] uppercase text-muted-foreground">iteration</div>
            <div className="text-sm font-bold text-cyan metric-value">
              {iteration.iteration}/{plannedIterationEnd}
            </div>
          </div>
          <div className="rounded-md border border-border/50 bg-surface-elevated px-3 py-2">
            <div className="text-[9px] uppercase text-muted-foreground">movement</div>
            <div className="text-sm font-bold text-node-green metric-value">
              {iteration.movement.toFixed(4)}
            </div>
          </div>
          <div className="rounded-md border border-border/50 bg-surface-elevated px-3 py-2">
            <div className="text-[9px] uppercase text-muted-foreground">inertia</div>
            <div className="text-sm font-bold text-violet-400 metric-value">
              {iteration.inertia.toFixed(1)}
            </div>
          </div>
        </div>
      </div>

      <div className="mb-5 grid grid-cols-2 gap-2 md:grid-cols-4 lg:grid-cols-8">
        {PHASES.map((phase) => {
          const status = getPhaseStatus(phase, activePhase);
          return (
            <div
              key={phase}
              className={cn(
                "rounded-md border px-2.5 py-2 transition-all",
                status === "active"
                  ? "border-cyan/60 bg-cyan/10 text-cyan shadow-[0_0_20px_oklch(0.80_0.16_200_/_0.12)]"
                  : status === "done"
                  ? "border-node-green/25 bg-node-green/5 text-node-green"
                  : "border-border/40 bg-white/[0.015] text-muted-foreground"
              )}
            >
              <div className="flex items-center gap-2">
                <span
                  className={cn(
                    "h-1.5 w-1.5 rounded-full",
                    status === "active" ? "bg-cyan pulse-dot" : status === "done" ? "bg-node-green" : "bg-white/20"
                  )}
                />
                <span className="text-[10px] font-mono uppercase tracking-wider">
                  {formatPhase(phase)}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(360px,0.8fr)]">
        <div className="rounded-lg border border-border/50 bg-[oklch(0.055_0.012_240)] p-3">
          <div className="mb-3 flex items-center justify-between">
            <div>
              <div className="text-xs font-mono uppercase tracking-wider text-muted-foreground">
                React Flow infrastructure graph
              </div>
              <div className="mt-0.5 text-[10px] font-mono text-muted-foreground/70">
                animated edges show data movement for the active phase
              </div>
            </div>
            <Broadcast className="h-4 w-4 text-cyan/70" weight="duotone" />
          </div>
          <div className="flashml-flow h-[520px] overflow-hidden rounded-md border border-border/30 bg-background/80">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={{ trainingNode: FlowTrainingNode }}
              defaultViewport={{ x: 70, y: 42, zoom: 0.72 }}
              fitView
              fitViewOptions={{ padding: 0.18, minZoom: 0.45, maxZoom: 0.9 }}
              minZoom={0.45}
              maxZoom={1.4}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable={false}
              onlyRenderVisibleElements={false}
              panOnScroll={false}
              zoomOnScroll={false}
              proOptions={{ hideAttribution: true }}
            >
              <Background color="oklch(1 0 0 / 0.10)" gap={28} />
              <Controls showInteractive={false} position="bottom-right" />
            </ReactFlow>
          </div>
        </div>

        <div className="space-y-4">
          <ClusterPlaybackCanvas
            iteration={iteration}
            previousIteration={previousIteration}
            k={job.k}
          />

          <div className="rounded-lg border border-border/50 bg-surface-elevated p-4">
            <div className="mb-3 flex items-center justify-between">
              <div>
                <div className="text-xs font-mono uppercase tracking-wider text-muted-foreground">
                  reducer state
                </div>
                <div className="mt-0.5 text-[10px] font-mono text-muted-foreground/70">
                  worker outputs merged into global centroids
                </div>
              </div>
              <Funnel className="h-4 w-4 text-violet-400" weight="duotone" />
            </div>

            <div className="grid grid-cols-2 gap-3">
              {iteration.workers.map((worker) => {
                const displayStatus = workerStatusForPhase(activePhase, worker.status);
                return (
                  <div key={worker.id} className="rounded-md border border-white/10 bg-white/[0.025] p-3">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold text-foreground">{worker.id}</span>
                      <span
                        className={cn(
                          "rounded border px-1.5 py-0.5 text-[9px] font-mono uppercase",
                          displayStatus === "mapping"
                            ? "border-amber-400/30 text-amber-400"
                            : displayStatus === "done"
                            ? "border-node-green/30 text-node-green"
                            : "border-border text-muted-foreground"
                        )}
                      >
                        {displayStatus}
                      </span>
                    </div>
                    <div className="space-y-1 text-[10px] font-mono">
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">shard</span>
                        <span className="text-cyan">{worker.shardId}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">samples</span>
                        <span className="text-foreground/80">{worker.sampleCount.toLocaleString()}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">local inertia</span>
                        <span className="text-violet-400">{worker.localInertia?.toFixed(1) ?? "waiting"}</span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>

            <div className="mt-4 border-t border-border/40 pt-3">
              <div className="mb-2 text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                aggregated centroid update
              </div>
              <div className="grid gap-2" style={{ gridTemplateColumns: `repeat(${job.k}, minmax(0, 1fr))` }}>
                {Array.from({ length: job.k }, (_, cluster) => (
                  <div key={cluster} className="rounded border border-white/10 bg-background/40 px-2 py-2">
                    <div className="flex items-center gap-1.5">
                      <span
                        className="h-2 w-2 rounded-full"
                        style={{ background: CLUSTER_COLORS[cluster % CLUSTER_COLORS.length] }}
                      />
                      <span className="text-[10px] font-mono text-muted-foreground">C{cluster}</span>
                    </div>
                    <div className="mt-1 text-xs font-mono text-foreground metric-value">
                      {(clusterTotals[String(cluster)] ?? 0).toLocaleString()}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="mt-5 rounded-lg border border-border/50 bg-background/50 p-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={togglePlayback}
              className="inline-flex h-9 items-center gap-2 rounded-md bg-cyan px-3 text-sm font-semibold text-background transition-all hover:bg-cyan/90 active:scale-[0.98]"
            >
              {isPlaying ? <Pause className="h-4 w-4" weight="bold" /> : <Play className="h-4 w-4" weight="bold" />}
              {isPlaying ? "Pause" : "Play"}
            </button>
            <button
              type="button"
              onClick={() => moveToIteration(safeSelectedIndex - 1)}
              disabled={safeSelectedIndex === 0}
              className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border/60 px-3 text-sm text-foreground transition-all hover:border-cyan/40 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <CaretLeft className="h-4 w-4" weight="bold" />
              Previous
            </button>
            <button
              type="button"
              onClick={() => moveToIteration(safeSelectedIndex + 1)}
              disabled={safeSelectedIndex >= iterations.length - 1}
              className="inline-flex h-9 items-center gap-1.5 rounded-md border border-border/60 px-3 text-sm text-foreground transition-all hover:border-cyan/40 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Next
              <CaretRight className="h-4 w-4" weight="bold" />
            </button>
            <button
              type="button"
              onClick={() => moveToIteration(0)}
              className="inline-flex h-9 items-center justify-center rounded-md border border-border/60 px-3 text-sm text-muted-foreground transition-all hover:border-cyan/40 hover:text-foreground"
              aria-label="Reset playback"
            >
              <ArrowsClockwise className="h-4 w-4" weight="bold" />
            </button>
          </div>

          <div className="min-w-0 flex-1">
            <div className="mb-1 flex items-center justify-between text-[10px] font-mono text-muted-foreground">
              <span>Iteration {firstIterationNumber}</span>
              <span>{timelineStatusLabel}</span>
            </div>
            <input
              type="range"
              min={0}
              max={Math.max(0, iterations.length - 1)}
              value={safeSelectedIndex}
              onChange={(event) => moveToIteration(Number(event.target.value))}
              className="h-2 w-full accent-cyan"
              aria-label="Training iteration timeline"
            />
          </div>

          <div className="min-w-[160px] rounded-md border border-border/50 bg-surface px-3 py-2 text-xs font-mono">
            <div className="flex justify-between gap-3">
              <span className="text-muted-foreground">phase</span>
              <span className="text-cyan">{formatPhase(activePhase)}</span>
            </div>
            <div className="mt-1 flex justify-between gap-3">
              <span className="text-muted-foreground">workers</span>
              <span className="text-node-green">{iteration.workers.length} reported</span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
