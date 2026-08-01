// Typed client for the FlashML cloud API — the real contract lives at
// `apps/api/flashml_cloud_api/app.py`, not `lib/api.ts` (stale, targets a
// retired coordinator) or `lib/poc-api.ts` (the pre-auth prototype's
// unscoped client). This file is the single source of the API's response
// types; pages import from here and define no shapes of their own.
//
// Every call attaches the current Supabase session's JWT as
// `Authorization: Bearer <jwt>`. Two error kinds get their own type because
// the UI must handle them differently, not just display them:
//
// - `NotAuthenticated` — no session, or the API answered 401. The API's
//   `current_user` dependency returns 401 for "sign-in required" and never
//   distinguishes "expired" from "bad signature", so neither does this
//   client. The UI must turn this into a redirect to /sign-in — never a
//   silently empty list, which would render as "you have no machines" when
//   it actually means "you are signed out".
// - `NotFound` — the API answered 404. For job routes this deliberately
//   means "does not exist" *or* "exists and is not yours" — the API
//   returns 404 rather than 403 so a guesser cannot learn which id is real
//   — so this must not be "helpfully" turned into an access-denied message
//   that would leak the distinction back.
//
// This module never reads `process.env` for anything but
// `NEXT_PUBLIC_CLOUD_API`, and never sends any credential but the signed-in
// user's own Supabase JWT — never a service-role key, never an operator
// token.

import { createBrowserSupabaseClient } from "./supabase";

/** Prepends `https://` when `url` has no scheme. Render's Blueprint
 * (`render.yaml`) resolves `NEXT_PUBLIC_CLOUD_API` via
 * `fromService: {type: web, property: host}`, which returns a bare
 * hostname like `flashml-api.onrender.com` — never a scheme. Handed
 * straight to `fetch()`, a scheme-less string is a *relative* path, so
 * every call would resolve against this site's own origin
 * (`flashml-web.onrender.com/flashml-api.onrender.com/...`) instead of the
 * API — the site still loads, since this file is never touched by a health
 * check, and every request then 404s. https, not http: this is a public,
 * browser-facing origin, unlike an internal Render private-service
 * hostport. An already-scheme'd value (including `http://localhost:...`
 * for local dev) and an empty value pass through unchanged. */
function withDefaultScheme(url: string): string {
  if (!url || url.includes("://")) {
    return url;
  }
  return `https://${url}`;
}

/** The cloud API's base URL. A function, not a module-level constant, so
 * it is read fresh on every call rather than captured once at import time
 * (and so tests can override `NEXT_PUBLIC_CLOUD_API` per-case). */
export function cloudApiBase(): string {
  return withDefaultScheme(process.env.NEXT_PUBLIC_CLOUD_API ?? "http://localhost:8000");
}

export class NotAuthenticated extends Error {
  constructor(message = "sign-in required") {
    super(message);
    this.name = "NotAuthenticated";
  }
}

export class NotFound extends Error {
  constructor(message = "not found") {
    super(message);
    this.name = "NotFound";
  }
}

/** Any other non-2xx response. `status` and `detail` carry whatever the
 * API returned so the UI can show it verbatim rather than a paraphrase. */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

/** A 400 from `submitFromRepo` carrying preflight findings — every finding
 * at once, per the API's contract, so the UI can render all of them rather
 * than one round trip per fix. */
export class PreflightRejected extends Error {
  readonly findings: PreflightFinding[];
  constructor(message: string, findings: PreflightFinding[]) {
    super(message);
    this.name = "PreflightRejected";
    this.findings = findings;
  }
}

// ---------------------------------------------------------------------------
// types — mirror the API's response shapes
// ---------------------------------------------------------------------------

/** `GET /v1alpha1/me` — public.profiles, upserted on first sign-in. */
export interface Profile {
  id: string;
  display_name: string | null;
  github_login: string | null;
  is_host: boolean;
  is_developer: boolean;
  created_at: string;
}

/** A row of `public.machines`, restricted to `MACHINE_PUBLIC_COLUMNS` —
 * never includes `token_hash`. */
