/** Per-task checkpoint state, said honestly.
 *
 * FlashML's headline claim is that a machine dying mid-run costs you the
 * work since the last checkpoint rather than the whole run. Until now the
 * console never showed a checkpoint anywhere: the landing page sells
 * recovery, the sign-in screen says "leases · checkpoints · deterministic
 * recovery", and a user who clicked into a running job saw nothing about
 * either. This module is the decision half of fixing that — the component
 * around it is markup, per the same split `lib/job-result.ts` documents
 * (`vitest.config.ts` only collects `*.test.ts`, so anything that could be
 * wrong belongs where a test can reach it).
 *
 * WHY THE COORDINATOR'S MANIFEST AND NOT THE EVENT LEDGER.
 * `CHECKPOINT_MANIFEST_COMMITTED` is emitted under the *scoped* catalog key
 * `"<job_id>::<task_id>"` (`flashruntime/checkpoint/catalog.py` via
 * `service/checkpoints.py`), while the console's ledger reads query the bare
 * `<job_id>`. Those events can therefore never appear in a job's event feed,
 * and no amount of filtering here would find them. `GET .../checkpoints/
 * latest` is correctly scoped and is the only read that answers the question.
 *
 * WHAT THIS MODULE REFUSES TO DO. A 404 from that route means "no valid
 * checkpoint", and nothing more — it does not distinguish a task that has
 * not reached its first checkpoint from a workload that never writes one,
 * and neither does the copy built from it. Any other failure is `unknown`,
 * never "none": telling somebody their run has no checkpoint because a
 * gateway blipped is exactly the reassurance-shaped lie this product cannot
 * afford. No step, count or timestamp appears here that the API did not
 * return.
 */

import type { JobTask, TaskCheckpointRead } from "./cloud-api";
import type { Attempt } from "./job-activity";

/** The directory flashnode's checkpoint relay ships from. Its executor
 * watches `<workdir>/out/ckpt/` for `step-*.json` and uploads each new file
 * to `{output_prefix}ckpt/{filename}` — so a checkpoint arrives in a job's
 * artifact list as `…/{task_id}/ckpt/step-<n>.json`, sitting next to the
 * task's real output with nothing to tell them apart. */
const CHECKPOINT_DIR = "ckpt";

/** The committed step a checkpoint artifact's key names, or null when the
 * key is not a checkpoint at all.
 *
 * Requires BOTH the `ckpt/` directory segment and a `step-<digits>` stem, so
 * a workload that happens to write `step-3.json` among its results is not
 * relabelled as a checkpoint. Extension-independent and zero-padding
 * tolerant for the same reason `classifyLogFilename` is in
 * `lib/task-artifacts.ts`: the exact spelling is fixed by a sibling
 * repository, not by any contract available here, and mislabelling output as
 * a checkpoint is worse than leaving a checkpoint unlabelled.
 *
 * Fails to `null` — an unrecognised file stays an ordinary artifact. */
export function checkpointStepFromKey(key: string): number | null {
  const segments = key.split("/");
  if (segments.length < 2) return null;
  if (segments[segments.length - 2] !== CHECKPOINT_DIR) return null;
  const stem = segments[segments.length - 1].replace(/\.[^./]+$/, "");
  const match = /^step-(\d+)$/.exec(stem);
  if (!match) return null;
  const step = Number.parseInt(match[1], 10);
  return Number.isFinite(step) ? step : null;
}

/** The most tasks this console will ask about on one poll tick.
 *
 * There is one HTTP call per task and the job page polls every 2.5s, so an
 * unbounded fan-out would put a fleet-wide expiry's worth of requests per
 * tick through the API for as long as a tab stays open. Twenty covers every
 * job this product runs today (`resources.maximumWorkers` bounds what can be
 * in flight); past that the panel says it is truncated rather than quietly
 * showing a subset. */
export const MAX_CHECKPOINT_READS = 20;

/** Task states that can still be holding uncommitted work.
 *
 * COMPLETED is excluded: its output was accepted, so what it did or did not
 * checkpoint costs nobody anything now. CANCELLED likewise. PENDING and
 * FAILED are included only with `attempts > 0` — a task that has never been
 * claimed cannot have committed anything, and asking would be one guaranteed
 * 404 per idle task. */
