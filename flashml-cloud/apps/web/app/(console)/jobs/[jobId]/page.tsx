"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, Warning } from "@phosphor-icons/react";
import { ArtifactsCard } from "@/components/jobs/ArtifactsCard";
import { StateBadge } from "@/components/jobs/StateBadge";
import { Swimlanes } from "@/components/jobs/Swimlanes";
import { FleetTopology } from "@/components/jobs/FleetTopology";
import { RoundProgress } from "@/components/jobs/RoundProgress";
import { MemberCredits } from "@/components/jobs/MemberCredits";
import { JobResultCard } from "@/components/jobs/JobResultCard";
import { CheckpointsCard } from "@/components/jobs/CheckpointsCard";
import { RoutingCard } from "@/components/jobs/RoutingCard";
import { TradeoffCard } from "@/components/jobs/TradeoffCard";
import { LedgerView } from "@/components/jobs/LedgerView";
import { Disclosure } from "@/components/jobs/Disclosure";
import { useWorkspaceHint } from "@/components/shell/WorkspaceHint";
import {
  deriveAttempts,
  deriveProgress,
  deriveStallReason,
} from "@/lib/job-activity";
import {
  summariseJobArtifacts,
  type ArtifactsPanel,
  type ArtifactsRead,
} from "@/lib/job-artifacts";
import {
  summariseRouting,
  type RoutingPanel,
  type RoutingRead,
} from "@/lib/job-routing";
import {
  summariseTradeoff,
  type TradeoffPanel,
  type TradeoffRead,
} from "@/lib/job-tradeoff";
import {
  selectTasksForCheckpointRead,
  summariseCheckpoints,
  type CheckpointPanel,
} from "@/lib/task-checkpoints";
import { workspacePath } from "@/lib/workspace-scope";
import {
  ApiError,
  NotAuthenticated,
  NotFound,
  cancelJob,
  getJob,
  getJobResult,
  getJobTradeoff,
  listJobArtifacts,
  listJobContributions,
  listJobEvents,
  listJobRounds,
  listJobTasks,
  previewJobPlans,
  readTaskCheckpoint,
  type TaskCheckpointRead,
  type JobContribution,
  type JobEvent,
  type JobRecord,
  type JobResult,
  type JobRound,
  type JobTask,
} from "@/lib/cloud-api";

// Three views over one job:
//   Progress   what the run is achieving
//   Placement  where it is running and what that cost
//   Ledger     what actually happened, in the coordinator's own words
//
// The rule that makes the switcher safe: it changes the DETAIL, never the
// ALARM. State, accepted-progress and the stall reason sit above the tabs and
// stay visible from every view, so nobody watches a flat metric panel for
// twenty minutes while the run has been wedged for nineteen.

// PARTIAL belongs here or the page polls a job that is finished and is
// never going to change again.
const TERMINAL = new Set(["SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"]);
const POLL_MS = 2500;

type View = "progress" | "placement" | "ledger";
type LoadState = "loading" | "ready" | "not-found" | "error";

