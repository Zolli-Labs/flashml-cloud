import { describe, expect, it } from "vitest";
import {
  checkpointRowFor,
  deriveTaskDetail,
  deriveTaskTimeline,
  eventsForTask,
  parseTimestamp,
  taskKey,
  type TaskTimelineAttempt,
  type TaskTimelineNote,
} from "./task-detail";
import type { CheckpointPanel } from "./task-checkpoints";
import type { JobEvent, JobTask } from "./cloud-api";

/**
 * The expanded task row is the only place this console tells the story of one
 * task, and the story it has to tell is the one the fixture below records: a
 * coordinator outage that cost two machines their leases before a third
 * finished the work. Every assertion here exists because getting it wrong
 * would either lose a chapter of that story (an event dropped for carrying no
 * `node_id`) or invent one (an attempt reconstructed from a count, a machine
 * id resolved against a list it does not belong to).
 *
 * THE FIXTURE IS THE REAL SHAPE. `TASKS` is a real `/tasks` payload from a
 * job the leases backend ran; the events mirror the ledger that produced it.
 */

function ev(
  type: string,
  at: string,
  data: Record<string, unknown> = {},
  message = ""
): JobEvent {
  return {
    job_id: "job-leases-1",
    type,
    timestamp: at,
    source: "flashruntime.leases",
    message,
    data,
  };
}

const T = (m: number, s: number) =>
  `2026-08-13T10:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}Z`;

const NODE_A = "fn-fa5b036f46f84538";
const NODE_B = "fn-3949134a0acd4d91";
const NODE_C = "fn-1af2567e03d744e8";

/** Verbatim from `GET /v1alpha1/jobs/{id}/tasks`. */
const TASK: JobTask = {
  task_id: "task-000",
  state: "COMPLETED",
  attempts: 3,
  max_attempts: 4,
  node_id: NODE_C,
  deadline: null,
};

/** Three claims, two lost leases, one completion — and the two TASK_* events
 * that carry no machine and explain the gaps between them. */
const EVENTS: JobEvent[] = [
  ev("TASK_CREATED", T(0, 0), { task_id: "task-000", node_id: null }, "task-000 created"),
  ev("LEASE_CLAIMED", T(0, 5), { task_id: "task-000", node_id: NODE_A }),
  ev("LEASE_RENEWED", T(0, 35), { task_id: "task-000", node_id: NODE_A }),
  ev("LEASE_RENEWED", T(1, 5), { task_id: "task-000", node_id: NODE_A }),
  ev("LEASE_EXPIRED", T(2, 5), { task_id: "task-000", node_id: NODE_A }),
  ev("TASK_REQUEUED", T(2, 6), { task_id: "task-000" }, "task-000 requeued"),
  ev("LEASE_CLAIMED", T(2, 10), { task_id: "task-000", node_id: NODE_B }),
  ev("LEASE_RENEWED", T(2, 40), { task_id: "task-000", node_id: NODE_B }),
  ev("LEASE_EXPIRED", T(3, 40), { task_id: "task-000", node_id: NODE_B }),
  ev("TASK_REQUEUED", T(3, 41), { task_id: "task-000" }, "task-000 requeued"),
  ev("LEASE_CLAIMED", T(3, 45), { task_id: "task-000", node_id: NODE_C }),
  ev("LEASE_RENEWED", T(4, 15), { task_id: "task-000", node_id: NODE_C }),
  ev("LEASE_RENEWED", T(4, 45), { task_id: "task-000", node_id: NODE_C }),
  ev("LEASE_RENEWED", T(5, 15), { task_id: "task-000", node_id: NODE_C }),
  ev("TASK_ATTEMPT_COMPLETED", T(5, 30), {
    task_id: "task-000",
    node_id: NODE_C,
  }),
];

const attemptsOf = (entries: { kind: string }[]) =>
  entries.filter((e) => e.kind === "attempt") as TaskTimelineAttempt[];
const notesOf = (entries: { kind: string }[]) =>
  entries.filter((e) => e.kind === "note") as TaskTimelineNote[];

