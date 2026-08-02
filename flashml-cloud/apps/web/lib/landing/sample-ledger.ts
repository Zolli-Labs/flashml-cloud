// SAMPLE DATA. NOT A REAL RUN.
//
// Every `type` below is a real member of `EventType` in
// `flashruntime.protocol.v1alpha1`, and the shape matches the real `Event`
// model (type, timestamp, source, message, data). The VALUES are invented:
// node ids, step numbers and losses did not come from an execution.
//
// Per the design spec §10.1, this file is a placeholder for a captured
// ledger from a real `make e2e-demo` run with a deliberately killed agent.
// Swapping it is the whole job: keep the export shape, replace the array.
// Nothing on the landing page is allowed to describe these numbers as
// measured, and the ledger component renders a "sample" label for exactly
// this reason.

/** Mirrors `flashruntime.protocol.v1alpha1.EventType` members used here. */
export type LedgerEventType =
  | "JOB_ACCEPTED"
  | "TASK_CREATED"
  | "LEASE_CLAIMED"
  | "LEASE_RENEWED"
  | "LEASE_EXPIRED"
  | "TASK_REQUEUED"
  | "TASK_COMMIT_ACCEPTED"
  | "NODE_HEARTBEAT_LOST"
  | "CHECKPOINT_MANIFEST_COMMITTED"
  | "FAILURE_CLASSIFIED"
  | "RECOVERY_ACTION_SELECTED"
  | "ITERATION_COMPLETED"
  | "ARTIFACT_COMMITTED"
  | "JOB_SUCCEEDED";

/** How a row reads, not what it is. `alert` is a loss, `good` is progress
 * accepted, `note` is everything else. Deliberately separate from the event
 * type so the palette rule stays in one place. */
export type LedgerTone = "note" | "alert" | "good";

export interface LedgerEvent {
  type: LedgerEventType;
  /** Seconds since job start. Rendered as a relative offset, never as a
   * wall-clock time, because a fabricated timestamp reads as a real one. */
  at: number;
  /** `Event.source` upstream: the raw signal this was derived from. */
  source: string;
  detail: string;
  tone: LedgerTone;
}

export const SAMPLE_LEDGER: LedgerEvent[] = [
  { type: "JOB_ACCEPTED", at: 0, source: "coordinator", detail: "resnet-cifar-fedavg", tone: "note" },
  { type: "TASK_CREATED", at: 1, source: "planner", detail: "16 tasks", tone: "note" },
  { type: "LEASE_CLAIMED", at: 4, source: "flashnode", detail: "node-7fb2 task-03", tone: "note" },
  { type: "LEASE_CLAIMED", at: 4, source: "flashnode", detail: "node-1c94 task-04", tone: "note" },
  { type: "LEASE_RENEWED", at: 34, source: "flashnode", detail: "node-7fb2 +60s", tone: "note" },
  { type: "CHECKPOINT_MANIFEST_COMMITTED", at: 61, source: "checkpoint.catalog", detail: "step 1400 restore_verified", tone: "good" },
  { type: "TASK_COMMIT_ACCEPTED", at: 63, source: "coordinator", detail: "task-04 accepted", tone: "good" },
  { type: "NODE_HEARTBEAT_LOST", at: 88, source: "monitor", detail: "node-7fb2 silent 45s", tone: "alert" },
  { type: "LEASE_EXPIRED", at: 91, source: "lease.sweep", detail: "task-03 deadline passed", tone: "alert" },
  { type: "FAILURE_CLASSIFIED", at: 91, source: "recovery.taxonomy", detail: "NODE_LOSS", tone: "alert" },
  { type: "RECOVERY_ACTION_SELECTED", at: 91, source: "recovery.policy", detail: "RETRY_TASK scope=task", tone: "note" },
  { type: "TASK_REQUEUED", at: 92, source: "coordinator", detail: "task-03 attempt 2/3", tone: "note" },
  { type: "LEASE_CLAIMED", at: 95, source: "flashnode", detail: "node-be40 task-03", tone: "note" },
  { type: "TASK_COMMIT_ACCEPTED", at: 148, source: "coordinator", detail: "task-03 accepted", tone: "good" },
  { type: "ITERATION_COMPLETED", at: 151, source: "workload.summary", detail: "round 4 of 8", tone: "note" },
  { type: "ARTIFACT_COMMITTED", at: 402, source: "artifacts", detail: "weights.safetensors", tone: "good" },
  { type: "JOB_SUCCEEDED", at: 404, source: "coordinator", detail: "16 of 16 accepted", tone: "good" },
];

/** The three beats the recovery section walks through, as slices of the
 * ledger above. Indices, not copies, so the two views cannot drift. */
export const RECOVERY_BEATS = [
  { from: 2, to: 7, key: "claimed" },
  { from: 7, to: 11, key: "lost" },
  { from: 11, to: 14, key: "resumed" },
] as const;
