export const WORKFLOW_EVENTS = [
  "LEASE_CLAIMED",
  "CHECKPOINT_MANIFEST_COMMITTED",
  "NODE_HEARTBEAT_LOST",
  "TASK_REQUEUED",
  "TASK_COMMIT_ACCEPTED",
] as const;

export type WorkflowEvent = (typeof WORKFLOW_EVENTS)[number];
export type WorkflowStepId =
  | "connect"
  | "register"
  | "submit"
  | "parallel"
  | "checkpoint"
  | "recover"
  | "accept";

export interface WorkflowStep {
  id: WorkflowStepId;
  key: WorkflowStepId;
  eyebrow: string;
  headline: string;
  title: string;
  body: string;
  outcome: string;
  event: WorkflowEvent | null;
  state: string;
}

export const WORKFLOW_STEPS = [
  {
    id: "connect",
    key: "connect",
    eyebrow: "01 / connect",
    headline: "Connect machines",
    title: "Connect machines",
    body: "Connect machines you operate or capacity you choose to add.",
    outcome: "Machines connected",
    event: null,
    state: "Machines connected",
  },
  {
    id: "register",
    key: "register",
    eyebrow: "02 / register",
    headline: "Register capacity",
    title: "Register capacity",
    body: "The control plane records the node information available to it.",
    outcome: "Capacity visible",
    event: null,
    state: "Capacity visible",
  },
  {
    id: "submit",
    key: "submit",
    eyebrow: "03 / submit",
    headline: "Submit one job",
    title: "Submit one job",
    body: "The operator supplies the repository and workload definition through the existing console flow.",
    outcome: "LEASE_CLAIMED",
    event: "LEASE_CLAIMED",
    state: "LEASE_CLAIMED",
  },
  {
    id: "parallel",
    key: "parallel",
    eyebrow: "04 / parallel",
    headline: "Split and lease tasks",
    title: "Split and lease tasks",
    body: "The control plane assigns bounded parallel work to available nodes.",
    outcome: "Tasks running",
    event: null,
    state: "Tasks running",
  },
  {
    id: "checkpoint",
    key: "checkpoint",
    eyebrow: "05 / checkpoint",
    headline: "Checkpoint progress",
    title: "Checkpoint progress",
    body: "Workers commit checkpoint manifests as progress becomes available.",
    outcome: "CHECKPOINT_MANIFEST_COMMITTED",
    event: "CHECKPOINT_MANIFEST_COMMITTED",
    state: "CHECKPOINT_MANIFEST_COMMITTED",
  },
  {
    id: "recover",
    key: "recover",
    eyebrow: "06 / recover",
    headline: "Recover interrupted work",
    title: "Recover interrupted work",
    body: "A missing heartbeat can requeue work from its recorded checkpoint.",
    outcome: "TASK_REQUEUED",
    event: "NODE_HEARTBEAT_LOST",
    state: "TASK_REQUEUED",
  },
  {
    id: "accept",
    key: "accept",
    eyebrow: "07 / accept",
    headline: "Accept one result",
    title: "Accept one result",
    body: "The control plane records the accepted task commit.",
    outcome: "TASK_COMMIT_ACCEPTED",
    event: "TASK_COMMIT_ACCEPTED",
    state: "TASK_COMMIT_ACCEPTED",
  },
] as const satisfies readonly WorkflowStep[];
