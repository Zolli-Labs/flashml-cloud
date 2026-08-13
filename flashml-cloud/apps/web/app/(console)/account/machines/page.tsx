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
import { isOnline, relativeTime } from "@/lib/machine-status";
import {
  MACHINE_BADGE_LABELS as BADGE_LABELS,
  MACHINE_BADGE_STYLES as BADGE_STYLES,
  machineBadge,
} from "@/lib/machine-badge";
import {
  NotAuthenticated,
  cloudApiBase,
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
            <Button render={<Link href="/activate" />}>Add a Machine</Button>
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
          const activeRows = rows.filter((m) => m.status !== "revoked");
          const online = activeRows.filter((m) =>
            isOnline(m.last_seen_at)
          ).length;
          return (
            <>
              {activeRows.length > 0 && (
                <div className="mb-6 flex items-baseline gap-6">
                  <div>
                    <div className="metric-lg">{online}</div>
                    <div className="label-caps mt-1">Online now</div>
                  </div>
                  <div>
                    <div className="metric-lg text-muted-foreground">
                      {activeRows.length}
                    </div>
                    <div className="label-caps mt-1">Enrolled</div>
                  </div>
                </div>
              )}
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
                    {rows.map((m) => (
                      <MachineRow
                        key={m.id}
                        machine={m}
                        onRevoke={handleRevoke}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
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

  const revoked = machine.status === "revoked";
  const online = !revoked && isOnline(machine.last_seen_at);
  const label = machine.name || machine.node_id;

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
    <tr className={revoked ? "opacity-45" : undefined}>
      <td className="px-3 py-3">
        <div className="flex items-center gap-2.5">
          <span
            className="status-dot"
            data-state={online ? "live" : undefined}
            style={{
              background: revoked
                ? "var(--muted-foreground)"
                : online
                  ? "var(--node-green)"
                  : "var(--muted-foreground)",
            }}
          />
          <span className="min-w-0">
            <span className="block truncate font-mono text-sm">
              {machine.name || machine.node_id}
            </span>
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
        {revoked
          ? `revoked ${relativeTime(machine.revoked_at)}`
          : relativeTime(machine.last_seen_at)}
      </td>
      <td className="px-3 py-3 text-right">
        {revoked ? (
          <span className="meta">revoked</span>
        ) : (
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
                  Its token stops working immediately and it can no longer
                  claim work. Any task it currently holds keeps running until
                  the lease expires, then requeues elsewhere. Re-enrolling
                  needs a new device code.
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
        )}
      </td>
    </tr>
  );
}