function couldLoseWork(task: JobTask): boolean {
  if (task.state === "LEASED") return true;
  if (task.state === "PENDING" || task.state === "FAILED") {
    return task.attempts > 0;
  }
  return false;
}

export interface CheckpointSelection {
  /** The task ids to read `checkpoints/latest` for, in task-id order. */
  taskIds: string[];
  /** How many at-risk tasks were left out by `MAX_CHECKPOINT_READS`. */
  skipped: number;
  /** True when this job's tasks carry a round — a federated run is one
   * coordinator job PER ROUND, and the checkpoint route is scoped to a
   * coordinator job id, so the umbrella id this page holds would answer 404
   * for every task. Reading nothing is right; implying "no checkpoints" from
   * it would not be. */
  federated: boolean;
}

/** Which tasks are worth asking the coordinator about, and how many were
 * left out. Exported so the fetcher and the panel agree by construction
 * rather than by two copies of the same rule. */
export function selectTasksForCheckpointRead(
  tasks: JobTask[],
  limit: number = MAX_CHECKPOINT_READS
): CheckpointSelection {
  const federated = tasks.some((t) => t.round !== undefined);
  if (federated) return { taskIds: [], skipped: 0, federated: true };

  const atRisk = tasks
    .filter(couldLoseWork)
    .map((t) => t.task_id)
    .sort();
  return {
    taskIds: atRisk.slice(0, limit),
    skipped: Math.max(0, atRisk.length - limit),
    federated: false,
  };
}

export type CheckpointRowKind =
  /** The coordinator returned a manifest. `step` and `committedAt` are real. */
  | "committed"
  /** 404 — no valid checkpoint. Says nothing about whether one is coming. */
  | "none"
  /** The read failed for any other reason. NOT "none". */
  | "unknown"
  /** Selected, not answered yet. The first render after a poll starts. */
  | "reading";

export interface TaskCheckpointRow {
  taskId: string;
  taskState: JobTask["state"];
  kind: CheckpointRowKind;
  /** The committed step, or null. Never a placeholder, never a 0 standing
   * in for "we don't know" — step 0 is a real checkpoint. */
  step: number | null;
  /** When the coordinator recorded the manifest, ISO, or null. */
  committedAt: string | null;
  /** The manifest's own validation verdict — `hash_verified`,
   * `restore_verified`, or `invalid`. Passed through verbatim: it is the
   * runtime's word for how far this checkpoint got up its ladder, and
   * paraphrasing it would invent confidence. */
  validation: string | null;
  /** The API's own words for why the read failed, for `unknown` only. */
  errorDetail: string | null;
  /** Attempts spent on this task so far, from the coordinator's task view. */
  attempts: number;
  /** When the latest attempt on this task was claimed (ms), or null when the
   * ledger records no lease for it. */
  latestAttemptStartedAt: number | null;
  /** Set only when the ledger and the manifest together support the claim
   * that this task's current attempt was handed a checkpoint to resume
   * from: more than one attempt, and a manifest committed BEFORE the latest
   * attempt was claimed. flashnode reads `checkpoints/latest` at the start
   * of every task and downloads the manifest's part as the run's resume
   * input, so a retry that began after a commit received it.
   *
   * Null whenever any leg is missing — a first attempt, a manifest that
   * postdates the claim (this attempt's own work), or no lease in the
   * ledger. "Restarted" and "resumed" are different claims and this console
   * makes the second one only when it can point at both facts. */
  resumedFromStep: number | null;
}

export type CheckpointPanelState =
  /** `rows` carries per-task detail. */
  | "rows"
  /** The API refused this console's credential for the checkpoint route.
   * A fact about our own plumbing, not about the user's job. */
  | "unreadable"
  /** Nothing is holding uncommitted work — every task is accepted,
   * cancelled, or has never been claimed. */
  | "settled"
  /** No task breakdown to say anything about; the page explains why
   * elsewhere. Render nothing. */
  | "absent";

export interface CheckpointPanel {
  state: CheckpointPanelState;
  rows: TaskCheckpointRow[];
  /** Set when at least one task answered 404, and says exactly what that
   * does and does not mean. */
  ambiguityNote: string | null;
  /** Set when at least one row carries resume evidence, explaining the
   * mechanism the claim rests on. */
  resumeNote: string | null;
  /** Set when `MAX_CHECKPOINT_READS` left tasks out. */
  truncationNote: string | null;
}

