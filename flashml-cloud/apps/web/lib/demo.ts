// The public demo page's data layer — types mirroring
// `GET /v1alpha1/public/demo`, plus every judgement the page makes about
// that payload.
//
// ALL OF IT PURE, AND ALL OF IT HERE rather than in the components, for one
// reason: no agent working in this repo can sign in to the console, and
// `/demo` — public though it is — still needs an API answering before a
// browser shows anything. So the logic that decides what a judge sees
// (which machine is working, whether a run is finished, which venue was
// faster) is tested directly, and the components are left with layout.
//
// EVERY READER HERE IS TOLERANT. This module parses a response from an API
// that is being written in parallel with it, and the page it feeds is the
// one a competition judge opens with no account and no second chance. A
// missing field, a state string this build has never heard of, a `runs`
// array that is not an array — none of them may throw. The rule is the same
// one `JobSpec` in `lib/cloud-api.ts` records: declaring a field required
// does not make it arrive, it only moves the failure from a build error to
// a TypeError mid-render, in front of the person you were trying to
// impress.

/** Which control plane drove a run. Same two values as
 * `lib/job-coordinator.ts`'s `CoordinatorVenue`, and deliberately the same
 * strings — the demo page renders them through the console's existing
 * `CoordinatorChip`, so a third venue added API-side must reach that chip
 * verbatim rather than being mapped to one of these two here. */
export type DemoCoordinator = "render" | "fc";

/** One machine in the live fleet. */
export interface DemoMachine {
  name: string;
  online: boolean;
  region: string;
  cpus: number | null;
  memory_gb: number | null;
  official: boolean;
}

/** One task of one run. `machine` is null until something claims it. */
export interface DemoTask {
  task_id: string;
  state: string;
  machine: string | null;
  started_at: string | null;
  finished_at: string | null;
  outcome: string | null;
}

export interface DemoArtifact {
  name: string;
  bytes: number | null;
}

export interface DemoRun {
  job_id: string;
  coordinator: string;
  state: string;
  created_at: string | null;
  finished_at: string | null;
  elapsed_s: number | null;
  tasks: DemoTask[];
  artifacts: DemoArtifact[];
}

export interface DemoSnapshot {
  fleet: DemoMachine[];
  runs: DemoRun[];
  /** The OTHER fleet: machines a judge plugged in themselves, in the
   * `demo-guests` pool. Same row shape as `fleet`, deliberately a separate
   * list rather than a flag on one — they are two different claims ("this is
   * the hardware we operate" and "this is your laptop, and it is online"),
   * and the second is the one a judge came to see. `[]` until somebody
   * joins, which is the common case. */
  guests: DemoMachine[];
  /** The single most recent guest run, or null. No per-venue split: the
   * guest job always goes to the default coordinator, because asking one
   * laptop to run the same task twice would say nothing about either control
   * plane. */
  guest_run: DemoRun | null;
}

/** The two panels, in the order they render. Left is the incumbent, right
 * is the one the comparison exists to make a case for. */
export const DEMO_VENUES: {
  coordinator: DemoCoordinator;
  /** The heading. One word, because `CoordinatorChip` renders the venue's
   * own name right beside it and "Always-on (Render)" next to a chip
   * reading "Render" says it twice. */
  title: string;
  /** What the venue actually is, for a judge who has never heard of
   * either. */
  subtitle: string;
}[] = [
  {
    coordinator: "render",
    title: "Always-on",
    subtitle: "A private service that is already running",
  },
  {
    coordinator: "fc",
    title: "Serverless",
    subtitle: "Alibaba Function Compute, cold until asked",
  },
];

/** Terminal run states: nothing further can happen, so nothing further needs
 * asking.
 *
 * An UNRECOGNISED state is deliberately NOT terminal. The page keeps polling
 * it, which is the harmless direction to be wrong in — a finished run that
 * gets asked about twice more costs two requests, where a running run
 * declared finished freezes the grid mid-flight and tells a judge the
 * network stalled. `useDemoPoll` puts a wall-clock ceiling on that so an
 * unknown state cannot poll forever. */
