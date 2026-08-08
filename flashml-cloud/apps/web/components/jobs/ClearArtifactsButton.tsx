"use client";

import { useState } from "react";
import { Trash } from "@phosphor-icons/react";
import { toast } from "sonner";
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
import { describeClearedArtifacts } from "@/lib/job-artifact-cleanup";
import { ApiError, NotFound, deleteJobArtifacts } from "@/lib/cloud-api";

/**
 * The confirmation every "clear this job's artifacts" action in the console
 * shares — the job detail page's Artifacts card and the account page's
 * Storage panel both render this one component rather than each inventing
 * its own dialog, so "how sure does clearing artifacts make someone" has
 * exactly one answer, asked the same way everywhere.
 *
 * That answer is deliberately the most careful one already in this
 * codebase, not a new one. Before this feature there were two: job cancel
 * (same page as this component's first caller) uses an inline two-button
 * prompt where "Cancel job" becomes "Confirm cancel" in the same few
 * pixels; the admin access-request queue's Decline has no confirmation at
 * all. Clearing artifacts is less recoverable than either — a cancelled
 * job's data is untouched and a declined request can be re-invited, but
 * this permanently frees the bytes — so it follows `account/machines`'
 * revoke-a-Zolli dialog instead: a real modal overlaying the page, a
 * second and separate click on `AlertDialogAction`, and copy that says
 * plainly that this cannot be undone.
 *
 * `onCleared` fires once there is nothing left to delete for this job —
 * after an actual delete, AND after a 404 this component treats as "already
 * gone" (another tab, or a doubled click, beat this one to it). Both are
 * the same outcome from the caller's point of view: whatever list it is
 * showing this job in should stop offering to delete it again as an error.
 */
export function ClearArtifactsButton({
  jobId,
  onCleared,
}: {
  jobId: string;
  onCleared: () => void;
}) {
  const [clearing, setClearing] = useState(false);

  // No manual open/close state. `onCleared` is expected to remove or
  // disable this button's own reason for existing (the job page clears
  // `job.artifacts`; the account page's list drops the job) — same shape as
  // `account/machines`' revoke dialog, whose row swaps away from rendering
  // `AlertDialog` at all once the machine is revoked, unmounting it. A
  // failed attempt leaves the dialog open with the API's own reason shown,
  // exactly where the click found it.
  async function confirmClear() {
    setClearing(true);
    try {
      const result = await deleteJobArtifacts(jobId);
      onCleared();
      toast.success(describeClearedArtifacts(result));
    } catch (err) {
      if (err instanceof NotFound) {
        onCleared();
        toast.info("Nothing left to delete for this job.");
        return;
      }
      const detail =
        err instanceof ApiError
          ? err.detail
          : "Couldn't clear this job's artifacts. Try again.";
      toast.error("Couldn't clear artifacts", { description: detail });
    } finally {
      setClearing(false);
    }
  }

  return (
    <AlertDialog>
      <AlertDialogTrigger
        render={
          <button
            type="button"
            className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs text-destructive hover:bg-destructive/10"
          >
            <Trash size={12} />
            Clear artifacts
          </button>
        }
      />
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Clear this job&apos;s artifacts?</AlertDialogTitle>
          <AlertDialogDescription>
            Every file this job wrote — results, checkpoints, logs — is
            deleted permanently to free your storage quota. This cannot be
            undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Keep them</AlertDialogCancel>
          <AlertDialogAction
            disabled={clearing}
            onClick={confirmClear}
            className="bg-destructive/15 text-destructive hover:bg-destructive/25"
          >
            {clearing ? "Clearing…" : "Clear artifacts"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
