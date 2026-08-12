/** Grouping a job's flat artifact list by the task that produced each one,
 * and picking out the conventionally-named stdout/stderr files among them.
 *
 * Why this exists: a failed task used to show a truncated reason in the
 * event ledger and nothing else. A parallel change makes each task write
 * its full stdout and stderr into its own output directory, so they arrive
 * here as ordinary job artifacts — no new route, just more rows in the same
 * list `ArtifactsCard` already renders. A flat list of forty files with no
 * indication of which task they came from, or which one is worth opening
 * after a failure, would bury exactly the file someone lands on this page
 * to find. This module does the grouping so the component can stay thin —
 * see `lib/job-result.ts`'s header for why that split exists at all:
 * `vitest.config.ts` only collects `**\/*.test.ts`, so a `.tsx` component
 * gets no test coverage, and any decision that could be wrong belongs where
 * a test can reach it.
 *
 * WHERE THE LIST COMES FROM. `GET /v1alpha1/jobs/{id}/artifacts` — the
 * listing route — and nothing else. It was `job.artifacts` until 2026-08-11,
 * which is always `[]` for every job this deployment runs, so this grouping
 * had been correct and fed nothing since the day it shipped. The listing's
 * rows are `{key, size_bytes}` with the key ALREADY relative to the job's own
 * prefix, so there is no longer a uri to strip a prefix off and no longer a
 * class of row that fails to resolve to a key at all.
 *
 * The exact filenames the task executor writes are not fixed by any
 * contract available here — that change lives in a sibling repo this task
 * was explicitly told not to go read. `classifyLogFilename` below matches
 * on the filename's root ("stdout", "stderr") after stripping one
 * extension, case-insensitively, rather than gambling on one exact spelling
 * like `stdout.log` — so `stdout.log`, `stdout.txt`, `STDOUT`, and a bare
 * `stdout` all resolve the same way, and this keeps working if the
 * extension turns out to differ from a guess made here.
 *
 * The same grouping now also separates CHECKPOINTS from output. Checkpointing
 * is on for every job, so a task that dies mid-run leaves `ckpt/step-*.json`
 * files at its own result keys — a deliberate, accepted consequence, but one
 * that reads as job output unless something says otherwise. Each group
 * therefore carries `results` and `checkpoints` alongside the full
 * `artifacts` list; the classifier itself lives in `lib/task-checkpoints.ts`
 * with the rest of the checkpoint vocabulary.
 */

import type { JobArtifactEntry, JobTask } from "./cloud-api";
import { checkpointStepFromKey } from "./task-checkpoints";

export type LogKind = "stdout" | "stderr";

export interface ArtifactWithKey {
  /** The key relative to this job's own artifact prefix — exactly what the
   * listing returned, and exactly what the download route's `{key}` segment
   * takes. Never re-derived from anything. */
  key: string;
  /** The last path segment of `key`. What the log classifier below matches
   * on — NOT what a download is saved as. That is the whole key with its
   * separators flattened (`lib/artifact-download.ts`'s `artifactFilename`),
   * because every task of a job writes a file called `stdout.txt` and the
   * last segment alone would save twenty of them over each other. */
  filename: string;
  /** The file's size as the API reported it, or null when it reported
   * something that is not a finite number. Null is rendered as unknown, not
   * as 0: a zero-byte file and a size we could not read are different facts
   * and a total built from them would be wrong in the second case. */
  sizeBytes: number | null;
  /** `"stdout"` or `"stderr"` when the filename matches one of those
   * conventions, else null. Null is the common case: most artifacts are
   * ordinary output, not a log. */
  logKind: LogKind | null;
  /** The step this file is a checkpoint of, or null for anything that is
   * not one — see `checkpointStepFromKey`. Now that checkpointing is on for
   * every job, a task that dies mid-run leaves its `ckpt/step-*.json` files
   * at its own result keys, and an unlabelled list renders them beside the
   * job's actual output as if they were part of the deliverable. They are
   * not: they are the machinery that made the run survivable. */
  checkpointStep: number | null;
}

