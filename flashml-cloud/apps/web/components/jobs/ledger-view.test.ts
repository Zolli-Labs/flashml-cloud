import { describe, expect, it } from "vitest";

import {
  FAILURE_MARKERS,
  MIN_COLLAPSED_RUN,
  NEVER_COLLAPSED,
  collapseLedgerRuns,
  countByLedgerFilter,
  filterLedgerEvents,
  isCollapsibleType,
  isFailureEvent,
  ledgerNodeId,
  matchesLedgerFilter,
  type LedgerItem,
  type LedgerRunItem,
} from "./ledger-view";
import type { JobEvent } from "@/lib/cloud-api";

// Built at call time rather than kept as a fixture — the same pattern
// `lib/tradeoff-row-groups.test.ts` uses. Only type, machine, round and
// stamp vary across these tests; everything else satisfies the interface.
function event(
  type: string,
  {
    node,
    at,
    round,
    message,
  }: { node?: string | null; at?: string; round?: number; message?: string } = {}
): JobEvent {
  return {
    job_id: "job-1",
    type,
    timestamp: at ?? "2026-08-13T10:20:00Z",
    source: "coordinator",
    message: message ?? "",
    data: node === undefined || node === null ? {} : { node_id: node },
    ...(round === undefined ? {} : { round }),
  };
}

/** A run of one type on one machine, one second apart. */
function renewals(count: number, node = "fn-98ca4710", startMinute = 20) {
  return Array.from({ length: count }, (_, i) =>
    event("LEASE_RENEWED", {
      node,
      at: `2026-08-13T10:${String(startMinute).padStart(2, "0")}:${String(i).padStart(2, "0")}Z`,
    })
  );
}

/** Every event the input carried, reconstructed from the grouped output — a
 * run's `.events` and a plain item's `.event` are the only places an event
 * can be, so this is the "nothing dropped, nothing invented" check. */
function flatten(items: LedgerItem[]): JobEvent[] {
  return items.flatMap((item) =>
    item.kind === "event" ? [item.event] : item.events
  );
}

function runs(items: LedgerItem[]): LedgerRunItem[] {
  return items.filter((item): item is LedgerRunItem => item.kind === "run");
}

describe("collapseLedgerRuns — no event is ever dropped or duplicated", () => {
  it("reconstructs exactly the input ledger, in order", () => {
    const events = [
      event("LEASE_CLAIMED", { node: "fn-a" }),
      ...renewals(23, "fn-a"),
      event("TASK_COMMIT_ACCEPTED", { node: "fn-a" }),
      ...renewals(4, "fn-b", 30),
      event("LEASE_EXPIRED", { node: "fn-b" }),
    ];
    expect(flatten(collapseLedgerRuns(events))).toEqual(events);
  });

  it("returns nothing for an empty ledger", () => {
    expect(collapseLedgerRuns([])).toEqual([]);
  });

  it("carries the run's events verbatim — same objects, same order", () => {
    const events = renewals(5);
    const [run] = runs(collapseLedgerRuns(events));
    expect(run.events).toEqual(events);
    expect(run.events[0]).toBe(events[0]);
  });
});

