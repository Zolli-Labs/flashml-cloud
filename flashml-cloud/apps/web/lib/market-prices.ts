/** What the price-comparison page is allowed to say.
 *
 * WHY THIS MODULE EXISTS. The pitch is "FlashML vs the world": the ZC
 * ladder and the best live ask on one side, scraped vendor prices on the
 * other. Every quote carries `captured_at`, `source` and the API's own
 * staleness verdict; the page's job is to render all three, and to render
 * a venue with no quote as *not observed* rather than skip it. Those are
 * decisions, so they live here where a test can reach them.
 *
 * THE CURRENCY DISCIPLINE. ZC and vendor currencies are shown side by side
 * and never summed — there is no exchange rate and no field may imply one.
 * This module therefore has no function that takes both a ZC amount and a
 * foreign amount: the two lists are formatted separately and meet only in
 * markup, in adjacent columns.
 *
 * THE STALENESS DISCIPLINE. The verdict is the API's (`stale`), the age is
 * the API's (`age_seconds`), and the page adds only a human shape for the
 * age. A stale quote renders with its capture date up front; a fresh one
 * never claims "live" — the most it may say is how long ago it was
 * observed, because that is the fact behind it.
 */
import type { PriceQuote, PriceUnpriced, ZcRung } from "./cloud-api";
import { formatZc } from "./market-credits";

/** `age_seconds` as a human interval, floored at each unit so the label
 * never claims a precision the measurement does not have. Negative ages
 * (a clock that disagreed) are quoted as-is rather than clamped — the
 * same choice `prices.quote_age` makes one side of the wire. */
export function ageLabel(ageSeconds: number): string {
  if (!Number.isFinite(ageSeconds)) return "age unknown";
  if (ageSeconds < 0) return `captured ${Math.ceil(-ageSeconds)}s in the future`;
  if (ageSeconds < 60) return `${Math.floor(ageSeconds)}s ago`;
  if (ageSeconds < 3600) return `${Math.floor(ageSeconds / 60)}m ago`;
  if (ageSeconds < 86_400) return `${Math.floor(ageSeconds / 3600)}h ago`;
  return `${Math.floor(ageSeconds / 86_400)}d ago`;
}

/** One quote as a comparison row. The amount stays the vendor's string —
 * parsing it into a number here would round digits the API preserved on
 * purpose — and carries its denomination visibly. */
export interface QuoteRow {
  venue: string;
  detail: string;
  amountText: string;
  capturedText: string;
  sourceText: string;
  stale: boolean;
}

export function quoteRow(quote: PriceQuote): QuoteRow {
  const where = [quote.region, quote.tier].filter(Boolean).join(" · ");
  return {
    venue: `${quote.provider} — ${quote.sku}`,
    detail: where,
    amountText: `${quote.currency} ${quote.amount} / ${quote.unit}`,
    capturedText: quote.stale
      ? `stale — observed ${ageLabel(quote.age_seconds)}`
      : `observed ${ageLabel(quote.age_seconds)}`,
    sourceText: quote.source,
    stale: quote.stale,
  };
}

/** A venue with no quote, as a row shaped like the others so the view
 * cannot skip it. `amount` null renders as "not observed", never 0. */
export function unpricedRow(venue: PriceUnpriced): QuoteRow {
  return {
    venue: venue.provider,
    detail: "",
    amountText: "not observed",
    capturedText: "no published price has been captured for this venue",
    sourceText: "",
    stale: false,
  };
}

/** The ZC side of the comparison: reference ladder and best live ask,
 * formatted but never converted. A null best ask is "no live ask" — an
 * empty book, not a zero price. */
export interface ZcRungRow {
  capabilityClass: string;
  referenceText: string;
  bestAskText: string;
}

export function zcRungRow(rung: ZcRung): ZcRungRow {
  return {
    capabilityClass: rung.capability_class,
    referenceText: `${formatZc(rung.reference_zc_per_hour)} ZC/hour reference`,
    bestAskText:
      rung.best_ask_zc === null
        ? "no live ask"
        : rung.best_ask_zc === 0
          ? "donated"
          : `${formatZc(rung.best_ask_zc)} ZC/hour best ask`,
  };
}
