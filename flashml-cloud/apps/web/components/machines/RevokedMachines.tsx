"use client";

import Link from "next/link";
import { useState } from "react";
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
import { Button } from "@/components/ui/button";
import { Disclosure } from "@/components/jobs/Disclosure";
import {
  deleteNotice,
  machineLabel,
  revokedLabel,
  type DeleteOutcome,
} from "@/lib/machine-lifecycle";
import type { Machine } from "@/lib/cloud-api";

/**
 * The revoked pile, collapsed.
 *
 * WHAT THIS REPLACED. A revoked machine used to render the same row an
 * enrolled one does — hostname, platform, trust badge, workspace chips,
 * heartbeat — greyed to 45% and never removable. Eight of eleven rows on the
 * owner's own screen were machines that had been retired months ago, and the
 * three that mattered were below the fold. Nothing about a revoked machine
 * is worth that much of the page: it cannot claim work, it has no platform
 * worth reading and its capability snapshot describes a device that is not
 * there. One line each, behind a caret.
 *
 * A NATIVE `<details>`, via `Disclosure`, and that is the reason it is
 * imported across from the jobs components rather than rebuilt here. This
 * page polls every 15 seconds; open/closed state held in React next to
 * polled data snaps shut under whoever is reading it. The DOM keeps this
 * one, and the browser's own in-page search can find a hostname inside it
 * while it is closed.
 */
export function RevokedMachines({
  machines,
  onDelete,
}: {
  machines: Machine[];
  /** Performs the delete and refetches the list. Returns what happened
   * rather than throwing, because "already gone" is not a failure — see
   * `DeleteOutcome` in `lib/machine-lifecycle.ts`. */
  onDelete: (machineId: string) => Promise<DeleteOutcome>;
}) {
  // A single line for the whole section, not one per row: the row a failure
  // is about may well have just left the list (that is what a 404 means),
  // and a notice pinned to a row that no longer exists cannot be read.
  const [notice, setNotice] = useState<string | null>(null);

  // The guard lives here rather than at the call site so "absent when there
  // are none" cannot be forgotten by a second caller. Hooks first: this is a
  // conditional render, not a conditional hook.
  if (machines.length === 0) return null;

  async function remove(machine: Machine) {
    const label = machineLabel(machine);
    // `onDelete` has already refetched by the time it answers, so the toast
    // never claims a success the list has not yet agreed to.
    const outcome = await onDelete(machine.id);
    setNotice(deleteNotice(outcome, label));
    if (outcome.kind === "deleted") {
      toast.success("Machine deleted", {
        description: `${label} is out of your fleet. Its accepted work still counts.`,
      });
    }
  }

  return (
    <div className="mt-8 border-t border-border pt-4">
      <Disclosure label={`Revoked (${machines.length})`}>
        {notice && (
          <p className="meta mb-2" role="status">
            {notice}
          </p>
        )}
        <ul className="divide-y divide-border">
          {machines.map((m) => (
            <RevokedRow key={m.id} machine={m} onDelete={remove} />
          ))}
        </ul>
      </Disclosure>
    </div>
  );
}

/** One retired machine: what it was called, when it stopped, and the two
 * things that can still be done about it. */
function RevokedRow({
  machine,
  onDelete,
}: {
  machine: Machine;
  onDelete: (machine: Machine) => Promise<void>;
}) {
  const [deleting, setDeleting] = useState(false);
  const label = machineLabel(machine);

  // The same modal the Revoke button on the enrolled table uses, for the same
  // reason: this one is terminal from this screen, and an inline swap where a
  // word changes under the cursor is easy to click twice on a row you did not
  // mean.
  async function confirm() {
    setDeleting(true);
    try {
      await onDelete(machine);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <li className="flex items-center gap-3 py-1.5">
      <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground">
        {label}
      </span>
      <span className="meta shrink-0 whitespace-nowrap">
        {revokedLabel(machine)}
      </span>
      <span className="flex shrink-0 items-center gap-1">
        <AlertDialog>
          <AlertDialogTrigger
            render={
              <Button
                variant="ghost"
                size="xs"
                className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
              >
                Delete
              </Button>
            }
          />
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete {label}?</AlertDialogTitle>
              <AlertDialogDescription>
                This forgets the device — its name, platform and capability
                snapshot go, and it leaves this list. The work it was credited
                for still counts towards your contributions. If the same
                machine enrols again it comes back as itself.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Keep it</AlertDialogCancel>
              <AlertDialogAction
                disabled={deleting}
                onClick={confirm}
                className="bg-destructive/15 text-destructive hover:bg-destructive/25"
              >
                {deleting ? "Deleting…" : "Delete"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
        {/* Not an undo. A revoked machine's token is dead, so the only way
            back is the enrolment flow — the same one a new machine takes.
            nativeButton={false}: the render prop swaps in Link's <a>, so the
            primitive must not assume native <button> semantics. */}
        <Button
          variant="ghost"
          size="xs"
          className="text-muted-foreground"
          nativeButton={false}
          render={
            <Link
              href="/machines/add"
              title={`${label} comes back by enrolling again — a new device code on the machine itself. It returns as itself, keeping its credit.`}
            />
          }
        >
          Add it back
        </Button>
      </span>
    </li>
  );
}
