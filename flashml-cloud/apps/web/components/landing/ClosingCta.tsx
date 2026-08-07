import Link from "next/link";
import { ArrowRight, GithubLogo } from "@phosphor-icons/react/dist/ssr";
import { Wordmark } from "@/components/brand/Mark";
import { ZolliCharacter } from "@/components/brand/ZolliCharacter";
import { Reveal } from "@/components/motion/Reveal";
import { MagneticLink } from "@/components/motion/MagneticLink";

const REPO = "https://github.com/Zolli-Labs/flashml";

export function ClosingCta() {
  return (
    <>
      <Reveal className="mx-auto max-w-7xl px-4 py-24 text-center sm:px-6 md:py-32">
        <div className="mx-auto flex max-w-sm items-end justify-center -space-x-5" aria-hidden>
          <ZolliCharacter role="captain" size={92} />
          <ZolliCharacter role="worker" size={104} mood="focused" />
          <ZolliCharacter role="scout" size={92} mood="waving" />
        </div>

        <h2 className="mx-auto mt-6 max-w-4xl font-display text-4xl font-semibold leading-[1.02] tracking-[-0.04em] md:text-6xl">
          Give every machine a role in the crew
        </h2>
        <p className="mx-auto mt-5 max-w-xl text-base leading-relaxed text-muted-foreground">
          Bring the hardware you already have together, then let leases, checkpoints, and recovery keep useful work moving.
        </p>

        <div className="mt-9 flex flex-wrap justify-center gap-3">
          <MagneticLink
            href="/workspaces"
            className="interactive inline-flex items-center gap-2 rounded-full bg-primary px-6 py-3 text-sm font-semibold text-primary-foreground transition-shadow hover:shadow-md"
          >
            Create your crew
            <ArrowRight weight="bold" className="h-4 w-4" />
          </MagneticLink>
          <Link
            href="/docs"
            className="interactive inline-flex items-center gap-2 rounded-full border border-border bg-surface px-6 py-3 text-sm font-medium text-foreground transition-colors hover:bg-surface-2"
          >
            Read the docs
          </Link>
        </div>
      </Reveal>

      <footer className="border-t border-border">
        <div className="mx-auto grid max-w-7xl gap-8 px-4 py-9 text-sm text-muted-foreground sm:px-6 md:grid-cols-[1fr_auto] md:items-end">
          <div>
            <Wordmark product />
            <p className="mt-3 max-w-lg text-xs leading-relaxed">
              ZolliAI Cloud is an early product. FlashML remains the open runtime and wire protocol underneath.
            </p>
            <p className="mt-3 text-xs">&copy; 2026 Zolli Labs</p>
          </div>
          <nav aria-label="Footer navigation" className="flex flex-wrap gap-x-5 gap-y-3 md:justify-end">
            <Link href="/workspaces" className="hover:text-foreground">
              Crews
            </Link>
            <Link href="/machines" className="hover:text-foreground">
              Zollis
            </Link>
            <Link href="/jobs" className="hover:text-foreground">
              Jobs
            </Link>
            <Link href="/docs" className="hover:text-foreground">
              Docs
            </Link>
            <a
              href={REPO}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 hover:text-foreground"
            >
              <GithubLogo size={14} weight="fill" />
              GitHub
            </a>
          </nav>
        </div>
      </footer>
    </>
  );
}
