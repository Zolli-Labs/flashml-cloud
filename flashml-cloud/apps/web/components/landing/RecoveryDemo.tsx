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
  "RTX 4090 machine destroyed",
  "Resumed on an RTX 3090",
  "58 epochs preserved",
] as const;

// COUNTS OF THINGS THAT HAPPENED, not performance comparisons — and the
// distinction is why these four and not the previous four.
//
// The set this replaced led with "47% faster batch completion". That number
// is the `lpt` run in `flashruntime/benchmarks/results/` — 4 repeats, the
// second-best of seven runs of the same comparison. The full set:
// -189.9% / +21.5% / +29.1% / +48.3% / +37.5% / +47.5% / +42.6%. Both
// ten-repeat runs (the only ones with enough samples to mean anything) say
// +42.6% and +37.5%, and one run has the pooled arm nearly THREE TIMES
// SLOWER. That benchmark is on record as not reproducing, with a standing
// decision to publish nothing from it.
//
// "3.7x worker speed range" came from the same cherry-picked run; the others
// range 3.164 to 4.121. "24 attacks blocked" and "<0.25% host memory
// overhead" have no source anywhere in either repository.
//
// A judge who asks "over how many repeats?" ends that conversation. These
// four are countable and were counted.
const EVIDENCE = [
  ["30", "production attempts", "Recorded across the first two contributing hosts."],
  ["2", "proven architectures", "macOS arm64 and Linux x86_64."],
  ["5", "steps lost, not 35", "Recovered from the last verified checkpoint."],
  ["1", "accepted result per task", "Idempotent commits reject duplicate outcomes."],
] as const;

export function RecoveryDemo() {
  return (
    <section
      id="recover"
      data-surface="light"
      className="landing-surface-light scroll-mt-20 border-b border-border py-20 md:py-28"
    >
      <div className="mx-auto max-w-[1240px] px-5 sm:px-6">
        <div className="grid gap-12 lg:grid-cols-[.85fr_1.15fr] lg:items-center lg:gap-20">
          <div>
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
              Recovery proof
            </p>
            <h2 className="mt-5 max-w-2xl text-[clamp(2.65rem,5.4vw,4.75rem)] font-semibold leading-[0.99] tracking-[-0.052em]">
              Machines disappear. <span className="text-muted-foreground">Progress doesn&apos;t.</span>
            </h2>
            <p className="mt-6 max-w-[54ch] leading-relaxed text-muted-foreground">
              When capacity vanishes, Zolli resumes supported work from recorded
              progress on another compatible machine.
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

        <div className="mt-14 border-t border-[var(--z-border-strong)] pt-10 md:mt-20 md:pt-14">
          <div className="grid gap-x-8 gap-y-10 sm:grid-cols-2 lg:grid-cols-4 lg:gap-10">
            {EVIDENCE.map(([value, label, detail]) => (
              <article key={label} className="min-w-0">
                <p
                  data-evidence-value={value}
                  className="font-mono text-6xl font-medium leading-none tracking-[-0.08em] tabular-nums sm:text-7xl"
                >
                  {value}
                </p>
                <h3 className="mt-5 font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-brand-foreground">
                  {label}
                </h3>
                <p className="mt-3 max-w-[27ch] text-sm leading-relaxed text-muted-foreground">{detail}</p>
              </article>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
