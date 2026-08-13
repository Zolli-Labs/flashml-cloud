import { SectionReveal } from "@/components/landing/motion/SectionReveal";

const STEPS = [
  {
    title: "Tell Zolli what you need.",
    body: "Describe the work and what matters—price, speed, or hardware.",
  },
  {
    title: "The network finds suitable machines.",
    body: "Zolli matches it to owned, community, and rented capacity.",
  },
  {
    title: "Your work continues as capacity changes.",
    body: "Recorded progress lets supported work continue on another machine.",
  },
] as const;

// The two facts worth keeping once the three-lane architecture walkthrough
// (host / runtime / recovery) is cut: what the sandbox actually allows, and
// what "parallel" actually means. Recovery's own lane is dropped here — the
// recovery proof section right below this one already carries that beat.
const MODULE_FACTS = [
  {
    label: "Host",
    body: "Every machine answers to flashnode. Shared machines run only allowlisted Docker images, sandboxed from the host.",
  },
  {
    label: "Runtime",
    body: "Independent tasks lease across machines. Inside one machine, multi-GPU DDP and FSDP run as PyTorch intends.",
  },
] as const;

export function HowItWorks() {
  return (
    <section
      id="how-it-works"
      data-surface="dark"
      className="landing-surface-dark scroll-mt-20 border-b border-white/10 py-20 text-[var(--landing-ivory)] md:py-28"
    >
      <div className="mx-auto max-w-[1240px] px-5 sm:px-6">
        <SectionReveal className="pb-10 md:pb-14">
          <div className="grid gap-8 md:grid-cols-[.72fr_1.28fr] md:gap-12">
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-[var(--landing-orange)]">How Zolli works</p>
            <h2 className="max-w-[820px] text-[clamp(2.65rem,5.4vw,4.75rem)] font-semibold leading-[0.99] tracking-[-0.052em]">
              From a compute need <span className="text-muted-foreground">to finished work.</span>
            </h2>
          </div>
        </SectionReveal>

        <SectionReveal>
          <ol className="grid border-t border-white/18 pt-10 md:grid-cols-3 md:pt-14">
            {STEPS.map((step, index) => (
              <li
                key={step.title}
                data-human-step={index + 1}
                className="border-b border-white/12 py-8 last:border-b-0 md:border-b-0 md:border-r md:px-8 md:first:pl-0 md:last:border-r-0 md:last:pr-0 md:py-12"
              >
                <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-[var(--landing-orange)]">0{index + 1}</p>
                <h3 className="mt-6 text-[clamp(1.45rem,2.4vw,2rem)] font-semibold leading-tight tracking-[-0.035em]">{step.title}</h3>
                <p className="mt-4 max-w-[35ch] text-[15px] leading-relaxed text-white/62">{step.body}</p>
              </li>
            ))}
          </ol>

          <div className="mt-10 grid gap-8 border-t border-white/12 pt-8 sm:grid-cols-2 md:mt-14 md:pt-10">
            {MODULE_FACTS.map((fact, index) => (
              <div
                key={fact.label}
                data-module-fact={fact.label}
                className={index > 0 ? "sm:border-l sm:border-white/12 sm:pl-8" : undefined}
              >
                <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-[var(--landing-orange)]">
                  {fact.label}
                </p>
                <p className="mt-3 max-w-[48ch] text-[15px] leading-relaxed text-white/62">{fact.body}</p>
              </div>
            ))}
          </div>
        </SectionReveal>
      </div>
    </section>
  );
}
