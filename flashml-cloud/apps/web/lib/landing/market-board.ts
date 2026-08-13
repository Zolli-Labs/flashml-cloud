/** The landing page's price board: `GET /v1alpha1/public/prices`, read
 * server-side, unauthenticated, for whoever lands on the marketing site
 * before they have an account.
 *
 * WHY THIS MODULE EXISTS. Everything this page says about price has to
 * survive a stranger clicking "view source". Types here mirror the API's
 * contract verbatim — see the endpoint's own docstring in
 * `apps/api/flashml_cloud_api/app.py` for the source of truth — so a field
 * this module invents cannot silently diverge from what the API actually
 * sends. `PriceBoard.tsx` renders only what these types allow it to hold.
 *
 * THE PRICE HONESTY DISCIPLINE (matches `lib/market-prices.ts`). `amount`
 * stays the vendor's decimal STRING; this module never parses it into a
 * float and reformats it, which would round digits the API preserved on
 * purpose. The staleness verdict (`stale`) and the age (`age_seconds`) are
 * the API's own; `capturedLabel` only gives the age a human shape, via the
 * same `ageLabel` the price-comparison page already uses — one clock
 * reads the same way everywhere it appears. A GPU the API did not quote is
 * simply absent from `rows`; this module fabricates no zero and no filler
 * row for it.
 *
 * `fetchLandingPrices()` NEVER THROWS. A fetch reject, a non-200, a body
 * that is not JSON, or a body that is JSON but not shaped like the
 * contract all become `null` — the same outcome, on purpose, so a
 * server-rendered marketing page never 500s because a stranger's browser
 * happened to load while the API was mid-deploy. `PriceBoard.tsx` turns
 * `null` (and an empty `rows`) into one quiet line, never an error page and
 * never an invented number.
 */
import { cloudApiBase } from "@/lib/cloud-api";
import { ageLabel } from "@/lib/market-prices";

/** `trend.direction` per the contract. `"new"` is a single observation with
 * no prior quote to compare against — it carries no other trend field. */
export type TrendDirection = "up" | "down" | "flat" | "new";

/** The trend attached to one row. `pct`, `previous_amount` and
 * `previous_captured_at` are genuinely OPTIONAL, not merely nullable: the
 * API omits all three outright when `direction` is `"new"`, and a type that
 * required them would force this module to invent a previous quote that
 * was never observed. */
export interface PriceTrend {
  direction: TrendDirection;
  pct?: string;
  previous_amount?: string;
  previous_captured_at?: string;
}

/** One curated GPU's current quote, exactly as `GET /v1alpha1/public/prices`
 * sends it. `amount` is the vendor's decimal string — see the module
 * docstring's price honesty discipline. */
export interface PriceBoardRow {
  gpu: string;
  sku: string;
  provider: string;
  tier: string;
  amount: string;
  currency: string;
  unit: string;
  captured_at: string;
  age_seconds: number;
  stale: boolean;
  trend: PriceTrend;
}

/** The endpoint's whole response. `rows` is `[]`, never absent, when the
 * curated list has no observations yet — still a 200, still a real (empty)
 * board, never a reason to treat the fetch as having failed. */
export interface PriceBoardData {
  generated_at: string;
  rows: PriceBoardRow[];
}

const TREND_GLYPH: Record<TrendDirection, string> = {
  up: "▲",
  down: "▼",
  flat: "–",
  new: "NEW",
};

/** The glyph a trend renders as. `"new"` is a text chip, not a glyph — a
 * single observation has no direction to draw an arrow for. */
export function trendGlyph(direction: TrendDirection): string {
  return TREND_GLYPH[direction];
}

const TREND_TONE: Record<TrendDirection, string> = {
  // Inverted from how a stock ticker would color it, on purpose: this board
  // serves a buyer deciding where to place a job, not a shareholder in the
  // venue. A RISING price is bad news for that buyer (warning tone); a
  // FALLING price is good news (healthy tone).
  up: "text-[var(--z-warning)]",
  down: "text-[var(--z-healthy)]",
  flat: "text-muted-foreground",
  new: "text-muted-foreground",
};

/** The tone class a trend renders with. See `TREND_TONE` above for why
 * "down" is healthy and "up" is a warning. */
export function trendToneClass(direction: TrendDirection): string {
  return TREND_TONE[direction];
}

