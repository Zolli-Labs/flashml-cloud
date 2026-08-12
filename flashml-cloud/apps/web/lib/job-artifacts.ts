/** What a job's Artifacts card is allowed to say, and about what.
 *
 * WHY THIS MODULE EXISTS. The card rendered `job.artifacts ?? []` until
 * 2026-08-11. That field is `[]` for every job this deployment actually runs
 * — its only writer sits on a KubeRay path nothing here takes — so the card
 * was empty for every finished job, and said "no artifacts were produced"
 * about runs that had written megabytes. `GET /v1alpha1/jobs/{id}/artifacts`
 * is the real listing; this module turns one read of it into the card's
 * whole vocabulary, in a file a test can reach (`vitest.config.ts` collects
 * only `**\/*.test.ts`, so a `.tsx` component gets no coverage — the same
 * split `lib/task-checkpoints.ts` and `lib/job-result.ts` document).
 *
 * THE DISTINCTION THIS MODULE EXISTS TO PROTECT. "This job produced no
 * artifacts" and "we could not read the listing" are different sentences and
 * the card must never substitute the first for the second. They arrive here
 * as different `ArtifactsRead` variants and leave as different panel states;
 * there is deliberately no code path that turns a failed read into an empty
 * list. A 404 in particular is NOT evidence of emptiness — an API deployed
 * before the listing route existed answers 404 for the route itself, and
 * this client cannot tell that from "unknown job".
 *
 * A THIRD CASE THAT IS NEITHER. A running job with nothing listed yet is not
 * an error and not a finished-empty job: tasks commit output as they go. The
 * empty copy branches on whether the job has stopped, so a job in flight is
 * told it is in flight.
 *
 * NOTHING HERE IS INVENTED. Every size, count and timestamp on the panel is
 * a value the listing returned. A size that is not a finite number becomes
 * null (rendered as unknown) and is left out of the total, which is then
 * labelled a floor rather than presented as exact — a fabricated 0 inside a
 * sum is indistinguishable from a real one afterwards.
 */

import type { JobArtifactListing, JobState, JobTask } from "./cloud-api";
import { CLEARABLE_STATES } from "./job-artifact-cleanup";
import { groupArtifactsByTask, type ArtifactGroup } from "./task-artifacts";

/** One attempt at reading the listing, classified rather than thrown — the
 * same shape `TaskCheckpointRead` uses next door, for the same reason: the
 * page renders every outcome, so every outcome has to be a value.
 *
 * `unreadable` carries the API's own words. It is deliberately NOT split by
 * status code: a 404 from an older API (no such route), a 404 for a job that
 * is not the caller's, a 502 and a dead network all leave this console
 * equally unable to say what this job wrote, and pretending to distinguish
 * them would mean guessing. */
export type ArtifactsRead =
  | { status: "loading" }
  | { status: "listed"; listing: JobArtifactListing }
  | { status: "unreadable"; detail: string };

export type ArtifactsPanelState = "loading" | "files" | "empty" | "unreadable";

export interface ArtifactsPanel {
  state: ArtifactsPanelState;
  /** Grouped by producing task, checkpoints separated from results — see
   * `lib/task-artifacts.ts`. Empty for every state but `files`. */
  groups: ArtifactGroup[];
  /** How many files the listing returned. 0 in every state but `files`, and
   * in `unreadable` that 0 means "unknown", which is why the card must read
   * `state` and not this number. */
  fileCount: number;
  /** The sum of the sizes that were readable. A floor, not a total, when
   * `totalIsPartial`. */
  totalBytes: number;
  totalIsPartial: boolean;
  /** The listing's `storage` value, verbatim, or null when nothing was
   * listed. Passed through unmapped so an unrecognised backend is named
   * rather than folded into whichever known one it resembles. */
  storage: string | null;
  /** The listing's `mirrored_at`, verbatim ISO, or null — including when
   * `storage` is `"oss"`, which the contract permits. Null means the API did
   * not report a time, never "just now". */
  mirroredAt: string | null;
  /** Where the bytes are, in the API's own terms and claiming nothing
   * further. Set only for `files`: with nothing listed there are no bytes to
   * locate, and a "not mirrored" line under "this job wrote nothing" says
   * something about a thing that does not exist. */
  storageNote: string | null;
  /** Set only for `empty`. */
  emptyMessage: string | null;
  /** Set only for `unreadable`. */
  errorMessage: string | null;
  /** The API's own words for the failure, for `unreadable` only. */
  errorDetail: string | null;
  /** Whether to offer "clear artifacts". False whenever the read failed: an
   * irreversible delete must not be offered on the strength of a listing
   * nobody could read. */
  canClear: boolean;
}

