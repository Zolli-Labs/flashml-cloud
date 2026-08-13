/** Which ledger rows a reader asked for, and which consecutive rows may
 * stand in for one another.
 *
 * WHY THIS MODULE EXISTS. The Ledger view rendered one row per event, newest
 * first, and a healthy run is mostly `LEASE_RENEWED`: a lease is renewed
 * every few seconds for as long as a task is held, so a two-minute task
 * produces dozens of consecutive identical rows. The events that a person
 * opens this view to find — an expiry, a rejected commit, a re-claim on
 * another machine — sit between them and scroll away first. A ledger that
 * buries its own exceptions under its own heartbeat is a log file, not a
 * view of one.
 *
 * THE RULE THIS MODULE EXISTS TO PROTECT: collapsing changes what is SHOWN,
 * never what is TRUE. A run carries its events verbatim — the same
 * `JobEvent` objects the API returned — and nothing here computes a new
 * number, rewrites a coordinator message, or summarises what happened. The
 * only new information a run states is how many events it stands for and
 * the span they cover, both read directly off the events themselves.
 *
 * NEVER COLLAPSED, whatever it costs in rows: anything whose type contains
 * EXPIRED, FAILED, REJECTED, REQUEUED, SUCCEEDED, CLAIMED or ACCEPTED.
 * Those are the outcomes — the moments where work changed hands or was lost
 * — and one of them hidden behind a caret is one a reader does not find. A
 * substring test rather than an exact list because the coordinator's type
 * vocabulary grows upstream in `flashruntime`, and a new
 * `TASK_COMMIT_REJECTED_STALE` must be interesting by default rather than
 * quietly collapsible until somebody notices.
 *
 * A run is same TYPE, same MACHINE, same ROUND, and consecutive. Round is
 * stricter than it needs to be for an independent job (there is no round)
 * and is load-bearing for a federated one, where the API fans out over the
 * per-round coordinator jobs: merging two rounds' renewals into one row
 * would drop the `r0`/`r1` a reader is using to tell them apart.
 *
 * THE THRESHOLD IS THREE, matching `lib/tradeoff-row-groups.ts` and for its
 * reason: a control that hides two rows costs a click and saves one line.
 *
 * Pure and presentation-only, and deliberately order-agnostic — it collapses
 * whatever consecutive runs it is handed and preserves that order, so the
 * view can pass its own newest-first array without this module holding an
 * opinion about which way time runs on screen. `startedAt` / `endedAt` are
 * still the earliest and latest stamps in the run, so the span reads
 * forwards either way.
 */

import type { JobEvent } from "@/lib/cloud-api";

/** The four questions the chips above the ledger ask. Predicates on the
 * event TYPE alone — no clock, no lookup, nothing that could disagree with
 * what the row itself renders. */
export type LedgerFilter = "all" | "commits" | "leases" | "failures";

export const LEDGER_FILTERS: readonly { id: LedgerFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "commits", label: "Commits" },
  { id: "leases", label: "Leases" },
  { id: "failures", label: "Failures" },
];

/** What "this went wrong" means on this page. The same set the ledger's own
 * row colouring uses, exported so the amber row and the Failures chip can
 * never disagree about which events are the bad ones. */
export const FAILURE_MARKERS: readonly string[] = [
  "FAILED",
  "REJECTED",
  "EXPIRED",
  "LOST",
  "FROZEN",
];

export function isFailureEvent(type: string): boolean {
  return FAILURE_MARKERS.some((marker) => type.includes(marker));
}

/** THE FILTERS ARE NOT A PARTITION, on purpose. `LEASE_EXPIRED` is both a
 * lease event and a failure, and it appears under both chips — a reader
 * asking "what happened to the leases" and a reader asking "what went wrong"
 * are both owed it. Forcing four disjoint buckets would mean picking which
 * of those two people to lie to. */
export function matchesLedgerFilter(type: string, filter: LedgerFilter): boolean {
  switch (filter) {
    case "commits":
      return type.includes("COMMIT");
    case "leases":
      return type.includes("LEASE");
    case "failures":
      return isFailureEvent(type);
    default:
      return true;
  }
}

export function filterLedgerEvents(
  events: JobEvent[],
  filter: LedgerFilter
): JobEvent[] {
  if (filter === "all") return events;
  return events.filter((event) => matchesLedgerFilter(event.type, filter));
}

/** How many events each chip would show. Rendered on the chip so an empty
 * filter is visible BEFORE it is clicked: "Failures 0" is the answer to the
 * question, and a reader who clicks it and finds an empty list has been made
 * to work for the same fact. */
