import {
  NOT_OBSERVED,
  formatClock,
  formatDuration,
} from "@/lib/sandbox-session";

/**
 * The public run page's data contract, and the one story it derives.
 *
 * The authority for every shape below is
 * `apps/api/flashml_cloud_api/job_share.py` — `public_job_view`,
 * `public_attempt_view` and `public_ledger_view`. Read its module docstring
 * before adding a field here: what that route publishes and what it withholds
 * is a security property (AS-16, *a session's output is ours; a job's output
 * is theirs*), and a type in this repo that names a field the API does not
 * send is an invitation to go and add it.
 *
 * **This module lives under `components/` rather than `lib/` deliberately.**
 * `app/share/[token]/page.tsx` is an App Router page module and may export
 * nothing but a default component plus route config — `lib/route-exports.test.ts`
 * enforces that, and the bug it was written against white-screened two routes
 * in production with no build error. So the parsing and the derivation sit
 * beside the components that consume them.
 *
 * Vocabulary is borrowed from `lib/sandbox-session` rather than restated:
 * `NOT_OBSERVED`, `formatClock` and `formatDuration` already carry this
 * console's answer to "what does an absent measurement look like", and the two
 * public views of one share token drifting apart is exactly the failure the
 * API avoids by calling one renderer from both of its branches.
 */

// ---------------------------------------------------------------------------
// the wire shapes
// ---------------------------------------------------------------------------

/**
 * `public_job_view` — four columns that survived `JOB_SHARE_COLUMNS` plus
 * counts derived from the attempt rows.
 *
 * Every count is `number | null` here even though the API always computes one.
 * Null is not defensive noise: it is the only way this page can render "we do
 * not have this" instead of `0`, and `0` on a page whose subject is how much
 * work was attempted is a claim, not a blank. A deploy skew that drops a key
 * must cost that one stat, never the page.
 *
 * `state` is the last state this control plane OBSERVED, not a live read — a
 * finished job nobody has polled since can still read `RUNNING` here. The
 * ledger carries `JOB_SUCCEEDED` / `JOB_FAILED` when the coordinator recorded
 * one, and the two together are honest. The UI says so rather than papering
 * over it.
 */
export interface PublicJob {
  job_id: string;
  state: string;
  created_at: string | null;
  finished_at: string | null;
  tasks_total: number | null;
  tasks_accepted: number | null;
  machines_claiming: number | null;
  attempts_total: number | null;
  attempts_accepted: number | null;
  attempts_failed: number | null;
  attempts_expired: number | null;
  attempts_abandoned: number | null;
  attempts_unresolved: number | null;
}

/**
 * `public_attempt_view` — one lease.
 *
 * `machine` and `task` are PSEUDONYMS the API assigns from a position within
 * this one job ("machine A", "task 1"), stable inside this payload and
 * meaningless outside it. They are not ids and there is no id behind them to
 * ask for: a machine's real name is a volunteer's hostname, which is a
 * person's name often enough that publishing one is the bug `job_share.py`
 * spends a page of docstring refusing to ship.
 *
 * `outcome` is null while an attempt is in flight (or for a row predating
 * migration 0015). That is a different statement from any of the four terminal
 * values and must never be folded into "failed".
 */
export interface PublicAttempt {
  machine: string;
  task: string;
  claimed_at: string | null;
  resolved_at: string | null;
  outcome: string | null;
  /** `resolved_at - claimed_at`, in SECONDS, from the API's own SQL. Null
   * whenever the attempt has not resolved. */
  duration_s: number | null;
}

/**
 * `public_ledger_view` — kind and position, and nothing else.
 *
 * **There is no timestamp, and one must not be invented.** The wire `at` was
 * removed on purpose: it came off the coordinator's answer rather than being
 * assigned by the control plane, `Event.source` names `"flashnode"` among its
 * origins, and it duplicated two values of certain provenance — `seq`, which
 * the API computes, and each attempt's `claimed_at` / `resolved_at`, which
 * come from our own table. A value of uncertain origin that duplicates a
 * certain one can only agree (adding nothing) or disagree (discrediting the
 * page), and a host with a skewed clock would have rendered an event dated
 * next year beside a correct attempt timeline.
 *
 * So the ledger is an ORDERED SEQUENCE, not a timeline. `seq` is dense from 1
 * over what was published — never the coordinator's own index, which would
 * leak how many events this page declined to describe.
 */
