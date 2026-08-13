import { SectionReveal } from "@/components/landing/motion/SectionReveal";

export function MarketStory() {
  return (
    <section
      id="network"
      data-surface="light"
      className="landing-surface-light scroll-mt-20 border-b border-border py-20 md:py-28"
    >
      <div className="mx-auto max-w-[1240px] px-5 sm:px-6">
        <SectionReveal className="pb-10 md:pb-14">
          <div className="grid gap-8 md:grid-cols-[.72fr_1.28fr] md:gap-12">
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
              The compute market
            </p>
            <div>
              <h2 className="max-w-[820px] text-[clamp(2.65rem,5.4vw,4.75rem)] font-semibold leading-[0.99] tracking-[-0.052em]">
                Compute is everywhere. <span className="text-muted-foreground">Access is not.</span>
              </h2>
              <p className="mt-6 max-w-[62ch] text-base leading-relaxed text-muted-foreground">
                Useful machines sit idle while teams wait for capacity.
              </p>
            </div>
          </div>
        </SectionReveal>

        <SectionReveal
          className="pb-10 md:pb-14"
          lineClassName="h-px w-full bg-[var(--z-border-strong)]"
        >
          <div className="grid gap-8 pt-10 md:grid-cols-[.72fr_1.28fr] md:gap-12 md:pt-14">
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
              A new allocation path
            </p>
            <div>
              <h3 className="max-w-[720px] text-[clamp(1.9rem,3.5vw,3.1rem)] font-semibold leading-[1.02] tracking-[-0.04em]">
                From isolated machines to <span className="text-muted-foreground">an open compute network.</span>
              </h3>
              <p className="mt-5 max-w-[62ch] text-base leading-relaxed text-muted-foreground">
                One allocation path across owned, community, and rented capacity.
              </p>
            </div>
          </div>
        </SectionReveal>

        <SectionReveal lineClassName="h-px w-full bg-[var(--z-border-strong)]">
          <div className="grid gap-8 pt-10 md:grid-cols-2 md:gap-12 md:pt-14">
            <article>
              <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">For demand</p>
              <p className="mt-5 max-w-[42ch] text-xl leading-relaxed tracking-[-0.025em]">
                Access more machines, compare more choices, and avoid depending on one provider&apos;s price or availability.
              </p>
            </article>
            <article className="border-t border-border pt-8 md:border-l md:border-t-0 md:pl-12 md:pt-0">
              <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">For supply</p>
              <p className="mt-5 max-w-[42ch] text-xl leading-relaxed tracking-[-0.025em]">
                Turn unused machines into productive capacity and earn when they complete useful work.
              </p>
              <div className="mt-8 border-t border-border pt-5">
                <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">Early network</p>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">Early testing uses Zolli credits. Cash payout is not live.</p>
              </div>
            </article>
          </div>
        </SectionReveal>
      </div>
    </section>
  );
}
