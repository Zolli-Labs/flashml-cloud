"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, DownloadSimple, Warning } from "@phosphor-icons/react";
import { StateBadge } from "@/components/jobs/StateBadge";
import { Swimlanes } from "@/components/jobs/Swimlanes";
import { FleetTopology } from "@/components/jobs/FleetTopology";
import { RoundProgress } from "@/components/jobs/RoundProgress";
import { MemberCredits } from "@/components/jobs/MemberCredits";
import { useWorkspaceHint } from "@/components/shell/WorkspaceHint";
import { formatBytes } from "@/lib/utils";
import {
  deriveAttempts,
  deriveProgress,
  deriveStallReason,
} from "@/lib/job-activity";
import { workspacePath } from "@/lib/workspace-scope";
import {
  ApiError,
  NotAuthenticated,
  NotFound,
  cancelJob,
  fetchJobArtifact,
  getJob,
  jobArtifactKey,
  listJobContributions,
  listJobEvents,
  listJobRounds,
  listJobTasks,
  type JobContribution,
  type JobEvent,
  type JobRecord,
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

const TERMINAL = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);
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
  const [state, setState] = useState<LoadState>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [now, setNow] = useState(() => Date.now());

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
    listJobTasks(jobId).then(setTasks).catch(soft);
    listJobEvents(jobId).then(setEvents).catch(soft);
    listJobContributions(jobId).then(setContributions).catch(soft);
  }, [jobId, router]);

  useEffect(() => {
    load();
  }, [load]);

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
  const backLabel = job.pool_id != null ? "Jobs" : "Crews";

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
          <ProgressView job={job} rounds={rounds} contributions={contributions} />
        )}
        {activeView === "placement" && (
          <PlacementView job={job} tasks={tasks} attempts={attempts} now={now} />
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
}: {
  job: JobRecord;
  rounds: JobRound[];
  contributions: JobContribution[];
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

      {rounds.length > 0 ? (
        <RoundProgress rounds={rounds} jobStartedAt={job.created_at} />
      ) : (
        <NoMetrics />
      )}

      {/* Renders nothing for a job with no recorded contributions — most
          jobs, since this data only exists for a pool job. */}
      <MemberCredits contributions={contributions} />

      <ArtifactsCard job={job} />
      {job.spec && <SpecCard job={job} />}
    </div>
  );
}

/** The honest empty state. The only model metric the system records is
 * `mean_loss` per federated round. Rather than draw an empty chart frame,
 * say what is missing and what would fill it. */
function NoMetrics() {
  return (
    <section className="rounded-lg border border-border bg-surface p-6">
      <h2 className="text-sm font-semibold">No training metrics for this job</h2>
      <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
        FlashML records a mean loss per round for federated runs. Independent
        jobs report no model metrics today, so there is nothing to chart here
        rather than an empty axis.
      </p>
      <p className="mt-3 max-w-prose text-sm leading-relaxed text-muted-foreground">
        Per-step curves need the workload to emit them through the event
        ledger, which is a change to the runtime&apos;s protocol package
        rather than to this console.
      </p>
    </section>
  );
}

