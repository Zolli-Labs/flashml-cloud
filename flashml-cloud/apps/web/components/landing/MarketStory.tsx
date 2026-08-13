export function MarketStory() {
  return (
    <section
      id="network"
      data-surface="light"
      className="landing-surface-light scroll-mt-20 border-b border-border py-20 md:py-28"
    >
      <div className="mx-auto max-w-[1240px] px-5 sm:px-6">
        <div className="grid gap-8 border-b border-border pb-10 md:grid-cols-[.72fr_1.28fr] md:gap-12 md:pb-14">
          <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
            The compute market
          </p>
          <div>
            <h2 className="max-w-[820px] text-[clamp(2.65rem,5.4vw,4.75rem)] font-semibold leading-[0.99] tracking-[-0.052em]">
              Compute is everywhere. Access is not.
            </h2>
            <p className="mt-6 max-w-[62ch] text-base leading-relaxed text-muted-foreground">
              Laptops, lab machines, rented GPUs, and cloud accounts behave like separate islands while useful machines elsewhere sit idle.
            </p>
          </div>
        </div>

        <div className="grid gap-8 border-b border-border py-10 md:grid-cols-[.72fr_1.28fr] md:gap-12 md:py-14">
          <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
            A new allocation path
          </p>
          <div>
            <h3 className="max-w-[720px] text-[clamp(1.9rem,3.5vw,3.1rem)] font-semibold leading-[1.02] tracking-[-0.04em]">
              From isolated machines to an open compute network.
            </h3>
            <p className="mt-5 max-w-[62ch] text-base leading-relaxed text-muted-foreground">
              Zolli is building one allocation path across personally owned, team, community, and rented capacity, guided by price, completion time, and hardware.
            </p>
          </div>
        </div>

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
      </div>
    </section>
  );
}
