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

/** Every machine bound to this workspace, across every member — not just the
 * viewer's own. `YourMachines` below this table is the per-device opt-in;
 * this is the read-only fleet-wide view it feeds. */
export function PoolFleetTable({ machines }: { machines: PoolMachine[] }) {
  if (machines.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No machines serving this workspace yet. Tick one of yours in below,
        or connect a new one.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-left">
        <thead>
          <tr className="border-b border-border">
            {["Machine", "Owner", "Trust", "Last seen"].map((h) => (
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
                : "oklch(1 0 0 / 0.25)",
            }}
          />
          <span className="min-w-0 truncate font-mono text-sm">{label}</span>
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