function PlacementView({
  job,
  tasks,
  attempts,
  now,
}: {
  job: JobRecord;
  tasks: JobTask[];
  attempts: ReturnType<typeof deriveAttempts>;
  now: number;
}) {
  if (tasks.length === 0 && attempts.length === 0) {
    return (
      <section className="rounded-lg border border-border bg-surface p-6">
        <h2 className="text-sm font-semibold">No placement recorded</h2>
        <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
          {noBreakdownReason(job)}
        </p>
      </section>
    );
  }

  return (
    <div className="space-y-6">
      {attempts.length > 0 && (
        <>
          {/* Two readings of the same events. The topology answers "where is
              the work right now", the swimlanes answer "where has it been" —
              and the topology's scrubber lets you ask the first question
              about any past instant, which is the join between them. */}
          <section className="panel p-4">
            <h2 className="text-sm font-semibold">Where the work is</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Scrub or replay to see the Crew at any moment of this run.
            </p>
            <div className="mt-4">
              <FleetTopology attempts={attempts} now={now} />
            </div>
          </section>

          <section className="panel p-4">
            <h2 className="text-sm font-semibold">Attempts by Zolli</h2>
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
                  {["Task", "State", "Attempts", "Zolli", "Lease ends"].map(
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

function ledgerTone(type: string): string {
  if (
    type.includes("FAILED") ||
    type.includes("REJECTED") ||
    type.includes("EXPIRED") ||
    type.includes("LOST") ||
    type.includes("FROZEN")
  ) {
    return "text-warning-foreground";
  }
  if (
    type.includes("ACCEPTED") ||
    type.includes("SUCCEEDED") ||
    type.includes("COMMITTED")
  ) {
    return "text-[var(--node-green)]";
  }
  return "text-muted-foreground";
}

function LedgerView({ events }: { events: JobEvent[] }) {
  if (events.length === 0) {
    return (
      <section className="rounded-lg border border-border bg-surface p-6">
        <h2 className="text-sm font-semibold">No events recorded</h2>
        <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
          The coordinator has written nothing for this job yet.
        </p>
      </section>
    );
  }

  // Newest first: when something has just gone wrong, it is at the top.
  const rows = [...events].reverse();

  return (
    <section className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <h2 className="text-sm font-semibold">Event ledger</h2>
        <span className="font-mono text-xs text-muted-foreground">
          {events.length} events
        </span>
      </div>
      <ul className="divide-y divide-border">
        {rows.map((e, i) => (
          <li key={`${e.timestamp}-${i}`} className="px-4 py-2.5">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="font-mono text-xs tabular-nums text-muted-foreground">
                {new Date(e.timestamp).toLocaleTimeString()}
              </span>
              {e.round !== undefined && (
                <span className="font-mono text-[10px] text-muted-foreground">
                  r{e.round}
                </span>
              )}
              <span className={`font-mono text-xs ${ledgerTone(e.type)}`}>
                {e.type}
              </span>
              {typeof e.data?.node_id === "string" && (
                <span className="font-mono text-[11px] text-muted-foreground">
                  {e.data.node_id}
                </span>
              )}
            </div>
            {/* The coordinator's own words, verbatim. Recovery decisions in
                particular must never be paraphrased: the policy's reason IS
                the explanation, and rewriting it would be inventing one. */}
            {e.message && (
              <p className="mt-1 font-mono text-[11px] leading-relaxed text-muted-foreground">
                {e.message}
              </p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function ArtifactsCard({ job }: { job: JobRecord }) {
  const artifacts = job.artifacts ?? [];
  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <h2 className="text-sm font-semibold">Artifacts</h2>
      <div className="mt-3 space-y-2">
        {artifacts.map((a) => (
          <ArtifactRow key={a.uri} jobId={job.job_id} artifact={a} />
        ))}
        {artifacts.length === 0 && (
          <p className="font-mono text-xs text-muted-foreground">
            {TERMINAL.has(job.state)
              ? "no artifacts were produced"
              : "artifacts appear once the job produces output"}
          </p>
        )}
      </div>
    </section>
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

function ArtifactRow({
  jobId,
  artifact,
}: {
  jobId: string;
  artifact: NonNullable<JobRecord["artifacts"]>[number];
}) {
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = jobArtifactKey(jobId, artifact.uri);

  async function download() {
    if (!key) return;
    setDownloading(true);
    setError(null);
    try {
      const blob = await fetchJobArtifact(jobId, key);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = key.split("/").pop() || "artifact";
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      setError("Couldn't download this artifact.");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex items-center justify-between gap-3 font-mono text-xs">
        <span className="truncate text-brand-foreground">{artifact.uri}</span>
        <span className="flex shrink-0 items-center gap-2">
          <span className="text-muted-foreground">
            {artifact.backend} · {formatBytes(artifact.size_bytes)}
          </span>
          {key && (
            <button
              type="button"
              onClick={download}
              disabled={downloading}
              aria-label="Download artifact"
              className="rounded p-1 hover:bg-surface-2 disabled:opacity-50"
            >
              <DownloadSimple className={downloading ? "animate-pulse" : ""} />
            </button>
          )}
        </span>
      </div>
      {error && <span className="text-[10px] text-destructive">{error}</span>}
    </div>
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
