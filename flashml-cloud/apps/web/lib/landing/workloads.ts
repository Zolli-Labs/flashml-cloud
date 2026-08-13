export const WORKLOADS = [
  {
    number: "01",
    title: "Model configuration search",
    body: "Run many model settings independently.",
    machineContext: "Laptops, CPU workstations, or rented GPUs.",
    layout: "lg:col-span-7 lg:pr-16",
  },
  {
    number: "02",
    title: "AI model evaluation",
    body: "Test prompts, datasets, or model versions as separate tasks.",
    machineContext: "CPU or GPU machines across a team.",
    layout: "lg:col-span-4 lg:col-start-9 lg:mt-16",
  },
  {
    number: "03",
    title: "Independent file processing",
    body: "Embeddings, OCR, transcription, conversion, or data preparation.",
    machineContext: "Supported macOS, Linux, and compatible cloud machines.",
    layout: "lg:col-span-5 lg:mt-12 lg:pr-8",
  },
  {
    number: "04",
    title: "Simulations and research trials",
    body: "Monte Carlo experiments and independent rollouts.",
    machineContext: "Mixed personal, lab, and cloud machines.",
    layout: "lg:col-span-4 lg:col-start-9 lg:mt-16",
  },
  {
    number: "05",
    title: "Checkpointable model training",
    body: "Save progress and continue after an interruption.",
    machineContext: "Linux machines with supported NVIDIA GPUs.",
    layout: "lg:col-span-6 lg:col-start-7 lg:mt-24 lg:pl-16",
  },
] as const;
