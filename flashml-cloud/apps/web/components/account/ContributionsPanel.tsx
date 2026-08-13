"use client";

import { useCallback, useEffect, useState } from "react";

import { StatePanel } from "@/components/shell/StatePanel";
import { resolvePanel } from "@/lib/console/panel-state";
import { summariseContributions } from "@/lib/contributions";
import {
  NotAuthenticated,
  getMyContributions,
  type MyContributions,
} from "@/lib/cloud-api";

/**
 * The account-wide running total of accepted work.
 *
 * NO `empty` STATE, and that is a decision rather than an omission.
 * `isEmpty` is `() => false`: `GET /me/contributions` answers with counters,
 * and a counter at zero is a measurement — "nothing has been accepted yet" —
 * not an absent one. Swapping that for an empty state would REMOVE a real,
 * API-returned figure and replace it with a sentence, which is the wrong
 * direction for a panel whose whole job is to be a tally. The zeros stay.
 *
 * What used to be missing is at the other end: this panel's only branches
 * were "error", "have data", and a bare `…`. A read that finished without
 * returning anything — the swallowed-401 path below — left that ellipsis on
 * screen permanently, a loading state with nothing behind it.
 */
export function ContributionsPanel() {
  const [contributions, setContributions] = useState<MyContributions | null>(
    null
  );
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // No `setLoading(true)` here — see `app/(console)/account/page.tsx`.
  const load = useCallback(() => {
    return getMyContributions()
      .then((c) => {
        setContributions(c);
        setError(null);
      })
      .catch((err) => {
        // Same doctrine as the storage panel: a 401 is the page's `getMe`
        // redirect to handle, not a second one from here. Clearing `loading`
        // in `finally` is what stops the swallow from becoming a permanent
        // loading state — `panelRead` then classifies a finished read with no
        // result as unreadable, which is what it is.
        if (!(err instanceof NotAuthenticated)) {
          setError(
            err instanceof Error
              ? err.message
              : "Couldn't load what you've contributed."
          );
        }
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const panel = resolvePanel(
    { loading, error, data: contributions },
    () => false
  );

  return (
    <section className="panel mt-4 p-5">
      <h2 className="text-sm font-semibold">Contributed</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        Work your Zollis have accepted, across every job you have ever run
        &mdash; not just the one job page a credit used to be visible from. A
        running tally, not a balance: nothing here is ever spent.
      </p>

      <StatePanel
        state={panel}
        className="mt-4 px-0 sm:px-0"
        loadingRows={1}
        label="what you've contributed"
        empty={{ title: "No contribution figures for this account." }}
        unreadable={{ retry: load }}
      >
        {(c) => (
          <div className="mt-4">
            <ContributionsSummaryView contributions={c} />
          </div>
        )}
      </StatePanel>
    </section>
  );
}

/** The account-wide contribution totals plus, when there is more than one
 * machine, the per-machine breakdown behind them. `lib/contributions.ts`
 * already decided the exact label for a machine with no reported hostname
 * or no recorded last-seen time; this only lays the rows out. */
function ContributionsSummaryView({
  contributions,
}: {
  contributions: MyContributions;
}) {
  const summary = summariseContributions(contributions);

  return (
    <div>
      <div className="flex flex-wrap gap-6">
        <div>
          <div className="metric-value text-2xl">{summary.acceptedTasks}</div>
          <div className="label-caps mt-1">Tasks accepted</div>
        </div>
        <div>
          <div className="metric-value text-2xl">
            {summary.jobsContributedTo}
          </div>
          <div className="label-caps mt-1">Jobs contributed to</div>
        </div>
      </div>

      {summary.machines.length > 0 && (
        <div className="mt-4 space-y-1.5">
          {summary.machines.map((m) => (
            <div
              key={m.machineId}
              className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2 text-xs"
            >
              <span className="min-w-0 truncate font-mono">
                {m.hostnameLabel}
              </span>
              <span className="flex shrink-0 items-center gap-3 text-muted-foreground">
                <span className="font-mono tabular-nums text-foreground">
                  {m.acceptedTasks}
                </span>
                <span>accepted</span>
                <span aria-hidden className="text-muted-foreground/40">
                  ·
                </span>
                <span>{m.lastSeenLabel}</span>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
