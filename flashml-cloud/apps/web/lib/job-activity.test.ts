import { describe, expect, it } from "vitest";
import {
  deriveAttemptCoverage,
  deriveAttempts,
  deriveProgress,
  deriveStallReason,
  nodesFromAttempts,
} from "./job-activity";
import type { JobEvent, JobTask } from "./cloud-api";

/**
 * These derivations are the console's only source of attempt history, because
 * the coordinator's /tasks view has none. If they are wrong the placement view
 * silently misattributes work to the wrong machine, which is worse than
 * showing nothing at all on a product whose pitch is an honest audit trail.
 */

function ev(
  type: string,
  at: string,
  data: Record<string, unknown> = {}
): JobEvent {
  return {
    job_id: "j1",
    type,
    timestamp: at,
    source: "flashruntime.leases",
    message: "",
    data,
  };
}

const T = (s: number) => `2026-08-02T10:00:${String(s).padStart(2, "0")}Z`;

describe("deriveAttempts", () => {
  it("pairs a claim with its accepted commit", () => {
    const attempts = deriveAttempts([
      ev("LEASE_CLAIMED", T(1), { task_id: "t1", node_id: "n1" }),
      ev("TASK_COMMIT_ACCEPTED", T(5), { task_id: "t1", node_id: "n1" }),
    ]);
    expect(attempts).toHaveLength(1);
    expect(attempts[0]).toMatchObject({
      taskId: "t1",
      nodeId: "n1",
      outcome: "accepted",
    });
    expect(attempts[0].endedAt).toBeGreaterThan(attempts[0].startedAt);
  });

  // The whole product in one test: a machine takes work, dies, and another
  // finishes it. BOTH attempts must survive, on their own lanes.
  it("keeps both attempts when a lease expires and another node finishes", () => {
    const attempts = deriveAttempts([
      ev("LEASE_CLAIMED", T(1), { task_id: "t1", node_id: "dead" }),
      ev("LEASE_EXPIRED", T(9), { task_id: "t1", node_id: "dead" }),
      ev("LEASE_CLAIMED", T(10), { task_id: "t1", node_id: "alive" }),
      ev("TASK_COMMIT_ACCEPTED", T(20), { task_id: "t1", node_id: "alive" }),
    ]);
    expect(attempts).toHaveLength(2);
    expect(attempts.map((a) => [a.nodeId, a.outcome])).toEqual([
      ["dead", "expired"],
      ["alive", "accepted"],
    ]);
    expect(nodesFromAttempts(attempts)).toEqual(["dead", "alive"]);
  });

  it("leaves an unfinished attempt running rather than inventing an end", () => {
    const attempts = deriveAttempts([
      ev("LEASE_CLAIMED", T(1), { task_id: "t1", node_id: "n1" }),
    ]);
    expect(attempts[0].outcome).toBe("running");
    expect(attempts[0].endedAt).toBeNull();
  });

  it("ignores job-level events that carry no placement", () => {
    expect(
      deriveAttempts([ev("JOB_ACCEPTED", T(0)), ev("JOB_SUCCEEDED", T(30))])
    ).toEqual([]);
  });

  // Federated jobs repeat task ids across rounds. Merging them would collapse
  // distinct work into one bar.
  it("keeps same-named tasks from different rounds apart", () => {
    const a = { ...ev("LEASE_CLAIMED", T(1), { task_id: "t1", node_id: "n1" }), round: 1 };
    const b = { ...ev("LEASE_CLAIMED", T(2), { task_id: "t1", node_id: "n1" }), round: 2 };
    expect(deriveAttempts([a, b])).toHaveLength(2);
  });
});

