export type ZolliRole =
  | "captain"
  | "worker"
  | "scout"
  | "keeper"
  | "relay"
  | "builder";

export type ZolliRoleDefinition = {
  label: string;
  subtitle: string;
  description: string;
  color: string;
};

export const ZOLLI_ROLES: Record<ZolliRole, ZolliRoleDefinition> = {
  captain: {
    label: "Captain",
    subtitle: "Coordinator",
    description: "Plans the work and keeps the crew in sync.",
    color: "#ef6828",
  },
  worker: {
    label: "Worker",
    subtitle: "Executor",
    description: "Claims tasks, computes, and delivers results.",
    color: "#1f6e5d",
  },
  scout: {
    label: "Scout",
    subtitle: "New Zolli",
    description: "Helps a new machine join the crew.",
    color: "#e7ad2b",
  },
  keeper: {
    label: "Keeper",
    subtitle: "Checkpoint",
    description: "Preserves progress through verified checkpoints.",
    color: "#b8b2ac",
  },
  relay: {
    label: "Relay",
    subtitle: "Handoff",
    description: "Hands interrupted work to the next Zolli.",
    color: "#252321",
  },
  builder: {
    label: "Builder",
    subtitle: "Training / Inference",
    description: "Turns code and data into completed models.",
    color: "#f48b68",
  },
};
