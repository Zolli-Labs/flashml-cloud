"use client";

import { useMemo, useState } from "react";

import { Disclosure } from "@/components/jobs/Disclosure";
import {
  LEDGER_FILTERS,
  collapseLedgerRuns,
  countByLedgerFilter,
  filterLedgerEvents,
  isFailureEvent,
  type LedgerFilter,
  type LedgerRunItem,
} from "@/components/jobs/ledger-view";
import type { JobEvent } from "@/lib/cloud-api";

/**
 * What actually happened, in the coordinator's own words.
 *
 * Lived inside `app/(console)/jobs/[jobId]/page.tsx` until the filter chips
 * and the run folding gave it state of its own. Out here for the reason the
 * rest of `components/jobs/` gives: a `page.tsx` may export only a default
 * component plus route config, and every decision worth testing belongs in a
 * `.ts` a test can reach — `components/jobs/ledger-view.ts` holds all of
 * them. This file is markup, a `useState`, and a clock format.
 *
 * TWO THINGS CHANGED AND NEITHER OF THEM IS THE DATA. A healthy run is
 * mostly `LEASE_RENEWED`, so consecutive identical rows fold into one that
 * says how many it stands for and over what span; and four chips filter by
 * event type. Both are reversible in one click, the folded rows are the same
 * rows, and no message is ever paraphrased — a recovery decision's reason IS
 * the explanation, and rewriting it would be inventing one.
 */
