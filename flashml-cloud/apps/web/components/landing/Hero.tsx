import Link from "next/link";
import { ArrowRight, ArrowUpRight } from "@phosphor-icons/react/dist/ssr";
import { HeroComputeFabric } from "@/components/landing/HeroComputeFabric";
import { MARKETING } from "@/lib/marketing";

export function Hero() {
  return (
    <section
      id="hero"
      data-surface="dark"
      className="relative isolate overflow-hidden border-b border-border pt-20"
    >
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10"
        style={{
          backgroundImage:
            "radial-gradient(circle at 78% 8%, rgb(243 107 50 / 0.08), transparent 23rem)",
        }}
      />
      <div className="mx-auto max-w-[1440px] px-5 pb-10 sm:px-6 xl:px-12">
        <div className="grid min-w-0 items-center gap-10 py-8 xl:grid-cols-[minmax(28rem,.82fr)_minmax(0,1.18fr)] xl:gap-10 2xl:gap-14">
          <div className="min-w-0">
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
              Fault-tolerant distributed compute
            </p>
            <h1 className="mt-5 max-w-[78rem] text-[clamp(2.75rem,5.6vw,5.7rem)] font-semibold leading-[0.93] tracking-[-0.058em]">
              <span className="block lg:whitespace-nowrap">Compute that </span>
              <span className="block text-muted-foreground lg:whitespace-nowrap">finishes the job.</span>
            </h1>
            <p className="mt-7 max-w-[58ch] text-[15px] leading-[1.62] tracking-[-0.006em] text-muted-foreground sm:mt-8">
              Zolli unifies compatible cloud capacity, rented compute, owned GPU infrastructure, and everyday machines under one control plane, then recovers work when a node disappears.
            </p>
            <div className="mt-7 flex flex-wrap gap-2.5">
              <Link
                href={MARKETING.consolePath}
                title="Open console"
                className="interactive inline-flex min-h-10 items-center gap-2 rounded-[7px] border border-primary bg-primary px-4 text-[13px] font-semibold text-primary-foreground hover:bg-[var(--z-orange-bright)]"
              >
                Open console
                <ArrowRight weight="bold" className="h-4 w-4" />
              </Link>
              <a
                href={MARKETING.calendlyUrl}
                target="_blank"
                rel="noreferrer"
                aria-label="Talk to Zolli (opens in a new tab)"
                className="interactive inline-flex min-h-10 items-center gap-2 rounded-[7px] border border-[var(--z-border-strong)] bg-surface px-4 text-[13px] font-semibold hover:bg-[var(--z-surface-hover)]"
              >
                Talk to Zolli
                <ArrowUpRight weight="bold" className="h-4 w-4" />
              </a>
            </div>
          </div>

          <HeroComputeFabric />
        </div>
      </div>
    </section>
  );
}
