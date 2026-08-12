"use client";

import { useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { DownloadSimple, FloppyDisk, Terminal, Warning } from "@phosphor-icons/react";
import { toast } from "sonner";

import { ClearArtifactsButton } from "@/components/jobs/ClearArtifactsButton";
import { DownloadAllButton } from "@/components/jobs/DownloadAllButton";
import {
  describeDownloadFailure,
  downloadArtifact,
  signInHref,
} from "@/lib/artifact-download";
import { NotAuthenticated } from "@/lib/cloud-api";
import type { ArtifactsPanel } from "@/lib/job-artifacts";
import type { ArtifactGroup, ArtifactWithKey } from "@/lib/task-artifacts";
import { formatBytes } from "@/lib/utils";

/**
 * What this job actually wrote, from the listing route.
 *
 * Lives here rather than inlined in `app/(console)/jobs/[jobId]/page.tsx`
 * for the reason `CheckpointsCard` and `MemberCredits` give: a `page.tsx`
 * may export only a default component plus route config, and a `.tsx` file
 * gets no test coverage at all. Every decision this renders — empty versus
 * unreadable, the storage sentence, the total and whether it is a floor —
 * is taken in `lib/job-artifacts.ts` where a test can reach it. This file is
 * markup and formatting.
 *
 * Nothing here is rendered from anything but a value the API returned. There
 * is no placeholder size, no sample row, and no path on which a failed read
 * renders as an empty job.
 */
export function ArtifactsCard({
  jobId,
  panel,
  onCleared,
  onRetry,
}: {
  jobId: string;
  panel: ArtifactsPanel;
  onCleared: () => void;
  onRetry: () => void;
}) {
  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">Artifacts</h2>
          {panel.state === "files" && (
            <p className="mt-0.5 font-mono text-xs text-muted-foreground">
              {panel.fileCount} file{panel.fileCount === 1 ? "" : "s"} ·{" "}
              {panel.totalIsPartial
                ? `at least ${formatBytes(panel.totalBytes)}`
                : formatBytes(panel.totalBytes)}
            </p>
          )}
        </div>
        {panel.state === "files" && (
          <div className="flex items-start gap-2">
            {/* `panel.storage` is the listing's own value, passed down rather
                than re-fetched: it tells the download whether asking the API
                for a presigned url could possibly help. See
                `lib/artifact-download.ts`. */}
            <DownloadAllButton
              jobId={jobId}
              storage={panel.storage}
              groups={panel.groups}
            />
            {panel.canClear && (
              <ClearArtifactsButton jobId={jobId} onCleared={onCleared} />
            )}
          </div>
        )}
      </div>

      {panel.state === "loading" && (
        <p className="mt-3 font-mono text-xs text-muted-foreground">
          reading this job&apos;s artifact list…
        </p>
      )}

      {panel.state === "empty" && (
        <p className="mt-3 max-w-prose text-sm leading-relaxed text-muted-foreground">
          {panel.emptyMessage}
        </p>
      )}

      {/* A failed read is never dressed as an empty job. The API's own words
          sit under the sentence, and the retry re-reads the listing rather
          than reloading the page. */}
      {panel.state === "unreadable" && (
        <div className="mt-3">
          <p className="flex max-w-prose items-start gap-2 text-sm leading-relaxed text-warning-foreground">
            <Warning className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
            <span>{panel.errorMessage}</span>
          </p>
          {panel.errorDetail && (
            <p className="mt-1.5 break-all font-mono text-[11px] text-muted-foreground">
              {panel.errorDetail}
            </p>
          )}
          <button
            type="button"
            onClick={onRetry}
            className="mt-3 rounded-md border border-border bg-surface px-2.5 py-1 text-xs hover:bg-surface-2"
          >
            Try again
          </button>
        </div>
      )}

      {panel.state === "files" && (
        <>
          {panel.storageNote && (
            <p className="mt-2 max-w-prose text-xs leading-relaxed text-muted-foreground">
              {panel.storageNote}
              {panel.mirroredAt && ` Recorded ${time(panel.mirroredAt)}.`}
            </p>
          )}
          <div className="mt-3 space-y-3">
            {panel.groups.map((g) => (
              <ArtifactGroupSection
                key={g.groupId}
                jobId={jobId}
                storage={panel.storage}
                group={g}
              />
            ))}
          </div>
        </>
      )}
    </section>
  );
}

/** Human label for a group whose id names no task — currently only a
 * reducer's own output bucket (`reduced/…`, see `lib/job-result.ts`'s
 * `concat` case). Falls back to the raw id for a bucket this page does not
 * yet know the name of, same "name it rather than hide it" rule
 * `summariseJobResult` follows for an unrecognised reducer. */
function groupLabel(groupId: string): string {
  if (groupId === "reduced") return "Reduced output";
  return groupId;
}

const TASK_STATE_TONE: Record<string, string> = {
  FAILED: "text-destructive border-destructive/40",
  CANCELLED: "text-muted-foreground border-muted",
  COMPLETED: "text-evergreen border-evergreen/40",
  LEASED: "text-brand-foreground border-brand/40",
  PENDING: "text-muted-foreground border-muted",
};

