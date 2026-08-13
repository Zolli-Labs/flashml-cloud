"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Info } from "@phosphor-icons/react";
import { PageHeader } from "@/components/shell/PageHeader";
import { PageShell } from "@/components/shell/PageShell";
import { StatePanel } from "@/components/shell/StatePanel";
import { resolvePanel } from "@/lib/console/panel-state";
import {
  NotAuthenticated,
  getMyMetrics,
  type PlatformMetrics,
} from "@/lib/cloud-api";
import {
  summariseMetrics,
  type CountStat,
  type MetricsSummary,
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
  // A read is in flight from the first render, not from the first effect:
  // the page mounts and immediately fetches, so a `false` here would render
  // one frame of "could not read this" before the request has been made.
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // `load` only ever settles the flags — it never raises them. Raising
  // `loading` synchronously in here made the mount effect write state during
  // its own pass (`react-hooks/set-state-in-effect`), which is a real double
  // render on a page that refetches on every window change, not a lint
  // nitpick. The two places a read STARTS are `useState(true)` above and the
  // event handlers below, and both are outside an effect.
  const load = useCallback(
    (days: number) => {
      getMyMetrics(days)
        .then((m) => {
          setMetrics(m);
          setError(null);
          setLoading(false);
        })
        .catch((err) => {
          if (err instanceof NotAuthenticated) {
            // Stay in `loading` on purpose: this navigates away, and
            // flipping to an error first flashes a failure over a page that
            // is only going to sign-in.
            router.push("/sign-in?next=/metrics");
            return;
          }
          setError(
            err instanceof Error ? err.message : "Couldn't load metrics."
          );
          setLoading(false);
        });
    },
    [router]
  );

  useEffect(() => {
    load(windowDays);
  }, [load, windowDays]);

  /** Change the window. An event handler, so raising the flags here is both
   * allowed and the whole point: this render — the one caused by the click —
   * already shows loading, so the previous window's figures are never on
   * screen under the new window's label. `panelRead` puts `loading` ahead of
   * `data` for the same reason, so the stale payload in `metrics` cannot
   * render while the next read is in flight.
   *
   * The equality guard is load-bearing. `windowDays` is what the effect
   * watches, so re-picking the window already selected would raise `loading`
   * and then never fire a read to lower it — a skeleton with nothing behind
   * it, forever. */
  function chooseWindow(days: number) {
    if (days === windowDays) return;
    setLoading(true);
    setError(null);
    setWindowDays(days);
  }

  /** Re-read the window already selected, for the failed-read retry. Calls
   * `load` directly because `windowDays` does not change, so the effect
   * above will not fire. */
  function retry() {
    setLoading(true);
    setError(null);
    load(windowDays);
  }

  const summary = metrics ? summariseMetrics(metrics) : null;

  // The four states, from the three flags above. Before this the page
  // rendered its header and then NOTHING while the fetch was in flight —
  // `metrics` and `error` both null — which is pixel-identical to "this
  // account has no data". On the one page whose whole purpose is to be
  // trusted about what was and was not measured, that is the loading/empty
  // collapse the house rule forbids.
  //
  // `isEmpty` is `() => false`, and that is a decision rather than an
  // omission: `GET /me/metrics` answers with a complete payload of counts,
  // and `lib/platform-metrics.ts` is explicit that 0 is "a true, complete
  // answer" this page renders AS 0. An `empty` branch here would have to
  // reclassify real returned zeros as "there is nothing here", which is
  // this page's own honesty logic inverted. A read that comes back with no
  // payload at all is already `unreadable` (`READ_WITHOUT_RESULT`), which
  // is the honest answer for it.
  const panel = resolvePanel<MetricsSummary>(
    { loading, error, data: summary },
    () => false
  );

  return (
    <PageShell width="standard">
      <PageHeader
        title="Reliability"
        description={
          <>
            What this account&apos;s jobs actually got, measured from the
            coordinator&apos;s own event ledger &mdash; not a claim, a count.
          </>
        }
        // Outside the panel, so the window can still be changed while the
        // read is in flight or after it failed — the picker is the only
        // control on the page that can start a different read.
        actions={<WindowPicker value={windowDays} onChange={chooseWindow} />}
      />

      <StatePanel
        state={panel}
        label="this account's reliability numbers"
        // Three bars for the page's three sections. Under-promising is the
        // rule: a skeleton that claims more rows than arrive is a claim
        // about how much data you have, made before any of it was read.
        loadingRows={3}
        className="mt-6"
        // Required by the contract even though `isEmpty` above can never
        // return true. Written to be true if it ever did.
        empty={{
          title: "Nothing measured in this window.",
          description:
            "No jobs, tasks or machines were recorded for this account in the window selected above.",
        }}
        // The page had no way out of a failed read before this: the banner
        // it replaces was a dead end, and re-reading meant changing the
        // window or reloading the browser.
        unreadable={{ retry }}
      >
        {(summaryData) => (
          <>
            <section className="mt-6">
              <h2 className="label-caps">
                Jobs, last {summaryData.windowDays} days
              </h2>
              <div className="mt-2 grid gap-3 sm:grid-cols-4">
                {summaryData.jobCounts.map((c) => (
                  <CountTile key={c.label} stat={c} />
                ))}
              </div>
            </section>

            <section className="mt-6">
              <h2 className="label-caps">Tasks</h2>
              <div className="mt-2 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {summaryData.taskCounts.map((c) => (
                  <CountTile key={c.label} stat={c} />
                ))}
                <CountTile
                  stat={{
                    label: "Machines contributing",
                    value: summaryData.machinesContributing,
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
                <ReliabilityCard stat={summaryData.goodput} />
                <ReliabilityCard stat={summaryData.lostTaskTime} />
                <ReliabilityCard stat={summaryData.mttr} />
                <ReliabilityCard stat={summaryData.mttd} />
              </div>
            </section>
          </>
        )}
      </StatePanel>
    </PageShell>
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