export interface Machine {
  id: string;
  node_id: string;
  name: string | null;
  platform: string | null;
  capabilities: Record<string, unknown> | null;
  status: "pending" | "active" | "revoked" | string;
  token_prefix: string | null;
  last_seen_at: string | null;
  created_at: string;
  revoked_at: string | null;
}

export interface ApproveDeviceCodeResult {
  machine_id: string;
  status: string;
}

export interface RevokeMachineResult {
  machine_id: string;
  status: string;
}

export type JobState =
  | "PENDING"
  | "SUBMITTED"
  | "RUNNING"
  | "RECOVERING"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED";

export interface JobSpec {
  metadata: { name: string };
  spec: {
    image: { repository: string; tag: string };
    workload: { type: string; parameters: Record<string, unknown> };
    resources: { minimumWorkers: number; maximumWorkers: number };
    isolation: { tier: string; allowFallback: boolean };
  };
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

/** The coordinator's job record, passed through by the API once ownership
 * is established. Field set matches `flashruntime.protocol.v1alpha1`. */
export interface JobRecord {
  job_id: string;
  spec: JobSpec;
  state: JobState;
  backend: string;
  deployment_profile: string;
  runtime_execution_id: string | null;
  created_at: string;
  finished_at: string | null;
  error: string | null;
  artifacts: ArtifactRecord[];
}

export interface PreflightFinding {
  level: "error" | "warning";
  code: string;
  message: string;
}

/** A row of `public.job_rounds`, restricted to `JOB_ROUND_PUBLIC_COLUMNS` —
 * one completed federated-averaging round. Only federated jobs have any of
 * these; an independent job's list is always empty, never an error.
 * `mean_loss` is nullable on the API side and must stay nullable here: a
 * round can complete with no loss reported, and rendering that as `0` or a
 * smoothed guess would fabricate a number a training dashboard would then
 * be trusted on. `contributors` is the list of contributing machines' node
 * ids for that round. */
export interface JobRound {
  round: number;
  participants: number;
  mean_loss: number | null;
  contributors: string[];
  coordinator_job_id: string | null;
  recorded_at: string;
}

export interface SubmitFromRepoResult extends JobRecord {
  findings: PreflightFinding[];
}

// ---------------------------------------------------------------------------
// request plumbing
// ---------------------------------------------------------------------------

async function authHeader(): Promise<Record<string, string>> {
  const supabase = createBrowserSupabaseClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session) {
    // No local session at all — skip the round trip, since the API would
    // answer 401 anyway.
    throw new NotAuthenticated();
  }
  return { Authorization: `Bearer ${session.access_token}` };
}

interface ParsedErrorBody {
  detail: string | null;
  findings: PreflightFinding[] | null;
}

