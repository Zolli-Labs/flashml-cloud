/** Machine liveness, extracted from MachineCard so the list and any future
 * detail view agree on it.
 *
 * A machine's heartbeat interval is NOT part of the API contract, so
 * "online" here is a display heuristic and not a fact the API asserts:
 * recently seen reads as online, otherwise offline. It must never be
 * presented as more precise than that, and nothing should branch on it
 * beyond styling. */
export const ONLINE_WITHIN_MS = 90_000;

export function relativeTime(iso: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "never";
  const deltaMs = Date.now() - then;
  if (deltaMs < 0) return "just now";
  const s = Math.floor(deltaMs / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function isOnline(lastSeenAt: string | null): boolean {
  if (!lastSeenAt) return false;
  const then = new Date(lastSeenAt).getTime();
  if (Number.isNaN(then)) return false;
  return Date.now() - then < ONLINE_WITHIN_MS;
}
