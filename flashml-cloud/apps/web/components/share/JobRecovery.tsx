import { NOT_OBSERVED } from "@/lib/sandbox-session";
import {
  clockOf,
  countOrAbsent,
  describeRun,
  heldFor,
  outcomeInProse,
  stampOf,
  type Handoff,
  type PublicAttempt,
  type PublicJob,
  type PublicLedgerEntry,
  type RunStory,
} from "./job-share";

/**
 * The public evidence view of ONE job: what this product actually claims.
 *
 * The claim is one sentence, and a stranger who has never heard of FlashML has
 * to be able to read it off this screen in under fifteen seconds:
 *
 *     work was running on a machine, that machine stopped, a different machine
 *     picked the work up and finished it.
 *
 * So that sentence is the `<h1>`, derived from the attempt rows rather than
 * asserted, and everything below it is the evidence for it in descending order
 * of how much a reader needs it: the handoff itself, the counts, the lease
 * table, the coordinator's ledger.
 *
 * ---------------------------------------------------------------------------
 * WHAT IS NOT HERE, AND WHY IT IS NOT DRAWN AS MISSING
 * ---------------------------------------------------------------------------
 *
 * There is no machine name, no region, no repository, no log tail and no error
 * text in this payload, and none of that is an outage to be reported with an
 * empty state. `job_share.py` withholds them on purpose: we execute
 * user-supplied code from a submitted repository, so a job's output is the
 * submitter's, and a machine's name is a volunteer's hostname — which is a
 * person's name often enough that publishing one would put it on a page anyone
 * with a link can read.
 *
 * This component is therefore designed for what it HAS. It never renders a
 * placeholder, a greyed-out column or a "not available" for a field the API
 * declines to send, because every one of those reads as breakage and invites
 * the next person to go and unwithhold it. The pseudonyms get one short line
 * saying they are deliberate, phrased as a design choice rather than a
 * limitation, and that is the whole acknowledgement.
 *
 * ---------------------------------------------------------------------------
 * A SERVER COMPONENT, DELIBERATELY
 * ---------------------------------------------------------------------------
 *
 * No `"use client"`, and nothing here needs a browser. That is a redaction
 * property, not a performance one: a client component's props are serialised
 * into the page's own HTML, so a field JSX never renders is still in view
 * source. Only what this file actually renders reaches the visitor, which
 * makes the withholding above hold at the boundary as well as in the API.
 */
