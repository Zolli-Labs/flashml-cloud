"use client";

import {
  busyMachineCount,
  machineLanes,
  tallyTasks,
  taskPhase,
  UNPLACED_LANE,
  type DemoMachine,
  type DemoRun,
  type DemoTask,
  type MachineLane,
  type TaskPhase,
} from "@/lib/demo";
import { cn } from "@/lib/utils";

/**
 * THE CENTREPIECE. Nine tasks, four machines, one column per machine.
 *
 * The whole page exists to make one claim legible in about five seconds:
 * this is real hardware and it is working in parallel. A table of nine rows
 * with a machine name in a column cannot make that claim — a reader has to
 * scan nine values and hold four names in their head to notice they are
 * distinct. So the machine is the AXIS, not a field: four columns, tasks as
 * tiles inside them, and parallelism is the shape of the thing rather than
 * something you work out.
 *
 * A judge sees it three ways at once, deliberately redundant because the
 * page gets one glance:
 *
 *   1. FOUR COLUMNS LIT AT ONCE. A running tile is solid orange. Four
 *      columns each holding an orange tile is a picture of four machines
 *      executing simultaneously, and it needs no caption.
 *   2. THE COUNTER SAYS IT IN WORDS. "4 of 4 machines working" is the same
 *      fact as a number, for the reader who is listening rather than
 *      looking, and for the screenshot that ends up in a slide.
 *   3. THE LIVE HALO. Each lane whose machine is executing carries the
 *      console's existing `.status-dot[data-state="live"]` — the one halo
 *      `globals.css` reserves for "genuinely live work". Four haloes
 *      pulsing in four headers is motion that means something.
 *
 * EVERY FLEET MACHINE GETS A COLUMN, including an idle one. Rendering only
 * the busy machines would destroy the comparison the grid is making: you
 * cannot see that four of four are working unless four are drawn. See
 * `machineLanes`.
 *
 * NO NEW COLOUR LANGUAGE. Orange is `--brand` (the console's working/active
 * colour), green is `--evergreen` (its success colour), red is
 * `--destructive`, and pending is the same hairline border every other
 * surface uses. A reader who has seen any other page in this console
 * already knows what these mean.
 */

/** The tile treatments, one per phase.
 *
 * SOLID FILLS FOR THE THREE THAT MATTER, hairline for the one that does not.
 * These tiles are ~26px and get read from across a room during a demo, so a
 * tinted background at 15% opacity — the console's usual restraint — is not
 * enough contrast to carry the claim. Pending stays empty and dashed on
 * purpose: it is work that has not happened, and it should recede so the
 * work that IS happening is what the eye lands on.
 */
const PHASE_TILE: Record<TaskPhase, string> = {
  pending:
    "border-dashed border-[var(--z-app-border-strong)] bg-transparent text-[var(--z-app-text-dim)]",
  running: "border-brand bg-brand text-[var(--primary-foreground)]",
  done: "border-evergreen bg-evergreen text-white",
  failed: "border-destructive bg-destructive text-white",
  cancelled: "border-border bg-surface-2 text-[var(--z-app-text-dim)]",
};

/** What each fill means, said once, above the grid. A legend is not
 * decoration here — the colours are the data, and a judge who guesses at
 * them is guessing at the result. */
const LEGEND: { phase: TaskPhase; label: string }[] = [
  { phase: "running", label: "Running" },
  { phase: "done", label: "Done" },
  { phase: "pending", label: "Queued" },
];

/**
 * The short label inside a tile.
 *
 * A tile is 26px and a task id is `trial-000`; the id will not fit and
 * shrinking it until it does produces a grey smudge. The trailing segment is
 * what actually distinguishes one task from another, so that is what is
 * printed — and the full id is on the tile's `title`, so nothing is lost,
 * it is only moved to hover.
 */
export function tileLabel(taskId: string): string {
  const tail = taskId.split(/[-_/]/).pop() ?? taskId;
  // A tail that is itself long (a uuid-shaped id) is truncated rather than
  // allowed to blow the tile's width out and break the grid's alignment.
  return tail.length > 4 ? tail.slice(-4) : tail;
}

function Tile({ task }: { task: DemoTask }) {
  const phase = taskPhase(task);
  return (
    <span
      title={`${task.task_id} — ${task.state || phase}${
        task.outcome ? ` (${task.outcome})` : ""
      }`}
      className={cn(
        "inline-flex h-7 min-w-7 items-center justify-center rounded-sm border px-1 font-mono text-[10px] font-medium tabular-nums transition-colors",
        PHASE_TILE[phase]
      )}
    >
      {tileLabel(task.task_id)}
    </span>
  );
}

