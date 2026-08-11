import { MachineCompatibilityCheck } from "@/components/landing/MachineCompatibilityCheck";
import { RuntimeSupportExplorer } from "@/components/landing/RuntimeSupportExplorer";
import { SectionReveal } from "@/components/landing/motion/SectionReveal";
import { HOST_SUPPORT } from "@/lib/landing/platform";

export function PlatformSupport() {
  return (
    <section
      id="platform"
      data-surface="sand"
      data-layout="machine-lanes"
      className="landing-surface-sand scroll-mt-20 border-b border-border py-20 md:py-28"
    >
      <div className="mx-auto max-w-[1240px] px-5 sm:px-6">
        <div className="grid gap-10 lg:grid-cols-[.72fr_1.28fr] lg:gap-12">
          <div>
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
              Platform support
            </p>
            <h2 className="mt-5 text-[clamp(2.65rem,5.4vw,4.75rem)] font-semibold leading-[0.99] tracking-[-0.052em]">
              Start from the machines <span className="text-muted-foreground">you can already run.</span>
            </h2>
          </div>
          <p className="max-w-[58ch] self-end text-base leading-relaxed text-muted-foreground">
            Host support is stated by observed execution state, with the execution contract shown alongside it.
          </p>
        </div>

        <SectionReveal
          className="mt-16 lg:mt-20"
          lineClassName="h-px w-full bg-[var(--z-border-strong)]"
          bottomLineClassName="h-px w-full bg-[var(--z-border-strong)]"
        >
          <div>
            <div className="px-0 py-5 sm:py-6">
              <p className="font-mono text-[11px] font-medium uppercase tracking-[0.1em] text-brand-foreground">
                Runtime support
              </p>
              <p className="mt-2 max-w-[52ch] text-sm text-muted-foreground">
                Select a runtime to see the curated image it ships in, where one is registered.
              </p>
            </div>
            <RuntimeSupportExplorer />
          </div>

          <div className="mt-10 border-t border-[var(--z-border-strong)] pt-5 sm:mt-12 sm:pt-6">
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.1em] text-brand-foreground">
              Control-plane host lanes
            </p>
            <p className="mt-2 max-w-[52ch] text-sm text-muted-foreground">
              Production-proven hosts and a preview host share one execution contract.
            </p>
            <div className="mt-5 grid gap-4 sm:grid-cols-3">
              {HOST_SUPPORT.map((host) => (
                <HostCard key={host.platform} {...host} />
              ))}
            </div>
          </div>

          <div className="mt-10 border-t border-[var(--z-border-strong)] pt-5 sm:mt-12 sm:pt-6">
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.1em] text-brand-foreground">
              Local machine hint
            </p>
            <p className="mt-2 max-w-[52ch] text-sm text-muted-foreground">
              Optional, browser-only, and never a substitute for a real host check.
            </p>
            <div className="mt-5">
              <MachineCompatibilityCheck />
            </div>
          </div>
        </SectionReveal>
      </div>
    </section>
  );
}

function HostCard({ platform, state, body }: (typeof HOST_SUPPORT)[number]) {
  const stateClass = state === "Proven"
    ? "text-[var(--z-healthy)]"
    : "text-[var(--z-warning)]";

  return (
    <article
      data-host-card={platform}
      className="rounded-[10px] border border-border bg-card p-5"
    >
      <h4 className="text-[15px] font-semibold">{platform}</h4>
      <span
        data-host-state
        className={`mt-2 inline-block font-mono text-[9px] font-medium uppercase tracking-[0.06em] ${stateClass}`}
      >
        {state}
      </span>
      <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{body}</p>
    </article>
  );
}