export function LedgerView({ events }: { events: JobEvent[] }) {
  const [filter, setFilter] = useState<LedgerFilter>("all");

  const counts = useMemo(() => countByLedgerFilter(events), [events]);
  // Newest first: when something has just gone wrong, it is at the top. The
  // reversal happens BEFORE the folding so a run's rows read in the same
  // direction as the rows around them; the run's own span is still stated
  // earliest-to-latest, which is how a person reads a time range.
  const items = useMemo(
    () => collapseLedgerRuns([...filterLedgerEvents(events, filter)].reverse()),
    [events, filter]
  );

  if (events.length === 0) {
    return (
      <section className="rounded-lg border border-border bg-surface p-6">
        <h2 className="text-sm font-semibold">No events recorded</h2>
        <p className="mt-2 max-w-prose text-sm leading-relaxed text-muted-foreground">
          The coordinator has written nothing for this job yet.
        </p>
      </section>
    );
  }

  const shown = counts[filter];

  return (
    <section className="overflow-hidden rounded-lg border border-border bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-border px-4 py-2.5">
        <h2 className="text-sm font-semibold">Event ledger</h2>
        <span className="font-mono text-xs text-muted-foreground tabular-nums">
          {/* What is on screen, against what exists. A filtered ledger that
              said "148 events" over twelve rows would be describing a list
              the reader cannot see. */}
          {filter === "all"
            ? `${events.length} events`
            : `${shown} of ${events.length} events`}
        </span>
      </div>

      {/* Predicates on the event type, nothing else — see
          `components/jobs/ledger-view.ts`. The counts are on the chips so an
          empty filter answers its own question without being clicked. */}
      <div className="flex flex-wrap gap-1.5 border-b border-border px-4 py-2">
        {LEDGER_FILTERS.map(({ id, label }) => (
          <button
            key={id}
            type="button"
            onClick={() => setFilter(id)}
            aria-pressed={filter === id}
            className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[11px] transition-colors ${
              filter === id
                ? "border-brand/40 bg-surface-2 text-foreground"
                : "border-border text-muted-foreground hover:text-foreground"
            }`}
          >
            {label}
            <span className="tabular-nums opacity-70">{counts[id]}</span>
          </button>
        ))}
      </div>

      {shown === 0 ? (
        <p className="max-w-prose px-4 py-6 text-sm leading-relaxed text-muted-foreground">
          Nothing here matches {labelFor(filter)}. All {events.length} events
          on this job&apos;s ledger are of other types.
        </p>
      ) : (
        <ul className="divide-y divide-border">
          {items.map((item, i) =>
            item.kind === "event" ? (
              <li key={`event-${i}`} className="px-4 py-2.5">
                <EventRow event={item.event} />
              </li>
            ) : (
              <li key={item.id} className="px-4 py-2.5">
                <RunRow run={item} />
              </li>
            )
          )}
        </ul>
      )}
    </section>
  );
}

/** The chip's own label, for the sentence that reports it matched nothing.
 * Read off `LEDGER_FILTERS` rather than title-cased from the id, so the
 * empty state names the chip the reader actually clicked. */
function labelFor(filter: LedgerFilter): string {
  return LEDGER_FILTERS.find((f) => f.id === filter)?.label ?? filter;
}

/** One event, exactly as this page has always rendered one. */
function EventRow({ event }: { event: JobEvent }) {
  return (
    <>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-mono text-xs tabular-nums text-muted-foreground">
          {clock(event.timestamp)}
        </span>
        {event.round !== undefined && (
          <span className="font-mono text-[10px] text-muted-foreground">
            r{event.round}
          </span>
        )}
        <span className={`font-mono text-xs ${ledgerTone(event.type)}`}>
          {event.type}
        </span>
        {typeof event.data?.node_id === "string" && (
          <span className="font-mono text-[11px] text-muted-foreground">
            {event.data.node_id}
          </span>
        )}
      </div>
      {/* The coordinator's own words, verbatim. Recovery decisions in
          particular must never be paraphrased: the policy's reason IS the
          explanation, and rewriting it would be inventing one. */}
      {event.message && (
        <p className="mt-1 font-mono text-[11px] leading-relaxed text-muted-foreground">
          {event.message}
        </p>
      )}
    </>
  );
}

/** A run of identical events, standing in for itself: the type, how many,
 * which machine, and the span they cover. Nothing summarised — every message
 * is inside, and the count and the span are read straight off the events.
 *
 * Deliberately says NOTHING about what happened during the run. "Held for 6
 * minutes" would be a claim about a lease that the ledger did not make; the
 * events did, and they are one click below. */
function RunRow({ run }: { run: LedgerRunItem }) {
  return (
    <Disclosure
      label={
        <span className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 font-mono text-xs">
          <span className={ledgerTone(run.type)}>{run.type}</span>
          <span className="tabular-nums text-foreground">×{run.count}</span>
          {run.round !== undefined && (
            <span className="text-[10px] text-muted-foreground">
              · r{run.round}
            </span>
          )}
          {run.nodeId && (
            <span className="text-[11px] text-muted-foreground">
              · {run.nodeId}
            </span>
          )}
          <span className="text-[11px] tabular-nums text-muted-foreground">
            · {clock(run.startedAt)}–{clock(run.endedAt)}
          </span>
        </span>
      }
    >
      <ul className="divide-y divide-border border-t border-border pl-4">
        {run.events.map((event, i) => (
          <li key={`${event.timestamp}-${i}`} className="py-2">
            <EventRow event={event} />
          </li>
        ))}
      </ul>
    </Disclosure>
  );
}

/** Amber for anything that went wrong, green for work that landed, muted for
 * the rest. The failure set is `FAILURE_MARKERS` in
 * `components/jobs/ledger-view.ts` — shared with the Failures chip, so the
 * colour of a row and the filter that finds it can never disagree. */
function ledgerTone(type: string): string {
  if (isFailureEvent(type)) return "text-warning-foreground";
  if (
    type.includes("ACCEPTED") ||
    type.includes("SUCCEEDED") ||
    type.includes("COMMITTED")
  ) {
    return "text-[var(--node-green)]";
  }
  return "text-muted-foreground";
}

/** An ISO stamp as a wall clock. Returns the raw string for a value that is
 * unparseable rather than "Invalid Date" — the API said it, and quoting it
 * back is more use than a placeholder. */
function clock(iso: string): string {
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? iso : new Date(ms).toLocaleTimeString();
}
