/** What the listings page is allowed to say about the book.
 *
 * WHY THIS MODULE EXISTS. The API returns the open asks ranked within each
 * capability class and the host's own listings verbatim; grouping the book
 * by class, and every sentence about a host's record, is a decision and
 * lives here where a test can reach it (the same split
 * `lib/job-artifacts.ts` documents).
 *
 * THE RECORD DISCIPLINE. `acceptance_rate` null is "this host has not been
 * asked yet" — the page says exactly that, in words, because a rendered
 * 0% would mean "asked, and failed everything", which is a different fact
 * with the same digits. The count behind a real rate travels with it
 * ("81% of 34 resolved") so the number can be argued with.
 *
 * THE PRICE DISCIPLINE. A zero ask is legal and means donated; the label
 * says "donated", never "free" (donated is the market's word for a zero
 * ask) and never "0 ZC/hour" dressed as a price. Non-zero asks render
 * through `formatZc`, the integer-only formatter, so the page shows the
 * same digits `marketplace.price_label` produced server-side.
 */
import type { MarketAsk, MarketListing } from "./cloud-api";
import { formatZc } from "./market-credits";

/** The book grouped by capability class, API rank order preserved inside
 * each class — the route iterates the classes and `open_asks` already
 * ranks by effective price, so re-sorting here would be a second copy of
 * a rule that lives in one place. */
export interface ClassBook {
  capabilityClass: string;
  asks: MarketAsk[];
}

export function groupBookByClass(asks: MarketAsk[]): ClassBook[] {
  const order: string[] = [];
  const byClass = new Map<string, MarketAsk[]>();
  for (const ask of asks) {
    if (!byClass.has(ask.capability_class)) {
      byClass.set(ask.capability_class, []);
      order.push(ask.capability_class);
    }
    byClass.get(ask.capability_class)?.push(ask);
  }
  return order.map((capabilityClass) => ({
    capabilityClass,
    asks: byClass.get(capabilityClass) ?? [],
  }));
}

/** A host's record, in words. Null rate and null count are the same
 * condition — unproven — and get the sentence, not a number. */
export function recordLabel(ask: MarketAsk): string {
  if (ask.acceptance_rate === null || ask.resolved_n === null) {
    return "unproven — no accepted-work record in this class yet";
  }
  const pct = Math.round(ask.acceptance_rate * 100);
  return `${pct}% accepted of ${ask.resolved_n} resolved in this class`;
}

/** The ask, as a price line. Donated first: zero is a legal ask and its
 * label is the word, not the number. */
export function askPriceLabel(ask: MarketAsk): string {
  if (ask.donated) return "donated";
  return `${formatZc(ask.ask_zc_per_hour)} ZC/hour`;
}

/** The host's own listing, as a status line. The state vocabulary is the
 * API's — open / paused / withdrawn — rendered verbatim. */
export function listingStateLabel(listing: MarketListing): string {
  switch (listing.state) {
    case "open":
      return "open — ranked in the book";
    case "paused":
      return "paused — kept out of the book";
    case "withdrawn":
      return "withdrawn";
    default:
      return `state the console does not recognise ("${listing.state}")`;
  }
}
