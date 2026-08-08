"use client";

import { useState } from "react";
import { DownloadSimple, Warning } from "@phosphor-icons/react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { fetchJobArtifact, type ArtifactRecord } from "@/lib/cloud-api";
import { MANY_FILES_THRESHOLD, planBulkDownload } from "@/lib/bulk-download";
import { formatBytes } from "@/lib/utils";

/** Milliseconds between one saved file and the next. Not a performance
 * knob: firing every download in the same synchronous burst is exactly the
 * pattern browsers ship abuse protection for (see
 * `MANY_FILES_THRESHOLD`'s doc in `lib/bulk-download.ts`) — spacing them out
 * gives the browser, and the person watching, room to see this as a
 * deliberate sequence rather than something to block. */
const DOWNLOAD_PACING_MS = 350;

/** What this button actually does, said once, next to the button rather
 * than only inside the confirmation dialog for large counts — a "Download
 * all" that quietly saves N separate files and calls it done is worse than
 * one that says so up front. `lib/bulk-download.ts` decided WHY a real
 * single archive is not on offer; this is the user-facing half of that
 * decision, kept short because it sits under a button on every render. */
function describePlan(plan: ReturnType<typeof planBulkDownload>): string {
  const size = plan.sizeIsPartial
    ? `at least ${formatBytes(plan.totalBytes)}`
    : formatBytes(plan.totalBytes);
  return `Saves ${plan.files.length} files separately (${size}), not as one archive.`;
}

/**
 * Fetches and saves every one of a job's artifacts, one at a time, instead
 * of making someone click a download link per file — the reason this exists
 * is a 24-shard job whose only retrieval path used to be 24 individual
 * clicks. It does NOT produce a single archive; see `lib/bulk-download.ts`'s
 * module doc for why that is not reachable here without a dependency, and
 * `describePlan` above for how that limitation is stated in the UI rather
 * than hidden behind a button that implies more than it does.
 *
 * Renders nothing when there is nothing this job's own key resolves to
 * download — same "absence is not an error" rule the rest of this page's
 * empty states already follow.
 */
export function DownloadAllButton({
  jobId,
  artifacts,
}: {
  jobId: string;
  artifacts: ArtifactRecord[];
}) {
  const plan = planBulkDownload(jobId, artifacts);
  const [progress, setProgress] = useState<{ done: number; failed: number } | null>(
    null
  );

  if (plan.files.length === 0) return null;

  const running =
    progress !== null && progress.done + progress.failed < plan.files.length;
  const many = plan.files.length > MANY_FILES_THRESHOLD;

  async function run() {
    setProgress({ done: 0, failed: 0 });
    for (let i = 0; i < plan.files.length; i++) {
      const file = plan.files[i];
      try {
        const blob = await fetchJobArtifact(jobId, file.key);
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = file.filename;
        a.click();
        URL.revokeObjectURL(url);
        setProgress((p) => (p ? { ...p, done: p.done + 1 } : p));
      } catch {
        // One failed file must not stop the rest — a 404 on a single
        // artifact (deleted between listing and this run, say) is exactly
        // the kind of thing this loop exists to keep going through, the
        // same way a person clicking each link by hand would just skip a
        // broken one and move on. `failed` is surfaced in the label so the
        // count at the end is honest about not being complete.
        setProgress((p) => (p ? { ...p, failed: p.failed + 1 } : p));
      }
      if (i < plan.files.length - 1) {
        await new Promise((resolve) => setTimeout(resolve, DOWNLOAD_PACING_MS));
      }
    }
  }

  const label = progress
    ? running
      ? `Downloading ${progress.done + progress.failed}/${plan.files.length}…`
      : progress.failed > 0
        ? `${progress.done} of ${plan.files.length} saved, ${progress.failed} failed`
        : `Saved all ${plan.files.length}`
    : `Download all (${plan.files.length})`;

  const button = (
    <button
      type="button"
      disabled={running}
      onClick={many ? undefined : run}
      className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs text-brand-foreground hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-60"
    >
      <DownloadSimple size={12} className={running ? "animate-pulse" : ""} />
      {label}
    </button>
  );

  return (
    <span className="flex flex-col items-end gap-1">
      {/* Below the threshold, one click just runs it — the plain, honest
          version of what a person clicking every link one at a time would
          do. Above it, a confirmation names the count and warns about
          browser download-permission prompts BEFORE triggering dozens of
          saves, the same "say it before, not after" rule the storage
          warning banner follows elsewhere in this console. */}
      {many ? (
        <AlertDialog>
          <AlertDialogTrigger render={button} />
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>
                Download {plan.files.length} files?
              </AlertDialogTitle>
              <AlertDialogDescription>
                {describePlan(plan)} Most browsers pause or block a page that
                triggers this many downloads in a row unless you allow it —
                watch for a permission prompt after the first few, and allow
                it to get the rest.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction onClick={run}>Download all</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      ) : (
        button
      )}
      <span className="flex items-start gap-1 text-[10px] text-muted-foreground">
        {progress?.failed ? (
          <Warning className="mt-px h-2.5 w-2.5 shrink-0 text-destructive" weight="fill" />
        ) : null}
        {describePlan(plan)}
      </span>
    </span>
  );
}
