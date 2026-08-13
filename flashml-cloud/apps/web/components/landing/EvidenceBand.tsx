import { SectionReveal } from "@/components/landing/motion/SectionReveal";

const EVIDENCE = [
  ["6", "trials completed", "One model search completed all six independent trials."],
  ["3", "machines shared the work", "A laptop and two rented GPUs completed the same search."],
  ["58", "epochs preserved", "Completed training progress survived when a rented GPU was destroyed."],
  ["1", "accepted result per task", "Duplicate outcomes are rejected instead of counted twice."],
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
            Figures from documented runs, not scale claims.
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
