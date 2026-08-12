"use client";

import type { MarketMatch } from "@/lib/cloud-api";
import { formatZc } from "@/lib/market-credits";

/** This account's priced entitlements, on both sides, state verbatim.
 *
 * Markup only. The state column is the API's vocabulary — granted,
 * claimed, settled, refunded, expired — because a match is an
 * entitlement, not an assignment, and the one misunderstanding this panel
 * can cause is a buyer reading `granted` as "work was assigned". The
 * sentence under the heading says so up front. */
export function MatchesPanel({
  matches,
}: {
  matches: { as_buyer: MarketMatch[]; as_host: MarketMatch[] } | null;
}) {
  if (matches === null) return null;
  const empty =
    matches.as_buyer.length === 0 && matches.as_host.length === 0;

  return (
    <section className="rounded-lg border border-border bg-surface p-4">
      <h2 className="text-sm font-semibold">Matches</h2>
      <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted-foreground">
        Priced entitlements, not assignments: a granted match entitles the
        host to pull the work; escrow is held only when a lease is claimed,
        and settles only for accepted work.
      </p>

      {empty ? (
        <p className="mt-3 max-w-prose text-sm text-muted-foreground">
          No matches on either side yet. A match appears when a bid clears
          an ask in one of the books.
        </p>
      ) : (
        <div className="mt-4 grid gap-6 lg:grid-cols-2">
          <MatchList title="As buyer" matches={matches.as_buyer} />
          <MatchList title="As host" matches={matches.as_host} />
        </div>
      )}
    </section>
  );
}

function MatchList({
  title,
  matches,
}: {
  title: string;
  matches: MarketMatch[];
}) {
  return (
    <div>
      <h3 className="label-caps">{title}</h3>
      {matches.length === 0 ? (
        <p className="mt-2 text-sm text-muted-foreground">none</p>
      ) : (
        <ul className="mt-2 divide-y divide-border rounded-md border border-border">
          {matches.map((m) => (
            <li key={m.id} className="px-3 py-2.5">
              <div className="flex items-baseline justify-between gap-4">
                <span className="font-mono text-xs">
                  {m.capability_class} · {m.tasks} task
                  {m.tasks === 1 ? "" : "s"}
                </span>
                <span className="shrink-0 font-mono text-xs">
                  {formatZc(m.agreed_zc_per_hour)} ZC/hour
                </span>
              </div>
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                <StateWord state={m.state} />
                {m.held_zc > 0 && ` · ${formatZc(m.held_zc)} ZC held`}
                {m.charged_zc > 0 && ` · ${formatZc(m.charged_zc)} ZC settled`}
                {m.refunded_zc > 0 &&
                  ` · ${formatZc(m.refunded_zc)} ZC refunded`}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The state in words a buyer cannot misread. `granted` says explicitly
 * that no money has moved; the closed states say what ended them. */
function StateWord({ state }: { state: string }) {
  switch (state) {
    case "granted":
      return "granted — entitled, no money moved yet";
    case "claimed":
      return "claimed — escrow held against the lease";
    case "settled":
      return "settled — paid for accepted work";
    case "refunded":
      return "refunded — the attempt produced nothing accepted";
    case "expired":
      return "expired — the hold went back to the buyer";
    default:
      return `state the console does not recognise ("${state}")`;
  }
}
