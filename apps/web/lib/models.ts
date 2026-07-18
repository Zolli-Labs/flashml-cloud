// FlashML Model Registry — single source of truth for all supported models.
// Every model describes its distributed execution pattern, and the rest of the
// app (launch page, dashboard, terminal) derives its behaviour from this file.

import type { ArchitectureMode } from "@/lib/api";

export type ExecutionPattern =
  | "MAP_REDUCE"
  | "GRADIENT_SYNC"
  | "EMBARRASSINGLY_PARALLEL"
  | "PIPELINE"
  | "MESSAGE_PASSING";

export type ModelCategory =
  | "Clustering"
  | "Classification"
  | "Regression"
  | "Deep Learning"
  | "Recommendation"
  | "Graph";

// "implemented" → real training runs on Runpod Flash workers
// "visualization" → animated topology only; launch is disabled
export type ModelStatus = "implemented" | "visualization";

export interface ModelParams {
  workers: number;
  k?: number;
  max_iter?: number;
  max_epochs?: number;
  n_points?: number;
  learning_rate?: number;
  n_trees?: number;
}

export interface MLModel {
  id: string;
  name: string;
  category: ModelCategory;
  executionPattern: ExecutionPattern;
  distributedStrategy: string;
  description: string;
  // Shown in the launch page config panel to explain FlashML's architecture choice
  whyDistributed: string;
  status: ModelStatus;
  // Backend routing: what /api/train receives
  algorithm: string;
  supportsDatasetUpload: boolean;
  defaultParams: ModelParams;
}

// Execution pattern → backend architecture field.
// Returns null for patterns that only have placeholder visualizations.
export function patternToArch(pattern: ExecutionPattern): ArchitectureMode | null {
  switch (pattern) {
    case "MAP_REDUCE": return "map_reduce";
    case "GRADIENT_SYNC": return "gradient_sync";
    case "EMBARRASSINGLY_PARALLEL": return "parallel_search";
    default: return null;
  }
}

export const PATTERN_LABELS: Record<ExecutionPattern, string> = {
  MAP_REDUCE: "MapReduce",
  GRADIENT_SYNC: "Gradient Sync",
  EMBARRASSINGLY_PARALLEL: "Embarrassingly Parallel",
  PIPELINE: "Pipeline Parallel",
  MESSAGE_PASSING: "Message Passing",
};

export const PATTERN_SHORT: Record<ExecutionPattern, string> = {
  MAP_REDUCE: "MAP_REDUCE",
  GRADIENT_SYNC: "GRADIENT_SYNC",
  EMBARRASSINGLY_PARALLEL: "E_PARALLEL",
  PIPELINE: "PIPELINE",
  MESSAGE_PASSING: "MSG_PASSING",
};

export const PATTERN_COLORS: Record<
  ExecutionPattern,
  { text: string; border: string; bg: string }
> = {
  MAP_REDUCE:             { text: "text-cyan",        border: "border-cyan/40",         bg: "bg-cyan/10"         },
  GRADIENT_SYNC:          { text: "text-violet-400",  border: "border-violet-400/40",   bg: "bg-violet-400/10"   },
  EMBARRASSINGLY_PARALLEL:{ text: "text-node-green",  border: "border-node-green/40",   bg: "bg-node-green/10"   },
  PIPELINE:               { text: "text-amber-400",   border: "border-amber-400/40",    bg: "bg-amber-400/10"    },
  MESSAGE_PASSING:        { text: "text-orange-400",  border: "border-orange-400/40",   bg: "bg-orange-400/10"   },
};

export const TOPOLOGY_FLOWS: Record<ExecutionPattern, string[]> = {
  MAP_REDUCE:             ["Dataset", "Shards", "Workers", "Reducer", "Result"],
  GRADIENT_SYNC:          ["Weights", "Workers", "Param Server", "Avg Gradient", "Updated Weights"],
  EMBARRASSINGLY_PARALLEL:["Config Space", "Job Queue", "Workers (independent)", "Aggregator", "Result"],
  PIPELINE:               ["Stage 1", "Stage 2", "Stage 3", "Stage 4", "Output"],
  MESSAGE_PASSING:        ["Workers", "Border Messages", "Global State", "Convergence", "Result"],
};