const TERMINAL_RUN_STATES = new Set([
  "SUCCEEDED",
  "FAILED",
  "CANCELLED",
  // PARTIAL IS TERMINAL, and it is not in the contract this page was written
  // against — that document lists four states, but the route returns the job
  // row's own `status` column verbatim (`demo.py`, `_run_payload`), and that
  // column really does carry PARTIAL: some tasks succeeded, some exhausted
  // their attempts, and the job allowed it. `JobState` in `lib/cloud-api.ts`
  // documents it as terminal and deliberately not SUCCEEDED.
  //
  // Omitting it was a live bug for the ~30 seconds it existed: a PARTIAL run
  // is finished, but the page would have kept polling it to the 15-minute
  // ceiling, held its Run button on "Running…", and — worst — never rendered
  // the speed comparison, because `compareSpeed` requires both runs
  // terminal. A demo that half-finishes and then silently withholds its own
  // headline number is the failure this page can least afford.
  //
  // RECOVERING is deliberately absent: a job re-placing lost work is still
  // going, and that is exactly the story this product exists to tell.
  "PARTIAL",
]);

export function isTerminalRun(state: string | null | undefined): boolean {
  return TERMINAL_RUN_STATES.has((state ?? "").toUpperCase());
}

/** What a task tile looks like. Five phases, not the raw state string: the
 * grid is read at a glance from across a room, and "LEASED" vs "RUNNING" is
 * a distinction that matters to the coordinator and to nobody watching. */
export type TaskPhase = "pending" | "running" | "done" | "failed" | "cancelled";

const PHASE_BY_STATE: Record<string, TaskPhase> = {
  PENDING: "pending",
  QUEUED: "pending",
  SUBMITTED: "pending",
  LEASED: "running",
  RUNNING: "running",
  COMPLETED: "done",
  SUCCEEDED: "done",
  FAILED: "failed",
  CANCELLED: "cancelled",
};

/**
 * Which of the five phases a task is in.
 *
 * The state string decides it when this build recognises the string. When it
 * does not — the API is being written alongside this page and may well grow
 * a state name that predates nothing — the TIMESTAMPS decide instead:
 * finished means done, started-and-not-finished means running, neither means
 * pending. That fallback is why an unknown state still animates correctly
 * rather than sitting grey through a whole run.
 */
export function taskPhase(task: Pick<DemoTask, "state" | "started_at" | "finished_at">): TaskPhase {
  const known = PHASE_BY_STATE[(task.state ?? "").toUpperCase()];
  if (known) return known;
  if (task.finished_at) return "done";
  if (task.started_at) return "running";
  return "pending";
}

/** One column of the task grid: a machine, and the work it is holding.
 *
 * EVERY FLEET MACHINE GETS A LANE, including one holding nothing. A grid
 * that renders only the busy machines cannot show parallelism — four
 * columns lighting up together is the claim, and you cannot see four
 * columns unless four columns are drawn. An idle lane renders as an empty
 * lane, which is a true statement about that machine. */
export interface MachineLane {
  /** The machine's name, or `null` for the unplaced-work lane. */
  machine: string | null;
  online: boolean;
  region: string | null;
  tasks: DemoTask[];
  running: number;
  done: number;
}

/** The lane tasks land in before any machine has claimed them. Rendered
 * only when it holds something.
 *
 * ONE WORD, because it is a lane header in a column roughly 150px wide set
 * in 10.5px mono, and it sits beside machine names that are themselves
 * near that budget. "Awaiting placement" was the first version and rendered
 * as "Awaiting place…" in the harness — a truncated label on the one lane
 * whose whole job is to explain itself. Checked in `.preview/demo.html`. */
export const UNPLACED_LANE = "Unplaced";

