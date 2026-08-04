"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowClockwise, Warning } from "@phosphor-icons/react";
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
          router.push("/sign-in?next=/machines");
          return;
        }
        setError(
          err instanceof Error ? err.message : "Couldn't load your machines."
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

  const active = machines.filter((m) => m.status !== "revoked");
  const online = active.filter((m) => isOnline(m.last_seen_at)).length;

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="title">Machines</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Enrolled to your account and taking work.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={load}
            aria-label="Refresh"
            className="rounded-md p-2 text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
          >
            <ArrowClockwise
              size={15}
              className={state === "loading" ? "animate-spin" : ""}
            />
          </button>
          <Link
            href="/activate"
            className="interactive rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110"
          >
            Attach a machine
          </Link>
        </div>
      </div>

      {active.length > 0 && (
        <div className="mt-7 flex items-baseline gap-6">
          <div>
            <div className="metric-lg">{online}</div>
            <div className="label-caps mt-1">Online now</div>
          </div>
          <div>
            <div className="metric-lg text-muted-foreground">
              {active.length}
            </div>
            <div className="label-caps mt-1">Enrolled</div>
          </div>
        </div>
      )}

      <div className="mt-6">
        {state === "loading" && machines.length === 0 ? (
          <div className="space-y-px">
            <div className="skeleton h-14" />
            <div className="skeleton h-14" />
          </div>
        ) : state === "error" ? (
          <div className="flex flex-col items-center gap-3 py-12 text-center">
            <Warning className="h-5 w-5 text-destructive" weight="fill" />
            <p className="text-sm text-muted-foreground">{error}</p>
            <button
              type="button"
              onClick={load}
              className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-white/[0.06]"
            >
              Try again
            </button>
          </div>
        ) : machines.length === 0 ? (
          <Empty />
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
                {machines.map((m) => (
                  <MachineRow key={m.id} machine={m} onRevoke={handleRevoke} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
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
      toast.error("Couldn't revoke that machine", {
        description: "The machine is unchanged. Try again.",
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
                  : "oklch(1 0 0 / 0.25)",
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
          {machine.pools.map((pool) => (
            <span
              key={pool.id}
              className="rounded-full border border-border/60 bg-white/[0.04] px-2 py-0.5 text-[10px] text-muted-foreground"
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
                <button
                  type="button"
                  className="rounded-md px-2.5 py-1 text-xs text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                >
                  Revoke
                </button>
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

function Empty() {
  return (
    <div className="flex flex-col items-center gap-5 py-14 text-center">
      <div>
        <h2 className="text-base font-semibold">No machines yet</h2>
        <p className="mx-auto mt-1.5 max-w-sm text-sm text-muted-foreground">
          Run these on the machine you want to lend. It can be this one.
        </p>
      </div>
      <EnrolInstructions base={cloudApiBase()} />
    </div>
  );
}
