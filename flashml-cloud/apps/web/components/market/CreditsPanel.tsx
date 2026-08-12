"use client";

import { Warning } from "@phosphor-icons/react";

import type { CreditsBalance } from "@/lib/cloud-api";
import { formatZc, type LedgerRow } from "@/lib/market-credits";

/** The credits card: the account's balance, spendable vs held, and the
 * ledger as movements with their counterparties.
 *
 * Markup only — every number and sentence arrives as a value from
 * `lib/market-credits.ts` and `lib/cloud-api.ts`. Spendable and held are
 * two tiles, never one netted number: credits in escrow are not credits
 * you can spend, and a single balance would say they are. */
export function CreditsPanel({
  state,
  balance,
  rows,
  nextBefore,
  onLoadMore,
  onRetry,
  error,
}: {
  state: "loading" | "present" | "unreadable";
  balance: CreditsBalance | null;
  rows: LedgerRow[];
  nextBefore: number | null;
  onLoadMore: () => void;
  onRetry: () => void;
  error: string | null;
}) {
  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <h2 className="text-sm font-semibold">Credits</h2>

      {state === "loading" && (
        <p className="mt-3 font-mono text-xs text-muted-foreground">
          reading your balance…
        </p>
      )}

      {state === "unreadable" && (
        <div className="mt-3">
          <p className="flex max-w-prose items-start gap-2 text-sm leading-relaxed text-warning-foreground">
            <Warning className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
            <span>
              Couldn&apos;t read your credits. A failed read is not a zero
              balance — this console does not know either way.
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

      {state === "present" && balance && (
        <>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <div className="rounded-lg border border-border bg-surface px-4 py-3.5">
              <div className="metric-value text-2xl">
                {formatZc(balance.spendable_zc)}
              </div>
              <div className="label-caps mt-1">ZC spendable</div>
            </div>
            <div className="rounded-lg border border-border bg-surface px-4 py-3.5">
              <div className="metric-value text-2xl">
                {formatZc(balance.held_zc)}
              </div>
              <div className="label-caps mt-1">ZC held in escrow</div>
              <p className="mt-1.5 text-[11px] leading-tight text-muted-foreground">
                Committed against claimed work; settled only for accepted
                work, refunded otherwise.
              </p>
            </div>
          </div>

          <h3 className="label-caps mt-6">Ledger</h3>
          {rows.length === 0 ? (
            <p className="mt-2 max-w-prose text-sm text-muted-foreground">
              No movements yet. The one-time starting grant appears here the
              first time your balance is read.
            </p>
          ) : (
            <ul className="mt-2 divide-y divide-border">
              {rows.map((row) => (
                <li
                  key={row.cursor}
                  className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-2.5"
                >
                  <div className="min-w-0">
                    <span className="text-sm">{row.label}</span>
                    <span className="ml-2 text-xs text-muted-foreground">
                      {row.counterparty}
                    </span>
                  </div>
                  <div className="flex shrink-0 items-baseline gap-3 font-mono text-xs">
                    <span>
                      {row.amountText === null ? "—" : row.amountText}
                    </span>
                    <span className="text-muted-foreground">
                      {time(row.at)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {nextBefore !== null && (
            <button
              type="button"
              onClick={onLoadMore}
              className="mt-3 rounded-md border border-border bg-surface px-2.5 py-1 text-xs hover:bg-surface-2"
            >
              Older movements
            </button>
          )}
        </>
      )}
    </section>
  );
}

/** The page's own clock format for an API timestamp; an em dash for a
 * value that does not parse, never "Invalid Date". */
function time(iso: string): string {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? "—" : new Date(ms).toLocaleString();
}