/**
 * Group a run's tasks by the machine running them, one lane per fleet
 * machine.
 *
 * Task order within a lane is the order the API listed them, which is task
 * id order — so a tile does not jump columns and rows between two polls
 * merely because its neighbour finished.
 *
 * A task naming a machine that is not in the fleet still gets a lane, added
 * after the fleet's own. That is not a hypothetical: the fleet list and the
 * run are two different reads, and a machine that dropped offline between
 * them would otherwise silently drop its tasks out of the grid — nine tasks
 * would render as seven, and the count would be a lie.
 */
export function machineLanes(fleet: DemoMachine[], run: DemoRun | null): MachineLane[] {
  const lanes = new Map<string, MachineLane>();
  const lane = (name: string | null, seed?: DemoMachine): MachineLane => {
    const key = name ?? UNPLACED_LANE;
    let found = lanes.get(key);
    if (!found) {
      found = {
        machine: name,
        online: seed?.online ?? false,
        region: seed?.region ?? null,
        tasks: [],
        running: 0,
        done: 0,
      };
      lanes.set(key, found);
    }
    return found;
  };

  for (const machine of fleet) lane(machine.name, machine);

  for (const task of run?.tasks ?? []) {
    const target = lane(task.machine ?? null);
    target.tasks.push(task);
    const phase = taskPhase(task);
    if (phase === "running") target.running += 1;
    if (phase === "done") target.done += 1;
  }

  // The unplaced lane last, whatever order it was created in — it is a
  // waiting room, not a machine, and putting it between two machines breaks
  // the read that every other column is a piece of hardware.
  const all = [...lanes.values()];
  const placed = all.filter((l) => l.machine !== null);
  const unplaced = all.filter((l) => l.machine === null && l.tasks.length > 0);
  return [...placed, ...unplaced];
}

/** The one number that states the claim: how many machines are executing
 * work at this instant. Four out of four is the whole demo. */
export function busyMachineCount(lanes: MachineLane[]): number {
  return lanes.filter((l) => l.machine !== null && l.running > 0).length;
}

export interface RunTally {
  total: number;
  pending: number;
  running: number;
  done: number;
  failed: number;
  cancelled: number;
}

export function tallyTasks(tasks: DemoTask[]): RunTally {
  const tally: RunTally = {
    total: tasks.length,
    pending: 0,
    running: 0,
    done: 0,
    failed: 0,
    cancelled: 0,
  };
  for (const task of tasks) tally[taskPhase(task)] += 1;
  return tally;
}

/** Is this job id anywhere in the snapshot yet — either venue run, or the
 * guest run?
 *
 * Used to close the gap between a Run press and the run appearing in a read.
 * Matched on the ID the POST handed back rather than on "are there any runs
 * now", because a SECOND press ("Run again") is made against a page where a
 * run of that kind already exists: a presence check would clear the wait
 * immediately, `anyRunLive` would see only the previous terminal run, and
 * polling would stop with the grid frozen on the old result. */
export function snapshotHasJob(
  snapshot: DemoSnapshot | null,
  jobId: string
): boolean {
  if (!snapshot) return false;
  if (snapshot.guest_run?.job_id === jobId) return true;
  return snapshot.runs.some((run) => run.job_id === jobId);
}

/** The run for one venue, or null. The contract allows at most one of each,
 * so the first match is the answer; a second is ignored rather than
 * concatenated, because two panels for one venue is not a shape this page
 * can draw. */
export function runFor(
  snapshot: DemoSnapshot | null,
  coordinator: DemoCoordinator
): DemoRun | null {
  return snapshot?.runs.find((r) => r.coordinator === coordinator) ?? null;
}

/** Whether anything on this page can still change — and therefore whether
 * to ask again. `runs: []` is a page at rest, not a page loading: nobody has
 * pressed anything yet.
 *
 * THE GUEST RUN COUNTS. It is a third run on the same page and it moves on
 * the same clock; leaving it out would stop the poll loop while a judge was
 * watching their own laptop's task — the one moment on this page they are
 * most invested in — and freeze it a second after they pressed the button. */
export function anyRunLive(snapshot: DemoSnapshot | null): boolean {
  if (!snapshot) return false;
  const runs = [...snapshot.runs];
  if (snapshot.guest_run) runs.push(snapshot.guest_run);
  return runs.some((run) => !isTerminalRun(run.state));
}

