"use client";

import { useEffect, useState } from "react";
import { useReducedMotion } from "motion/react";
import type { LedgerEvent, LedgerTone } from "@/lib/landing/sample-ledger";

// The ledger is the landing page's one real product visual. It is a live
// component rendering the real `Event` shape, not a picture of a UI and not
// a stack of divs pretending to be a screenshot.
//
// The values it renders are sample data (see lib/landing/sample-ledger.ts).
// The `label` prop below is what says so on screen, and it is not optional
// by accident: a fabricated ledger presented as a measurement would
// undercut the exact claim this page makes.

const TONE_TEXT: Record<LedgerTone, string> = {
  note: "text-muted-foreground",
  alert: "text-[var(--warning)]",
  good: "text-[var(--node-green)]",
};

const TONE_MARK: Record<LedgerTone, string> = {
  note: "bg-white/20",
  alert: "bg-[var(--warning)]",
  good: "bg-[var(--node-green)]",
};

function formatOffset(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function LedgerRow({ event }: { event: LedgerEvent }) {
  return (
    <li className="flex items-baseline gap-3 py-[5px] text-[11px] leading-snug">
      <span
        aria-hidden
        className={`mt-[6px] h-1 w-1 shrink-0 self-start rounded-full ${TONE_MARK[event.tone]}`}
      />
      <span className="w-9 shrink-0 font-mono tabular-nums text-white/35">
        {formatOffset(event.at)}
      </span>
      <span className={`shrink-0 font-mono ${TONE_TEXT[event.tone]}`}>
        {event.type}
      </span>
      <span className="truncate font-mono text-white/35">{event.detail}</span>
    </li>
  );
}

export function EventLedger({
  events,
  label,
  stream = false,
  className = "",
}: {
  events: LedgerEvent[];
  /** Renders above the rows. Says the data is a sample. Required. */
  label: string;
  stream?: boolean;
  className?: string;
}) {
  const reduce = useReducedMotion();
  const [shown, setShown] = useState(stream ? 0 : events.length);

  useEffect(() => {
    if (!stream) {
      setShown(events.length);
      return;
    }
    // Reduced motion gets the finished ledger immediately rather than a
    // slower version of the same animation.
    if (reduce) {
      setShown(events.length);
      return;
    }
    setShown(0);
    const id = setInterval(() => {
      setShown((n) => {
        if (n >= events.length) return n;
        return n + 1;
      });
    }, 420);
    return () => clearInterval(id);
  }, [stream, reduce, events.length]);

  const visible = events.slice(0, shown);

  return (
    <div className={className}>
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-white/40">
          event ledger
        </span>
        <span className="font-mono text-[10px] text-white/30">{label}</span>
      </div>
      <ul
        aria-label="FlashML coordinator event ledger, sample data"
        className="min-h-[276px] overflow-hidden px-4 py-3"
      >
        {visible.map((e, i) => (
          <LedgerRow key={`${e.type}-${e.at}-${i}`} event={e} />
        ))}
      </ul>
    </div>
  );
}
