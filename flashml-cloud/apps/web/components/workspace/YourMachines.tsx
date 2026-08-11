"use client";

import { useCallback, useEffect, useState } from "react";
import { Warning } from "@phosphor-icons/react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { isOnline } from "@/lib/machine-status";
import {
  MACHINE_BADGE_LABELS,
  MACHINE_BADGE_STYLES,
  machineBadge,
} from "@/lib/machine-badge";
import {
  bindMachineToPool,
  listMachines,
  unbindMachineFromPool,
  type Machine,
} from "@/lib/cloud-api";

// ---------------------------------------------------------------------------
// Your machines — per-device opt-in
// ---------------------------------------------------------------------------

/** `listMachines()` is scoped to the caller by the API itself, so this is
 * always "your machines", never every machine bound to the pool — the
 * member table's "Machines"/"Online" columns already summarise that in
 * aggregate, one row per member, counting only machines actually bound to
 * this pool (not every machine the member happens to own). */
export function YourMachines({
  poolId,
  poolName,
}: {
  poolId: string;
  poolName: string;
}) {
  const [machines, setMachines] = useState<Machine[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">(
    "loading"
  );
  const [error, setError] = useState<string | null>(null);
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());

  const load = useCallback(() => {
    listMachines()
      .then((r) => {
        // A revoked machine's token is dead — it can never claim work, so
        // "opt it into this pool" is meaningless. Filtered here, not just
        // styled differently, because the API still accepts a bind for one
        // (204: bindMachineToPool only checks ownership, not status), and a
        // checkable row would silently misrepresent this pool's real
        // capacity to every member who can see it.
        setMachines(r.filter((m) => m.status !== "revoked"));
        setState("ready");
        setError(null);
      })
      .catch((err) => {
        setError(
          err instanceof Error ? err.message : "Couldn't load your machines."
        );
        setState("error");
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Optimistic, with revert on failure — the checkbox flips immediately
  // rather than waiting a round trip, and flips back with a toast if the
  // API refuses. Same "confirm, then reflect it in state" shape the
  // machines page's revoke button uses, just eagerly instead of after the
  // fact, since a toggle (unlike an irreversible revoke) is cheap to undo
  // on screen.
  async function toggle(machine: Machine, bound: boolean) {
    const label = machine.name || machine.node_id;
    setPendingIds((prev) => new Set(prev).add(machine.id));
    setMachines((prev) =>
      prev.map((m) =>
        m.id !== machine.id
          ? m
          : {
              ...m,
              pools: bound
                ? m.pools.filter((p) => p.id !== poolId)
                : [...m.pools, { id: poolId, name: poolName }],
            }
      )
    );
    try {
      if (bound) {
        await unbindMachineFromPool(poolId, machine.id);
      } else {
        await bindMachineToPool(poolId, machine.id);
      }
    } catch {
      setMachines((prev) =>
        prev.map((m) =>
          m.id !== machine.id
            ? m
            : {
                ...m,
                pools: bound
                  ? [...m.pools, { id: poolId, name: poolName }]
                  : m.pools.filter((p) => p.id !== poolId),
              }
        )
      );
      toast.error(`Couldn't ${bound ? "remove" : "add"} ${label}`, {
        description: "This workspace is unchanged. Try again.",
      });
    } finally {
      setPendingIds((prev) => {
        const next = new Set(prev);
        next.delete(machine.id);
        return next;
      });
    }
  }

  return (
    <section>
      <h2 className="text-sm font-semibold">Your machines</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Opt your own machines into this workspace&apos;s work. A machine
        serves no workspace until it&apos;s selected here, even if you own
        it.
      </p>

      <div className="mt-3">
        {state === "loading" ? (
          <div className="space-y-px">
            <div className="skeleton h-11" />
            <div className="skeleton h-11" />
          </div>
        ) : state === "error" ? (
          <div className="flex items-center gap-2 py-2 text-sm text-destructive">
            <Warning className="h-4 w-4 shrink-0" weight="fill" />
            <span>{error}</span>
            <button
              type="button"
              onClick={load}
              className="text-muted-foreground hover:text-foreground"
            >
              Try again
            </button>
          </div>
        ) : machines.length === 0 ? (
          <div className="flex items-center gap-4 rounded-[7px] border border-border bg-surface px-4 py-4">
            <p className="text-sm text-muted-foreground">
              No machines on your account yet.{" "}
              <a href="#connect-panel" className="text-brand-foreground hover:underline">
                Connect one below.
              </a>
            </p>
          </div>
        ) : (
          <div className="divide-y divide-border">
            {machines.map((m) => (
              <MachineToggleRow
                key={m.id}
                machine={m}
                // `?? []`: same api/web deploy-race insurance as
                // machines/page.tsx's pool-chip list — a response missing
                // `pools` must read as "not bound", never throw.
                bound={(m.pools ?? []).some((p) => p.id === poolId)}
                pending={pendingIds.has(m.id)}
                onToggle={toggle}
              />
            ))}
          </div>
        )}
      </div>

      {machines.length > 0 && (
        <p className="mt-2.5 text-xs text-muted-foreground">
          Takes effect within ~30s while the agent is running.
        </p>
      )}
    </section>
  );
}

function MachineToggleRow({
  machine,
  bound,
  pending,
  onToggle,
}: {
  machine: Machine;
  bound: boolean;
  pending: boolean;
  onToggle: (machine: Machine, bound: boolean) => void;
}) {
  const badge = machineBadge(machine);
  // `YourMachinesSection` already filters revoked machines out before this
  // ever renders, but the `!revoked &&` guard stays anyway — defense in
  // depth against a future caller of this row that doesn't, same
  // derivation `machines/page.tsx`'s `MachineRow` uses for its own dot.
  const revoked = machine.status === "revoked";
  const online = !revoked && isOnline(machine.last_seen_at);
  const label = machine.name || machine.node_id;

  return (
    <label className="flex cursor-pointer items-center gap-3 py-2.5 text-sm">
      <input
        type="checkbox"
        checked={bound}
        disabled={pending}
        onChange={() => onToggle(machine, bound)}
        // User-visible too, just only to screen readers — same "workspace"
        // vocabulary as every other string on this page.
        aria-label={`${bound ? "Remove" : "Add"} ${label} ${bound ? "from" : "to"} this workspace`}
        className="h-4 w-4 shrink-0 rounded border-border accent-primary disabled:opacity-50"
      />
      <span
        className="status-dot"
        data-state={online ? "live" : undefined}
        style={{
          background: online ? "var(--node-green)" : "var(--muted-foreground)",
        }}
      />
      <span className="min-w-0 flex-1 truncate font-mono">{label}</span>
      <Badge variant="outline" className={MACHINE_BADGE_STYLES[badge]}>
        {MACHINE_BADGE_LABELS[badge]}
      </Badge>
    </label>
  );
}