describe("deriveProgress", () => {
  it("counts accepted separately from attempts spent", () => {
    const tasks: JobTask[] = [
      { task_id: "t1", state: "COMPLETED", attempts: 2, max_attempts: 3, node_id: "n2", deadline: null },
      { task_id: "t2", state: "LEASED", attempts: 1, max_attempts: 3, node_id: "n1", deadline: null },
    ];
    const attempts = deriveAttempts([
      ev("LEASE_CLAIMED", T(1), { task_id: "t1", node_id: "n1" }),
      ev("LEASE_EXPIRED", T(5), { task_id: "t1", node_id: "n1" }),
      ev("LEASE_CLAIMED", T(6), { task_id: "t1", node_id: "n2" }),
      ev("TASK_COMMIT_ACCEPTED", T(9), { task_id: "t1", node_id: "n2" }),
      ev("LEASE_CLAIMED", T(10), { task_id: "t2", node_id: "n1" }),
    ]);
    expect(deriveProgress(tasks, attempts)).toEqual({
      accepted: 1,
      total: 2,
      attemptsSpent: 3,
      wasted: 1,
    });
  });
});

describe("deriveAttemptCoverage", () => {
  const task = (over: Partial<JobTask> = {}): JobTask => ({
    task_id: "t1",
    state: "COMPLETED",
    attempts: 3,
    max_attempts: 4,
    node_id: "fn-1af2567e03d744e8",
    deadline: null,
    ...over,
  });

  it("uses the ledger whenever the ledger answered", () => {
    const attempts = deriveAttempts([
      ev("LEASE_CLAIMED", T(1), { task_id: "t1", node_id: "n1" }),
    ]);
    expect(deriveAttemptCoverage([task()], attempts)).toEqual({
      source: "ledger",
      spent: 1,
      tasks: 0,
      note: null,
    });
  });

  // The case this exists for: a job whose lease events carry no `node_id`, so
  // `deriveAttempts` drops every one of them and the placement view would
  // otherwise say nothing about attempts while the table beside it shows 3/4.
  it("falls back to the coordinator's own tally, and says it is doing so", () => {
    const coverage = deriveAttemptCoverage(
      [task()],
      deriveAttempts([ev("TASK_REQUEUED", T(2), { task_id: "t1" })])
    );
    expect(coverage.source).toBe("tasks");
    expect(coverage.spent).toBe(3);
    expect(coverage.tasks).toBe(1);
    expect(coverage.note).toContain("3 attempts across 1 task");
    expect(coverage.note).toContain("counted by the coordinator");
  });

  it("adds up attempts across every task", () => {
    const coverage = deriveAttemptCoverage(
      [task(), task({ task_id: "t2", attempts: 2 })],
      []
    );
    expect(coverage.spent).toBe(5);
    expect(coverage.note).toContain("5 attempts across 2 tasks");
  });

  it("says nothing at all when nothing has been attempted", () => {
    expect(
      deriveAttemptCoverage([task({ attempts: 0, state: "PENDING" })], [])
    ).toEqual({ source: "none", spent: 0, tasks: 0, note: null });
    expect(deriveAttemptCoverage([], [])).toMatchObject({ source: "none" });
  });
});

describe("deriveStallReason", () => {
  it("is silent for a job that is not running", () => {
    expect(deriveStallReason("SUCCEEDED", [], [])).toBeNull();
  });

  // The one that matters most: the policy stopping on purpose must never
  // look like a hang.
  it("surfaces RECOVERY_FROZEN above everything else", () => {
    const frozen = ev("RECOVERY_FROZEN", T(5));
    frozen.message = "correlated failures, automation stopped";
    const reason = deriveStallReason(
      "RUNNING",
      [{ task_id: "t1", state: "PENDING", attempts: 0, max_attempts: 3, node_id: null, deadline: null }],
      [frozen]
    );
    expect(reason).toBe("correlated failures, automation stopped");
  });

  it("names unclaimed work when no machine has taken it", () => {
    const reason = deriveStallReason(
      "RUNNING",
      [{ task_id: "t1", state: "PENDING", attempts: 0, max_attempts: 3, node_id: null, deadline: null }],
      []
    );
    expect(reason).toMatch(/waiting to be claimed/);
  });

  it("reports the next deadline when everything is in flight", () => {
    const now = Date.parse(T(0));
    const reason = deriveStallReason(
      "RUNNING",
      [{ task_id: "t1", state: "LEASED", attempts: 1, max_attempts: 3, node_id: "n1", deadline: T(45) }],
      [],
      now
    );
    expect(reason).toMatch(/in flight/);
    expect(reason).toMatch(/45s/);
  });
});
