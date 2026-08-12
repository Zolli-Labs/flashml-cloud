"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Info, Warning } from "@phosphor-icons/react";
import {
  NotAuthenticated,
  getMyMetrics,
  type PlatformMetrics,
} from "@/lib/cloud-api";
import {
  summariseMetrics,
  type CountStat,
  type ReliabilityStat,
} from "@/lib/platform-metrics";

// The reliability page. It exists to back one specific claim this product
// makes about itself — that unreliable volunteer machines cost real,
// measurable work, and that FlashML measures it rather than asserting it.
//
// The API's contract for this route (`GET /v1alpha1/me/metrics`) is
// explicit that every derived field — goodput, lost task time, MTTR, MTTD —
// really will be null for every account right now, because the ledger
// events that would derive them are not recorded yet. A page about proving
// a reliability claim that quietly turns "not measured" into "0" would be
// lying in the specific way this page exists to prevent, so every number
// below either came from `lib/platform-metrics.ts` as a real measurement or
// is rendered as an explicit "not measured yet" — never as a fabricated
// zero, a bare dash, or an empty chart standing in for missing data.

const WINDOW_OPTIONS = [7, 30, 90] as const;

export default function MetricsPage() {
  const router = useRouter();
  const [windowDays, setWindowDays] = useState<number>(30);
  const [metrics, setMetrics] = useState<PlatformMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    (days: number) => {
      getMyMetrics(days)
        .then((m) => {
          setMetrics(m);
          setError(null);
        })
        .catch((err) => {
          if (err instanceof NotAuthenticated) {
            router.push("/sign-in?next=/metrics");
            return;
          }
          setError(
            err instanceof Error ? err.message : "Couldn't load metrics."
          );
        });
    },
    [router]
  );

  useEffect(() => {
    load(windowDays);
  }, [load, windowDays]);

  const summary = metrics ? summariseMetrics(metrics) : null;

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="title">Reliability</h1>
          <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
            What this account&apos;s jobs actually got, measured from the
            coordinator&apos;s own event ledger &mdash; not a claim, a
            count.
          </p>
        </div>
        <WindowPicker value={windowDays} onChange={setWindowDays} />
      </div>

      {error && (
        <div className="mt-6 flex items-start gap-2 rounded-lg border border-destructive/30 bg-surface p-4 text-sm text-destructive">
          <Warning className="mt-0.5 h-4 w-4 shrink-0" weight="fill" />
          <span>{error}</span>
        </div>
      )}

      {summary && (
        <>
          <section className="mt-6">
            <h2 className="label-caps">
              Jobs, last {summary.windowDays} days
            </h2>
            <div className="mt-2 grid gap-3 sm:grid-cols-4">
              {summary.jobCounts.map((c) => (
                <CountTile key={c.label} stat={c} />
              ))}
            </div>
          </section>

          <section className="mt-6">
            <h2 className="label-caps">Tasks</h2>
            <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {summary.taskCounts.map((c) => (
                <CountTile key={c.label} stat={c} />
              ))}
              <CountTile
                stat={{
                  label: "Machines contributing",
                  value: summary.machinesContributing,
                }}
              />
            </div>
          </section>

          <section className="mt-8">
            <h2 className="label-caps">What unreliable machines cost</h2>
            <p className="mt-1.5 max-w-prose text-xs leading-relaxed text-muted-foreground">
              Goodput is accepted work over <em>resolved</em> work &mdash;
              attempts that actually reached an end &mdash; and the gap is
              what retries and dead machines spent. Work still in flight is
              in neither number: counting it as a failure would drag the
              figure down for being busy. Recovery timing says
              how fast the coordinator noticed and replaced a lost machine.
              Below, an unfilled card means exactly what it says: FlashML
              has not measured that number for this account yet, not that
              the answer is zero.
            </p>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <ReliabilityCard stat={summary.goodput} />
              <ReliabilityCard stat={summary.lostTaskTime} />
              <ReliabilityCard stat={summary.mttr} />
              <ReliabilityCard stat={summary.mttd} />
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function WindowPicker({
  value,
  onChange,
}: {
  value: number;
  onChange: (days: number) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Time window"
      className="inline-flex rounded-md border border-border bg-surface p-0.5"
    >
      {WINDOW_OPTIONS.map((days) => (
        <button
          key={days}
          type="button"
          onClick={() => onChange(days)}
          aria-current={value === days ? "true" : undefined}
          className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
            value === days
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          {days}d
        </button>
      ))}
    </div>
  );
}

function CountTile({ stat }: { stat: CountStat }) {
  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3.5">
      <div className="metric-value text-2xl">{stat.value}</div>
      <div className="label-caps mt-1">{stat.label}</div>
    </div>
  );
}

/** A measured figure and an unmeasured one are drawn deliberately
 * differently — not the same card with a greyed-out number. A dimmed
 * number still LOOKS like a number; this instead swaps in an explanatory
 * sentence, so "not measured yet" cannot be mistaken for "measured, and
 * it's low". */
function ReliabilityCard({ stat }: { stat: ReliabilityStat }) {
  if (!stat.measured) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-surface px-4 py-3.5">
        <div className="label-caps">{stat.label}</div>
        <p className="mt-2 flex items-start gap-1.5 text-sm text-muted-foreground">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
          <span>{stat.display}</span>
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3.5">
      <div className="label-caps">{stat.label}</div>
      <div className="metric-value mt-1 text-2xl">{stat.display}</div>
    </div>
  );
}
