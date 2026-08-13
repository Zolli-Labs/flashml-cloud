"use client";

import Link from "next/link";

import { historyStamp, type HostClassGroup } from "@/lib/market/board";
import { PriceHistory } from "./PriceHistory";
import { SourceChip } from "./SourceChip";

/** The front of the board: the classes THIS host has machines in, with the
 * chart already drawn.
 *
 * WHY IT LEADS THE PAGE. A host arrives with one question — is my machine
 * cheaper or dearer than it was — and the answer used to be four clicks
 * away: read the ticker strip, recognise which of thirty-odd tickers is
 * yours, click it, then read the chart on the class page. Everything in
 * this section is the same data the class page draws; it is here because
 * the question is asked here.
 *
 * THE VERDICT LINE IS THE POINT, and it is why the REFERENCE badge sits on
 * the line itself rather than only on the chart's caption. A generated
 * vendor-band series moves like a real one, and "cheaper than yesterday
 * ▼ 4.1%" read off it is a sentence about our market that our market never
 * said. The badge travels with the sentence — and for a class priced off a
 * donor card it names the donor, so a host whose rig sits in `cpu-large`
 * gets a verdict, a chart and the card they came from instead of the words
 * "no history yet".
 *
 * NAMED FOR WHAT IT LISTS. The section heading says machines because that
 * is what a host recognises, but a card is a CLASS — and
 * `components/workspace/YourMachines.tsx` already exists and lists actual
 * machines. Two components called `YourMachines` in one console is an
 * import nobody reads carefully twice.
 *
 * Markup only: every group, series, price string and verdict comes from
 * `hostClassGroups` in `lib/market/board.ts`. */
export function YourClasses({
  groups,
  state,
}: {
  groups: HostClassGroup[];
  /** The machines read (`listMachines` + one market-hint per machine).
   * Its own state, because it is its own route: the board can be readable
   * while this is not, and the reverse. */
  state: "loading" | "present" | "unreadable";
}) {
  return (
    <section>
      <h2 className="label-caps">Your machines on the market</h2>

      {state === "loading" && (
        <div className="mt-3 grid gap-4 xl:grid-cols-2" aria-busy="true">
          <div className="skeleton h-56 w-full" />
          <div className="skeleton h-56 w-full" />
        </div>
      )}

      {/* Not the empty line: "we could not ask" and "you have none" are
          different facts, and the second one tells a host with four
          machines to go and add one. */}
      {state === "unreadable" && (
        <p className="mt-2 text-xs text-muted-foreground">
          Couldn&apos;t read your machines — that is not the same as having
          none.
        </p>
      )}

      {state === "present" && groups.length === 0 && (
        <p className="mt-2 text-xs text-muted-foreground">
          <Link
            href="/machines/add"
            className="text-brand-foreground hover:underline"
          >
            Add a machine
          </Link>{" "}
          to see your classes here.
        </p>
      )}

      {state === "present" && groups.length > 0 && (
        <div className="mt-3 grid gap-4 xl:grid-cols-2">
          {groups.map((group) => (
            <ClassCard key={group.klass} group={group} />
          ))}
        </div>
      )}
    </section>
  );
}

/** One class the host is in: what it is, whose machines are in it, what it
 * costs, which way it moved, and the series behind that. */
function ClassCard({ group }: { group: HostClassGroup }) {
  const { row, verdict } = group;
  // The stamp for the SERIES, which is not always the stamp for the price:
  // one live observation prices the class live and still leaves the chart
  // borrowing a line. `historyStamp` is null when the series is ours.
  const seriesStamp = historyStamp(group.history);
  return (
    <div className="panel px-4 py-3.5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="min-w-0">
          <Link
            href={group.href}
            className="font-mono text-sm hover:underline"
          >
            {group.klass}
          </Link>
          {group.displayName && (
            <span className="ml-2 text-[11px] text-muted-foreground">
              {group.displayName}
            </span>
          )}
        </div>
        {row && <SourceChip source={row.source} />}
      </div>

      {/* Their machines, mono — the same identity the machines pages print,
          so a host can match a card to a box on their desk. */}
      <ul className="mt-1.5 flex flex-wrap gap-x-2 gap-y-1">
        {group.machines.map((machine) => (
          <li
            key={machine.id}
            className="truncate font-mono text-[11px] text-muted-foreground"
          >
            {machine.label}
          </li>
        ))}
      </ul>

      <div className="mt-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="metric-value text-xl">{row?.lastAskText ?? "—"}</span>
        <span className="flex items-baseline gap-2">
          <span
            className={`font-mono text-[11px] tabular-nums ${DELTA_TONE[verdict.direction]}`}
          >
            {verdict.text}
          </span>
          {verdict.reference && seriesStamp && (
            <SourceChip source={seriesStamp} />
          )}
        </span>
      </div>
      {row?.equivalentText && (
        <p className="mt-0.5 font-mono text-[10px] text-muted-foreground tabular-nums">
          {row.equivalentText}
        </p>
      )}

      <PriceHistory data={group.history} compact />
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
