import { type ReactNode } from "react";
import { cn } from "@/lib/utils";

interface MetricCardProps {
  label: string;
  value: string | number;
  unit?: string;
  trend?: "up" | "down" | "neutral";
  trendValue?: string;
  icon?: ReactNode;
  accent?: "cyan" | "green" | "amber" | "red";
  className?: string;
}

const accentMap = {
  cyan: "text-cyan",
  green: "text-node-green",
  amber: "text-amber-400",
  red: "text-red-400",
};

export function MetricCard({
  label,
  value,
  unit,
  trendValue,
  trend,
  icon,
  accent = "cyan",
  className,
}: MetricCardProps) {
  return (
    <div
      className={cn(
        "p-4 rounded-lg border border-border/60 bg-surface",
        "hover:border-border/80 transition-colors",
        className
      )}
    >
      <div className="flex items-start justify-between mb-3">
        <span className="text-xs font-mono text-muted-foreground uppercase tracking-wider">
          {label}
        </span>
        {icon && <span className={cn("text-sm", accentMap[accent])}>{icon}</span>}
      </div>
      <div className="flex items-baseline gap-1">
        <span className={cn("text-2xl font-mono font-bold metric-value", accentMap[accent])}>
          {value}
        </span>
        {unit && (
          <span className="text-xs font-mono text-muted-foreground">{unit}</span>
        )}
      </div>
      {trendValue && (
        <div className={cn(
          "mt-2 text-xs font-mono",
          trend === "up" ? "text-node-green" : trend === "down" ? "text-red-400" : "text-muted-foreground"
        )}>
          {trend === "up" ? "+" : trend === "down" ? "-" : ""}{trendValue}
        </div>
      )}
    </div>
  );
}
