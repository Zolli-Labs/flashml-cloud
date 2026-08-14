/** What one task's expanded row says, decided here.
 *
 * WHY THIS EXISTS. The Placement view's Tasks table drew five columns of
 * current state and nothing else. Clicking a row did nothing at all — which
 * is how it was reported: "bugs when I click into the tasks". There was no
 * bug to find, because there was no behaviour: the rows were plain `<tr>`s.
 * A task that burned three attempts across three machines showed `3/4` in one
 * cell and left the story of those three attempts unreadable.
 *
 * The story is already in the ledger. `lib/job-activity.ts` reconstructs it
 * FLEET-WIDE, keyed `round|task_id|node_id`, which is the right key for the
 * swimlanes (one lane per machine) and the wrong one for this: it drops every
 * event that carries no `node_id` (`deriveAttempts` :72), and those are
 * exactly the events that explain the gaps — TASK_CREATED before anything has
 * claimed, TASK_REQUEUED between one machine losing a lease and the next
 * picking it up. Fleet-wide those are noise. Inside one task they are the
 * plot, so this module keeps them in an explicit bucket rather than dropping
 * them silently.
 *
 * DECISIONS LIVE HERE, MARKUP LIVES IN THE COMPONENT — the same split
 * `lib/task-checkpoints.ts` and `components/jobs/placement-summary.ts`
 * document, for the same reason: `vitest.config.ts` collects only
 * `*.test.{ts,tsx}` and the `.tsx` components in this repo have no tests, so
 * anything that could be wrong belongs where a test can reach it.
 *
 * TWO RULES THIS MODULE WILL NOT BEND.
 *
 * 1. Node ids are rendered VERBATIM and are never looked up. An `fn-*` id is
 *    a lease holder in the coordinator's ledger, not a row in this account's
 *    machine list — the two namespaces overlap by coincidence at best.
 *    Resolving one against the other would attribute a stranger's work to a
 *    user's own machine, so nothing here returns anything but the string the
 *    coordinator wrote.
 * 2. Nothing is invented to fill a hole. A task the ledger never names gets
 *    an honest empty state that says so and says what the coordinator's own
 *    tally is; it does not get a blank panel, and it does not get an attempt
 *    reconstructed from the task view's `attempts` count.
 */

import type { JobEvent, JobTask } from "./cloud-api";
import { ATTEMPT_CLOSERS, type AttemptOutcome } from "./job-activity";
import type { CheckpointPanel, TaskCheckpointRow } from "./task-checkpoints";

/** `LEASE_CLAIMED` opens an attempt; `LEASE_RENEWED` extends the open one.
 * Neither is spelled anywhere else in this module. */
const CLAIM = "LEASE_CLAIMED";
const RENEWAL = "LEASE_RENEWED";