export interface ArtifactGroup {
  /** The key's first path segment. For an ordinary task this is a task id;
   * for a job-level artifact no task produced (a reducer's merged output,
   * e.g. `reduced/rows.jsonl` — see `lib/job-result.ts`'s `concat` case)
   * it names that bucket instead, and `taskState` is null for exactly that
   * reason: there is no task to have a state. */
  groupId: string;
  /** The matching task's current state from `listJobTasks()`, or null when
   * `groupId` does not name a task at all. */
  taskState: JobTask["state"] | null;
  /** Everything in this group, checkpoints included — what a download-all
   * or a count has to work from. */
  artifacts: ArtifactWithKey[];
  /** The group's artifacts minus its checkpoints: the part that is job
   * output. Ordered exactly as they arrived. */
  results: ArtifactWithKey[];
  /** The checkpoint files, oldest step first. Empty, never absent, for a
   * task that wrote none — which says nothing about whether one is coming;
   * see `lib/task-checkpoints.ts`. */
  checkpoints: ArtifactWithKey[];
  /** The subset of `artifacts` recognised as a log file, stdout ordered
   * before stderr when both exist. Empty, never absent, when this task
   * predates the logging change or genuinely wrote no logs — an older job's
   * tasks will have no log files at all, and that is a normal empty state,
   * not an error to render. */
  logs: ArtifactWithKey[];
  /** True only when the task actually FAILED *and* at least one log file
   * exists to open. A failed task from before this feature shipped has
   * `taskState === "FAILED"` and `logs.length === 0` — this stays false for
   * it rather than promising a log that does not exist. This is the flag a
   * component reads to decide which group to put first and highlight, so
   * "the obvious thing to open" is never a broken promise. */
  hasFailureLog: boolean;
}

/** Matches a filename's root against the two conventional log names,
 * case-insensitively and independent of extension. See the module header
 * for why this does not commit to one exact spelling. */
function classifyLogFilename(filename: string): LogKind | null {
  const stem = filename.toLowerCase().replace(/\.[^./]+$/, "");
  if (stem === "stdout") return "stdout";
  if (stem === "stderr") return "stderr";
  return null;
}

/** stdout before stderr; anything else (there is nothing else today) keeps
 * its relative order. A plain comparator rather than a fixed two-element
 * sort so a third log kind, if one is ever added, does not need this
 * function rewritten to keep working. */
function logOrder(kind: LogKind): number {
  return kind === "stdout" ? 0 : 1;
}

/** The size to carry for one listed file. The contract types `size_bytes` as
 * an integer, so this normally passes it straight through; anything that is
 * not a finite number becomes null so it renders as unknown rather than as a
 * fabricated 0 that a total would then silently absorb. */
function sizeOf(entry: JobArtifactEntry): number | null {
  return typeof entry.size_bytes === "number" &&
    Number.isFinite(entry.size_bytes)
    ? entry.size_bytes
    : null;
}

export function groupArtifactsByTask(
  artifacts: JobArtifactEntry[],
  tasks: JobTask[]
): ArtifactGroup[] {
  const taskStateById = new Map(tasks.map((t) => [t.task_id, t.state]));
  const groupsById = new Map<string, ArtifactGroup>();
  const order: string[] = []; // first-seen order, since Map iteration order already is insertion order — kept explicit for the sort below to read from

  for (const entry of artifacts) {
    const key = entry.key;
    const segments = key.split("/");
    const groupId = segments[0];
    const filename = segments[segments.length - 1];
    const logKind = classifyLogFilename(filename);
    // Matched against the key, not the filename: a checkpoint is identified
    // by its `ckpt/` directory as well as its `step-<n>` name, so a result
    // file that happens to be called `step-3.json` is not relabelled.
    const checkpointStep = checkpointStepFromKey(key);
    const item: ArtifactWithKey = {
      key,
      filename,
      sizeBytes: sizeOf(entry),
      logKind,
      checkpointStep,
    };

    let group = groupsById.get(groupId);
    if (!group) {
      group = {
        groupId,
        taskState: taskStateById.get(groupId) ?? null,
        artifacts: [],
        results: [],
        checkpoints: [],
        logs: [],
        hasFailureLog: false,
      };
      groupsById.set(groupId, group);
      order.push(groupId);
    }
    group.artifacts.push(item);
    if (checkpointStep !== null) group.checkpoints.push(item);
    else group.results.push(item);
    if (logKind !== null) group.logs.push(item);
  }

  const groups = order.map((id) => groupsById.get(id)!);
  for (const group of groups) {
    group.logs.sort((a, b) => logOrder(a.logKind!) - logOrder(b.logKind!));
    // Oldest step first, so the last row is the furthest the task got.
    group.checkpoints.sort((a, b) => a.checkpointStep! - b.checkpointStep!);
    group.hasFailureLog = group.taskState === "FAILED" && group.logs.length > 0;
  }

  // Stable sort (guaranteed by the spec since ES2019): FAILED-task groups
  // move to the front without disturbing the relative order of the rest.
  groups.sort((a, b) => Number(b.hasFailureLog) - Number(a.hasFailureLog));

  return groups;
}
