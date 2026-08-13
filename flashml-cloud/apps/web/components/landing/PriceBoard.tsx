import { SectionReveal } from "@/components/landing/motion/SectionReveal";
import {
  capturedAbsoluteLabel,
  capturedLabel,
  fetchLandingPrices,
  trendGlyph,
  trendToneClass,
  type PriceBoardRow,
} from "@/lib/landing/market-board";

/** The landing page's market board — an async server component, rendered
 * fresh on the server for every visitor, no client-side polling.
 *
 * Read `lib/landing/market-board.ts` before touching this file. Every fact
 * on screen comes from `fetchLandingPrices()`'s honest contract: an
 * `amount` is always the vendor's own decimal string, a provenance cell
 * always accompanies a price, and a GPU with no quote is missing from the
 * table rather than shown at zero. This component adds no numbers of its
 * own — it only lays the API's rows out.
 *
 * `fetchLandingPrices()` returning `null`, and an empty `rows` array, are
 * both real, unremarkable states: a stranger's first paint of this page
 * must never show a stack trace or a fabricated price just because the API
 * was mid-deploy or the curated list is still empty. Both render the same
 * quiet line. */
export async function PriceBoard() {
  const board = await fetchLandingPrices();
  const rows = board?.rows ?? [];

  return (
    <section
      id="market"
      data-surface="sand"
      data-layout="price-board"
      className="landing-surface-sand scroll-mt-20 border-b border-border py-20 md:py-28"
    >
      <div className="mx-auto max-w-[1240px] px-5 sm:px-6">
        <div className="grid gap-8 lg:grid-cols-[.72fr_1.28fr] lg:gap-12">
          <div>
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
              The compute market
            </p>
            <h2 className="mt-5 text-[clamp(2.65rem,5.4vw,4.75rem)] font-semibold leading-[0.99] tracking-[-0.052em]">
              Today&apos;s GPU prices. <span className="text-muted-foreground">Observed, not promised.</span>
            </h2>
          </div>
          <p className="max-w-[58ch] self-end text-base leading-relaxed text-muted-foreground">
            Every quote below carries when it was captured and whether it is stale — the same
            discipline the price-comparison page holds itself to.
          </p>
        </div>

        <SectionReveal
          className="mt-14 md:mt-20"
          lineClassName="h-px w-full bg-[var(--z-border-strong)]"
          bottomLineClassName="h-px w-full bg-[var(--z-border-strong)]"
        >
          {rows.length === 0 ? (
            <p className="py-10 text-sm leading-relaxed text-muted-foreground">
              No market observations yet.
            </p>
          ) : (
            <div className="overflow-x-auto py-2">
              <table className="w-full min-w-[720px] border-collapse text-left">
                <thead>
                  <tr className="border-b border-[var(--z-border-strong)]">
                    {["GPU", "Price", "Trend", "Tier", "Provenance"].map((heading) => (
                      <th
                        key={heading}
                        scope="col"
                        className="whitespace-nowrap px-3 py-3 font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-brand-foreground first:pl-0 last:pr-0"
                      >
                        {heading}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <PriceRow key={`${row.provider}-${row.gpu}-${row.tier}`} row={row} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </SectionReveal>

        <div className="mt-10 border-t border-[var(--z-border-strong)] pt-5 sm:mt-12 sm:pt-6">
          <div className="grid gap-8 lg:grid-cols-2">
            <article>
              <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
                For demand
              </p>
              <p className="mt-5 max-w-[42ch] text-xl leading-relaxed tracking-[-0.025em]">
                One request puts every source to work — your machines, community hosts, RunPod,
                and Alibaba Cloud compete on price.
              </p>
            </article>
            <article className="border-t border-border pt-8 lg:border-l lg:border-t-0 lg:pl-12 lg:pt-0">
              <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
                For supply
              </p>
              <p className="mt-5 max-w-[42ch] text-xl leading-relaxed tracking-[-0.025em]">
                Idle machines already cost you. Connect them and earn when they complete useful
                work.
              </p>
            </article>
          </div>
          <p className="mt-8 text-sm leading-relaxed text-muted-foreground">
            Early testing uses Zolli credits. Cash payout is not live.
          </p>
        </div>
      </div>
    </section>
  );
}

function PriceRow({ row }: { row: PriceBoardRow }) {
  const tone = trendToneClass(row.trend.direction);
  const isNew = row.trend.direction === "new";

  return (
    <tr data-price-row={row.gpu} className="border-b border-border last:border-b-0">
      <td className="whitespace-nowrap px-3 py-4 pl-0 text-[15px] font-medium">{row.gpu}</td>
      <td className="whitespace-nowrap px-3 py-4 font-mono tabular-nums">
        {row.currency} {row.amount}
        <span className="ml-1 text-sm text-muted-foreground">/ gpu-hr</span>
      </td>
      <td className={`whitespace-nowrap px-3 py-4 font-mono text-sm tabular-nums ${tone}`}>
        {isNew ? (
          // A single observation has no direction to compare — a chip
          // saying so, not a glyph implying a trend that was never measured.
          <span className="inline-flex items-center rounded-full border border-current px-1.5 py-0.5 text-[10px] tracking-wide">
            {trendGlyph("new")}
          </span>
        ) : (
          <>
            {trendGlyph(row.trend.direction)}
            <span className="ml-1">{row.trend.pct}%</span>
          </>
        )}
      </td>
      <td className="whitespace-nowrap px-3 py-4 text-sm text-muted-foreground">{row.tier}</td>
      <td className="whitespace-nowrap px-3 py-4 pr-0 text-sm text-muted-foreground">
        {/* The relative label alone can misstate itself once this page's
            static prerender goes stale between Render's manual deploys —
            the absolute UTC date beside it cannot, whatever the visitor's
            clock or the age of the build. */}
        <span className="block">{capturedLabel(row)}</span>
        <span className="block text-xs text-muted-foreground/70">
          {capturedAbsoluteLabel(row)}
        </span>
      </td>
    </tr>
  );
}