export function countByLedgerFilter(
  events: JobEvent[]
): Record<LedgerFilter, number> {
  const counts: Record<LedgerFilter, number> = {
    all: events.length,
    commits: 0,
    leases: 0,
    failures: 0,
  };
  for (const event of events) {
    if (matchesLedgerFilter(event.type, "commits")) counts.commits += 1;
    if (matchesLedgerFilter(event.type, "leases")) counts.leases += 1;
    if (matchesLedgerFilter(event.type, "failures")) counts.failures += 1;
  }
  return counts;
}

/** Types that stay one row per event, however many of them there are. See
 * the module docblock: these are outcomes, and an outcome behind a caret is
 * an outcome nobody reads. */
export const NEVER_COLLAPSED: readonly string[] = [
  "EXPIRED",
  "FAILED",
  "REJECTED",
  "REQUEUED",
  "SUCCEEDED",
  "CLAIMED",
  "ACCEPTED",
];

export function isCollapsibleType(type: string): boolean {
  return !NEVER_COLLAPSED.some((marker) => type.includes(marker));
}

/** Three, for `lib/tradeoff-row-groups.ts`'s reason. */
export const MIN_COLLAPSED_RUN = 3;

/** The machine an event names, or null for a job-level event that names
 * none. Null is a machine identity of its own here: a run of job-level
 * events of one type is a real run, and it is never merged with a run from a
 * named machine. */
export function ledgerNodeId(event: JobEvent): string | null {
  const value = event.data?.node_id;
  return typeof value === "string" && value.length > 0 ? value : null;
}

export interface LedgerEventItem {
  kind: "event";
  event: JobEvent;
}

export interface LedgerRunItem {
  kind: "run";
  /** Stable across polls: built from the run's type, machine and EARLIEST
   * stamp, none of which move as the run grows. A key built from the count
   * or from the newest stamp would change on every renewal and snap an
   * expanded run shut under the reader. */
  id: string;
  type: string;
  nodeId: string | null;
  /** Present only on a federated job — see `JobEvent.round`. */
  round: number | undefined;
  count: number;
  /** The earliest and latest stamps in the run, verbatim ISO, whichever
   * order the events arrived in. */
  startedAt: string;
  endedAt: string;
  /** Every event this run stands for, in the order it was given them.
   * Nothing summarised, nothing dropped: expanding a run renders exactly
   * this array. */
  events: JobEvent[];
}

export type LedgerItem = LedgerEventItem | LedgerRunItem;

/** The run's span. An unparseable stamp orders nothing rather than winning
 * the comparison as NaN; a run where none of them parse falls back to input
 * order, which is the only ordering left and is still honest about which
 * strings the API sent. */
function spanOf(events: JobEvent[]): { startedAt: string; endedAt: string } {
  let earliest: JobEvent | null = null;
  let latest: JobEvent | null = null;
  let earliestMs = 0;
  let latestMs = 0;

  for (const event of events) {
    const ms = Date.parse(event.timestamp);
    if (Number.isNaN(ms)) continue;
    if (earliest === null || ms < earliestMs) {
      earliest = event;
      earliestMs = ms;
    }
    if (latest === null || ms > latestMs) {
      latest = event;
      latestMs = ms;
    }
  }

  return {
    startedAt: (earliest ?? events[0]).timestamp,
    endedAt: (latest ?? events[events.length - 1]).timestamp,
  };
}

function sameRun(a: JobEvent, b: JobEvent): boolean {
  return (
    a.type === b.type &&
    ledgerNodeId(a) === ledgerNodeId(b) &&
    a.round === b.round
  );
}

/** Groups a ledger for display: every uncollapsible event stays its own
 * item, in place, and a run of three or more consecutive collapsible events
 * of one type, machine and round becomes a single run item. Pure — the same
 * events always produce the same grouping, and every input event appears in
 * exactly one output item, once. */
export function collapseLedgerRuns(events: JobEvent[]): LedgerItem[] {
  const items: LedgerItem[] = [];
  let run: JobEvent[] = [];

  const flush = () => {
    if (run.length === 0) return;
    if (run.length >= MIN_COLLAPSED_RUN) {
      const span = spanOf(run);
      const nodeId = ledgerNodeId(run[0]);
      items.push({
        kind: "run",
        id: `run-${run[0].type}-${nodeId ?? ""}-${run[0].round ?? ""}-${span.startedAt}`,
        type: run[0].type,
        nodeId,
        round: run[0].round,
        count: run.length,
        startedAt: span.startedAt,
        endedAt: span.endedAt,
        events: run,
      });
    } else {
      for (const event of run) items.push({ kind: "event", event });
    }
    run = [];
  };

  for (const event of events) {
    if (!isCollapsibleType(event.type)) {
      flush();
      items.push({ kind: "event", event });
      continue;
    }
    if (run.length > 0 && !sameRun(run[0], event)) flush();
    run.push(event);
  }
  flush();

  return items;
}
