import { Check, Minus } from "@phosphor-icons/react/dist/ssr";
import { Reveal } from "@/components/motion/Reveal";

// The section that does the most work for credibility, and the one most
// likely to get quietly softened later. It should not be. With no logos and
// no customers, stating the limits IS the trust signal, and the bandwidth
// caveat below is load-bearing: the M1 spec and the supply-side note both
// say pooling is often slower than one machine, so the page must never
// imply otherwise.

const WORKS = [
  "A GitHub repo becomes a running job",
  "Federated rounds across enrolled machines",
  "Leases expire and tasks requeue with nobody watching",
  "Checkpoints counted only once every part hashes clean",
  "Artifacts downloadable when the run ends",
];

const NOT_YET = [
  "GPU detection and GPU-aware placement",
  "Result verification for machines you do not trust",
  "Payouts, credits and a marketplace",
];

export function HonestState() {
  return (
    <section className="border-t border-border bg-surface-2 py-20 md:py-28">
      <div className="mx-auto max-w-7xl px-4 sm:px-6">
        <Reveal>
          <p className="font-mono text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
            Where this actually is
          </p>
          <h2 className="mt-4 max-w-2xl text-3xl font-semibold tracking-[-0.025em] md:text-4xl">
            Alpha, and specific about it.
          </h2>
        </Reveal>

        <Reveal delay={0.08} className="mt-12 grid gap-10 md:grid-cols-2 md:gap-16">
          <div>
            {/* Sentence case, not a mono all-caps label. Uppercase micro-
                labels above every block are the templated rhythm this page
                is trying not to have; the two real eyebrows are enough. */}
            <h3 className="text-sm font-semibold text-[var(--node-green)]">
              Works today
            </h3>
            <ul className="mt-5 space-y-3">
              {WORKS.map((item) => (
                <li key={item} className="flex gap-3 text-sm leading-relaxed">
                  <Check
                    size={16}
                    weight="bold"
                    className="mt-[3px] shrink-0 text-[var(--node-green)]"
                  />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <h3 className="text-sm font-semibold text-muted-foreground">
              Not yet
            </h3>
            <ul className="mt-5 space-y-3">
              {NOT_YET.map((item) => (
                <li
                  key={item}
                  className="flex gap-3 text-sm leading-relaxed text-muted-foreground"
                >
                  <Minus
                    size={16}
                    weight="bold"
                    className="mt-[3px] shrink-0 opacity-50"
                  />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </div>
        </Reveal>

        <Reveal
          delay={0.12}
          className="mt-14 max-w-3xl border-l-2 border-[var(--warning)]/50 pl-6"
        >
          <p className="text-sm leading-relaxed text-muted-foreground md:text-base">
            One thing worth saying plainly: every federated round ships
            weights out and deltas back. Over home internet with a small
            model, pooling machines is usually{" "}
            <span className="text-foreground">slower</span> than training on
            one. FlashML is for work that shards across machines, not for
            making a single run faster.
          </p>
        </Reveal>
      </div>
    </section>
  );
}
