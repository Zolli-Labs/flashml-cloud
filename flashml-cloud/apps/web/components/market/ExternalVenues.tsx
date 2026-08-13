"use client";

import { useState } from "react";

import type { PriceQuote, PriceUnpriced } from "@/lib/cloud-api";
import { ageLabel, quoteRow, unpricedRow } from "@/lib/market-prices";

/** What other venues charge, compressed to one line each.
 *
 * These used to occupy a third of the board as stacked cards. They are
 * context for our own prices, not the subject of the page, so they moved
 * under the rail and lost their card — but NOT their content: the region,
 * tier, capture age, source and ZC equivalent that each card carried are on
 * the row's `title`, and the two facts that change how a number should be
 * read stay visible, which are the staleness verdict and that a venue with
 * no quote is *not observed* rather than absent.
 *
 * EIGHT ROWS, THEN A COUNT. In a 300px rail beside a paged board, a full
 * venue list is the longest thing on the page and it is the least
 * important thing on the page. The toggle carries the total, so the
 * collapsed state still says how much is behind it — a cap that hides its
 * own size is indistinguishable from a short list.
 *
 * THE TIER CHIP IS A DISAMBIGUATOR, not a decoration. Providers quote the
 * same SKU twice, secure and community, at different prices; without the
 * chip those two rows are the same provider and the same SKU with two
 * numbers, which reads as a data fault. Null tier stays bare rather than
 * being defaulted to either word.
 *
 * Every string still comes from `lib/market-prices.ts`; nothing here
 * converts a currency. */
export function ExternalVenues({
  quotes,
  unpriced,
}: {
  quotes: PriceQuote[];
  unpriced: PriceUnpriced[];
}) {
  const [showAll, setShowAll] = useState(false);

  if (quotes.length === 0 && unpriced.length === 0) {
    return (
      <section>
        <h2 className="label-caps">External venues</h2>
        <p className="mt-2 text-xs text-muted-foreground">
          No venue quotes captured yet.
        </p>
      </section>
    );
  }

  const total = quotes.length + unpriced.length;
  // Priced rows first, so a cap never spends its eight lines on venues we
  // have no number for.
  const shownQuotes = showAll ? quotes : quotes.slice(0, VENUE_CAP);
  const shownUnpriced = showAll
    ? unpriced
    : unpriced.slice(0, Math.max(0, VENUE_CAP - shownQuotes.length));

  return (
    <section>
      <h2 className="label-caps">External venues</h2>
      <ul className="mt-2 divide-y divide-border">
        {shownQuotes.map((quote, i) => {
          const row = quoteRow(quote);
          return (
            <li
              key={`${quote.provider}-${quote.sku}-${quote.tier ?? ""}-${i}`}
              title={[
                row.detail,
                row.capturedText,
                row.sourceText,
                row.equivalentText,
              ]
                .filter(Boolean)
                .join(" · ")}
              className={`flex items-baseline justify-between gap-2 py-1.5 text-xs ${
                row.stale ? "opacity-60" : ""
              }`}
            >
              <span className="flex min-w-0 items-baseline gap-1.5">
                <span className="min-w-0 truncate">
                  {quote.provider}
                  <span className="text-muted-foreground"> · {quote.sku}</span>
                </span>
                {quote.tier && (
                  <span className="shrink-0 rounded-full border border-border px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-muted-foreground">
                    {quote.tier}
                  </span>
                )}
              </span>
              <span className="flex shrink-0 items-baseline gap-1.5">
                <span className="font-mono tabular-nums">
                  {quote.amount} {quote.currency}
                </span>
                {quote.stale && (
                  <span
                    className="rounded-full border border-warning/40 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-warning-foreground"
                    title={`observed ${ageLabel(quote.age_seconds)}`}
                  >
                    STALE
                  </span>
                )}
              </span>
            </li>
          );
        })}
        {shownUnpriced.map((venue) => {
          const row = unpricedRow(venue);
          return (
            <li
              key={venue.provider}
              title={row.capturedText}
              className="flex items-baseline justify-between gap-2 py-1.5 text-xs"
            >
              <span className="min-w-0 truncate">{venue.provider}</span>
              <span className="shrink-0 font-mono text-muted-foreground">
                not observed
              </span>
            </li>
          );
        })}
      </ul>

      {total > VENUE_CAP && (
        <button
          type="button"
          onClick={() => setShowAll((open) => !open)}
          className="mt-2 rounded-md border border-border bg-surface px-2.5 py-1 text-xs transition-colors hover:bg-surface-2"
        >
          {showAll ? "Show fewer" : `Show all (${total})`}
        </button>
      )}
    </section>
  );
}

const VENUE_CAP = 8;
