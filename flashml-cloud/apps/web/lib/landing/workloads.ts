export const WORKLOADS = [
  {
    number: "01",
    title: "Federated training",
    body: "Coordinate independent machines while aggregating accepted model updates.",
    layout: "lg:col-span-7 lg:pr-16",
  },
  {
    number: "02",
    title: "Hyperparameter search",
    body: "Lease isolated trials across mixed compute and retain each accepted result.",
    layout: "lg:col-span-4 lg:col-start-9 lg:mt-16",
  },
  {
    number: "03",
    title: "Shared data processing",
    body: "Distribute map work, collect verified partials, and coordinate deterministic reduction.",
    layout: "lg:col-span-5 lg:mt-12 lg:pr-8",
  },
  {
    number: "04",
    title: "Checkpointable model training",
    body: "Resume long-running training from the latest verified checkpoint after interruption.",
    layout: "lg:col-span-6 lg:col-start-7 lg:mt-24 lg:pl-16",
  },
] as const;