describe("deriveTaskTimeline — the attempt churn of one real task", () => {
  const timeline = deriveTaskTimeline(EVENTS, TASK);

  it("recovers all three attempts, in claim order", () => {
    expect(timeline.attempts.map((a) => a.index)).toEqual([1, 2, 3]);
  });

  // The rule this module will not bend: an `fn-*` id is a lease holder in the
  // coordinator's ledger, not a machine in this account's list. It is printed
  // and never resolved.
  it("carries the node ids through verbatim", () => {
    expect(timeline.attempts.map((a) => a.nodeId)).toEqual([
      NODE_A,
      NODE_B,
      NODE_C,
    ]);
  });

  it("counts renewals per attempt instead of listing them", () => {
    expect(timeline.attempts.map((a) => a.renewals)).toEqual([2, 1, 3]);
  });

  it("ends the first two as expired and the last as accepted", () => {
    expect(timeline.attempts.map((a) => a.outcome)).toEqual([
      "expired",
      "expired",
      "accepted",
    ]);
    expect(timeline.attempts.map((a) => a.closedBy)).toEqual([
      "LEASE_EXPIRED",
      "LEASE_EXPIRED",
      "TASK_ATTEMPT_COMPLETED",
    ]);
  });

  it("dates every attempt from the ledger and closes each one", () => {
    expect(timeline.attempts[0].claimedAt).toBe(Date.parse(T(0, 5)));
    expect(timeline.attempts[2].endedAt).toBe(Date.parse(T(5, 30)));
    for (const a of timeline.attempts) expect(a.endedAt).not.toBeNull();
  });

  // The whole reason this module exists rather than reusing `deriveAttempts`:
  // those two events carry no `node_id` and the fleet-wide walk drops them.
  // They are what explains the gap between one machine losing the work and
  // the next picking it up.
  it("keeps the task-scoped events that carry no machine", () => {
    expect(notesOf(timeline.entries).map((n) => n.type)).toEqual([
      "TASK_CREATED",
      "TASK_REQUEUED",
      "TASK_REQUEUED",
    ]);
    for (const n of timeline.notes) {
      expect(n.reason).toBe("no-node");
      expect(n.nodeId).toBeNull();
    }
  });

  it("interleaves attempts and notes into one chronological read", () => {
    expect(
      timeline.entries.map((e) =>
        e.kind === "attempt" ? `claim:${e.nodeId}` : e.type
      )
    ).toEqual([
      "TASK_CREATED",
      `claim:${NODE_A}`,
      "TASK_REQUEUED",
      `claim:${NODE_B}`,
      "TASK_REQUEUED",
      `claim:${NODE_C}`,
    ]);
  });

  it("is not empty, and counts every event it read", () => {
    expect(timeline.empty).toBe(false);
    expect(timeline.emptyReason).toBeNull();
    expect(timeline.eventCount).toBe(EVENTS.length);
  });
});

describe("deriveTaskTimeline — ledgers that arrive wrong", () => {
  // The API paginates and the console concatenates; nothing guarantees the
  // order the browser ends up holding. A claim rendered after the expiry it
  // preceded is a wrong story, not a cosmetic issue.
  it("reads the same story from a shuffled event list", () => {
    const shuffled = [...EVENTS].reverse();
    const fromShuffled = deriveTaskTimeline(shuffled, TASK);
    expect(fromShuffled.attempts.map((a) => [a.nodeId, a.renewals, a.outcome])).toEqual(
      deriveTaskTimeline(EVENTS, TASK).attempts.map((a) => [
        a.nodeId,
        a.renewals,
        a.outcome,
      ])
    );
  });

  it("keeps an undateable event rather than dropping or dating it", () => {
    const timeline = deriveTaskTimeline(
      [...EVENTS, ev("TASK_NOTE", "not-a-timestamp", { task_id: "task-000" })],
      TASK
    );
    const last = timeline.entries[timeline.entries.length - 1];
    expect(last).toMatchObject({ kind: "note", type: "TASK_NOTE", at: null });
    // Null, never 0 — `new Date(0)` renders as a confident 1970.
    expect((last as TaskTimelineNote).at).not.toBe(0);
  });

  it("closes an attempt as superseded when another machine claims over it", () => {
    const timeline = deriveTaskTimeline(
      [
        ev("LEASE_CLAIMED", T(0, 5), { task_id: "task-000", node_id: NODE_A }),
        ev("LEASE_CLAIMED", T(1, 0), { task_id: "task-000", node_id: NODE_B }),
      ],
      TASK
    );
    expect(timeline.attempts[0]).toMatchObject({
      nodeId: NODE_A,
      outcome: "superseded",
      endedAt: Date.parse(T(1, 0)),
      // Nothing in the ledger said how it ended, so nothing is named.
      closedBy: null,
    });
    expect(timeline.attempts[1].outcome).toBe("running");
    expect(timeline.attempts[1].endedAt).toBeNull();
  });

  it("keeps a renewal that matches no open attempt, as an orphan", () => {
    const timeline = deriveTaskTimeline(
      [ev("LEASE_RENEWED", T(0, 30), { task_id: "task-000", node_id: NODE_A })],
      TASK
    );
    expect(timeline.attempts).toHaveLength(0);
    expect(timeline.notes[0]).toMatchObject({
      type: "LEASE_RENEWED",
      reason: "orphan",
      nodeId: NODE_A,
    });
  });

  it("does not open an attempt for a claim that names no machine", () => {
    const timeline = deriveTaskTimeline(
      [ev("LEASE_CLAIMED", T(0, 5), { task_id: "task-000" })],
      TASK
    );
    expect(timeline.attempts).toHaveLength(0);
    expect(timeline.notes[0]).toMatchObject({
      type: "LEASE_CLAIMED",
      reason: "no-node",
    });
  });
});

