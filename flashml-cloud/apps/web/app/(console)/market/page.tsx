"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { CreditsPanel } from "@/components/market/CreditsPanel";
import { MatchesPanel } from "@/components/market/MatchesPanel";
import {
  NotAuthenticated,
  getCredits,
  getCreditsLedger,
  getMarketMatches,
  type CreditsBalance,
  type LedgerMovement,
  type MarketMatches,
} from "@/lib/cloud-api";
import { ledgerRows, type LedgerRow } from "@/lib/market-credits";

/** The market's account page: balance, ledger, and this account's priced
 * entitlements. Reads are classified — loading, present, unreadable — and
 * a failed read renders as unreadable, never as an empty account. */
export default function MarketPage() {
  const router = useRouter();
  const [state, setState] = useState<"loading" | "present" | "unreadable">(
    "loading"
  );
  const [balance, setBalance] = useState<CreditsBalance | null>(null);
  const [movements, setMovements] = useState<LedgerMovement[]>([]);
  const [nextBefore, setNextBefore] = useState<number | null>(null);
  const [matches, setMatches] = useState<MarketMatches | null>(null);
  const [error, setError] = useState<string | null>(null);

  // No synchronous setState here: this runs from an effect on mount, and
  // the initial state is already "loading". Retries go through `retry`,
  // which is a click handler and may say so.
  const load = useCallback(() => {
    Promise.all([
      getCredits(),
      getCreditsLedger({ limit: 25 }),
      getMarketMatches().catch(() => null),
    ])
      .then(([credits, ledger, found]) => {
        setBalance(credits);
        setMovements(ledger.movements);
        setNextBefore(ledger.next_before);
        setMatches(found);
        setError(null);
        setState("present");
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push("/sign-in?next=/market");
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

  const loadMore = useCallback(() => {
    if (nextBefore === null) return;
    getCreditsLedger({ limit: 25, before: nextBefore })
      .then((ledger) => {
        setMovements((prev) => [...prev, ...ledger.movements]);
        setNextBefore(ledger.next_before);
      })
      .catch(() => {
        // Paging that fails leaves the rows already shown where they are:
        // the button stays, the retry is one click.
      });
  }, [nextBefore]);

  const rows: LedgerRow[] = ledgerRows(movements);

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
      <h1 className="title">Market</h1>
      <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
        Your Zolli Credits and the double-entry ledger behind them — every
        movement shown with its counterparty, because a balance change
        without a cause is not a ledger.
      </p>

      <div className="mt-6 space-y-6">
        <CreditsPanel
          state={state}
          balance={balance}
          rows={rows}
          nextBefore={nextBefore}
          onLoadMore={loadMore}
          onRetry={retry}
          error={error}
        />
        <MatchesPanel matches={state === "present" ? matches : null} />
      </div>
    </div>
  );
}
