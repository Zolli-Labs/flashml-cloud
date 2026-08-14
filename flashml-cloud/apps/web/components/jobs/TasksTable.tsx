"use client";

import { useId, useState } from "react";
import { CaretRight } from "@phosphor-icons/react";

import type { AttemptCoverage } from "@/lib/job-activity";
import type { JobEvent, JobTask } from "@/lib/cloud-api";
import type { CheckpointPanel } from "@/lib/task-checkpoints";
import {
  deriveTaskDetail,
  taskKey,
  type TaskDetailPanel,
  type TaskTimelineAttempt,
  type TaskTimelineNote,
} from "@/lib/task-detail";

/**
 * The Placement view's Tasks table, and the per-task detail that opens
 * underneath a row.
 *
 * WHY IT MOVED OUT OF `page.tsx`. It was five columns of plain `<tr>` there,
 * and clicking a row did nothing — reported as "bugs when I click into the
 * tasks", though there was no bug, only an absence. Adding the detail meant
 * adding state and a derivation, and a `page.tsx` may export only a default
 * component plus route config (see `StateBadge`, which cost a debugging
 * session to that rule). Every judgement below is taken in
 * `lib/task-detail.ts`, where a test can reach it; this file is markup.
 *
 * WHY REACT STATE AND NOT A `<details>`. `components/jobs/Disclosure.tsx`
 * argues for native `<details>` on this page, and is right about the rest of
 * it — but a `<details>` cannot wrap a `<tr>`, and putting one inside a cell
 * would collapse the columns that make the table a table. So the open row is
 * held here, keyed by `taskKey` — round and task id, never an array index —
 * so the 2.5s poll replacing the task list cannot slide a reader's open panel
 * onto a different task.
 *
 * NODE IDS ARE PRINTED AND NEVER RESOLVED. The `fn-*` ids below are lease
 * holders in the coordinator's ledger. They are not rows in this account's
 * machine list, nothing here looks them up, and none of them is a link.
 */
