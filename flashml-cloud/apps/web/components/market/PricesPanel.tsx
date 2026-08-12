"use client";

import { Fragment, useState } from "react";
import {
  CaretDown,
  CaretRight,
  ChartLine,
  Clock,
  Globe,
  Tag,
  Warning,
} from "@phosphor-icons/react";

import type { PricesView } from "@/lib/cloud-api";
import { formatZc } from "@/lib/market-credits";
import {
  boardStrip,
  deltaCell,
  quoteRow,
  sparkPoints,
  unpricedRow,
  zcRungRow,
  type QuoteRow,
} from "@/lib/market-prices";
import { Sparkline } from "./Sparkline";

/** The compute board: the capability classes are the tickers — last live
 * ask, 24-hour movement, depth, sparkline and reference rung — with the
 * external venues quoted in a strip beside the board.
 *
 * Markup only: every figure arrives through `lib/market-prices.ts` and
 * `lib/market-credits.ts`. The layout keeps source amounts beside the API's
 * fixed-parity equivalents without calculating or summing either. A failed
 * read is unreadable, never empty; null is never 0. */
export function PricesPanel({
  state,
  view,
  onRetry,
  error,
}: {
  state: "loading" | "present" | "unreadable";
  view: PricesView | null;
  onRetry: () => void;
  error: string | null;
}) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());

  const toggle = (klass: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(klass)) {
        next.delete(klass);
      } else {
        next.add(klass);
      }
      return next;
    });

  return (
    <section>
      {state === "loading" && (
        <div aria-busy="true">
          <p className="font-mono text-xs text-muted-foreground">
            reading the board…
          </p>
          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            {Array.from({ length: 3 }, (_, i) => (
              <div
                key={i}
                className="rounded-lg border border-border bg-surface px-4 py-3.5"
              >
                <div className="skeleton h-7 w-14" />
                <div className="skeleton mt-2 h-3 w-28" />
              </div>
            ))}
          </div>
          <div className="mt-6 grid items-start gap-6 lg:grid-cols-3">
            <div className="space-y-3 rounded-lg border border-border bg-surface p-4 lg:col-span-2">
              {Array.from({ length: 6 }, (_, i) => (
                <div key={i} className="skeleton h-4 w-full" />
              ))}
            </div>
            <div className="space-y-2 rounded-lg border border-border bg-surface p-4">
              {Array.from({ length: 4 }, (_, i) => (
                <div key={i} className="skeleton h-12 w-full" />
              ))}
            </div>
          </div>
        </div>
      )}

      {state === "unreadable" && (
        <div className="rounded-lg border border-destructive/30 bg-surface p-4">
          <p className="flex max-w-prose items-start gap-2 text-sm text-destructive">
            <Warning className="mt-0.5 h-4 w-4 shrink-0" weight="fill" />
            <span>
              Couldn&apos;t read the price board. A failed read is
              unreadable, not empty — the numbers are still on the server,
              retry when the API is reachable.
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
        </div>
      )}

      {state === "present" && view && (
        <>
          {/* The board's header strip: three real counts over the whole
              book, shaped as §4's stat tiles. */}
          <div className="grid gap-3 sm:grid-cols-3">
            {boardStrip(view.board).map((tile) => {
              const meta = TILE_META[tile.label];
              const Icon = meta?.icon;
              return (
                <div
                  key={tile.label}
                  className="flex items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3.5"
                >
                  {Icon && (
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                      <Icon className="h-4 w-4" />
                    </span>
                  )}
                  <div className="min-w-0">
                    <div className="metric-value text-2xl">{tile.value}</div>
                    <div className="label-caps mt-0.5">{tile.label}</div>
                    {meta && (
                      <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
                        {meta.subtitle}
                      </p>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="mt-6 grid items-start gap-6 lg:grid-cols-3">
            {/* The board itself — one ticker row per capability class. */}
            <section className="min-w-0 lg:col-span-2">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h2 className="label-caps">The board — ZC asks and USD equivalents</h2>
                <p className="text-[11px] text-muted-foreground">
                  select a class to open its observations
                </p>
              </div>

              {view.zc.length === 0 ? (
                <div className="mt-2 flex flex-col items-center gap-3 rounded-lg border border-dashed border-border bg-surface px-6 py-10 text-center">
                  <span className="flex h-12 w-12 items-center justify-center rounded-lg border border-dashed border-border text-muted-foreground">
                    <ChartLine className="h-5 w-5" />
                  </span>
                  <p className="max-w-prose text-sm text-muted-foreground">
                    No capability classes on the board yet.
                  </p>
                </div>
              ) : (
                <div className="mt-2 overflow-x-auto rounded-lg border border-border bg-surface">
                  <table className="w-full min-w-[600px] border-collapse text-xs">
                    <thead>
                      <tr className="border-b border-border">
                        <th scope="col" className="label-caps px-3 py-2 text-left">
                          Class
                        </th>
                        <th scope="col" className="label-caps px-2 py-2 text-right">
                          Last
                        </th>
                        <th scope="col" className="label-caps px-2 py-2 text-right">
                          24h
                        </th>
                        <th scope="col" className="label-caps px-2 py-2 text-right">
                          Depth
                        </th>
                        <th scope="col" className="px-2 py-2">
                          <span className="sr-only">Trend</span>
                        </th>
                        <th scope="col" className="label-caps px-3 py-2 text-right">
                          Reference
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border">
                      {view.zc.map((rung) => {
                        const isOpen = expanded.has(rung.capability_class);
                        const delta = deltaCell(rung.change_zc);
                        const row = zcRungRow(rung);
                        return (
                          <Fragment key={rung.capability_class}>
                            <tr
                              onClick={() => toggle(rung.capability_class)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" || e.key === " ") {
                                  e.preventDefault();
                                  toggle(rung.capability_class);
                                }
                              }}
                              tabIndex={0}
                              aria-expanded={isOpen}
                              className="cursor-pointer transition-colors hover:bg-surface-2"
                            >
                              <td className="px-3 py-2.5">
                                <span className="flex items-center gap-1.5 font-mono">
                                  {isOpen ? (
                                    <CaretDown className="h-3 w-3 shrink-0 text-muted-foreground" />
                                  ) : (
                                    <CaretRight className="h-3 w-3 shrink-0 text-muted-foreground" />
                                  )}
                                  {rung.capability_class}
                                </span>
                              </td>
                              <td className="px-2 py-2.5 text-right font-mono">
                                <div>{row.bestAskText}</div>
                                {row.bestAskEquivalentText && (
                                  <div className="mt-0.5 text-[10px] text-muted-foreground">
                                    {row.bestAskEquivalentText}
                                  </div>
                                )}
                              </td>
                              <td
                                className={`px-2 py-2.5 text-right font-mono ${DELTA_TONE[delta.direction]}`}
                              >
                                {delta.text}
                              </td>
                              <td className="px-2 py-2.5 text-right font-mono">
                                {rung.depth} asks
                              </td>
                              <td className="px-2 py-2.5">
                                <Sparkline points={sparkPoints(rung.history)} />
                              </td>
                              <td className="whitespace-nowrap px-3 py-2.5 text-right text-[11px] text-muted-foreground">
                                <div>{row.referenceText}</div>
                                <div className="mt-0.5 text-[10px]">
                                  {row.referenceEquivalentText}
                                </div>
                              </td>
                            </tr>
                            {isOpen && (
                              <tr className="bg-surface-2">
                                <td colSpan={6} className="px-3 py-2.5">
                                  {rung.history.length === 0 ? (
                                    <p className="py-1 font-mono text-[11px] text-muted-foreground">
                                      no observations recorded for this class yet
                                    </p>
                                  ) : (
                                    <ul className="divide-y divide-border">
                                      {rung.history.map((point, i) => (
                                        <li
                                          key={`${point.at}-${i}`}
                                          className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 py-1.5 font-mono text-[11px]"
                                        >
                                          <span className="text-muted-foreground">
                                            {time(point.at)}
                                          </span>
                                          <span className="text-right">
                                            <span>
                                              {point.best_ask_zc === null
                                                ? "—"
                                                : `${formatZc(point.best_ask_zc)} ZC/h`}
                                            </span>
                                            {point.best_ask_usd !== null && (
                                              <span className="ml-2 text-muted-foreground">
                                                ${point.best_ask_usd}/h equivalent
                                              </span>
                                            )}
                                          </span>
                                          <span className="text-muted-foreground">
                                            {point.open_asks} open asks
                                          </span>
                                        </li>
                                      ))}
                                    </ul>
                                  )}
                                </td>
                              </tr>
                            )}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {/* External venues retain vendor amounts beside API-provided ZC
                equivalents; the console never derives a conversion. */}
            <section className="min-w-0">
              <div className="flex items-center gap-1.5">
                <Globe className="h-3.5 w-3.5 text-muted-foreground" />
                <h2 className="label-caps">External venues</h2>
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                Vendor amounts stay in their own currency, with a ZC
                equivalent only when the API provides one.
              </p>

              {view.quotes.length === 0 && view.unpriced.length === 0 ? (
                <div className="mt-2 flex flex-col items-center gap-3 rounded-lg border border-dashed border-border bg-surface px-6 py-10 text-center">
                  <span className="flex h-12 w-12 items-center justify-center rounded-lg border border-dashed border-border text-muted-foreground">
                    <Globe className="h-5 w-5" />
                  </span>
                  <p className="max-w-prose text-sm text-muted-foreground">
                    No external venues to compare yet.
                  </p>
                </div>
              ) : (
                <ul className="mt-2 space-y-2">
                  {view.quotes.map((quote, i) => (
                    <QuoteCard key={`${quote.provider}-${i}`} row={quoteRow(quote)} />
                  ))}
                  {view.unpriced.map((venue) => {
                    const row = unpricedRow(venue);
                    return (
                      <li
                        key={venue.provider}
                        className="rounded-lg border border-dashed border-border bg-surface px-3 py-2.5"
                      >
                        <div className="flex items-baseline justify-between gap-3">
                          <span className="min-w-0 truncate text-xs">
                            {row.venue}
                          </span>
                          <span className="shrink-0 font-mono text-xs text-muted-foreground">
                            {row.amountText}
                          </span>
                        </div>
                        <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                          {row.capturedText}
                        </p>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
          </div>
        </>
      )}
    </section>
  );
}

/** Dressing for the header-strip tiles, keyed by the label `boardStrip`
 * decides. Values stay the lib's; the subtitle only rewords what the count
 * already says. */
const TILE_META: Record<string, { icon: typeof Tag; subtitle: string }> = {
  "open asks": { icon: Tag, subtitle: "across the whole book" },
  "classes with a live book": {
    icon: ChartLine,
    subtitle: "on the capability ladder",
  },
  "observations, 24h": {
    icon: Clock,
    subtitle: "recorded as the book moved",
  },
};

/** Market-convention colours for the 24h cell, purely directional: the
 * direction is the lib's decision, this only maps it to a token. */
const DELTA_TONE: Record<"up" | "down" | "none", string> = {
  up: "text-evergreen",
  down: "text-destructive",
  none: "text-muted-foreground",
};

/** One scraped quote with its provenance. A stale card is dimmed and leads
 * with the staleness sentence, so a scraped price can never sit here
 * looking live; a fresh one says only how long ago it was observed. */
function QuoteCard({ row }: { row: QuoteRow }) {
  return (
    <li
      className={`rounded-lg border border-border bg-surface px-3 py-2.5 ${
        row.stale ? "opacity-60" : ""
      }`}
    >
      {row.stale && (
        <p className="mb-1 text-[11px] text-muted-foreground">
          {row.capturedText}
          {row.sourceText && <> · {row.sourceText}</>}
        </p>
      )}
      <div className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 truncate text-xs">{row.venue}</span>
        <span className="shrink-0 font-mono text-xs">{row.amountText}</span>
      </div>
      {row.equivalentText && (
        <p className="mt-0.5 text-right font-mono text-[11px] text-muted-foreground">
          {row.equivalentText}
        </p>
      )}
      {row.detail && (
        <p className="mt-0.5 truncate text-[11px] text-muted-foreground">
          {row.detail}
        </p>
      )}
      {!row.stale && (
        <p className="mt-1 text-[11px] text-muted-foreground">
          {row.capturedText}
          {row.sourceText && <> · {row.sourceText}</>}
        </p>
      )}
    </li>
  );
}

/** The page's own clock format for an API timestamp; an em dash for a
 * value that does not parse, never "Invalid Date". */
function time(iso: string): string {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? "—" : new Date(ms).toLocaleString();
}
