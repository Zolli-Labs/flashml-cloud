import type { AccessRequestRow } from "./cloud-api";
import type { Option } from "./onboarding-options";

/** Human label for a raw enum value the API returns (e.g. "ml_engineer" ->
 * "ML engineer"). Falls back to the raw value itself when it doesn't match
 * a known option — an admin seeing an unrecognised-but-real value is closer
 * to the truth than an em dash hiding it — and to an em dash only when
 * there is no value at all. */
export function labelFor(options: Option[], value: string | null): string {
  if (!value) return "—";
  return options.find((o) => o.value === value)?.label ?? value;
}

/** `requested_at` at day precision. An admin triaging this queue needs "how
 * long has this been waiting", not a time-of-day. */
export function formatRequestedAt(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** The row's display name. `first_name`/`last_name` are both required by
 * the onboarding form before a request can even be submitted
 * (`isComplete`, `lib/onboarding-options.ts`), so a row missing both is
 * defensive rather than expected — it falls back to the email address
 * rather than rendering blank, and to a plain label when even that is
 * missing. */
export function fullNameFor(
  row: Pick<AccessRequestRow, "first_name" | "last_name" | "email">
): string {
  const name = [row.first_name, row.last_name]
    .filter((part): part is string => Boolean(part))
    .join(" ")
    .trim();
  return name || row.email || "No name on file";
}

/** The "Invited to {pool} by {inviter}" line, or null when this request
 * isn't attached to a pending pool invite. `invited_by_name` can be null
 * even when `pending_pool_name` is set — the inviter is a real account
 * that just never set a display name — so this falls back rather than
 * rendering "by " with nothing after it. */
export function inviteLine(
  row: Pick<AccessRequestRow, "pending_pool_name" | "invited_by_name">
): string | null {
  if (!row.pending_pool_name) return null;
  return `Invited to ${row.pending_pool_name} by ${row.invited_by_name ?? "someone else"}`;
}

/** Re-inserts a row that an optimistic Approve/Decline pulled out of the
 * queue, keeping `requested_at` order — so a failed action restores the
 * list to how it looked, rather than bumping the row to wherever a plain
 * push/unshift would land it. Matches the API's own `order by
 * ar.requested_at` (oldest first, `list_access_requests` in `db.py`). */
export function restoreRequest(
  rows: AccessRequestRow[],
  row: AccessRequestRow
): AccessRequestRow[] {
  const next = [...rows, row];
  next.sort((a, b) => a.requested_at.localeCompare(b.requested_at));
  return next;
}
