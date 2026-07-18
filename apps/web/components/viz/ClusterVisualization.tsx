"use client";

import { useEffect, useRef } from "react";

const CLUSTER_COLORS = [
  "oklch(0.80 0.16 200)",   // cyan
  "oklch(0.65 0.20 290)",   // violet
  "oklch(0.76 0.18 145)",   // green
  "oklch(0.80 0.18 60)",    // amber
  "oklch(0.70 0.20 20)",    // red
  "oklch(0.75 0.15 320)",   // pink
];

interface ClusterVisualizationProps {
  points2d: number[][];
  assignments: number[];
  k: number;
  nPoints: number;
}

export function ClusterVisualization({ points2d, assignments, k, nPoints }: ClusterVisualizationProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || points2d.length === 0) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const W = canvas.offsetWidth;
    const H = canvas.offsetHeight;
    canvas.width = W * window.devicePixelRatio;
    canvas.height = H * window.devicePixelRatio;
    ctx.scale(window.devicePixelRatio, window.devicePixelRatio);
    ctx.clearRect(0, 0, W, H);

    // Normalize real 2D SVD-projected points into [0.08, 0.92] canvas space
    const xs = points2d.map((p) => p[0]);
    const ys = points2d.map((p) => p[1]);
    const xMin = Math.min(...xs), xMax = Math.max(...xs);
    const yMin = Math.min(...ys), yMax = Math.max(...ys);
    const xRange = xMax - xMin || 1;
    const yRange = yMax - yMin || 1;
    const pad = 0.08;

    const toScreen = (x: number, y: number) => ({
      x: (pad + ((x - xMin) / xRange) * (1 - 2 * pad)) * W,
      y: (pad + ((y - yMin) / yRange) * (1 - 2 * pad)) * H,
    });

    // Points
    points2d.forEach(([x, y], i) => {
      const cluster = assignments[i] ?? 0;
      const { x: sx, y: sy } = toScreen(x, y);
      ctx.beginPath();
      ctx.arc(sx, sy, 3.5, 0, Math.PI * 2);
      ctx.fillStyle = CLUSTER_COLORS[cluster % CLUSTER_COLORS.length].replace(")", " / 0.75)").replace("oklch(", "oklch(");
      ctx.fill();
    });

    // Centroids: mean of each cluster's real 2D points
    for (let c = 0; c < k; c++) {
      const clusterPoints = points2d.filter((_, i) => assignments[i] === c);
      if (clusterPoints.length === 0) continue;
      const mx = clusterPoints.reduce((s, p) => s + p[0], 0) / clusterPoints.length;
      const my = clusterPoints.reduce((s, p) => s + p[1], 0) / clusterPoints.length;
      const { x: cx, y: cy } = toScreen(mx, my);
      const color = CLUSTER_COLORS[c % CLUSTER_COLORS.length];

      const grd = ctx.createRadialGradient(cx, cy, 0, cx, cy, 20);
      grd.addColorStop(0, color.replace(")", " / 0.3)").replace("oklch(", "oklch("));
      grd.addColorStop(1, "transparent");
      ctx.beginPath();
      ctx.arc(cx, cy, 20, 0, Math.PI * 2);
      ctx.fillStyle = grd;
      ctx.fill();

      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(cx - 8, cy);
      ctx.lineTo(cx + 8, cy);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(cx, cy - 8);
      ctx.lineTo(cx, cy + 8);
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(cx, cy, 5, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
    }
  }, [points2d, assignments, k]);

  const clusterCounts = Array.from({ length: k }, (_, c) => assignments.filter((a) => a === c).length);

  return (
    <div className="p-4 rounded-lg border border-border/60 bg-surface">
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="text-xs font-mono text-muted-foreground uppercase tracking-wider">
            Cluster Visualization
          </div>
          <div className="text-xs font-mono text-muted-foreground/60 mt-0.5">
            K-Means · k={k} · {nPoints} tickets projected to 2D (TF-IDF + TruncatedSVD)
          </div>
        </div>
        <div className="flex items-center gap-3 text-[10px] font-mono">
          {Array.from({ length: k }, (_, i) => (
            <div key={i} className="flex items-center gap-1">
              <span
                className="w-2.5 h-2.5 rounded-full"
                style={{ background: CLUSTER_COLORS[i % CLUSTER_COLORS.length] }}
              />
              <span className="text-muted-foreground">C{i}</span>
            </div>
          ))}
          <span className="text-muted-foreground/50">+ centroids</span>
        </div>
      </div>

      <div className="relative w-full aspect-[2/1] rounded-md overflow-hidden bg-[oklch(0.06_0.01_240)] border border-border/30">
        <canvas ref={canvasRef} className="w-full h-full" />
      </div>

      <div className="mt-3 grid gap-2" style={{ gridTemplateColumns: `repeat(${k}, minmax(0, 1fr))` }}>
        {clusterCounts.map((count, i) => (
          <div key={i} className="text-center text-[10px] font-mono text-muted-foreground">
            C{i}: {count}
          </div>
        ))}
      </div>
    </div>
  );
}
