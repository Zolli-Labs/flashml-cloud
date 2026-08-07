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
