"use client";

import Link from "next/link";
import { useState } from "react";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  VRAM_FILTERS,
  filterRows,
  kindChips,
  paginate,
  type BoardRow,
  type KindFilter,
  type VramFilter,
} from "@/lib/market/board";
import { REFERENCE_META } from "@/lib/market/reference";
import { SourceChip } from "./SourceChip";
import { Sparkline } from "./Sparkline";

/** The full board: every class, classified and paged ten at a time.
 *
 * WHY IT IS PAGED. Thirty-odd rows in one scroll is a listing, and a
 * listing is what the owner said this page still read as. Ten rows plus a
 * count is a board: it fits a screen, and the count tells you how much
 * board there is behind it rather than leaving you to guess from a
 * scrollbar.
 *
 * THE PAGE NUMBER IS DELIBERATELY NOT IN THE URL. A filter and a page are
 * a reading position, not a destination — nobody links a colleague to page
 * 3 of the CPU rows — and a URL that carries them turns every chip click
 * into a history entry to back out of. Component state, and the filters
 * reset it to the first page because a filter that leaves you on page 3 of
 * a one-page result looks like an empty board.
 *
 * Markup and state only: which rows a chip selects, how a band is bounded,
 * and what happens to a page number the row set no longer has all live in
 * `lib/market/board.ts`. */
export function BoardTable({ rows }: { rows: BoardRow[] }) {
  const [kind, setKind] = useState<KindFilter>("all");
  const [vram, setVram] = useState<VramFilter>("any");
  const [page, setPage] = useState(1);

  const chips = kindChips(rows);
  const filtered = filterRows(rows, { kind, vram });
  // `paginate` clamps, so `shown.page` is what is on screen even when the
  // state above it is stale — the Prev/Next below step from the clamped
  // number rather than from the one that was asked for.
  const shown = paginate(filtered, page);

  return (
    <div className="min-w-0">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div role="group" aria-label="Filter by kind" className="flex gap-1">
          {chips.map((chip) => (
            <button
              key={chip.value}
              type="button"
              aria-pressed={kind === chip.value}
              onClick={() => {
                setKind(chip.value);
                setPage(1);
              }}
              className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
                kind === chip.value
                  ? "bg-surface-2 text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {chip.label}
              <span className="ml-1.5 font-mono tabular-nums opacity-60">
                {chip.count}
              </span>
            </button>
          ))}
        </div>

        <Select
          value={vram}
          onValueChange={(value) => {
            setVram((value as VramFilter | null) ?? "any");
            setPage(1);
          }}
        >
          <SelectTrigger size="sm" aria-label="Filter by VRAM">
            <SelectValue>
              {(value) =>
                VRAM_FILTERS.find((option) => option.value === value)?.label ??
                "Any VRAM"
              }
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            {VRAM_FILTERS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="overflow-x-auto rounded-lg border border-border bg-surface">
        <table className="w-full min-w-[620px] border-collapse text-xs">
          <thead>
            <tr className="border-b border-border">
              <th scope="col" className="label-caps px-3 py-2 text-left">
                Class
              </th>
              <th scope="col" className="label-caps px-2 py-2 text-right">
                Last ask
              </th>
              <th scope="col" className="label-caps px-2 py-2 text-right">
                24h
              </th>
              <th scope="col" className="label-caps px-2 py-2 text-left">
                7d
              </th>
              <th scope="col" className="label-caps px-2 py-2 text-right">
                Open asks
              </th>
              <th scope="col" className="label-caps px-3 py-2 text-right">
                Source
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {shown.rows.map((row) => (
              <tr
                key={row.klass}
                className="transition-colors hover:bg-surface-2"
              >
                <td className="px-3 py-2.5">
                  <Link href={row.href} className="block min-w-0">
                    <span className="font-mono hover:underline">
                      {row.klass}
                    </span>
                    {row.displayName && (
                      <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
                        {row.displayName}
                      </span>
                    )}
                  </Link>
                </td>
                <td className="px-2 py-2.5 text-right font-mono tabular-nums">
                  <div>{row.lastAskText}</div>
                  {row.equivalentText && (
                    <div className="mt-0.5 text-[10px] text-muted-foreground">
                      {row.equivalentText}
                    </div>
                  )}
                </td>
                <td
                  className={`px-2 py-2.5 text-right font-mono tabular-nums ${DELTA_TONE[row.change.direction]}`}
                >
                  {row.change.text}
                </td>
                <td className="px-2 py-2.5">
                  <Sparkline points={row.spark} />
                </td>
                <td className="px-2 py-2.5 text-right font-mono tabular-nums">
                  {row.depth === null ? (
                    <span className="text-muted-foreground">—</span>
                  ) : (
                    row.depth
                  )}
                </td>
                <td className="px-3 py-2.5 text-right">
                  <SourceChip source={row.source} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {/* A filter that matches nothing is a dead end without a way out of
            it — the same defect the workspace job list was fixed for. */}
        {shown.rows.length === 0 && (
          <div className="px-3 py-6 text-center">
            <p className="text-xs text-muted-foreground">
              No classes match these filters.
            </p>
            <button
              type="button"
              onClick={() => {
                setKind("all");
                setVram("any");
                setPage(1);
              }}
              className="mt-2 rounded-md border border-border bg-surface px-2.5 py-1 text-xs transition-colors hover:bg-surface-2"
            >
              Show every class
            </button>
          </div>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <span className="font-mono text-[11px] text-muted-foreground tabular-nums">
          {shown.rangeText}
        </span>
        <div className="flex gap-1">
          <button
            type="button"
            onClick={() => setPage(shown.page - 1)}
            disabled={shown.page <= 1}
            className="rounded-md border border-border bg-surface px-2.5 py-1 text-xs transition-colors hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Prev
          </button>
          <button
            type="button"
            onClick={() => setPage(shown.page + 1)}
            disabled={shown.page >= shown.pages}
            className="rounded-md border border-border bg-surface px-2.5 py-1 text-xs transition-colors hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Next
          </button>
        </div>
      </div>

      <p className="mt-2 text-[11px] text-muted-foreground">
        Reference rows are generated {REFERENCE_META.generatedAt} from live
        vendor bands.
      </p>
    </div>
  );
}

/** Market-convention colours for a movement, purely directional: the
 * direction is the lib's decision, this only maps it to a token. */
const DELTA_TONE: Record<"up" | "down" | "none", string> = {
  up: "text-evergreen",
  down: "text-destructive",
  none: "text-muted-foreground",
};