/**
 * How long a run has been going, in seconds.
 *
 * `elapsed_s` when the API sent one — it is the authority, it is measured
 * server-side, and it does not depend on the judge's laptop clock agreeing
 * with ours. Derived from `created_at` only when it did not, so a live
 * counter still moves against an API that only fills the field on
 * completion. Null when neither is available, which renders as an em dash
 * rather than a confident zero.
 */
export function runElapsedSeconds(run: DemoRun | null, now: number): number | null {
  if (!run) return null;
  if (typeof run.elapsed_s === "number" && Number.isFinite(run.elapsed_s)) {
    return Math.max(0, run.elapsed_s);
  }
  const started = run.created_at ? Date.parse(run.created_at) : NaN;
  if (!Number.isFinite(started)) return null;
  const ended = run.finished_at ? Date.parse(run.finished_at) : NaN;
  const end = Number.isFinite(ended) ? ended : now;
  return Math.max(0, Math.round((end - started) / 1000));
}

/** Seconds as a stopwatch a person reads: `48s`, `6m 27s`. Never `387s` —
 * this number is the comparison the page exists to make, and a reader
 * should not have to divide. */
export function formatElapsed(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "—";
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `${whole}s`;
  const minutes = Math.floor(whole / 60);
  return `${minutes}m ${String(whole % 60).padStart(2, "0")}s`;
}

/** Artifact sizes. Binary units, one decimal above KB, because these are
 * checkpoint and model files and a judge comparing two runs cares that they
 * produced the SAME bytes, not the exact byte count. */
export function formatBytes(bytes: number | null | undefined): string {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}

/** The head-to-head, once there is one to state. */
export interface SpeedComparison {
  render: number;
  fc: number;
  /** Which venue finished sooner, or null for a genuine tie. */
  faster: DemoCoordinator | null;
  /** Seconds between them, always positive. */
  deltaSeconds: number;
  /** Slower ÷ faster, e.g. 1.6 for "1.6× faster". Null on a tie or when the
   * faster run took zero measurable seconds — dividing by it would print an
   * `Infinity×` that means nothing. */
  ratio: number | null;
}

/**
 * Compare the two venues — but ONLY once both have actually finished.
 *
 * A comparison drawn against a run still in flight is not a slow result, it
 * is a wrong one, and it is the single most misleading thing this page could
 * put in front of a judge. So both runs must exist, both must be terminal,
 * and both must carry a duration; anything short of that returns null and
 * the page says the comparison is not ready yet.
 *
 * Deliberately NOT restricted to SUCCEEDED. A run that failed after 20
 * seconds still took 20 seconds, and hiding that would be flattering the
 * result. The panel states each run's own state next to its time, so a
 * reader sees what they are comparing.
 */
export function compareSpeed(snapshot: DemoSnapshot | null): SpeedComparison | null {
  const renderRun = runFor(snapshot, "render");
  const fcRun = runFor(snapshot, "fc");
  if (!renderRun || !fcRun) return null;
  if (!isTerminalRun(renderRun.state) || !isTerminalRun(fcRun.state)) return null;

  // `now` is irrelevant for a terminal run carrying `finished_at`; passing 0
  // keeps this function pure and its result stable across re-renders.
  const render = runElapsedSeconds(renderRun, 0);
  const fc = runElapsedSeconds(fcRun, 0);
  if (render === null || fc === null) return null;

  const faster = render === fc ? null : render < fc ? "render" : "fc";
  const slowest = Math.max(render, fc);
  const fastest = Math.min(render, fc);
  return {
    render,
    fc,
    faster,
    deltaSeconds: slowest - fastest,
    ratio: faster === null || fastest === 0 ? null : slowest / fastest,
  };
}

// ---------------------------------------------------------------------------
// parsing
// ---------------------------------------------------------------------------

