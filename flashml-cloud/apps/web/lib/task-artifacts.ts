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
 * The exact filenames the task executor writes are not fixed by any
 * contract available here — that change lives in a sibling repo this task
 * was explicitly told not to go read. `classifyLogFilename` below matches
 * on the filename's root ("stdout", "stderr") after stripping one
 * extension, case-insensitively, rather than gambling on one exact spelling
 * like `stdout.log` — so `stdout.log`, `stdout.txt`, `STDOUT`, and a bare
 * `stdout` all resolve the same way, and this keeps working if the
 * extension turns out to differ from a guess made here.
 */

import type { ArtifactRecord, JobTask } from "./cloud-api";
import { jobArtifactKey } from "./cloud-api";

export type LogKind = "stdout" | "stderr";

export interface ArtifactWithKey {
  artifact: ArtifactRecord;
  /** The relative key under this job's own artifact route — what
   * `fetchJobArtifact` and the download link need, not the raw `uri`. */
  key: string;
  /** The last path segment of `key` — what a download offers as a
   * filename. */
  filename: string;
  /** `"stdout"` or `"stderr"` when the filename matches one of those
   * conventions, else null. Null is the common case: most artifacts are
   * ordinary output, not a log. */
  logKind: LogKind | null;
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
  artifacts: ArtifactWithKey[];
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

export interface GroupedArtifacts {
  /** FAILED-task groups first (so the failed task's logs are the obvious
   * thing to open), then every other group in the order its first artifact
   * appeared in the input list. */
  groups: ArtifactGroup[];
  /** Artifacts whose `uri` does not resolve to a key under this job's own
   * prefix at all — `jobArtifactKey` returns null for these (e.g. a staged
   * input-code upload, which is not a result). Kept rather than dropped so
   * nothing an API actually returned silently disappears from the page. */
  unresolved: ArtifactRecord[];
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

export function groupArtifactsByTask(
  jobId: string,
  artifacts: ArtifactRecord[],
  tasks: JobTask[]
): GroupedArtifacts {
  const taskStateById = new Map(tasks.map((t) => [t.task_id, t.state]));
  const groupsById = new Map<string, ArtifactGroup>();
  const order: string[] = []; // first-seen order, since Map iteration order already is insertion order — kept explicit for the sort below to read from
  const unresolved: ArtifactRecord[] = [];

  for (const artifact of artifacts) {
    const key = jobArtifactKey(jobId, artifact.uri);
    if (key === null) {
      unresolved.push(artifact);
      continue;
    }

    const segments = key.split("/");
    const groupId = segments[0];
    const filename = segments[segments.length - 1];
    const logKind = classifyLogFilename(filename);
    const entry: ArtifactWithKey = { artifact, key, filename, logKind };

    let group = groupsById.get(groupId);
    if (!group) {
      group = {
        groupId,
        taskState: taskStateById.get(groupId) ?? null,
        artifacts: [],
        logs: [],
        hasFailureLog: false,
      };
      groupsById.set(groupId, group);
      order.push(groupId);
    }
    group.artifacts.push(entry);
    if (logKind !== null) group.logs.push(entry);
  }

  const groups = order.map((id) => groupsById.get(id)!);
  for (const group of groups) {
    group.logs.sort((a, b) => logOrder(a.logKind!) - logOrder(b.logKind!));
    group.hasFailureLog = group.taskState === "FAILED" && group.logs.length > 0;
  }

  // Stable sort (guaranteed by the spec since ES2019): FAILED-task groups
  // move to the front without disturbing the relative order of the rest.
  groups.sort((a, b) => Number(b.hasFailureLog) - Number(a.hasFailureLog));

  return { groups, unresolved };
}