export function TasksTable({
  tasks,
  events,
  checkpoints,
  coverage,
  defaultExpandedKey,
}: {
  tasks: JobTask[];
  events: JobEvent[];
  checkpoints?: CheckpointPanel | null;
  /** Where the attempt total on this page came from. Its note renders only
   * when the ledger could not answer — see `deriveAttemptCoverage`. */
  coverage?: AttemptCoverage | null;
  /** Open one row on first render. For the preview harness, which renders to
   * static HTML and can never click anything. */
  defaultExpandedKey?: string;
}) {
  const [expanded, setExpanded] = useState<string | null>(
    defaultExpandedKey ?? null
  );
  // Instance-scoped, because `aria-labelledby` resolves against the whole
  // document: two of these tables on one page with hand-rolled `task-row-0`
  // ids would point every panel at the FIRST table's row. The preview gallery
  // renders five of them and caught exactly that.
  const uid = useId();

  if (tasks.length === 0) return null;

  return (
    <section className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="border-b border-border px-4 py-2.5">
        <h2 className="text-sm font-semibold">Tasks</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Open a task for the attempts the ledger recorded against it.
        </p>
        {coverage?.note && (
          <p className="mt-1.5 max-w-prose text-xs leading-relaxed text-muted-foreground">
            {coverage.note}
          </p>
        )}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-left">
          <thead>
            <tr className="border-b border-border">
              {["Task", "State", "Attempts", "Machine", "Lease ends"].map(
                (h) => (
                  <th key={h} className="label-caps px-4 py-2 font-medium">
                    {h}
                  </th>
                )
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {tasks.map((task, i) => {
              const key = taskKey(task);
              const open = expanded === key;
              const panelId = `${uid}task-detail-${i}`;
              const buttonId = `${uid}task-row-${i}`;
              const toggle = () => setExpanded(open ? null : key);
              return (
                <Row
                  key={`${key}-${i}`}
                  task={task}
                  events={events}
                  checkpoints={checkpoints}
                  open={open}
                  onToggle={toggle}
                  panelId={panelId}
                  buttonId={buttonId}
                />
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Row({
  task,
  events,
  checkpoints,
  open,
  onToggle,
  panelId,
  buttonId,
}: {
  task: JobTask;
  events: JobEvent[];
  checkpoints?: CheckpointPanel | null;
  open: boolean;
  onToggle: () => void;
  panelId: string;
  buttonId: string;
}) {
  // Derived only while the row is open: this runs inside a 2.5s poll, and a
  // job with twenty tasks would otherwise walk the whole ledger twenty times
  // a tick to build panels nobody has asked for.
  const detail = open
    ? deriveTaskDetail({ task, events, checkpoints })
    : null;

  return (
    <>
      <tr
        onClick={onToggle}
        className={`cursor-pointer transition-colors hover:bg-surface-2 ${
          open ? "bg-surface-2" : ""
        }`}
      >
        <td className="px-4 py-2.5 font-mono text-xs">
          {/* The keyboard path, and the accessible name of the panel below.
              The row's own onClick is mouse convenience on top of it; the
              button stops the click bubbling so one press is one toggle. */}
          <button
            type="button"
            id={buttonId}
            aria-expanded={open}
            aria-controls={open ? panelId : undefined}
            onClick={(e) => {
              e.stopPropagation();
              onToggle();
            }}
            className="inline-flex items-center gap-1.5 rounded-sm text-left transition-colors hover:text-brand-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            <CaretRight
              className={`h-3 w-3 shrink-0 transition-transform ${
                open ? "rotate-90" : ""
              }`}
              weight="bold"
            />
            {task.round !== undefined && (
              <span className="text-muted-foreground">r{task.round}/</span>
            )}
            {task.task_id}
          </button>
        </td>
        <td className="px-4 py-2.5 font-mono text-xs">{task.state}</td>
        <td className="px-4 py-2.5 font-mono text-xs tabular-nums">
          {task.attempts}/{task.max_attempts}
        </td>
        <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
          {task.node_id ?? "—"}
        </td>
        <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
          {task.deadline ? clock(Date.parse(task.deadline)) : "—"}
        </td>
      </tr>
      {open && detail && (
        <tr>
          <td colSpan={5} className="bg-surface-2 px-4 py-4">
            <div id={panelId} role="region" aria-labelledby={buttonId}>
              <TaskDetail panel={detail} />
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function TaskDetail({ panel }: { panel: TaskDetailPanel }) {
  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <TaskStateChip state={panel.state} />
          {/* "3 of 4 attempts spent", not "attempt 3 of 4": this task has
              FINISHED three attempts, and the second phrasing reads as one in
              progress. Same vocabulary as the progress bar above. */}
          <span className="font-mono text-xs tabular-nums text-muted-foreground">
            {panel.attempts} of {panel.maxAttempts} attempts spent
          </span>
        </div>

        <dl className="mt-3 space-y-1.5 text-xs">
          <Fact label="lease ends">
            {/* Null is an em dash and never `new Date(null)`, which is the
                epoch and renders as a confident 1/1/1970. */}
            {panel.deadlineMs === null ? "—" : clock(panel.deadlineMs)}
          </Fact>
          <Fact label="machine">{panel.nodeId ?? "—"}</Fact>
          {panel.round !== null && <Fact label="round">r{panel.round}</Fact>}
          <Fact label="checkpoint">
            <CheckpointSummary panel={panel} />
          </Fact>
        </dl>

        {panel.attemptGapNote && (
          <p className="mt-3 max-w-prose text-xs leading-relaxed text-muted-foreground">
            {panel.attemptGapNote}
          </p>
        )}
      </div>

      <div>
        <p className="label-caps">Attempt history</p>
        {panel.timeline.empty ? (
          <p className="mt-2 max-w-prose text-xs leading-relaxed text-muted-foreground">
            {panel.timeline.emptyReason}
          </p>
        ) : (
          <ol className="mt-2 space-y-2 border-l border-border pl-3">
            {panel.timeline.entries.map((entry, i) =>
              entry.kind === "attempt" ? (
                <AttemptLine key={`a-${i}`} attempt={entry} />
              ) : (
                <NoteLine key={`n-${i}`} note={entry} />
              )
            )}
          </ol>
        )}
      </div>
    </div>
  );
}

function Fact({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  // A fixed label column rather than `justify-between`. This panel sits
  // inside the table's horizontal scroll region and inherits its 560px
  // minimum, so a right-aligned value parks itself off the edge of a narrow
  // screen while its label stays visible on the left.
  return (
    <div className="grid grid-cols-[6.5rem_minmax(0,1fr)] items-baseline gap-x-3">
      <dt className="label-caps">{label}</dt>
      <dd className="truncate font-mono">{children}</dd>
    </div>
  );
}

/** One attempt: who held it, for how many renewals, and how it ended. */
function AttemptLine({ attempt }: { attempt: TaskTimelineAttempt }) {
  return (
    <li className="text-xs">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-muted-foreground tabular-nums">
          {attempt.index}.
        </span>
        {/* Verbatim. A lease holder id, not a machine this account owns —
            nothing looks it up and it is deliberately not a link. */}
        <span className="font-mono break-all">{attempt.nodeId}</span>
        <OutcomeChip outcome={attempt.outcome} />
      </div>
      <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
        claimed {clock(attempt.claimedAt)}
        {attempt.renewals > 0 &&
          ` · ${attempt.renewals} renewal${attempt.renewals === 1 ? "" : "s"}`}
        {attempt.endedAt !== null && ` · ended ${clock(attempt.endedAt)}`}
        {attempt.closedBy && ` · ${attempt.closedBy}`}
      </div>
    </li>
  );
}

/** A task-scoped event that belongs to no attempt — TASK_CREATED before
 * anything claimed, TASK_REQUEUED between one machine losing the work and the
 * next taking it. The fleet-wide derivation drops these because they carry no
 * machine; inside one task they are what explains the gaps. */
function NoteLine({ note }: { note: TaskTimelineNote }) {
  return (
    <li className="text-xs">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-mono text-muted-foreground">{note.type}</span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {clock(note.at)}
        </span>
        {note.nodeId && (
          <span className="font-mono text-[11px] break-all text-muted-foreground">
            {note.nodeId}
          </span>
        )}
      </div>
      {note.message && (
        <p className="mt-0.5 max-w-prose text-[11px] leading-relaxed text-muted-foreground">
          {note.message}
        </p>
      )}
    </li>
  );
}

/** The same words `CheckpointsCard` uses, because they describe the same
 * read. Nothing here invents a step or turns a failed read into "none". */
function CheckpointSummary({ panel }: { panel: TaskDetailPanel }) {
  const row = panel.checkpoint;
  if (!row) return <span className="text-muted-foreground">—</span>;
  if (row.kind === "committed") {
    return (
      <span className="text-[var(--node-green)]">
        step {row.step}
        {row.validation && (
          <span className="text-muted-foreground"> · {row.validation}</span>
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
  return <span className="text-warning-foreground">unknown</span>;
}

const taskStateStyles: Record<string, string> = {
  PENDING: "text-muted-foreground border-muted",
  LEASED: "text-brand-foreground border-brand/40",
  COMPLETED: "text-evergreen border-evergreen/40",
  FAILED: "text-destructive border-destructive/40",
  CANCELLED: "text-muted-foreground border-muted",
};

function TaskStateChip({ state }: { state: string }) {
  return (
    <span
      className={`w-fit rounded-full border px-2 py-0.5 font-mono text-[11px] ${
        taskStateStyles[state] ?? "text-muted-foreground border-muted"
      }`}
    >
      {state}
    </span>
  );
}

const outcomeStyles: Record<string, string> = {
  accepted: "text-evergreen border-evergreen/40",
  running: "text-brand-foreground border-brand/40",
  expired: "text-warning-foreground border-warning/50",
  superseded: "text-warning-foreground border-warning/50",
  failed: "text-destructive border-destructive/40",
  rejected: "text-destructive border-destructive/40",
};

function OutcomeChip({ outcome }: { outcome: string }) {
  return (
    <span
      className={`w-fit rounded-full border px-1.5 py-0.5 font-mono text-[10px] ${
        outcomeStyles[outcome] ?? "text-muted-foreground border-muted"
      }`}
    >
      {outcome}
    </span>
  );
}

/** The page's clock format for a coordinator timestamp. An em dash for
 * anything that could not be dated — never "Invalid Date", never an epoch. */
function clock(ms: number | null): string {
  return ms === null ? "—" : new Date(ms).toLocaleTimeString();
}
