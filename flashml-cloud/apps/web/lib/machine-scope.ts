import { isOnline } from "./machine-status";

/** Whether a machine is online *right now*: not revoked, and seen recently.
 *
 * Two notions were drifting apart before this existed. `status` is
 * ENROLMENT state — a machine that enrolled and then went to sleep is still
 * `"active"` — while `isOnline` is heartbeat recency. Anything the console
 * labels "online" means the second, combined with the first: a revoked
 * machine is never online no matter how recently it was seen.
 *
 * Same derivation `MachineToggleRow` already used inline, extracted so the
 * header count and the table's own dots cannot disagree. */
export function isMachineOnline(machine: {
  status: string;
  last_seen_at: string | null;
}): boolean {
  return machine.status !== "revoked" && isOnline(machine.last_seen_at);
}

/** The fleet-size breakdown `w/[poolId]/machines` shows nowhere of its own —
 * every figure a viewer sees on that tab comes from the shared
 * `WorkspaceHeader` (density audit §3, gap 4). `total` is the array's own
 * length, so the four figures can never disagree with each other or with
 * what the table below renders. `online` reuses `isMachineOnline` rather
 * than re-deriving it, for the same reason that function itself exists: one
 * definition, so a header count and a table's dots cannot drift apart. */
export interface PoolFleetCounts {
  total: number;
  online: number;
  pending: number;
  revoked: number;
}

export function poolFleetCounts(
  machines: readonly { status: string; last_seen_at: string | null }[]
): PoolFleetCounts {
  let online = 0;
  let pending = 0;
  let revoked = 0;
  for (const machine of machines) {
    if (isMachineOnline(machine)) online++;
    if (machine.status === "pending") pending++;
    if (machine.status === "revoked") revoked++;
  }
  return { total: machines.length, online, pending, revoked };
}
