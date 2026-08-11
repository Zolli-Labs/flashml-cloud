import Link from "next/link";
import { ArrowRight, ArrowUpRight } from "@phosphor-icons/react/dist/ssr";
import { Wordmark } from "@/components/brand/Mark";
import { CommitSignal } from "@/components/landing/CommitSignal";
import { MARKETING } from "@/lib/marketing";

export function ClosingCta() {
  return (
    <>
      <section
        id="start"
        data-surface="orange"
        className="landing-surface-orange px-5 py-20 sm:px-6 md:py-28"
      >
        <div className="mx-auto grid max-w-[1240px] gap-12 lg:grid-cols-[1.2fr_.8fr] lg:items-end lg:gap-18">
          <div>
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
              Zolli Cloud
            </p>
            <h2 className="mt-4 max-w-[800px] text-[clamp(3rem,6vw,5.25rem)] font-semibold leading-[0.96] tracking-[-0.055em]">
              Bring the fleet. <span className="text-muted-foreground">Keep the progress.</span>
            </h2>
          </div>

          <div className="border-t border-[var(--z-border-strong)] pt-6">
            <p className="max-w-[42ch] text-[15px] leading-relaxed text-muted-foreground">
              Connect the machines you already have. Zolli handles bounded leases,
              checkpoint verification, and deterministic recovery.
            </p>
            <div className="mt-7 flex flex-wrap items-center gap-x-5 gap-y-2.5">
              <Link
                href={MARKETING.consolePath}
                className="interactive inline-flex min-h-10 items-center gap-2 rounded-[7px] border border-[var(--landing-graphite)] bg-[var(--landing-graphite)] px-5 text-[13px] font-semibold text-[#f3f1ec] hover:bg-[#1b1f20]"
              >
                Open console
                <ArrowRight weight="bold" className="h-4 w-4" />
              </Link>
              <a
                href={MARKETING.calendlyUrl}
                target="_blank"
                rel="noreferrer"
                aria-label="Talk to Zolli (opens in a new tab)"
                className="interactive inline-flex min-h-10 items-center gap-2 text-[13px] font-semibold text-foreground underline decoration-[var(--z-border-strong)] underline-offset-4 hover:decoration-foreground"
              >
                Talk to Zolli
                <ArrowUpRight weight="bold" className="h-4 w-4" />
              </a>
            </div>
          </div>
        </div>
        <div className="mx-auto max-w-[1240px]">
          <CommitSignal event="TASK_COMMIT_ACCEPTED" />
        </div>
      </section>

      <footer
        data-surface="dark"
        className="landing-surface-dark border-t border-white/20"
      >
        <div className="mx-auto grid max-w-[1240px] gap-12 px-5 py-12 text-muted-foreground sm:px-6 lg:grid-cols-[1.1fr_1.9fr] lg:gap-16">
          <div>
            <Wordmark product tone="dark" />
            <p className="mt-4 max-w-sm text-xs leading-relaxed">
              Zolli Cloud is an early product. FlashML remains the open runtime and wire protocol underneath.
            </p>
            <p className="mt-5 font-mono text-[10px]">&copy; 2026 Zolli Labs</p>
          </div>
          <div className="grid grid-cols-2 gap-x-8 gap-y-10 sm:grid-cols-4">
            <nav aria-label="Product footer navigation">
              <p className="font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-foreground">
                Product
              </p>
              <div className="mt-3 flex flex-col">
                <Link href={MARKETING.consolePath} className="interactive inline-flex min-h-10 items-center text-xs hover:text-foreground">
                  Console
                </Link>
                <Link href="/account/machines" className="interactive inline-flex min-h-10 items-center text-xs hover:text-foreground">
                  Machines
                </Link>
                <Link href="/jobs" className="interactive inline-flex min-h-10 items-center text-xs hover:text-foreground">
                  Jobs
                </Link>
                <Link href="#platform" className="interactive inline-flex min-h-10 items-center text-xs hover:text-foreground">
                  Platform
                </Link>
              </div>
            </nav>

            <nav aria-label="Resources footer navigation">
              <p className="font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-foreground">
                Resources
              </p>
              <div className="mt-3 flex flex-col">
                <Link href="/docs" className="interactive inline-flex min-h-10 items-center text-xs hover:text-foreground">
                  Docs
                </Link>
                <a
                  href={MARKETING.runtimeRepo}
                  target="_blank"
                  rel="noreferrer"
                  className="interactive inline-flex min-h-10 items-center text-xs hover:text-foreground"
                >
                  GitHub<span className="sr-only"> (opens in a new tab)</span>
                </a>
                <a
                  href={MARKETING.runtimeRepo}
                  target="_blank"
                  rel="noreferrer"
                  className="interactive inline-flex min-h-10 items-center text-xs hover:text-foreground"
                >
                  Open runtime<span className="sr-only"> (opens in a new tab)</span>
                </a>
                <Link href="#faq" className="interactive inline-flex min-h-10 items-center text-xs hover:text-foreground">
                  FAQ
                </Link>
              </div>
            </nav>

            <nav aria-label="Company footer navigation">
              <p className="font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-foreground">
                Company
              </p>
              <div className="mt-3 flex flex-col">
                <Link href="/contact" className="interactive inline-flex min-h-10 items-center text-xs hover:text-foreground">
                  Contact
                </Link>
                <a
                  href={MARKETING.calendlyUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="interactive inline-flex min-h-10 items-center text-xs hover:text-foreground"
                >
                  Schedule a call<span className="sr-only"> (opens in a new tab)</span>
                </a>
              </div>
            </nav>

            <nav aria-label="Legal footer navigation">
              <p className="font-mono text-[10px] font-medium uppercase tracking-[0.12em] text-foreground">
                Legal
              </p>
              <div className="mt-3 flex flex-col">
                <Link href="/privacy" className="interactive inline-flex min-h-10 items-center text-xs hover:text-foreground">
                  Privacy
                </Link>
                <Link href="/terms" className="interactive inline-flex min-h-10 items-center text-xs hover:text-foreground">
                  Terms
                </Link>
                <Link href="/security" className="interactive inline-flex min-h-10 items-center text-xs hover:text-foreground">
                  Security
                </Link>
              </div>
            </nav>
          </div>
        </div>
      </footer>
    </>
  );
}
