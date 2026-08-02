"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowLeft, DownloadSimple, Warning } from "@phosphor-icons/react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { formatBytes } from "@/lib/utils";
import {
  ApiError,
  NotAuthenticated,
  NotFound,
  cancelJob,
  fetchJobArtifact,
  getJob,
  jobArtifactKey,
  listJobRounds,
  type JobRecord,
  type JobRound,
} from "@/lib/cloud-api";
import { StateBadge } from "@/components/jobs/StateBadge";

const TERMINAL_STATES = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);
const POLL_MS = 2500;

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
  const [state, setState] = useState<LoadState>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const load = useCallback(() => {
    getJob(jobId)
      .then((j) => {
        setJob(j);
        setState("ready");
        setErrorMessage(null);
        // Best-effort: round history is only ever non-empty for a
        // federated job, and its absence must never block the rest of the
        // page from rendering. A NotAuthenticated here is still routed to
        // sign-in (the JWT just expired between the two calls); any other
        // failure — including NotFound, which can legitimately happen for
        // a non-federated job depending on how the API models it — just
        // leaves `rounds` empty, which the "only render when there is
        // round history" rule already treats as "nothing to show".
        listJobRounds(jobId)
          .then(setRounds)
          .catch((err) => {
            if (err instanceof NotAuthenticated) {
              router.push(`/sign-in?next=/jobs/${jobId}`);
            }
          });
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
  }, [jobId, router]);

  useEffect(() => {
    load();
  }, [load]);

  // Stop polling once the job has reached a terminal state — a finished
  // job's status never changes again, so continuing to poll would just
  // hammer the API for nothing.
  useEffect(() => {
    if (job && TERMINAL_STATES.has(job.state)) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [job, load]);

  if (state === "loading") {
    return (
      <div className="max-w-4xl mx-auto px-4 sm:px-6 py-8 space-y-6">
        <Card>
          <CardContent className="h-32 animate-pulse bg-muted/30 rounded-lg" />
        </Card>
      </div>
    );
  }

  if (state === "not-found") {
    return (
      <div className="max-w-4xl mx-auto px-4 sm:px-6 py-8 space-y-6">
        <Card>
          <CardContent className="flex flex-col items-center text-center gap-3 py-10">
            <Warning className="w-6 h-6 text-muted-foreground" weight="fill" />
            <p className="text-sm text-muted-foreground">
              This job doesn&apos;t exist, or isn&apos;t yours.
            </p>
            <Link href="/jobs" className="text-cyan text-sm underline underline-offset-2">
              Back to jobs
            </Link>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (state === "error" || !job) {
    return (
      <div className="max-w-4xl mx-auto px-4 sm:px-6 py-8 space-y-6">
        <Card>
          <CardContent className="flex flex-col items-center text-center gap-3 py-10">
            <Warning className="w-6 h-6 text-destructive" weight="fill" />
            <p className="text-sm text-muted-foreground">{errorMessage}</p>
            <Button type="button" variant="outline" size="sm" onClick={load}>
              Try again
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 py-8 space-y-6">
      <Link
        href="/jobs"
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="w-3.5 h-3.5" /> Jobs
      </Link>

      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold font-mono truncate">
            {job.spec.metadata.name}
          </h1>
          <p className="text-xs text-muted-foreground font-mono mt-1 truncate">
            {job.job_id} · {job.backend} · {job.deployment_profile}
            {job.runtime_execution_id && <> · {job.runtime_execution_id}</>}
          </p>
        </div>
        <StateBadge state={job.state} />
      </div>

      <div className="grid gap-3 grid-cols-2 sm:grid-cols-3">
        <Row label="created" value={new Date(job.created_at).toLocaleString()} />
        {job.finished_at && (
          <Row label="finished" value={new Date(job.finished_at).toLocaleString()} />
        )}
        <Row label="artifacts" value={String(job.artifacts.length)} />
      </div>

      {job.error && (
        <Card className="border-destructive/30">
          <CardHeader>
            <CardTitle className="text-sm font-mono text-destructive">Error</CardTitle>
          </CardHeader>
          <CardContent className="text-sm font-mono text-destructive">
            {job.error}
          </CardContent>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-mono">Artifacts</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {job.artifacts.map((a) => (
              <ArtifactRow key={a.uri} jobId={job.job_id} artifact={a} />
            ))}
            {job.artifacts.length === 0 && (
              <p className="text-xs font-mono text-muted-foreground">
                {TERMINAL_STATES.has(job.state)
                  ? "no artifacts were produced"
                  : "artifacts appear once the job produces output"}
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-mono">Spec</CardTitle>
          </CardHeader>
          <CardContent className="text-xs font-mono space-y-1.5">
            <SpecRow k="image" v={`${job.spec.spec.image.repository}:${job.spec.spec.image.tag}`} />
            <SpecRow k="workload" v={job.spec.spec.workload.type} />
            <SpecRow
              k="workers"
              v={`${job.spec.spec.resources.minimumWorkers}–${job.spec.spec.resources.maximumWorkers}`}
            />
            <SpecRow k="isolation" v={job.spec.spec.isolation.tier} />
          </CardContent>
        </Card>
      </div>

      {/* Only federated jobs ever have rounds — `GET /rounds` returns an
          empty list for every independent job, and an empty "Rounds" card
          on an independent job's page would misread as "stuck" or
          "failed". Render the section only once there is real history. */}
      {rounds.length > 0 && <RoundsSection rounds={rounds} />}

      <CancelSection job={job} onCancelled={setJob} />
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

  if (TERMINAL_STATES.has(job.state)) return null;

  async function confirmCancel() {
    setCancelling(true);
    setError(null);
    try {
      const updated = await cancelJob(job.job_id);
      onCancelled(updated);
      setConfirming(false);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : "Couldn't cancel this job. Try again."
      );
    } finally {
      setCancelling(false);
    }
  }

  return confirming ? (
    <Card className="border-destructive/30">
      <CardContent className="flex flex-col gap-3 py-4">
        <div className="flex items-start gap-2 text-sm text-destructive">
          <Warning className="w-4 h-4 shrink-0 mt-0.5" weight="fill" />
          <span>Cancel this job? It cannot be resumed.</span>
        </div>
        {error && <p className="text-xs text-destructive">{error}</p>}
        <div className="flex gap-2">
          <Button
            type="button"
            variant="destructive"
            size="sm"
            disabled={cancelling}
            onClick={confirmCancel}
          >
            {cancelling ? "Cancelling…" : "Confirm cancel"}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={cancelling}
            onClick={() => {
              setConfirming(false);
              setError(null);
            }}
          >
            Keep running
          </Button>
        </div>
      </CardContent>
    </Card>
  ) : (
    <Button
      type="button"
      variant="outline"
      className="text-destructive hover:text-destructive"
      onClick={() => setConfirming(true)}
    >
      Cancel job
    </Button>
  );
}

/** `mean_loss` is nullable end to end — the API leaves it null when a round
 * completed without reporting a loss, and this must render as absent, never
 * as `0` or an interpolated value. Its only use here beyond display is as
 * the denominator for the trend bar's width, computed from the rounds that
 * actually reported a number — nothing is smoothed or guessed. */
function RoundsSection({ rounds }: { rounds: JobRound[] }) {
  const reportedLosses = rounds
    .map((r) => r.mean_loss)
    .filter((v): v is number => v !== null);
  const maxLoss = reportedLosses.length > 0 ? Math.max(...reportedLosses) : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-mono">
          Rounds <span className="text-muted-foreground">({rounds.length})</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-0">
        {rounds.map((r) => (
          <RoundRow key={r.round} round={r} maxLoss={maxLoss} />
        ))}
      </CardContent>
    </Card>
  );
}

function RoundRow({ round, maxLoss }: { round: JobRound; maxLoss: number | null }) {
  const hasLoss = round.mean_loss !== null;

  return (
    <div className="flex flex-col gap-1.5 py-2.5 border-b border-border/50 last:border-0">
      <div className="flex items-center justify-between gap-3 text-xs font-mono">
        <span className="text-foreground">round {round.round}</span>
        <span className="text-muted-foreground">
          {round.participants} participant{round.participants === 1 ? "" : "s"}
        </span>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-xs font-mono text-muted-foreground w-24 shrink-0">
          {hasLoss ? `loss ${round.mean_loss!.toFixed(4)}` : "loss —"}
        </span>
        {/* The trend visual: a bar scaled against the largest reported loss
            across this job's rounds. Only drawn for a round that actually
            reported a number — an absent mean_loss gets no bar at all,
            never a zero-width or full-width stand-in that would imply a
            value. */}
        {hasLoss && maxLoss !== null && (
          <div className="h-1.5 flex-1 rounded-full bg-muted overflow-hidden">
            <div
              className="h-full bg-cyan"
              style={{
                width: `${maxLoss > 0 ? (round.mean_loss! / maxLoss) * 100 : 100}%`,
              }}
            />
          </div>
        )}
      </div>

      {/* Which machines contributed — an explicit acceptance criterion, so
          it is rendered directly in the row, not behind a click. */}
      <div className="flex flex-wrap gap-1">
        {round.contributors.map((nodeId) => (
          <Badge
            key={nodeId}
            variant="outline"
            className="font-mono text-[10px] text-muted-foreground"
          >
            {nodeId}
          </Badge>
        ))}
      </div>
    </div>
  );
}

function ArtifactRow({
  jobId,
  artifact,
}: {
  jobId: string;
  artifact: JobRecord["artifacts"][number];
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
      <div className="flex items-center justify-between gap-3 text-xs font-mono">
        <span className="text-cyan truncate">{artifact.uri}</span>
        <span className="flex items-center gap-2 shrink-0">
          <span className="text-muted-foreground">
            {artifact.backend} · {formatBytes(artifact.size_bytes)}
          </span>
          {key && (
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              onClick={download}
              disabled={downloading}
              aria-label="Download artifact"
            >
              <DownloadSimple className={downloading ? "animate-pulse" : ""} />
            </Button>
          )}
        </span>
      </div>
      {error && <span className="text-[10px] text-destructive">{error}</span>}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardContent className="py-3">
        <div className="text-sm font-mono text-foreground truncate">{value}</div>
        <div className="text-xs uppercase tracking-wide text-muted-foreground">
          {label}
        </div>
      </CardContent>
    </Card>
  );
}

function SpecRow({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-4">
      <span className="text-muted-foreground uppercase text-[10px] tracking-wide shrink-0">{k}</span>
      <span className="truncate">{v}</span>
    </div>
  );
}