export const CATEGORIES: ModelCategory[] = [
  "Clustering",
  "Classification",
  "Regression",
  "Deep Learning",
  "Recommendation",
  "Graph",
];

export const MODEL_REGISTRY: MLModel[] = [
  // ─── Clustering ───────────────────────────────────────────────────────────
  {
    id: "kmeans",
    name: "K-Means",
    category: "Clustering",
    executionPattern: "MAP_REDUCE",
    distributedStrategy:
      "Workers compute partial centroid sums. Reducer aggregates global centroids each iteration.",
    description:
      "Partition n data points into k clusters by iteratively refining centroids.",
    whyDistributed:
      "K-Means uses MapReduce because each worker computes partial centroid statistics for its shard, and a central reducer merges them into global centroids — a natural map/reduce decomposition that scales linearly with data size.",
    status: "implemented",
    algorithm: "kmeans",
    supportsDatasetUpload: true,
    defaultParams: { workers: 3, k: 3, max_iter: 15 },
  },
  {
    id: "mini_batch_kmeans",
    name: "Mini-Batch K-Means",
    category: "Clustering",
    executionPattern: "MAP_REDUCE",
    distributedStrategy:
      "Same MapReduce architecture as K-Means but operating on random mini-batches each iteration.",
    description:
      "Faster K-Means variant using mini-batches to reduce per-iteration compute cost.",
    whyDistributed:
      "Mini-Batch K-Means uses MapReduce for the same reason as K-Means — workers compute partial statistics — but operates on small random batches for much faster convergence on large datasets.",
    status: "visualization",
    algorithm: "mini_batch_kmeans",
    supportsDatasetUpload: false,
    defaultParams: { workers: 3 },
  },
  {
    id: "dbscan",
    name: "DBSCAN",
    category: "Clustering",
    executionPattern: "MESSAGE_PASSING",
    distributedStrategy:
      "Border points require neighbor queries across worker boundaries — workers exchange messages to resolve cross-shard clusters.",
    description:
      "Density-based clustering that finds arbitrarily-shaped clusters without a predefined k.",
    whyDistributed:
      "DBSCAN uses Message Passing because border points span worker boundaries. Workers must communicate to decide whether a point belongs to a cluster that started on a different worker.",
    status: "visualization",
    algorithm: "dbscan",
    supportsDatasetUpload: false,
    defaultParams: { workers: 3 },
  },

  // ─── Classification ────────────────────────────────────────────────────────
  {
    id: "logistic_regression",
    name: "Logistic Regression",
    category: "Classification",
    executionPattern: "GRADIENT_SYNC",
    distributedStrategy:
      "Workers compute local log-loss gradients on their shard. Parameter server averages and applies weight updates each epoch.",
    description:
      "Binary classification via gradient descent on log loss with sigmoid output.",
    whyDistributed:
      "Logistic Regression uses Gradient Synchronization because each worker computes gradients on its data shard and those gradients must be averaged at a parameter server after every epoch — the textbook distributed gradient descent pattern.",
    status: "implemented",
    algorithm: "logistic_regression",
    supportsDatasetUpload: false,
    defaultParams: { workers: 3, max_epochs: 30, n_points: 1000 },
  },
  {
    id: "naive_bayes",
    name: "Naive Bayes",
    category: "Classification",
    executionPattern: "MAP_REDUCE",
    distributedStrategy:
      "Workers compute class frequency tables on their shard. Reducer merges all statistics in a single pass.",
    description:
      "Probabilistic classifier based on Bayes' theorem with conditional independence assumption.",
    whyDistributed:
      "Naive Bayes uses MapReduce because computing class probabilities and feature statistics is fully decomposable — each worker handles its shard and the reducer merges count tables in a single pass, with no iteration required.",
    status: "implemented",
    algorithm: "naive_bayes",
    supportsDatasetUpload: false,
    defaultParams: { workers: 3, n_points: 1000 },
  },
  {
    id: "random_forest",
    name: "Random Forest",
    category: "Classification",
    executionPattern: "EMBARRASSINGLY_PARALLEL",
    distributedStrategy:
      "Each worker trains independent decision trees on bootstrapped samples. No inter-worker communication needed.",
    description:
      "Ensemble of decision trees trained on bootstrapped samples, aggregated by majority vote.",
    whyDistributed:
      "Random Forest is embarrassingly parallel because each tree is completely independent — workers train their subset of trees with zero coordination, and an aggregator combines them into the final forest.",
    status: "implemented",
    algorithm: "random_forest",
    supportsDatasetUpload: false,
    defaultParams: { workers: 3, n_points: 1000, n_trees: 30 },
  },
  {
    id: "xgboost",
    name: "XGBoost",
    category: "Classification",
    executionPattern: "GRADIENT_SYNC",
    distributedStrategy:
      "Workers synchronize histogram statistics for split finding. Trees are built sequentially with global histogram merges.",
    description:
      "Extreme gradient boosting with regularization — state of the art on tabular data.",
    whyDistributed:
      "XGBoost uses Gradient Synchronization because workers must share histogram statistics when finding optimal splits — unlike Random Forest, trees in XGBoost are built sequentially with inter-stage communication after each tree.",
    status: "visualization",
    algorithm: "xgboost",
    supportsDatasetUpload: false,
    defaultParams: { workers: 3 },
  },

  // ─── Regression ───────────────────────────────────────────────────────────
  {
    id: "linear_regression",
    name: "Linear Regression",
    category: "Regression",
    executionPattern: "GRADIENT_SYNC",
    distributedStrategy:
      "Distributed gradient descent. Workers compute local MSE gradients, parameter server updates weights.",
    description:
      "Fit a linear model to minimize mean squared error via distributed gradient descent.",
    whyDistributed:
      "Linear Regression uses Gradient Synchronization because the gradient with respect to weights can be computed locally on each shard, then averaged at a parameter server — the canonical distributed gradient descent pattern.",
    status: "implemented",
    algorithm: "linear_regression",
    supportsDatasetUpload: false,
    defaultParams: { workers: 3, max_epochs: 20, n_points: 1000 },
  },
  {
    id: "hyperparameter_search",
    name: "Hyperparameter Search",
    category: "Regression",
    executionPattern: "EMBARRASSINGLY_PARALLEL",
    distributedStrategy:
      "Each worker tests independent hyperparameter configurations. No inter-worker communication.",
    description:
      "Search Ridge + PolynomialFeatures config space across 9 configurations in parallel.",
    whyDistributed:
      "Hyperparameter search is embarrassingly parallel — each configuration is completely independent. Workers test different combinations simultaneously with zero communication overhead.",
    status: "implemented",
    algorithm: "hyperparameter_search",
    supportsDatasetUpload: false,
    defaultParams: { workers: 3 },
  },

  // ─── Deep Learning ─────────────────────────────────────────────────────────
  {
    id: "neural_network",
    name: "Neural Network",
    category: "Deep Learning",
    executionPattern: "GRADIENT_SYNC",
    distributedStrategy:
      "Distributed SGD. Workers compute per-layer gradients, parameter server averages and broadcasts.",
    description:
      "Feedforward neural network trained with stochastic gradient descent.",
    whyDistributed:
      "Neural Networks use Gradient Synchronization — the same pattern as linear regression, scaled to millions of parameters. Workers compute layer gradients in parallel; the parameter server averages them using AllReduce.",
    status: "visualization",
    algorithm: "neural_network",
    supportsDatasetUpload: false,
    defaultParams: { workers: 3 },
  },
  {
    id: "cnn",
    name: "CNN",
    category: "Deep Learning",
    executionPattern: "GRADIENT_SYNC",
    distributedStrategy:
      "Same as Neural Network but with convolutional layers requiring larger gradient tensors per layer.",
    description:
      "Convolutional Neural Network for image and spatial data.",
    whyDistributed:
      "CNNs use Gradient Synchronization with large per-layer gradient tensors. Communication is the bottleneck — high-bandwidth interconnects like NVLink and InfiniBand matter significantly at scale.",
    status: "visualization",
    algorithm: "cnn",
    supportsDatasetUpload: false,
    defaultParams: { workers: 4 },
  },
  {
    id: "transformer",
    name: "Transformer",
    category: "Deep Learning",
    executionPattern: "PIPELINE",
    distributedStrategy:
      "Different transformer layers execute across different workers in a forward pipeline.",
    description:
      "Attention-based model — the architecture behind BERT, GPT, and all modern LLMs.",
    whyDistributed:
      "Transformers use Pipeline Parallelism because model weights don't fit on a single GPU. Different layers are placed on different workers — outputs flow through the pipeline in microbatches.",
    status: "visualization",
    algorithm: "transformer",
    supportsDatasetUpload: false,
    defaultParams: { workers: 4 },
  },
  {
    id: "llm_finetune",
    name: "LLM Fine-Tuning",
    category: "Deep Learning",
    executionPattern: "GRADIENT_SYNC",
    distributedStrategy:
      "LoRA or full fine-tuning with gradient synchronization across GPU workers.",
    description:
      "Fine-tune a pre-trained language model on domain-specific data.",
    whyDistributed:
      "LLM Fine-Tuning uses Gradient Synchronization. Each worker processes a microbatch and gradients are averaged — LoRA reduces gradient size significantly by updating only low-rank adapters.",
    status: "visualization",
    algorithm: "llm_finetune",
    supportsDatasetUpload: false,
    defaultParams: { workers: 4 },
  },

  // ─── Recommendation ────────────────────────────────────────────────────────
  {
    id: "matrix_factorization",
    name: "Matrix Factorization",
    category: "Recommendation",
    executionPattern: "GRADIENT_SYNC",
    distributedStrategy:
      "Workers handle partitions of the user-item matrix, synchronizing latent factor updates.",
    description:
      "Decompose the user-item interaction matrix into latent factors for collaborative filtering.",
    whyDistributed:
      "Matrix Factorization uses Gradient Synchronization because workers each own a partition of the interaction matrix and must exchange latent factor updates — the same user may appear in ratings handled by different workers.",
    status: "visualization",
    algorithm: "matrix_factorization",
    supportsDatasetUpload: false,
    defaultParams: { workers: 3 },
  },

  // ─── Graph ─────────────────────────────────────────────────────────────────
  {
    id: "pagerank",
    name: "PageRank",
    category: "Graph",
    executionPattern: "MAP_REDUCE",
    distributedStrategy:
      "Each iteration maps outgoing rank contributions across edges, then reduces by summing all incoming contributions.",
    description:
      "Iterative algorithm to rank nodes in a graph by the number and quality of links.",
    whyDistributed:
      "PageRank uses MapReduce because each iteration maps outgoing rank contributions across edges, then reduces by summing all incoming contributions per node — a perfect fit for the MapReduce pattern.",
    status: "visualization",
    algorithm: "pagerank",
    supportsDatasetUpload: false,
    defaultParams: { workers: 3 },
  },
  {
    id: "connected_components",
    name: "Connected Components",
    category: "Graph",
    executionPattern: "MESSAGE_PASSING",
    distributedStrategy:
      "Workers propagate component labels through edges until convergence across all workers.",
    description:
      "Find groups of nodes where every pair is connected — the fundamental graph decomposition.",
    whyDistributed:
      "Connected Components requires Message Passing because component labels must propagate across worker boundaries — a node's label can only be determined after all its neighbors across all workers have been processed.",
    status: "visualization",
    algorithm: "connected_components",
    supportsDatasetUpload: false,
    defaultParams: { workers: 3 },
  },
];

export function getModelById(id: string): MLModel | undefined {
  return MODEL_REGISTRY.find((m) => m.id === id);
}

export function getModelByAlgorithm(algorithm: string): MLModel | undefined {
  return MODEL_REGISTRY.find((m) => m.algorithm === algorithm);
}

export function getModelsByCategory(category: ModelCategory): MLModel[] {
  return MODEL_REGISTRY.filter((m) => m.category === category);
}
