import Link from "next/link";

import type { MarketAsk } from "@/lib/cloud-api";
import { askPriceLabel, askUsdEquivalentLabel } from "@/lib/market-listings";

/** Who is offering this class right now, one row per open ask.
 *
 * The price cell is `askPriceLabel`, not the ask's `price_label`: the
 * server field is the CATEGORY — "donated" or "priced" — and the helper is
 * what turns the category and the digits into the one string a buyer reads,
 * with a zero ask rendered as the word.
 *
 * `acceptance_rate` null means the host has no accepted-work record in this
 * class yet. That is "new", not 0% — a host who has never been asked has
 * not failed. */
export function ClassAsks({ asks }: { asks: MarketAsk[] }) {
  if (asks.length === 0) {
    return (
      <p className="mt-2 text-sm text-muted-foreground">
        No machines are listing this class right now.{" "}
        <Link
          href="/market/listings"
          className="text-brand-foreground hover:underline"
        >
          Open the book
        </Link>
        .
      </p>
    );
  }

  return (
    <div className="mt-2 overflow-x-auto rounded-lg border border-border bg-surface">
      <table className="w-full min-w-[560px] border-collapse text-xs">
        <thead>
          <tr className="border-b border-border">
            <th scope="col" className="label-caps px-3 py-2 text-left">
              Machine
            </th>
            <th scope="col" className="label-caps px-3 py-2 text-left">
              Reported hardware
            </th>
            <th scope="col" className="label-caps px-3 py-2 text-right">
              Ask
            </th>
            <th scope="col" className="label-caps px-3 py-2 text-right">
              Accepted
            </th>
            <th scope="col" className="label-caps px-3 py-2 text-right">
              Concurrency
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {asks.map((ask) => (
            <tr key={ask.id}>
              <td className="max-w-40 truncate px-3 py-2.5" title={ask.machine_id}>
                {ask.machine_name ?? (
                  <span className="font-mono text-muted-foreground">
                    {ask.machine_id.slice(0, 8)}
                  </span>
                )}
              </td>
              <td className="px-3 py-2.5 text-muted-foreground">
                {ask.gpu_label ?? "not reported"}
              </td>
              <td className="px-3 py-2.5 text-right font-mono tabular-nums">
                <div>{askPriceLabel(ask)}</div>
                <div className="mt-0.5 text-[10px] text-muted-foreground">
                  {askUsdEquivalentLabel(ask)}
                </div>
              </td>
              <td className="px-3 py-2.5 text-right font-mono tabular-nums">
                {ask.acceptance_rate === null ? (
                  <span className="text-muted-foreground">— new</span>
                ) : (
                  `${Math.round(ask.acceptance_rate * 100)}%`
                )}
              </td>
              <td className="px-3 py-2.5 text-right font-mono tabular-nums">
                {ask.max_concurrent_tasks}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
