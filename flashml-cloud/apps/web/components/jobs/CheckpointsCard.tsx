"use client";

import { FloppyDisk } from "@phosphor-icons/react";

import type { CheckpointPanel, TaskCheckpointRow } from "@/lib/task-checkpoints";

/**
 * What a machine death would cost this job right now.
 *
 * The console's whole product claim is fault tolerance, and until this card
 * existed a user who clicked into a running job saw nothing about
 * checkpointing at all — while the landing page sold recovery and the
 * sign-in screen promised "leases · checkpoints · deterministic recovery".
 * This is the screen that has to make good on it.
 *
 * Lives here rather than inlined in `app/(console)/jobs/[jobId]/page.tsx`
 * for the reason `MemberCredits` gives: a `page.tsx` may export only a
 * default component plus route config. Every decision it renders is taken in
 * `lib/task-checkpoints.ts`, where a test can reach it; this file is markup
 * and formatting.
 *
 * Nothing here is rendered from anything but a value the API returned. A row
 * that could not be answered says so; it never falls back to a zero, a dash
 * standing in for a step, or a reassuring "no checkpoints needed".
 */
export function CheckpointsCard({ panel }: { panel: CheckpointPanel }) {
  if (panel.state === "absent") return null;

  if (panel.state === "settled") {
    return (
      <Shell>
        <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
          No task is holding uncommitted work: every one has been accepted,
          cancelled, or never claimed by a machine. There is nothing for a
          machine death to cost right now.
        </p>
      </Shell>
    );
  }

  if (panel.state === "unreadable") {
    return (
      <Shell>
        <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
          Checkpoint state could not be read — the API refused this session.
          Signing in again should restore it. This says nothing about whether
          the job is checkpointing, and the console will not guess either way.
        </p>
      </Shell>
    );
  }

  return (
    <Shell>
      <p className="mt-1 text-xs text-muted-foreground">
        The latest checkpoint each task has committed. Work done after it is
        what a machine dying now would cost.
      </p>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[560px] text-left">
          <thead>
            <tr className="border-b border-border">
              {["Task", "Latest checkpoint", "Committed", "Attempts"].map(
                (h) => (
                  <th key={h} className="label-caps px-3 py-2 font-medium">
                    {h}
                  </th>
                )
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {panel.rows.map((row) => (
              <Row key={row.taskId} row={row} />
            ))}
          </tbody>
        </table>
      </div>

      {(panel.resumeNote || panel.ambiguityNote || panel.truncationNote) && (
        <div className="mt-4 space-y-2 border-t border-border pt-3">
          {panel.resumeNote && (
            <p className="max-w-prose text-xs leading-relaxed text-muted-foreground">
              {panel.resumeNote}
            </p>
          )}
          {panel.ambiguityNote && (
            <p className="max-w-prose text-xs leading-relaxed text-muted-foreground">
              {panel.ambiguityNote}
            </p>
          )}
          {panel.truncationNote && (
            <p className="max-w-prose text-xs leading-relaxed text-muted-foreground">
              {panel.truncationNote}
            </p>
          )}
        </div>
      )}
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <FloppyDisk className="h-4 w-4 text-muted-foreground" />
        Checkpoints
      </h2>
      {children}
    </section>
  );
}

function Row({ row }: { row: TaskCheckpointRow }) {
  return (
    <tr>
      <td className="px-3 py-2.5 align-top font-mono text-xs">
        {row.taskId}
        <span className="ml-2 text-muted-foreground">{row.taskState}</span>
        {/* The claim, and directly under it the two facts it rests on, so
            "resumed" is checkable rather than asserted. */}
        {row.resumedFromStep !== null && (
          <div className="mt-1 flex flex-col gap-0.5">
            <span className="w-fit rounded-full border border-evergreen/40 px-1.5 py-0.5 font-mono text-[10px] text-evergreen">
              resumed from step {row.resumedFromStep}
            </span>
            <span className="text-[10px] text-muted-foreground">
              committed {time(row.committedAt)}, this attempt claimed{" "}
              {timeFromMs(row.latestAttemptStartedAt)}
            </span>
          </div>
        )}
      </td>
      <td className="px-3 py-2.5 align-top font-mono text-xs">
        <CheckpointCell row={row} />
      </td>
      <td className="px-3 py-2.5 align-top font-mono text-xs tabular-nums text-muted-foreground">
        {row.kind === "committed" ? time(row.committedAt) : "—"}
      </td>
      <td className="px-3 py-2.5 align-top font-mono text-xs tabular-nums text-muted-foreground">
        {row.attempts}
      </td>
    </tr>
  );
}

function CheckpointCell({ row }: { row: TaskCheckpointRow }) {
  if (row.kind === "committed") {
    return (
      <span className="flex flex-col gap-0.5">
        <span className="metric-value text-[var(--node-green)]">
          step {row.step}
        </span>
        {row.validation && (
          <span className="text-[10px] text-muted-foreground">
            {row.validation}
          </span>
        )}
      </span>
    );
  }
  if (row.kind === "none") {
    return <span className="text-muted-foreground">no checkpoint yet</span>;
  }
  if (row.kind === "reading") {
    return <span className="text-muted-foreground">reading…</span>;
  }
  // "unknown" — the read failed for a reason that is not 404. Saying "none"
  // here would report a gateway blip to a user as their run having lost its
  // resume point.
  return (
    <span className="flex flex-col gap-0.5">
      <span className="text-warning-foreground">unknown</span>
      {row.errorDetail && (
        <span className="text-[10px] break-all text-muted-foreground">
          {row.errorDetail}
        </span>
      )}
    </span>
  );
}

/** Same clock format the rest of this page uses for a coordinator
 * timestamp. Returns an em dash for a value that is absent or unparseable
 * rather than "Invalid Date". */
function time(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? "—" : new Date(ms).toLocaleTimeString();
}

function timeFromMs(ms: number | null): string {
  return ms === null ? "—" : new Date(ms).toLocaleTimeString();
}
