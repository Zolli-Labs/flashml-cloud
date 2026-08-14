"use client";

import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { relativeTime } from "@/lib/machine-status";
import {
  MACHINE_BADGE_LABELS,
  MACHINE_BADGE_STYLES,
  machineBadge,
} from "@/lib/machine-badge";
import { isMachineOnline } from "@/lib/machine-scope";
import type { PoolMachine } from "@/lib/cloud-api";

const COLUMNS = ["Machine", "Owner", "Trust", "Last seen"];

/** Every machine bound to this workspace, across every member — not just the
 * viewer's own. `YourMachines` below this table is the per-device opt-in;
 * this is the read-only fleet-wide view it feeds.
 *
 * ROWS ONLY, like `MemberTable`: an empty `machines` array does not say
 * whether the read succeeded, so `machines/page.tsx` owns that call through
 * `StatePanel`. The empty sentence that used to live here moved there
 * unchanged. */
export function PoolFleetTable({ machines }: { machines: PoolMachine[] }) {
  return (
    <Table className="min-w-[640px]">
      <TableHeader>
        <TableRow>
          {/* No `label-caps` here — `TableHead`'s own default now supplies
              the identical machine-register treatment (plus mono, which
              `.label-caps` alone did not). */}
          {COLUMNS.map((h) => (
            <TableHead key={h}>{h}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {machines.map((m) => (
          <FleetRow key={m.id} machine={m} />
        ))}
      </TableBody>
    </Table>
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
    <TableRow>
      <TableCell>
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
      </TableCell>
      {/* `?? "unnamed"` and not `?? "—"`: the API returning null here means
          the owner has not set a display name, which is a thing we observed,
          not a gap in the read. */}
      <TableCell className="meta">
        {machine.owner_display_name ?? "unnamed"}
      </TableCell>
      <TableCell>
        <Badge variant="outline" className={MACHINE_BADGE_STYLES[badge]}>
          {MACHINE_BADGE_LABELS[badge]}
        </Badge>
      </TableCell>
      <TableCell className="meta">
        {relativeTime(machine.last_seen_at)}
      </TableCell>
    </TableRow>
  );
}