export function JobRecovery({
  job,
  attempts,
  events,
  presenter,
}: {
  job: PublicJob;
  attempts: readonly PublicAttempt[];
  events: readonly PublicLedgerEntry[];
  presenter: boolean;
}) {
  const story = describeRun(attempts);

  return (
    <div data-testid="job-recovery" data-story={story.kind}>
      <h1 className={presenter ? "text-xl font-semibold" : "title"}>
        {headline(story)}
      </h1>

      {/* The one paragraph a stranger gets, and it has to do two jobs: say
          what they are looking at, and say what makes it evidence rather than
          a marketing claim. */}
      <p
        className={
          presenter
            ? "mt-1 text-xs leading-relaxed text-muted-foreground"
            : "mt-3 max-w-prose text-sm leading-relaxed text-muted-foreground"
        }
      >
        {subhead(story)} This is a read-only record of one distributed training
        run on Zolli Cloud, reconstructed from the control plane&apos;s own
        lease and outcome records — every time below was written when the thing
        it describes happened.
      </p>

      <JobHeader job={job} presenter={presenter} />

      {(story.kind === "recovered" || story.kind === "handed-off") && (
        <HandoffStrip handoff={story.lead} presenter={presenter} />
      )}

      <Counts job={job} presenter={presenter} />

      <Attempts attempts={attempts} presenter={presenter} />

      {/* Omitted entirely when empty rather than drawn as an empty section.
          The API returns `[]` for a job whose coordinator is unreachable — a
          section of the page that cannot be drawn, not an error to report to
          somebody who cannot act on it. The evidence that matters most is in
          our own Postgres and is already above. */}
      {events.length > 0 && <Ledger events={events} presenter={presenter} />}

      <p
        className={`leading-relaxed text-muted-foreground ${
          presenter ? "mt-3 text-[11px]" : "mt-6 max-w-prose text-xs"
        }`}
      >
        Machines are identified by position within this run — machine A is
        simply the first to claim work. The people who host them are
        volunteers, and which machine is whose is theirs to say, not ours.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// the sentence
// ---------------------------------------------------------------------------

/**
 * The headline, one per story. Five sentences rather than one with clauses
 * bolted on: this is the line the whole page is built to deliver, and a
 * sentence that hedges across every case delivers none of them.
 */
function headline(story: RunStory): string {
  switch (story.kind) {
    case "recovered":
      return "A machine stopped. Another finished the work.";
    case "handed-off":
      return "A machine stopped. Another picked the work up.";
    case "interrupted":
      return "A machine stopped mid-run.";
    case "clean":
      return "Every task finished on the machine that claimed it.";
    case "no-work":
      return "This run has not claimed any work yet.";
  }
}

/** The sentence under the headline: the same fact, with the specifics that
 * make it checkable against the table below. */
function subhead(story: RunStory): string {
  switch (story.kind) {
    case "recovered":
    case "handed-off": {
      const { task, lostOn, lostOutcome, resumedOn, resumedAccepted } =
        story.lead;
      const ending = resumedAccepted
        ? "and its result was accepted"
        : "and is running it now";
      return `${cap(task)} was running on ${lostOn} when it ${outcomeInProse(
        lostOutcome
      )}. ${cap(resumedOn)} claimed the same task ${ending}.`;
    }
    case "interrupted":
      return story.losses === 1
        ? "One lease ended without its work being accepted, and no other machine has claimed that task yet."
        : `${story.losses} leases ended without their work being accepted, and no other machine has claimed those tasks yet.`;
    case "clean":
      return "No lease was lost, so nothing needed recovering.";
    case "no-work":
      return "No machine has claimed a lease for this run so far.";
  }
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

// ---------------------------------------------------------------------------
// the evidence
// ---------------------------------------------------------------------------

/** State, and the two instants that bound the run.
 *
 * The caveat under the state is not hedging. `state` is the last state this
 * control plane OBSERVED — a non-federated job's end is only ever observed,
 * never reported — so a finished job nobody has polled since can still read
 * `RUNNING` here, while the ledger below carries `JOB_SUCCEEDED`. Saying so is
 * what keeps the two consistent instead of making the page look wrong. */
function JobHeader({ job, presenter }: { job: PublicJob; presenter: boolean }) {
  const started = stampOf(job.created_at);
  const finished = stampOf(job.finished_at);

  return (
    <div
      className={`flex flex-wrap items-center gap-x-4 gap-y-2 ${
        presenter ? "mt-3" : "mt-5"
      }`}
    >
      <StatePill state={job.state} />
      {/* Rendered only when the API sent one. A job that has not finished has
          no finish time — that is an absence, and a row reading "Finished —"
          would invite a reader to fill the dash in with a measurement. */}
      {started && (
        <span className="meta">
          <span className="text-muted-foreground">started</span> {started}
        </span>
      )}
      {finished && (
        <span className="meta">
          <span className="text-muted-foreground">finished</span> {finished}
        </span>
      )}
      {!presenter && (
        <span className="meta">last state observed by the control plane</span>
      )}
    </div>
  );
}

/** The job's state as a pill.
 *
 * A local one rather than `components/jobs/StateBadge`: that component is a
 * client component typed against `lib/cloud-api`'s `JobState`, and putting a
 * client boundary on this page would serialise its props into the HTML for no
 * gain. The colours are the same vocabulary — `PARTIAL` warns rather than
 * succeeding, because a run that lost six of twenty-four shards did not
 * succeed. An unrecognised state is styled neutrally and printed verbatim
 * rather than guessed at. */
function StatePill({ state }: { state: string }) {
  const styles: Record<string, string> = {
    PENDING: "text-muted-foreground border-muted",
    SUBMITTED: "text-brand-foreground border-brand/40",
    RUNNING: "text-brand-foreground border-brand/40",
    RECOVERING: "text-warning-foreground border-warning/50",
    SUCCEEDED: "text-evergreen border-evergreen/40",
    PARTIAL: "text-warning-foreground border-warning/50",
    FAILED: "text-destructive border-destructive/40",
    CANCELLED: "text-muted-foreground border-muted",
  };
  return (
    <span
      data-testid="job-state"
      className={`inline-flex items-center rounded-md border px-2 py-0.5 font-mono text-xs ${
        styles[state] ?? "text-muted-foreground border-muted"
      }`}
    >
      {state}
    </span>
  );
}

/**
 * The handoff, drawn.
 *
 * This is the one graphic on the page and it carries the whole claim, so it is
 * two boxes and an arrow and nothing else — the shape reads before the words
 * do, which is what a fifteen-second budget actually buys.
 *
 * The pickup interval sits ON the arrow because it is the number that makes
 * the claim interesting, and it is omitted entirely when it cannot be stated
 * honestly (see `Handoff.pickupSeconds`) rather than shown as a dash.
 */
function HandoffStrip({
  handoff,
  presenter,
}: {
  handoff: Handoff;
  presenter: boolean;
}) {
  const pickup = heldFor(handoff.pickupSeconds);

  return (
    <section
      data-testid="recovery-handoff"
      className={`panel ${presenter ? "mt-3 p-3" : "mt-6 p-4"}`}
    >
      <div className="label-caps">{handoff.task}</div>
      <div className="mt-3 flex flex-col items-stretch gap-3 sm:flex-row sm:items-center">
        <MachineCell
          machine={handoff.lostOn}
          note={outcomeInProse(handoff.lostOutcome)}
          tone="lost"
        />

        <div className="flex shrink-0 flex-col items-center justify-center px-1">
          <span aria-hidden className="text-lg leading-none text-brand">
            &rarr;
          </span>
          {pickup && (
            <span className="meta mt-1 whitespace-nowrap">
              {pickup} to pick up
            </span>
          )}
        </div>

        <MachineCell
          machine={handoff.resumedOn}
          note={
            handoff.resumedAccepted ? "finished, accepted" : "running it now"
          }
          tone={handoff.resumedAccepted ? "won" : "running"}
        />
      </div>
    </section>
  );
}

function MachineCell({
  machine,
  note,
  tone,
}: {
  machine: string;
  note: string;
  tone: "lost" | "won" | "running";
}) {
  const border =
    tone === "lost"
      ? "border-warning/50"
      : tone === "won"
        ? "border-evergreen/50"
        : "border-brand/40";
  return (
    <div className={`flex-1 rounded-md border px-3 py-2.5 ${border}`}>
      <div className="font-mono text-sm font-medium">{machine}</div>
      <div className="meta mt-0.5">{note}</div>
    </div>
  );
}

/**
 * The counts.
 *
 * **Attempted and accepted are separate numbers and stay separate** — hard
 * rule 4, the same distinction the credit ledger draws. Collapsing them would
 * drive every finished job to "100%" and erase the wasted work this page
 * exists to show; that erasure is the entire reason the product has something
 * to claim.
 *
 * These are tallies, so `0` is a real answer and is printed as `0`. That is
 * the opposite of the rule for a measurement, where a zero would be a
 * fabrication — the difference being that nobody has to observe anything for a
 * count of zero to be true.
 */
function Counts({ job, presenter }: { job: PublicJob; presenter: boolean }) {
  const accepted = countOrAbsent(job.tasks_accepted);
  const total = countOrAbsent(job.tasks_total);

  return (
    <div className={presenter ? "mt-3" : "mt-6"}>
      <dl className="grid grid-cols-1 gap-px overflow-hidden rounded-md border border-border bg-border sm:grid-cols-3">
        <Stat label="Tasks accepted" value={`${accepted} of ${total}`} />
        <Stat
          label="Machines that claimed work"
          value={countOrAbsent(job.machines_claiming)}
        />
        <Stat
          label="Leases handed out"
          value={countOrAbsent(job.attempts_total)}
        />
      </dl>

      {/* The full accounting of those leases, on one line. Every category is
          always shown, including the zeroes: a breakdown that hides its empty
          buckets is a breakdown a reader cannot check against the total. */}
      <p className="meta mt-2" data-testid="attempt-breakdown">
        {countOrAbsent(job.attempts_accepted)} accepted ·{" "}
        {countOrAbsent(job.attempts_failed)} failed ·{" "}
        {countOrAbsent(job.attempts_expired)} expired ·{" "}
        {countOrAbsent(job.attempts_abandoned)} abandoned ·{" "}
        {countOrAbsent(job.attempts_unresolved)} still in flight
      </p>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-surface px-3 py-3">
      <dt className="label-caps">{label}</dt>
      <dd className="metric-value mt-1 text-lg">{value}</dd>
    </div>
  );
}

/**
 * Every lease, in the order it was claimed.
 *
 * The outcome column prints the API's own enum verbatim — `expired`, not
 * "stopped responding". Here the value is evidence and our own code assigned
 * it, so a paraphrase would be weaker than the word itself; the prose above
 * does the explaining. Same reason the console renders the router's verdicts
 * verbatim elsewhere.
 *
 * An attempt with no `resolved_at` says `in flight` in both its time columns.
 * That is a state, not a missing measurement — the lease has not ended, so
 * there is no end to report and nothing has failed to be observed.
 */
function Attempts({
  attempts,
  presenter,
}: {
  attempts: readonly PublicAttempt[];
  presenter: boolean;
}) {
  if (attempts.length === 0) return null;

  return (
    <section className={presenter ? "mt-3" : "mt-6"}>
      <h2 className="label-caps">Leases</h2>
      {/* Its own scroll container: six columns of evidence must not make the
          page body scroll sideways on a phone. */}
      <div className="mt-2 overflow-x-auto">
        <table className="w-full min-w-[34rem] border-collapse text-left text-xs">
          <thead>
            <tr className="border-b border-border text-muted-foreground">
              <Th>Task</Th>
              <Th>Machine</Th>
              <Th>Claimed</Th>
              <Th>Resolved</Th>
              <Th>Held for</Th>
              <Th>Outcome</Th>
            </tr>
          </thead>
          <tbody>
            {attempts.map((attempt, index) => {
              const resolved = clockOf(attempt.resolved_at);
              const held = heldFor(attempt.duration_s);
              const running = attempt.resolved_at === null;
              return (
                <tr
                  // Nothing on a public attempt is unique — two machines can
                  // claim one task, one machine can claim it twice — so the
                  // row's position is the only honest key.
                  key={index}
                  data-outcome={attempt.outcome ?? "unresolved"}
                  className="border-b border-border/60 last:border-0"
                >
                  <Td>{attempt.task}</Td>
                  <Td className="font-medium">{attempt.machine}</Td>
                  <Td>{clockOf(attempt.claimed_at) ?? NOT_OBSERVED}</Td>
                  <Td>{running ? "in flight" : (resolved ?? NOT_OBSERVED)}</Td>
                  <Td>{running ? "in flight" : (held ?? NOT_OBSERVED)}</Td>
                  <Td>{attempt.outcome ?? "in flight"}</Td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th scope="col" className="label-caps px-2 py-2 font-medium">
      {children}
    </th>
  );
}

function Td({
  children,
  className = "",
  }: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <td className={`px-2 py-2 font-mono tabular-nums ${className}`}>
      {children}
    </td>
  );
}

/**
 * The coordinator's ledger — AN ORDERED SEQUENCE, NOT A TIMELINE.
 *
 * There is no time column and there must never be one. The wire timestamp was
 * removed from this payload deliberately: it came off the coordinator's answer
 * rather than being assigned by the control plane, and it duplicated `seq`
 * (which the API computes) and the attempt timestamps above (which come from
 * our own table). A host with a skewed clock would have rendered an event
 * dated next year sitting beside a correct attempt timeline — not a leak, just
 * visibly wrong, on the one surface whose entire job is to be believed.
 *
 * Nor is a time interpolated from the attempts to fill the gap. A number this
 * page derived and presented in a column a reader will take for a measurement
 * is the same lie with more steps.
 *
 * `seq` is dense from 1 over what was PUBLISHED. The kinds are an allowlist
 * API-side, so anything the coordinator says that this page has not agreed to
 * name never arrives — which also means the sequence is not the coordinator's
 * own index and does not reveal how much was dropped.
 */
function Ledger({
  events,
  presenter,
}: {
  events: readonly PublicLedgerEntry[];
  presenter: boolean;
}) {
  return (
    <section className={presenter ? "mt-3" : "mt-6"} data-testid="job-ledger">
      <h2 className="label-caps">Control-plane events, in order</h2>
      <ol className="mt-2 flex flex-wrap gap-1.5">
        {events.map((event) => (
          <li
            key={event.seq}
            className="flex items-center gap-1.5 rounded-md border border-border px-2 py-1 font-mono text-[11px]"
          >
            <span className="text-muted-foreground">{event.seq}</span>
            <span>{event.kind}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
