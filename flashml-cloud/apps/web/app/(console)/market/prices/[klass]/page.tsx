"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Warning } from "@phosphor-icons/react";

import { ClassAsks } from "@/components/market/ClassAsks";
import { PriceHistory } from "@/components/market/PriceHistory";
import { SourceChip } from "@/components/market/SourceChip";
import { PageHeader } from "@/components/shell/PageHeader";
import { PageShell } from "@/components/shell/PageShell";
import { Button } from "@/components/ui/button";
import {
  NotAuthenticated,
  getMarketListings,
  getPrices,
  type MarketAsk,
  type PricesView,
} from "@/lib/cloud-api";
import { classHistory, classRow, classSpecLine } from "@/lib/market/board";
import { referenceClass } from "@/lib/market/reference";

/** One capability class, end to end: what it costs, what it has done, and
 * who is offering it right now.
 *
 * TWO INDEPENDENT READS. The board and the book are different routes and a
 * failure of one must not empty the other — a class with no readable book
 * still has a price history, and saying "no machines are listing this" when
 * the truth is "we could not ask" would be a lie the page tells silently.
 *
 * AN UNKNOWN TICKER IS NOT AN ERROR. A klass neither the coordinator nor
 * the seed knows gets one sentence and a way back, not an error wall: the
 * URL was mistyped, nothing failed. */
export default function ClassPricePage({
  params,
}: {
  params: Promise<{ klass: string }>;
}) {
  const router = useRouter();
  const klass = decodeKlass(use(params).klass);

  const [state, setState] = useState<"loading" | "present" | "unreadable">(
    "loading"
  );
  const [view, setView] = useState<PricesView | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The book gets its own state word rather than sharing the board's or
  // leaning on `asks === null`. Not-read-yet and could-not-read look
  // identical in an empty array, and the two reads finish at different
  // times — so an "unreadable" derived from emptiness renders a failure
  // message for every millisecond between them.
  const [asks, setAsks] = useState<MarketAsk[]>([]);
  const [asksState, setAsksState] = useState<
    "loading" | "present" | "unreadable"
  >("loading");
  const hadData = useRef(false);

  const load = useCallback(() => {
    getPrices()
      .then((v) => {
        hadData.current = true;
        setView(v);
        setError(null);
        setState("present");
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push(`/sign-in?next=/market/prices/${encodeURIComponent(klass)}`);
          return;
        }
        setError(err instanceof Error ? err.message : "Couldn't load.");
        if (!hadData.current) setState("unreadable");
      });

    getMarketListings()
      .then((listings) => {
        setAsks(listings.asks.filter((ask) => ask.capability_class === klass));
        setAsksState("present");
      })
      .catch(() => setAsksState("unreadable"));
  }, [klass, router]);

  useEffect(() => {
    load();
  }, [load]);

  const row = classRow(view, klass);
  const entry = referenceClass(klass);
  const spec = classSpecLine(entry);
  const rung = view?.zc.find((r) => r.capability_class === klass) ?? null;

  return (
    <PageShell width="wide">
      <PageHeader
        title={klass}
        titleTone="identifier"
        back={{ href: "/market/prices", label: "Prices" }}
        description={row?.displayName ?? undefined}
        meta={spec ?? undefined}
        actions={
          // Not while the board is still in flight. `classRow` answers from
          // the seed alone before the read lands, so a class that HAS a live
          // ask would show a reference price for a beat and then swap — a
          // number that was never wrong, in a place it was not yet right.
          state !== "loading" &&
          row && (
            <div className="text-right">
              <div className="flex items-baseline justify-end gap-2">
                <span className="metric-lg">{row.lastAskText}</span>
                <SourceChip source={row.source} />
              </div>
              <div className="mt-1 flex items-baseline justify-end gap-2 font-mono text-[11px] tabular-nums">
                {row.equivalentText && (
                  <span className="text-muted-foreground">
                    {row.equivalentText}
                  </span>
                )}
                {/* A derived movement never wears live green/red — the same
                    rule the board's 24h cell follows. */}
                <span
                  className={
                    row.change.reference
                      ? "text-muted-foreground"
                      : DELTA_TONE[row.change.direction]
                  }
                >
                  {row.change.text}
                </span>
              </div>
            </div>
          )
        }
      />

      {state === "loading" && (
        <div className="mt-6 space-y-4" aria-busy="true">
          <div className="skeleton h-40 w-full" />
          <div className="skeleton h-24 w-full" />
        </div>
      )}

      {state === "unreadable" && (
        <div className="mt-6 rounded-lg border border-destructive/30 bg-surface p-4">
          <p className="flex max-w-prose items-start gap-2 text-sm text-destructive">
            <Warning className="mt-0.5 h-4 w-4 shrink-0" weight="fill" />
            <span>
              Couldn&apos;t read the board. A failed read is unreadable, not
              empty.
            </span>
          </p>
          {error && (
            <p className="mt-1.5 break-all font-mono text-[11px] text-muted-foreground">
              {error}
            </p>
          )}
          <button
            type="button"
            onClick={load}
            className="mt-3 rounded-md border border-border bg-surface px-2.5 py-1 text-xs transition-colors hover:bg-surface-2"
          >
            Try again
          </button>
        </div>
      )}

      {state === "present" && row === null && (
        <p className="mt-6 text-sm text-muted-foreground">
          Nothing known about this class.{" "}
          <Link
            href="/market/prices"
            className="text-brand-foreground hover:underline"
          >
            Back to the board
          </Link>
          .
        </p>
      )}

      {state === "present" && row !== null && (
        <div className="mt-8 space-y-8">
          <section>
            <h2 className="label-caps">Price history</h2>
            <PriceHistory data={classHistory(rung, entry)} />
          </section>

          <section>
            <h2 className="label-caps">Hosting right now</h2>
            {asksState === "loading" && (
              <div className="skeleton mt-2 h-20 w-full" />
            )}
            {asksState === "unreadable" && (
              <p className="mt-2 text-sm text-muted-foreground">
                Couldn&apos;t read the book for this class — that is not the
                same as nobody hosting it.
              </p>
            )}
            {asksState === "present" && <ClassAsks asks={asks} />}
          </section>

          <div className="flex flex-wrap items-center gap-2">
            <Button render={<Link href="/deploy" />} nativeButton={false}>
              Deploy
            </Button>
            <Button
              variant="outline"
              render={<Link href="/market/listings" />}
              nativeButton={false}
            >
              List a machine at this class
            </Button>
          </div>
        </div>
      )}
    </PageShell>
  );
}

/** The ticker out of the URL. Next has already decoded the segment in the
 * ordinary case; this is for the one it has not, and it hands back the raw
 * text rather than throwing on a malformed escape — a bad URL should reach
 * "nothing known about this class", not a client-side exception. */
function decodeKlass(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

/** Market-convention colours for the header's 24h figure. */
const DELTA_TONE: Record<"up" | "down" | "none", string> = {
  up: "text-evergreen",
  down: "text-destructive",
  none: "text-muted-foreground",
};
