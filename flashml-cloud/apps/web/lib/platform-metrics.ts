/** Turning `GET /v1alpha1/me/metrics` into something a page can render.
 *
 * The logic lives here rather than in the component for the same reason
 * `lib/job-result.ts` does it for the job-result payload: it is the part
 * worth testing, and `vitest.config.ts` only collects `**\/*.test.ts` —
 * a `.tsx` component gets no coverage at all, so every decision that could
 * be wrong has to live in a module a test can reach.
 *
 * This page exists to back a claim the product makes about its own
 * reliability, which makes the one rule here stricter than usual: a metric
 * this account has not actually measured yet must say so in words, and must
 * never be presented as a real zero, a bare dash, or (worse) silently
 * dropped. `jobs_total`, `tasks_attempted` and friends are plain counts —
 * `0` for them is a true, complete answer ("nothing happened in this
 * window") and is rendered as the number 0 with no qualification. The
 * derived reliability figures (goodput, lost task time, MTTR, MTTD) are a
 * different kind of field: each is independently nullable, and every one of
 * them really will be null in production for now, because the events needed
 * to derive them do not exist yet. That null must survive all the way to a
 * sentence, not a chart with nothing in it and not a coerced 0.
 */

import type { PlatformMetrics } from "./cloud-api";

/** The generic explanation for a derived stat the platform has not
 * computed yet. Exported so the page and this module's own tests agree on
 * the exact copy — a UI that silently drifted from "not measured yet" to
 * some other phrasing would be exactly the kind of quiet regression this
 * page cannot afford. */
export const NOT_MEASURED = "Not measured yet";

/** Goodput's more specific null reason: the API's own contract for
 * `goodput_ratio` is "null when attempted is 0" — there is nothing to
 * divide, which is a real and different situation from "not instrumented
 * yet", and worth saying so rather than flattening both into the same
 * generic sentence. */
const NO_ATTEMPTS = "No task attempts in this window";

export interface CountStat {
  label: string;
  /** A plain tally. 0 is a genuine, complete answer here and is never
   * treated as "unmeasured" — see the module docstring. */
  value: number;
}

/** One derived reliability figure. `measured` is the field the UI branches
 * on; `display` carries either the formatted value (when `measured`) or the
 * reason it is absent (when not) — always one or the other, never a bare
 * fallback that leaves the caller guessing which. */
export interface ReliabilityStat {
  label: string;
  measured: boolean;
  display: string;
}

export interface MetricsSummary {
  windowDays: number;
  jobCounts: CountStat[];
  taskCounts: CountStat[];
  machinesContributing: number;
  goodput: ReliabilityStat;
  lostTaskTime: ReliabilityStat;
  mttr: ReliabilityStat;
  mttd: ReliabilityStat;
}

/** `0.914` -> `"91.4%"`, `1` -> `"100%"` (no trailing `.0`), `0` -> `"0%"`.
 * `goodput_ratio` travels as a 0..1 fraction; this is the one place it
 * becomes the percentage the page actually shows. */
function formatPercent(ratio: number): string {
  const pct = Math.round(ratio * 1000) / 10; // one decimal place
  return `${Number.isInteger(pct) ? pct.toFixed(0) : pct.toFixed(1)}%`;
}

/** Whole seconds into a short duration string: `8` -> `"8s"`,
 * `135` -> `"2m 15s"`, `120` -> `"2m"` (a zero remainder is dropped rather
 * than printed as noise), `3661` -> `"1h 1m"`. These four fields (lost task
 * time, MTTR, MTTD) can plausibly range from single-digit seconds to hours
 * of aggregate lost time, so the format widens itself rather than assuming
 * one scale fits all of them. */
function formatDurationSeconds(totalSeconds: number): string {
  const s = Math.round(totalSeconds);
  if (s < 60) return `${s}s`;

  const totalMinutes = Math.floor(s / 60);
  const remainderSeconds = s % 60;
  if (totalMinutes < 60) {
    return remainderSeconds > 0
      ? `${totalMinutes}m ${remainderSeconds}s`
      : `${totalMinutes}m`;
  }

  const hours = Math.floor(totalMinutes / 60);
  const remainderMinutes = totalMinutes % 60;
  return remainderMinutes > 0 ? `${hours}h ${remainderMinutes}m` : `${hours}h`;
}

/** Builds one `ReliabilityStat`. `raw === null` is the only branch that
 * matters: everything else is real, measured data, including 0. */
function reliabilityStat(
  label: string,
  raw: number | null,
  format: (n: number) => string,
  unmeasuredReason: string
): ReliabilityStat {
  if (raw === null) {
    return { label, measured: false, display: unmeasuredReason };
  }
  return { label, measured: true, display: format(raw) };
}

export function summariseMetrics(payload: PlatformMetrics): MetricsSummary {
  return {
    windowDays: payload.window_days,
    jobCounts: [
      { label: "Total", value: payload.jobs_total },
      { label: "Succeeded", value: payload.jobs_succeeded },
      { label: "Partial", value: payload.jobs_partial },
      { label: "Failed", value: payload.jobs_failed },
      // The remainder, shown explicitly so the outcomes add up to Total.
      //
      // `jobs_total` is always >= succeeded + partial + failed: the rest
      // are jobs still running, plus jobs that finished while nobody had
      // the console open, since the API only records a terminal state it
      // has actually observed. Four tiles that visibly fail to sum invite
      // the reader to conclude the page is broken — the one conclusion a
      // page whose entire purpose is to be believed cannot afford.
      //
      // Clamped at 0: the counts come from separate queries and could in
      // principle disagree, and "-3 in flight" is a bug report rather than
      // information.
      {
        label: "In flight",
        value: Math.max(
          0,
          payload.jobs_total -
            payload.jobs_succeeded -
            payload.jobs_partial -
            payload.jobs_failed
        ),
      },
    ],
    taskCounts: [
      { label: "Attempted", value: payload.tasks_attempted },
      { label: "Accepted", value: payload.tasks_accepted },
    ],
    machinesContributing: payload.machines_contributing,
    goodput: reliabilityStat(
      "Goodput",
      payload.goodput_ratio,
      formatPercent,
      // See NO_ATTEMPTS above: a null ratio with zero attempts is math,
      // not a gap in instrumentation, so it earns its own sentence.
      payload.tasks_attempted === 0 ? NO_ATTEMPTS : NOT_MEASURED
    ),
    lostTaskTime: reliabilityStat(
      "Lost task time",
      payload.lost_task_seconds,
      formatDurationSeconds,
      NOT_MEASURED
    ),
    mttr: reliabilityStat(
      "Mean time to recovery",
      payload.mttr_seconds,
      formatDurationSeconds,
      NOT_MEASURED
    ),
    mttd: reliabilityStat(
      "Mean time to detect",
      payload.mttd_seconds,
      formatDurationSeconds,
      NOT_MEASURED
    ),
  };
}
