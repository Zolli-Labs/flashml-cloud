export type HeroSourceKey = "cloud" | "rented" | "owned" | "everyday";
export type HeroJobStepKey =
  | "submitted"
  | "assigned"
  | "checkpointed"
  | "lost"
  | "resumed"
  | "accepted";

export interface HeroSource {
  key: HeroSourceKey;
  label: string;
  shortLabel: string;
  examples: readonly string[];
  meaning: string;
  outcome: string;
}

export interface HeroJobStep {
  key: HeroJobStepKey;
  label: string;
  durationMs: number;
  progress: number;
}

export interface HeroRuntimeState {
  selectedSource: HeroSourceKey;
  jobStep: HeroJobStepKey;
  storyPlaying: boolean;
  fabricFailed: boolean;
  fabricEntranceComplete: boolean;
}

export type HeroRuntimeEvent =
  | { type: "select-source"; source: HeroSourceKey }
  | { type: "select-job-step"; jobStep: HeroJobStepKey }
  | { type: "advance-job-step"; jobStep: HeroJobStepKey }
  | { type: "toggle-story"; reducedMotion: boolean }
  | { type: "set-story-playing"; playing: boolean }
  | { type: "initialize-motion" }
  | { type: "fabric-failure" }
  | { type: "fabric-entrance-complete" };

export const HERO_SOURCES = [
  {
    key: "cloud",
    label: "Cloud / HPC",
    shortLabel: "Cloud / HPC",
    examples: ["Cloud VM", "HPC cluster", "Slurm node"],
    meaning: "External high-compute foundations your workloads can already reach.",
    outcome: "Use cloud and HPC as one part of the pool—not the definition of the pool.",
  },
  {
    key: "rented",
    label: "Rented GPU",
    shortLabel: "Rented GPU",
    examples: ["Runpod", "Vast.ai", "Lambda"],
    meaning: "Elastic GPU capacity rented for the work at hand.",
    outcome: "Bring temporary accelerators into the same recoverable execution path.",
  },
  {
    key: "owned",
    label: "Owned Infrastructure",
    shortLabel: "Owned",
    examples: ["GPU workstation", "Lab server", "Private rack"],
    meaning: "Infrastructure your team already operates and controls.",
    outcome: "Make existing capital useful without creating another isolated scheduler.",
  },
  {
    key: "everyday",
    label: "Everyday Machines",
    shortLabel: "Everyday",
    examples: ["Laptop", "Desktop", "Home server"],
    meaning: "Normal machines people deliberately make available as compute nodes.",
    outcome: "Turn compatible idle machines into useful first-class capacity with explicit control.",
  },
] as const satisfies readonly HeroSource[];

export const HERO_JOB_STEPS = [
  { key: "submitted", label: "Job submitted", durationMs: 950, progress: 0.03 },
  { key: "assigned", label: "Zolli assigns", durationMs: 1200, progress: 0.2 },
  { key: "checkpointed", label: "Checkpoint retained", durationMs: 1250, progress: 0.4 },
  { key: "lost", label: "Node lost", durationMs: 1100, progress: 0.6 },
  { key: "resumed", label: "Resumed elsewhere", durationMs: 1450, progress: 0.8 },
  { key: "accepted", label: "Result accepted", durationMs: 2100, progress: 0.97 },
] as const satisfies readonly HeroJobStep[];

export function createHeroRuntimeState(): HeroRuntimeState {
  return {
    selectedSource: "everyday",
    jobStep: "accepted",
    storyPlaying: false,
    fabricFailed: false,
    fabricEntranceComplete: false,
  };
}

export function reduceHeroRuntime(
  state: HeroRuntimeState,
  event: HeroRuntimeEvent,
): HeroRuntimeState {
  switch (event.type) {
    case "select-source":
      return { ...state, selectedSource: event.source, storyPlaying: false };
    case "select-job-step":
      return { ...state, jobStep: event.jobStep, storyPlaying: false };
    case "advance-job-step":
      return { ...state, jobStep: event.jobStep };
    case "toggle-story":
      return event.reducedMotion ? state : { ...state, storyPlaying: !state.storyPlaying };
    case "set-story-playing":
      return { ...state, storyPlaying: event.playing };
    case "initialize-motion":
      return { ...state, jobStep: "submitted", storyPlaying: true };
    case "fabric-failure":
      return state.fabricFailed && !state.storyPlaying
        ? state
        : { ...state, fabricFailed: true, storyPlaying: false };
    case "fabric-entrance-complete":
      return state.fabricEntranceComplete
        ? state
        : { ...state, fabricEntranceComplete: true };
  }
}

export function getNextHeroJobStep(key: HeroJobStepKey): HeroJobStepKey {
  const index = HERO_JOB_STEPS.findIndex((step) => step.key === key);
  return HERO_JOB_STEPS[(index + 1) % HERO_JOB_STEPS.length].key;
}

export function getHeroJobStep(key: HeroJobStepKey) {
  return HERO_JOB_STEPS.find((step) => step.key === key) ?? HERO_JOB_STEPS[0];
}

export function getHeroSource(key: HeroSourceKey) {
  return HERO_SOURCES.find((source) => source.key === key) ?? HERO_SOURCES.at(-1)!;
}
