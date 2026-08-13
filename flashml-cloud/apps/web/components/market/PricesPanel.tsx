"use client";

import Link from "next/link";
import { ArrowClockwise, Warning } from "@phosphor-icons/react";

import type { PricesView } from "@/lib/cloud-api";
import { boardStrip } from "@/lib/market-prices";
import {
  boardRows,
  hottestRows,
  tickerRows,
  type BoardRow,
} from "@/lib/market/board";
import { REFERENCE_META } from "@/lib/market/reference";
import { ExternalVenues } from "./ExternalVenues";
import { SourceChip } from "./SourceChip";
import { Sparkline } from "./Sparkline";

/** The compute board: capability classes as tickers, ranked, with the
 * reference table filling in every class nobody has listed in yet.
 *
 * Markup and arrangement only — every row, rank, word and source stamp
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
  onRetry,
  error,
  stale = false,
}: {
  state: "loading" | "present" | "unreadable";
  view: PricesView | null;
  onRetry: () => void;
  /** The first-load failure's detail. */
  error: string | null;
  /** A refresh failed after data was already shown. */
  stale?: boolean;
}) {
  if (state === "loading") {
    return (
      <section aria-busy="true">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="panel px-4 py-3.5">
              <div className="skeleton h-3 w-20" />
              <div className="skeleton mt-2.5 h-6 w-24" />
              <div className="skeleton mt-2.5 h-7 w-full" />
            </div>
          ))}
        </div>
        <div className="mt-6 grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
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
  const ticker = tickerRows(rows);
  const hottest = hottestRows(view.zc);

  return (
    <section>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {ticker.map((row) => (
          <TickerCard key={row.klass} row={row} />
        ))}
      </div>

      <div className="mt-6 grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
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

          <div className="overflow-x-auto rounded-lg border border-border bg-surface">
            <table className="w-full min-w-[620px] border-collapse text-xs">
              <thead>
                <tr className="border-b border-border">
                  <th scope="col" className="label-caps px-3 py-2 text-left">
                    Class
                  </th>
                  <th scope="col" className="label-caps px-2 py-2 text-right">
                    Last ask
                  </th>
                  <th scope="col" className="label-caps px-2 py-2 text-right">
                    24h
                  </th>
                  <th scope="col" className="label-caps px-2 py-2 text-left">
                    7d
                  </th>
                  <th scope="col" className="label-caps px-2 py-2 text-right">
                    Open asks
                  </th>
                  <th scope="col" className="label-caps px-3 py-2 text-right">
                    Source
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((row) => (
                  <tr key={row.klass} className="transition-colors hover:bg-surface-2">
                    <td className="px-3 py-2.5">
                      <Link href={row.href} className="block min-w-0">
                        <span className="font-mono hover:underline">
                          {row.klass}
                        </span>
                        {row.displayName && (
                          <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
                            {row.displayName}
                          </span>
                        )}
                      </Link>
                    </td>
                    <td className="px-2 py-2.5 text-right font-mono tabular-nums">
                      <div>{row.lastAskText}</div>
                      {row.equivalentText && (
                        <div className="mt-0.5 text-[10px] text-muted-foreground">
                          {row.equivalentText}
                        </div>
                      )}
                    </td>
                    <td
                      className={`px-2 py-2.5 text-right font-mono tabular-nums ${DELTA_TONE[row.change.direction]}`}
                    >
                      {row.change.text}
                    </td>
                    <td className="px-2 py-2.5">
                      <Sparkline points={row.spark} />
                    </td>
                    <td className="px-2 py-2.5 text-right font-mono tabular-nums">
                      {row.depth === null ? (
                        <span className="text-muted-foreground">—</span>
                      ) : (
                        row.depth
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      <SourceChip source={row.source} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <p className="mt-2 text-[11px] text-muted-foreground">
            Reference rows are generated {REFERENCE_META.generatedAt} from live
            vendor bands.
          </p>
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

/** One ticker card. The chip is the card's honesty: a reference card looks
 * exactly like a live one except for it, which is the only reason a
 * reference card is allowed to be here at all. */
function TickerCard({ row }: { row: BoardRow }) {
  return (
    <Link
      href={row.href}
      className="panel block px-3.5 py-3 transition-colors hover:bg-surface-2"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate font-mono text-xs">{row.klass}</span>
        <SourceChip source={row.source} />
      </div>
      <div className="mt-2 flex items-baseline justify-between gap-2">
        <span className="metric-value text-xl">{row.lastAskText}</span>
        <span
          className={`font-mono text-[11px] tabular-nums ${DELTA_TONE[row.change.direction]}`}
        >
          {row.change.text}
        </span>
      </div>
      <div className="mt-1 flex items-center justify-between gap-2">
        <span className="text-[11px] text-muted-foreground tabular-nums">
          {row.equivalentText ?? " "}
        </span>
        <Sparkline points={row.spark} />
      </div>
    </Link>
  );
}

/** Market-convention colours for a movement, purely directional: the
 * direction is the lib's decision, this only maps it to a token. */
const DELTA_TONE: Record<"up" | "down" | "none", string> = {
  up: "text-evergreen",
  down: "text-destructive",
  none: "text-muted-foreground",
};
