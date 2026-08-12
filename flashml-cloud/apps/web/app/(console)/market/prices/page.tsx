"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { PricesPanel } from "@/components/market/PricesPanel";
import { NotAuthenticated, getPrices, type PricesView } from "@/lib/cloud-api";
import { quoteRow, unpricedRow, zcRungRow } from "@/lib/market-prices";

/** The pitch: what capacity costs here and elsewhere, side by side and
 * never converted. Quotes carry their capture time, source and the API's
 * staleness verdict; venues with no quote render as not observed. */
export default function PricesPage() {
  const router = useRouter();
  const [state, setState] = useState<"loading" | "present" | "unreadable">(
    "loading"
  );
  const [data, setData] = useState<PricesView | null>(null);
  const [error, setError] = useState<string | null>(null);

  // No synchronous setState here: mount goes through the effect with the
  // initial "loading" state; the retry button goes through `retry`.
  const load = useCallback(() => {
    getPrices()
      .then((view) => {
        setData(view);
        setError(null);
        setState("present");
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push("/sign-in?next=/market/prices");
          return;
        }
        setError(err instanceof Error ? err.message : "Couldn't load.");
        setState("unreadable");
      });
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  const retry = useCallback(() => {
    setState("loading");
    load();
  }, [load]);

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
      <h1 className="title">Prices</h1>
      <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
        Every quote below says when it was observed and where it came from,
        and a stale one says so — a scraped price must never sit here
        looking live.
      </p>

      <div className="mt-6">
        <PricesPanel
          state={state}
          zcRows={data ? data.zc.map(zcRungRow) : []}
          quoteRows={data ? data.quotes.map(quoteRow) : []}
          unpricedRows={data ? data.unpriced.map(unpricedRow) : []}
          onRetry={retry}
          error={error}
        />
      </div>
    </div>
  );
}