const EMPTY_PANEL: ArtifactsPanel = {
  state: "loading",
  groups: [],
  fileCount: 0,
  totalBytes: 0,
  totalIsPartial: false,
  storage: null,
  mirroredAt: null,
  storageNote: null,
  emptyMessage: null,
  errorMessage: null,
  errorDetail: null,
  canClear: false,
};

/** The empty state, branched on whether anything could still be written.
 *
 * `CLEARABLE_STATES` rather than a set defined here: it already answers
 * exactly this question ("has every task that could still write into this
 * job's output directory stopped"), it already includes PARTIAL for the
 * reason its own doc gives, and two copies of a state set drift. */
function emptyCopy(jobState: JobState | string): string {
  return CLEARABLE_STATES.has(jobState)
    ? "This job finished without writing any artifacts."
    : "No artifacts yet. Files appear here as tasks commit their output.";
}

/** Where the bytes are. Three branches and no fourth: an unrecognised
 * `storage` value is quoted back rather than mapped onto the nearest known
 * one, because the whole point of this line is that the reader can trust it. */
function storageCopy(storage: string, mirroredAt: string | null): string {
  if (storage === "oss") {
    return mirroredAt === null
      ? "Mirrored to Alibaba OSS. The API did not report when."
      : "Mirrored to Alibaba OSS.";
  }
  if (storage === "coordinator") {
    return "On the coordinator only — not mirrored to object storage.";
  }
  return `The API reported storage as "${storage}", which this console does not recognise. It is making no claim about where these files are held.`;
}

export const ARTIFACTS_UNREADABLE_MESSAGE =
  "Couldn't read this job's artifact list. That is not the same as the job having produced nothing — this console does not know either way.";

/** A federated run's listing is empty BY DESIGN, and saying "this job
 * finished without writing any artifacts" about it would be a lie of the
 * cardinal kind: every round wrote output, under its own coordinator job.
 * The parent id names no coordinator job, and the rounds' keys would not
 * compose with the fetch-by-key route, so the honest answer is to explain
 * the gap rather than render an empty box — the same words the API's own
 * route docstring gives. */
export const FEDERATED_ARTIFACTS_MESSAGE =
  "Federated runs write their output per round, under each round's own job — this parent id lists nothing, and the rounds' keys would not compose with the download route. The per-round jobs carry the files.";

export function summariseJobArtifacts({
  read,
  jobState,
  tasks,
  federated = false,
}: {
  read: ArtifactsRead;
  jobState: JobState | string;
  /** Only to label each group with its task's current state. A job with no
   * task breakdown still lists its files; the groups just carry a null
   * `taskState`. */
  tasks: JobTask[];
  /** The job's `mode` says "federated": the empty listing is then the
   * designed answer, not a finished-empty job, and the card explains the
   * per-round shape instead of claiming nothing was written. */
  federated?: boolean;
}): ArtifactsPanel {
  if (read.status === "loading") return EMPTY_PANEL;

  if (read.status === "unreadable") {
    return {
      ...EMPTY_PANEL,
      state: "unreadable",
      errorMessage: ARTIFACTS_UNREADABLE_MESSAGE,
      errorDetail: read.detail,
    };
  }

  const { artifacts, storage, mirrored_at: mirroredAt } = read.listing;
  const base = {
    ...EMPTY_PANEL,
    storage,
    mirroredAt,
    storageNote: storageCopy(storage, mirroredAt),
  };

  if (artifacts.length === 0) {
    // No storage note: there are no bytes, so there is nothing to say about
    // where they are held. `storage` and `mirroredAt` are still carried
    // verbatim — the API said them — but the card has no sentence to build.
    // A federated run is the exception: its empty listing is the designed
    // answer, and the explanation replaces the empty-job sentence rather
    // than sitting beside it.
    return {
      ...base,
      state: "empty",
      storageNote: null,
      emptyMessage: federated
        ? FEDERATED_ARTIFACTS_MESSAGE
        : emptyCopy(jobState),
    };
  }

  let totalBytes = 0;
  let totalIsPartial = false;
  for (const entry of artifacts) {
    if (typeof entry.size_bytes === "number" && Number.isFinite(entry.size_bytes)) {
      totalBytes += entry.size_bytes;
    } else {
      totalIsPartial = true;
    }
  }

  return {
    ...base,
    state: "files",
    groups: groupArtifactsByTask(artifacts, tasks),
    fileCount: artifacts.length,
    totalBytes,
    totalIsPartial,
    canClear: CLEARABLE_STATES.has(jobState),
  };
}