function LaneColumn({ lane }: { lane: MachineLane }) {
  const unplaced = lane.machine === null;
  const live = lane.running > 0;

  return (
    <div
      className={cn(
        "flex min-w-0 flex-col rounded-md border p-2.5 transition-colors",
        // The lane a machine is CURRENTLY WORKING IN gets the orange edge.
        // With four columns side by side this is what makes "all four at
        // once" readable in peripheral vision, before any tile is parsed.
        live ? "border-brand/50 bg-brand/[0.04]" : "border-border bg-surface",
        unplaced && "border-dashed"
      )}
    >
      <div className="flex items-center gap-1.5">
        {!unplaced && (
          <span
            className="status-dot"
            // The console's one reserved live treatment. Not decoration:
            // a halo here means this machine is executing right now.
            data-state={live ? "live" : undefined}
            style={{
              background: live
                ? "var(--node-green)"
                : lane.online
                  ? "var(--z-app-text-dim)"
                  : "var(--z-app-border-strong)",
            }}
            aria-hidden="true"
          />
        )}
        <span
          className={cn(
            "min-w-0 flex-1 truncate font-mono text-[10.5px]",
            unplaced
              ? "text-[var(--z-app-text-dim)]"
              : "font-medium text-foreground"
          )}
        >
          {lane.machine ?? UNPLACED_LANE}
        </span>
      </div>

      {/* The tiles. `min-h` keeps an idle lane the same height as a busy
          one, so the four columns stay a grid rather than a ragged skyline
          that redraws every poll. */}
      <div className="mt-2 flex min-h-[3.75rem] flex-wrap content-start gap-1">
        {lane.tasks.map((task) => (
          <Tile key={task.task_id} task={task} />
        ))}
      </div>

      <p className="mt-1.5 border-t border-border pt-1.5 font-mono text-[10px] text-muted-foreground tabular-nums">
        {lane.tasks.length === 0
          ? unplaced
            ? "—"
            : lane.online
              ? "idle"
              : "offline"
          : `${lane.running} running · ${lane.done} done`}
      </p>
    </div>
  );
}

export function TaskGrid({
  fleet,
  run,
}: {
  fleet: DemoMachine[];
  run: DemoRun | null;
}) {
  const lanes = machineLanes(fleet, run);
  const machines = lanes.filter((l) => l.machine !== null).length;
  const busy = busyMachineCount(lanes);
  const tally = tallyTasks(run?.tasks ?? []);

  return (
    <div>
      {/* ── The claim, in words ──────────────────────────────────────
          Same fact as the four lit columns below, stated as a number so it
          survives a screenshot, a projector, and a reader who is being
          talked at rather than looking. */}
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="text-[13px] text-foreground">
          <span className="metric-value text-lg font-semibold text-foreground">
            {busy}
          </span>
          <span className="text-muted-foreground"> of </span>
          <span className="metric-value text-lg font-semibold text-foreground">
            {machines}
          </span>
          <span className="text-muted-foreground">
            {" "}
            machines working right now
          </span>
        </p>
        <p className="meta">
          {tally.total > 0
            ? `${tally.total} tasks · ${tally.done} done · ${tally.running} running · ${tally.pending} queued` +
              (tally.failed > 0 ? ` · ${tally.failed} failed` : "") +
              (tally.cancelled > 0 ? ` · ${tally.cancelled} cancelled` : "")
            : "no tasks yet"}
        </p>
      </div>

      {/* ── The grid ─────────────────────────────────────────────────
          Two columns on a phone, four from `sm` up. Four is not arbitrary:
          it is the fleet, and the whole read is "one column per machine".
          A fifth lane (unplaced work, or a machine the fleet list did not
          carry) wraps onto a second row rather than compressing the four. */}
      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {lanes.map((lane) => (
          <LaneColumn key={lane.machine ?? UNPLACED_LANE} lane={lane} />
        ))}
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1">
        {LEGEND.map(({ phase, label }) => (
          <span
            key={phase}
            className="flex items-center gap-1.5 text-[11px] text-muted-foreground"
          >
            <span
              className={cn(
                "inline-block h-2.5 w-2.5 rounded-[2px] border",
                PHASE_TILE[phase]
              )}
              aria-hidden="true"
            />
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}
