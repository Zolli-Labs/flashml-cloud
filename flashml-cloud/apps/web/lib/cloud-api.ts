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
  // `||`, not `??`. An EMPTY value is not the same as an unset one and `??`
  // does not catch it: `"" ?? fallback` is `""`, withDefaultScheme returns ""
  // in turn, and fetch() is then handed a RELATIVE url — every API call
  // silently goes to the web origin instead of the API. Render produces
  // exactly this when a `fromService` reference fails to resolve: nothing
  // fails at build time, and a signed-in user sees a bare "Failed to fetch".
  return withDefaultScheme(process.env.NEXT_PUBLIC_CLOUD_API || "http://localhost:8000");
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

/** The four-state access model. `admitted` alone could not express a
 * signed-in account that has not filled the onboarding form: it is
 * neither admitted nor refused. */
export type AccessState =
  | "needs_onboarding"
  | "pending"
  | "admitted"
  | "declined";

/** `GET /v1alpha1/me` — public.profiles, upserted on first sign-in. */
export interface Profile {
  id: string;
  display_name: string | null;
  github_login: string | null;
  is_host: boolean;
  is_developer: boolean;
  created_at: string;
  /** Whether this account has redeemed an invite (or predates the gate —
   * see the migration's grandfather clause). `GET /v1alpha1/me` stays
   * reachable for an un-admitted account on purpose (`admitted_user`'s own
   * docstring: "reads stay open") specifically so the console can read
   * this flag and show the invite gate instead of a silent 403 on every
   * other route.
   *
   * Kept alongside `access` rather than replaced: it predates this and
   * other readers rely on it. */
  admitted: boolean;
  access: AccessState;
  /** Whether this account can open the access-request queue. Read-only
   * everywhere — granted by one manual SQL UPDATE and by nothing else, and
   * `PATCH /v1alpha1/me` refuses it. The console reads it for one purpose:
   * whether to draw the Admin entry in the rail. Every admin route
   * re-checks it server-side, so a client that lies to itself here gains
   * nothing but a link to a 403. */
  is_admin: boolean;
  first_name: string | null;
  last_name: string | null;
  company_name: string | null;
  role: string | null;
  team_size: string | null;
}

/** One of `GET /v1alpha1/machines`' `pools` chips — the pools a machine is
 * opted into serving, not the pools its owner merely belongs to. */
export interface PoolChip {
  id: string;
  name: string;
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
  /** The display-only capability snapshot the API persists at register
   * time. Drives the trust badge — see `lib/machine-badge.ts` for what
   * each of the three flags means and the deliberate precedence when more
   * than one is true. `module_capable` has no badge of its own; it only
   * distinguishes "can run built-in modules" from "can run nothing" within
   * the modules-only tier. */
  sandbox_capable: boolean;
  argv_capable: boolean;
  unsandboxed_argv_capable: boolean;
  module_capable: boolean;
  /** The pools this machine is bound to, as one aggregate query per caller
   * rather than one per machine (`pools_for_machines_of_owner`, `db.py`) —
   * same reasoning `listJobRounds` documents elsewhere for a single-query
   * join over an N+1 loop. Empty, never absent, for an unbound machine. */
  pools: PoolChip[];
}

/** A row of `GET /v1alpha1/pools/{id}/machines` — every machine bound to a
 * pool, across all its members, which `listMachines()` cannot return
 * because it is scoped to the caller by design.
 *
 * Deliberately NOT derived from `Machine`. This route is read by every
 * MEMBER of a pool, so it returns strictly less than the owner's own view:
 * no `token_prefix`, `capabilities`, `platform`, `created_at` or
 * `revoked_at` — see `_POOL_MACHINE_COLUMNS` in `apps/api/.../db.py` for
 * why. Spelling the fields out here rather than `Omit`-ing them off
 * `Machine` keeps the two shapes independent: adding a column to the
 * owner's view must not silently widen this one, and reading a field the
 * API no longer sends should be a compile error.
 *
 * No `pools` field either: this response already answers "which pool", so a
 * per-machine chip list would be a longer way of saying the id in the URL. */