describe("deriveTaskTimeline — scoping", () => {
  it("ignores events belonging to another task", () => {
    const timeline = deriveTaskTimeline(
      [
        ...EVENTS,
        ev("LEASE_CLAIMED", T(0, 6), { task_id: "task-001", node_id: NODE_A }),
      ],
      TASK
    );
    expect(timeline.attempts).toHaveLength(3);
    expect(timeline.eventCount).toBe(EVENTS.length);
  });

  // A federated job repeats task ids across rounds. Merging them would fuse
  // three separate runs of the same task into one impossible history.
  it("keeps rounds apart when the task carries one", () => {
    const round = (n: number): JobEvent => ({
      ...ev("LEASE_CLAIMED", T(n, 0), { task_id: "task-000", node_id: NODE_A }),
      round: n,
    });
    const task: JobTask = { ...TASK, round: 2 };
    const timeline = deriveTaskTimeline([round(1), round(2), round(3)], task);
    expect(timeline.attempts).toHaveLength(1);
    expect(eventsForTask([round(1), round(2), round(3)], task)).toHaveLength(1);
  });

  it("keys a task by round and id, not by position", () => {
    expect(taskKey(TASK)).toBe("|task-000");
    expect(taskKey({ ...TASK, round: 2 })).toBe("2|task-000");
    expect(taskKey({ ...TASK, round: 2 })).not.toBe(taskKey(TASK));
  });
});

describe("deriveTaskTimeline — a task the ledger never names", () => {
  it("says what is missing and what the coordinator counts anyway", () => {
    const timeline = deriveTaskTimeline(
      [ev("LEASE_CLAIMED", T(0, 5), { task_id: "task-999", node_id: NODE_A })],
      TASK
    );
    expect(timeline.empty).toBe(true);
    expect(timeline.entries).toEqual([]);
    expect(timeline.emptyReason).toContain("task-000");
    expect(timeline.emptyReason).toContain("3 attempts");
    expect(timeline.emptyReason).toContain("not a task nothing has run");
  });

  it("separates 'not read yet' from 'read, and it says nothing'", () => {
    expect(deriveTaskTimeline([], TASK).emptyReason).toContain(
      "No events have been read"
    );
  });

  it("says so plainly for a task nothing has claimed", () => {
    const fresh: JobTask = {
      ...TASK,
      state: "PENDING",
      attempts: 0,
      node_id: null,
    };
    const reason = deriveTaskTimeline(
      [ev("JOB_ACCEPTED", T(0, 0))],
      fresh
    ).emptyReason;
    expect(reason).toContain("Nothing has claimed this task yet.");
  });
});

