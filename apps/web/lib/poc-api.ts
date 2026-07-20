// Client for the FlashML Cloud v1alpha1 API (apps/api). Shapes mirror
// flashruntime.protocol.v1alpha1 — the cloud UI stays backend-neutral and
// never sees Ray/KubeRay specifics beyond display strings.

export const CLOUD_API =
  process.env.NEXT_PUBLIC_CLOUD_API ?? "http://localhost:8000";

export interface NodeCapabilities {
  cpu_cores: number | null;
  memory_bytes: number | null;
  gpus: Record<string, unknown>[];
  os: string;
  architecture: string;
}

export interface NodeStatusView {
  registration: {
    node_id: string;
    kubernetes_node: string;
    hostname: string;
    capabilities: NodeCapabilities;
    environment: "local" | "cloud" | "edge";
    sandbox_capable: boolean;
    pool: string;
    runtime_profile: string;
    labels: Record<string, string>;
    agent_version: string;
  };
  online: boolean;
  last_heartbeat: string | null;
  accepted_task_count: number;
}

export interface ArtifactRecord {
  uri: string;
  backend: "minio" | "oss" | "local";
  bucket: string;
  object_key: string;
  etag: string | null;
  sha256: string | null;
  size_bytes: number | null;
  created_at: string;
}

export type JobState =
  | "PENDING" | "SUBMITTED" | "RUNNING" | "RECOVERING"
  | "SUCCEEDED" | "FAILED" | "CANCELLED";

export interface JobRecord {
  job_id: string;
  spec: {
    metadata: { name: string };
    spec: {
      image: { repository: string; tag: string };
      workload: { type: string; parameters: Record<string, unknown> };
      resources: { minimumWorkers: number; maximumWorkers: number };
      isolation: { tier: string; allowFallback: boolean };
    };
  };
  state: JobState;
  backend: string;
  deployment_profile: string;
  runtime_execution_id: string | null;
  created_at: string;
  finished_at: string | null;
  error: string | null;
  artifacts: ArtifactRecord[];
}

export interface FlashEvent {
  job_id: string;
  type: string;
  timestamp: string;
  source: string;
  message: string;
  data: Record<string, unknown>;
}

export interface IntegrationStatus {
  profile: string;
  backend: string;
  environment: string;
  artifact_store: string;
  image_registry: string;
  ack_connected: boolean;
  oss_bucket: string;
  sls_enabled: boolean;
  prometheus_enabled: boolean;
  sandbox_pool_available: boolean;
  paidlc_adapter: string;
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${CLOUD_API}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

export const fetchNodes = () => get<NodeStatusView[]>("/v1alpha1/nodes");
export const fetchJobs = () => get<JobRecord[]>("/v1alpha1/jobs");
export const fetchJob = (id: string) => get<JobRecord>(`/v1alpha1/jobs/${id}`);
export const fetchEvents = (id: string) =>
  get<FlashEvent[]>(`/v1alpha1/jobs/${id}/events`);
export const fetchIntegration = () =>
  get<IntegrationStatus>("/v1alpha1/integration");

export async function submitJob(spec: unknown): Promise<JobRecord> {
  const r = await fetch(`${CLOUD_API}/v1alpha1/jobs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(spec),
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}));
    throw new Error(detail.detail ?? `submit failed: ${r.status}`);
  }
  return r.json();
}

export function formatBytes(n: number | null): string {
  if (n == null) return "—";
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GiB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MiB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KiB`;
  return `${n} B`;
}