export interface PoolMachine {
  id: string;
  node_id: string;
  name: string | null;
  owner_id: string;
  owner_display_name: string | null;
  status: "pending" | "active" | "revoked" | string;
  last_seen_at: string | null;
  /** Same three flags `machineBadge` reads, and `module_capable` alongside
   * them — see `Machine` above for what each means. */
  sandbox_capable: boolean;
  argv_capable: boolean;
  unsandboxed_argv_capable: boolean;
  module_capable: boolean;
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
  /** Some tasks succeeded, some exhausted their attempts, and the job set
   *  `allow_partial`. Terminal, and deliberately not SUCCEEDED: a badge
   *  reading "succeeded" over a run that lost six of twenty-four shards
   *  misrepresents what came back. */
  | "PARTIAL"
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
/** A job as `/v1alpha1/jobs` returns it — and it returns TWO shapes.
 *
 * A coordinator job carries everything below. A FEDERATED job does not exist
 * on the coordinator (it is one coordinator job per round), so the API
 * synthesises it from public.jobs and sends only:
 *
 *   {job_id, name, state, mode: "federated"}
 *
 * Everything a federated job omits is therefore optional HERE, however
 * reliably a coordinator job supplies it. Declaring them required did not make
 * them arrive: it only moved the failure from a type error at build time to a
 * TypeError during render, which took the whole /jobs page down the first time
 * anyone ran a federated job. A type cannot constrain what a server sends. */
export interface JobRecord {
  job_id: string;
  state: JobState;
  /** Absent on federated jobs. */
  spec?: JobSpec;
  /** Present on federated jobs, which have no spec to carry a name. */
  name?: string;
  /** "federated" for a federated run; absent otherwise. */
  mode?: string;
  /** The pool this job belongs to, or null for every job submitted before
   * pools shipped. OPTIONAL, not merely nullable: the web and API deploy
   * separately, so a browser running this code will briefly talk to an API
   * that has never heard of the field. `lib/job-scope.ts` treats absent and
   * null identically for exactly that reason. */
  pool_id?: string | null;
  /** Display name of whoever submitted this, for the attribution that makes
   * a shared workspace read as shared. Null when they never set one. */
  submitted_by?: string | null;
  backend?: string;
  deployment_profile?: string;
  runtime_execution_id?: string | null;
  created_at?: string;
  finished_at?: string | null;
  error?: string | null;
  artifacts?: ArtifactRecord[];
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

/** `GET /v1alpha1/jobs/{id}/result` — the job-level answer.
 *
 * `accepted`/`total` travel WITH the result rather than being derivable
 * from it: the coordinator answers while a job is still running, and a
 * partial answer that does not say it is partial misrepresents how much
 * work went into it. `result` is null when the job declared no reducer,
 * which is a real answer (the task artifacts are the deliverable), not an
 * absence. */
export interface JobResult {
  job_id: string;
  reducer: string;
  accepted: number;
  total: number;
  complete: boolean;
  result: Record<string, unknown> | null;
}

/** `GET /v1alpha1/me/storage` — what this account is using, and against
 *  what ceiling.
 *
 * `limit_bytes` and `percent_used` are BOTH null for an unlimited account,
 * and must stay null through the client. Coercing either to 0 draws an
 * empty progress bar and implies a limit that does not exist — "no limit"
 * and "0% of a limit" look identical the moment one of them becomes a
 * number. */
export interface AccountStorage {
  used_bytes: number;
  limit_bytes: number | null;
  percent_used: number | null;
}

/** `GET /v1alpha1/me/metrics?window_days=N` — the platform's reliability
 *  numbers over the trailing window, for the reliability page.
 *
 * Two different kinds of field, and they must not be handled the same way:
 *
 * - The counts (`jobs_*`, `tasks_*`, `machines_contributing`) are plain
 *   tallies. `0` is a real, honest answer for them — "nothing happened in
 *   this window" — and renders as the number 0.
 * - `goodput_ratio`, `lost_task_seconds`, `mttr_seconds`, `mttd_seconds`
 *   are DERIVED and every one of them is independently nullable. Every one
 *   of them really will be null in production for now — the events needed
 *   to derive them may not exist yet. Null here means "not measured", and
 *   must never be coerced to 0 or rendered as an empty chart: on a page
 *   whose whole point is proving a reliability claim, a fabricated zero is
 *   worse than an honest blank. `goodput_ratio` additionally reads null
 *   whenever `tasks_attempted` is 0 (nothing to divide), which is a
 *   different reason than "not instrumented yet" but the same correct
 *   rendering — see `lib/platform-metrics.ts` for how the two are told
 *   apart in copy.
 *
 * `goodput_ratio` is a 0..1 fraction, not a percentage — multiply by 100
 * before display. */
export interface PlatformMetrics {
  window_days: number;
  jobs_total: number;
  jobs_succeeded: number;
  jobs_partial: number;
  jobs_failed: number;
  tasks_attempted: number;
  tasks_accepted: number;
  goodput_ratio: number | null;
  lost_task_seconds: number | null;
  mttr_seconds: number | null;
  mttd_seconds: number | null;
  machines_contributing: number;
}

export interface SubmitFromRepoResult extends JobRecord {
  findings: PreflightFinding[];
}

/** One entry of the coordinator's event ledger, as
 * `GET /v1alpha1/jobs/{id}/events` returns it. Mirrors
 * `flashruntime.protocol.v1alpha1.Event`.
 *
 * `type` is typed as a plain string on purpose. It is an enum upstream with
 * 30-odd members that grows independently of this repo, and a union here
 * would turn "the runtime added an event type" into a build break in the
 * console. The UI groups by known prefixes and renders anything it does not
 * recognise verbatim rather than dropping it, which is the behaviour you
 * want from a ledger.
 *
 * `data` carries `task_id` and, for lease and commit events, `node_id`.
 * Neither is guaranteed: job-level events (JOB_ACCEPTED, JOB_SUCCEEDED)
 * carry neither, so both are read defensively.
 */
export interface JobEvent {
  job_id: string;
  type: string;
  timestamp: string;
  source: string;
  message: string;
  data: Record<string, unknown>;
  /** Present only on a federated job, where the API fans out over the
   * per-round coordinator jobs and tags each event with its round. */
  round?: number;
}

/** Current state of one task, from `GET /v1alpha1/jobs/{id}/tasks`.
 *
 * Current state ONLY. The coordinator's task view carries no attempt
 * history and no `accepted` flag, so anything historical (which node held
 * what, and how it ended) is derived from the event ledger instead. See
 * `lib/job-activity.ts`. */
export interface JobTask {
  task_id: string;
  state: "PENDING" | "LEASED" | "COMPLETED" | "FAILED" | "CANCELLED" | string;
  attempts: number;
  max_attempts: number;
  /** The node holding it now, or the last one to hold it. Null if never
   * claimed. */
  node_id: string | null;
  /** The live lease deadline, ISO. Null when no lease is active. */
  deadline: string | null;
  /** Federated only: task ids repeat across rounds, so rows are not merged. */
  round?: number;
}

/** A row of `public.pools` — exactly `POOL_PUBLIC_COLUMNS` in `db.py` — as
 * every pool route returns it: `GET /v1alpha1/pools/{id}` (flattened
 * alongside `members`, see `getPool()`) and `POST /v1alpha1/pools` both
 * return precisely this, no more.
 *
 * `GET /v1alpha1/pools` (the LIST route) returns MORE than this per pool —
 * see `PoolSummary` below — so this base type deliberately does not carry
 * `member_count`/`machines_online`: a route that cannot supply them no
 * longer has to lie about it with a runtime-`undefined` field the type
 * claims is a `number`. */
export interface Pool {
  id: string;
  name: string;
  owner_id: string;
  created_at: string;
}

/** `GET /v1alpha1/pools`'s row shape: `Pool` plus the two aggregates
 * `list_pools_for_user` (`db.py`) computes in the same query —
 * `member_count`, `machines_online` — the same single-query-not-N
 * reasoning `listJobRounds` documents elsewhere. Only this route populates
 * them; do not reach for these two fields off a `getPool()` or
 * `createPool()` result, which are typed as the plain `Pool` above
 * specifically so the compiler catches that instead of a comment having
 * to. */
export interface PoolSummary extends Pool {
  member_count: number;
  machines_online: number;
}

/** A row of `public.pool_members` joined to the member's profile, as
 * `GET /v1alpha1/pools/{id}` returns each entry of `members`. Per-MEMBER
 * machine counts (their own enrolled machines), not the pool's aggregate —
 * see `list_pool_members`, `db.py`. */
export interface PoolMember {
  user_id: string;
  display_name: string | null;
  joined_at: string;
  machine_count: number;
  machines_online: number;
}

/** `GET /v1alpha1/jobs/{id}/contributions`'s row shape — the per-machine
 * credit view for a job: which machine did the work, whose it is, and how
 * much. Mirrors `list_job_contributions`'s query in `db.py` exactly (join
 * of `contributions` -> `machines` -> `profiles`), one row per machine that
 * was credited, not per task.
 *
 * `machine_name` and `member_display_name` are nullable because `Machine.name`
 * and `Profile.display_name` both are — a machine enrolled without a name,
 * or a member who never set one, still gets a credit row, just with a null
 * label rather than a missing one. An independent (non-pool) job has no
 * contributions at all and this route returns `[]` for it, never an error. */
export interface JobContribution {
  node_id: string;
  machine_name: string | null;
  member_display_name: string | null;
  tasks_credited: number;
  total_duration_s: number;
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

