import { EventLedger } from "@/components/landing/EventLedger";
import { RecoveryStack } from "@/components/landing/RecoveryStack";
import { SAMPLE_LEDGER, type LedgerEventType } from "@/lib/landing/sample-ledger";

const RECOVERY_EVENT_TYPES = new Set<LedgerEventType>([
  "LEASE_CLAIMED",
  "CHECKPOINT_MANIFEST_COMMITTED",
  "NODE_HEARTBEAT_LOST",
  "TASK_REQUEUED",
  "TASK_COMMIT_ACCEPTED",
]);

const RECOVERY_EVENTS = SAMPLE_LEDGER.filter((event) => RECOVERY_EVENT_TYPES.has(event.type)).filter(
  (event, index, events) =>
    event.type === "TASK_COMMIT_ACCEPTED"
      ? events.map((candidate) => candidate.type).lastIndexOf(event.type) === index
      : events.findIndex((candidate) => candidate.type === event.type) === index,
);

const RECOVERY_PROOF = [
  "Failure at step 35",
  "Checkpoint at step 30",
  "5 steps of work lost",
] as const;

export function RecoveryDemo() {
  return (
    <section
      id="recover"
      data-surface="light"
      className="landing-surface-light scroll-mt-20 border-b border-border py-20 md:py-28"
    >
      <div className="mx-auto grid max-w-[1240px] gap-12 px-5 sm:px-6 lg:grid-cols-[.85fr_1.15fr] lg:items-center lg:gap-20">
        <div>
          <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
            Recovery proof
          </p>
          <h2 className="mt-5 max-w-2xl text-[clamp(2.65rem,5.4vw,4.75rem)] font-semibold leading-[0.99] tracking-[-0.052em]">
            Lost machine. <span className="text-muted-foreground">Verified recovery.</span>
          </h2>
          <p className="mt-6 max-w-[54ch] leading-relaxed text-muted-foreground">
            A lost heartbeat does not erase accepted progress. The lease expires,
            the task requeues, and another worker continues from a verified checkpoint.
          </p>
          <RecoveryStack items={RECOVERY_PROOF} />
          <div className="mt-8 grid grid-cols-[auto_1fr] gap-x-4">
            <span className="font-mono text-[10px] text-warning-foreground">01:28</span>
            <p className="text-sm leading-relaxed text-foreground">
              The ledger records the exact protocol events behind the handoff,
              from heartbeat loss through the accepted replacement commit.
            </p>
          </div>
        </div>

        <div>
          <div
            data-surface="dark"
            className="landing-surface-dark overflow-hidden rounded-[10px] border border-white/14 bg-[var(--landing-graphite)]"
          >
            <EventLedger events={RECOVERY_EVENTS} label="sample data" />
          </div>
          <p className="mt-4 max-w-[58ch] text-xs leading-relaxed text-muted-foreground">
            Every protocol name above is a real member of the runtime&apos;s event ledger. The displayed values are sample data until a captured run replaces them.
          </p>
        </div>
      </div>
    </section>
  );
}
