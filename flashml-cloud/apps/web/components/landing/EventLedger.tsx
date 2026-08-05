"use client";

import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import { QUICK } from "@/lib/motion";
import type { LedgerEvent, LedgerEventType, LedgerTone } from "@/lib/landing/sample-ledger";

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
  alert: "text-warning-foreground",
  good: "text-[var(--node-green)]",
};

const TONE_MARK: Record<LedgerTone, string> = {
  note: "bg-surface-2",
  alert: "bg-[var(--warning)]",
  good: "bg-[var(--node-green)]",
};

const FRIENDLY_EVENT: Record<LedgerEventType, string> = {
  JOB_ACCEPTED: "The Crew accepted the job.",
  TASK_CREATED: "Captain divided the job into tasks.",
  LEASE_CLAIMED: "A Zolli claimed a time-limited lease.",
  LEASE_RENEWED: "The Zolli renewed its lease.",
  LEASE_EXPIRED: "An unrenewed lease reached its deadline.",
  TASK_REQUEUED: "Captain made the interrupted task available again.",
  TASK_COMMIT_ACCEPTED: "The Crew accepted a validated result.",
  NODE_HEARTBEAT_LOST: "A Zolli stopped checking in.",
  CHECKPOINT_MANIFEST_COMMITTED: "Keeper saved a verified checkpoint manifest.",
  FAILURE_CLASSIFIED: "The runtime classified the interruption.",
  RECOVERY_ACTION_SELECTED: "The recovery policy selected the next action.",
  ITERATION_COMPLETED: "The Crew completed another training round.",
  ARTIFACT_COMMITTED: "Builder committed a completed output.",
  JOB_SUCCEEDED: "Every task reached an accepted result.",
};

function formatOffset(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

// Justification for the entry animation: state transition. A row appearing
// instantly reads as a re-render; sliding in from the left reads as an event
// arriving, which is what it is.
export function LedgerRow({ event }: { event: LedgerEvent }) {
  const reduce = useReducedMotion();
  return (
    <motion.li
      initial={reduce ? false : { opacity: 0, x: -8 }}
      animate={{ opacity: 1, x: 0 }}
      transition={QUICK}
      className="grid grid-cols-[0.3rem_minmax(0,1fr)] gap-x-3 border-b border-border/70 py-3 last:border-b-0"
    >
      <span
        aria-hidden
        className={`mt-[0.55rem] h-1.5 w-1.5 self-start rounded-full ${TONE_MARK[event.tone]}`}
      />
      <div className="min-w-0">
        <p className={`text-sm font-medium leading-snug ${TONE_TEXT[event.tone]}`}>
          {FRIENDLY_EVENT[event.type]}
        </p>
        <p className="mt-1 truncate font-mono text-[10px] tabular-nums text-muted-foreground">
          {event.type} · +{formatOffset(event.at)}
        </p>
        <p className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground">
          {event.source} · {event.detail}
        </p>
      </div>
    </motion.li>
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

  // Reduced motion gets the finished ledger immediately, not a slower
  // version of the same animation.
  const wantsStream = stream && !reduce;

  const [streamed, setStreamed] = useState(0);

  // setState happens only inside the interval callback, never in the effect
  // body. Setting it synchronously here is what react-hooks/set-state-in-effect
  // flags, and the non-streaming case does not need an effect at all: it is
  // derived below.
  useEffect(() => {
    if (!wantsStream) return;
    const id = setInterval(() => {
      setStreamed((n) => (n >= events.length ? n : n + 1));
    }, 420);
    return () => clearInterval(id);
  }, [wantsStream, events.length]);

  const shown = wantsStream ? streamed : events.length;
  const visible = events.slice(0, shown);

  return (
    <div className={className}>
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
          recovery ledger
        </span>
        <span className="font-mono text-[10px] text-muted-foreground">{label}</span>
      </div>
      <ul
        aria-label="ZolliAI Cloud recovery ledger, sample data from FlashML protocol events"
        className="min-h-[276px] overflow-hidden px-4 py-2 [&>li]:min-w-0"
      >
        {visible.map((e, i) => (
          <LedgerRow key={`${e.type}-${e.at}-${i}`} event={e} />
        ))}
      </ul>
    </div>
  );
}
