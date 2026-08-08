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

import type { ArtifactRecord } from "./cloud-api";
import { jobArtifactKey } from "./cloud-api";

export interface DownloadableArtifact {
  artifact: ArtifactRecord;
  /** What `fetchJobArtifact` needs — see `jobArtifactKey`. */
  key: string;
  /** Safe to hand to `<a download>`: `key` with every `/` flattened to
   * `__`, so two tasks that both wrote `stdout` save as two distinguishable
   * files (`task-000__stdout`, `task-001__stdout`) instead of colliding and
   * making the browser silently append `(1)` to the second one — which
   * erases exactly the "which task was this" fact a person opens the file
   * to find. */
  filename: string;
}

export interface BulkDownloadPlan {
  /** In the order `artifacts` was given, minus anything that is not a
   * result of this job at all (see `jobArtifactKey`'s own doc — a staged
   * input-code upload has no browser-readable route and must not be
   * silently attempted and failed). */
  files: DownloadableArtifact[];
  /** The sum of every KNOWN `size_bytes` among `files`. Not an estimate of
   * the true total when `sizeIsPartial` is true — an unknown size is
   * omitted from the sum entirely rather than treated as 0, so this number
   * is always a real, verifiable floor, never a guess dressed up as a
   * total. */
  totalBytes: number;
  /** True when at least one file's `size_bytes` is null — `totalBytes` is
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

function flattenKey(key: string): string {
  return key.replace(/\//g, "__");
}

export function planBulkDownload(
  jobId: string,
  artifacts: ArtifactRecord[]
): BulkDownloadPlan {
  const files: DownloadableArtifact[] = [];
  let totalBytes = 0;
  let sizeIsPartial = false;

  for (const artifact of artifacts) {
    const key = jobArtifactKey(jobId, artifact.uri);
    if (key === null) continue; // not a result of this job — nothing to offer

    files.push({ artifact, key, filename: flattenKey(key) });
    if (artifact.size_bytes === null) {
      sizeIsPartial = true;
    } else {
      totalBytes += artifact.size_bytes;
    }
  }

  return { files, totalBytes, sizeIsPartial };
}
