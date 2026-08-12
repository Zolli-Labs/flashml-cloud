// The Alibaba FC Sandbox evaluation lifecycle, derived from its own ledger.
//
// The API returns two things: a session row (current state) and an
// append-only event stream (what was observed, in order). **Everything this
// module reports is derived from the events**, not from the row's `state`
// column, and the difference is the whole point.
//
//   - The row says HIBERNATED. It cannot say how long the pause took, when
//     the wake happened, or whether the marker still matched afterwards.
//     Only the ledger can, and only the ledger can prove the order.
//   - The row is a single mutable value. A session that went
//     ACTIVE -> PREPARED -> HIBERNATED -> RESUMING -> ACTIVE visits ACTIVE
//     twice, and the second visit is the entire hibernation claim. Reading
//     the column would collapse both into one word.
//
// Events arrive by polling, so they arrive out of order and more than once.
// `normaliseEvents` is the only door into this module's derivations: dedupe
// on `sequence` (the API allocates it gaplessly from 1 under the session
// row's lock, so it is a real total order, unlike a timestamp — this
// lifecycle produces sub-millisecond pairs that tie), then sort ascending.
//
// THE HOUSE RULE, which every function here obeys: a metric that was not
// observed reports `NOT_OBSERVED`. Never 0, never "—", never a plausible
// default. This is `metrics.py`'s rule ("0.0 means it failed everything,
// None means it has not been asked yet") applied to a page whose only job is
// to survive a judge asking "how do you know?". A fabricated 0 here would be
// a claim about Alibaba's infrastructure that we did not measure.
//
// Second rule, specific to this surface: a value we DID observe but choose
// not to disclose on the public page is `withheld`, never `not observed`.
// Collapsing the two would be the same lie in the other direction.
//
// No React, no I/O, no clock of its own — `now` is an argument. Everything
// is a pure function so `vitest.config.ts` (which collects only `*.test.ts`)
// can reach all of it; a decision that lives in a `.tsx` gets no coverage at
// all. Same reasoning as `lib/platform-metrics.ts` and `lib/job-activity.ts`.

// ---------------------------------------------------------------------------
// Wire types
//
// These live here rather than in `lib/cloud-api.ts` — which is otherwise the
// single source of the API's response shapes — for one concrete reason: the
// PUBLIC share page (`app/share/[token]/page.tsx`) renders these types on the
// server for a visitor with no account, and `cloud-api.ts` imports the
// Supabase auth client at module scope. A page that must work with no session
// has no business pulling an auth client into its module graph.
// `cloud-api.ts` imports the types back from here, so there is still exactly
// one definition.
// ---------------------------------------------------------------------------

/** The nine states of migration 0014's check constraint, in lifecycle order.
 * The set is fixed in the database, so a union is safe here in a way it is
 * not for `type` below. */
export type SandboxSessionState =
  | "REQUESTED"
  | "ACTIVE"
  | "PREPARED"
  | "HIBERNATED"
  | "RESUMING"
  | "EVALUATING"
  | "SUCCEEDED"
  | "FAILED"
  | "TERMINATED";

/** Who observed an event. `fc` is the provider's own answer, `runtime` is the
 * coordinator or the agent, `controller` is our API watching its own calls. A
 * latency the provider reported and one we timed around the provider are
 * different claims, and this view has to be able to say which it is showing. */
export type SandboxEventSource = "controller" | "fc" | "runtime";

export interface SandboxSession {
  id: string;
  state: SandboxSessionState;
  /** `"alibaba-fc-sandbox"` today. Typed as a plain string for the reason
   * `JobEvent.type` is: the value comes from a column default upstream and a
   * second provider must not become a build break in the console. */
  provider: string;
  region: string;
  template: string;
  /** Full id for the owner; the API's public view sends the last 6 chars, or
   * omits it entirely. Either way this module never renders more than a
   * suffix — see `SESSION_SHARE_COLUMNS` upstream. */
  external_sandbox_id: string | null;
  /** Full hash for the owner; first 12 chars on the public view. */
  marker_sha256: string | null;
  training_job_id: string;
  evaluation_job_id: string | null;
  created_at: string;
  terminated_at: string | null;
  error_code: string | null;
  /** Sanitized upstream (`sandbox_sessions.redact`). Still never rendered on
   * the public view — `error_code` is the only failure detail a stranger
   * gets. */
  error_message: string | null;
}

