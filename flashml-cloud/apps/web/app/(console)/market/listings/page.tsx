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

/** The listings page: the open book grouped by capability class, the
 * market's read on a machine you might list, and your own listings with
 * the offer and withdraw actions. The API enforces admission on both
 * writes; the console shows the door to admitted accounts only.
 *
 * The book and the machine list are read INDEPENDENTLY: a failed book
 * read renders "unreadable" over the book, and must never empty the
 * picker — they are different facts from different routes. */
export default function ListingsPage() {
  const router = useRouter();
  const [state, setState] = useState<"loading" | "present" | "unreadable">(
    "loading"
  );
  const [data, setData] = useState<MarketListings | null>(null);
  const [machines, setMachines] = useState<Machine[]>([]);
  const [machinesLoaded, setMachinesLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // No synchronous setState here: mount goes through the effect with the
  // initial "loading" state; the retry button goes through `retry`.
  const load = useCallback(() => {
    listMachines()
      .then((found) => setMachines(found))
      .catch(() => setMachines([]))
      .finally(() => setMachinesLoaded(true));

    getMarketListings()
      .then((listings) => {
        setData(listings);
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
    async (listingId: string): Promise<void> => {
      try {
        await withdrawMarketListing(listingId);
        await load();
      } catch (err) {
        if (err instanceof NotAuthenticated) {
          router.push("/sign-in?next=/market/listings");
          return;
        }
        toast.error("Couldn't withdraw the listing", {
          description: err instanceof Error ? err.message : undefined,
        });
      }
    },
    [load, router]
  );

  const books = data ? groupBookByClass(data.asks) : [];

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="title">Listings</h1>
          <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
            Capacity offered at its host&apos;s own ask, ranked by what a
            buyer actually pays per accepted result. A zero ask is donated,
            and an unproven host says so rather than borrowing a number.
          </p>
        </div>
        {/* The header strip exists only when there is a market to count:
            real rows only, absent (not zeroed) for an empty book. */}
        {state === "present" && data && data.asks.length > 0 && (
          <div className="flex gap-6 pb-1">
            <HeaderStat value={data.asks.length} label="Open asks" />
            <HeaderStat
              value={books.length}
              label={books.length === 1 ? "Class" : "Classes"}
            />
            <HeaderStat value={data.mine.length} label="Your listings" />
          </div>
        )}
      </div>

      <div className="mt-8">
        <ListingsPanel
          state={state}
          books={books}
          mine={data?.mine ?? []}
          machines={machines}
          machinesLoaded={machinesLoaded}
          onList={onList}
          onWithdraw={onWithdraw}
          onRetry={retry}
          error={error}
        />
      </div>
    </div>
  );
}

/** A right-aligned header figure: a real count, mono, with a caps label. */
function HeaderStat({ value, label }: { value: number; label: string }) {
  return (
    <div className="text-right">
      <div className="metric-value text-xl">{value}</div>
      <div className="label-caps mt-0.5">{label}</div>
    </div>
  );
}
