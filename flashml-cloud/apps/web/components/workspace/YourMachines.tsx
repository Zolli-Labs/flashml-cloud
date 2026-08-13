"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { StatePanel } from "@/components/shell/StatePanel";
import {
  isEmptyList,
  resolvePanelState,
  type PanelRead,
} from "@/lib/console/panel-state";
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
 * this pool (not every machine the member happens to own).
 *
 * THIS PANEL DOES ITS OWN READ, which is what makes it the one place on the
 * machines tab where all four states are genuinely reachable. Everything else
 * on that page is fed by `WorkspaceProvider`'s single `Promise.all`, so its
 * loading and unreadable branches belong to `WorkspaceGate` one level up.
 * This one owns its own, and it already handled all four before the sweep —
 * the conversion below is to `StatePanel` for the shared markup, not a
 * repair.
 */
export function YourMachines({
  poolId,
  poolName,
}: {
  poolId: string;
  poolName: string;
}) {
  /** The read itself, not three loosely-coupled flags.
   *
   * `machines` used to be a separate `useState` alongside `state`/`error`,
   * which meant rows could sit in state while `state === "error"` — the
   * arrangement that lets stale rows render under a failure banner. Holding
   * them ON the successful read makes that unrepresentable rather than
   * merely avoided, which is the same property `PanelState` is built for. */
  const [read, setRead] = useState<PanelRead<Machine[]>>({
    status: "loading",
  });
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());

  const load = useCallback(() => {
    // No `setRead({ status: "loading" })` here. `load` is what the mount
    // effect calls, and a synchronous setState in an effect body is a
    // cascading render (`react-hooks/set-state-in-effect`) — the initial
    // state is already `loading`, so on mount it would also be a no-op that
    // costs a render. Retrying is the case that genuinely needs to go BACK
    // to loading, and that happens in an event handler; see `retry` below.
    listMachines()
      .then((r) => {
        // A revoked machine's token is dead — it can never claim work, so
        // "opt it into this pool" is meaningless. Filtered here, not just
        // styled differently, because the API still accepts a bind for one
        // (204: bindMachineToPool only checks ownership, not status), and a
        // checkable row would silently misrepresent this pool's real
        // capacity to every member who can see it.
        setRead({
          status: "read",
          data: r.filter((m) => m.status !== "revoked"),
        });
      })
      .catch((err) => {
        // The API's own words, carried through to `state.detail` and shown
        // verbatim by `StatePanel`. `panel-state.ts` substitutes its own
        // sentence only when this one is blank.
        setRead({
          status: "unreadable",
          detail:
            err instanceof Error ? err.message : "Couldn't load your machines.",
        });
      });
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  /** The retry affordance on the unreadable state. Runs from a click, which
   * is why it may put the panel back to `loading` — so the skeleton returns
   * and the failed read's sentence goes away, rather than the error sitting
   * on screen looking unchanged while the new request is in flight. */
  const retry = useCallback(() => {
    setRead({ status: "loading" });
    load();
  }, [load]);

  /** Rewrite the rows of a read that SUCCEEDED, and do nothing otherwise.
   * There is deliberately no path that turns a loading or failed read into a
   * populated one. */
  function updateRows(fn: (prev: Machine[]) => Machine[]) {
    setRead((prev) =>
      prev.status === "read" ? { status: "read", data: fn(prev.data) } : prev
    );
  }

  // Optimistic, with revert on failure — the checkbox flips immediately
  // rather than waiting a round trip, and flips back with a toast if the
  // API refuses. Same "confirm, then reflect it in state" shape the
  // machines page's revoke button uses, just eagerly instead of after the
  // fact, since a toggle (unlike an irreversible revoke) is cheap to undo
  // on screen.
  async function toggle(machine: Machine, bound: boolean) {
    const label = machine.name || machine.node_id;
    setPendingIds((prev) => new Set(prev).add(machine.id));
    updateRows((prev) =>
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
      updateRows((prev) =>
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

  const panel = resolvePanelState(read, isEmptyList);

  return (
    <section>
      <h2 className="text-sm font-semibold">Your machines</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Opt your own machines into this workspace&apos;s work. A machine
        serves no workspace until it&apos;s selected here, even if you own
        it.
      </p>

      <div className="mt-3">
        <StatePanel
          state={panel}
          label="your machines"
          empty={{
            title: "No machines on your account yet.",
            action: (
              <a
                href="#connect-panel"
                className="text-sm text-brand-foreground hover:underline"
              >
                Connect one below.
              </a>
            ),
          }}
          unreadable={{ retry }}
        >
          {(rows) => (
            <div className="divide-y divide-border">
              {rows.map((m) => (
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
        </StatePanel>
      </div>

      {/* Only under rows that exist. Reading it off `panel.kind` rather than
          off a separate array means it cannot appear under a skeleton or
          under an error, which a `machines.length > 0` check on a detached
          array could. */}
      {panel.kind === "present" && (
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
  // `YourMachines` already filters revoked machines out before this ever
  // renders, but the `!revoked &&` guard stays anyway — defense in depth
  // against a future caller of this row that doesn't, same derivation
  // `machines/page.tsx`'s `MachineRow` uses for its own dot.
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
