"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowClockwise } from "@phosphor-icons/react";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/shell/PageHeader";
import { PageShell } from "@/components/shell/PageShell";
import { StatePanel } from "@/components/shell/StatePanel";
import { isEmptyList, resolvePanel } from "@/lib/console/panel-state";
import { EnrolInstructions } from "@/components/machines/EnrolInstructions";
import { RevokedMachines } from "@/components/machines/RevokedMachines";
import { relativeTime } from "@/lib/machine-status";
import { isMachineOnline } from "@/lib/machine-scope";
import {
  MACHINE_BADGE_LABELS as BADGE_LABELS,
  MACHINE_BADGE_STYLES as BADGE_STYLES,
  machineBadge,
} from "@/lib/machine-badge";
import {
  machineLabel,
  splitFleet,
  type DeleteOutcome,
} from "@/lib/machine-lifecycle";
import {
  NotAuthenticated,
  NotFound,
  cloudApiBase,
  deleteMachine,
  listMachines,
  revokeMachine,
  type Machine,
} from "@/lib/cloud-api";

const POLL_MS = 15_000;

export default function MachinesPage() {
  const router = useRouter();
  const [machines, setMachines] = useState<Machine[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listMachines()
      .then((r) => {
        setMachines(r);
        setState("ready");
        setError(null);
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          // 401 means signed out, not "you have no machines". Rendering the
          // empty state here would tell a signed-out user their fleet is
          // gone.
          //
          // `/account/machines`, this page's own path — `/machines` is only
          // a redirect stub now, so signing in used to bounce you through it
          // instead of straight back to where you were.
          router.push("/sign-in?next=/account/machines");
          return;
        }
        setError(
          err instanceof Error ? err.message : "Couldn't load your Machines."
        );
        setState("error");
      });
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

  async function handleRevoke(id: string) {
    await revokeMachine(id);
    setMachines((prev) =>
      prev.map((m) =>
        m.id === id
          ? { ...m, status: "revoked", revoked_at: new Date().toISOString() }
          : m
      )
    );
  }

  /**
   * Delete, refetch, and classify — never throw.
   *
   * The list is REFETCHED rather than edited in place, unlike revoke above:
   * revoke changes one field on a row that stays, and delete removes the row
   * from what the API will return next. Patching state to match would be
   * this page asserting the server's answer instead of reading it.
   *
   * A 404 refetches too. The route folds "unknown", "not yours" and "already
   * deleted" into one answer, and on a row this page just read the third is
   * what it means: the list was stale, so the honest response is to re-read
   * it and say so once. Treating it as an error would put a failure on
   * screen for an outcome that is exactly what the reader asked for.
   */
  async function handleDelete(id: string): Promise<DeleteOutcome> {
    try {
      await deleteMachine(id);
    } catch (err) {
      if (err instanceof NotFound) {
        load();
        return { kind: "already-gone" };
      }
      return {
        kind: "failed",
        detail: err instanceof Error ? err.message : "the API did not say why",
      };
    }
    load();
    return { kind: "deleted" };
  }

  // Same ordering the hand-rolled switch below used — error, then loading,
  // then the rows — with the one difference that no arrangement of these can
  // now reach the empty state from a failed read.
  const panel = resolvePanel(
    { loading: state === "loading", error, data: machines },
    isEmptyList
  );

  return (
    <PageShell width="wide">
      <PageHeader
        title="My Machines"
        description="Machines you own. Tick one into a Workspace to let your workspace members place jobs on it."
        actions={
          <>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={load}
              aria-label="Refresh"
            >
              <ArrowClockwise
                className={state === "loading" ? "animate-spin" : ""}
              />
            </Button>
            {/* No sizing override. The `default` variant now carries the same
                36px height, 600 weight and brand hover the hand-rolled string
                did; only horizontal padding differs, by 2px per side, which is
                the size scale's business and not this page's. */}
            {/* nativeButton={false}: the render prop swaps the underlying
                element for Link's <a>, so the primitive must not assume
                native <button> semantics (it adds role/keyboard handling
                itself instead). */}
            <Button render={<Link href="/activate" />} nativeButton={false}>
              Add a Machine
            </Button>
          </>
        }
      />

      <StatePanel
        state={panel}
        className="mt-6"
        label="your Machines"
        empty={{
          title: "No machines yet",
          description:
            "Run these commands on the machine you want to connect. It can be this one.",
          action: <EnrolInstructions base={cloudApiBase()} />,
        }}
        unreadable={{ retry: load }}
      >
        {(rows) => {
          // Both counts moved inside `present`. They used to render above the
          // state switch, so a failed poll left "2 Online now" standing over a
          // panel that had just said it could not read anything — stale
          // numbers presented as current.
          //
          // The split, and the two numbers over it, are one decision made in
          // `lib/machine-lifecycle.ts` — the header cannot count one set of
          // machines while the table draws another.
          const { enrolled, revoked, online } = splitFleet(rows);
          return (
            <>
              {/* Rendered even at zero. A fleet that is entirely revoked is
                  the state this whole screen exists for, and "0 Enrolled" is
                  a measured answer to the question the label asks. The
                  revoked count is deliberately NOT up here: it is not
                  capacity, and it already labels the section that holds it. */}
              <div className="mb-6 flex items-baseline gap-6">
                <div>
                  <div className="metric-lg">{online}</div>
                  <div className="label-caps mt-1">Online now</div>
                </div>
                <div>
                  <div className="metric-lg text-muted-foreground">
                    {enrolled.length}
                  </div>
                  <div className="label-caps mt-1">Enrolled</div>
                </div>
              </div>

              {enrolled.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Nothing enrolled right now.{" "}
                  <Link href="/machines/add" className="underline">
                    Add a machine
                  </Link>
                  , or bring one back below.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[620px] text-left">
                    <thead>
                      <tr className="border-b border-border">
                        {["Machine", "Platform", "Last seen", ""].map((h, i) => (
                          <th
                            key={h || i}
                            className={`label-caps px-3 py-2 font-medium ${i === 3 ? "text-right" : ""}`}
                          >
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {enrolled.map((m) => (
                        <MachineRow
                          key={m.id}
                          machine={m}
                          onRevoke={handleRevoke}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <RevokedMachines machines={revoked} onDelete={handleDelete} />
            </>
          );
        }}
      </StatePanel>
    </PageShell>
  );
}

function MachineRow({
  machine,
  onRevoke,
}: {
  machine: Machine;
  onRevoke: (id: string) => Promise<void>;
}) {
  const [revoking, setRevoking] = useState(false);

  // Enrolled rows only. `splitFleet` sends the revoked ones to
  // `RevokedMachines`, so nothing below branches on status any more — the
  // greyed-out full-detail row this used to draw for a revoked machine is
  // the clutter that section exists to replace. `isMachineOnline` rather
  // than a bare `isOnline` so this dot and the header count above it are the
  // same predicate.
  const online = isMachineOnline(machine);
  const label = machineLabel(machine);

  // Revoking is irreversible from this screen, so it gets a real modal
  // rather than the previous inline swap where "Revoke" quietly became
  // "Confirm" in the same few pixels — easy to click twice by accident on a
  // row you did not mean.
  async function confirm() {
    setRevoking(true);
    try {
      await onRevoke(machine.id);
      toast.success("Machine revoked", {
        description: `${label} can no longer claim work.`,
      });
    } catch {
      toast.error("Couldn't revoke that Machine", {
        description: "The Machine is unchanged. Try again.",
      });
    } finally {
      setRevoking(false);
    }
  }

  const badge = machineBadge(machine);

  return (
    <tr>
      <td className="px-3 py-3">
        <div className="flex items-center gap-2.5">
          <span
            className="status-dot"
            data-state={online ? "live" : undefined}
            style={{
              background: online
                ? "var(--node-green)"
                : "var(--muted-foreground)",
            }}
          />
          <span className="min-w-0">
            <span className="block truncate font-mono text-sm">{label}</span>
            <span className="meta block truncate">{machine.node_id}</span>
            <Badge
              variant="outline"
              className={`mt-1 ${BADGE_STYLES[badge]}`}
            >
              {BADGE_LABELS[badge]}
            </Badge>
          </span>
        </div>
      </td>
      <td className="px-3 py-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="meta">{machine.platform ?? "—"}</span>
          {/* `?? []`: an API ahead of a not-yet-deployed web build (or vice
              versa) could omit `pools` from the response entirely — this
              must degrade to "no chips shown", never throw. */}
          {(machine.pools ?? []).map((pool) => (
            <span
              key={pool.id}
              className="rounded-full border border-border bg-surface-2 px-2 py-0.5 text-[10px] text-muted-foreground"
            >
              {pool.name}
            </span>
          ))}
        </div>
      </td>
      <td className="meta px-3 py-3 whitespace-nowrap">
        {relativeTime(machine.last_seen_at)}
      </td>
      <td className="px-3 py-3 text-right">
        <AlertDialog>
          <AlertDialogTrigger
            render={
              <Button
                variant="ghost"
                size="sm"
                className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
              >
                Revoke
              </Button>
            }
          />
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Revoke {label}?</AlertDialogTitle>
              <AlertDialogDescription>
                Its token stops working immediately and it can no longer claim
                work. Any task it currently holds keeps running until the
                lease expires, then requeues elsewhere. Re-enrolling needs a
                new device code. It moves to Revoked below, where it can be
                deleted for good.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Keep it</AlertDialogCancel>
              <AlertDialogAction
                disabled={revoking}
                onClick={confirm}
                className="bg-destructive/15 text-destructive hover:bg-destructive/25"
              >
                {revoking ? "Revoking…" : "Revoke"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </td>
    </tr>
  );
}
