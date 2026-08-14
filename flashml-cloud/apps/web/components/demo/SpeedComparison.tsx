"use client";

import { CoordinatorChip } from "@/components/jobs/CoordinatorChip";
import {
  compareSpeed,
  formatElapsed,
  type DemoCoordinator,
  type DemoSnapshot,
} from "@/lib/demo";
import { cn } from "@/lib/utils";

/**
 * The head-to-head, once — and only once — both venues have finished.
 *
 * PLAINLY, which is the whole brief for this block. Two numbers, the same
 * chips the panels above used, and one sentence naming the winner and the
 * gap. No bar chart: two values do not need an axis, and a chart here would
 * be decoration standing where a fact should be.
 *
 * It renders NOTHING until `compareSpeed` says the comparison is real (both
 * runs terminal, both timed). A partial comparison is not an early result,
 * it is a wrong one — the slower venue has not finished being slow yet — and
 * this is the number a judge is most likely to write down.
 */
export function SpeedComparison({ snapshot }: { snapshot: DemoSnapshot | null }) {
  const result = compareSpeed(snapshot);
  if (!result) return null;

  const rows: { coordinator: DemoCoordinator; seconds: number }[] = [
    { coordinator: "render", seconds: result.render },
    { coordinator: "fc", seconds: result.fc },
  ];

  return (
    <section className="panel p-4">
      <p className="label-caps">Same nine tasks, same four machines</p>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {rows.map(({ coordinator, seconds }) => {
          const won = result.faster === coordinator;
          return (
            <div
              key={coordinator}
              className={cn(
                "flex items-center justify-between gap-3 rounded-md border px-3 py-2.5",
                won ? "border-brand/50 bg-brand/[0.05]" : "border-border bg-surface"
              )}
            >
              <CoordinatorChip coordinator={coordinator} />
              <span
                className={cn(
                  "metric-lg",
                  won ? "text-foreground" : "text-muted-foreground"
                )}
              >
                {formatElapsed(seconds)}
              </span>
            </div>
          );
        })}
      </div>

      <p className="mt-3 text-[13px] leading-relaxed text-foreground">
        {result.faster === null ? (
          <>Both control planes drove the same work in the same time.</>
        ) : (
          <>
            <CoordinatorChip coordinator={result.faster} /> finished{" "}
            <span className="metric-value font-semibold">
              {formatElapsed(result.deltaSeconds)}
            </span>{" "}
            sooner
            {result.ratio !== null && (
              <>
                {" "}
                — <span className="metric-value font-semibold">
                  {result.ratio.toFixed(1)}×
                </span>{" "}
                the speed
              </>
            )}
            .
          </>
        )}
      </p>
    </section>
  );
}