/** The provenance cell's relative half: "observed <ageLabel>", or
 * "stale — observed <ageLabel>" when the API's own `stale` verdict says so —
 * the identical convention `lib/market-prices.ts`'s `quoteRow` already uses
 * for `capturedText`, so a stale quote reads the same way on both pages.
 * Never says "live" — the most a fresh quote may claim is how long ago it
 * was actually observed. */
export function capturedLabel(row: Pick<PriceBoardRow, "age_seconds" | "stale">): string {
  const observed = `observed ${ageLabel(row.age_seconds)}`;
  return row.stale ? `stale — ${observed}` : observed;
}

/** The provenance cell's absolute half: "2026-08-13 12:00 UTC", parsed
 * straight from `captured_at`.
 *
 * WHY THIS EXISTS ALONGSIDE `capturedLabel`. This page prerenders
 * statically, and `flashml-web` on Render is a free plan with manual
 * deploys — so the first visitor after a cold start can be looking at a
 * relative label ("observed 3h ago") that was computed at BUILD time and is
 * now days stale itself. A relative label can only ever be honest as of the
 * moment it was rendered; an absolute UTC date parsed from the API's own
 * `captured_at` cannot misstate itself no matter how long the prerender
 * sits, so the two are always shown together — never the relative label
 * alone. */
export function capturedAbsoluteLabel(row: Pick<PriceBoardRow, "captured_at">): string {
  const d = new Date(row.captured_at);
  if (Number.isNaN(d.getTime())) return row.captured_at;
  const pad = (n: number) => String(n).padStart(2, "0");
  const date = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  const time = `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
  return `${date} ${time} UTC`;
}

const TREND_DIRECTIONS: readonly TrendDirection[] = ["up", "down", "flat", "new"];

function isTrend(value: unknown): value is PriceTrend {
  if (!value || typeof value !== "object") return false;
  const direction = (value as { direction?: unknown }).direction;
  if (typeof direction !== "string" || !TREND_DIRECTIONS.includes(direction as TrendDirection)) {
    return false;
  }
  // "new" carries no other trend fields — nothing further to check.
  if (direction === "new") return true;
  const v = value as Record<string, unknown>;
  return (
    typeof v.pct === "string" &&
    typeof v.previous_amount === "string" &&
    typeof v.previous_captured_at === "string"
  );
}

function isRow(value: unknown): value is PriceBoardRow {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.gpu === "string" &&
    typeof v.sku === "string" &&
    typeof v.provider === "string" &&
    typeof v.tier === "string" &&
    typeof v.amount === "string" &&
    typeof v.currency === "string" &&
    typeof v.unit === "string" &&
    typeof v.captured_at === "string" &&
    typeof v.age_seconds === "number" &&
    typeof v.stale === "boolean" &&
    isTrend(v.trend)
  );
}

/** Server-side fetch of the curated price board. Resolves the API's base
 * URL exactly the way the rest of the app does — `cloudApiBase()` from
 * `lib/cloud-api.ts`, which reads `NEXT_PUBLIC_CLOUD_API` and falls back to
 * `http://localhost:8000` — so this module has no second opinion about
 * where the API lives.
 *
 * `next: { revalidate: 300 }` matches the endpoint's own 60s server-side
 * cache with headroom rather than hammering it every render: the board is
 * "today's prices", not a ticker, and this page has no client-side polling
 * to keep it any fresher than that.
 *
 * Returns `null` on ANY failure — a rejected fetch, a non-200, a body that
 * is not valid JSON, or a body that does not match the contract above.
 * Never throws: see the module docstring for why a marketing page cannot
 * afford to. */
export async function fetchLandingPrices(): Promise<PriceBoardData | null> {
  try {
    const res = await fetch(`${cloudApiBase()}/v1alpha1/public/prices`, {
      next: { revalidate: 300 },
    });
    if (!res.ok) return null;

    const body: unknown = await res.json();
    if (!body || typeof body !== "object") return null;

    const { generated_at, rows } = body as Record<string, unknown>;
    if (typeof generated_at !== "string") return null;
    if (!Array.isArray(rows) || !rows.every(isRow)) return null;

    return { generated_at, rows };
  } catch {
    // Transport failure, DNS, offline, a proxy returning HTML — none of it
    // is a reason for a landing page render to throw.
    return null;
  }
}
