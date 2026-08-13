import { SectionReveal } from "@/components/landing/motion/SectionReveal";

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

export function EvidenceBand() {
  return (
    <section
      id="evidence"
      data-surface="sand"
      data-layout="evidence-ledger"
      className="landing-surface-sand scroll-mt-20 border-b border-border py-20 md:py-28"
    >
      <div className="mx-auto max-w-[1240px] px-5 sm:px-6">
        <div className="grid gap-8 lg:grid-cols-[.72fr_1.28fr] lg:gap-12">
          <div>
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
              Product evidence
            </p>
            <h2 className="mt-5 text-[clamp(2.65rem,5.4vw,4.75rem)] font-semibold leading-[0.99] tracking-[-0.052em]">
              Proven with <span className="text-muted-foreground">real work.</span>
            </h2>
          </div>
          <p className="max-w-[55ch] self-end text-base leading-relaxed text-muted-foreground">
            Measured in documented runs and benchmarks — not scale claims.
          </p>
        </div>

        <SectionReveal
          className="mt-14 md:mt-20"
          lineClassName="h-px w-full bg-[var(--z-border-strong)]"
          bottomLineClassName="h-px w-full bg-[var(--z-border-strong)]"
        >
          <div className="grid gap-x-8 gap-y-12 py-8 sm:grid-cols-2 sm:py-10 lg:grid-cols-4 lg:gap-10">
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
        </SectionReveal>
      </div>
    </section>
  );
}
