export type WorkloadMode = "divide" | "resume";

export const WORKLOADS = [
  {
    number: "01",
    mode: "divide",
    title: "Model configuration search",
    body: "Run many model settings independently.",
    machineContext: "Laptops, CPU workstations, or rented GPUs.",
  },
  {
    number: "02",
    mode: "divide",
    title: "AI model evaluation",
    body: "Test prompts, datasets, or model versions as separate tasks.",
    machineContext: "CPU or GPU machines across a team.",
  },
  {
    number: "03",
    mode: "divide",
    title: "Independent file processing",
    body: "Embeddings, OCR, transcription, conversion, or data preparation.",
    machineContext: "Supported macOS, Linux, and compatible cloud machines.",
  },
  {
    number: "04",
    mode: "divide",
    title: "Simulations and research trials",
    body: "Monte Carlo experiments and independent rollouts.",
    machineContext: "Mixed personal, lab, and cloud machines.",
  },
  {
    number: "05",
    mode: "resume",
    title: "Checkpointable model training",
    body: "Save progress and continue after an interruption.",
    machineContext: "Linux machines with supported NVIDIA GPUs.",
  },
] as const satisfies readonly {
  number: string;
  mode: WorkloadMode;
  title: string;
  body: string;
  machineContext: string;
}[];
