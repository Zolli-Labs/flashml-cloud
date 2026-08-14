// Derivations over the real coordinator event ledger.
//
// Nothing here invents data. Every value is read out of events the
// coordinator actually recorded, and where the ledger cannot answer a
// question the functions return null so the UI can say "unknown" instead of
// guessing. That matters more than usual on this product: the console's
// whole claim is that it tells you the truth about a run.
//
// Why derive at all: the coordinator's /tasks view is current state only
// (task_id, state, attempts, node_id, deadline). It carries no attempt
// history and no `accepted` flag. The ledger does carry both, as a sequence
// of LEASE_CLAIMED / TASK_COMMIT_ACCEPTED / LEASE_EXPIRED / ... events with
// {task_id, node_id} in `data`. So placement history is reconstructed here
// rather than added to a coordinator endpoint we do not own.

import type { JobEvent, JobTask } from "@/lib/cloud-api";

export type AttemptOutcome =
  | "running"
  | "accepted"
  | "rejected"
  | "failed"
  | "expired";

export interface Attempt {
  taskId: string;
  nodeId: string;
  startedAt: number;
  /** null while still running. */
  endedAt: number | null;
  outcome: AttemptOutcome;
  round?: number;
}

function str(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

function ms(iso: string): number {
  const t = Date.parse(iso);
  return Number.isNaN(t) ? 0 : t;
}

/** Events that close an open attempt, and what they close it as.
 *
 * Exported because `lib/task-detail.ts` reconstructs the same attempts at a
 * different granularity (one task, including the events with no `node_id`
 * that the fleet-wide walk below drops) and the two must agree on what
 * "accepted" means by construction rather than by two copies of this table
 * drifting apart. */
export const ATTEMPT_CLOSERS: Record<string, AttemptOutcome> = {
  TASK_COMMIT_ACCEPTED: "accepted",
  TASK_COMMIT_REJECTED: "rejected",
  TASK_ATTEMPT_FAILED: "failed",
  TASK_ATTEMPT_COMPLETED: "accepted",
  LEASE_EXPIRED: "expired",
};

/**
 * Reconstruct per-attempt history: who held which task, when, and how it
 * ended.
 *
 * LEASE_CLAIMED opens an attempt keyed by task+node. The first closing event
 * for that key closes it. Keying on task+node rather than task alone matters:
 * after a lease expires the task is claimed elsewhere, and both attempts are
 * real and must both appear. That is the entire point of the view.
 *
 * An attempt with no closing event is left `running` with `endedAt: null`.
 * The UI draws those to "now" rather than pretending they finished.
 */
export function deriveAttempts(events: JobEvent[]): Attempt[] {
  const open = new Map<string, Attempt>();
  const done: Attempt[] = [];

  for (const e of events) {
    const taskId = str(e.data?.task_id);
    const nodeId = str(e.data?.node_id);
    if (!taskId || !nodeId) continue; // job-level event, no placement
    const key = `${e.round ?? ""}|${taskId}|${nodeId}`;

    if (e.type === "LEASE_CLAIMED") {
      // A re-claim by the same node on the same task closes the previous
      // one as expired: the coordinator only re-issues after a sweep.
      const prev = open.get(key);
      if (prev) {
        prev.endedAt = ms(e.timestamp);
        prev.outcome = "expired";
        done.push(prev);
      }
      open.set(key, {
        taskId,
        nodeId,
        startedAt: ms(e.timestamp),
        endedAt: null,
        outcome: "running",
        ...(e.round !== undefined ? { round: e.round } : {}),
      });
      continue;
    }

    const outcome = ATTEMPT_CLOSERS[e.type];
    if (!outcome) continue;
    const cur = open.get(key);
    if (!cur) continue; // a close with no open: ledger truncated, skip
    cur.endedAt = ms(e.timestamp);
    cur.outcome = outcome;
    done.push(cur);
    open.delete(key);
  }

  return [...done, ...open.values()].sort((a, b) => a.startedAt - b.startedAt);
}

/** Distinct nodes that appear in the ledger, in first-seen order. This is
 * the lane order for the placement view; sorting alphabetically would make
 * lanes jump around as new machines join. */
export function nodesFromAttempts(attempts: Attempt[]): string[] {
  const seen: string[] = [];
  for (const a of attempts) if (!seen.includes(a.nodeId)) seen.push(a.nodeId);
  return seen;
}

export interface Progress {
  /** Tasks whose work was ACCEPTED. The only monotonic progress number, and
   * the one hard rule 4 says to distinguish from merely attempted. */
  accepted: number;
  total: number;
  /** Every attempt ever started, accepted or not. The gap between this and
   * `accepted` is what unreliable supply costs. */
  attemptsSpent: number;
  /** Attempts that ended without producing accepted work. */
  wasted: number;
}

export function deriveProgress(
  tasks: JobTask[],
  attempts: Attempt[]
): Progress {
  const accepted = tasks.filter((t) => t.state === "COMPLETED").length;
  const wasted = attempts.filter(
    (a) => a.outcome === "expired" || a.outcome === "failed" || a.outcome === "rejected"
  ).length;
  return {
    accepted,
    total: tasks.length,
    attemptsSpent: attempts.length,
    wasted,
  };
}

export interface AttemptCoverage {
  /** Where the number on screen came from. */
  source: "ledger" | "tasks" | "none";
  /** Attempts spent, from whichever source could answer. */
  spent: number;
  /** How many tasks that total is spread across. Zero for `"ledger"`, which
   * counts reconstructed attempts rather than tasks. */
  tasks: number;
  /** Set ONLY for `"tasks"` — the fallback needs to say it is a fallback.
   * Null otherwise: the ledger answering is the normal case and does not
   * need a sentence about itself. */
  note: string | null;
}

/**
 * The attempt count the Placement view can honestly show, and where it came
 * from.
 *
 * `deriveAttempts` above drops every event that carries no `node_id`, which
 * is correct for the per-machine views — an attempt with no machine has no
 * lane to draw it in. But when the ledger carries ONLY such events, the
 * swimlanes, the topology and the progress bar's "attempts spent" all go to
 * zero at once, and the Placement view then says nothing about attempts at
 * all while the task table beside it plainly shows `3/4`. That reads as a
 * console that has lost the run.
 *
 * The coordinator's task view carries its own tally, so the fallback is a
 * different SOURCE for the same question rather than a guess. It is used only
 * when the ledger produced nothing, and it says which of the two it is —
 * silently swapping sources is how two numbers that disagree end up on one
 * page with no explanation.
 */
export function deriveAttemptCoverage(
  tasks: JobTask[],
  attempts: Attempt[]
): AttemptCoverage {
  if (attempts.length > 0) {
    return { source: "ledger", spent: attempts.length, tasks: 0, note: null };
  }
  const spent = tasks.reduce((n, t) => n + (t.attempts ?? 0), 0);
  if (spent === 0) return { source: "none", spent: 0, tasks: 0, note: null };
  return {
    source: "tasks",
    spent,
    tasks: tasks.length,
    note: `${spent} attempt${spent === 1 ? "" : "s"} across ${tasks.length} task${
      tasks.length === 1 ? "" : "s"
    }, counted by the coordinator. No event in this job's ledger could be placed on a machine, so there is no per-machine history to draw from it — open a task below for what the ledger does say.`,
  };
}

/**
 * Why a running job is not making progress, or null if it is.
 *
 * This exists because the most valuable thing the console can say is not
 * "running" but "running, and here is what it is waiting on". In particular
 * RECOVERY_FROZEN is the policy deliberately refusing to act during a
 * correlated incident, which is the system behaving *well*; without a line
 * of UI it reads as a hang and the user concludes the product is broken.
 *
 * Returns null when there is nothing to report. Never guesses: each branch
 * is backed by a specific ledger fact or task state.
 */
export function deriveStallReason(
  state: string,
  tasks: JobTask[],
  events: JobEvent[],
  now: number = Date.now()
): string | null {
  if (state !== "RUNNING" && state !== "RECOVERING") return null;

  // Most recent first. RECOVERY_FROZEN outranks everything: automation has
  // stopped on purpose and nothing else explains the silence as well.
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].type === "RECOVERY_FROZEN") {
      return (
        events[i].message ||
        "Recovery is frozen: correlated failures tripped the policy and automatic retries have stopped."
      );
    }
  }

  if (tasks.length === 0) return null;

  const leased = tasks.filter((t) => t.state === "LEASED");
  const pending = tasks.filter((t) => t.state === "PENDING");

  if (leased.length === 0 && pending.length > 0) {
    return `${pending.length} task${pending.length === 1 ? "" : "s"} waiting to be claimed. No machine has picked them up.`;
  }

  // Everything in flight, and the soonest deadline is still far out: not
  // stuck, just slow. Say so rather than leaving a blank.
  const deadlines = leased
    .map((t) => (t.deadline ? Date.parse(t.deadline) : NaN))
    .filter((d) => !Number.isNaN(d));
  if (deadlines.length > 0) {
    const soonest = Math.min(...deadlines);
    const secs = Math.round((soonest - now) / 1000);
    if (secs > 0) {
      return `${leased.length} task${leased.length === 1 ? "" : "s"} in flight. Next lease deadline in ${secs < 60 ? `${secs}s` : `${Math.round(secs / 60)}m`}.`;
    }
  }

  return null;
}