/** One task's (or reducer bucket's) artifacts, grouped. A FAILED task with
 * at least one recognised log gets a visible callout — `hasFailureLog` is
 * exactly that check, computed once in `lib/task-artifacts.ts` rather than
 * re-derived here, and `groupArtifactsByTask` already sorts these groups to
 * the front so the callout is the first thing on the card, not something
 * to scroll past a healthy task's output to find. */
function ArtifactGroupSection({
  jobId,
  storage,
  group,
}: {
  jobId: string;
  storage: string | null;
  group: ArtifactGroup;
}) {
  return (
    <div
      className={`rounded-md border p-3 ${
        group.hasFailureLog
          ? "border-destructive/30 bg-destructive/[0.03]"
          : "border-border"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs font-medium">
          {group.taskState === null ? groupLabel(group.groupId) : group.groupId}
        </span>
        {group.taskState !== null && (
          <span
            className={`rounded-full border px-1.5 py-0.5 font-mono text-[10px] ${
              TASK_STATE_TONE[group.taskState] ??
              "text-muted-foreground border-muted"
            }`}
          >
            {group.taskState}
          </span>
        )}
      </div>
      {group.hasFailureLog && (
        <p className="mt-1.5 flex items-start gap-1.5 text-xs text-destructive">
          <Terminal className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
          <span>This task failed. Its stdout and stderr are below.</span>
        </p>
      )}
      <div className="mt-2 space-y-1.5">
        {group.results.map((entry) => (
          <ArtifactRow
            key={entry.key}
            jobId={jobId}
            storage={storage}
            entry={entry}
          />
        ))}
      </div>
      {/* Kept, and kept apart. Checkpointing is on for every job, so a task
          that died mid-run leaves its `ckpt/step-*.json` files at these same
          keys — they are downloadable and sometimes exactly what someone
          wants, but they are the machinery that made the run survivable, not
          the run's output, and an undifferentiated list said otherwise. */}
      {group.checkpoints.length > 0 && (
        <div className="mt-3 border-t border-border pt-2">
          <div className="label-caps">
            Checkpoints ({group.checkpoints.length})
          </div>
          <p className="mt-1 text-[11px] text-muted-foreground">
            Written while the task ran, so a machine death resumes here
            instead of starting over. Not job output.
          </p>
          <div className="mt-2 space-y-1.5">
            {group.checkpoints.map((entry) => (
              <ArtifactRow
                key={entry.key}
                jobId={jobId}
                storage={storage}
                entry={entry}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** One file, and the button that gets it.
 *
 * A BUTTON, NOT AN ANCHOR, and that is the fix rather than a style choice. A
 * plain `<a href download>` to the API's download route is a NAVIGATION, and a
 * navigation sends no `Authorization` header — the route is authenticated, so
 * every click answered 401. `downloadArtifact` asks the API where these bytes
 * are and then either navigates to a presigned OSS url (which carries its own
 * grant, so no header is needed and nothing streams through this page) or
 * fetches them through the API with the header. `lib/artifact-download.ts`
 * holds that decision, and the reasoning for it; this is the click. */
function ArtifactRow({
  jobId,
  storage,
  entry,
}: {
  jobId: string;
  storage: string | null;
  entry: ArtifactWithKey;
}) {
  const [downloading, setDownloading] = useState(false);
  const router = useRouter();
  const pathname = usePathname();

  async function start() {
    setDownloading(true);
    try {
      await downloadArtifact({ jobId, key: entry.key, storage });
    } catch (err) {
      if (err instanceof NotAuthenticated) {
        // The same rule the page's own reads follow: a 401 is a real
        // signed-out state and belongs at sign-in, not in a toast beside a
        // page full of data the session already fetched.
        router.push(signInHref(pathname));
        return;
      }
      // Named by the key, because a job page can have forty of these rows and
      // "the download failed" would not say which. The description is the
      // API's own words when it gave any — nothing here guesses a cause.
      toast.error(`Couldn't download ${entry.key}`, {
        description: describeDownloadFailure(err),
      });
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="flex items-center justify-between gap-3 font-mono text-xs">
      <span className="flex min-w-0 items-center gap-1.5">
        {entry.logKind && (
          <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">
            <Terminal className="h-2.5 w-2.5" />
            {entry.logKind}
          </span>
        )}
        {entry.checkpointStep !== null && (
          <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">
            <FloppyDisk className="h-2.5 w-2.5" />
            step {entry.checkpointStep}
          </span>
        )}
        <span className="truncate text-brand-foreground">{entry.key}</span>
      </span>
      <span className="flex shrink-0 items-center gap-2">
        {/* `formatBytes(null)` is an em dash: a size the listing did not
            report reads as unknown, never as 0 B. */}
        <span className="text-muted-foreground">
          {formatBytes(entry.sizeBytes)}
        </span>
        <button
          type="button"
          onClick={start}
          disabled={downloading}
          aria-label={`Download ${entry.key}`}
          className="rounded p-1 hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <DownloadSimple className={downloading ? "animate-pulse" : ""} />
        </button>
      </span>
    </div>
  );
}

/** Same clock format the rest of this page uses for an API timestamp.
 * Returns an em dash for a value that is unparseable rather than the string
 * "Invalid Date". */
function time(iso: string): string {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? "—" : new Date(ms).toLocaleString();
}