function str(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function array(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/**
 * Read `GET /v1alpha1/public/demo` into something the page can render.
 *
 * NEVER THROWS, AND NEVER RETURNS A HALF-BUILT OBJECT. An entry missing the
 * one field that identifies it — a machine with no name, a run with no
 * `job_id`, a task with no `task_id` — is DROPPED rather than rendered with
 * a placeholder identity, because a nameless tile in the grid is worse than
 * an absent one: it invites a reader to count it. Everything else defaults
 * to null and prints as an em dash.
 *
 * A body that is not an object at all yields an empty snapshot, which the
 * page renders as "no fleet reported" — the same thing it shows before the
 * first response arrives, and an honest description of what it knows.
 */
export function parseDemoSnapshot(body: unknown): DemoSnapshot {
  const root = record(body);
  if (!root) return EMPTY_SNAPSHOT();

  return {
    fleet: parseMachines(root.fleet),
    runs: array(root.runs)
      .map(parseRun)
      .filter((r): r is DemoRun => r !== null),
    // `guests` is the OTHER fleet — machines a judge plugged in themselves —
    // and it is built by the API's same two functions over a different pool,
    // so it parses through the same reader. `[]` in any deployment where
    // nobody has joined, which is the common case and not an error.
    guests: parseMachines(root.guests),
    // A single run, or null. Null before anybody presses "Run on my
    // machine", and null in every deployment where nobody has joined.
    guest_run: parseRun(root.guest_run),
  };
}

function EMPTY_SNAPSHOT(): DemoSnapshot {
  return { fleet: [], runs: [], guests: [], guest_run: null };
}

/** One fleet array — `fleet` or `guests`, identical row shape. */
function parseMachines(value: unknown): DemoMachine[] {
  const machines: DemoMachine[] = [];
  for (const entry of array(value)) {
    const row = record(entry);
    const name = row && str(row.name);
    if (!row || !name) continue;
    machines.push({
      name,
      online: row.online === true,
      region: str(row.region) ?? "—",
      cpus: num(row.cpus),
      memory_gb: num(row.memory_gb),
      official: row.official === true,
    });
  }
  return machines;
}

/**
 * One run — an entry of `runs`, or the whole of `guest_run`.
 *
 * Extracted so the guest run cannot drift from the venue runs: they are the
 * same shape on the wire (the API builds all three with `_demo_run_view`),
 * and two readers for one shape is how a field gets handled in one place and
 * forgotten in the other.
 */
function parseRun(entry: unknown): DemoRun | null {
  const row = record(entry);
  const jobId = row && str(row.job_id);
  if (!row || !jobId) return null;

  const tasks: DemoTask[] = [];
  for (const rawTask of array(row.tasks)) {
    const t = record(rawTask);
    const taskId = t && str(t.task_id);
    if (!t || !taskId) continue;
    tasks.push({
      task_id: taskId,
      state: str(t.state) ?? "",
      machine: str(t.machine),
      started_at: str(t.started_at),
      finished_at: str(t.finished_at),
      outcome: str(t.outcome),
    });
  }

  const artifacts: DemoArtifact[] = [];
  for (const rawArtifact of array(row.artifacts)) {
    const a = record(rawArtifact);
    const name = a && str(a.name);
    if (!a || !name) continue;
    artifacts.push({ name, bytes: num(a.bytes) });
  }

  return {
    job_id: jobId,
    // Verbatim, never defaulted to "render": this page draws one panel per
    // venue and matches on this string, so guessing here would file a run
    // under a venue that did not produce it.
    coordinator: str(row.coordinator) ?? "",
    state: str(row.state) ?? "",
    created_at: str(row.created_at),
    finished_at: str(row.finished_at),
    elapsed_s: num(row.elapsed_s),
    tasks,
    artifacts,
  };
}

// ---------------------------------------------------------------------------
// the judge's own machine
// ---------------------------------------------------------------------------

/** What `POST /v1alpha1/public/demo/join` hands back to the judge who just
 * joined.
 *
 * `name` is their machine's REAL hostname and can be absent; `label` is the
 * `prov…` handle every stranger sees, and is the ONLY one that appears in
 * the public `guests` array. So `label` — never `name` — is what matches a
 * row in that list. See `render_joined_machine` in the API. */
export interface JoinedMachine {
  name: string | null;
  node_id: string;
  label: string;
}

export function parseJoinedMachine(body: unknown): JoinedMachine | null {
  const root = record(body);
  const machine = root && record(root.machine);
  const label = machine && str(machine.label);
  if (!machine || !label) return null;
  return {
    name: str(machine.name),
    node_id: str(machine.node_id) ?? "",
    label,
  };
}

/** Is this row in `guests` the machine THIS visitor just plugged in?
 *
 * Matched on the handle, because that is the only identifier the public list
 * carries — the array is read by everybody and names nobody. A judge who has
 * not joined in this browser matches nothing, which is correct: somebody
 * else's laptop is not theirs to be shown as theirs. */
export function isMyGuest(
  machine: Pick<DemoMachine, "name">,
  joined: JoinedMachine | null
): boolean {
  return joined !== null && machine.name === joined.label;
}

/** The characters `device_codes.user_code` actually holds.
 *
 * Mirrors the API's `normalise_user_code` exactly: a judge reads a code off
 * a terminal and types it into a browser, so it arrives with the dash the
 * CLI printed, in whatever case they were in, with a trailing space from the
 * copy. None of those is a different code, and the alphabet contains no
 * dash, space or lowercase letter, so stripping them cannot collide two
 * distinct codes.
 *
 * Done here as well as server-side purely so the page can tell whether there
 * is anything to submit — the API remains the authority on whether a code is
 * real. */
export function normaliseUserCode(raw: string): string {
  return raw
    .toUpperCase()
    .split("")
    .filter((ch) => /[A-Z0-9]/.test(ch))
    .join("");
}

/** Whether the button should be live. Deliberately "anything at all" rather
 * than a length check: a code length is the API's business, and a page that
 * refuses to submit an 8-character code because it expected 9 is a page that
 * cannot be recovered from the browser. */
export function userCodeReady(raw: string): boolean {
  return normaliseUserCode(raw).length > 0;
}

/** FastAPI puts its message in `detail`. Pulled out so the page can print
 * the API's own sentence rather than a paraphrase — those strings are
 * written for this exact reader ("that code has expired — run `flashnode
 * login` again") and re-wording them here would only make them worse. */
export function apiDetail(body: unknown): string | null {
  const root = record(body);
  return root ? str(root.detail) : null;
}

/**
 * What to tell a judge whose join did not work.
 *
 * PREFERS THE API'S OWN SENTENCE. Every failure this route produces already
 * carries a `detail` written for a person standing at a terminal, and a
 * status-code lookup here would replace four good sentences with four worse
 * ones — and would silently mistranslate any fifth the API grows later.
 *
 * The per-status fallbacks exist only for the case where there is no body to
 * read at all: a proxy 502, a dropped connection, a response that is not
 * JSON. They are deliberately vaguer than the real messages, because in that
 * situation the page genuinely does not know which of them applies.
 */
export function joinErrorMessage(status: number, detail: string | null): string {
  if (detail) return detail;
  if (status === 400) return "That does not look like a machine code.";
  if (status === 404)
    return "That code is not one we recognise. It may have expired — run `flashnode login` again for a fresh one.";
  if (status === 409) return "That machine has already joined.";
  if (status === 429)
    return "That is a lot of attempts from one network. Give it a moment and try again.";
  if (status === 503) return "The demo is not accepting machines right now.";
  return "Could not join that machine right now.";
}

/** The same treatment for `run-mine`, whose one interesting failure is the
 * 503 that means nobody has plugged anything in yet. */
export function runMineErrorMessage(
  status: number,
  detail: string | null
): string {
  if (detail) return detail;
  if (status === 503)
    return "No machine has joined yet — paste a code above first.";
  if (status === 429)
    return "That is a lot of attempts from one network. Give it a moment and try again.";
  return "Could not start that run right now.";
}
