"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { PricesPanel } from "@/components/market/PricesPanel";
import { NotAuthenticated, getPrices, type PricesView } from "@/lib/cloud-api";

/** The compute board. Every number on the page comes from
 * `GET /v1alpha1/prices`: the capability classes as tickers (last live ask,
 * 24h movement, depth, observations) and the external venues' own quotes
 * beside them. ZC and vendor currencies are shown side by side and never
 * summed or converted — there is no exchange rate and no cell may imply
 * one. Not signed in goes to /sign-in; any other failed read is
 * unreadable, never an empty board. */
export default function PricesPage() {
  const router = useRouter();
  const [state, setState] = useState<"loading" | "present" | "unreadable">(
    "loading"
  );
  const [view, setView] = useState<PricesView | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    getPrices()
      .then((v) => {
        setView(v);
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
    setError(null);
    load();
  }, [load]);

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <h1 className="title">Prices</h1>
      <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
        Compute, read like a market: each capability class is a ticker with
        its last live ask in Zolli Credits, its 24-hour movement, and the
        observations behind it — external venues quoted beside the board in
        their own currencies, never converted.
      </p>

      <div className="mt-6">
        <PricesPanel state={state} view={view} onRetry={retry} error={error} />
      </div>
    </div>
  );
}
