import { ZolliCharacter } from "@/components/brand/ZolliCharacter";
import { EventLedger } from "@/components/landing/EventLedger";
import { Reveal } from "@/components/motion/Reveal";
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

export function RecoveryDemo() {
  return (
    <section
      id="recover"
      className="scroll-mt-24 border-y border-border bg-surface-2 py-20 md:py-28"
    >
      <div className="mx-auto grid max-w-7xl gap-12 px-4 sm:px-6 lg:grid-cols-[0.85fr_1.15fr] lg:items-center lg:gap-20">
        <Reveal>
          <p className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-brand-foreground">
            Recovery proof
          </p>
          <h2 className="mt-4 max-w-2xl font-display text-4xl font-semibold leading-[1.02] tracking-[-0.035em] md:text-6xl">
            A Zolli steps out. The Crew keeps moving.
          </h2>
          <p className="mt-5 max-w-[54ch] leading-relaxed text-muted-foreground">
            A lost heartbeat does not erase accepted progress. The lease expires, the task requeues, and another Zolli can continue from a verified checkpoint.
          </p>
          <div className="mt-8 flex items-center gap-4 rounded-2xl border border-border bg-surface p-4">
            <ZolliCharacter role="relay" size={88} mood="focused" />
            <p className="text-sm leading-relaxed text-foreground">
              Relay explains the handoff; the ledger shows the exact FlashML protocol events behind it.
            </p>
          </div>
        </Reveal>

        <Reveal delay={0.08}>
          <div className="overflow-hidden rounded-3xl border border-border bg-surface shadow-md">
            <EventLedger events={RECOVERY_EVENTS} label="sample run" />
          </div>
          <p className="mt-4 max-w-[58ch] text-xs leading-relaxed text-muted-foreground">
            Every protocol name above is a real member of the runtime&apos;s event ledger. The values are sample data until a captured run replaces them.
          </p>
        </Reveal>
      </div>
    </section>
  );
}