export interface PublicLedgerEntry {
  seq: number;
  kind: string;
}

/** The `kind: "job"` half of the discriminated envelope. */
export interface JobPayload {
  job: PublicJob;
  attempts: PublicAttempt[];
  events: PublicLedgerEntry[];
}

// ---------------------------------------------------------------------------
// parsing
// ---------------------------------------------------------------------------

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

/** A count, or null. `Number.isFinite` rather than `typeof === "number"`
 * because `NaN` is a number and would render as the word "NaN" on a page a
 * stranger is being asked to believe. */
function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * The `kind: "job"` branch of `GET /v1alpha1/public/share/{token}`, or null.
 *
 * Null for anything that is not unmistakably a job payload — a different
 * `kind`, a missing `job`, a job with no `state`. The caller turns null into
 * the same page an unknown token gets, which is the whole failure doctrine of
 * this route: a non-200, an unreachable API and a body that is not a payload
 * must be indistinguishable to an anonymous visitor, because telling them
 * apart tells a prober which tokens exist.
 *
 * `kind` is checked rather than sniffed. The API's own docstring makes the
 * argument — "a consumer that has to sniff which of several shapes it received
 * is a consumer with a branch that will eventually be wrong" — and the session
 * branch downstream of this one is exactly such a consumer, kept only because
 * it already shipped.
 */
export function readJobPayload(body: unknown): JobPayload | null {
  const envelope = asRecord(body);
  if (!envelope || envelope.kind !== "job") return null;

  const raw = asRecord(envelope.job);
  if (!raw) return null;

  const jobId = str(raw.job_id);
  const state = str(raw.state);
  // A job with no state has no story to tell and nothing to badge. Refusing it
  // here is better than rendering a page with a blank where the verdict goes.
  if (!jobId || !state) return null;

  const job: PublicJob = {
    job_id: jobId,
    state,
    created_at: str(raw.created_at),
    finished_at: str(raw.finished_at),
    tasks_total: num(raw.tasks_total),
    tasks_accepted: num(raw.tasks_accepted),
    machines_claiming: num(raw.machines_claiming),
    attempts_total: num(raw.attempts_total),
    attempts_accepted: num(raw.attempts_accepted),
    attempts_failed: num(raw.attempts_failed),
    attempts_expired: num(raw.attempts_expired),
    attempts_abandoned: num(raw.attempts_abandoned),
    attempts_unresolved: num(raw.attempts_unresolved),
  };

  // Rows are read field by field rather than cast wholesale. A cast would
  // carry any extra key the API grew straight into this page's props — and on
  // THIS page the next key added upstream is withheld by default, not
  // published by default. Nothing reaches a component that is not named here.
  const attempts: PublicAttempt[] = (
    Array.isArray(envelope.attempts) ? envelope.attempts : []
  ).flatMap((entry): PublicAttempt[] => {
    const row = asRecord(entry);
    const machine = row && str(row.machine);
    const task = row && str(row.task);
    if (!row || !machine || !task) return [];
    return [
      {
        machine,
        task,
        claimed_at: str(row.claimed_at),
        resolved_at: str(row.resolved_at),
        outcome: str(row.outcome),
        duration_s: num(row.duration_s),
      },
    ];
  });

  const events: PublicLedgerEntry[] = (
    Array.isArray(envelope.events) ? envelope.events : []
  ).flatMap((entry): PublicLedgerEntry[] => {
    const row = asRecord(entry);
    const kind = row && str(row.kind);
    const seq = row && num(row.seq);
    if (!row || !kind || seq === null) return [];
    return [{ seq, kind }];
  });

  return { job, attempts, events };
}

