"use client";

import { CheckCircle, Play } from "@phosphor-icons/react";

import { StateBadge } from "@/components/jobs/StateBadge";
import { TaskGrid } from "@/components/demo/TaskGrid";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import {
  formatElapsed,
  isMyGuest,
  isTerminalRun,
  runElapsedSeconds,
  tallyTasks,
  type DemoMachine,
  type DemoRun,
  type JoinedMachine,
} from "@/lib/demo";
import type { JobState } from "@/lib/cloud-api";

/**
 * "Run on my machine" — one task, seconds long, on hardware the judge owns.
 *
 * ONE TASK IS THE POINT, not a smaller version of the nine-task sweep. The
 * moment being staged is "the machine I just plugged in did work on this
 * network", and the shortest path from pressing a button to seeing your own
 * machine against a finished task is one task on one laptop. The parallelism
 * argument is already made above, on hardware guaranteed to be there.
 *
 * So this panel is deliberately NOT `RunPanel`. It has no venue chip and no
 * venue to choose — the guest job always goes to the default coordinator,
 * because asking one laptop to run the same task twice on two control planes
 * would say nothing about either. It reuses the same `TaskGrid`, stopwatch
 * and state badge so it reads as the same page, and adds the one thing the
 * venue panels have no reason to: a completion line that says, in words,
 * that this machine did the work.
 */
export function GuestRunPanel({
  guests,
  run,
  joined,
  now,
  starting,
  error,
  onRun,
}: {
  guests: DemoMachine[];
  run: DemoRun | null;
  joined: JoinedMachine | null;
  now: number;
  starting: boolean;
  error: string | null;
  onRun: () => void;
}) {
  const elapsed = runElapsedSeconds(run, now);
  const live = run !== null && !isTerminalRun(run.state);
  const finished = run !== null && isTerminalRun(run.state);
  const tally = tallyTasks(run?.tasks ?? []);

  // Nobody has joined, so `run-mine` would 503. Better to say why the button
  // is inert than to let a judge press it and read an error they could have
  // been spared.
  const nobodyJoined = guests.length === 0;

  /** Did a machine THIS visitor joined actually execute a task in this run?
   * The difference between "a task finished" and "YOUR machine finished a
   * task" is the entire point of this half of the page, so it is checked
   * rather than assumed — somebody else's laptop may well be the one that
   * claimed the work. */
  const mineDidWork =
    run !== null &&
    joined !== null &&
    run.tasks.some(
      (task) => task.machine !== null && isMyGuest({ name: task.machine }, joined)
    );

  return (
    <section className="panel flex flex-col p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="page-title">Run one task on the guest machines</h3>
          <p className="mt-1 text-[12.5px] leading-snug text-muted-foreground">
            A single task, a few seconds long, scoped so it can only land on a
            machine a visitor joined — never on ours.
          </p>
        </div>
        <div className="shrink-0 text-right">
          <p className="metric-lg text-foreground">{formatElapsed(elapsed)}</p>
          <p className="label-caps mt-0.5">
            {live ? "elapsed" : finished ? "total" : "not run yet"}
          </p>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-3">
        <Button onClick={onRun} disabled={nobodyJoined || starting || live} size="sm">
          {starting ? (
            <Spinner data-icon="inline-start" className="h-3.5 w-3.5" />
          ) : (
            <Play data-icon="inline-start" weight="fill" className="h-3.5 w-3.5" />
          )}
          {live ? "Running…" : finished ? "Run again" : "Run on my machine"}
        </Button>

        {run?.state ? <StateBadge state={run.state as JobState} /> : null}

        {run ? (
          <span className="meta truncate" title={run.job_id}>
            {run.job_id}
          </span>
        ) : null}

        {nobodyJoined && (
          <span className="meta">join a machine above to enable this</span>
        )}
      </div>

      {error && (
        <p className="mt-3 rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-[13px] leading-relaxed text-destructive">
          {error}
        </p>
      )}

      {/* ── The moment ────────────────────────────────────────────────────
          A judge should not have to read a task grid to learn that their own
          laptop just did work on somebody else's network. This says it. */}
      {finished && tally.done > 0 && (
        <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-evergreen/40 bg-evergreen/[0.06] px-3 py-2.5 text-[13px] text-foreground">
          <CheckCircle
            aria-hidden="true"
            weight="fill"
            className="h-4 w-4 shrink-0 text-evergreen"
          />
          <span>
            {mineDidWork ? (
              <>
                <span className="font-medium">Your machine</span> ran this task
                and returned the result in{" "}
                <span className="metric-value font-semibold">
                  {formatElapsed(elapsed)}
                </span>
                .
              </>
            ) : (
              <>
                A guest machine ran this task and returned the result in{" "}
                <span className="metric-value font-semibold">
                  {formatElapsed(elapsed)}
                </span>
                .
              </>
            )}
          </span>
        </p>
      )}

      {/* The grid over the GUEST fleet, not ours — one lane per joined
          machine, so a judge sees their own handle holding the task. */}
      <div className="mt-4">
        <TaskGrid fleet={guests} run={run} />
      </div>
    </section>
  );
}