describe("collapseLedgerRuns — what may be collapsed", () => {
  it("collapses a run of three or more into one item carrying the count", () => {
    const items = collapseLedgerRuns(renewals(23));
    expect(items).toHaveLength(1);
    const [run] = runs(items);
    expect(run.count).toBe(23);
    expect(run.type).toBe("LEASE_RENEWED");
    expect(run.nodeId).toBe("fn-98ca4710");
  });

  it("leaves a run of one or two exactly as it was", () => {
    for (const length of [1, 2]) {
      const items = collapseLedgerRuns(renewals(length));
      expect(items).toHaveLength(length);
      expect(runs(items)).toHaveLength(0);
    }
    expect(MIN_COLLAPSED_RUN).toBe(3);
  });

  it("never collapses across two different types", () => {
    const items = collapseLedgerRuns([
      ...renewals(3),
      event("HEARTBEAT_RECEIVED", { node: "fn-98ca4710" }),
      ...renewals(3),
    ]);
    expect(runs(items)).toHaveLength(2);
    expect(items).toHaveLength(3);
  });

  it("never collapses across two different machines", () => {
    const items = collapseLedgerRuns([
      ...renewals(4, "fn-a"),
      ...renewals(4, "fn-b"),
    ]);
    const grouped = runs(items);
    expect(grouped).toHaveLength(2);
    expect(grouped.map((r) => r.nodeId)).toEqual(["fn-a", "fn-b"]);
  });

  it("never collapses across two rounds of a federated job", () => {
    const items = collapseLedgerRuns([
      ...[0, 0, 0].map(() => event("LEASE_RENEWED", { node: "fn-a", round: 0 })),
      ...[1, 1, 1].map(() => event("LEASE_RENEWED", { node: "fn-a", round: 1 })),
    ]);
    const grouped = runs(items);
    expect(grouped).toHaveLength(2);
    expect(grouped.map((r) => r.round)).toEqual([0, 1]);
  });

  it("collapses a run of job-level events that name no machine", () => {
    const items = collapseLedgerRuns([
      event("JOB_PROGRESS"),
      event("JOB_PROGRESS"),
      event("JOB_PROGRESS"),
    ]);
    const [run] = runs(items);
    expect(run.count).toBe(3);
    expect(run.nodeId).toBeNull();
  });

  it("never merges a job-level run with a machine's run of the same type", () => {
    const items = collapseLedgerRuns([
      event("JOB_PROGRESS"),
      event("JOB_PROGRESS"),
      event("JOB_PROGRESS"),
      event("JOB_PROGRESS", { node: "fn-a" }),
      event("JOB_PROGRESS", { node: "fn-a" }),
      event("JOB_PROGRESS", { node: "fn-a" }),
    ]);
    expect(runs(items).map((r) => r.nodeId)).toEqual([null, "fn-a"]);
  });
});

describe("collapseLedgerRuns — the interesting types stay one row each", () => {
  it.each(NEVER_COLLAPSED.map((marker) => [marker]))(
    "keeps every %s event as its own row",
    (marker) => {
      const type = `TASK_${marker}`;
      expect(isCollapsibleType(type)).toBe(false);
      const items = collapseLedgerRuns(
        Array.from({ length: 6 }, () => event(type, { node: "fn-a" }))
      );
      expect(items).toHaveLength(6);
      expect(runs(items)).toHaveLength(0);
    }
  );

  it("does not let an outcome merge the runs on either side of it", () => {
    const items = collapseLedgerRuns([
      ...renewals(3, "fn-a"),
      event("LEASE_EXPIRED", { node: "fn-a" }),
      ...renewals(3, "fn-a"),
    ]);
    expect(items.map((i) => i.kind)).toEqual(["run", "event", "run"]);
  });

  it("treats an unknown collapsible type as collapsible and an unknown outcome as not", () => {
    expect(isCollapsibleType("SOMETHING_ELSE_HAPPENED")).toBe(true);
    expect(isCollapsibleType("TASK_COMMIT_REJECTED_STALE")).toBe(false);
  });
});

describe("collapseLedgerRuns — the span it reports", () => {
  it("reports the earliest and latest stamps in the run", () => {
    const [run] = runs(collapseLedgerRuns(renewals(4)));
    expect(run.startedAt).toBe("2026-08-13T10:20:00Z");
    expect(run.endedAt).toBe("2026-08-13T10:20:03Z");
  });

  it("reports the same span when the events arrive newest first", () => {
    const [run] = runs(collapseLedgerRuns([...renewals(4)].reverse()));
    expect(run.startedAt).toBe("2026-08-13T10:20:00Z");
    expect(run.endedAt).toBe("2026-08-13T10:20:03Z");
  });

  it("falls back to input order when no stamp parses, rather than inventing one", () => {
    const events = [
      event("LEASE_RENEWED", { node: "fn-a", at: "not-a-time" }),
      event("LEASE_RENEWED", { node: "fn-a", at: "also-not" }),
      event("LEASE_RENEWED", { node: "fn-a", at: "still-not" }),
    ];
    const [run] = runs(collapseLedgerRuns(events));
    expect(run.startedAt).toBe("not-a-time");
    expect(run.endedAt).toBe("still-not");
  });

  it("ignores an unparseable stamp rather than letting it win the comparison", () => {
    const events = [
      event("LEASE_RENEWED", { node: "fn-a", at: "2026-08-13T10:20:00Z" }),
      event("LEASE_RENEWED", { node: "fn-a", at: "nonsense" }),
      event("LEASE_RENEWED", { node: "fn-a", at: "2026-08-13T10:26:00Z" }),
    ];
    const [run] = runs(collapseLedgerRuns(events));
    expect(run.startedAt).toBe("2026-08-13T10:20:00Z");
    expect(run.endedAt).toBe("2026-08-13T10:26:00Z");
  });

  it("keys a run off its earliest stamp, so a growing run keeps its identity", () => {
    const first = runs(collapseLedgerRuns(renewals(3)))[0];
    const grown = runs(collapseLedgerRuns(renewals(9)))[0];
    expect(grown.id).toBe(first.id);
  });
});

