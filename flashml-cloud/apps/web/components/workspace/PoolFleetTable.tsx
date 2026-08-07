"use client";

import { Badge } from "@/components/ui/badge";
import { relativeTime } from "@/lib/machine-status";
import {
  MACHINE_BADGE_LABELS,
  MACHINE_BADGE_STYLES,
  machineBadge,
} from "@/lib/machine-badge";
import { isMachineOnline } from "@/lib/machine-scope";
import type { PoolMachine } from "@/lib/cloud-api";
import { ZolliCharacter } from "@/components/brand/ZolliCharacter";

/** Every machine bound to this workspace, across every member — not just the
 * viewer's own. `YourMachines` below this table is the per-device opt-in;
 * this is the read-only fleet-wide view it feeds. */
export function PoolFleetTable({ machines }: { machines: PoolMachine[] }) {
  if (machines.length === 0) {
    return (
      <div className="flex items-center gap-4 rounded-lg border border-border bg-surface px-4 py-4">
        <ZolliCharacter role="scout" size={54} />
        <p className="text-sm text-muted-foreground">
          No Zollis are serving this Crew yet. Tick one of yours in below,
          or connect a new one.
        </p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-left">
        <thead>
          <tr className="border-b border-border">
            {["Zolli", "Owner", "Trust", "Last seen"].map((h) => (
              <th key={h} className="label-caps px-3 py-2 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {machines.map((m) => (
            <FleetRow key={m.id} machine={m} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FleetRow({ machine }: { machine: PoolMachine }) {
  const online = isMachineOnline(machine);
  const label = machine.name || machine.node_id;
  const badge = machineBadge(machine);
  // `list_pool_machines` deliberately does NOT filter revoked machines, on
  // the stated grounds that the console renders their status. It did not:
  // `isMachineOnline` is false for a revoked machine exactly as it is for a
  // sleeping one, so a dead machine read as merely offline and nothing on
  // screen distinguished "asleep, will come back" from "token destroyed,
  // never will". Hence this badge — the thing that makes that docstring
  // true.
  const revoked = machine.status === "revoked";

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
          <span className="min-w-0 truncate font-mono text-sm">{label}</span>
          {revoked && (
            <Badge
              variant="outline"
              className="shrink-0 border-destructive/30 bg-destructive/10 text-destructive"
            >
              Revoked
            </Badge>
          )}
        </div>
      </td>
      <td className="meta px-3 py-3">
        {machine.owner_display_name ?? "unnamed"}
      </td>
      <td className="px-3 py-3">
        <Badge variant="outline" className={MACHINE_BADGE_STYLES[badge]}>
          {MACHINE_BADGE_LABELS[badge]}
        </Badge>
      </td>
      <td className="meta px-3 py-3 whitespace-nowrap">
        {relativeTime(machine.last_seen_at)}
      </td>
    </tr>
  );
}
