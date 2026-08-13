"use client";

import Link from "next/link";
import { ArrowClockwise, Warning } from "@phosphor-icons/react";

import type { PricesView } from "@/lib/cloud-api";
import { boardStrip } from "@/lib/market-prices";
import {
  boardRows,
  hostClassGroups,
  hottestRows,
  type HostMachine,
} from "@/lib/market/board";
import { BoardTable } from "./BoardTable";
import { ExternalVenues } from "./ExternalVenues";
import { YourClasses } from "./YourClasses";

/** The compute board: the host's own classes first, then every class the
 * coordinator or the reference table knows, filtered and paged.
 *
 * THE HOST'S CLASSES LEAD. The page used to open with a four-card ticker
 * strip ranked by activity — the busiest classes on the market, which are
 * only yours by coincidence. A host reading this page is asking about
 * their own machines, so those go first, with the chart already drawn;
 * everything else is the board below.
 *
 * Markup and arrangement only — every row, word, source stamp and verdict
 * arrives from `lib/market/board.ts`, and every currency string from
 * `lib/market-prices.ts` or the API itself. Nothing here decides what a
 * number means.
 *
 * FOUR STATES, NOT THREE. First-load failure is still the full unreadable
 * panel, because there is nothing to show behind it. A failure while data
 * is on screen is different: the last answer is still the best answer we
 * have, so it stays, and `stale` puts a strip over it saying so. Replacing
 * a readable board with an error panel because a background refresh missed
 * is how a page loses information it already had. */
export function PricesPanel({
  state,
  view,
  machines,
  machinesState,
  onRetry,
  error,
  stale = false,
}: {
  state: "loading" | "present" | "unreadable";
  view: PricesView | null;
  /** The signed-in host's machines, already resolved to capability classes
   * by the page. Read independently of the board — see `YourClasses`. */
  machines: HostMachine[];
  machinesState: "loading" | "present" | "unreadable";
  onRetry: () => void;
  /** The first-load failure's detail. */
  error: string | null;
  /** A refresh failed after data was already shown. */
  stale?: boolean;
}) {
  if (state === "loading") {
    return (
      <section aria-busy="true">
        <div className="grid gap-4 xl:grid-cols-2">
          <div className="skeleton h-56 w-full" />
          <div className="skeleton h-56 w-full" />
        </div>
        <div className="mt-8 grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div className="panel space-y-3 p-4">
            {Array.from({ length: 8 }, (_, i) => (
              <div key={i} className="skeleton h-4 w-full" />
            ))}
          </div>
          <div className="space-y-3">
            <div className="skeleton h-28 w-full" />
            <div className="skeleton h-24 w-full" />
          </div>
        </div>
      </section>
    );
  }

  if (state === "unreadable" || view === null) {
    return (
      <section className="rounded-lg border border-destructive/30 bg-surface p-4">
        <p className="flex max-w-prose items-start gap-2 text-sm text-destructive">
          <Warning className="mt-0.5 h-4 w-4 shrink-0" weight="fill" />
          <span>
            Couldn&apos;t read the price board. A failed read is unreadable,
            not empty — the numbers are still on the server.
          </span>
        </p>
        {error && (
          <p className="mt-1.5 break-all font-mono text-[11px] text-muted-foreground">
            {error}
          </p>
        )}
        <button
          type="button"
          onClick={onRetry}
          className="mt-3 rounded-md border border-border bg-surface px-2.5 py-1 text-xs transition-colors hover:bg-surface-2"
        >
          Try again
        </button>
      </section>
    );
  }

  const rows = boardRows(view);
  const hottest = hottestRows(view.zc);

  return (
    <section>
      <YourClasses
        groups={hostClassGroups(view, machines)}
        state={machinesState}
      />

      <div className="mt-8 grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0">
          {stale && (
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-1.5 text-xs text-warning-foreground">
              <span>Couldn&apos;t refresh — showing the last answer.</span>
              <button
                type="button"
                onClick={onRetry}
                className="inline-flex items-center gap-1 rounded border border-warning/40 px-1.5 py-0.5 text-[11px] transition-colors hover:bg-warning/10"
              >
                <ArrowClockwise className="h-3 w-3" />
                Retry
              </button>
            </div>
          )}

          <BoardTable rows={rows} />
        </div>

        <div className="min-w-0 space-y-6">
          <section>
            <h2 className="label-caps">Hottest right now</h2>
            {hottest.length === 0 ? (
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                No live activity yet — the board below carries reference
                prices.
              </p>
            ) : (
              <ol className="mt-2 divide-y divide-border">
                {hottest.map((hot, i) => (
                  <li key={hot.why}>
                    <Link
                      href={hot.href}
                      className="flex items-baseline gap-2 py-2 transition-colors hover:text-brand-foreground"
                    >
                      <span className="meta w-3 shrink-0">{i + 1}</span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-mono text-xs">
                          {hot.klass}
                        </span>
                        <span className="block text-[11px] text-muted-foreground">
                          {hot.why}
                        </span>
                      </span>
                      <span className="shrink-0 font-mono text-xs tabular-nums">
                        {hot.valueText}
                      </span>
                    </Link>
                  </li>
                ))}
              </ol>
            )}
          </section>

          <section className="panel px-3.5 py-3">
            <h2 className="label-caps">Board</h2>
            <dl className="mt-2 space-y-1.5">
              {boardStrip(view.board).map((tile) => (
                <div
                  key={tile.label}
                  className="flex items-baseline justify-between gap-3"
                >
                  <dt className="text-[11px] text-muted-foreground">
                    {tile.label}
                  </dt>
                  <dd className="font-mono text-xs tabular-nums">
                    {tile.value}
                  </dd>
                </div>
              ))}
            </dl>
          </section>

          <ExternalVenues quotes={view.quotes} unpriced={view.unpriced} />
        </div>
      </div>
    </section>
  );
}

// The four-card ticker strip that used to live here is gone with
// `tickerRows`: it ranked classes by market activity, which answered a
// question no host was asking. `YourClasses` above is what replaced it.