describe("ledgerNodeId", () => {
  it("reads a machine id out of the event data", () => {
    expect(ledgerNodeId(event("LEASE_RENEWED", { node: "fn-a" }))).toBe("fn-a");
  });

  it("is null for a job-level event, an empty string, or a non-string", () => {
    expect(ledgerNodeId(event("JOB_SUBMITTED"))).toBeNull();
    expect(ledgerNodeId(event("JOB_SUBMITTED", { node: "" }))).toBeNull();
    const numeric = { ...event("JOB_SUBMITTED"), data: { node_id: 7 } };
    expect(ledgerNodeId(numeric)).toBeNull();
  });
});

describe("ledger filters — pure predicates on the type", () => {
  it("passes everything under All", () => {
    const events = [event("LEASE_RENEWED"), event("TASK_COMMIT_ACCEPTED")];
    expect(filterLedgerEvents(events, "all")).toBe(events);
  });

  it("selects commits, leases and failures by what the type contains", () => {
    expect(matchesLedgerFilter("TASK_COMMIT_ACCEPTED", "commits")).toBe(true);
    expect(matchesLedgerFilter("CHECKPOINT_MANIFEST_COMMITTED", "commits")).toBe(
      true
    );
    expect(matchesLedgerFilter("LEASE_RENEWED", "commits")).toBe(false);

    expect(matchesLedgerFilter("LEASE_CLAIMED", "leases")).toBe(true);
    expect(matchesLedgerFilter("TASK_COMMIT_ACCEPTED", "leases")).toBe(false);

    expect(matchesLedgerFilter("TASK_ATTEMPT_FAILED", "failures")).toBe(true);
    expect(matchesLedgerFilter("RECOVERY_FROZEN", "failures")).toBe(true);
    expect(matchesLedgerFilter("LEASE_CLAIMED", "failures")).toBe(false);
  });

  it("shows LEASE_EXPIRED under both Leases and Failures — the chips are not a partition", () => {
    expect(matchesLedgerFilter("LEASE_EXPIRED", "leases")).toBe(true);
    expect(matchesLedgerFilter("LEASE_EXPIRED", "failures")).toBe(true);
  });

  it("agrees with the row colouring about what a failure is", () => {
    for (const marker of FAILURE_MARKERS) {
      expect(isFailureEvent(`TASK_${marker}`)).toBe(true);
    }
    expect(isFailureEvent("TASK_COMMIT_ACCEPTED")).toBe(false);
  });

  it("counts every chip over the same events, overlaps included", () => {
    const counts = countByLedgerFilter([
      event("LEASE_CLAIMED"),
      event("LEASE_RENEWED"),
      event("LEASE_EXPIRED"),
      event("TASK_COMMIT_ACCEPTED"),
      event("TASK_COMMIT_REJECTED"),
    ]);
    expect(counts).toEqual({ all: 5, commits: 2, leases: 3, failures: 2 });
  });

  it("filters to exactly the events the predicate accepts", () => {
    const events = [
      event("LEASE_CLAIMED"),
      event("LEASE_RENEWED"),
      event("TASK_COMMIT_ACCEPTED"),
    ];
    expect(filterLedgerEvents(events, "leases")).toEqual([
      events[0],
      events[1],
    ]);
    expect(filterLedgerEvents(events, "commits")).toEqual([events[2]]);
  });
});
