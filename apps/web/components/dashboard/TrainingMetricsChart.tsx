"use client";

import { cn } from "@/lib/utils";

function sparkPath(data: number[], width: number, height: number, maxVal: number): string {
  if (data.length === 0) return "";
  const xStep = data.length > 1 ? width / (data.length - 1) : 0;
  return data
    .map((v, i) => {
      const x = i * xStep;
      const y = height - (v / maxVal) * height;
      return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
}

interface TrainingMetricsChartProps {
  movements: number[];
  maxIter: number;
}

export function TrainingMetricsChart({ movements, maxIter }: TrainingMetricsChartProps) {
  const data = movements;
  const current = data[data.length - 1] ?? 0;
  const prev = data[data.length - 2];
  const delta = prev !== undefined ? (current - prev).toFixed(4) : null;
  const maxVal = Math.max(...data, 0.0001);

  const W = 320;
  const H = 80;
  const path = sparkPath(data, W, H, maxVal);

  const convergencePct = data.length > 0
    ? Math.max(0, Math.min(100, ((maxVal - current) / maxVal) * 100))
    : 0;

  return (
    <div className="p-4 rounded-lg border border-border/60 bg-surface">
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="text-xs font-mono text-muted-foreground uppercase tracking-wider">
            Centroid Movement
          </div>
          <div className="text-2xl font-mono font-bold text-cyan metric-value mt-0.5">
            {current.toFixed(4)}
          </div>
          {delta && (
            <div className={cn("text-xs font-mono", parseFloat(delta) < 0 ? "text-node-green" : "text-amber-400")}>
              {parseFloat(delta) < 0 ? "" : "+"}{delta} / iter
            </div>
          )}
        </div>
        <div className="text-right">
          <div className="text-xs font-mono text-muted-foreground">Iteration</div>
          <div className="text-lg font-mono font-bold text-foreground/80 metric-value">
            {data.length} / {maxIter}
          </div>
        </div>
      </div>

      {/* Spark chart */}
      <div className="relative w-full overflow-hidden" style={{ height: H }}>
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full h-full"
          preserveAspectRatio="none"
        >
          {/* Grid lines */}
          {[0, 0.25, 0.5, 0.75, 1].map((frac) => (
            <line
              key={frac}
              x1="0"
              y1={H * frac}
              x2={W}
              y2={H * frac}
              stroke="currentColor"
              strokeWidth="0.5"
              className="text-white/5"
            />
          ))}

          {/* Fill */}
          {data.length > 1 && (
            <path
              d={`${path} L ${W} ${H} L 0 ${H} Z`}
              fill="oklch(0.80 0.16 200 / 0.08)"
            />
          )}

          {/* Line */}
          {data.length > 1 && (
            <path
              d={path}
              fill="none"
              stroke="oklch(0.80 0.16 200)"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          )}

          {/* Current dot */}
          {data.length > 0 && (() => {
            const i = data.length - 1;
            const x = data.length > 1 ? (i / (data.length - 1)) * W : 0;
            const y = H - (data[i] / maxVal) * H;
            return (
              <circle
                cx={x}
                cy={y}
                r="3"
                fill="oklch(0.80 0.16 200)"
                className="opacity-90"
              />
            );
          })()}
        </svg>
      </div>

      {/* Convergence bar */}
      <div className="mt-3">
        <div className="flex justify-between text-[10px] font-mono text-muted-foreground mb-1">
          <span>Convergence</span>
          <span>{convergencePct.toFixed(1)}%</span>
        </div>
        <div className="w-full h-1 rounded-full bg-white/5">
          <div
            className="h-full rounded-full bg-gradient-to-r from-cyan/80 to-cyan transition-all duration-700"
            style={{ width: `${convergencePct}%` }}
          />
        </div>
      </div>
    </div>
  );
}
