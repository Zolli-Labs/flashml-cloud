// Phase 3 migration target: flashml serve. Until then this client talks to
// the retained legacy FastAPI coordinator.
// Start it with: cd legacy/coordinator && ../../.venv/bin/python server.py

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

export const JOB_ID_STORAGE_KEY = "flashml_job_id";

export type ArchitectureMode = "map_reduce" | "gradient_sync" | "parallel_search";
export type NodeType =
  | "cpu_small"
  | "cpu_large"
  | "gpu_any"
  | "gpu_ada_24"
  | "gpu_ada_32_pro"
  | "gpu_ada_80_pro"
  | "gpu_ampere_24";

export interface TrainRequest {
  model_id: string;
  architecture: ArchitectureMode;
  algorithm: string;
  // K-Means
  dataset: string;
  k: number;
  workers: number;
  max_iter: number;
  node_type: NodeType;
  // Gradient Sync / classification
  n_points: number;
  learning_rate: number;
  max_epochs: number;
  // Random Forest
  n_trees: number;
}

export interface UploadedDataset {
  dataset: string;
  filename: string;
  rows: number;
  columns: string[];
}

export interface IterationLog {
  iter: number;
  movement: number;
  inertia?: number;
  elapsed_iter: number;
  elapsed_total: number;
  cluster_sizes: Record<string, number>;
}

export type TrainingPhase =
  | "upload"
  | "partition"
  | "map"
  | "shuffle"
  | "reduce"
  | "aggregate"
  | "broadcast"
  | "complete";

export interface TrainingIteration {
  iteration: number;
  phase: TrainingPhase;
  movement: number;
  inertia: number;
  workers: {
    id: string;
    shardId: string;
    status: "idle" | "mapping" | "done" | "waiting";
    sampleCount: number;
    localInertia?: number;
    clusterCounts: Record<string, number>;
  }[];
  points2d: {
    id: string;
    x: number;
    y: number;
    cluster: number;
    workerId: string;
  }[];
  centroids2d: {
    id: string;
    x: number;
    y: number;
    cluster: number;
  }[];
}

export interface ClusterInfo {
  id: number;
  count: number;
  pct: number;
  top_terms: string[];
  sample_categories: string[];
  centroid: number[];
}

export type JobStatus =
  | "queued"
  | "vectorizing"
  | "starting_flash"
  | "running"
  | "done"
  | "error";

// --- Gradient Sync types ---

export interface GradientSyncWorkerSnap {
  id: string;
  shardId: string;
  status: "idle" | "computing" | "done";
  sampleCount: number;
  localLoss: number | null;
  gradientNorm: number | null;
}

export interface GradientSyncSnapshot {
  epoch: number;
  phase: string;
  loss: number;
  val_mse: number;
  gradient_norm: number;
  sync_time_ms: number;
  weights: number[];
  workers: GradientSyncWorkerSnap[];
}

export interface EpochLog {
  epoch: number;
  loss: number;
  val_mse: number;
  gradient_norm: number;
  sync_time_ms: number;
  elapsed_total: number;
}

// --- Parallel Search types ---

export interface ParallelSearchConfig {
  degree: number;
  alpha: number;
}

export interface WorkerTaskResult {
  task_id: string;
  worker_id: string;
  config: ParallelSearchConfig;
  score: number;
  runtime_ms: number;
  status: "pending" | "running" | "done";
}

export interface LeaderboardEntry extends WorkerTaskResult {
  rank: number;
}

export interface WorkerTaskGroup {
  worker_id: string;
  tasks: string[];
  status: "pending" | "running" | "done";
}

// --- Combined job response ---

export interface JobStatusResponse {
  job_id: string;
  architecture: ArchitectureMode;
  algorithm: string;
  status: JobStatus;
  current_phase?: string;
  iteration: number;
  max_iter: number;
  k: number;
  workers_count: number;
  node_type: NodeType;
  node_label: string;
  node_detail: string;
  compute_type: "cpu" | "gpu";
  dataset: string;
  dataset_filename?: string | null;
  movement: number | null;
  embedding_backend: string | null;
  embedding_model: string | null;
  embedding_raw_dim: number | null;
  embedding_cluster_dim: number | null;
  embedding_fallback: boolean;
  embedding_error: string | null;
  embedding_cache_hit?: boolean;
  elapsed: number;
  n_points: number;
  shard_sizes: number[];
  cluster_info: ClusterInfo[] | null;
  iterations_log: IterationLog[];
  training_iterations?: TrainingIteration[];
  converged_at: number | null;
  error: string | null;
  // Gradient Sync
  epoch?: number;
  max_epochs?: number;
  training_loss?: number | null;
  validation_mse?: number | null;
  weights?: number[] | null;
  gradient_norm?: number | null;
  epochs_log?: EpochLog[];
  training_snapshots?: GradientSyncSnapshot[];
  // Parallel Search / Random Forest
  total_configs?: number;
  completed_configs?: number;
  worker_tasks?: WorkerTaskGroup[];
  worker_results?: WorkerTaskResult[];
  leaderboard?: LeaderboardEntry[];
  best_config?: ParallelSearchConfig | null;
  best_score?: number | null;
  // Classification metrics (Logistic Regression, Naive Bayes, Random Forest)
  val_accuracy?: number | null;
  train_accuracy?: number | null;
  // Random Forest
  n_trees_total?: number;
  n_trees_done?: number;
}

export interface ResultsResponse extends JobStatusResponse {
  X_2d: number[][];
  assignments: number[];
  categories: string[];
  ticket_ids: string[];
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return res.json();
}

export function startTraining(req: TrainRequest): Promise<{ job_id: string }> {
  return request("/api/train", { method: "POST", body: JSON.stringify(req) });
}

export async function uploadDataset(file: File): Promise<UploadedDataset> {
  const res = await fetch(`${API_BASE}/api/datasets/upload`, {
    method: "POST",
    headers: {
      "Content-Type": "text/csv",
      "X-Filename": encodeURIComponent(file.name),
    },
    body: file,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return res.json();
}

export function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  return request(`/api/jobs/${jobId}`);
}

export function getResults(jobId: string): Promise<ResultsResponse> {
  return request(`/api/results/${jobId}`);
}
