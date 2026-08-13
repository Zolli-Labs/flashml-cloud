/** What one task's artifact group says about itself before it is opened.
 *
 * WHY THIS MODULE EXISTS. `ArtifactsCard` rendered every file of every group
 * inline. Checkpointing is on for every job, so a nine-task run that
 * checkpoints six times is fifty-four rows — and the row somebody landed on
 * this page to find, a failed task's `stderr`, is somewhere in the middle of
 * them. The card now shows one line per task and opens the files on demand,
 * which needs each line to state what it is standing in for.
 *
 * NOTHING HERE IS INVENTED, and in particular no size is. A file whose size
 * the listing did not report is counted in the file count and left out of the
 * byte total, which is then flagged as a FLOOR — the same rule
 * `lib/job-artifacts.ts` applies to the job-level total, for the same reason:
 * a fabricated 0 inside a sum is indistinguishable from a real one
 * afterwards.
 *
 * Lives beside the component rather than in `lib/` with the grouping itself
 * because it is a decision about the summary LINE, not about the data — but
 * it is still a decision, so it is in a `.ts` where a test can reach it
 * rather than inlined in the `.tsx` where nothing can.
 */

import type { ArtifactGroup } from "@/lib/task-artifacts";

export interface ArtifactGroupSummary {
  /** Output files — the group's artifacts minus its checkpoints. Counted
   * separately because they are the deliverable and the checkpoints are the
   * machinery that made the run survivable; `lib/task-artifacts.ts` splits
   * them for that reason and this line keeps the split visible. */
  fileCount: number;
  checkpointCount: number;
  /** Bytes across EVERYTHING in the group, checkpoints included: it is the
   * answer to "what would downloading this task cost me", and a total that
   * silently omitted the checkpoints would understate it. */
  totalBytes: number;
  /** True when at least one file's size was not reported, so `totalBytes` is
   * a floor and has to be rendered as one. */
  totalIsPartial: boolean;
}

export function summariseArtifactGroup(
  group: ArtifactGroup
): ArtifactGroupSummary {
  let totalBytes = 0;
  let totalIsPartial = false;
  for (const artifact of group.artifacts) {
    if (artifact.sizeBytes === null) totalIsPartial = true;
    else totalBytes += artifact.sizeBytes;
  }
  return {
    fileCount: group.results.length,
    checkpointCount: group.checkpoints.length,
    totalBytes,
    totalIsPartial,
  };
}