const ABSENT: CheckpointPanel = {
  state: "absent",
  rows: [],
  ambiguityNote: null,
  resumeNote: null,
  truncationNote: null,
};

/** The latest lease claim per task, in ms. Read from the same reconstructed
 * attempt history the placement view draws, so the two never disagree. */
function latestClaimByTask(attempts: Attempt[]): Map<string, number> {
  const latest = new Map<string, number>();
  for (const attempt of attempts) {
    const current = latest.get(attempt.taskId);
    if (current === undefined || attempt.startedAt > current) {
      latest.set(attempt.taskId, attempt.startedAt);
    }
  }
  return latest;
}

export function summariseCheckpoints({
  tasks,
  reads,
  attempts,
}: {
  tasks: JobTask[];
  /** By task id. A selected task missing from this map has not answered
   * yet, which is a different row from one that answered 404. */
  reads: Record<string, TaskCheckpointRead>;
  attempts: Attempt[];
}): CheckpointPanel {
  if (tasks.length === 0) return ABSENT;

  const selection = selectTasksForCheckpointRead(tasks);
  if (selection.federated) return ABSENT;
  if (selection.taskIds.length === 0) {
    return { ...ABSENT, state: "settled" };
  }

  // One refusal is the whole story: the route's credential requirement is a
  // property of the API, not of this task, so it will refuse every other
  // read the same way. Saying it once beats twenty identical rows.
  //
  // Read across EVERY answer ever received, not just the currently selected
  // tasks. The page stops asking after the first refusal — retrying a
  // guaranteed 401 per task per tick is not free — so the refused task can
  // later drop out of the selection (it completes, it is cancelled) while no
  // newly selected task ever gets an answer. Scoped to the selection, this
  // would then silently become a table of tasks stuck reading forever.
  if (Object.values(reads).some((read) => read.status === "unreadable")) {
    return { ...ABSENT, state: "unreadable" };
  }

  const byId = new Map(tasks.map((t) => [t.task_id, t]));
  const claims = latestClaimByTask(attempts);
  const rows: TaskCheckpointRow[] = [];

  for (const taskId of selection.taskIds) {
    const task = byId.get(taskId)!;
    const read = reads[taskId];
    const latestAttemptStartedAt = claims.get(taskId) ?? null;

    const base = {
      taskId,
      taskState: task.state,
      step: null,
      committedAt: null,
      validation: null,
      errorDetail: null,
      attempts: task.attempts,
      latestAttemptStartedAt,
      resumedFromStep: null,
    };

    if (read === undefined) {
      rows.push({ ...base, kind: "reading" });
      continue;
    }
    if (read.status === "none") {
      rows.push({ ...base, kind: "none" });
      continue;
    }
    if (read.status === "unknown") {
      rows.push({ ...base, kind: "unknown", errorDetail: read.detail });
      continue;
    }
    if (read.status === "unreadable") {
      // Unreachable: the panel returned above the moment any read reported
      // this. Written out rather than left to the compiler, which cannot
      // follow the `.some()` that already excluded it — and a row is not
      // invented for a task whose state nobody knows.
      continue;
    }

    const manifest = read.manifest;
    const committedMs = Date.parse(manifest.created);
    // Three legs, each independently checkable, and the claim is dropped if
    // any of them is missing rather than softened into a hedge.
    const resumed =
      task.attempts > 1 &&
      latestAttemptStartedAt !== null &&
      Number.isFinite(committedMs) &&
      committedMs < latestAttemptStartedAt;

    rows.push({
      ...base,
      kind: "committed",
      step: manifest.step,
      committedAt: manifest.created,
      validation: manifest.validation,
      resumedFromStep: resumed ? manifest.step : null,
    });
  }

  return {
    state: "rows",
    rows,
    ambiguityNote: rows.some((r) => r.kind === "none")
      ? "A task with no checkpoint has either not written its first one yet or runs a workload that does not checkpoint. The coordinator answers the same in both cases, and this console does not guess between them."
      : null,
    resumeNote: rows.some((r) => r.resumedFromStep !== null)
      ? "A machine asks the coordinator for the task's latest checkpoint before it starts work, so an attempt claimed after a commit was handed that checkpoint to resume from."
      : null,
    truncationNote:
      selection.skipped > 0
        ? `Showing ${selection.taskIds.length} of ${
            selection.taskIds.length + selection.skipped
          } tasks that could still lose work.`
        : null,
  };
}
