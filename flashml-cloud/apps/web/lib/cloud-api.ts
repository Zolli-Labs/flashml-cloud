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
import type { SandboxEvent, SandboxSession } from "./sandbox-session";

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

/** A developer CLI credential. Mirrors `public.cli_credentials`, minus
 * `token_hash` — the API's column allowlist never sends it and this type
 * must not invite anyone to look for it. */
export interface CliCredential {
  id: string;
  label: string | null;
  status: "active" | "revoked";
  /** The first 12 characters of the raw token, kept so a person can tell
   * two credentials apart. Not a secret and not sufficient to authenticate. */
  token_prefix: string;
  last_used_at: string | null;
  created_at: string;
  revoked_at: string | null;
}

/** `POST /v1alpha1/device/approve`. One route, two flows: `kind` says
 * which, and exactly one of `machine_id` / `credential_id` is present.
 * `kind` is optional only because a response from an API deployed before
 * the CLI flow existed carries `machine_id` and no `kind` — treat its
 * absence as "machine". */
export interface ApproveDeviceCodeResult {
  status: string;
  kind?: "machine" | "cli";
  machine_id?: string;
  credential_id?: string;
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

/** One file of `GET /v1alpha1/jobs/{id}/artifacts` — the listing route, and
 * the ONLY source of a job's output files this console reads.
 *
 * `key` is already relative to the job's own artifact prefix (`task-000/
 * result.json`, `task-000/ckpt/step-40.json`), which is exactly what the
 * download route's `{key}` segment takes — so nothing here strips a prefix
 * off anything, and there is no uri to misread. */
export interface JobArtifactEntry {
  key: string;
  size_bytes: number;
}

/** Where the bytes this listing describes actually are.
 *
 * `"oss"` — mirrored to Alibaba OSS. `"coordinator"` — still only on the
 * coordinator. Typed as a widened string for the same reason `JobEvent.type`
 * is: a third backend added API-side must not turn into a console build
 * break, and the UI names an unrecognised value verbatim rather than
 * guessing which of the two it resembles. */
export type ArtifactStorage = "oss" | "coordinator";

/** `GET /v1alpha1/jobs/{id}/artifacts`.
 *
 * `mirrored_at` is null whenever no mirror has been recorded — including
 * when `storage` is `"oss"`, which the contract permits. Null means "not
 * reported", never "just now", and nothing built on it may supply a time the
 * API did not. */
export interface JobArtifactListing {
  artifacts: JobArtifactEntry[];
  storage: ArtifactStorage | string;
  mirrored_at: string | null;
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
  /** The coordinator's own artifact list, and **always `[]` for every job
   * this deployment actually runs**: its only writer sits on a KubeRay path
   * nothing here takes. Kept because the API still sends the field, but the
   * console must not read it — `listJobArtifacts` below is the real listing.
   * Rendering a card off this field is how the Artifacts card came to be
   * empty for every job in the first place. */
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
  /** Every lease claimed — the honest answer to "how much work was handed
   * out". **Not the goodput denominator**, and it stopped being one on
   * 2026-08-11 (migration 0015). See `tasks_resolved`. */
  tasks_attempted: number;
  /** Attempts that reached a terminal state, and the number `goodput_ratio`
   * divides by. Before 0015 one column did both jobs, so an attempt still in
   * flight and an attempt that had failed were the same row: the ratio fell
   * the moment work was handed out and recovered only if that exact attempt
   * was accepted. An UNRESOLVED attempt is in flight or predates 0015, and in
   * neither case is it evidence of anything. */
  tasks_resolved: number;
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

/** One file of a committed checkpoint. Mirrors
 * `flashruntime.protocol.v1alpha1.CheckpointPart`. */
export interface CheckpointPart {
  key: string;
  sha256: string;
  size_bytes: number;
}

/** One committed checkpoint manifest, as
 * `GET /v1alpha1/jobs/{id}/tasks/{task_id}/checkpoints/latest` returns it.
 * Mirrors `flashruntime.protocol.v1alpha1.CheckpointManifest`.
 *
 * `job_id` here is NOT this job's id. The catalog is keyed per (job, task)
 * and the scope it stores is the composite `"<job_id>::<task_id>"`
 * (`flashruntime/service/checkpoints.py`), so the manifest carries that
 * composite string in its `job_id` field. Nothing in the console should
 * match it against a job id; the id you asked about is the one you already
 * have.
 *
 * `validation` is the runtime's own verdict from its validation ladder —
 * `hash_verified`, `restore_verified`, or `invalid` — and is typed loosely
 * for the same reason `JobEvent.type` is: it is an enum that grows upstream,
 * and a union here would turn a runtime addition into a console build break. */
export interface CheckpointManifest {
  manifest_id: string;
  job_id: string;
  attempt_id: string;
  step: number;
  framework: string;
  strategy_family: string;
  world_size: number;
  compatible_world_sizes: number[];
  storage_prefix: string;
  parts: CheckpointPart[];
  validation: "hash_verified" | "restore_verified" | "invalid" | string;
  created: string;
  checkpoint_duration_s: number | null;
}

/** The four answers a checkpoint read can give, kept apart because the UI
 * must say something different for each — and because collapsing any two of
 * them would put a false statement on the page.
 *
 * - `committed` — a manifest. Every number shown comes from it.
 * - `none` — the coordinator answered 404, which its own route spells "no
 *   valid checkpoint". That is ALL it means: it does not distinguish a task
 *   that has not reached its first checkpoint from a workload that never
 *   writes one, so nothing built on it may claim to either.
 * - `unreadable` — 401. The checkpoint routes are declared `tags=["agent"]`
 *   and depend on `current_machine`, whose docstring is explicit that a
 *   valid browser JWT is refused: "a different credential kind [that] must
 *   not open agent routes". A 401 here is therefore a fact about this API's
 *   shape, NOT about the caller's session — which is exactly why this
 *   function does not raise `NotAuthenticated` and must never be allowed to
 *   bounce a signed-in user to /sign-in. `getJob` next door is the authority
 *   on whether the session is real.
 * - `unknown` — anything else. Rendered as unknown, never as "no
 *   checkpoints": a gateway blip must not be reported to a user as their run
 *   having lost its resume point. */
export type TaskCheckpointRead =
  | { status: "committed"; manifest: CheckpointManifest }
  | { status: "none" }
  | { status: "unreadable" }
  | { status: "unknown"; detail: string };

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

/** One row of `GET /v1alpha1/me/contributions`'s `machines` array — one of
 * this account's own machines, and how much of it has been credited across
 * every job it has ever run, not just the one job page a credit used to be
 * visible from.
 *
 * `hostname` and `last_seen_at` are genuinely nullable: an agent that has
 * never reported a hostname, or a machine that has never been leased a task
 * at all, is a real and different state from "we know and it's blank" — see
 * `lib/contributions.ts` for how each renders as its own explicit label
 * rather than an empty string or a fabricated date. */
export interface ContributionMachine {
  machine_id: string;
  hostname: string | null;
  accepted_tasks: number;
  last_seen_at: string | null;
}

/** `GET /v1alpha1/me/contributions` — the caller's own running total of
 * accepted work, across every machine and every job, independent of which
 * job page happens to be open. These are COUNTERS, not a currency: nothing
 * here is ever spent, redeemed, or drawn down, only added to as more work is
 * accepted — see `lib/contributions.ts`'s module doc for why the copy built
 * from this must never imply a balance. */
export interface MyContributions {
  accepted_tasks: number;
  jobs_contributed_to: number;
  machines: ContributionMachine[];
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
// the router's answer: POST /v1alpha1/jobs/preview-plans
// ---------------------------------------------------------------------------
//
// Read `preview_plans`' docstring in `apps/api/flashml_cloud_api/app.py`
// before changing any of these. Four of its invariants are shapes, not
// conventions, and the types below are written to make breaking them
// awkward:
//
//   1. ZC and USD retain separate settlement fields. The API also supplies a
//      normalized comparison value under the fixed 1 ZC = $1 USD policy. The
//      routing card deliberately does not render that value as a combined
//      cash total.
//   2. Every figure carries its `basis` and its `n`.
//   3. Anything not derivable is `null` — *not observed*, never 0. That is
//      why `makespan_seconds`, `basis`, `n`, `acceptance_rate` and friends
//      are nullable here rather than defaulted.
//   4. A plan's `basis` is the WEAKEST behind any machine it allocates to.
//
// `kind`, `kind_evidence`, `venues` and `venue_excluded_machines` are
// OPTIONAL rather than nullable, and that is a fact about the route: its
// `_degraded` answer (routing not configured on this deployment, a spec that
// expands to nothing, an account with no capacity at all) is a 200 that omits
// those keys entirely. A consumer that assumes they are present renders the
// router's verdict for a job the router never looked at.

/** A cost vector. Source currencies remain separate; their fixed-rate
 * comparable value is supplied for scheduler ordering, not settlement. */
export interface PreviewCost {
  zc: number;
  usd: number;
  total_usd_value: number;
}

/** A duration estimate with the evidence behind it. `null` for the whole
 * object means no estimate exists; `low_seconds`/`high_seconds` are the
 * range. `basis` is one of `measured` / `estimated` / `projected`. */
export interface PreviewEstimate {
  low_seconds: number | null;
  high_seconds: number | null;
  basis: string | null;
  n: number | null;
  note: string | null;
}

/** One venue judged against this job's kind of work.
 *
 * THE TWO REFUSALS ARE DIFFERENT STATEMENTS and this interface keeps them
 * apart because the API does:
 *
 *   `suited: false`      this venue PHYSICALLY CANNOT run this work
 *   `acquirable: false`  we CANNOT GET CAPACITY there yet
 *
 * `usable` is both. Collapsing the first two into one boolean would let a
 * surface imply we chose not to use a venue we simply cannot reach. */
export interface PreviewVenue {
  id: string;
  display: string;
  currency: string;
  suited: boolean;
  acquirable: boolean;
  usable: boolean;
  /** `automatic` | `manual` | `none`. */
  acquisition: string;
  /** The API's own sentence, carrying the fact behind the verdict. Rendered
   * verbatim — paraphrasing it makes it more confident than it is. */
  reason: string;
}

/** One machine inside one plan. */
export interface PreviewAllocation {
  machine_id: string;
  tasks: number;
  finish_seconds: number | null;
  cost: PreviewCost;
  venue: string | null;
  currency: string | null;
  reliability_tier: string;
  basis: string | null;
  n: number | null;
}

/** One of cheapest / balanced / fastest. `balanced` appears ONLY when a
 * deadline was given — with nothing to balance against it would be
 * "cheapest" shown twice. */
export interface PreviewPlan {
  name: string;
  recommended: boolean;
  tasks_placed: number;
  tasks_unplaced: number;
  cost: PreviewCost;
  /** Answered PER CURRENCY, and `null` where no budget was given for that
   * currency — a fact about a question nobody asked, not a pass. */
  within_budget: { zc: boolean | null; usd: boolean | null };
  makespan_seconds: number | null;
  deadline_seconds: number | null;
  deadline_met: boolean | null;
  achievable_deadline_seconds: number | null;
  basis: string | null;
  n: number | null;
  duration_basis: string | null;
  dominated_by: string | null;
  allocations: PreviewAllocation[];
  notes: string[];
}

/** One machine in the fleet this account can reach, priced. */
export interface PreviewCandidate {
  machine_id: string;
  name: string | null;
  venue: string | null;
  currency: string;
  price_zc_per_hour: number;
  price_label: string;
  listing_id: string | null;
  capability_class: string | null;
  reliability_tier: string;
  acceptance_rate: number | null;
  n: number | null;
  max_concurrent_tasks: number;
  seconds_per_task: number | null;
  basis: string | null;
  eligible: boolean;
}

/** The probe that turns "we cannot predict this" into a measurement. */
export interface PreviewCanary {
  machine_id: string;
  tasks_to_calibrate: number;
  reason: string;
  current_basis: string | null;
}

/** `POST /v1alpha1/jobs/preview-plans`. Nothing is submitted, matched, held
 * or charged by this call — it is read-only by construction. */
export interface PlanPreview {
  job_id: string | null;
  tasks: number | null;
  /** Absent on the `_degraded` answer — see the block comment above. */
  kind?: string | null;
  kind_evidence?: string | null;
  venues?: PreviewVenue[];
  venue_excluded_machines?: number;
  duration: PreviewEstimate | null;
  plans: PreviewPlan[];
  candidates: PreviewCandidate[];
  canary: PreviewCanary | null;
  recommended: string | null;
  eligible_machines: number;
  excluded_machines: number;
  unplannable_machines: number;
  notes: string[];
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

/** One authenticated call, up to and including turning a non-2xx into the
 * right error class — everything `request` does except reading the body.
 *
 * Split out so the artifact download can share it: that one wants the raw
 * `Response` to read as a Blob rather than as JSON, and it must fail in
 * exactly the same way as every other call (a 401 has to become
 * `NotAuthenticated` and redirect to sign-in, not surface as a corrupt file).
 * A second hand-rolled fetch with its own error handling is how those two
 * drift apart. */
async function send(path: string, init: RequestInit = {}): Promise<Response> {
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
  return res;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await send(path, init);
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

export function listCliCredentials(): Promise<CliCredential[]> {
  return request<CliCredential[]>("/v1alpha1/cli-credentials");
}

export function revokeCliCredential(
  credentialId: string
): Promise<{ revoked: boolean }> {
  return request<{ revoked: boolean }>(
    `/v1alpha1/cli-credentials/${encodeURIComponent(credentialId)}/revoke`,
    { method: "POST" }
  );
}

// -- GitHub App installations ------------------------------------------

export interface GitHubInstallation {
  installation_id: number;
  account_login: string;
  account_type: string;
  /** `all` or `selected`. Advisory: it explains a 404 on a repo the
   * installation cannot see. GitHub, not this field, decides what a token
   * may read. */
  repository_selection: string;
  created_at: string;
}

export interface GitHubInstallations {
  /** False when the deployment has no GitHub App at all. The page renders
   * no Connect button in that case — offering one would walk somebody to a
   * dead end. */
  configured: boolean;
  installations: GitHubInstallation[];
}

export function listGitHubInstallations(): Promise<GitHubInstallations> {
  return request<GitHubInstallations>("/v1alpha1/github/installations");
}

/** `POST /v1alpha1/github/install-url` — returns where to send the person.
 *
 * The URL is asked for rather than assembled here, and that is not
 * incidental: it carries a single-use `state` the API minted and recorded
 * against this account. A URL built in the browser would carry no state,
 * the callback would be refused, and the failure would look like a GitHub
 * problem rather than ours. */
export async function startGitHubInstall(): Promise<string> {
  const { url } = await request<{ url: string }>(
    "/v1alpha1/github/install-url",
    { method: "POST" }
  );
  return url;
}

/** `POST /v1alpha1/github/installations` — finish the flow.
 *
 * Both values come from GitHub's redirect query string and must travel
 * together: the id says what to connect, the state proves this account
 * started the flow. */
export function connectGitHubInstallation(
  installationId: number,
  state: string
): Promise<{ installation_id: number; account_login: string }> {
  return request<{ installation_id: number; account_login: string }>(
    "/v1alpha1/github/installations",
    {
      method: "POST",
      body: JSON.stringify({ installation_id: installationId, state }),
    }
  );
}

/** Forgets our record of the installation. Does NOT uninstall the App on
 * GitHub — one installation is shared by every colleague who connected it,
 * so that is the account admin's call, made on GitHub. */
export function disconnectGitHubInstallation(
  installationId: number
): Promise<void> {
  return request<void>(
    `/v1alpha1/github/installations/${encodeURIComponent(installationId)}`,
    { method: "DELETE" }
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

/** `GET /v1alpha1/me/contributions` — the caller's own contribution totals.
 * Never another account's: same doctrine as `getMyStorage`, the route reads
 * the verified JWT sub and takes no id. FlashML's premise is that people
 * contribute machines and get credited for it, and until this route existed
 * that credit was visible only inside one job's own page — this is the
 * account-wide total. */
export function getMyContributions(): Promise<MyContributions> {
  return request<MyContributions>("/v1alpha1/me/contributions");
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

/** `GET /v1alpha1/jobs/{id}/tasks/{task_id}/checkpoint`, classified rather
 * than thrown.
 *
 * Singular, and NOT the agent's `.../checkpoints/latest` one path segment
 * away: that route is machine-credentialed and answers 401 to a browser JWT
 * by design. This is the browser-tagged wrapper over the same coordinator
 * read, gated on the job's viewer check.
 *
 * The one read that can answer "how much work would a machine death cost
 * right now", and the only one that can: the coordinator emits
 * `CHECKPOINT_MANIFEST_COMMITTED` under the composite scope
 * `"<job_id>::<task_id>"`, so those events are absent from the bare-job
 * event feed `listJobEvents` reads and no ledger query can recover them.
 *
 * NEVER REJECTS. A job page reads this for several tasks on every poll tick,
 * and none of those reads may take the page down, redirect it, or leave an
 * unhandled rejection behind — the checkpoint panel is additive detail over
 * a page that has to keep working without it. See `TaskCheckpointRead` for
 * why a 401 in particular is classified and not raised. */
export async function readTaskCheckpoint(
  jobId: string,
  taskId: string
): Promise<TaskCheckpointRead> {
  try {
    const manifest = await request<CheckpointManifest>(
      `/v1alpha1/jobs/${encodeURIComponent(jobId)}/tasks/${encodeURIComponent(
        taskId
      )}/checkpoint`
    );
    return { status: "committed", manifest };
  } catch (err) {
    if (err instanceof NotFound) return { status: "none" };
    if (err instanceof NotAuthenticated) return { status: "unreadable" };
    if (err instanceof ApiError) return { status: "unknown", detail: err.detail };
    return {
      status: "unknown",
      detail: err instanceof Error ? err.message : String(err),
    };
  }
}

/** `POST /v1alpha1/jobs/preview-plans` for a job this account already
 * submitted — the routing story behind it: what kind of work the router took
 * it to be, which venues can and cannot run it, and what the reachable fleet
 * would cost and take.
 *
 * A POST that writes nothing. The route is read-only by construction (its
 * docstring: "Nothing is submitted, matched, held or charged by this call"),
 * and it is a POST only because it also accepts a whole JobSpec in the body.
 * That is why it is safe to issue from a page load — but it plans the fleet
 * on every call, so it is deliberately NOT part of this page's 2.5s poll.
 *
 * Owner-scoped like `getJob`: somebody else's job id is a 404 and not a 403,
 * because a 403 would confirm to a guesser that the id is real. A job with no
 * stored spec to plan against is a 409, which reaches the caller as an
 * `ApiError` carrying the API's own sentence. */
export function previewJobPlans(jobId: string): Promise<PlanPreview> {
  return request<PlanPreview>("/v1alpha1/jobs/preview-plans", {
    method: "POST",
    body: JSON.stringify({ job_id: jobId }),
  });
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

/** `GET /v1alpha1/sandbox-sessions/{id}` — owner-scoped, 404 for a session
 * that is not yours, same doctrine as `getJob`.
 *
 * The response types live in `lib/sandbox-session.ts` rather than here, which
 * is the one exception to this file's "single source of the API's response
 * types" rule and has a concrete reason: the PUBLIC share page renders those
 * same shapes on the server for a visitor with no account, and this module
 * imports the Supabase auth client. Defining them there and importing them
 * here keeps one definition without dragging an auth client into a page that
 * must work without one. */
export function getSandboxSession(sessionId: string): Promise<SandboxSession> {
  return request<SandboxSession>(
    `/v1alpha1/sandbox-sessions/${encodeURIComponent(sessionId)}`
  );
}

/** `GET /v1alpha1/sandbox-sessions/{id}/events` — owner-scoped.
 *
 * Returns the whole ledger every time. There is no `since` cursor here (the
 * job ledger has one) and none is needed: a session's event count is tens,
 * not thousands, and `normaliseEvents` in `lib/sandbox-session.ts` dedupes on
 * `sequence` anyway — so a caller merging overlapping polls gets the same
 * answer whether or not the API ever grows one. */
export function listSandboxEvents(sessionId: string): Promise<SandboxEvent[]> {
  return request<SandboxEvent[]>(
    `/v1alpha1/sandbox-sessions/${encodeURIComponent(sessionId)}/events`
  );
}

/** `GET /v1alpha1/jobs/{id}/artifacts` — what this job actually wrote.
 *
 * Owner/viewer-scoped exactly like `getJob`, so an unknown or someone
 * else's job answers 404 (`NotFound`) rather than a distinguishing 403. An
 * API deployed before this route existed answers 404 too, and that is not a
 * distinction this client can make — which is precisely why the caller must
 * render a failed read as "could not read", never as "no artifacts". */
export function listJobArtifacts(jobId: string): Promise<JobArtifactListing> {
  return request<JobArtifactListing>(
    `/v1alpha1/jobs/${encodeURIComponent(jobId)}/artifacts`
  );
}

/** `{jobId}/{key}` as a path, each segment encoded on its own so the `/`
 * separators inside a key stay separators — both artifact routes take the key
 * as `{key:path}` and expect exactly that.
 *
 * Not exported. It used to be, as `jobArtifactDownloadUrl`, and every caller
 * put the string it returned into an `<a href download>`. That is precisely
 * the bug this pair of functions replaces: the route is authenticated, a
 * NAVIGATION sends no `Authorization` header, and every download answered
 * 401. Keeping the URL builder public would keep that mistake one import
 * away. */
function jobArtifactPath(jobId: string, key: string, route: string): string {
  const path = key.split("/").map(encodeURIComponent).join("/");
  return `/v1alpha1/jobs/${encodeURIComponent(jobId)}/${route}/${path}`;
}

/** `GET /v1alpha1/jobs/{id}/artifact-url/{key}` — where ONE artifact's bytes
 * should be fetched from.
 *
 * `storage: "oss"` comes with a `url`: a presigned Alibaba OSS grant, good
 * for one object and expiring on its own, which the browser may simply
 * navigate to. That navigation is the point — it needs no header of ours (so
 * no 401), it crosses no CORS boundary the browser will refuse (a navigation
 * is not a fetch), and it never puts a multi-gigabyte checkpoint through this
 * tab's memory.
 *
 * `storage: "coordinator"` comes with a null `url`, and that is an ORDINARY
 * answer, not a failure. Four situations reach it and the caller handles them
 * identically, by fetching the bytes through the API: no OSS configured (the
 * deployment default), the job never mirrored, the bucket unwell, or — the
 * one that has nothing to do with configuration — **this key is absent from
 * the job's manifest.** A task that failed produced no accepted output, so
 * its `stderr.txt` legitimately exists, is legitimately readable, and has no
 * OSS copy, while the job as a whole is stamped mirrored. Anything that reads
 * the job-level `storage` as the answer for every key breaks on exactly the
 * file somebody opens after a failure. */
export interface JobArtifactUrl {
  storage: ArtifactStorage | string;
  /** Null whenever these bytes must come through the API instead. */
  url: string | null;
}

export function getJobArtifactUrl(
  jobId: string,
  key: string
): Promise<JobArtifactUrl> {
  return request<JobArtifactUrl>(
    jobArtifactPath(jobId, key, "artifact-url")
  );
}

/** One artifact's bytes, through the API, with the bearer header.
 *
 * The path for anything not mirrored. It holds the whole file in memory as a
 * Blob, which is acceptable here and only here: these are the files still on
 * the coordinator's own 5 GB disk, so the size is bounded by that disk rather
 * than by what a user might upload. Mirrored artifacts — the ~100 MB–1 GB
 * model weights this product exists to produce — never come this way; they
 * are a navigation to OSS, and the browser streams them to disk itself.
 *
 * `send`, not a hand-rolled fetch: a 401 here must become `NotAuthenticated`
 * and send the person to sign in, exactly as it does on every other call,
 * rather than being saved to their downloads folder as a file full of JSON. */
export function fetchJobArtifactBlob(
  jobId: string,
  key: string
): Promise<Blob> {
  return send(jobArtifactPath(jobId, key, "artifacts")).then((res) =>
    res.blob()
  );
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

/** What the admin queue's approve/decline routes answer with. `emailed` is
 * false when no provider is configured, when the account has no address, or
 * when the provider refused — the caller must not assume a message went
 * out. */
export interface AccessDecision {
  user_id: string;
  status: string;
  emailed: boolean;
}

/** `POST /v1alpha1/admin/access-requests/{userId}/approve` — admin only.
 * 404s (via `NotFound`) when there was no pending request for this user;
 * `NotFound` here means "nothing to approve", not "unknown user". */
export function approveAccessRequest(userId: string): Promise<AccessDecision> {
  return request<AccessDecision>(
    `/v1alpha1/admin/access-requests/${encodeURIComponent(userId)}/approve`,
    { method: "POST" }
  );
}

/** The decline counterpart of `approveAccessRequest` — same route shape,
 * same 404-means-nothing-pending doctrine. */
export function declineAccessRequest(userId: string): Promise<AccessDecision> {
  return request<AccessDecision>(
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

// -- marketplace -----------------------------------------------------------
//
// The seven routes the 2026-08-12 plan put over marketplace.py / prices.py.
// Every money figure is MILLICREDITS as an integer (1 ZC = 1000) — the API
// settles in integers so it cannot round, and formatting for display lives
// in lib/market-credits.ts where a test can reach it. Fixed-parity display
// strings arrive from the API alongside source-denominated settlement fields;
// the browser does not calculate the policy value itself.

export interface CreditsBalance {
  spendable_zc: number;
  held_zc: number;
  /** Display strings supplied by the API alongside integer balances. The
   * console never re-prices the wallet locally: these are the values the
   * current API says its ZC amounts represent. */
  usd_per_zc: string;
  spendable_usd: string;
  held_usd: string;
  /** Lifetime sums read out of the ledger. True zeros for a fresh account
   * — a lifetime sum of zero is a fact, unlike an unmeasured metric; the
   * tiles are labelled "lifetime" so 0 reads as "nothing yet". */
  lifetime: {
    earned_zc: number;
    spent_zc: number;
    granted_zc: number;
    refunded_zc: number;
  };
}

export function getCredits(): Promise<CreditsBalance> {
  return request<CreditsBalance>("/v1alpha1/credits");
}

/** A requester's own credit-request record. Amounts remain integer
 * millicredits even after approval so the UI can distinguish what was
 * requested from what was granted without rounding either. */
export interface CreditRequest {
  id: string;
  user_id: string;
  requested_zc: number;
  approved_zc: number | null;
  purpose: string;
  status: "pending" | "approved" | "declined";
  requested_at: string;
  decided_at: string | null;
  decided_by: string | null;
}

/** `GET /v1alpha1/admin/credit-requests` adds the requester profile and
 * their live account balances to the base request record. */
export interface AdminCreditRequest extends CreditRequest {
  display_name: string | null;
  first_name: string | null;
  last_name: string | null;
  company_name: string | null;
  role: string | null;
  team_size: string | null;
  spendable_zc: number;
  escrow_zc: number;
}

export function listCreditRequests(): Promise<CreditRequest[]> {
  return request<CreditRequest[]>("/v1alpha1/credit-requests");
}

export function submitCreditRequest(body: {
  requested_zc: number;
  purpose: string;
}): Promise<CreditRequest> {
  return request<CreditRequest>("/v1alpha1/credit-requests", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listAdminCreditRequests(
  status: "pending" | "approved" | "declined" = "pending"
): Promise<AdminCreditRequest[]> {
  return request<AdminCreditRequest[]>(
    `/v1alpha1/admin/credit-requests?status=${encodeURIComponent(status)}`
  );
}

export function approveCreditRequest(
  requestId: string,
  approvedZc: number
): Promise<CreditRequest> {
  return request<CreditRequest>(
    `/v1alpha1/admin/credit-requests/${encodeURIComponent(requestId)}/approve`,
    { method: "POST", body: JSON.stringify({ approved_zc: approvedZc }) }
  );
}

export function declineCreditRequest(requestId: string): Promise<CreditRequest> {
  return request<CreditRequest>(
    `/v1alpha1/admin/credit-requests/${encodeURIComponent(requestId)}/decline`,
    { method: "POST" }
  );
}

/** One leg of one ledger movement. `mine` says whether the account belongs
 * to the caller; the counterparty leg travels with the viewer's own because
 * a movement without its counterparty is a balance change without a cause. */
export interface LedgerLeg {
  kind: "spendable" | "escrow" | string;
  delta_zc: number;
  mine: boolean;
}

export interface LedgerMovement {
  cursor: number;
  created_at: string;
  /** The viewer's own leg's reason; a settlement's other leg carries the
   * paired reason, so the display copy lives in lib/market-credits.ts. */
  reason: string;
  ref_type: string | null;
  ref_id: string | null;
  legs: LedgerLeg[];
}

export interface LedgerPage {
  movements: LedgerMovement[];
  /** The cursor for the next page; null when this page was not full. */
  next_before: number | null;
}

export function getCreditsLedger(opts?: {
  limit?: number;
  before?: number;
}): Promise<LedgerPage> {
  const params = new URLSearchParams();
  if (opts?.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts?.before !== undefined) params.set("before", String(opts.before));
  const query = params.toString();
  return request<LedgerPage>(
    `/v1alpha1/credits/ledger${query ? `?${query}` : ""}`
  );
}

/** One open ask in the market book. `acceptance_rate` is null for an
 * unproven host — "not asked yet", never 0 — and `resolved_n` is the count
 * behind the rate, null on the same condition. */
export interface MarketAsk {
  id: string;
  machine_id: string;
  host_id: string;
  capability_class: string;
  /** The host's machine, named and spec'd from what the agent reported. */
  machine_name: string | null;
  gpu_label: string | null;
  ask_zc_per_hour: number;
  ask_usd_per_hour: string;
  donated: boolean;
  price_label: string;
  usd_equivalent_label: string;
  max_concurrent_tasks: number;
  acceptance_rate: number | null;
  resolved_n: number | null;
  /** ask/rate, the per-accepted-hour price. Null for unproven (no rate)
   * and for rate 0 (unclearable) — both render as words, not a number. */
  effective_zc_per_hour: number | null;
}

/** What the market says about a machine you might list — the class, the
 * book in that class, and your own record. The "suggestion" is the market
 * itself (best/median/reference), never a model. */
export interface MarketHint {
  capability_class: string | null;
  unclassifiable: string | null;
  book: {
    open_asks: number;
    best_ask_zc: number | null;
    median_ask_zc: number | null;
    reference_zc_per_hour: number;
  } | null;
  your_record: { acceptance_rate: number; resolved_n: number } | null;
}

export function getMarketHint(machineId: string): Promise<MarketHint> {
  return request<MarketHint>(
    `/v1alpha1/machines/${encodeURIComponent(machineId)}/market-hint`
  );
}

/** The host's own listing row, verbatim from the API plus the two display
 * fields the server computes from the ask. */
export interface MarketListing {
  id: string;
  machine_id: string;
  capability_class: string;
  ask_zc_per_hour: number;
  ask_usd_per_hour: string;
  max_concurrent_tasks: number;
  state: string;
  donated: boolean;
  price_label: string;
  usd_equivalent_label: string;
  created_at: string;
}

export interface MarketListings {
  asks: MarketAsk[];
  mine: MarketListing[];
}

export function getMarketListings(): Promise<MarketListings> {
  return request<MarketListings>("/v1alpha1/market/listings");
}

export function createMarketListing(body: {
  machine_id: string;
  ask_zc_per_hour: number;
  max_concurrent_tasks?: number;
}): Promise<MarketListing> {
  return request<MarketListing>("/v1alpha1/market/listings", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function withdrawMarketListing(
  listingId: string
): Promise<{ withdrawn: boolean }> {
  return request<{ withdrawn: boolean }>(
    `/v1alpha1/market/listings/${encodeURIComponent(listingId)}`,
    { method: "DELETE" }
  );
}

/** A priced entitlement, verbatim from the API. `state` is the vocabulary
 * granted/claimed/settled/refunded/expired — the console renders it as-is,
 * because a match is an entitlement, not an assignment. */
export interface MarketMatch {
  id: string;
  bid_id: string;
  listing_id: string;
  machine_id: string;
  buyer_id: string;
  host_id: string;
  job_id: string;
  capability_class: string;
  agreed_zc_per_hour: number;
  tasks: number;
  est_task_seconds: number;
  state: string;
  granted_at: string;
  claimed_at: string | null;
  settled_at: string | null;
  held_zc: number;
  charged_zc: number;
  refunded_zc: number;
}

export interface MarketMatches {
  as_buyer: MarketMatch[];
  as_host: MarketMatch[];
}

export function getMarketMatches(): Promise<MarketMatches> {
  return request<MarketMatches>("/v1alpha1/market/matches");
}

/** One external quote as `prices.render` shapes it: the vendor's digits as
 * a string (a float would round them on the way to the page), when it was
 * OBSERVED, who observed it, and the staleness verdict with its age. */
export interface PriceQuote {
  provider: string;
  sku: string;
  region: string;
  tier: string | null;
  currency: string;
  amount: string;
  unit: string;
  attrs: Record<string, unknown>;
  captured_at: string;
  age_seconds: number;
  stale: boolean;
  max_age_seconds: number;
  source: string;
  observed_by: string | null;
  zc_equivalent_amount: string | null;
}

/** A venue with no quote, shaped like one so the view cannot skip it.
 * `amount` null, state "not observed" — never 0. */
export interface PriceUnpriced {
  provider: string;
  state: string;
  amount: null;
  currency: null;
  unit: null;
  captured_at: null;
  age_seconds: null;
  stale: null;
  source: null;
  observed_by: null;
  zc_equivalent_amount: null;
}

/** One observation of a class's book, recorded as the book moved. The
 * sparkline and the 24h delta are drawn from these and nothing else — no
 * interpolation between points. */
export interface PricePoint {
  at: string;
  best_ask_zc: number | null;
  best_ask_usd: string | null;
  open_asks: number;
}

/** A row on the compute board: one capability class as a ticker. `last`
 * null where the book is empty (rendered `—`, never 0); `change_zc` null
 * when there is no observation older than 24 h to compare against
 * ("no history", not 0). */
export interface ZcRung {
  capability_class: string;
  reference_zc_per_hour: number;
  reference_usd_per_hour: string;
  best_ask_zc: number | null;
  best_ask_usd: string | null;
  change_zc: number | null;
  depth: number;
  history: PricePoint[];
}

export interface PricesView {
  quotes: PriceQuote[];
  unpriced: PriceUnpriced[];
  zc: ZcRung[];
  /** Real counts over the whole book, for the board's header strip. */
  board: {
    open_asks_total: number;
    live_classes: number;
    observations_24h: number;
  };
}

export function getPrices(): Promise<PricesView> {
  return request<PricesView>("/v1alpha1/prices");
}
