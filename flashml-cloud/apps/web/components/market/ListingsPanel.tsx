"use client";

import { useState } from "react";
import { Warning } from "@phosphor-icons/react";

import type { Machine, MarketListing } from "@/lib/cloud-api";
import { formatZc } from "@/lib/market-credits";
import {
  askPriceLabel,
  listingStateLabel,
  recordLabel,
  type ClassBook,
} from "@/lib/market-listings";

/** The listings card: the open book grouped by capability class, and this
 * host's own listings with a withdraw action.
 *
 * Markup only. The book's ranking is the API's; the record sentence and
 * the price line come from `lib/market-listings.ts`. A class with no asks
 * is simply absent — an empty book renders the empty sentence, never an
 * invented row. */
export function ListingsPanel({
  state,
  books,
  mine,
  machines,
  onList,
  onWithdraw,
  onRetry,
  error,
}: {
  state: "loading" | "present" | "unreadable";
  books: ClassBook[];
  mine: MarketListing[];
  /** The account's own machines, for the listing form. */
  machines: Machine[];
  onList: (machineId: string, askMzc: number) => Promise<string | null>;
  onWithdraw: (listingId: string) => void;
  onRetry: () => void;
  error: string | null;
}) {
  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <h2 className="text-sm font-semibold">Listings</h2>

      {state === "loading" && (
        <p className="mt-3 font-mono text-xs text-muted-foreground">
          reading the book…
        </p>
      )}

      {state === "unreadable" && (
        <div className="mt-3">
          <p className="flex max-w-prose items-start gap-2 text-sm leading-relaxed text-warning-foreground">
            <Warning className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
            <span>
              Couldn&apos;t read the market book. That is not the same as an
              empty book.
            </span>
          </p>
          {error && (
            <p className="mt-1.5 break-all font-mono text-[11px] text-muted-foreground">
              {error}
            </p>
          )}
          <button
            type="button"
            onClick={onRetry}
            className="mt-3 rounded-md border border-border bg-surface px-2.5 py-1 text-xs hover:bg-surface-2"
          >
            Try again
          </button>
        </div>
      )}

      {state === "present" && (
        <>
          <div className="mt-3">
            {books.length === 0 ? (
              <p className="max-w-prose text-sm text-muted-foreground">
                The book is empty right now — nobody is offering capacity.
                List a machine below and it appears here at your ask.
              </p>
            ) : (
              <div className="space-y-5">
                {books.map((book) => (
                  <div key={book.capabilityClass}>
                    <h3 className="label-caps">{book.capabilityClass}</h3>
                    <ul className="mt-2 divide-y divide-border rounded-md border border-border">
                      {book.asks.map((ask) => (
                        <li
                          key={ask.id}
                          className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-3 py-2.5"
                        >
                          <div className="min-w-0">
                            <span className="font-mono text-xs">
                              {askPriceLabel(ask)}
                            </span>
                            <span className="ml-2 text-[11px] text-muted-foreground">
                              {recordLabel(ask)}
                            </span>
                          </div>
                          <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                            {ask.max_concurrent_tasks} concurrent task
                            {ask.max_concurrent_tasks === 1 ? "" : "s"}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </div>

          <h3 className="label-caps mt-6">Your listings</h3>
          {mine.length === 0 ? (
            <p className="mt-2 max-w-prose text-sm text-muted-foreground">
              You have no listings. Offer one of your machines at your own
              ask — zero is legal and reads as donated.
            </p>
          ) : (
            <ul className="mt-2 divide-y divide-border rounded-md border border-border">
              {mine.map((listing) => (
                <li
                  key={listing.id}
                  className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-3 py-2.5"
                >
                  <div className="min-w-0">
                    <span className="font-mono text-xs">
                      {listing.capability_class} ·{" "}
                      {formatZc(listing.ask_zc_per_hour)} ZC/hour
                    </span>
                    <span className="ml-2 text-[11px] text-muted-foreground">
                      {listingStateLabel(listing)}
                    </span>
                  </div>
                  {listing.state !== "withdrawn" && (
                    <button
                      type="button"
                      onClick={() => onWithdraw(listing.id)}
                      className="shrink-0 rounded-md border border-border px-2 py-0.5 text-[11px] text-muted-foreground hover:bg-surface-2 hover:text-foreground"
                    >
                      Withdraw
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}

          <ListForm machines={machines} onList={onList} />
        </>
      )}
    </section>
  );
}

/** Offer a machine at your own ask. The capability class is NOT an input:
 * the API computes it from what the agent reported, and the form says so,
 * because a host who could name their own class would sell a 3070 as
 * Hopper-class. */
function ListForm({
  machines,
  onList,
}: {
  machines: Machine[];
  onList: (machineId: string, askMzc: number) => Promise<string | null>;
}) {
  const [machineId, setMachineId] = useState("");
  const [ask, setAsk] = useState("0");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function submit() {
    // The ask field is a ZC decimal typed by a person; parse it to
    // millicredits with integer arithmetic only. A value that does not
    // parse is refused here, not rounded.
    const parsed = parseZcToMzc(ask);
    if (parsed === null) {
      setProblem("The ask must be a number of ZC per hour, e.g. 0.22 or 0.");
      return;
    }
    if (!machineId) {
      setProblem("Choose which machine to list.");
      return;
    }
    setBusy(true);
    setProblem(null);
    const err = await onList(machineId, parsed);
    setBusy(false);
    if (err) setProblem(err);
  }

  return (
    <div className="mt-6 rounded-md border border-border p-3">
      <h3 className="label-caps">List a machine</h3>
      {machines.length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">
          No machines on this account yet — enroll one under My machines
          first.
        </p>
      ) : (
        <div className="mt-2 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Machine
            <select
              value={machineId}
              onChange={(e) => setMachineId(e.target.value)}
              className="rounded-md border border-border bg-surface px-2 py-1.5 text-sm text-foreground"
            >
              <option value="">choose…</option>
              {machines.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Ask, ZC per hour
            <input
              value={ask}
              onChange={(e) => setAsk(e.target.value)}
              inputMode="decimal"
              className="w-28 rounded-md border border-border bg-surface px-2 py-1.5 font-mono text-sm text-foreground"
            />
          </label>
          <button
            type="button"
            disabled={busy}
            onClick={submit}
            className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-60"
          >
            {busy ? "Listing…" : "List"}
          </button>
        </div>
      )}
      <p className="mt-2 text-[11px] leading-tight text-muted-foreground">
        The capability class comes from what your agent reported about the
        machine — it is computed server-side and is not a choice here. Zero
        is a legal ask and reads as donated.
      </p>
      {problem && (
        <p className="mt-2 text-xs text-destructive">{problem}</p>
      )}
    </div>
  );
}

/** A person-typed ZC amount to millicredits, refusing anything that is
 * not a clean decimal with at most three places — the fourth place would
 * be a fraction of a millicredit the ledger cannot hold. Integer
 * arithmetic; no float multiply. */
export function parseZcToMzc(text: string): number | null {
  const trimmed = text.trim();
  if (!/^\d+(\.\d{1,3})?$/.test(trimmed)) return null;
  const [whole, frac = ""] = trimmed.split(".");
  return Number(whole) * 1000 + Number(frac.padEnd(3, "0") || "0");
}
