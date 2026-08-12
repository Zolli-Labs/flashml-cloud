/** Deciding what a job page's "Download all" button can honestly offer, and
 * how each file it triggers should be named once the browser saves it.
 *
 * A browser gives a script exactly one download primitive: an `<a
 * download>` click, which saves ONE file. There is no API to hand it a
 * folder, and no way to make it present a save-as-archive dialog. This app
 * is not allowed to add a dependency to work around that, and there is no
 * dependency-free way to produce a REAL single archive either — a zip or
 * tar file is a byte-exact format, and hand-rolling one here would mean
 * shipping this app's own untested archive writer instead of a real one; a
 * single wrong header byte is a corrupted download nobody downstream can
 * diagnose. So this module does not pretend to zip or tar anything. It
 * plans the same sequence of individual downloads a person clicking every
 * link one at a time would get — the honest version of "all of it" — and
 * leaves the pacing and the up-front warning to the component that drives
 * it (`components/jobs/DownloadAllButton.tsx`), because a person about to
 * trigger dozens of saves at once needs to be told that BEFORE it happens,
 * not discover it as a stream of browser permission prompts.
 */

import { artifactFilename } from "./artifact-download";
import type { ArtifactGroup } from "./task-artifacts";

export interface DownloadableArtifact {
  /** The listing's own key, which is already what the download route takes.
   * Nothing is stripped or re-derived from it. */
  key: string;
  /** What this file is saved as: `key` with every `/` flattened to `__`, so
   * two tasks that both wrote `stdout` save as two distinguishable files
   * (`task-000__stdout`, `task-001__stdout`) instead of colliding.
   *
   * `artifactFilename` rather than a copy of the rule: single downloads,
   * bulk downloads and the API's own `Content-Disposition` must all name a
   * file the same way, and a second implementation of "flatten the key" is
   * how two of the three come to disagree. */
  filename: string;
  /** The size the listing reported, or null when it reported something that
   * was not a finite number. */
  sizeBytes: number | null;
}

export interface BulkDownloadPlan {
  /** Every file of every group, in group order — so the failed task whose
   * logs `groupArtifactsByTask` sorted to the front is also the first thing
   * saved. Checkpoints are included: they are as downloadable as anything
   * else, and a button labelled "all" that quietly skipped them would be
   * the same lie in the other direction. */
  files: DownloadableArtifact[];
  /** The sum of every KNOWN size among `files`. Not an estimate of the true
   * total when `sizeIsPartial` is true — an unknown size is omitted from the
   * sum entirely rather than treated as 0, so this number is always a real,
   * verifiable floor, never a guess dressed up as a total. */
  totalBytes: number;
  /** True when at least one file's size was unreadable — `totalBytes` is
   * then a floor, not a total, and must be labelled "at least" rather than
   * presented as exact. */
  sizeIsPartial: boolean;
}

/** Above this many files, the triggered sequence starts to look, from the
 * browser's own point of view, like the "a site is trying to download
 * multiple files" pattern Chrome and Firefox both ship abuse protection
 * for — a permission prompt at best, a silently dropped download at worst.
 * There is no documented, stable number to target exactly, so this is
 * deliberately conservative: it exists to make the caller warn well before
 * downloads actually start failing, not to promise that failures begin
 * exactly here. */
export const MANY_FILES_THRESHOLD = 10;

export function planBulkDownload(groups: ArtifactGroup[]): BulkDownloadPlan {
  const files: DownloadableArtifact[] = [];
  let totalBytes = 0;
  let sizeIsPartial = false;

  for (const group of groups) {
    for (const entry of group.artifacts) {
      files.push({
        key: entry.key,
        filename: artifactFilename(entry.key),
        sizeBytes: entry.sizeBytes,
      });
      if (entry.sizeBytes === null) sizeIsPartial = true;
      else totalBytes += entry.sizeBytes;
    }
  }

  return { files, totalBytes, sizeIsPartial };
}