// ---------------------------------------------------------------------------
// the story
// ---------------------------------------------------------------------------

/** The four terminal outcomes migration 0015 constrains, plus the null that
 * means "still in flight". */
export type AttemptOutcome = "accepted" | "failed" | "expired" | "abandoned";

/** An attempt that ended without its work being accepted. Not a synonym for
 * "failed": a lease that expired because a machine stopped renewing it is the
 * headline case this whole page exists to show, and it is not a failure of the
 * work. */
const LOST = new Set<string>(["failed", "expired", "abandoned"]);

/**
 * One machine losing a task and a DIFFERENT machine picking it up — the single
 * fact this page exists to make legible.
 */
export interface Handoff {
  task: string;
  lostOn: string;
  lostOutcome: string;
  resumedOn: string;
  /** Whether the machine that picked the work up went on to have its result
   * accepted. False while it is still running, which is a different and
   * weaker claim than "finished elsewhere". */
  resumedAccepted: boolean;
  /** Seconds between the lost lease resolving and the next machine claiming
   * the same task, or null when that cannot be stated honestly.
   *
   * Null rather than zero, and null rather than a negative number: an expired
   * lease is stamped `resolved_at` by the reconciler, which can run AFTER
   * another machine has already claimed the task, so the subtraction really
   * can come out negative. That is an artefact of when a row was written, not
   * a machine picking work up before it was dropped, and printing it would be
   * the page telling a stranger something untrue about time.
   */
  pickupSeconds: number | null;
}

/**
 * What happened to this run, in the order a reader cares about.
 *
 * Five outcomes, not one with optional fields, because each needs a different
 * sentence and collapsing any two would put a false one on the page. The whole
 * point is that `recovered` — work lost and finished elsewhere — is the claim,
 * and every other state has to be able to say it did not happen.
 */
export type RunStory =
  | { kind: "recovered"; lead: Handoff; handoffs: Handoff[] }
  | { kind: "handed-off"; lead: Handoff; handoffs: Handoff[] }
  | { kind: "interrupted"; losses: number }
  | { kind: "clean" }
  | { kind: "no-work" };

/**
 * Derive the run's story from its attempts alone.
 *
 * Attempts arrive ordered by `claimed_at` (the API's `order by`), so a task's
 * rows are already its history. A handoff is read off CONSECUTIVE pairs within
 * one task: attempt *i* ended without acceptance and attempt *i+1* is on a
 * different machine.
 *
 * Consecutive rather than "the next attempt on any other machine", because a
 * task retried twice on the same host before moving — A(expired), A(failed),
 * B(accepted) — is ONE handoff, A to B. Scanning forward for the next
 * different machine would report it twice and inflate the only number on this
 * page anybody would think to count.
 */
export function describeRun(attempts: readonly PublicAttempt[]): RunStory {
  if (attempts.length === 0) return { kind: "no-work" };

  const byTask = new Map<string, PublicAttempt[]>();
  for (const attempt of attempts) {
    const rows = byTask.get(attempt.task);
    if (rows) rows.push(attempt);
    else byTask.set(attempt.task, [attempt]);
  }

  const handoffs: Handoff[] = [];
  let losses = 0;

  for (const rows of byTask.values()) {
    for (let i = 0; i < rows.length; i += 1) {
      const lost = rows[i];
      if (!lost.outcome || !LOST.has(lost.outcome)) continue;
      losses += 1;

      const resumed = rows[i + 1];
      if (!resumed || resumed.machine === lost.machine) continue;

      handoffs.push({
        task: lost.task,
        lostOn: lost.machine,
        lostOutcome: lost.outcome,
        resumedOn: resumed.machine,
        resumedAccepted: resumed.outcome === "accepted",
        pickupSeconds: gapSeconds(lost.resolved_at, resumed.claimed_at),
      });
    }
  }

  if (handoffs.length > 0) {
    // The lead is the first COMPLETED recovery when there is one: "picked it
    // up and finished it" is the claim, and a handoff still in flight is only
    // half of it. Falling back to the first handoff keeps the weaker sentence
    // rather than upgrading it.
    const finished = handoffs.find((h) => h.resumedAccepted);
    return finished
      ? { kind: "recovered", lead: finished, handoffs }
      : { kind: "handed-off", lead: handoffs[0], handoffs };
  }
  if (losses > 0) return { kind: "interrupted", losses };
  return { kind: "clean" };
}

