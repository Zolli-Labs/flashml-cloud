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
  alert: "text-[var(--z-warning)]",
  good: "text-[var(--z-healthy)]",
};

const TONE_MARK: Record<LedgerTone, string> = {
  note: "bg-surface-2",
  alert: "bg-[var(--z-warning)]",
  good: "bg-[var(--z-healthy)]",
};

const FRIENDLY_EVENT: Record<LedgerEventType, string> = {
  JOB_ACCEPTED: "The coordinator accepted the job.",
  TASK_CREATED: "The planner expanded the job into tasks.",
  LEASE_CLAIMED: "A worker claimed a time-limited lease.",
  LEASE_RENEWED: "The worker renewed its lease.",
  LEASE_EXPIRED: "An unrenewed lease reached its deadline.",
  TASK_REQUEUED: "The interrupted task returned to the queue.",
  TASK_COMMIT_ACCEPTED: "The coordinator accepted a validated result.",
  NODE_HEARTBEAT_LOST: "A machine stopped reporting heartbeats.",
  CHECKPOINT_MANIFEST_COMMITTED: "The catalog committed a verified checkpoint manifest.",
  FAILURE_CLASSIFIED: "The runtime classified the interruption.",
  RECOVERY_ACTION_SELECTED: "The recovery policy selected the next action.",
  ITERATION_COMPLETED: "The workload completed another training round.",
  ARTIFACT_COMMITTED: "The artifact service committed a completed output.",
  JOB_SUCCEEDED: "Every task reached an accepted result.",
};

function formatOffset(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function LedgerRow({ event }: { event: LedgerEvent }) {
  return (
    <li className="grid grid-cols-[0.3rem_minmax(0,1fr)] gap-x-3 border-b border-border py-3 last:border-b-0">
      <span
        aria-hidden
        className={`mt-[0.55rem] h-1.5 w-1.5 self-start rounded-full ${TONE_MARK[event.tone]}`}
      />
      <div className="min-w-0">
        <p className={`text-[11px] font-medium leading-snug ${TONE_TEXT[event.tone]}`}>
          {FRIENDLY_EVENT[event.type]}
        </p>
        <p className="mt-1 truncate font-mono text-[10px] tabular-nums text-muted-foreground">
          {event.type} · +{formatOffset(event.at)}
        </p>
        <p className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground">
          {event.source} · {event.detail}
        </p>
      </div>
    </li>
  );
}

export function EventLedger({
  events,
  label,
  className = "",
}: {
  events: LedgerEvent[];
  /** Renders above the rows. Says the data is a sample. Required. */
  label: string;
  className?: string;
}) {
  return (
    <div className={className}>
      <div className="flex items-center justify-between border-b border-border px-4 py-3.5">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
          event ledger
        </span>
        <span className="font-mono text-[10px] text-muted-foreground">{label}</span>
      </div>
      <ul
        aria-label="Zolli Cloud recovery ledger, sample data from FlashML protocol events"
        className="min-h-[276px] overflow-hidden px-4 py-1 [&>li]:min-w-0"
      >
        {events.map((e, i) => (
          <LedgerRow key={`${e.type}-${e.at}-${i}`} event={e} />
        ))}
      </ul>
    </div>
  );
}
