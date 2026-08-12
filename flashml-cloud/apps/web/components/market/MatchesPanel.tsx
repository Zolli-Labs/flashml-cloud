"use client";

import { Coins, HandCoins, type Icon } from "@phosphor-icons/react";

import type { MarketMatches } from "@/lib/cloud-api";
import { matchCards, type MatchCard } from "@/lib/market-credits";

/** This account's priced entitlements, on both sides, drawn as cards.
 *
 * Markup only: `matchCards` in `lib/market-credits.ts` turns each API
 * match into its card — the class, the tasks × price line, the state
 * badge with its one-line consequence, and the held/settled/refunded
 * mini-stats (present only when non-zero). Nothing here re-derives a
 * state or invents an amount.
 *
 * The state column stays the API's vocabulary — granted, claimed,
 * settled, refunded, expired — because a match is an entitlement, not an
 * assignment, and the one misunderstanding this panel can cause is a
 * buyer reading `granted` as "work was assigned". The sentence under the
 * heading says so up front. */
export function MatchesPanel({ matches }: { matches: MarketMatches | null }) {
  if (matches === null) return null;
  const cards = matchCards(matches);

  return (
    <section>
      <h2 className="text-sm font-semibold">Matches</h2>
      <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted-foreground">
        Priced entitlements, not assignments: a granted match entitles the
        host to pull the work; escrow is held only when a lease is claimed,
        and settles only for accepted work.
      </p>

      <div className="mt-4 grid gap-6 lg:grid-cols-2">
        <MatchColumn
          title="As buyer"
          cards={cards.buyer}
          icon={Coins}
          emptySentence="No matches as a buyer yet — a match appears when a bid clears an ask in one of the books."
        />
        <MatchColumn
          title="As host"
          cards={cards.host}
          icon={HandCoins}
          emptySentence="No matches as a host yet — a match appears when a bid clears an ask in one of the books."
        />
      </div>
    </section>
  );
}

/** One side of the market: a column of match cards, or the designed empty
 * state — an icon in a dashed square and one sentence, never a bare
 * "none". */
function MatchColumn({
  title,
  cards,
  icon: Icon,
  emptySentence,
}: {
  title: string;
  cards: MatchCard[];
  icon: Icon;
  emptySentence: string;
}) {
  return (
    <div>
      <h3 className="label-caps">{title}</h3>
      {cards.length === 0 ? (
        <div className="mt-2 flex flex-col items-center justify-center rounded-lg border border-dashed border-border px-4 py-8 text-center">
          <div className="flex h-9 w-9 items-center justify-center rounded-md border border-dashed border-border text-muted-foreground">
            <Icon className="h-4 w-4" />
          </div>
          <p className="mt-2 max-w-prose text-sm text-muted-foreground">
            {emptySentence}
          </p>
        </div>
      ) : (
        <div className="mt-2 space-y-3">
          {cards.map((card) => (
            <MatchCardView key={card.id} card={card} />
          ))}
        </div>
      )}
    </div>
  );
}

/** One match as a card: class chip and state badge on the first line, the
 * tasks × agreed price in mono, and the money mini-stats when the lib
 * says there are any. */
function MatchCardView({ card }: { card: MatchCard }) {
  return (
    <div className="rounded-lg border border-border bg-surface p-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="rounded-full border border-border px-1.5 py-0.5 font-mono text-[10px] text-foreground">
          {card.capabilityClass}
        </span>
        <span className="rounded-full border border-border px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
          {card.stateBadge}
        </span>
      </div>

      <div className="mt-3 font-mono text-xs">
        {card.tasks} × {card.priceText}
      </div>

      {card.mini.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2 border-t border-border pt-3">
          {card.mini.map((stat) => (
            <div key={stat.label}>
              <div className="label-caps">{stat.label}</div>
              <div className="mt-0.5 font-mono text-xs">
                {stat.valueText} ZC
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
