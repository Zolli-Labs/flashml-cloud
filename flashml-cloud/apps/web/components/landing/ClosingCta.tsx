import Link from "next/link";
import { ArrowRight, GithubLogo } from "@phosphor-icons/react/dist/ssr";
import { Mark } from "@/components/brand/Mark";
import { Reveal } from "@/components/motion/Reveal";
import { MagneticLink } from "@/components/motion/MagneticLink";

const REPO = "https://github.com/Zolli-Labs/flashml";

// No "All systems operational" pill in the footer. RunPod has one and it is
// a good pattern, but there is no status page behind it here, and a
// decorative uptime indicator on a page selling reliability is the one lie
// that would cost the most.

export function ClosingCta() {
  return (
    <>
      <Reveal className="mx-auto max-w-7xl px-4 py-28 text-center sm:px-6 md:py-36">
        <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-primary">
          <Mark size={28} className="text-primary-foreground" />
        </div>

        <h2 className="mx-auto mt-8 max-w-3xl text-3xl font-semibold tracking-[-0.028em] md:text-5xl">
          Read what claims your machine{" "}
          <span className="text-accent-text">before you attach one.</span>
        </h2>
        <p className="mx-auto mt-5 max-w-xl text-base leading-relaxed text-muted-foreground">
          The runtime, the wire protocol and the host agent are public under
          Apache 2.0. The scheduler that decides what runs on your hardware is
          not a black box.
        </p>

        <div className="mt-9 flex flex-wrap justify-center gap-3">
          <MagneticLink
            href="/submit"
            className="interactive inline-flex items-center gap-2 rounded-full bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground hover:brightness-110"
          >
            Submit a job
            <ArrowRight weight="bold" className="h-4 w-4" />
          </MagneticLink>
          <a
            href={REPO}
            target="_blank"
            rel="noreferrer"
            className="interactive inline-flex items-center gap-2 rounded-full border border-border bg-white/[0.04] px-6 py-3 text-sm font-medium text-foreground transition-colors hover:bg-white/[0.08]"
          >
            <GithubLogo size={16} weight="fill" />
            Read the source
          </a>
        </div>
      </Reveal>

      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-8 text-xs text-muted-foreground sm:flex-row sm:items-center sm:justify-between sm:px-6">
          <span>&copy; 2026 Zolli Labs</span>
          <nav className="flex gap-6">
            <Link href="/jobs" className="hover:text-foreground">
              Jobs
            </Link>
            <Link href="/machines" className="hover:text-foreground">
              Machines
            </Link>
            <a
              href={REPO}
              target="_blank"
              rel="noreferrer"
              className="hover:text-foreground"
            >
              GitHub
            </a>
          </nav>
        </div>
      </footer>
    </>
  );
}