export interface SandboxEvent {
  sequence: number;
  /** Deliberately a plain string. The vocabulary is written by the API and
   * the agent and grows without this repo; an unrecognised type is shown
   * verbatim in the ledger rather than dropped. See `KNOWN_EVENT_TYPES`. */
  type: string;
  source: SandboxEventSource;
  observed_at: string;
  /** Milliseconds the observer timed around its own call. Null when nobody
   * timed it — which is a `not observed`, not a zero. */
  latency_ms: number | null;
  data: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Copy that tests pin
// ---------------------------------------------------------------------------

/** The words an unobserved metric renders as. Exported so the component, the
 * public page and the tests agree on the exact string — a UI that drifted to
 * "—" would be exactly the regression this rule exists to prevent. */
export const NOT_OBSERVED = "not observed";

/** Observed, deliberately not disclosed. Distinct from NOT_OBSERVED on
 * purpose: telling a judge "not observed" about a fact we hold and are
 * withholding is a lie in the flattering direction. */
export const WITHHELD = "withheld from the public view";

/** Non-negotiable, on every render of this view in both modes.
 *
 * FlashRuntime survives machines that never come back; FC suspends a machine
 * and returns it whole. Two guarantees at two layers. A judge who reads this
 * screen as "the sandbox rescued the training job" has been misled, and this
 * sentence is what stops that reading. */
export const BOUNDARY_NOTE =
  "Training retry and sandbox hibernation are separate guarantees. " +
  "Hibernation suspended this evaluator; it did not rescue the training job — " +
  "that recovery is FlashRuntime's lease and checkpoint machinery, on other machines.";

/** Hibernation is not free, and "active compute avoided" must not be read as
 * "cost avoided": a deep-hibernated sandbox still bills a snapshot. Rendered
 * next to the number rather than in a footnote. */
export const HIBERNATION_COST_NOTE =
  "Active compute only. A hibernated sandbox still bills its snapshot, so this is " +
  "compute the evaluator did not spend — not a bill it did not receive.";

// ---------------------------------------------------------------------------
// Event vocabulary
//
// Two families reach the ledger for the same transition, and both have to be
// understood or half the lifecycle disappears:
//
//   `state.<lowercase>`  the default `transition()` writes when the caller
//                        supplies no observation of its own.
//   provider aliases     `sandbox.created`, `sandbox.paused`, … — a caller's
//                        own observation, which REPLACES the default rather
//                        than accompanying it. A session paused with a
//                        `sandbox.paused` observation has no
//                        `state.hibernated` event at all.
//
// Anything not in here is still kept, still counted, still shown in the raw
// ledger. It just does not move the timeline.
// ---------------------------------------------------------------------------

/** Provider/controller observation types that evidence entry into a state. */
const STATE_ALIASES: Readonly<Record<string, SandboxSessionState>> = {
  "session.requested": "REQUESTED",
  "sandbox.created": "ACTIVE",
  "sandbox.connected": "ACTIVE",
  "sandbox.resumed": "ACTIVE",
  "sandbox.prepared": "PREPARED",
  "sandbox.paused": "HIBERNATED",
  "sandbox.hibernated": "HIBERNATED",
  "sandbox.resuming": "RESUMING",
  "sandbox.killed": "TERMINATED",
  "sandbox.destroyed": "TERMINATED",
  "sandbox.terminated": "TERMINATED",
};

const STATES = new Set<string>([
  "REQUESTED",
  "ACTIVE",
  "PREPARED",
  "HIBERNATED",
  "RESUMING",
  "EVALUATING",
  "SUCCEEDED",
  "FAILED",
  "TERMINATED",
]);

/** The external event (spec D6): a model artifact appearing in OSS. Not a
 * timer and not a poll of our own database — a provider-neutral object store
 * a judge can check independently. Several spellings are accepted because the
 * orchestrator that emits it is being written alongside this view; anything
 * under a `trigger.` prefix also counts. */
const TRIGGER_TYPES = new Set([
  "oss.model_observed",
  "oss.object_observed",
  "oss.artifact_observed",
  "model.observed",
  "trigger.observed",
  "trigger.model_artifact",
]);

/** Post-wake marker verification. `worker.verified` / `worker.unhealthy` are
 * emitted by `sandbox_bootstrap.verify_worker` and carry
 * `data.marker_matches`. */
const MARKER_VERIFY_TYPES = new Set([
  "worker.verified",
  "worker.unhealthy",
  "marker.verified",
  "marker.mismatch",
  "worker.marker.failed",
]);

/** Types that, on their own, prove the marker did NOT survive. */
const MARKER_FAILED_TYPES = new Set(["marker.mismatch", "worker.marker.failed"]);

/** The evaluation commit being accepted. Falls back to the state machine:
 * SUCCEEDED is reachable only from EVALUATING, so entering it is itself
 * evidence that a commit was accepted. */
const ACCEPTED_TYPES = new Set([
  "evaluation.accepted",
  "task.commit.accepted",
  "runtime.commit.accepted",
]);

/** Somebody asked for the sandbox to go away. */
const CLEANUP_REQUESTED_TYPES = new Set([
  "cleanup.requested",
  "sandbox.kill.requested",
  "session.cleanup",
]);

/** The provider answered. This is the difference between "we recorded
 * TERMINATED" and "the sandbox is gone", and it is the one the voucher
 * cares about. */
const CLEANUP_OBSERVED_TYPES = new Set([
  "sandbox.killed",
  "sandbox.destroyed",
  "sandbox.terminated",
]);

const CREDENTIAL_REVOKED_TYPES = new Set([
  "worker.credential.deleted",
  "credential.revoked",
  "machine.token.revoked",
]);

/** Every type this view keys a claim off. Exported so the API side can be
 * checked against it — a renamed event type silently empties a lifecycle row,
 * and this is the list that makes that reviewable in one place. */
export const KNOWN_EVENT_TYPES: readonly string[] = [
  ...Object.keys(STATE_ALIASES),
  ...[...STATES].map((s) => `state.${s.toLowerCase()}`),
  ...TRIGGER_TYPES,
  ...MARKER_VERIFY_TYPES,
  ...ACCEPTED_TYPES,
  ...CLEANUP_REQUESTED_TYPES,
  ...CLEANUP_OBSERVED_TYPES,
  ...CREDENTIAL_REVOKED_TYPES,
].sort();

// ---------------------------------------------------------------------------
// Small readers. `data` is `Record<string, unknown>` off the wire; nothing
// here trusts a field's type.
// ---------------------------------------------------------------------------

function str(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

function bool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

function ms(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

// ---------------------------------------------------------------------------
// Normalisation
// ---------------------------------------------------------------------------

/**
 * One ordered, duplicate-free ledger out of however many overlapping polling
 * responses.
 *
 * **Dedupe on `sequence`, first copy wins.** The table is append-only and a
 * row never changes, so a second copy of a sequence carries no information the
 * first did not; keeping the first makes the merge independent of the order
 * the responses happened to arrive in, which is the property a polling UI
 * needs. A duplicate sequence with *different* content is an upstream bug —
 * the unique constraint on `(session_id, sequence)` makes it impossible — and
 * this silently prefers the copy it already had rather than flickering.
 *
 * **Sort ascending on `sequence`, never on `observed_at`.** The API allocates
 * sequences gaplessly from 1 under the session row's lock; timestamps tie on
 * the sub-millisecond pairs this lifecycle actually produces, and a tie in the
 * wrong direction turns "woke, then verified the marker" into "verified the
 * marker, then woke".
 *
 * An event with an unusable `sequence` (not a finite number) is kept — losing
 * a ledger entry is worse than mis-ordering one — and sorted after everything
 * numbered, in arrival order.
 */
export function normaliseEvents(
  events: readonly SandboxEvent[] | null | undefined
): SandboxEvent[] {
  if (!events || events.length === 0) return [];

  const bySequence = new Map<number, SandboxEvent>();
  const unnumbered: SandboxEvent[] = [];

  for (const event of events) {
    if (!event || typeof event !== "object") continue;
    const sequence = event.sequence;
    if (typeof sequence !== "number" || !Number.isFinite(sequence)) {
      unnumbered.push(event);
      continue;
    }
    if (!bySequence.has(sequence)) bySequence.set(sequence, event);
  }

  const numbered = [...bySequence.values()].sort(
    (a, b) => a.sequence - b.sequence
  );
  return [...numbered, ...unnumbered];
}

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------

/** One observed entry INTO a state, with the event that evidenced it. */
export interface LifecycleEntry {
  state: SandboxSessionState;
  /** Epoch ms of `observed_at`, or null when the timestamp was unparseable. */
  at: number | null;
  sequence: number;
  /** The event type that evidenced this entry — shown in the UI so every row
   * of the timeline names its own proof. */
  type: string;
  source: SandboxEventSource;
  /** What the observer timed around its own call, if anything. */
  latencyMs: number | null;
}

/** Which state, if any, an event evidences entry into. */
function stateOf(type: string): SandboxSessionState | null {
  const alias = STATE_ALIASES[type];
  if (alias) return alias;
  if (type.startsWith("state.")) {
    const name = type.slice("state.".length).toUpperCase();
    if (STATES.has(name)) return name as SandboxSessionState;
  }
  return null;
}

/**
 * The lifecycle as the ledger records it: every entry into a state, in
 * sequence order, including the SECOND entry into ACTIVE that the session
 * row can never show.
 *
 * Consecutive entries into the same state collapse — a controller that
 * reconciles after a lost connection appends what it observed, which may be
 * the state the session is already in, and drawing that as a second visit
 * would invent a transition. A repeat that is separated by some other state
 * is kept: that one is a real second visit, and it is the whole hibernation
 * claim.
 */
export function deriveTimeline(
  events: readonly SandboxEvent[] | null | undefined
): LifecycleEntry[] {
  const entries: LifecycleEntry[] = [];
  for (const event of normaliseEvents(events)) {
    const state = stateOf(event.type);
    if (!state) continue;
    if (entries.length > 0 && entries[entries.length - 1].state === state) {
      continue;
    }
    entries.push({
      state,
      at: ms(event.observed_at),
      sequence: event.sequence,
      type: event.type,
      source: event.source,
      latencyMs:
        typeof event.latency_ms === "number" && Number.isFinite(event.latency_ms)
          ? event.latency_ms
          : null,
    });
  }
  return entries;
}

// ---------------------------------------------------------------------------
// Measurements
// ---------------------------------------------------------------------------

/**
 * How a number was arrived at, which the UI must show rather than imply.
 *
 * `measured` — the observer put a clock around its own call and reported
 *   `latency_ms`. This is what "wake took 946 ms" means, and it is the only
 *   basis on which that sentence may be said out loud.
 * `estimated` — the difference between two observed instants. Honest, and
 *   strictly weaker: it includes whatever queueing, scheduling and polling
 *   sat between the two events. A judge told "946 ms measured" and shown an
 *   estimate has been misled about the strength of the evidence, not the
 *   value.
 */
export type MeasurementBasis = "measured" | "estimated";

export interface Measurement {
  /** The field the UI branches on. False means `display` is a reason, not a
   * value — never a formatted zero. */
  observed: boolean;
  ms: number | null;
  basis: MeasurementBasis | null;
  /** Either the formatted duration or `NOT_OBSERVED`. Always one or the
   * other; never a bare fallback that leaves the caller guessing which. */
  display: string;
  /** The interval has not closed yet — `display` is a floor that will grow.
   * Only ever true for a duration between an observed start and `now`. */
  ongoing: boolean;
}

const UNOBSERVED: Measurement = {
  observed: false,
  ms: null,
  basis: null,
  display: NOT_OBSERVED,
  ongoing: false,
};

function measurement(
  msValue: number | null,
  basis: MeasurementBasis,
  ongoing = false
): Measurement {
  if (msValue === null || !Number.isFinite(msValue) || msValue < 0) {
    return UNOBSERVED;
  }
  return {
    observed: true,
    ms: msValue,
    basis,
    display: formatDuration(msValue),
    ongoing,
  };
}

/**
 * Milliseconds into something a judge reads at a glance, at the precision the
 * measurement actually has.
 *
 * `946 ms` · `2.64 s` · `12.3 s` · `6m 12s` · `1h 4m`.
 *
 * Sub-second values keep whole milliseconds because that is the headline
 * number of this whole feature (a measured ~1 s wake); an hours-long
 * hibernation loses the seconds because they are noise beside it.
 */
export function formatDuration(value: number): string {
  const v = Math.max(0, value);
  if (v < 1000) return `${Math.round(v)} ms`;
  // Rounded on the integer before the divide. `(2635 / 1000).toFixed(2)` is
  // "2.63": 2.635 has no exact binary representation and lands just below the
  // midpoint, so `toFixed` rounds down. A latency that reads 10 ms short of
  // what was measured is a small lie on a screen whose entire subject is
  // measured latencies.
  if (v < 10_000) return `${(Math.round(v / 10) / 100).toFixed(2)} s`;
  if (v < 60_000) return `${(Math.round(v / 100) / 10).toFixed(1)} s`;

  const totalSeconds = Math.round(v / 1000);
  if (totalSeconds < 3600) {
    const m = Math.floor(totalSeconds / 60);
    const s = totalSeconds % 60;
    return s > 0 ? `${m}m ${s}s` : `${m}m`;
  }
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.round((totalSeconds % 3600) / 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

/**
 * A UTC wall-clock stamp, `14:32:07 UTC`.
 *
 * UTC and hand-formatted rather than `toLocaleTimeString()` on purpose. This
 * component server-renders on the public share page and then hydrates in a
 * browser in some other timezone; a locale-formatted time differs between the
 * two and React tears the tree down over it. It is also the better evidence
 * format — a judge comparing this row to an Alibaba console log is comparing
 * UTC to UTC.
 */
export function formatClock(at: number | null): string {
  if (at === null) return NOT_OBSERVED;
  const d = new Date(at);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(
    d.getUTCSeconds()
  )} UTC`;
}

// ---------------------------------------------------------------------------
// Identifiers — every one of them redacted at the source
// ---------------------------------------------------------------------------

/**
 * What may be shown for one identifier, decided here rather than in the
 * component.
 *
 * The redaction lives in the summary, not in JSX, so that a component cannot
 * leak a full id by rendering the wrong field: on the public path the full
 * value never enters the object the component is handed. A test can then
 * assert the property on the summary AND on the markup, and both are the same
 * guarantee.
 */
export interface Identifier {
  /** `shown` — a suffix or prefix, safe everywhere.
   *  `withheld` — we hold it (or the API does) and are not disclosing it.
   *  `not-observed` — nobody has seen this value yet. */
  state: "shown" | "withheld" | "not-observed";
  display: string;
}

const NOT_OBSERVED_ID: Identifier = { state: "not-observed", display: NOT_OBSERVED };
const WITHHELD_ID: Identifier = { state: "withheld", display: WITHHELD };

/** Last `n` characters, with a leading ellipsis so nobody mistakes it for the
 * whole value. Idempotent: the public API already truncates to 6, and slicing
 * 6 characters off a 6-character string is a no-op. Defence in depth — this
 * module never assumes the API redacted anything. */
function suffix(value: string, n: number): string {
  return value.length <= n ? `…${value}` : `…${value.slice(-n)}`;
}

function prefixHash(value: string, n: number): string {
  return value.length <= n ? value : `${value.slice(0, n)}…`;
}

/**
 * The template, as much of it as identifies the image without being
 * deployment configuration.
 *
 * A template carrying a digest (`name@sha256:abc…`) shows the digest's tail,
 * which is what pins the environment and is exactly the thing a judge wants
 * to see survive a hibernation. A plain template name is shown as-is: it is
 * in the API's own public column set, it names no infrastructure, and hiding
 * it would leave the row saying nothing.
 */
export function templateDisplay(
  template: string | null
): { label: string; display: string } | null {
  const value = str(template);
  if (!value) return null;
  const at = value.lastIndexOf("@");
  if (at > 0 && at < value.length - 1) {
    return { label: "template digest", display: suffix(value.slice(at + 1), 12) };
  }
  if (/^[0-9a-f]{32,}$/i.test(value)) {
    return { label: "template digest", display: suffix(value, 12) };
  }
  return { label: "template", display: value };
}

// ---------------------------------------------------------------------------
// The public trust boundary
// ---------------------------------------------------------------------------

/** `data` keys the public view may keep, and how. An ALLOWLIST, because the
 * alternative — dropping the keys we can currently think of — silently
 * publishes whatever the orchestrator adds next. Two entries earn their
 * place: the marker verdict, without which the continuity claim disappears,
 * and the object name, reduced to its basename because an OSS key is
 * `jobs/<training job id>/model.pt`. */
const PUBLIC_DATA_KEYS = new Set(["key", "object", "name"]);

/**
 * Narrow a session and its ledger to what a stranger may hold — **before**
 * either object is handed to a component.
 *
 * Not a display concern. `SandboxLifecycle` is a client component, so every
 * prop it receives is serialised into the RSC payload embedded in the page's
 * own HTML: a field that JSX never renders is still in view source. Redacting
 * at render time would therefore protect nothing. This runs at the boundary,
 * on the server, and what it returns is all the browser ever gets.
 *
 * The API's public projection already drops the worst of it
 * (`SESSION_SHARE_COLUMNS`: no owner, pool, machine or sandbox id). This does
 * not trust that — it re-narrows everything, and it narrows two things the
 * projection deliberately still sends in full, because the API needs them and
 * a reader does not: the training and evaluation job ids.
 */
export function redactForPublic(
  session: SandboxSession,
  events: readonly SandboxEvent[] | null | undefined
): { session: SandboxSession; events: SandboxEvent[] } {
  const short = (value: string | null): string | null =>
    value ? suffix(value, 6) : null;

  return {
    session: {
      ...session,
      // The route needed it to read the events; the browser does not need it
      // at all.
      id: suffix(session.id, 6),
      external_sandbox_id: short(session.external_sandbox_id),
      marker_sha256: session.marker_sha256
        ? session.marker_sha256.slice(0, 12)
        : null,
      training_job_id: suffix(session.training_job_id, 6),
      evaluation_job_id: short(session.evaluation_job_id),
      // Sanitized upstream is not the same as safe to publish. The code is
      // the whole failure story a stranger gets.
      error_message: null,
    },
    events: normaliseEvents(events).map((event) => ({
      sequence: event.sequence,
      type: event.type,
      source: event.source,
      observed_at: event.observed_at,
      latency_ms: event.latency_ms,
      data: publicData(event.data),
    })),
  };
}

function publicData(data: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (!data || typeof data !== "object") return out;

  // The one verdict the continuity claim rests on. Kept as a boolean, so
  // there is nothing in it to leak.
  const matches = bool(data.marker_matches);
  if (matches !== null) out.marker_matches = matches;

  for (const key of PUBLIC_DATA_KEYS) {
    const value = str(data[key]);
    if (value) {
      out[key] = value.split("/").pop() ?? value;
      break;
    }
  }
  return out;
}

// ---------------------------------------------------------------------------
// The summary
// ---------------------------------------------------------------------------

/** Who is looking. `public` is a stranger holding a share link and nothing
 * else; every identifier is narrowed before it reaches the component. */
export type SandboxVisibility = "owner" | "public";

/** The judge's eight words, in order, as the timeline's own labels. D-2 asks
 * for exactly these to be findable on one screen in under fifteen seconds, so
 * they are the row headings rather than prose somewhere near them. */
export type SandboxStepId =
  | "create"
  | "prepare"
  | "hibernate"
  | "trigger"
  | "wake"
  | "evaluate"
  | "accepted"
  | "cleanup";

export interface SandboxStep {
  id: SandboxStepId;
  /** The judge-facing keyword: EXECUTE, WAIT, HIBERNATE, … */
  keyword: string;
  label: string;
  /** One line saying what this step is evidence of. */
  detail: string;
  observed: boolean;
  at: number | null;
  atDisplay: string;
  /** The step's own duration or latency, or **null when the step has no
   * duration to report at all** — an object appearing in a bucket is an
   * instant, not an interval.
   *
   * Null and `NOT_OBSERVED` are different sentences and the UI draws them
   * differently: "there is no such measurement" versus "there is one and we
   * did not observe it". Collapsing them would print `not observed` against
   * three steps that are working perfectly, which reads as a broken run. */
  measurement: Measurement | null;
  /** The event type that proves this row, so the row names its own evidence
   * rather than asking to be believed. */
  evidence: string | null;
  source: SandboxEventSource | null;
  /** The one fact that makes this particular row mean something — the object
   * that appeared, the verdict on the commit. Null where the label already
   * says everything. Rendered in presenter mode too: it is short and it is
   * the difference between "an event happened" and "this happened". */
  note: string | null;
}

export interface MarkerContinuity {
  observed: boolean;
  /** Null only when unobserved. */
  matched: boolean | null;
  /** First 12 hex characters of the hash the session recorded. */
  digest: Identifier;
  at: number | null;
  evidence: string | null;
  display: string;
}

export interface CleanupStatus {
  /** Somebody asked, or the session was recorded TERMINATED. */
  requested: boolean;
  /** The provider answered. This is the one that means no sandbox is still
   * billing. */
  observed: boolean;
  credentialRevoked: boolean;
  at: number | null;
  evidence: string | null;
  display: string;
}

export interface ExternalTrigger {
  observed: boolean;
  at: number | null;
  evidence: string | null;
  /** The object that appeared, basename only on the public view — an OSS key
   * carries the training job id in its path. */
  detail: string;
  display: string;
}

export interface EvaluationAttempt {
  /** The sandbox entered EVALUATING. */
  started: boolean;
  /** A commit was accepted. */
  accepted: boolean;
  at: number | null;
  job: Identifier;
  display: string;
}

export interface SandboxSummary {
  visibility: SandboxVisibility;
  provider: string;
  region: string;
  /** The state the LEDGER shows, which is the one this view reports. */
  observedState: SandboxSessionState | null;
  /** The state the session row carries. Equal to `observedState` in every
   * healthy case; shown separately when they disagree rather than quietly
   * preferring one. */
  recordedState: SandboxSessionState;
  stateAgrees: boolean;
  sandboxId: Identifier;
  template: { label: string; display: string } | null;
  trainingJob: Identifier;
  timeline: LifecycleEntry[];
  steps: SandboxStep[];
  /** Wall-clock time the sandbox spent hibernated. */
  hibernated: Measurement;
  /** The same interval, framed as the cost story: compute the evaluator did
   * not spend. Same number, different sentence — and `HIBERNATION_COST_NOTE`
   * travels with it. */
  activeComputeAvoided: Measurement;
  marker: MarkerContinuity;
  trigger: ExternalTrigger;
  evaluation: EvaluationAttempt;
  cleanup: CleanupStatus;
  /** Sanitized failure code, and the message only for the owner. */
  errorCode: string | null;
  errorMessage: string | null;
  /** The whole normalised ledger, for the raw view. Unrecognised types
   * included — a ledger that hides what it did not understand is not a
   * ledger. */
  events: SandboxEvent[];
  /** True while something may still happen, so the UI knows whether to keep
   * a clock running. */
  live: boolean;
}

export interface SummariseOptions {
  now?: number;
  visibility?: SandboxVisibility;
}

/** Nothing further will happen and no sandbox is still running. Only
 * TERMINATED qualifies — a SUCCEEDED session still owns a live sandbox until
 * something kills it, which is how a voucher gets drained by a run everybody
 * stopped watching. Mirrors `sandbox_sessions.TERMINAL_STATES`. */
const TERMINAL: ReadonlySet<string> = new Set(["TERMINATED"]);

export function summariseSandboxSession(
  session: SandboxSession,
  events: readonly SandboxEvent[] | null | undefined,
  options: SummariseOptions = {}
): SandboxSummary {
  const now = options.now ?? Date.now();
  const visibility = options.visibility ?? "owner";
  const ledger = normaliseEvents(events);
  const timeline = deriveTimeline(ledger);

  const at = (state: SandboxSessionState, after = -1) =>
    timeline.find((e) => e.state === state && e.sequence > after) ?? null;

  // The two visits to ACTIVE. The first is the sandbox coming up; the second
  // — the one after a RESUMING — is the wake, and telling them apart is the
  // reason this module reads the ledger instead of the state column.
  let created: LifecycleEntry | null = null;
  let woke: LifecycleEntry | null = null;
  let sawResuming = false;
  for (const entry of timeline) {
    if (entry.state === "RESUMING") sawResuming = true;
    if (entry.state !== "ACTIVE") continue;
    if (!sawResuming) created ??= entry;
    else woke ??= entry;
  }

  const requested = at("REQUESTED");
  const prepared = at("PREPARED");
  const hibernated = at("HIBERNATED");
  const resuming = at("RESUMING");
  const evaluating = at("EVALUATING", hibernated?.sequence ?? -1);
  const succeeded = at("SUCCEEDED");
  const terminated = at("TERMINATED");

  const sessionCreatedAt = ms(session.created_at);

  // Latency, in order of evidential strength: what the observer timed around
  // its own call, else the gap between two observed instants, else nothing.
  const span = (
    entry: LifecycleEntry | null,
    from: LifecycleEntry | null | number
  ): Measurement => {
    if (!entry) return UNOBSERVED;
    if (entry.latencyMs !== null) return measurement(entry.latencyMs, "measured");
    const start = typeof from === "number" ? from : from?.at ?? null;
    if (start === null || entry.at === null) return UNOBSERVED;
    return measurement(entry.at - start, "estimated");
  };

  const createLatency = span(created, requested ?? sessionCreatedAt);
  const prepareLatency = span(prepared, created);
  const hibernateLatency = span(hibernated, prepared);
  const wakeLatency = span(woke, resuming);

  // How long the sandbox was actually asleep. Ends at the resume if there was
  // one, at whatever state came next if the session died in its sleep, and at
  // `now` if it is still hibernated — the last of which is a floor that grows,
  // and says so.
  const hibernatedFor = hibernationSpan(timeline, hibernated, now);

  const marker = deriveMarker(ledger, session, hibernated?.sequence ?? -1, visibility);
  const trigger = deriveTrigger(ledger, hibernated?.sequence ?? -1, visibility);
  const cleanup = deriveCleanup(ledger, terminated);

  const acceptedEvent =
    ledger.find((e) => ACCEPTED_TYPES.has(e.type)) ?? null;
  const accepted = Boolean(acceptedEvent || succeeded);
  const acceptedAt = acceptedEvent ? ms(acceptedEvent.observed_at) : succeeded?.at ?? null;

  const evaluation: EvaluationAttempt = {
    started: Boolean(evaluating),
    accepted,
    at: evaluating?.at ?? null,
    job: identifier(session.evaluation_job_id, visibility, 8),
    display: accepted
      ? "accepted"
      : evaluating
        ? "claimed, no accepted commit observed"
        : NOT_OBSERVED,
  };

  const observedState = timeline.length > 0 ? timeline[timeline.length - 1].state : null;

  const steps: SandboxStep[] = [
    {
      id: "create",
      keyword: "EXECUTE",
      label: "Sandbox created",
      detail: "FC returned a running sandbox in " + session.region + ".",
      observed: Boolean(created),
      at: created?.at ?? null,
      atDisplay: formatClock(created?.at ?? null),
      measurement: createLatency,
      evidence: created?.type ?? null,
      source: created?.source ?? null,
      note: null,
    },
    {
      id: "prepare",
      keyword: "EXECUTE",
      label: "Evaluator prepared",
      detail: "Marker written, credential placed, worker registered with the coordinator.",
      observed: Boolean(prepared),
      at: prepared?.at ?? null,
      atDisplay: formatClock(prepared?.at ?? null),
      measurement: prepareLatency,
      evidence: prepared?.type ?? null,
      source: prepared?.source ?? null,
      note: null,
    },
    {
      id: "hibernate",
      keyword: "WAIT · HIBERNATE",
      label: "Hibernated",
      detail: "The prepared evaluator was suspended rather than left idling while training ran.",
      observed: Boolean(hibernated),
      at: hibernated?.at ?? null,
      atDisplay: formatClock(hibernated?.at ?? null),
      measurement: hibernateLatency,
      evidence: hibernated?.type ?? null,
      source: hibernated?.source ?? null,
      note: null,
    },
    {
      id: "trigger",
      keyword: "EXTERNAL EVENT",
      label: "Model artifact appeared",
      detail: "An object in OSS, not a timer and not a poll of our own database.",
      observed: trigger.observed,
      at: trigger.at,
      atDisplay: formatClock(trigger.at),
      measurement: null,
      evidence: trigger.evidence,
      source: null,
      note: trigger.observed ? trigger.detail : null,
    },
    {
      id: "wake",
      keyword: "WAKE",
      label: "Same sandbox resumed",
      detail: "Reconnected to the same sandbox id — not a new one built to look like it.",
      observed: Boolean(woke),
      at: woke?.at ?? null,
      atDisplay: formatClock(woke?.at ?? null),
      measurement: wakeLatency,
      evidence: woke?.type ?? null,
      source: woke?.source ?? null,
      note: null,
    },
    {
      id: "evaluate",
      keyword: "CONTINUE",
      label: "Evaluation claimed",
      detail: "The woken worker claimed the evaluation task that was queued before the resume.",
      observed: evaluation.started,
      at: evaluating?.at ?? null,
      atDisplay: formatClock(evaluating?.at ?? null),
      measurement: span(evaluating, woke),
      evidence: evaluating?.type ?? null,
      source: evaluating?.source ?? null,
      note: null,
    },
    {
      id: "accepted",
      keyword: "ACCEPTED OUTPUT",
      label: "Commit accepted",
      detail: "The coordinator accepted the evaluation's output — attempted work that became accepted work.",
      observed: accepted,
      at: acceptedAt,
      atDisplay: formatClock(acceptedAt),
      measurement: null,
      evidence: acceptedEvent?.type ?? succeeded?.type ?? null,
      source: acceptedEvent?.source ?? succeeded?.source ?? null,
      note: accepted ? evaluation.display : null,
    },
    {
      id: "cleanup",
      keyword: "CLEANUP",
      label: "Sandbox destroyed",
      detail: "A forgotten sandbox bills by the second, so cleanup is observed rather than assumed.",
      observed: cleanup.observed,
      at: cleanup.at,
      atDisplay: formatClock(cleanup.at),
      measurement: null,
      evidence: cleanup.evidence,
      source: null,
      // No note: the cleanup card below says all of this, and repeating it
      // on the row wraps it onto a second line for nothing.
      note: null,
    },
  ];

  return {
    visibility,
    provider: session.provider,
    region: session.region,
    observedState,
    recordedState: session.state,
    stateAgrees: observedState === null || observedState === session.state,
    sandboxId: sandboxIdentifier(session, visibility, Boolean(created)),
    template: templateDisplay(session.template),
    trainingJob: identifier(session.training_job_id, visibility, 8),
    timeline,
    steps,
    hibernated: hibernatedFor,
    activeComputeAvoided: hibernatedFor,
    marker,
    trigger,
    evaluation,
    cleanup,
    errorCode: str(session.error_code),
    // The public view gets the code and nothing else. The message is
    // sanitized upstream, which is a reason to show it to its owner — not a
    // reason to hand it to a stranger.
    errorMessage: visibility === "public" ? null : str(session.error_message),
    events: ledger,
    live: !TERMINAL.has(session.state),
  };
}

/**
 * How long the sandbox was actually asleep.
 *
 * Ends at whatever the ledger recorded next — the resume in the ordinary
 * case, but a FAILED or TERMINATED entry if the session died in its sleep,
 * which is a real outcome and must not be reported as an infinite nap. With
 * nothing after it, the interval is still open: measured to `now` and flagged
 * `ongoing`, because a number that is still growing has to say so or it reads
 * as a final result.
 */
function hibernationSpan(
  timeline: readonly LifecycleEntry[],
  hibernated: LifecycleEntry | null,
  now: number
): Measurement {
  if (!hibernated || hibernated.at === null) return UNOBSERVED;
  const start = hibernated.at;
  for (const entry of timeline) {
    if (entry.sequence <= hibernated.sequence) continue;
    if (entry.at === null) continue;
    return measurement(entry.at - start, "estimated");
  }
  return measurement(now - start, "estimated", true);
}

/** An id narrowed for whoever is looking. Everything is a suffix — including
 * for the owner — because this view's contract is "sandbox id suffix", and a
 * component that never receives a full id cannot print one. */
function identifier(
  value: string | null,
  visibility: SandboxVisibility,
  n: number
): Identifier {
  const v = str(value);
  if (!v) return NOT_OBSERVED_ID;
  return { state: "shown", display: suffix(v, visibility === "public" ? 6 : n) };
}

/** The sandbox id, with the distinction that matters most on this row.
 *
 * The API's public payload omits `external_sandbox_id` entirely
 * (`SESSION_SHARE_COLUMNS`), so a null arriving on the public path means "not
 * disclosed", not "there is no sandbox". Rendering `not observed` there would
 * tell a judge we never saw a sandbox id for a session that visibly created
 * one — a false statement about our own evidence. `withheld` is the honest
 * word, and it is only reachable when the ledger shows a sandbox was in fact
 * created. */
function sandboxIdentifier(
  session: SandboxSession,
  visibility: SandboxVisibility,
  sandboxWasCreated: boolean
): Identifier {
  const v = str(session.external_sandbox_id);
  if (v) return { state: "shown", display: suffix(v, 6) };
  if (visibility === "public" && sandboxWasCreated) return WITHHELD_ID;
  return NOT_OBSERVED_ID;
}

/**
 * Did the environment survive the wait?
 *
 * Only a verification recorded AFTER the hibernation counts. The marker
 * written during preparation proves nothing about continuity — it is the
 * hash we are comparing against — and treating it as evidence would let a
 * session that never woke up claim its filesystem survived.
 *
 * A verification event whose result cannot be read (no boolean
 * `marker_matches`, no type that decides it on its own) is reported as NOT
 * observed rather than as a pass. "We ran the check and cannot tell you what
 * it said" is not evidence of continuity.
 */
function deriveMarker(
  ledger: readonly SandboxEvent[],
  session: SandboxSession,
  afterSequence: number,
  visibility: SandboxVisibility
): MarkerContinuity {
  const recordedHash = str(session.marker_sha256);
  const digest: Identifier = recordedHash
    ? { state: "shown", display: prefixHash(recordedHash, 12) }
    : visibility === "public" && afterSequence >= 0
      ? WITHHELD_ID
      : NOT_OBSERVED_ID;

  let found: { event: SandboxEvent; matched: boolean } | null = null;
  for (const event of ledger) {
    if (event.sequence <= afterSequence) continue;
    if (!MARKER_VERIFY_TYPES.has(event.type)) continue;
    const reported = bool(event.data?.marker_matches);
    const matched =
      reported ??
      (event.type === "marker.verified"
        ? true
        : MARKER_FAILED_TYPES.has(event.type)
          ? false
          : null);
    if (matched === null) continue;
    found = { event, matched };
  }

  if (!found) {
    return {
      observed: false,
      matched: null,
      digest,
      at: null,
      evidence: null,
      display: NOT_OBSERVED,
    };
  }

  return {
    observed: true,
    matched: found.matched,
    digest,
    at: ms(found.event.observed_at),
    evidence: found.event.type,
    display: found.matched
      ? "hash matched across the wake"
      : "HASH DID NOT MATCH across the wake",
  };
}

/** The external event, and only the part of it a stranger may read. An OSS
 * key is `jobs/<training job id>/model.pt`; the basename is the fact, the
 * path is an internal id. */
function deriveTrigger(
  ledger: readonly SandboxEvent[],
  afterSequence: number,
  visibility: SandboxVisibility
): ExternalTrigger {
  const event =
    ledger.find(
      (e) =>
        e.sequence > afterSequence &&
        (TRIGGER_TYPES.has(e.type) || e.type.startsWith("trigger."))
    ) ?? null;

  if (!event) {
    return {
      observed: false,
      at: null,
      evidence: null,
      detail: NOT_OBSERVED,
      display: NOT_OBSERVED,
    };
  }

  const key = str(event.data?.key) ?? str(event.data?.object) ?? str(event.data?.name);
  const detail = key
    ? visibility === "public"
      ? key.split("/").pop() ?? key
      : key
    : "object observed in OSS";

  return {
    observed: true,
    at: ms(event.observed_at),
    evidence: event.type,
    detail,
    display: detail,
  };
}

/**
 * Requested is not observed.
 *
 * A default `state.terminated` event says our controller recorded the
 * session as finished. Only a provider observation — `sandbox.killed` and
 * friends, which carry the gateway's own answer — says the sandbox is
 * actually gone. The gap between those two sentences is a sandbox billing by
 * the second against a voucher, so the UI says which one it has.
 */
function deriveCleanup(
  ledger: readonly SandboxEvent[],
  terminated: LifecycleEntry | null
): CleanupStatus {
  const observedEvent =
    ledger.find((e) => CLEANUP_OBSERVED_TYPES.has(e.type)) ?? null;
  const requestedEvent =
    ledger.find((e) => CLEANUP_REQUESTED_TYPES.has(e.type)) ?? null;
  const credentialRevoked = ledger.some((e) =>
    CREDENTIAL_REVOKED_TYPES.has(e.type)
  );

  const observed = Boolean(observedEvent);
  const requested = observed || Boolean(requestedEvent) || Boolean(terminated);

  return {
    requested,
    observed,
    credentialRevoked,
    at: observedEvent
      ? ms(observedEvent.observed_at)
      : requestedEvent
        ? ms(requestedEvent.observed_at)
        : terminated?.at ?? null,
    evidence: observedEvent?.type ?? requestedEvent?.type ?? terminated?.type ?? null,
    display: observed
      ? credentialRevoked
        ? "sandbox destroyed and credential revoked, both observed"
        : "sandbox destroyed, observed"
      : requested
        ? "recorded as terminated — no provider confirmation observed"
        : NOT_OBSERVED,
  };
}
