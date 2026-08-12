"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { ListingsPanel } from "@/components/market/ListingsPanel";
import {
  ApiError,
  NotAuthenticated,
  createMarketListing,
  getMarketListings,
  listMachines,
  withdrawMarketListing,
  type Machine,
  type MarketListings,
} from "@/lib/cloud-api";
import { groupBookByClass } from "@/lib/market-listings";

/** The book: open asks grouped by capability class, and this host's own
 * listings with the offer and withdraw actions. The API enforces
 * admission on both writes; the console shows the door to admitted
 * accounts only. */
export default function ListingsPage() {
  const router = useRouter();
  const [state, setState] = useState<"loading" | "present" | "unreadable">(
    "loading"
  );
  const [data, setData] = useState<MarketListings | null>(null);
  const [machines, setMachines] = useState<Machine[]>([]);
  const [error, setError] = useState<string | null>(null);

  // No synchronous setState here: mount goes through the effect with the
  // initial "loading" state; the retry button goes through `retry`.
  const load = useCallback(() => {
    Promise.all([
      getMarketListings(),
      listMachines().catch(() => [] as Machine[]),
    ])
      .then(([listings, found]) => {
        setData(listings);
        setMachines(found);
        setError(null);
        setState("present");
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push("/sign-in?next=/market/listings");
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

  const onList = useCallback(
    async (machineId: string, askMzc: number): Promise<string | null> => {
      try {
        await createMarketListing({
          machine_id: machineId,
          ask_zc_per_hour: askMzc,
        });
        load();
        return null;
      } catch (err) {
        if (err instanceof NotAuthenticated) {
          router.push("/sign-in?next=/market/listings");
          return null;
        }
        return err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : "Couldn't list the machine.";
      }
    },
    [load, router]
  );

  const onWithdraw = useCallback(
    (listingId: string) => {
      withdrawMarketListing(listingId)
        .then(() => load())
        .catch((err) => {
          toast.error("Couldn't withdraw the listing", {
            description:
              err instanceof Error ? err.message : undefined,
          });
        });
    },
    [load]
  );

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
      <h1 className="title">Listings</h1>
      <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
        Capacity offered at its host&apos;s own ask, ranked by what a buyer
        actually pays per accepted result. A zero ask is donated, and an
        unproven host says so rather than borrowing a number.
      </p>

      <div className="mt-6">
        <ListingsPanel
          state={state}
          books={data ? groupBookByClass(data.asks) : []}
          mine={data?.mine ?? []}
          machines={machines}
          onList={onList}
          onWithdraw={onWithdraw}
          onRetry={retry}
          error={error}
        />
      </div>
    </div>
  );
}
