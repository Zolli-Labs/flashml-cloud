"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Warning } from "@phosphor-icons/react";

import { ClearArtifactsButton } from "@/components/jobs/ClearArtifactsButton";
import { StatePanel } from "@/components/shell/StatePanel";
import { resolvePanel } from "@/lib/console/panel-state";
import { summariseStorage } from "@/lib/account-storage";
import { clearableJobs } from "@/lib/job-artifact-cleanup";
import {
  NotAuthenticated,
  getMyStorage,
  listJobs,
  type AccountStorage,
  type JobRecord,
} from "@/lib/cloud-api";

/**
 * How much this account is storing, and the shortcut for getting some of it
 * back.
 *
 * OWNS ITS OWN READS, both of them, because they belong together and to
 * nothing else on the page: a successful clear invalidates the usage figure
 * and the clearable-jobs list at the same instant, and `onCleared` refetches
 * both. Keeping them in the page meant that one callback reached across four
 * unrelated `useState`s.
 *
 * NO `empty` STATE THAT MEANS ANYTHING. `isEmpty` is `() => false` on
 * purpose: `GET /me/storage` always answers with a used figure and a limit
 * (or an explicit null limit for an unlimited account), so a successful read
 * is never "there is nothing here". Zero bytes used is a real measurement and
 * renders as one.
 */
export function StoragePanel() {
  const [storage, setStorage] = useState<AccountStorage | null>(null);
  const [storageError, setStorageError] = useState<string | null>(null);
  const [storageLoading, setStorageLoading] = useState(true);

  const [jobs, setJobs] = useState<JobRecord[] | null>(null);
  const [jobsUnreadable, setJobsUnreadable] = useState(false);

  // No `setStorageLoading(true)` here — see `app/(console)/account/page.tsx`
  // for why (cascading render from an effect), and note that a refetch after
  // a clear should NOT blank the usage bar back to a skeleton: the figure on
  // screen is a real reading until the next one lands.
  const loadStorage = useCallback(() => {
    return getMyStorage()
      .then((s) => {
        setStorage(s);
        setStorageError(null);
      })
      .catch((err) => {
        // A 401 here is handled by the page's own `getMe` redirect, which
        // fires from the same page on the same signed-out session — no need
        // for a second redirect from this call too.
        //
        // What is NOT swallowed any more is the CONSEQUENCE. This branch used
        // to leave `storage` null and `storageError` null forever, and the
        // panel's only non-error, non-present branch was a `…` — so the panel
        // sat in a permanent loading state with nothing behind it. Clearing
        // the loading flag hands the classification to `panelRead`, which
        // calls a finished read that returned nothing what it is: unreadable.
        if (!(err instanceof NotAuthenticated)) {
          setStorageError(
            err instanceof Error
              ? err.message
              : "Couldn't load your storage usage."
          );
        }
      })
      .finally(() => setStorageLoading(false));
  }, []);

  useEffect(() => {
    loadStorage();
  }, [loadStorage]);

  const loadJobs = useCallback(() => {
    return listJobs()
      .then((j) => {
        setJobs(j);
        setJobsUnreadable(false);
      })
      .catch(() => {
        // Still best-effort — no banner, no retry, and the rest of this panel
        // is unaffected. But "we could not look" and "there is nothing to
        // clear" are different answers, and rendering nothing for both is the
        // same collapse the four-state rule exists to stop. One quiet line
        // below tells them apart.
        setJobsUnreadable(true);
      });
  }, []);

  useEffect(() => {
    loadJobs();
  }, [loadJobs]);

  // After a successful clear, both lists on screen are stale: the usage
  // bar still shows the bytes that were just freed, and the "free up
  // space" list still offers a job whose artifacts are gone. Re-fetching
  // both from the API is simpler and more honest than reconstructing the
  // new numbers on the client — `freed_bytes` is a delta and `used_bytes`
  // is a total, and subtracting one from a value we already know can drift
  // from the account's real usage the instant something else in this
  // account writes an artifact in parallel.
  const handleArtifactsCleared = useCallback(() => {
    loadStorage();
    loadJobs();
  }, [loadStorage, loadJobs]);

  const panel = resolvePanel(
    { loading: storageLoading, error: storageError, data: storage },
    () => false
  );

  return (
    <section className="panel mt-4 p-5">
      <h2 className="text-sm font-semibold">Storage</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Artifacts and checkpoints kept for jobs this account submitted.
      </p>

      <StatePanel
        state={panel}
        className="mt-4 px-0 sm:px-0"
        loadingRows={1}
        label="your storage usage"
        empty={{ title: "No storage figures for this account." }}
        unreadable={{ retry: loadStorage }}
      >
        {(s) => (
          <div className="mt-4">
            <StorageUsage storage={s} />
          </div>
        )}
      </StatePanel>

      <FreeUpSpace
        jobs={jobs}
        unreadable={jobsUnreadable}
        onCleared={handleArtifactsCleared}
      />
    </section>
  );
}

