"use client";

import { Warning } from "@phosphor-icons/react";

import type { QuoteRow, ZcRungRow } from "@/lib/market-prices";

/** The price-comparison card: the ZC ladder and the best live ask on one
 * side, scraped vendor prices on the other, venues with no quote rendered
 * as not observed.
 *
 * Markup only, and the layout is the discipline: the two denominations sit
 * in adjacent columns and no cell combines them — there is no exchange
 * rate and none may be implied. A stale quote is drawn dimmed with its
 * staleness in the captured line, so a scraped price can never sit here
 * looking live. */
export function PricesPanel({
  state,
  zcRows,
  quoteRows,
  unpricedRows,
  onRetry,
  error,
}: {
  state: "loading" | "present" | "unreadable";
  zcRows: ZcRungRow[];
  quoteRows: QuoteRow[];
  unpricedRows: QuoteRow[];
  onRetry: () => void;
  error: string | null;
}) {
  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <h2 className="text-sm font-semibold">Prices</h2>
      <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted-foreground">
        What capacity costs here and elsewhere, side by side. Zolli Credits
        and vendor currencies are shown next to each other and never
        converted — there is no exchange rate behind this page.
      </p>

      {state === "loading" && (
        <p className="mt-3 font-mono text-xs text-muted-foreground">
          reading the quotes…
        </p>
      )}

      {state === "unreadable" && (
        <div className="mt-3">
          <p className="flex max-w-prose items-start gap-2 text-sm leading-relaxed text-warning-foreground">
            <Warning className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
            <span>Couldn&apos;t read the price quotes.</span>
          </p>
          {error && (
            <p className="mt-1.5 break-all font-mono text-[11px] text-muted-foreground">
              {error}
            </p>
          )}
          <button
            type="button"
            onClick={onRetry}
            className="mt-3 rounded-md border border-border bg-surface px-2.5 py-1 text-xs hover:bg-surface-2"
          >
            Try again
          </button>
        </div>
      )}

      {state === "present" && (
        <div className="mt-4 grid gap-6 lg:grid-cols-2">
          <div>
            <h3 className="label-caps">Zolli Credits, per hour</h3>
            <ul className="mt-2 divide-y divide-border rounded-md border border-border">
              {zcRows.map((row) => (
                <li
                  key={row.capabilityClass}
                  className="flex items-baseline justify-between gap-4 px-3 py-2.5"
                >
                  <span className="font-mono text-xs">
                    {row.capabilityClass}
                  </span>
                  <span className="text-right font-mono text-xs text-muted-foreground">
                    <span className="block text-foreground">
                      {row.bestAskText}
                    </span>
                    {row.referenceText}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <h3 className="label-caps">Elsewhere, as observed</h3>
            <ul className="mt-2 divide-y divide-border rounded-md border border-border">
              {quoteRows.map((row, i) => (
                <li
                  key={`${row.venue}-${i}`}
                  className={`px-3 py-2.5 ${row.stale ? "opacity-60" : ""}`}
                >
                  <div className="flex items-baseline justify-between gap-4">
                    <span className="min-w-0 truncate text-xs">
                      {row.venue}
                      {row.detail && (
                        <span className="ml-1.5 text-[11px] text-muted-foreground">
                          {row.detail}
                        </span>
                      )}
                    </span>
                    <span className="shrink-0 font-mono text-xs">
                      {row.amountText}
                    </span>
                  </div>
                  <p className="mt-0.5 text-[11px] text-muted-foreground">
                    {row.capturedText}
                    {row.sourceText && ` · ${row.sourceText}`}
                  </p>
                </li>
              ))}
              {unpricedRows.map((row) => (
                <li key={row.venue} className="px-3 py-2.5">
                  <div className="flex items-baseline justify-between gap-4">
                    <span className="text-xs">{row.venue}</span>
                    <span className="font-mono text-xs text-muted-foreground">
                      {row.amountText}
                    </span>
                  </div>
                  <p className="mt-0.5 text-[11px] text-muted-foreground">
                    {row.capturedText}
                  </p>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </section>
  );
}
