"use client";

import { Fragment } from "react";
import {
  ArrowsLeftRight,
  Coin,
  Lock,
  LockOpen,
  Question,
  TrendDown,
  TrendUp,
  Wallet,
  Warning,
  type Icon,
} from "@phosphor-icons/react";

import type { CreditsBalance, LedgerMovement } from "@/lib/cloud-api";
import {
  activityStrip,
  amountTone,
  groupRowsByDay,
  movementIcon,
  walletTiles,
  type LedgerRow,
} from "@/lib/market-credits";

/** The wallet: four header tiles, an activity strip, and the ledger as a
 * table — movements with their counterparties, amounts right-aligned.
 *
 * Markup only. Every number and sentence arrives as a value from
 * `lib/market-credits.ts`: the tiles from `walletTiles`, the chips from
 * `activityStrip`, the rows from `ledgerRows` (computed by the page), the
 * per-row icon from `movementIcon` and the per-row colour from
 * `amountTone`. Nothing here re-derives a label or a tone, and nothing
 * renders a number the API did not return: a null amount is an em dash,
 * a failed read is unreadable, never an empty account.
 *
 * `movements` travels alongside `rows` because the icon and the tone are
 * facts about the movement, looked up by cursor — the row carries the
 * display text, the movement carries the verdict. */
export function CreditsPanel({
  state,
  balance,
  movements,
  rows,
  nextBefore,
  onLoadMore,
  onRetry,
  error,
}: {
  state: "loading" | "present" | "unreadable";
  balance: CreditsBalance | null;
  movements: LedgerMovement[];
  rows: LedgerRow[];
  nextBefore: number | null;
  onLoadMore: () => void;
  onRetry: () => void;
  error: string | null;
}) {
  return (
    <section>
      <h2 className="text-sm font-semibold">Credits</h2>

      {state === "loading" && (
        <div className="mt-3">
          {/* Skeletons shaped like what is arriving: the four tiles, then
              the ledger. The mono line says the read is in flight. */}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="skeleton h-[128px] rounded-lg" />
            ))}
          </div>
          <p className="mt-4 font-mono text-xs text-muted-foreground">
            reading your credits…
          </p>
          <div className="skeleton mt-4 h-44 rounded-lg" />
        </div>
      )}

      {state === "unreadable" && (
        <div className="mt-3 rounded-lg border border-border bg-surface p-4">
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
        <PresentWallet
          balance={balance}
          movements={movements}
          rows={rows}
          nextBefore={nextBefore}
          onLoadMore={onLoadMore}
        />
      )}
    </section>
  );
}

/** The icon key the lib assigns a tile, mapped to the glyph that draws it.
 * The keys come from `walletTiles`; lib stays free of React. */
const TILE_ICONS: Record<string, Icon> = {
  spendable: Wallet,
  held: Lock,
  earned: TrendUp,
  spent: TrendDown,
};

/** The semantic key `movementIcon` returns, mapped to its phosphor glyph:
 * Coin for the grant, Lock for a hold, LockOpen for a release or refund,
 * ArrowsLeftRight for a settlement, and a neutral Question for a reason
 * the console does not recognise — never the nearest known glyph. */
const MOVEMENT_ICONS: Record<string, Icon> = {
  grant: Coin,
  hold: Lock,
  release: LockOpen,
  settle: ArrowsLeftRight,
  unknown: Question,
};

/** The colour for `amountTone`'s verdict: evergreen for a credit to
 * spendable, ink for a debit, muted for a self-transfer. */
const TONE_CLASS: Record<"credit" | "debit" | "self", string> = {
  credit: "text-evergreen",
  debit: "text-foreground",
  self: "text-muted-foreground",
};