async function parseErrorBody(res: Response): Promise<ParsedErrorBody> {
  const text = await res.text().catch(() => "");
  if (!text) return { detail: null, findings: null };
  try {
    const body = JSON.parse(text);
    if (body && typeof body === "object") {
      const detail = typeof body.detail === "string" ? body.detail : null;
      const findings = Array.isArray(body.findings) ? body.findings : null;
      return { detail: detail ?? (findings ? null : text), findings };
    }
  } catch {
    // Not JSON — the coordinator or a proxy can return plain text.
  }
  return { detail: text, findings: null };
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const auth = await authHeader();
  const headers: Record<string, string> = {
    ...auth,
    ...(init.body ? { "Content-Type": "application/json" } : {}),
    ...(init.headers as Record<string, string> | undefined),
  };

  const res = await fetch(`${cloudApiBase()}${path}`, { ...init, headers });

  if (res.status === 401) {
    throw new NotAuthenticated();
  }
  if (res.status === 404) {
    const { detail } = await parseErrorBody(res);
    throw new NotFound(detail ?? "not found");
  }
  if (!res.ok) {
    const { detail, findings } = await parseErrorBody(res);
    if (findings) {
      throw new PreflightRejected(
        detail ?? "preflight found problems with this job",
        findings
      );
    }
    throw new ApiError(res.status, detail ?? `${res.status} ${res.statusText}`);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

// ---------------------------------------------------------------------------
// calls
// ---------------------------------------------------------------------------

export function getMe(): Promise<Profile> {
  return request<Profile>("/v1alpha1/me");
}

export function listMachines(): Promise<Machine[]> {
  return request<Machine[]>("/v1alpha1/machines");
}

export function approveDeviceCode(
  userCode: string
): Promise<ApproveDeviceCodeResult> {
  return request<ApproveDeviceCodeResult>("/v1alpha1/device/approve", {
    method: "POST",
    body: JSON.stringify({ user_code: userCode }),
  });
}

export function revokeMachine(machineId: string): Promise<RevokeMachineResult> {
  return request<RevokeMachineResult>(
    `/v1alpha1/machines/${encodeURIComponent(machineId)}/revoke`,
    { method: "POST" }
  );
}

export function listJobs(): Promise<JobRecord[]> {
  return request<JobRecord[]>("/v1alpha1/jobs");
}

export function getJob(jobId: string): Promise<JobRecord> {
  return request<JobRecord>(`/v1alpha1/jobs/${encodeURIComponent(jobId)}`);
}

export function cancelJob(jobId: string): Promise<JobRecord> {
  return request<JobRecord>(
    `/v1alpha1/jobs/${encodeURIComponent(jobId)}/cancel`,
    { method: "POST" }
  );
}

/** `GET /v1alpha1/jobs/{id}/rounds` — owner-scoped exactly like `getJob`:
 * a job that isn't yours 404s here too, never a 403 that would confirm the
 * id exists. Oldest round first, matching the API's `order by r.round`. */
export function listJobRounds(jobId: string): Promise<JobRound[]> {
  return request<JobRound[]>(
    `/v1alpha1/jobs/${encodeURIComponent(jobId)}/rounds`
  );
}

/** The relative key a result artifact's `uri` maps to under
 * `/v1alpha1/jobs/{jobId}/artifacts/{key}` — the only artifact route a
 * browser may call (`apps/api/flashml_cloud_api/compile.py` sets every
 * job's `outputPrefix` to `artifact://jobs/{job_id}/`). Returns `null` for
 * a `uri` that isn't under this job's output prefix — e.g. the staged
 * input-code upload from `/from-repo`, which is not a result and has no
 * browser-readable route. Never guessed: it is a straight strip of the
 * prefix the API itself defines. */
export function jobArtifactKey(jobId: string, uri: string): string | null {
  const prefix = `artifact://jobs/${jobId}/`;
  return uri.startsWith(prefix) ? uri.slice(prefix.length) : null;
}

/** Fetches a result artifact's bytes with the caller's JWT attached, for a
 * browser-triggered download — the route requires auth, so a plain `<a
 * href>` cannot reach it. */
export async function fetchJobArtifact(
  jobId: string,
  key: string
): Promise<Blob> {
  const auth = await authHeader();
  const res = await fetch(
    `${cloudApiBase()}/v1alpha1/jobs/${encodeURIComponent(jobId)}/artifacts/${key
      .split("/")
      .map(encodeURIComponent)
      .join("/")}`,
    { headers: auth }
  );
  if (res.status === 401) throw new NotAuthenticated();
  if (res.status === 404) throw new NotFound("artifact not found");
  if (!res.ok) {
    const { detail } = await parseErrorBody(res);
    throw new ApiError(res.status, detail ?? `${res.status} ${res.statusText}`);
  }
  return res.blob();
}

/** `POST /v1alpha1/jobs` — the plain path, no repo/preflight involved. */
export function submitJob(spec: unknown): Promise<JobRecord> {
  return request<JobRecord>("/v1alpha1/jobs", {
    method: "POST",
    body: JSON.stringify(spec),
  });
}

/** `POST /v1alpha1/jobs/from-repo` — throws `PreflightRejected` (never a
 * plain `ApiError`) when the API's preflight finds a blocking error. */
export function submitFromRepo(
  repo: string,
  ref?: string
): Promise<SubmitFromRepoResult> {
  return request<SubmitFromRepoResult>("/v1alpha1/jobs/from-repo", {
    method: "POST",
    body: JSON.stringify(ref ? { repo, ref } : { repo }),
  });
}