export default function JobDetailPage({
  params,
}: {
  params: Promise<{ jobId: string }>;
}) {
  const { jobId } = use(params);
  const router = useRouter();

  const [job, setJob] = useState<JobRecord | null>(null);
  const [rounds, setRounds] = useState<JobRound[]>([]);
  const [tasks, setTasks] = useState<JobTask[]>([]);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [contributions, setContributions] = useState<JobContribution[]>([]);
  const [result, setResult] = useState<JobResult | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [checkpoints, setCheckpoints] = useState<
    Record<string, TaskCheckpointRead>
  >({});
  // Its own state, not a field of `job`: `job.artifacts` is always `[]` for
  // every job this deployment runs (see `JobRecord.artifacts`), and reading
  // the listing separately is the whole point of this card existing. It
  // starts as `loading` and is only ever replaced by an answer — never
  // silently by an empty list.
  const [artifactsRead, setArtifactsRead] = useState<ArtifactsRead>({
    status: "loading",
  });
  // Its own state and its own loader, for the same reason the artifacts
  // listing has one: "the router put this job here, and here is why" and "we
  // could not ask the router" are different sentences, and the panel must
  // never substitute the second for the first.
  const [routingRead, setRoutingRead] = useState<RoutingRead>({
    status: "loading",
  });
  // And its own again, for the third time on this page and for the third
  // instance of the same reason: "one more machine buys you nothing" and "we
  // could not ask" are different sentences, and a curve that is missing
  // because a read failed must never render as a curve with nothing in it.
  const [tradeoffRead, setTradeoffRead] = useState<TradeoffRead>({
    status: "loading",
  });
  // One refusal ends the reads for the life of this page. The checkpoint
  // route is declared `tags=["agent"]` and depends on `current_machine`,
  // which refuses a browser JWT by design — so a 401 is a property of the
  // API and will be the same answer for every task, on every tick. Retrying
  // it would be one guaranteed 401 per task per 2.5s for as long as the tab
  // is open. A ref, not state: it must take effect within the same tick that
  // observed it, and it must not re-render anything by itself.
  const checkpointsRefused = useRef(false);

  /** Ask the coordinator, through the API, for the latest committed
   * checkpoint of every task that could still lose work.
   *
   * A read per task, because that is the granularity the coordinator's
   * catalog is keyed at — and the reason this is a poll at all rather than a
   * subscription to the event feed: the coordinator emits
   * `CHECKPOINT_MANIFEST_COMMITTED` under the composite scope
   * `"<job_id>::<task_id>"`, so it can never appear in this job's ledger,
   * however long anyone watches it.
   *
   * `readTaskCheckpoint` never rejects, so no failure here can take the page
   * down or bounce a signed-in user to /sign-in — every outcome is a value
   * the panel renders honestly. */
  const readCheckpoints = useCallback(
    async (currentTasks: JobTask[]) => {
      if (checkpointsRefused.current) return;
      const { taskIds } = selectTasksForCheckpointRead(currentTasks);
      if (taskIds.length === 0) return;

      const reads = await Promise.all(
        taskIds.map(
          async (taskId) =>
            [taskId, await readTaskCheckpoint(jobId, taskId)] as const
        )
      );
      if (reads.some(([, read]) => read.status === "unreadable")) {
        checkpointsRefused.current = true;
      }
      setCheckpoints((prev) => {
        const next = { ...prev };
        for (const [taskId, read] of reads) next[taskId] = read;
        return next;
      });
    },
    [jobId]
  );

  /** Read the artifact listing, classifying every outcome into a value the
   * card renders.
   *
   * Separate from `load` so the card's own "Try again" can re-read just this
   * — a terminal job stops polling, so without it a transient failure would
   * be permanent until a full page reload.
   *
   * A failure is recorded as `unreadable`, NEVER as an empty listing: a 404
   * here is "unknown job" *or* an API deployed before this route existed,
   * and neither is evidence that the job wrote nothing. `NotAuthenticated`
   * is the one exception, handed to the same sign-in redirect the page's
   * other reads use — this is a plain browser route, so a 401 really does
   * mean signed out. */
  const loadArtifacts = useCallback(() => {
    listJobArtifacts(jobId)
      .then((listing) => setArtifactsRead({ status: "listed", listing }))
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push(`/sign-in?next=/jobs/${jobId}`);
          return;
        }
        setArtifactsRead({
          status: "unreadable",
          detail:
            err instanceof ApiError
              ? err.detail
              : err instanceof Error
                ? err.message
                : String(err),
        });
      });
  }, [jobId, router]);

  /** Ask the router how this job routes.
   *
   * DELIBERATELY NOT PART OF `load` BELOW. Every other read on this page is
   * re-issued on a 2.5s timer; this one plans the whole reachable fleet
   * against the job's spec on every call, and the answer it gives — what
   * kind of work this is, which venues can run it — is a property of the
   * spec, which cannot change after submission. Polling it would spend a
   * fleet-wide plan every 2.5s to redraw the same panel.
   *
   * It never rejects into the page's error state either: a job submitted
   * before this API stored specs answers 409, and a deployment with no
   * routing configured answers 200 with an explanation. Neither is a broken
   * job page, so both land in the panel as values. */
  const loadRouting = useCallback(() => {
    // No synchronous `setRoutingRead({ status: "loading" })` here, matching
    // `loadArtifacts` above: this runs from an effect, and a setState in an
    // effect body is a cascading render (`react-hooks/set-state-in-effect`).
    // The initial state is already `loading`, and `retryRouting` below —
    // which runs from a click, not an effect — is what resets it.
    previewJobPlans(jobId)
      .then((preview) => setRoutingRead({ status: "previewed", preview }))
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push(`/sign-in?next=/jobs/${jobId}`);
          return;
        }
        setRoutingRead({
          status: "unavailable",
          detail:
            err instanceof ApiError
              ? err.detail
              : err instanceof Error
                ? err.message
                : String(err),
        });
      });
  }, [jobId, router]);

  /** The Try again button. Puts the panel back into `loading` before asking
   * again, so a retry that ends in the same failure still looks like a
   * retry. Safe to setState here — a click is an event, not an effect. */
  const retryRouting = useCallback(() => {
    setRoutingRead({ status: "loading" });
    loadRouting();
  }, [loadRouting]);

  /** Ask what each additional machine would buy this job.
   *
   * Once per job, not per tick, for exactly `loadRouting`'s reason: the
   * answer is a property of the spec and the fleet rather than of the run,
   * and it plans every fleet size on every call. A read-only GET — nothing
   * is rented, held, matched or charged by it, and there is no
   * confirm-and-rent flow behind this panel. */
  const loadTradeoff = useCallback(() => {
    getJobTradeoff(jobId)
      .then((t) => setTradeoffRead({ status: "read", tradeoff: t }))
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push(`/sign-in?next=/jobs/${jobId}`);
          return;
        }
        setTradeoffRead({
          status: "unreadable",
          detail:
            err instanceof ApiError
              ? err.detail
              : err instanceof Error
                ? err.message
                : String(err),
        });
      });
  }, [jobId, router]);

  const retryTradeoff = useCallback(() => {
    setTradeoffRead({ status: "loading" });
    loadTradeoff();
  }, [loadTradeoff]);

  const load = useCallback(() => {
    getJob(jobId)
      .then((j) => {
        setJob(j);
        setState("ready");
        setErrorMessage(null);
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push(`/sign-in?next=/jobs/${jobId}`);
          return;
        }
        if (err instanceof NotFound) {
          setState("not-found");
          return;
        }
        setErrorMessage(
          err instanceof Error ? err.message : "Couldn't load this job."
        );
        setState("error");
      });

    // The three detail sources are best-effort and independent. A federated
    // job has no coordinator tasks; an older job may predate the ledger.
    // None of that should take the page down, so each failure just leaves
    // its section empty. NotAuthenticated is the exception: that is a real
    // signed-out state and belongs at sign-in, not rendered as "no data".
    const soft = (err: unknown) => {
      if (err instanceof NotAuthenticated) {
        router.push(`/sign-in?next=/jobs/${jobId}`);
      }
    };
    listJobRounds(jobId).then(setRounds).catch(soft);
    listJobTasks(jobId)
      .then((ts) => {
        setTasks(ts);
        // Chained off the task list rather than given its own timer: the
        // checkpoint route is per (job, task), so which tasks to ask about
        // is only knowable once this answer lands, and hanging it here keeps
        // the whole page on one cadence with no second interval to reason
        // about. `selectTasksForCheckpointRead` — the same function the
        // panel uses — bounds the fan-out to the tasks that could still lose
        // work, so a completed task is never asked about again.
        return readCheckpoints(ts);
      })
      .catch(soft);
    listJobEvents(jobId).then(setEvents).catch(soft);
    listJobContributions(jobId).then(setContributions).catch(soft);
    // Best-effort like its neighbours: a coordinator older than the
    // reduction surface 404s here, and that must leave the panel absent,
    // not take the page down.
    getJobResult(jobId).then(setResult).catch(soft);
    // On every tick, not once: a running job gains files as tasks commit,
    // and the listing is the only place they appear.
    loadArtifacts();
  }, [jobId, router, readCheckpoints, loadArtifacts]);

  useEffect(() => {
    load();
  }, [load]);

  // Once per job, not per tick — see `loadRouting`.
  useEffect(() => {
    loadRouting();
  }, [loadRouting]);

  // Same cadence and the same reason — see `loadTradeoff`.
  useEffect(() => {
    loadTradeoff();
  }, [loadTradeoff]);

  useEffect(() => {
    if (job && TERMINAL.has(job.state)) return;
    const t = setInterval(() => {
      load();
      setNow(Date.now());
    }, POLL_MS);
    return () => clearInterval(t);
  }, [job, load]);

  const attempts = useMemo(() => deriveAttempts(events), [events]);
  const progress = useMemo(
    () => deriveProgress(tasks, attempts),
    [tasks, attempts]
  );
  const stall = useMemo(
    () => (job ? deriveStallReason(job.state, tasks, events, now) : null),
    [job, tasks, events, now]
  );
  const checkpointPanel = useMemo(
    () => summariseCheckpoints({ tasks, reads: checkpoints, attempts }),
    [tasks, checkpoints, attempts]
  );
  const routingPanel = useMemo(
    () => summariseRouting(routingRead),
    [routingRead]
  );
  const tradeoffPanel = useMemo(
    () => summariseTradeoff(tradeoffRead),
    [tradeoffRead]
  );
  const artifactsPanel = useMemo(
    () =>
      summariseJobArtifacts({
        read: artifactsRead,
        jobState: job?.state ?? "",
        tasks,
        federated: job?.mode === "federated",
      }),
    [artifactsRead, job?.state, tasks, job?.mode]
  );

  // This route's path carries no pool id, so the rail would otherwise show
  // no workspace tabs and "Choose a workspace" — one click out of a
  // workspace losing all of its navigation. The job says which workspace it
  // belongs to; the same `pool_id` the back link below uses. Above the early
  // returns because it is a hook: while `job` is still null this passes
  // `undefined`, which leaves the rail exactly as the URL alone would have
  // it.
  useWorkspaceHint(job?.pool_id);

  // Default the view by state: a failed job opens on the ledger, because
  // "why" is the only question you have. Opening it on a truncated metric
  // chart is a small cruelty. Once the user picks a view, theirs wins.
  const activeView: View =
    view ?? (job && job.state === "FAILED" ? "ledger" : "progress");

  if (state === "loading") {
    return (
      <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        <div className="skeleton h-32 rounded-lg" />
      </div>
    );
  }

  if (state === "not-found") {
    return (
      <Shell>
        <p className="text-sm text-muted-foreground">
          This job doesn&apos;t exist, or isn&apos;t yours.
        </p>
        <Link href="/jobs" className="text-sm text-brand-foreground hover:underline">
          Back to jobs
        </Link>
      </Shell>
    );
  }

  if (state === "error" || !job) {
    return (
      <Shell>
        <Warning className="h-5 w-5 text-destructive" weight="fill" />
        <p className="text-sm text-muted-foreground">{errorMessage}</p>
        <button
          type="button"
          onClick={load}
          className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm hover:bg-surface-2"
        >
          Try again
        </button>
      </Shell>
    );
  }

  const name = job.spec?.metadata?.name ?? job.name ?? job.job_id;
  const backHref =
    job.pool_id != null ? workspacePath(job.pool_id, "jobs") : "/workspaces";
  const backLabel = job.pool_id != null ? "Jobs" : "Workspaces";

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <Link
        href={backHref}
        className="inline-flex items-center gap-1.5 text-sm text-brand-foreground hover:underline"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> {backLabel}
      </Link>

      <div className="mt-4 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="truncate font-mono text-2xl font-semibold">{name}</h1>
          <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
            {[
              job.job_id,
              job.backend,
              job.deployment_profile,
              job.submitted_by ? `by ${job.submitted_by}` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        <StateBadge state={job.state} />
      </div>

      {/* The alarm band. Visible from every view, by design. */}
      <div className="mt-6 rounded-lg border border-border bg-surface p-4">
        {progress.total > 0 ? (
          <ProgressBar progress={progress} />
        ) : (
          <p className="text-xs text-muted-foreground">
            {noBreakdownReason(job)}
          </p>
        )}
        {stall && (
          <p className="mt-3 flex items-start gap-2 border-t border-border pt-3 text-xs text-warning-foreground">
            <Warning className="mt-px h-3.5 w-3.5 shrink-0" weight="fill" />
            <span>{stall}</span>
          </p>
        )}
      </div>

      <div className="mt-6 flex gap-6 border-b border-border">
        {(
          [
            ["progress", "Progress"],
            ["placement", "Placement"],
            ["ledger", "Ledger"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setView(id)}
            aria-current={activeView === id ? "page" : undefined}
            className={`-mb-px border-b-2 px-0.5 pb-2.5 text-sm transition-colors ${
              activeView === id
                ? "border-primary font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <div className="mt-6">
        {activeView === "progress" && (
          <ProgressView
            job={job}
            rounds={rounds}
            contributions={contributions}
            result={result}
            checkpoints={checkpointPanel}
            artifacts={artifactsPanel}
            onReloadArtifacts={loadArtifacts}
          />
        )}
        {activeView === "placement" && (
          <PlacementView
            job={job}
            tasks={tasks}
            attempts={attempts}
            now={now}
            routing={routingPanel}
            onRetryRouting={retryRouting}
            tradeoff={tradeoffPanel}
            onRetryTradeoff={retryTradeoff}
          />
        )}
        {activeView === "ledger" && <LedgerView events={events} />}
      </div>

      <div className="mt-8">
        <CancelSection job={job} onCancelled={setJob} />
      </div>
    </div>
  );
}

/** Why there is no task breakdown, said accurately.
 *
 * "No task breakdown reported" was wrong and actively misleading for the
 * commonest case. A federated run is one coordinator job PER ROUND, and the
 * API can only find a round's coordinator job through
 * `job_rounds.coordinator_job_id` — a row written when the round COMPLETES
 * (db.py `list_round_jobs_for_owner`: "for every completed round"). So while
 * round 0 is still running there is no pointer to it, the fan-out matches
 * nothing, and the console says "nothing here" about a coordinator that is
 * sitting on a full set of tasks.
 *
 * Until the driver persists the in-flight round, say which of the two it is
 * rather than implying the work does not exist. */
function noBreakdownReason(job: JobRecord): string {
  if (job.mode === "federated" && !TERMINAL.has(job.state)) {
    return "Per-round detail appears once a round finishes. A federated run is one coordinator job per round, and the round in flight is not recorded until it completes.";
  }
  return "The coordinator reported no task breakdown for this job.";
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <div className="flex flex-col items-center gap-3 rounded-lg border border-border bg-surface py-10 text-center">
        {children}
      </div>
    </div>
  );
}

/** Accepted work, with attempts spent shown underneath. The gap between the
 * two is what unreliable machines cost, and hiding it would hide the one
 * number this product exists to talk about. */
function ProgressBar({
  progress,
}: {
  progress: ReturnType<typeof deriveProgress>;
}) {
  const pct = progress.total ? (progress.accepted / progress.total) * 100 : 0;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm">
          <span className="metric-value">
            {progress.accepted}/{progress.total}
          </span>{" "}
          <span className="text-muted-foreground">tasks accepted</span>
        </span>
        {progress.attemptsSpent > 0 && (
          <span className="font-mono text-xs text-muted-foreground">
            {progress.attemptsSpent} attempt
            {progress.attemptsSpent === 1 ? "" : "s"} spent
            {progress.wasted > 0 && `, ${progress.wasted} wasted`}
          </span>
        )}
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-[var(--node-green)] transition-[width] duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function ProgressView({
  job,
  rounds,
  contributions,
  result,
  checkpoints,
  artifacts,
  onReloadArtifacts,
}: {
  job: JobRecord;
  rounds: JobRound[];
  contributions: JobContribution[];
  result: JobResult | null;
  checkpoints: CheckpointPanel;
  artifacts: ArtifactsPanel;
  /** Re-read the listing. Used both by the card's own retry and after a
   * clear — the API is the authority on what is left, so the console asks it
   * rather than assuming the delete emptied everything. */
  onReloadArtifacts: () => void;
}) {
  return (
    <div className="space-y-6">
      {job.error && (
        <div className="rounded-lg border border-destructive/30 bg-surface p-4">
          <div className="label-caps text-destructive">Error</div>
          <p className="mt-1.5 font-mono text-sm text-destructive">
            {job.error}
          </p>
        </div>
      )}

      {/* Above the metrics: this is the answer the job was submitted for.
          Renders nothing for a federated job or one with no reducer. */}
      <JobResultCard result={result} />

      {rounds.length > 0 ? (
        <RoundProgress rounds={rounds} jobStartedAt={job.created_at} />
      ) : (
        <NoMetrics />
      )}

      {/* Above the artifacts, and above the credits: the answer to "what
          does a machine dying right now cost me" is the reason this product
          exists, and it was the one thing this page never said. Renders
          nothing when there is no task breakdown to say it about. */}
      <CheckpointsCard panel={checkpoints} />

      {/* Renders nothing for a job with no recorded contributions — most
          jobs, since this data only exists for a pool job. */}
      <MemberCredits contributions={contributions} />

      <ArtifactsCard
        jobId={job.job_id}
        panel={artifacts}
        onCleared={onReloadArtifacts}
        onRetry={onReloadArtifacts}
      />
      {job.spec && <SpecCard job={job} />}
    </div>
  );
}

/** The honest empty state. The only model metric the system records is
 * `mean_loss` per federated round. Rather than draw an empty chart frame,
 * say what is missing and what would fill it.
 *
 * ONE SENTENCE, then the rest on request. Two paragraphs explaining an
 * absence — where the metric comes from, and which repository would have to
 * change for there to be more of them — is more page than the absence
 * deserves, sitting where a chart would be. Neither paragraph is gone: both
 * are under `why`, word for word. */
function NoMetrics() {
  return (
    <section className="rounded-lg border border-border bg-surface p-6">
      <h2 className="text-sm font-semibold">No training metrics for this job</h2>
      <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
        Independent jobs report no model metrics today, so there is nothing to
        chart here rather than an empty axis.
      </p>
      <Disclosure className="mt-2" label="why">
        <p className="max-w-prose text-sm leading-relaxed text-muted-foreground">
          Zolli displays the mean loss that FlashML records for each federated
          round. Independent jobs report no model metrics today, so there is
          nothing to chart here rather than an empty axis.
        </p>
        <p className="mt-3 max-w-prose text-sm leading-relaxed text-muted-foreground">
          Per-step curves need the workload to emit them through the event
          ledger, which is a change to the runtime&apos;s protocol package
          rather than to this console.
        </p>
      </Disclosure>
    </section>
  );
}

function PlacementView({
  job,
  tasks,
  attempts,
  now,
  routing,
  onRetryRouting,
  tradeoff,
  onRetryTradeoff,
}: {
  job: JobRecord;
  tasks: JobTask[];
  attempts: ReturnType<typeof deriveAttempts>;
  now: number;
  routing: RoutingPanel;
  onRetryRouting: () => void;
  tradeoff: TradeoffPanel;
  onRetryTradeoff: () => void;
}) {
  // The routing card sits ABOVE the "nothing has run yet" guard on purpose.
  // Where the work went and where it COULD go are different questions, and
  // the second one has an answer from the moment the job exists — a PENDING
  // job with no attempts yet is exactly when "which venues can run this, and
  // why not the others" is the only thing there is to say.
  //
  // The trade-off curve sits directly under it for the same reason and one
  // more: it answers the question the routing card raises. Routing says where
  // this work MAY go; the curve says what going there would actually buy —
  // including, often, nothing.
  const placementCards = (
    <>
      <RoutingCard panel={routing} onRetry={onRetryRouting} />
      <TradeoffCard panel={tradeoff} onRetry={onRetryTradeoff} />
    </>
  );

  if (tasks.length === 0 && attempts.length === 0) {
    return (
      <div className="space-y-6">
        {placementCards}
        <section className="rounded-lg border border-border bg-surface p-6">
          <h2 className="text-sm font-semibold">No placement recorded</h2>
          <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
            {noBreakdownReason(job)}
          </p>
        </section>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {placementCards}

      {attempts.length > 0 && (
        <>
          {/* Two readings of the same events. The topology answers "where is
              the work right now", the swimlanes answer "where has it been" —
              and the topology's scrubber lets you ask the first question
              about any past instant, which is the join between them. */}
          <section className="panel p-4">
            <h2 className="text-sm font-semibold">Where the work is</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Scrub or replay to see the Workspace at any moment of this run.
            </p>
            <div className="mt-4">
              <FleetTopology attempts={attempts} now={now} />
            </div>
          </section>

          <section className="panel p-4">
            <h2 className="text-sm font-semibold">Attempts by Machine</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Reconstructed from the coordinator&apos;s lease and commit
              events.
            </p>
            <div className="mt-4">
              <Swimlanes attempts={attempts} now={now} />
            </div>
          </section>
        </>
      )}

      {tasks.length > 0 && (
        <section className="overflow-hidden rounded-lg border border-border bg-surface">
          <div className="border-b border-border px-4 py-2.5">
            <h2 className="text-sm font-semibold">Tasks</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-left">
              <thead>
                <tr className="border-b border-border">
                  {["Task", "State", "Attempts", "Machine", "Lease ends"].map(
                    (h) => (
                      <th key={h} className="label-caps px-4 py-2 font-medium">
                        {h}
                      </th>
                    )
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {tasks.map((t, i) => (
                  <tr key={`${t.round ?? ""}-${t.task_id}-${i}`}>
                    <td className="px-4 py-2.5 font-mono text-xs">
                      {t.round !== undefined && (
                        <span className="text-muted-foreground">
                          r{t.round}/
                        </span>
                      )}
                      {t.task_id}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs">{t.state}</td>
                    <td className="px-4 py-2.5 font-mono text-xs tabular-nums">
                      {t.attempts}/{t.max_attempts}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                      {t.node_id ?? "—"}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                      {t.deadline
                        ? new Date(t.deadline).toLocaleTimeString()
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}

function SpecCard({ job }: { job: JobRecord }) {
  const s = job.spec;
  if (!s) return null;
  const rows: [string, string][] = [
    ["image", `${s.spec.image.repository}:${s.spec.image.tag}`],
    ["workload", s.spec.workload.type],
    [
      "workers",
      `${s.spec.resources.minimumWorkers}–${s.spec.resources.maximumWorkers}`,
    ],
    ["isolation", s.spec.isolation.tier],
  ];
  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <h2 className="text-sm font-semibold">Spec</h2>
      <dl className="mt-3 space-y-1.5 text-xs">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-4">
            <dt className="label-caps">{k}</dt>
            <dd className="truncate font-mono">{v}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function CancelSection({
  job,
  onCancelled,
}: {
  job: JobRecord;
  onCancelled: (job: JobRecord) => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (TERMINAL.has(job.state)) return null;

  async function confirmCancel() {
    setCancelling(true);
    setError(null);
    try {
      const updated = await cancelJob(job.job_id);
      onCancelled(updated);
      setConfirming(false);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : "Couldn't cancel this job. Try again."
      );
    } finally {
      setCancelling(false);
    }
  }

  return confirming ? (
    <div className="rounded-lg border border-destructive/30 bg-surface p-4">
      <div className="flex items-start gap-2 text-sm text-destructive">
        <Warning className="mt-0.5 h-4 w-4 shrink-0" weight="fill" />
        <span>Cancel this job? It cannot be resumed.</span>
      </div>
      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          disabled={cancelling}
          onClick={confirmCancel}
          className="rounded-md bg-destructive/15 px-3 py-1.5 text-sm text-destructive hover:bg-destructive/25 disabled:opacity-50"
        >
          {cancelling ? "Cancelling…" : "Confirm cancel"}
        </button>
        <button
          type="button"
          disabled={cancelling}
          onClick={() => {
            setConfirming(false);
            setError(null);
          }}
          className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground"
        >
          Keep running
        </button>
      </div>
    </div>
  ) : (
    <button
      type="button"
      onClick={() => setConfirming(true)}
      className="rounded-md border border-border px-3 py-1.5 text-sm text-destructive hover:bg-destructive/10"
    >
      Cancel job
    </button>
  );
}