/** Seconds between two wire instants, or null when the answer would not be
 * honest — either timestamp missing or unparseable, or a negative result (see
 * `Handoff.pickupSeconds`). */
function gapSeconds(from: string | null, to: string | null): number | null {
  if (typeof from !== "string" || typeof to !== "string") return null;
  const a = Date.parse(from);
  const b = Date.parse(to);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  const seconds = (b - a) / 1000;
  return seconds >= 0 ? seconds : null;
}

// ---------------------------------------------------------------------------
// formatting — every one of these returns null for "there is nothing to show"
// ---------------------------------------------------------------------------
//
// Null out, never a dash and never a zero. The caller then decides between
// omitting the row entirely and printing NOT_OBSERVED in words, which are
// different statements: a job that has not finished has no finish time (omit),
// while an attempt that resolved with no duration recorded is a measurement we
// expected and did not get (say so).

/** `14:32:07 UTC`. Time of day only — for rows sitting under a header that
 * has already established the date. */
export function clockOf(iso: string | null): string | null {
  const ms = instant(iso);
  return ms === null ? null : formatClock(ms);
}

/** `2026-08-12 14:32:07 UTC`. Hand-built from UTC parts rather than
 * `toLocaleString`, for the reason `formatClock` gives: this page server-renders
 * and then hydrates in a browser in some other timezone, and a locale-formatted
 * stamp differs between the two renders. It is also the better evidence format
 * — a judge comparing this to a coordinator log is comparing UTC to UTC. */
export function stampOf(iso: string | null): string | null {
  const ms = instant(iso);
  if (ms === null) return null;
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`
  );
}

function instant(iso: string | null): number | null {
  if (typeof iso !== "string") return null;
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : null;
}

/** How long a lease was held. Seconds in — the API's `duration_s` — and the
 * console's own duration vocabulary out, which is in milliseconds. */
export function heldFor(seconds: number | null): string | null {
  return seconds === null ? null : formatDuration(seconds * 1000);
}

/** A count for display, or the words for its absence. Counts are tallies, so
 * `0` is a real and honest answer here and is rendered as `0` — unlike a
 * measurement, where zero would be a fabrication. */
export function countOrAbsent(value: number | null): string {
  return value === null ? NOT_OBSERVED : String(value);
}

/**
 * Plain language for an outcome, for use IN PROSE only.
 *
 * The attempts table prints the API's own enum verbatim, because there the
 * value is evidence and a paraphrase is weaker than the word our own code
 * assigned. The narrative sentence above it is written for someone who has
 * never heard of this product and has fifteen seconds, and "expired" does not
 * survive that reader: the lease expired because the machine stopped renewing
 * it, which to a stranger is the machine stopping.
 */
export function outcomeInProse(outcome: string): string {
  switch (outcome) {
    case "expired":
      return "stopped responding";
    case "failed":
      return "failed";
    case "abandoned":
      return "gave the work back";
    case "accepted":
      return "was accepted";
    default:
      // An outcome this console has not met. Named verbatim rather than
      // guessed at — the same discipline `JobEvent.type` documents for an
      // enum that grows upstream of this repo.
      return outcome;
  }
}

/** Terminal job states. A job outside this set can still change, so the page
 * keeps refreshing; one inside it is finished and re-fetching it forever is
 * just load. Mirrors `JobState` in `lib/cloud-api.ts` — `PARTIAL` is terminal
 * and deliberately not `SUCCEEDED`. */
const TERMINAL_JOB_STATES = new Set([
  "SUCCEEDED",
  "PARTIAL",
  "FAILED",
  "CANCELLED",
]);

export function jobIsSettled(state: string): boolean {
  return TERMINAL_JOB_STATES.has(state);
}