function PresentWallet({
  balance,
  movements,
  rows,
  nextBefore,
  onLoadMore,
}: {
  balance: CreditsBalance;
  movements: LedgerMovement[];
  rows: LedgerRow[];
  nextBefore: number | null;
  onLoadMore: () => void;
}) {
  const tiles = walletTiles(balance);
  const chips = activityStrip(movements);
  const byCursor = new Map<number, LedgerMovement>(
    movements.map((m) => [m.cursor, m])
  );
  const grouped = groupRowsByDay(rows);

  return (
    <>
      {/* The wallet header: four tiles, then the activity strip computed
          from the ledger page in view. */}
      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {tiles.map((tile) => {
          const Icon = TILE_ICONS[tile.icon] ?? Wallet;
          return (
            <div
              key={tile.label}
              className="rounded-lg border border-border bg-surface px-4 py-3.5"
            >
              <div className="flex h-9 w-9 items-center justify-center rounded-md bg-primary/10 text-primary">
                <Icon className="h-4 w-4" />
              </div>
              <div className="metric-value mt-3 text-2xl">
                {tile.valueText}
              </div>
              <div className="label-caps mt-1">{tile.label}</div>
              <p className="mt-1 text-[11px] leading-tight text-muted-foreground">
                {tile.sub}
              </p>
            </div>
          );
        })}
      </div>

      {/* Real counts of the rows in view; an empty ledger has no strip,
          not a strip of zeros. */}
      {chips.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          {chips.map((chip) => (
            <span
              key={chip}
              className="rounded-full border border-border px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground"
            >
              {chip}
            </span>
          ))}
        </div>
      )}

      {/* The ledger as a table: a header row, day separators, and rows
          carrying their icon, counterparty, and signed amount. */}
      <div className="mt-6 rounded-lg border border-border bg-surface">
        <h3 className="label-caps px-4 pt-4">Ledger</h3>

        {rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center px-4 pb-8 pt-6 text-center">
            <div className="flex h-9 w-9 items-center justify-center rounded-md border border-dashed border-border text-muted-foreground">
              <Coin className="h-4 w-4" />
            </div>
            <p className="mt-2 max-w-prose text-sm text-muted-foreground">
              No movements yet — the one-time starting grant appears here the
              first time your balance is read.
            </p>
          </div>
        ) : (
          <div className="mt-2 overflow-x-auto px-4 pb-4">
            <table className="w-full min-w-[36rem] text-sm">
              <thead>
                <tr>
                  <th scope="col" className="label-caps pb-2 pr-3 text-left font-medium">
                    When
                  </th>
                  <th scope="col" className="label-caps pb-2 pr-3 text-left font-medium">
                    Movement
                  </th>
                  <th scope="col" className="label-caps pb-2 pr-3 text-left font-medium">
                    Counterparty
                  </th>
                  <th scope="col" className="label-caps pb-2 text-right font-medium">
                    Amount
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {grouped.map((group) => (
                  <Fragment key={group.day}>
                    <tr>
                      <td colSpan={4} className="label-caps pb-1.5 pt-4">
                        {group.day}
                      </td>
                    </tr>
                    {group.rows.map((row) => {
                      const movement = byCursor.get(row.cursor);
                      const Icon =
                        MOVEMENT_ICONS[
                          movement ? movementIcon(movement.reason) : "unknown"
                        ] ?? Question;
                      const toneClass = movement
                        ? TONE_CLASS[amountTone(movement)]
                        : TONE_CLASS.self;
                      return (
                        <tr
                          key={row.cursor}
                          title={`${row.at}${row.ref ? ` · ${row.ref}` : ""}`}
                        >
                          <td className="whitespace-nowrap py-2.5 pr-3 align-top font-mono text-xs text-muted-foreground">
                            {localeTime(row.at)}
                          </td>
                          <td className="py-2.5 pr-3 align-top">
                            <div className="flex items-center gap-2.5">
                              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-surface-2 text-muted-foreground">
                                <Icon className="h-3 w-3" />
                              </span>
                              <span>{row.label}</span>
                            </div>
                          </td>
                          <td className="py-2.5 pr-3 align-top text-xs text-muted-foreground">
                            {row.counterparty}
                          </td>
                          <td
                            className={`py-2.5 text-right align-top font-mono text-xs ${toneClass}`}
                          >
                            {row.amountText === null ? "—" : row.amountText}
                          </td>
                        </tr>
                      );
                    })}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {nextBefore !== null && (
          <div className="border-t border-border px-4 py-3">
            <button
              type="button"
              onClick={onLoadMore}
              className="rounded-md border border-border bg-surface px-2.5 py-1 text-xs hover:bg-surface-2"
            >
              Older movements
            </button>
          </div>
        )}
      </div>
    </>
  );
}

/** The visible clock format for a ledger row: "12 Aug, 04:22". The full
 * ISO timestamp and the ref travel on the row's title attribute instead.
 * An unparseable value reads as an em dash, never "Invalid Date". */
function localeTime(iso: string): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "—";
  const d = new Date(ms);
  const date = d.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
  });
  const time = d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  });
  return `${date}, ${time}`;
}