/** The used/limit bar, or the unlimited state — two genuinely different
 * layouts, not one bar with a hidden edge case. An unlimited account gets
 * no percentage and no fill at all: a bar drawn to any width would claim a
 * ceiling this account does not have. See `lib/account-storage.ts` for why
 * `unlimited` has to be checked before `percent` is ever read. */
function StorageUsage({ storage }: { storage: AccountStorage }) {
  const display = summariseStorage(storage);

  if (display.unlimited) {
    return (
      <div className="flex items-center justify-between gap-3">
        <span className="metric-value text-lg">{display.usedLabel}</span>
        <span className="rounded-full border border-border bg-surface-2 px-2.5 py-1 text-xs font-medium text-muted-foreground">
          No storage limit
        </span>
      </div>
    );
  }

  const pct = display.percent;
  // Green at "ok", the same warning/destructive tones the rest of this app
  // already uses (see the submit page's preflight findings) once there is
  // something to act on — a bar that stays green at 96% used would say
  // "fine" right up to the refusal this page exists to warn about.
  const barColor =
    display.severity === "full"
      ? "bg-destructive"
      : display.severity === "approaching"
        ? "bg-warning"
        : "bg-[var(--node-green)]";
  const pctColor =
    display.severity === "full"
      ? "text-destructive"
      : display.severity === "approaching"
        ? "text-warning-foreground"
        : "text-muted-foreground";
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm">
          <span className="metric-value">{display.usedLabel}</span>{" "}
          <span className="text-muted-foreground">of {display.limitLabel}</span>
        </span>
        <span className={`font-mono text-xs ${pctColor}`}>
          {Math.round(pct)}%
        </span>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className={`h-full rounded-full transition-[width] duration-500 ${barColor}`}
          style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
        />
      </div>
      {display.message && (
        <p
          className={`mt-2 flex items-start gap-1.5 text-xs leading-relaxed ${
            display.severity === "full"
              ? "text-destructive"
              : "text-warning-foreground"
          }`}
        >
          <Warning className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
          <span>{display.message}</span>
        </p>
      )}
    </div>
  );
}

/** How many at once — enough to be useful without turning the Storage panel
 * into a second jobs list. Whoever has more than this already knows where
 * `/jobs` is; the point of putting this here at all is reaching someone who
 * doesn't, right where the refusal message told them to come. */
const MAX_SHOWN = 5;

/** The shortcut this account's refusal message promises: "delete a finished
 * job's artifacts to free space", with an actual place to do it. `jobs` is
 * this account's own list — `listJobs()` needs no scoping of its own —
 * narrowed to the ones `DELETE /v1alpha1/jobs/{id}/artifacts` will accept
 * without a 409.
 *
 * Renders nothing when there is nothing clearable, the same "absence is not
 * an error" rule every empty state on this page already follows: a
 * brand-new account with no finished jobs should not see an empty shelf
 * where this shortcut would go.
 *
 * `unreadable` is the one case that is NOT absence. It stays quiet — this is
 * a convenience on top of the panel's real content and does not deserve a
 * banner — but it says so, because "you have nothing to clear" is a claim
 * and we did not manage to check. */
function FreeUpSpace({
  jobs,
  unreadable,
  onCleared,
}: {
  jobs: JobRecord[] | null;
  unreadable: boolean;
  onCleared: () => void;
}) {
  if (unreadable) {
    return (
      <p className="meta mt-5 border-t border-border pt-4">
        Couldn&apos;t check which jobs have artifacts to clear.
      </p>
    );
  }

  // Still reading, or read and nothing qualifies. Both render nothing, which
  // is what this shortcut has always done — it is a shortcut appearing, not
  // content arriving, so a skeleton here would promise a section that may
  // never exist.
  const all = clearableJobs(jobs ?? []);
  if (all.length === 0) return null;
  const shown = all.slice(0, MAX_SHOWN);

  return (
    <div className="mt-5 border-t border-border pt-4">
      <h3 className="text-sm font-medium">Free up space</h3>
      <p className="mt-1 text-xs text-muted-foreground">
        Clearing a job&apos;s artifacts permanently deletes what it wrote —
        results, checkpoints, logs — and cannot be undone.
      </p>
      <ul className="mt-3 space-y-2">
        {shown.map((j) => (
          <li
            key={j.jobId}
            className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2"
          >
            <Link
              href={`/jobs/${j.jobId}`}
              className="min-w-0 truncate font-mono text-xs text-brand-foreground hover:underline"
            >
              {j.label}
            </Link>
            <ClearArtifactsButton jobId={j.jobId} onCleared={onCleared} />
          </li>
        ))}
      </ul>
      {all.length > shown.length && (
        <Link
          href="/jobs"
          className="mt-2 inline-block text-xs text-brand-foreground hover:underline"
        >
          See all jobs
        </Link>
      )}
    </div>
  );
}
