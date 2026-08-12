"use client";

import { useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { DownloadSimple } from "@phosphor-icons/react";
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
import { downloadArtifact, signInHref } from "@/lib/artifact-download";
import { MANY_FILES_THRESHOLD, planBulkDownload } from "@/lib/bulk-download";
import { NotAuthenticated } from "@/lib/cloud-api";
import type { ArtifactGroup } from "@/lib/task-artifacts";
import { formatBytes } from "@/lib/utils";

/** Milliseconds between one triggered download and the next. Not a
 * performance knob: firing every download in the same synchronous burst is
 * exactly the pattern browsers ship abuse protection for (see
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
  return `Triggers ${plan.files.length} separate downloads (${size}), not one archive.`;
}

/**
 * Triggers a download of every one of a job's artifacts, one at a time,
 * instead of making someone click a download link per file — the reason this
 * exists is a 24-shard job whose only retrieval path used to be 24
 * individual clicks. It does NOT produce a single archive; see
 * `lib/bulk-download.ts`'s module doc for why that is not reachable here
 * without a dependency, and `describePlan` above for how that limitation is
 * stated in the UI rather than hidden behind a button that implies more than
 * it does.
 *
 * EVERY FILE GOES THROUGH `downloadArtifact`, exactly as a single row's
 * button does — the two must not have their own ideas about how a download
 * works. Which means each file is resolved on its own: a mirrored one becomes
 * a navigation to a presigned OSS url (no header needed, nothing through this
 * page's memory), an unmirrored one a fetch through the API with the bearer
 * header. A job can be both at once, since only ACCEPTED work is mirrored, so
 * "download all" on a run with a failed shard genuinely takes both paths.
 *
 * WHAT THIS CAN AND CANNOT REPORT. A triggered navigation reports nothing
 * back, so the label says "started" and never claims a file was saved. A
 * fetch that fails, on the other hand, is a fact — it is counted and said out
 * loud rather than folded into a number that reads as success, and one
 * failure does not stop the rest.
 *
 * Renders nothing when the job listed no files — same "absence is not an
 * error" rule the rest of this page's empty states already follow.
 */
export function DownloadAllButton({
  jobId,
  storage,
  groups,
}: {
  jobId: string;
  /** The listing's job-level `storage`, verbatim — see
   * `lib/artifact-download.ts` for what it is used for and what it is not. */
  storage: string | null;
  groups: ArtifactGroup[];
}) {
  const plan = planBulkDownload(groups);
  const [started, setStarted] = useState<number | null>(null);
  const [failed, setFailed] = useState(0);
  const router = useRouter();
  const pathname = usePathname();

  if (plan.files.length === 0) return null;

  const running = started !== null && started < plan.files.length;
  const many = plan.files.length > MANY_FILES_THRESHOLD;

  async function run() {
    setStarted(0);
    setFailed(0);
    let failures = 0;
    for (let i = 0; i < plan.files.length; i++) {
      try {
        await downloadArtifact({ jobId, key: plan.files[i].key, storage });
      } catch (err) {
        if (err instanceof NotAuthenticated) {
          // Stop, do not keep trying: the session is gone, so every remaining
          // file would fail the same way and the count would read as forty
          // separate problems instead of one.
          router.push(signInHref(pathname));
          return;
        }
        // Counted, not thrown: one unreachable file must not cost the other
        // thirty-nine. The count is shown below, so this is not swallowed
        // either.
        failures += 1;
      }
      setStarted(i + 1);
      if (i < plan.files.length - 1) {
        await new Promise((resolve) => setTimeout(resolve, DOWNLOAD_PACING_MS));
      }
    }
    setFailed(failures);
  }

  const label = running
    ? `Starting ${started}/${plan.files.length}…`
    : started !== null
      ? `Started ${plan.files.length}`
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
                it to get the rest. Your browser reports how each one ends;
                this page only knows how many it started.
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
      <span className="text-[10px] text-muted-foreground">
        {describePlan(plan)}
      </span>
      {/* Only ever shown after a run that actually failed something, and it
          counts fetches this page watched fail — never a navigation, whose
          outcome this page cannot see and does not claim to. */}
      {failed > 0 && (
        <span className="text-[10px] text-destructive">
          {failed} could not be fetched.
        </span>
      )}
    </span>
  );
}
