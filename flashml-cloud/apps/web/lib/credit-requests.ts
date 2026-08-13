import { formatZc } from "./market-credits";

/** Converts a positive ZC text input to its exact integer millicredit
 * value. Invalid, zero, negative, over-precise, and unsafe values stay null
 * so callers cannot turn an incomplete input into an API request. */
export function parseZcInput(input: string): number | null {
  const value = input.trim();
  const match = /^(\d+)(?:\.(\d{1,3}))?$/.exec(value);
  if (!match) return null;

  const whole = Number(match[1]);
  const fractional = Number((match[2] ?? "").padEnd(3, "0"));
  const millicredits = whole * 1000 + fractional;
  return Number.isSafeInteger(millicredits) && millicredits > 0
    ? millicredits
    : null;
}

/** Formats the request amount's USD preview from exact millicredits. USD
 * rounds to cents only at display time; the submitted API value remains the
 * unrounded integer amount. */
export function usdForMillicredits(millicredits: number): string {
  if (!Number.isFinite(millicredits)) return "—";
  const cents = Math.round(millicredits / 10);
  const sign = cents < 0 ? "-" : "";
  const absolute = Math.abs(cents);
  return `${sign}$${Math.floor(absolute / 100)}.${String(absolute % 100).padStart(2, "0")}`;
}

/** A decision keeps both amounts visible: a partial approval is not a
 * smaller version of the original request. */
export function creditRequestSummary(request: {
  requested_zc: number;
  approved_zc: number | null;
  status?: string;
}): string {
  if (request.approved_zc !== null) {
    return `${formatZc(request.approved_zc)} ZC approved from ${formatZc(request.requested_zc)} ZC requested`;
  }
  if (request.status === "declined") {
    return `${formatZc(request.requested_zc)} ZC requested — declined`;
  }
  return `${formatZc(request.requested_zc)} ZC requested`;
}

/** The sum of `requested_zc` across the queue rows currently on screen —
 * never across a read that has not finished, since a queue this page has not
 * fully read cannot honestly claim a total for it. Callers pass only the
 * rows a `present`/`empty` panel actually holds (`lib/console/queue-panel.ts`
 * `queueBadgeCount` draws the same line for the row count beside it), so an
 * empty array here is a real "nothing pending", not "not read yet". */
export function totalRequestedZc(requests: readonly { requested_zc: number }[]): number {
  return requests.reduce((sum, request) => sum + request.requested_zc, 0);
}

/** Restores an optimistically removed oldest-first admin queue row. The id
 * check makes it safe if a refresh repopulated that same pending row before
 * the failed action's catch handler runs. */
export function restoreCreditRequest<T extends { id: string; requested_at: string }>(
  requests: T[],
  request: T
): T[] {
  if (requests.some((existing) => existing.id === request.id)) return requests;
  return [...requests, request].sort((left, right) => {
    const byDate = left.requested_at.localeCompare(right.requested_at);
    return byDate || left.id.localeCompare(right.id);
  });
}

/** Advances the admin credit queue version when a request starts or a
 * decision changes its contents. A list response may install only when it
 * still carries the current version. */
export function nextCreditQueueGeneration(generation: number): number {
  return generation + 1;
}

/** Returns a list response that is still current, excluding ids currently
 * being decided or already decided in this view. `null` means its read began
 * before a later list or decision and must not overwrite newer queue state. */
export function applyCreditQueueLoad<T extends { id: string }>(
  requests: T[],
  responseGeneration: number,
  currentGeneration: number,
  blockedIds: ReadonlySet<string>
): T[] | null {
  if (responseGeneration !== currentGeneration) return null;
  return requests.filter((request) => !blockedIds.has(request.id));
}
