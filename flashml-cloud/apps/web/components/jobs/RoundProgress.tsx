"use client";

import { useMemo } from "react";
import { StatTile } from "@/components/ui/stat-tile";
import type { JobRound } from "@/lib/cloud-api";

// The training-progress view for a federated run.
//
// Everything here is arithmetic over real `job_rounds` columns: mean_loss,
// participants, contributors and recorded_at. Nothing is smoothed,
// interpolated or extrapolated. A round that reported no loss leaves a GAP
// in the curve rather than a straight line through it, because joining
// across a missing measurement draws a number nobody recorded.
//
// The previous version rendered loss as a bar scaled to the maximum, which
// inverted the meaning: the worst round got the longest bar and a run that
// was converging nicely looked like it was shrinking away. Loss belongs on
// a descending curve with a real axis.

interface Point {
  round: number;
  loss: number | null;
  participants: number;
  contributors: string[];
  at: number | null;
}

const PAD = { top: 14, right: 12, bottom: 24, left: 46 };
const W = 720;
const H = 210;

/** Shared with `MemberCredits` so both durations on a job page are
 * formatted identically — there is exactly one way this product renders a
 * span of time, not one per component that happens to need it. */
export function fmtDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${String(s % 60).padStart(2, "0")}s`;
}

export function RoundProgress({
  rounds,
  jobStartedAt,
}: {
  rounds: JobRound[];
  jobStartedAt?: string | null;
}) {
  const model = useMemo(() => {
    const pts: Point[] = rounds.map((r) => ({
      round: r.round,
      loss: r.mean_loss,
      participants: r.participants,
      contributors: r.contributors ?? [],
      at: r.recorded_at ? Date.parse(r.recorded_at) : null,
    }));

    const reported = pts.filter(
      (p): p is Point & { loss: number } => p.loss !== null
    );

    const first = reported[0]?.loss ?? null;
    const last = reported[reported.length - 1]?.loss ?? null;

    // Round durations. The first round is measured from the job's own start
    // if we have it; every later one from the previous round's timestamp.
    // Null wherever a timestamp is missing rather than a guessed average.
    const jobStart = jobStartedAt ? Date.parse(jobStartedAt) : null;
    const durations = pts.map((p, i) => {
      const prev = i === 0 ? jobStart : pts[i - 1].at;
      if (p.at === null || prev === null || Number.isNaN(prev)) return null;
      return p.at - prev;
    });

    // Which machines showed up, and for how many rounds. `contributors` has
    // been carried by the API all along and shown nowhere.
    const byNode = new Map<string, number>();
    for (const p of pts) {
      for (const n of p.contributors) byNode.set(n, (byNode.get(n) ?? 0) + 1);
    }
    const contributors = [...byNode.entries()].sort((a, b) => b[1] - a[1]);

    const timed = durations.filter((d): d is number => d !== null);

    return {
      pts,
      reported,
      first,
      last,
      improvement:
        first !== null && last !== null && first !== 0
          ? (first - last) / first
          : null,
      durations,
      totalTime: timed.length ? timed.reduce((a, b) => a + b, 0) : null,
      meanTime: timed.length
        ? timed.reduce((a, b) => a + b, 0) / timed.length
        : null,
      contributors,
    };
  }, [rounds, jobStartedAt]);

  if (rounds.length === 0) return null;

  const { pts, reported, first, last, improvement, durations, totalTime, meanTime, contributors } =
    model;

  // Y domain from the reported values only, padded so the curve does not
  // touch the frame. Deliberately NOT zero-based: a loss moving 0.63 -> 0.23
  // would be a barely visible wiggle against a zero baseline, and the axis
  // labels below carry the real numbers so nothing is hidden.
  const losses = reported.map((p) => p.loss);
  const lo = losses.length ? Math.min(...losses) : 0;
  const hi = losses.length ? Math.max(...losses) : 1;
  const span = hi - lo || Math.abs(hi) || 1;
  const yMin = lo - span * 0.15;
  const yMax = hi + span * 0.15;

  const iw = W - PAD.left - PAD.right;
  const ih = H - PAD.top - PAD.bottom;
  const x = (i: number) =>
    PAD.left + (pts.length === 1 ? iw / 2 : (i / (pts.length - 1)) * iw);
  const y = (v: number) =>
    PAD.top + ih - ((v - yMin) / (yMax - yMin)) * ih;

  // Break the path wherever a round reported no loss. One `d` string with a
  // fresh `M` after every gap: connecting across would invent a value.
  const path = pts
    .map((p, i) => (p.loss === null ? null : `${x(i)},${y(p.loss)}`))
    .reduce<string[]>((acc, coord, i) => {
      if (coord === null) return acc;
      const prevMissing = i === 0 || pts[i - 1].loss === null;
      acc.push(`${prevMissing ? "M" : "L"}${coord}`);
      return acc;
    }, [])
    .join(" ");

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-4">
        <StatTile
          label="Final loss"
          value={last === null ? "—" : last.toFixed(4)}
        />
        <StatTile
          label="Improvement"
          value={
            improvement === null ? "—" : `${(improvement * 100).toFixed(1)}%`
          }
          tone={improvement !== null && improvement > 0 ? "good" : "plain"}
          hint={
            first !== null && last !== null
              ? `${first.toFixed(4)} → ${last.toFixed(4)}`
              : undefined
          }
        />
        <StatTile label="Rounds" value={String(rounds.length)} />
        <StatTile
          label="Mean round"
          value={meanTime === null ? "—" : fmtDuration(meanTime)}
          hint={totalTime === null ? undefined : `${fmtDuration(totalTime)} total`}
        />
      </div>

      <section className="rounded-lg border border-border bg-surface p-4">
        <div className="flex items-baseline justify-between gap-3">
          <h3 className="text-sm font-semibold">Mean loss per round</h3>
          {reported.length < pts.length && (
            <span className="font-mono text-[10px] text-warning-foreground">
              {pts.length - reported.length} round
              {pts.length - reported.length === 1 ? "" : "s"} reported no loss
            </span>
          )}
        </div>

        {reported.length === 0 ? (
          <p className="mt-3 text-xs text-muted-foreground">
            No round reported a loss, so there is no curve to draw.
          </p>
        ) : (
          <svg
            viewBox={`0 0 ${W} ${H}`}
            className="mt-3 w-full"
            role="img"
            aria-label={`Mean loss across ${pts.length} rounds, from ${first?.toFixed(4)} to ${last?.toFixed(4)}`}
          >
            {[0, 0.25, 0.5, 0.75, 1].map((t) => {
              const v = yMax - t * (yMax - yMin);
              const yy = PAD.top + t * ih;
              return (
                <g key={t}>
                  <line
                    x1={PAD.left}
                    x2={W - PAD.right}
                    y1={yy}
                    y2={yy}
                    stroke="var(--border)"
                    strokeWidth="1"
                  />
                  <text
                    x={PAD.left - 8}
                    y={yy + 3}
                    textAnchor="end"
                    className="fill-[var(--muted-foreground)] font-mono"
                    fontSize="9"
                  >
                    {v.toFixed(3)}
                  </text>
                </g>
              );
            })}

            <path
              d={path}
              fill="none"
              stroke="var(--primary)"
              strokeWidth="2"
              strokeLinejoin="round"
              strokeLinecap="round"
            />

            {pts.map((p, i) =>
              p.loss === null ? (
                // A missing measurement is marked, not skipped silently.
                <text
                  key={p.round}
                  x={x(i)}
                  y={PAD.top + ih / 2}
                  textAnchor="middle"
                  className="fill-[var(--warning)] font-mono"
                  fontSize="10"
                >
                  ?
                </text>
              ) : (
                <circle
                  key={p.round}
                  cx={x(i)}
                  cy={y(p.loss)}
                  r="3"
                  fill="var(--background)"
                  stroke="var(--primary)"
                  strokeWidth="2"
                >
                  <title>{`round ${p.round}: ${p.loss.toFixed(4)}`}</title>
                </circle>
              )
            )}

            {pts.map((p, i) => (
              <text
                key={p.round}
                x={x(i)}
                y={H - 7}
                textAnchor="middle"
                className="fill-[var(--muted-foreground)] font-mono"
                fontSize="9"
              >
                r{p.round}
              </text>
            ))}
          </svg>
        )}
      </section>

      <section className="overflow-hidden rounded-lg border border-border bg-surface">
        <div className="border-b border-border px-4 py-2.5">
          <h3 className="text-sm font-semibold">Per round</h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left">
            <thead>
              <tr className="border-b border-border">
                {["Round", "Loss", "Change", "Took", "Contributors"].map((h) => (
                  <th key={h} className="label-caps px-4 py-2 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {pts.map((p, i) => {
                // Delta against the previous round that actually reported.
                // Comparing against a null would either fabricate a change
                // or hide a real one.
                //
                // When that skips over an unreported round the delta spans
                // more than one round, and the column heading says "Change",
                // which a reader will take as round-over-round. So name the
                // round it is actually measured against whenever it is not
                // the immediately preceding one.
                let delta: number | null = null;
                let against: number | null = null;
                for (let k = i - 1; k >= 0; k--) {
                  if (pts[k].loss !== null && p.loss !== null) {
                    delta = p.loss - (pts[k].loss as number);
                    against = pts[k].round;
                    break;
                  }
                }
                const spansGap = against !== null && against !== p.round - 1;
                return (
                  <tr key={p.round}>
                    <td className="px-4 py-2.5 font-mono text-xs">r{p.round}</td>
                    <td className="px-4 py-2.5 font-mono text-xs tabular-nums">
                      {p.loss === null ? (
                        <span className="text-muted-foreground">—</span>
                      ) : (
                        p.loss.toFixed(4)
                      )}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs tabular-nums">
                      {delta === null ? (
                        <span className="text-muted-foreground">—</span>
                      ) : (
                        <>
                          <span
                            className={
                              delta < 0
                                ? "text-[var(--node-green)]"
                                : "text-warning-foreground"
                            }
                          >
                            {delta > 0 ? "+" : ""}
                            {delta.toFixed(4)}
                          </span>
                          {spansGap && (
                            <span className="ml-1.5 text-[10px] text-muted-foreground">
                              vs r{against}
                            </span>
                          )}
                        </>
                      )}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs tabular-nums text-muted-foreground">
                      {durations[i] === null ? "—" : fmtDuration(durations[i]!)}
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex flex-wrap gap-1">
                        {p.contributors.length === 0 ? (
                          <span className="font-mono text-xs text-muted-foreground">
                            {p.participants} (ids not recorded)
                          </span>
                        ) : (
                          p.contributors.map((n) => (
                            <span
                              key={n}
                              className="rounded-sm border border-border bg-surface-elevated px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
                            >
                              {n}
                            </span>
                          ))
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {contributors.length > 0 && (
        <section className="rounded-lg border border-border bg-surface p-4">
          <h3 className="text-sm font-semibold">Machines that contributed</h3>
          <div className="mt-3 space-y-2">
            {contributors.map(([node, count]) => (
              <div key={node} className="flex items-center gap-3">
                <span className="w-44 shrink-0 truncate font-mono text-xs" title={node}>
                  {node}
                </span>
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full bg-[var(--node-green)]"
                    style={{ width: `${(count / rounds.length) * 100}%` }}
                  />
                </div>
                <span className="w-24 shrink-0 text-right font-mono text-xs tabular-nums text-muted-foreground">
                  {count}/{rounds.length} rounds
                </span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