  const url = `${cloudApiBase()}${path}`;

  // fetch() rejects with a bare `TypeError: Failed to fetch` for every
  // transport-level failure — wrong host, DNS, CORS, mixed content, offline,
  // connection refused — and names none of them. Shown to a user verbatim it
  // is indistinguishable from the API being down, and it hides the one fact
  // that identifies the cause: which URL was actually called. Say it.
  let res: Response;
  try {
    res = await fetch(url, { ...init, headers });
  } catch (cause) {
    const reason = cause instanceof Error ? cause.message : String(cause);
    throw new ApiError(0, `${reason} — could not reach ${url}`);
  }

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

/** `PATCH /v1alpha1/me` — sets the display name. Kept alongside the wider
 * `updateProfile` below for its existing callers, who only ever touch this
 * one field. Email and avatar belong to the identity provider,
 * `github_login` is written by enrolment, and the role flags are roles
 * rather than preferences, so none of them are editable here or through
 * `updateProfile`.
 *
 * The API rejects an empty string rather than treating it as "leave it
 * alone", because clearing a field and not touching it are different
 * intentions and the underlying upsert coalesces null to the latter. */
export function updateMe(displayName: string): Promise<Profile> {
  return request<Profile>("/v1alpha1/me", {
    method: "PATCH",
    body: JSON.stringify({ display_name: displayName }),
  });
}

export function listMachines(): Promise<Machine[]> {
  return request<Machine[]>("/v1alpha1/machines");
}

/** `POST /v1alpha1/device/approve`. `poolId`, when given, rides in the
 * body as `pool_id` and asks the API to approve-and-bind in one atomic
 * step — the route's own docstring: binding after the one-shot device
 * code is already consumed would strand the volunteer's agent on a bind
 * failure it cannot retry. Omitted from the body entirely (not sent as
 * `pool_id: undefined`, which `JSON.stringify` would also drop, but left
 * out of the object built here) when no pool is chosen, so the request is
 * byte-identical to what this client sent before pools existed. */
export function approveDeviceCode(
  userCode: string,
  poolId?: string
): Promise<ApproveDeviceCodeResult> {
  const body: { user_code: string; pool_id?: string } = { user_code: userCode };
  if (poolId) body.pool_id = poolId;
  return request<ApproveDeviceCodeResult>("/v1alpha1/device/approve", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function revokeMachine(machineId: string): Promise<RevokeMachineResult> {
  return request<RevokeMachineResult>(
    `/v1alpha1/machines/${encodeURIComponent(machineId)}/revoke`,
    { method: "POST" }
  );
}

// -- pools and invites -------------------------------------------------

export function listPools(): Promise<PoolSummary[]> {
  return request<PoolSummary[]>("/v1alpha1/pools");
}

/** `POST /v1alpha1/pools` requires admission (creating a pool is state
 * creation, the thing the alpha gate exists to block) — a 401-shaped
 * `NotAuthenticated` never fires here for "not admitted"; that case
 * surfaces as a plain `ApiError` with the API's own detail
 * ("access not yet approved"), same as any other refusal. */
export function createPool(name: string): Promise<Pool> {
  return request<Pool>("/v1alpha1/pools", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

/** `GET /v1alpha1/pools/{id}/machines` — the workspace's whole fleet, one
 * row per bound machine across every member.
 *
 * Member-scoped server-side: a non-member gets 404, indistinguishable from
 * a pool that does not exist, so `NotFound` from here must never be
 * reworded into an access-denied message.
 *
 * Machines whose owner has left the pool are already excluded by the query
 * (`list_pool_machines`), matching what placement actually sees — so the
 * count rendered from this list is the workspace's real capacity, not an
 * optimistic one. */
export function listPoolMachines(poolId: string): Promise<PoolMachine[]> {
  return request<PoolMachine[]>(
    `/v1alpha1/pools/${encodeURIComponent(poolId)}/machines`
  );
}

/** `PATCH /v1alpha1/pools/{id}` — rename, owner only.
 *
 * A member who is not the owner gets 404, the same as a stranger: the
 * caller cannot distinguish the two and must not try to. The API trims and
 * caps at 200 characters, so the returned `Pool` is the authority on what
 * the name actually became — render that, not the string you sent. */
export function renamePool(poolId: string, name: string): Promise<Pool> {
  return request<Pool>(`/v1alpha1/pools/${encodeURIComponent(poolId)}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}

/** `GET /v1alpha1/pools/{id}` actually returns the pool's own columns
 * flattened alongside `members` — `{...Pool fields, members: [...]}` — NOT
 * `{pool: {...}, members: [...]}`. */
type PoolDetailResponse = Pool & { members: PoolMember[] };

/** Reshapes that flat response into `{pool, members}` so callers get one
 * nested shape, matching every other place this client separates "the
 * thing" from "the list attached to it". No cast needed on the way out:
 * `Pool` (unlike `PoolSummary`) has no fields this route fails to supply. */
export async function getPool(
  poolId: string
): Promise<{ pool: Pool; members: PoolMember[] }> {
  const { members, ...pool } = await request<PoolDetailResponse>(
    `/v1alpha1/pools/${encodeURIComponent(poolId)}`
  );
  return { pool, members };
}

/** Opts one of the caller's own machines into serving one of the caller's
 * own pools — `PUT /v1alpha1/pools/{poolId}/machines/{machineId}`, 204, no
 * body. Both halves are scoped to the caller exactly as the route's own
 * docstring describes (pool via membership, machine via ownership), so an
 * id that is not the caller's own 404s here as `NotFound`, same as
 * everywhere else in this client — never a distinguishing 403. */
export function bindMachineToPool(
  poolId: string,
  machineId: string
): Promise<void> {
  return request<void>(
    `/v1alpha1/pools/${encodeURIComponent(poolId)}/machines/${encodeURIComponent(machineId)}`,
    { method: "PUT" }
  );
}

/** The inverse of `bindMachineToPool` — `DELETE` on the same route.
 * Unbinding a pair that was never bound is a no-op on the API side, not an
 * error, as long as both ids are the caller's own. */
export function unbindMachineFromPool(
  poolId: string,
  machineId: string
): Promise<void> {
  return request<void>(
    `/v1alpha1/pools/${encodeURIComponent(poolId)}/machines/${encodeURIComponent(machineId)}`,
    { method: "DELETE" }
  );
}

/** Mints a one-time invite link's token. The API returns the raw token
 * exactly once — it is hashed for storage and never kept in the clear, so
 * this is the only place it could ever be recovered — and this function
 * does nothing with it but hand it back: no logging, no persistence, no
 * echoing anywhere else in this client.
 *
 * `opts.uses` and `opts.expires_hours` each ride in the body only when
 * explicitly set, exactly like `submitFromRepo`'s `ref`/`pool` already do
 * — an omitted or empty `opts` sends no body at all, byte-identical to
 * this call before either knob existed, so the API's own defaults (10
 * uses / 720 hours) apply. */
export function createPoolInvite(
  poolId: string,
  opts?: { uses?: number; expires_hours?: number }
): Promise<{ token: string }> {
  const body: { uses?: number; expires_hours?: number } = {};
  if (opts?.uses !== undefined) body.uses = opts.uses;
  if (opts?.expires_hours !== undefined) body.expires_hours = opts.expires_hours;
  return request<{ token: string }>(
    `/v1alpha1/pools/${encodeURIComponent(poolId)}/invites`,
    {
      method: "POST",
      ...(Object.keys(body).length > 0 ? { body: JSON.stringify(body) } : {}),
    }
  );
}

/** `GET /v1alpha1/pools/{id}/invites`'s non-empty shape — the pool's
 * current standing invite, owner only. Never a token or its hash (see
 * `fetch_outstanding_invite`'s own docstring): this is a state summary for
 * the console to render, not a way to recover a link already handed out. */
export interface PoolInviteState {
  uses_remaining: number;
  expires_at: string;
  created_at: string;
}

/** Fetches the pool's outstanding invite, if any. The API answers `{}`
 * (never a 404) when nothing is currently redeemable — this maps that
 * empty object to `null` so the console can branch on "generate a link"
 * vs. "here's the current one" without every caller re-deriving "empty
 * object means nothing outstanding" for itself. */
export async function getPoolInviteState(
  poolId: string
): Promise<PoolInviteState | null> {
  const state = await request<PoolInviteState | Record<string, never>>(
    `/v1alpha1/pools/${encodeURIComponent(poolId)}/invites`
  );
  return Object.keys(state).length === 0 ? null : (state as PoolInviteState);
}

/** `DELETE /v1alpha1/pools/{id}/invites` — kills every invite ever issued
 * for this pool, owner only, same 404 doctrine as the other invite
 * routes. Returns how many rows were revoked. */
export function revokePoolInvites(poolId: string): Promise<{ revoked: number }> {
  return request<{ revoked: number }>(
    `/v1alpha1/pools/${encodeURIComponent(poolId)}/invites`,
    { method: "DELETE" }
  );
}

/** `POST /v1alpha1/invites/accept` — deliberately callable by a
 * signed-in-but-not-yet-admitted account (the API's `accept_invite` sits on
 * `current_user`, not `admitted_user`): the only path into a workspace
 * cannot require already being in one.
 *
 * This call joins a WORKSPACE. It does not admit the account — admission is
 * a separate, admin-granted decision (see `AccessState`/`submitAccessRequest`).
 * `joined` reports which happened: `true` for an already-admitted caller,
 * who is added to the pool immediately; `false` for anyone else, whose join
 * is banked on their access request and only applied when an admin later
 * approves them. */
export function acceptInvite(
  token: string
): Promise<{ pool_id: string; name: string; joined: boolean }> {
  return request<{ pool_id: string; name: string; joined: boolean }>(
    "/v1alpha1/invites/accept",
    { method: "POST", body: JSON.stringify({ token }) }
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

/** `GET /v1alpha1/jobs/{id}/result` — owner-scoped exactly like `getJob`.
 *
 * Resolves to `null` for a federated job, which the API answers 409 to: its
 * aggregation IS its round history and the driver already performed it.
 * That is a shape of job, not a failure, so the page must be able to omit
 * the panel without rendering an error. Every other non-2xx still throws. */
/** `GET /v1alpha1/me/storage` — the caller's own usage. Never another
 *  account's: the route reads the verified JWT sub and takes no id. */
export function getMyStorage(): Promise<AccountStorage> {
  return request<AccountStorage>("/v1alpha1/me/storage");
}

/** `GET /v1alpha1/me/metrics` — the caller's own reliability numbers over
 *  the trailing `windowDays`. Defaults to 30, matching the API's own
 *  documented default and the contract example, but the query param is
 *  always sent explicitly (never omitted) so the reliability page's window
 *  selector round-trips to a URL a caller could bookmark or reason about
 *  rather than depending on a default that lives only in the API. */
export function getMyMetrics(windowDays = 30): Promise<PlatformMetrics> {
  return request<PlatformMetrics>(
    `/v1alpha1/me/metrics?window_days=${windowDays}`
  );
}

export async function getJobResult(jobId: string): Promise<JobResult | null> {
  try {
    return await request<JobResult>(
      `/v1alpha1/jobs/${encodeURIComponent(jobId)}/result`
    );
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) return null;
    throw err;
  }
}

/** `GET /v1alpha1/jobs/{id}/events` — owner-scoped exactly like `getJob`.
 *
 * `since` is an offset into an append-only list, not a timestamp: pass the
 * number of events already held and get only what arrived after. A time
 * cursor would be wrong here because a single sweep expires a lease and
 * requeues its task with identical timestamps, so any `>` comparison drops
 * one of them and any `>=` replays it. */
export function listJobEvents(jobId: string, since = 0): Promise<JobEvent[]> {
  const q = since > 0 ? `?since=${since}` : "";
  return request<JobEvent[]>(
    `/v1alpha1/jobs/${encodeURIComponent(jobId)}/events${q}`
  );
}

/** `GET /v1alpha1/jobs/{id}/tasks` — owner-scoped. Current state only. */
export function listJobTasks(jobId: string): Promise<JobTask[]> {
  return request<JobTask[]>(
    `/v1alpha1/jobs/${encodeURIComponent(jobId)}/tasks`
  );
}

/** `GET /v1alpha1/jobs/{id}/contributions` — visibility matches the
 * sibling read routes above: the owner, or any member of the job's pool,
 * may see who did the work; a job that exists and the caller cannot see
 * 404s here too, same as `getJob`. An independent (non-pool) job returns
 * `[]`, not an error — there is nobody to credit. */
export function listJobContributions(jobId: string): Promise<JobContribution[]> {
  return request<JobContribution[]>(
    `/v1alpha1/jobs/${encodeURIComponent(jobId)}/contributions`
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

/** `DELETE /v1alpha1/jobs/{id}/artifacts`'s success body — how much this
 * call actually freed, so the caller can say something more useful than
 * "done". */
export interface DeleteJobArtifactsResult {
  deleted_files: number;
  freed_bytes: number;
}

/** `DELETE /v1alpha1/jobs/{id}/artifacts` — permanently removes every
 * artifact this job wrote, freeing the account's storage quota. THIS
 * DESTROYS DATA AND CANNOT BE UNDONE; this function performs no
 * confirmation of its own — that decision belongs to the UI, which must
 * make it hard to reach by accident.
 *
 * Owner-scoped exactly like every other job route: a job that is not the
 * caller's own answers 404, same as one with nothing to delete — the two
 * causes share a status deliberately (see `NotFound`'s class doc) so a
 * guesser cannot learn which id is real from the difference. A 409
 * (surfaced as a plain `ApiError`, not a special class — this client has no
 * other route that reacts to a still-running job this way) means the job
 * has not reached a terminal state: something could still be writing into
 * its own output directory, and the API's own detail explains that. */
export function deleteJobArtifacts(
  jobId: string
): Promise<DeleteJobArtifactsResult> {
  return request<DeleteJobArtifactsResult>(
    `/v1alpha1/jobs/${encodeURIComponent(jobId)}/artifacts`,
    { method: "DELETE" }
  );
}

/** `POST /v1alpha1/jobs` — the plain path, no repo/preflight involved. */
export function submitJob(spec: unknown): Promise<JobRecord> {
  return request<JobRecord>("/v1alpha1/jobs", {
    method: "POST",
    body: JSON.stringify(spec),
  });
}

/** `POST /v1alpha1/jobs/from-repo` — throws `PreflightRejected` (never a
 * plain `ApiError`) when the API's preflight finds a blocking error.
 *
 * `pool` rides in the body only when set, exactly like `ref` already did:
 * omitting the key (rather than sending `pool: undefined`, which
 * `JSON.stringify` also drops, or `pool: null`, which it would not) keeps
 * the request identical to what this client sent before pools existed for
 * every submission that leaves the selector on "No pool — public queue". */
export function submitFromRepo(
  repo: string,
  ref?: string,
  pool?: string
): Promise<SubmitFromRepoResult> {
  const body: { repo: string; ref?: string; pool?: string } = { repo };
  if (ref) body.ref = ref;
  if (pool) body.pool = pool;
  return request<SubmitFromRepoResult>("/v1alpha1/jobs/from-repo", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// -- onboarding and access -----------------------------------------------

export interface OnboardingSubmission {
  first_name: string;
  last_name: string;
  company_name: string;
  role: string;
  team_size: string;
  use_case: string;
  compute_sources: string[];
  heard_from?: string;
}

/** `POST /v1alpha1/access-request` — callable by a not-yet-admitted
 * account on purpose: this IS how an account asks to be admitted. A 409
 * means the account's access is already decided (admitted or declined); an
 * already-admitted user edits these same fields through `updateProfile`
 * instead. */
export function submitAccessRequest(
  body: OnboardingSubmission
): Promise<{ access: AccessState }> {
  return request<{ access: AccessState }>("/v1alpha1/access-request", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** A row of `GET /v1alpha1/admin/access-requests` — one account's
 * onboarding submission plus the admin-facing context around it:
 * `pending_pool_name`/`invited_by_name` surface which invite (if any) is
 * banked on this decision, so an admin approving or declining can see what
 * they're admitting the account into. */
export interface AccessRequestRow {
  user_id: string;
  email: string | null;
  first_name: string | null;
  last_name: string | null;
  company_name: string | null;
  role: string | null;
  team_size: string | null;
  email_domain: string | null;
  is_personal_email: boolean | null;
  use_case: string | null;
  compute_sources: string[];
  heard_from: string | null;
  requested_at: string;
  pending_pool_name: string | null;
  invited_by_name: string | null;
}

/** `GET /v1alpha1/admin/access-requests` — admin only. A non-admin gets a
 * plain `ApiError` carrying the API's "admin required", not a
 * `NotAuthenticated`: this is an authorization failure on an authenticated
 * caller, not a signed-out one. An unrecognised `status` is a 400, not an
 * empty list — a typo must not read as "nobody is waiting". */
export function listAccessRequests(
  status: string = "pending"
): Promise<AccessRequestRow[]> {
  return request<AccessRequestRow[]>(
    `/v1alpha1/admin/access-requests?status=${encodeURIComponent(status)}`
  );
}

/** `POST /v1alpha1/admin/access-requests/{userId}/approve` — admin only.
 * 404s (via `NotFound`) when there was no pending request for this user;
 * `NotFound` here means "nothing to approve", not "unknown user". */
export function approveAccessRequest(userId: string): Promise<void> {
  return request<void>(
    `/v1alpha1/admin/access-requests/${encodeURIComponent(userId)}/approve`,
    { method: "POST" }
  );
}

/** The decline counterpart of `approveAccessRequest` — same route shape,
 * same 404-means-nothing-pending doctrine. */
export function declineAccessRequest(userId: string): Promise<void> {
  return request<void>(
    `/v1alpha1/admin/access-requests/${encodeURIComponent(userId)}/decline`,
    { method: "POST" }
  );
}

/** `PATCH /v1alpha1/me` with the wider profile field set the onboarding
 * form collects. Sibling to `updateMe` (display name only), not a
 * replacement for it — see `updateMe`'s docstring for which fields are
 * user-owned at all. The API silently drops anything outside this set
 * rather than rejecting it, so a caller passing e.g. `is_admin` here would
 * see it ignored, not erred on; the `Pick` below stops that at compile
 * time instead. */
export function updateProfile(
  fields: Partial<
    Pick<
      Profile,
      | "display_name"
      | "first_name"
      | "last_name"
      | "company_name"
      | "role"
      | "team_size"
    >
  >
): Promise<Profile> {
  return request<Profile>("/v1alpha1/me", {
    method: "PATCH",
    body: JSON.stringify(fields),
  });
}
