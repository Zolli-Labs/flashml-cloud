"use client";

import { Play } from "@phosphor-icons/react";

import { CoordinatorChip } from "@/components/jobs/CoordinatorChip";
import { StateBadge } from "@/components/jobs/StateBadge";
import { TaskGrid } from "@/components/demo/TaskGrid";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import {
  formatBytes,
  formatElapsed,
  isTerminalRun,
  runElapsedSeconds,
  type DemoCoordinator,
  type DemoMachine,
  type DemoRun,
} from "@/lib/demo";
import type { JobState } from "@/lib/cloud-api";

/**
 * One venue: its Run button, its stopwatch, and its task grid.
 *
 * Two of these render side by side, and they are deliberately IDENTICAL in
 * structure — same heading position, same stopwatch position, same grid.
 * That is what makes them comparable at a glance: any difference a judge
 * sees between the two panels is a difference in the RESULT, never in how
 * the two were drawn.
 *
 * The venue's identity comes from `CoordinatorChip`, the console's existing
 * Render-vs-Function-Compute artifact, rather than a second visual language
 * invented here for the same distinction. The chip already carries the rule
 * that matters: the two venues differ by icon SHAPE and by TEXT, never by
 * tint alone.
 */
export function RunPanel({
  coordinator,
  title,
  subtitle,
  fleet,
  run,
  now,
  starting,
  disabled,
  onRun,
}: {
  coordinator: DemoCoordinator;
  title: string;
  subtitle: string;
  fleet: DemoMachine[];
  run: DemoRun | null;
  /** One clock, owned by the parent and passed down, so both panels' live
   * stopwatches tick on the same instant rather than drifting apart. */
  now: number;
  starting: boolean;
  /** True before the fleet has answered: there is nothing to run on yet. */
  disabled: boolean;
  onRun: () => void;
}) {
  const elapsed = runElapsedSeconds(run, now);
  const live = run !== null && !isTerminalRun(run.state);
  const finished = run !== null && isTerminalRun(run.state);

  return (
    <section className="panel flex flex-col p-4">
      {/* ── Identity ─────────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="page-title">{title}</h2>
            <CoordinatorChip coordinator={coordinator} />
          </div>
          <p className="mt-1 text-[12.5px] leading-snug text-muted-foreground">
            {subtitle}
          </p>
        </div>

        {/* ── The stopwatch ────────────────────────────────────────
            Top-right, large, monospace and tabular — this is the number the
            two panels are being compared on, so it gets the weight of a
            headline rather than sitting in a footer. It ticks live while
            the run is going, which is also the page's proof that it is
            watching something real and not replaying a recording. */}
        <div className="shrink-0 text-right">
          <p className="metric-lg text-foreground">{formatElapsed(elapsed)}</p>
          <p className="label-caps mt-0.5">
            {live ? "elapsed" : finished ? "total" : "not run yet"}
          </p>
        </div>
      </div>

      {/* ── The control ──────────────────────────────────────────── */}
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
        <Button
          onClick={onRun}
          // Disabled while a run of THIS venue is live, because the API
          // answers a second press with the same job id: pressing again
          // would do nothing and look broken. Disabled before the fleet
          // answers, because there is nothing to run on.
          disabled={disabled || starting || live}
          size="sm"
        >
          {starting ? (
            <Spinner data-icon="inline-start" className="h-3.5 w-3.5" />
          ) : (
            <Play data-icon="inline-start" weight="fill" className="h-3.5 w-3.5" />
          )}
          {live ? "Running…" : finished ? "Run again" : "Run 9 tasks"}
        </Button>

        {run?.state ? (
          // `StateBadge` types its prop as `JobState` but resolves an
          // unrecognised state to a neutral treatment at runtime (see its
          // `?? ` fallback), which is exactly the behaviour this page wants
          // for a control plane that grows a state string this build has
          // never seen.
          <StateBadge state={run.state as JobState} />
        ) : null}

        {run ? (
          <span className="meta truncate" title={run.job_id}>
            {run.job_id}
          </span>
        ) : null}
      </div>

      {/* ── The grid ─────────────────────────────────────────────── */}
      <div className="mt-4">
        <TaskGrid fleet={fleet} run={run} />
      </div>

      {/* ── Artifacts ────────────────────────────────────────────────
          What the run actually produced. Absent until there is something to
          list: an empty "Artifacts" heading over nothing reads as a broken
          panel, where no heading at all correctly says the run has not got
          there yet. */}
      {run && run.artifacts.length > 0 && (
        <div className="mt-4 border-t border-border pt-3">
          <p className="label-caps">Artifacts produced</p>
          <ul className="mt-1.5 divide-y divide-border">
            {run.artifacts.map((artifact) => (
              <li
                key={artifact.name}
                className="flex items-baseline justify-between gap-3 py-1.5"
              >
                <span className="min-w-0 truncate font-mono text-[11.5px] text-foreground">
                  {artifact.name}
                </span>
                <span className="meta shrink-0">{formatBytes(artifact.bytes)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
