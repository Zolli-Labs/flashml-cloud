"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

const STAGES = [
  { id: "upload", label: "Upload", color: "bg-cyan", textColor: "text-cyan", duration: 1200 },
  { id: "partition", label: "Partition", color: "bg-violet-400", textColor: "text-violet-400", duration: 800 },
  { id: "map", label: "Map", color: "bg-amber-400", textColor: "text-amber-400", duration: 2400 },
  { id: "shuffle", label: "Shuffle", color: "bg-orange-400", textColor: "text-orange-400", duration: 400 },
  { id: "reduce", label: "Reduce", color: "bg-violet-400", textColor: "text-violet-400", duration: 600 },
  { id: "aggregate", label: "Aggregate", color: "bg-cyan", textColor: "text-cyan", duration: 500 },
  { id: "output", label: "Output", color: "bg-node-green", textColor: "text-node-green", duration: 300 },
];

const TOTAL = STAGES.reduce((s, st) => s + st.duration, 0);

export function PipelineTimeline({ currentStage = 4 }: { currentStage?: number }) {
  const [active, setActive] = useState(currentStage);

  useEffect(() => {
    if (active >= STAGES.length - 1) return;
    const timer = setTimeout(() => setActive((s) => s + 1), 3000);
    return () => clearTimeout(timer);
  }, [active]);

  return (
    <div className="p-4 rounded-lg border border-border/60 bg-surface">
      <div className="text-xs font-mono text-muted-foreground uppercase tracking-wider mb-3">
        MapReduce Pipeline
      </div>

      {/* Stage blocks */}
      <div className="flex h-6 w-full rounded overflow-hidden gap-px">
        {STAGES.map((stage, i) => {
          const pct = (stage.duration / TOTAL) * 100;
          const done = i < active;
          const current = i === active;
          return (
            <div
              key={stage.id}
              style={{ width: `${pct}%` }}
              className={cn(
                "relative h-full transition-all duration-500 flex-shrink-0 min-w-[2px]",
                done
                  ? stage.color
                  : current
                  ? cn(stage.color, "opacity-70 animate-pulse")
                  : "bg-white/5"
              )}
              title={stage.label}
            />
          );
        })}
      </div>

      {/* Labels */}
      <div className="flex mt-2 w-full gap-px">
        {STAGES.map((stage, i) => {
          const pct = (stage.duration / TOTAL) * 100;
          const done = i < active;
          const current = i === active;
          return (
            <div
              key={stage.id}
              style={{ width: `${pct}%` }}
              className={cn(
                "text-[9px] font-mono truncate flex-shrink-0 min-w-[2px]",
                done ? stage.textColor : current ? cn(stage.textColor, "opacity-80") : "text-muted-foreground/40"
              )}
            >
              {pct > 5 ? stage.label : ""}
            </div>
          );
        })}
      </div>

      {/* Current stage indicator */}
      <div className="mt-3 flex items-center gap-2">
        <div
          className={cn(
            "w-1.5 h-1.5 rounded-full pulse-dot",
            active < STAGES.length - 1 ? "bg-cyan" : "bg-node-green"
          )}
        />
        <span className="text-xs font-mono text-muted-foreground">
          Stage:{" "}
          <span className={STAGES[Math.min(active, STAGES.length - 1)].textColor}>
            {STAGES[Math.min(active, STAGES.length - 1)].label.toUpperCase()}
          </span>
        </span>
        {active >= STAGES.length - 1 && (
          <span className="ml-auto text-xs font-mono text-node-green">COMPLETE</span>
        )}
      </div>
    </div>
  );
}