describe("parseTimestamp", () => {
  // The bug this exists to make impossible: `new Date(null)` is the epoch and
  // renders as a confident "1/1/1970", which is worse than an em dash.
  it("returns null for a missing deadline rather than the epoch", () => {
    expect(parseTimestamp(null)).toBeNull();
    expect(parseTimestamp(undefined)).toBeNull();
    expect(parseTimestamp("")).toBeNull();
    expect(parseTimestamp("whenever")).toBeNull();
    expect(parseTimestamp(T(1, 0))).toBe(Date.parse(T(1, 0)));
  });
});

describe("deriveTaskDetail", () => {
  const detail = deriveTaskDetail({ task: TASK, events: EVENTS });

  it("passes the coordinator's current state through unchanged", () => {
    expect(detail).toMatchObject({
      taskId: "task-000",
      state: "COMPLETED",
      attempts: 3,
      maxAttempts: 4,
      nodeId: NODE_C,
      round: null,
      deadlineMs: null,
    });
  });

  it("agrees with the coordinator's tally, so says nothing about it", () => {
    expect(detail.ledgerAttempts).toBe(3);
    expect(detail.attemptGapNote).toBeNull();
  });

  it("shows both counts when the ledger has seen fewer attempts", () => {
    const partial = deriveTaskDetail({
      task: TASK,
      events: EVENTS.slice(0, 5),
    });
    expect(partial.ledgerAttempts).toBe(1);
    expect(partial.attemptGapNote).toContain("3 attempts");
    expect(partial.attemptGapNote).toContain("accounts for 1");
  });

  it("shows both counts when the ledger has seen more", () => {
    const undercounted = deriveTaskDetail({
      task: { ...TASK, attempts: 1 },
      events: EVENTS,
    });
    expect(undercounted.attemptGapNote).toContain("3 lease claims");
    expect(undercounted.attemptGapNote).toContain("tally is 1");
  });

  it("says nothing about a gap it cannot see", () => {
    expect(deriveTaskDetail({ task: TASK, events: [] }).attemptGapNote).toBeNull();
  });

  it("keeps a live deadline as a parsed instant", () => {
    const leased = deriveTaskDetail({
      task: { ...TASK, state: "LEASED", deadline: T(6, 0) },
      events: EVENTS,
    });
    expect(leased.deadlineMs).toBe(Date.parse(T(6, 0)));
  });
});

describe("checkpointRowFor", () => {
  const row = {
    taskId: "task-000",
    taskState: "LEASED",
    kind: "committed" as const,
    step: 40,
    committedAt: T(4, 0),
    validation: "hash_verified",
    errorDetail: null,
    attempts: 3,
    latestAttemptStartedAt: Date.parse(T(3, 45)),
    resumedFromStep: 40,
  };
  const panel = (over: Partial<CheckpointPanel>): CheckpointPanel => ({
    state: "rows",
    rows: [row],
    ambiguityNote: null,
    resumeNote: null,
    truncationNote: null,
    ...over,
  });

  it("finds this task's row", () => {
    expect(checkpointRowFor(panel({}), TASK)?.step).toBe(40);
    expect(
      deriveTaskDetail({ task: TASK, events: EVENTS, checkpoints: panel({}) })
        .checkpoint?.validation
    ).toBe("hash_verified");
  });

  it("has nothing to say for another task, or for no panel at all", () => {
    expect(checkpointRowFor(panel({}), { ...TASK, task_id: "task-001" })).toBeNull();
    expect(checkpointRowFor(null, TASK)).toBeNull();
    expect(checkpointRowFor(undefined, TASK)).toBeNull();
  });

  // `settled`, `unreadable` and `absent` are statements about the whole job.
  // The card above the table makes them once; paraphrasing them per row is
  // three chances to word the same fact differently.
  it("refuses to speak for a panel that is not showing rows", () => {
    for (const state of ["settled", "unreadable", "absent"] as const) {
      expect(checkpointRowFor(panel({ state, rows: [row] }), TASK)).toBeNull();
    }
  });
});

describe("attemptsOf/notesOf are only test helpers", () => {
  it("splits the entry union the way the component does", () => {
    const timeline = deriveTaskTimeline(EVENTS, TASK);
    expect(attemptsOf(timeline.entries)).toHaveLength(3);
    expect(notesOf(timeline.entries)).toHaveLength(3);
  });
});
