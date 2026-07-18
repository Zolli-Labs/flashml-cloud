import { cn } from "@/lib/utils";

interface WorkerNodeCardProps {
  id: string;
  status: "running" | "idle" | "offline";
  computeType?: "cpu" | "gpu";
  cpuUsage?: number;
  gpuUsage?: number;
  samplesProcessed: number;
  latencyMs?: number;
  localInertia?: number;
  iteration: number;
  shards: string[];
  className?: string;
}

const statusConfig = {
  running: { color: "text-node-green", dot: "bg-node-green", label: "RUNNING" },
  idle: { color: "text-amber-400", dot: "bg-amber-400", label: "IDLE" },
  offline: { color: "text-red-400", dot: "bg-red-400", label: "OFFLINE" },
};

function UtilBar({ value, color }: { value: number; color: string }) {
  return (
    <div className="w-full h-1 rounded-full bg-white/5">
      <div
        className={cn("h-full rounded-full transition-all duration-500", color)}
        style={{ width: `${value}%` }}
      />
    </div>
  );
}

export function WorkerNodeCard({
  id,
  status,
  computeType = "cpu",
  cpuUsage,
  gpuUsage,
  samplesProcessed,
  latencyMs,
  localInertia,
  iteration,
  shards,
  className,
}: WorkerNodeCardProps) {
  const s = statusConfig[status];

  return (
    <div
      className={cn(
        "p-4 rounded-lg border bg-surface overflow-hidden relative",
        status === "running"
          ? "border-node-green/25"
          : status === "idle"
          ? "border-amber-400/20"
          : "border-border/40",
        className
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <div className={cn("w-1.5 h-1.5 rounded-full pulse-dot", s.dot)} />
          <span className="text-sm font-mono font-semibold text-foreground">{id}</span>
        </div>
        <span className={cn("text-[10px] font-mono", s.color)}>{s.label}</span>
      </div>

      {/* Shards */}
      <div className="flex flex-wrap gap-1 mb-3">
        {shards.map((shard) => (
          <span
            key={shard}
            className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-white/5 text-muted-foreground border border-border/40"
          >
            {shard}
          </span>
        ))}
      </div>

      {/* Metrics grid */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 mb-3">
        <div>
          <div className="text-[10px] font-mono text-muted-foreground mb-1">{computeType.toUpperCase()}</div>
          {(computeType === "gpu" ? gpuUsage : cpuUsage) === undefined ? (
            <div className="text-[11px] font-mono text-muted-foreground mt-0.5">not reported</div>
          ) : (
            <>
              <UtilBar value={computeType === "gpu" ? gpuUsage! : cpuUsage!} color={computeType === "gpu" ? "bg-violet-400" : "bg-cyan"} />
              <div className={cn("text-[11px] font-mono mt-0.5", computeType === "gpu" ? "text-violet-400" : "text-cyan")}>
                {computeType === "gpu" ? gpuUsage : cpuUsage}%
              </div>
            </>
          )}
        </div>
        {computeType === "cpu" && gpuUsage !== undefined && (
          <div>
            <div className="text-[10px] font-mono text-muted-foreground mb-1">GPU</div>
            <UtilBar value={gpuUsage} color="bg-violet-400" />
            <div className="text-[11px] font-mono text-violet-400 mt-0.5">{gpuUsage}%</div>
          </div>
        )}
        <div>
          <div className="text-[10px] font-mono text-muted-foreground">Samples</div>
          <div className="text-[11px] font-mono text-foreground/80">
            {samplesProcessed.toLocaleString()}
          </div>
        </div>
        <div>
          <div className="text-[10px] font-mono text-muted-foreground">Worker latency</div>
          {latencyMs === undefined ? (
            <div className="text-[11px] font-mono text-muted-foreground">not reported</div>
          ) : (
            <div className={cn(
              "text-[11px] font-mono",
              latencyMs > 300 ? "text-amber-400" : "text-node-green"
            )}>
              {latencyMs}ms
            </div>
          )}
        </div>
      </div>

      {localInertia !== undefined && (
        <div className="pt-2.5 border-t border-border/40">
          <div className="flex justify-between items-center">
            <span className="text-[10px] font-mono text-muted-foreground">Local inertia</span>
            <span className="text-[11px] font-mono text-foreground/80">
              {localInertia.toFixed(2)}
            </span>
          </div>
        </div>
      )}

      <div className="absolute top-3 right-3 text-[10px] font-mono text-muted-foreground/40">
        iter {iteration}
      </div>
    </div>
  );
}
