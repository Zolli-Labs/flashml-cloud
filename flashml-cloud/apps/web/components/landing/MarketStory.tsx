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
          <div className="grid gap-6 pt-10 md:grid-cols-2 md:gap-8 md:pt-14">
            <article
              data-market-side="demand"
              className="border border-[var(--z-border-strong)] bg-[var(--z-surface)] p-7 sm:p-9"
            >
              <div className="flex items-baseline justify-between gap-5">
                <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">Demand</p>
                <p className="font-mono text-[11px] uppercase tracking-[0.13em] text-muted-foreground">I need compute</p>
              </div>
              <h3 className="mt-6 text-[clamp(1.7rem,3vw,2.6rem)] font-semibold leading-[1.02] tracking-[-0.04em]">
                Need compute? <span className="text-muted-foreground">Every source competes.</span>
              </h3>
              <p className="mt-4 max-w-[46ch] text-[15px] leading-relaxed text-muted-foreground">
                One request reaches your own machines, community hosts, and cloud
                providers at once — they compete on price and finish time.
              </p>
              <ul className="mt-7 border-t border-border">
                {[
                  ["Lower cost", "Hosts and providers bid for your work."],
                  ["More options", "Every compatible machine on the network is a candidate."],
                  ["No lock-in", "Never depend on one provider's price or availability."],
                ].map(([label, body]) => (
                  <li key={label} className="grid gap-1 border-b border-border py-3.5 sm:grid-cols-[9rem_1fr] sm:gap-5">
                    <p className="text-[15px] font-semibold tracking-[-0.01em]">{label}</p>
                    <p className="text-sm leading-relaxed text-muted-foreground">{body}</p>
                  </li>
                ))}
              </ul>
              <div className="mt-7">
                <p className="font-mono text-[11px] font-medium uppercase tracking-[0.1em] text-muted-foreground">Capacity sources</p>
                <div className="mt-3 flex flex-wrap items-center gap-2.5">
                  {[
                    ["Z", "Zolli hosts"],
                    ["R", "RunPod"],
                    ["A", "Alibaba Cloud"],
                  ].map(([mark, name]) => (
                    <span
                      key={name}
                      className="inline-flex items-center gap-2 rounded-[7px] border border-[var(--z-border-strong)] bg-[var(--z-surface-2)] py-1.5 pl-1.5 pr-3"
                    >
                      <span aria-hidden className="grid h-6 w-6 place-items-center rounded-[5px] bg-[var(--landing-graphite)] font-mono text-[10px] font-semibold text-[#f3f1ec]">
                        {mark}
                      </span>
                      <span className="font-mono text-[11px] font-medium uppercase tracking-[0.08em]">{name}</span>
                    </span>
                  ))}
                  <span className="font-mono text-[11px] uppercase tracking-[0.08em] text-muted-foreground">+ more lanes expanding</span>
                </div>
              </div>
            </article>
            <article
              data-market-side="supply"
              className="border border-[var(--z-border-strong)] bg-[var(--z-surface)] p-7 sm:p-9"
            >
              <div className="flex items-baseline justify-between gap-5">
                <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">Supply</p>
                <p className="font-mono text-[11px] uppercase tracking-[0.13em] text-muted-foreground">I have machines</p>
              </div>
              <h3 className="mt-6 text-[clamp(1.7rem,3vw,2.6rem)] font-semibold leading-[1.02] tracking-[-0.04em]">
                Idle machines? <span className="text-muted-foreground">Let them earn.</span>
              </h3>
              <p className="mt-4 max-w-[46ch] text-[15px] leading-relaxed text-muted-foreground">
                Connect hardware you already own. It becomes productive capacity
                the moment it completes useful work.
              </p>
              <ul className="mt-7 border-t border-border">
                {[
                  ["Earn from idle", "Machines you already pay for start paying you back."],
                  ["Sandboxed by default", "Tasks run allowlisted, network-off, and resource-capped."],
                  ["Only real work counts", "Earnings follow verified, accepted outcomes."],
                ].map(([label, body]) => (
                  <li key={label} className="grid gap-1 border-b border-border py-3.5 sm:grid-cols-[9rem_1fr] sm:gap-5">
                    <p className="text-[15px] font-semibold tracking-[-0.01em]">{label}</p>
                    <p className="text-sm leading-relaxed text-muted-foreground">{body}</p>
                  </li>
                ))}
              </ul>
              <div className="mt-7 border-t border-border pt-5">
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