function str(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

/** Milliseconds, or null for anything that is not a parseable instant.
 *
 * NULL, not 0 and not `new Date(null)` — which is the Unix epoch and renders
 * as a confident "01/01/1970". Everything downstream carries the null through
 * and the component prints an em dash. */
export function parseTimestamp(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms;
}

/** How an attempt ended, as far as the ledger says.
 *
 * `superseded` is this module's own and is not in `AttemptOutcome`: another
 * machine claimed the task while this attempt was still open, so it certainly
 * ended, and the ledger never said how. Calling that `expired` would put a
 * specific coordinator action in the ledger's mouth. */
export type TaskAttemptOutcome = AttemptOutcome | "superseded";

export interface TaskTimelineAttempt {
  kind: "attempt";
  /** 1-based, in claim order. The reader's "attempt 2 of 3". */
  index: number;
  /** The coordinator's string, verbatim. Never resolved to a machine. */
  nodeId: string;
  claimedAt: number | null;
  /** How many times this lease was renewed. A count, not a list: 23 renewals
   * are 23 identical rows saying the machine was still alive. */
  renewals: number;
  /** Null while the attempt is still open. */
  endedAt: number | null;
  outcome: TaskAttemptOutcome;
  /** The event type that closed it, verbatim, or null if nothing did. */
  closedBy: string | null;
}

/** A task-scoped event that belongs to no attempt.
 *
 * Two ways to get here, and the panel distinguishes them because they mean
 * different things: `no-node` is an event the coordinator recorded about the
 * task itself (TASK_CREATED, TASK_REQUEUED), and `orphan` is an event that
 * names a machine but arrived with no matching open attempt — a truncated or
 * out-of-order ledger. Neither is dropped. */
export interface TaskTimelineNote {
  kind: "note";
  at: number | null;
  /** Verbatim event type. Never mapped onto a friendlier vocabulary. */
  type: string;
  message: string;
  nodeId: string | null;
  reason: "no-node" | "orphan";
}

export type TaskTimelineEntry = TaskTimelineAttempt | TaskTimelineNote;

export interface TaskTimeline {
  taskId: string;
  /** Attempts and notes interleaved, oldest first. */
  entries: TaskTimelineEntry[];
  attempts: TaskTimelineAttempt[];
  notes: TaskTimelineNote[];
  /** Ledger events that named this task, however they were classified. */
  eventCount: number;
  empty: boolean;
  /** Set only when `empty`. Says what is missing and what the coordinator
   * counts anyway, so an empty panel is a statement rather than a blank. */
  emptyReason: string | null;
}

/** The identity a task keeps across polls: round and id, never an array
 * index. The Tasks table holds "which row is open" in React state next to a
 * list that is replaced every 2.5s, so the key has to survive that — an index
 * would slide a reader's open panel onto a different task the moment the
 * coordinator reordered its answer. */
export function taskKey(task: JobTask): string {
  return `${task.round ?? ""}|${task.task_id}`;
}

/** Every ledger event that names this task.
 *
 * Round is matched only when the TASK carries one. A federated job is one
 * coordinator job per round and its task ids repeat across rounds, so
 * ignoring the round there would merge three rounds of the same task id into
 * one story. An independent job has no round on either side and matches on
 * the id alone. */
export function eventsForTask(events: JobEvent[], task: JobTask): JobEvent[] {
  return events.filter(
    (e) =>
      str(e.data?.task_id) === task.task_id &&
      (task.round === undefined || e.round === task.round)
  );
}

/**
 * Reconstruct one task's attempt history from the ledger.
 *
 * ORDER IS TAKEN FROM THE TIMESTAMPS, not from the array. The fleet-wide
 * `deriveAttempts` walks the list as given; here the events are sorted by
 * their own timestamps first, because a per-task panel reads as a narrative
 * and a claim rendered after the expiry it preceded is a wrong narrative. The
 * sort is stable and events with an unparseable timestamp keep their original
 * relative order at the end rather than being dropped or dated.
 *
 * `LEASE_CLAIMED` opens an attempt. Renewals and closing events attach to the
 * open one when they name the same machine — or name none, which is how the
 * coordinator records a task-scoped completion. Everything else becomes a
 * note. A claim arriving while an attempt is still open closes that attempt
 * as `superseded`.
 */
export function deriveTaskTimeline(
  events: JobEvent[],
  task: JobTask
): TaskTimeline {
  const scoped = eventsForTask(events, task);

  const ordered = scoped
    .map((e, i) => ({ e, i, at: parseTimestamp(e.timestamp) }))
    .sort((a, b) => {
      if (a.at === null && b.at === null) return a.i - b.i;
      if (a.at === null) return 1;
      if (b.at === null) return -1;
      return a.at === b.at ? a.i - b.i : a.at - b.at;
    });

  const entries: TaskTimelineEntry[] = [];
  const attempts: TaskTimelineAttempt[] = [];
  const notes: TaskTimelineNote[] = [];
  let open: TaskTimelineAttempt | null = null;

  const note = (
    e: JobEvent,
    at: number | null,
    nodeId: string | null,
    reason: TaskTimelineNote["reason"]
  ) => {
    const entry: TaskTimelineNote = {
      kind: "note",
      at,
      type: e.type,
      message: e.message,
      nodeId,
      reason,
    };
    notes.push(entry);
    entries.push(entry);
  };

  for (const { e, at } of ordered) {
    const nodeId = str(e.data?.node_id);

    if (e.type === CLAIM) {
      // A claim with no machine named cannot open an attempt — there is
      // nothing to attribute it to. It is still the ledger saying something
      // happened to this task, so it is kept as a note.
      if (nodeId === null) {
        note(e, at, null, "no-node");
        continue;
      }
      if (open) {
        open.endedAt = at;
        open.outcome = "superseded";
      }
      open = {
        kind: "attempt",
        index: attempts.length + 1,
        nodeId,
        claimedAt: at,
        renewals: 0,
        endedAt: null,
        outcome: "running",
        closedBy: null,
      };
      attempts.push(open);
      entries.push(open);
      continue;
    }

    if (e.type === RENEWAL) {
      if (open && (nodeId === null || nodeId === open.nodeId)) {
        open.renewals += 1;
        continue;
      }
      note(e, at, nodeId, nodeId === null ? "no-node" : "orphan");
      continue;
    }

    const outcome = ATTEMPT_CLOSERS[e.type];
    if (outcome && open && (nodeId === null || nodeId === open.nodeId)) {
      open.endedAt = at;
      open.outcome = outcome;
      open.closedBy = e.type;
      open = null;
      continue;
    }

    note(e, at, nodeId, nodeId === null ? "no-node" : "orphan");
  }

  const empty = entries.length === 0;
  return {
    taskId: task.task_id,
    entries,
    attempts,
    notes,
    eventCount: scoped.length,
    empty,
    emptyReason: empty ? emptyReason(task, events) : null,
  };
}

/** Why there is nothing to draw, said accurately.
 *
 * Three different absences, and they are not the same sentence. An empty
 * event list is a read that has not happened; a ledger with events but none
 * for this task, on a task the coordinator says has been attempted, is a gap
 * in what this console can see; and a task with no events and no attempts has
 * genuinely not been touched. */
function emptyReason(task: JobTask, events: JobEvent[]): string {
  if (events.length === 0) {
    return "No events have been read for this job, so there is no history to show for this task yet.";
  }
  if (task.attempts > 0) {
    const n = task.attempts;
    return `The ledger carries no event naming ${task.task_id}, but the coordinator counts ${n} attempt${n === 1 ? "" : "s"} on it. That is a gap in the events this console can read, not a task nothing has run.`;
  }
  return `The ledger carries no event naming ${task.task_id}, and the coordinator counts no attempt on it. Nothing has claimed this task yet.`;
}

/** The checkpoint row for one task, or null.
 *
 * Null whenever the checkpoint panel is not in its `rows` state: `settled`,
 * `unreadable` and `absent` are all statements about the job as a whole and
 * none of them is per-task evidence. The card above the table already says
 * which of them it is; repeating a paraphrase of it inside every expanded row
 * would be three chances to word it differently. */
export function checkpointRowFor(
  panel: CheckpointPanel | null | undefined,
  task: JobTask
): TaskCheckpointRow | null {
  if (!panel || panel.state !== "rows") return null;
  return panel.rows.find((r) => r.taskId === task.task_id) ?? null;
}

export interface TaskDetailPanel {
  taskId: string;
  /** Federated only; null for an independent job's task. */
  round: number | null;
  state: JobTask["state"];
  attempts: number;
  maxAttempts: number;
  /** The live lease deadline in ms, or null when there is no lease.
   * Parsed here so no component can reach `new Date(null)`. */
  deadlineMs: number | null;
  /** Whoever holds it now, or last held it, verbatim. Never resolved. */
  nodeId: string | null;
  timeline: TaskTimeline;
  checkpoint: TaskCheckpointRow | null;
  /** Attempts the ledger can actually account for. */
  ledgerAttempts: number;
  /** Set only when that number and the coordinator's disagree. Neither is
   * corrected to match the other: they are two different counts and the
   * panel shows both. */
  attemptGapNote: string | null;
}

export function deriveTaskDetail({
  task,
  events,
  checkpoints,
}: {
  task: JobTask;
  events: JobEvent[];
  checkpoints?: CheckpointPanel | null;
}): TaskDetailPanel {
  const timeline = deriveTaskTimeline(events, task);
  const ledgerAttempts = timeline.attempts.length;

  let attemptGapNote: string | null = null;
  if (!timeline.empty && ledgerAttempts !== task.attempts) {
    attemptGapNote =
      ledgerAttempts < task.attempts
        ? `The coordinator counts ${task.attempts} attempt${task.attempts === 1 ? "" : "s"} on this task; the ledger this console reads accounts for ${ledgerAttempts}. The rest were never recorded as lease claims here.`
        : `The ledger records ${ledgerAttempts} lease claim${ledgerAttempts === 1 ? "" : "s"} for this task; the coordinator's own tally is ${task.attempts}. The two count different things and neither is adjusted to match the other.`;
  }

  return {
    taskId: task.task_id,
    round: task.round ?? null,
    state: task.state,
    attempts: task.attempts,
    maxAttempts: task.max_attempts,
    deadlineMs: parseTimestamp(task.deadline),
    nodeId: task.node_id,
    timeline,
    checkpoint: checkpointRowFor(checkpoints, task),
    ledgerAttempts,
    attemptGapNote,
  };
}
