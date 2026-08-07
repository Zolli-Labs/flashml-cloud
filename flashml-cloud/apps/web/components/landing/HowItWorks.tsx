import { Reveal, RevealGroup, RevealItem } from "@/components/motion/Reveal";

const STEPS = [
  {
    verb: "Point",
    title: "Give it a repo and a ref",
    body: "FlashML reads your training code straight from GitHub. There is no archive to build and nothing to upload by hand.",
  },
  {
    verb: "Check",
    title: "Preflight reports everything at once",
    body: "A job that cannot run fails here, with every finding in one response, rather than halfway through round three on somebody else's machine.",
  },
  {
    verb: "Run",
    title: "Tasks go wherever there is room",
    body: "Whatever machines are attached claim work a round at a time and checkpoint as they go. Attach more and the pool grows. Lose some and the run continues.",
  },
];

export function HowItWorks() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-20 sm:px-6 md:py-28">
      <Reveal>
        <h2 className="max-w-xl text-3xl font-semibold tracking-[-0.025em] md:text-4xl">
          From a repo URL to a running job.
        </h2>
      </Reveal>

      <RevealGroup className="mt-14" stagger={0.09}>
        {STEPS.map((s, i) => (
          <RevealItem key={s.verb}>
            {i > 0 && <hr className="rule-fade border-0" />}
            <div className="grid gap-4 py-9 md:grid-cols-12 md:gap-10 md:py-12">
              <div className="md:col-span-3">
                <span className="font-mono text-2xl font-medium tracking-tight text-brand-foreground md:text-3xl">
                  {s.verb}
                </span>
              </div>
              <h3 className="text-lg font-semibold md:col-span-4">{s.title}</h3>
              <p className="max-w-[54ch] text-sm leading-relaxed text-muted-foreground md:col-span-5">
                {s.body}
              </p>
            </div>
          </RevealItem>
        ))}
      </RevealGroup>
    </section>
  );
}
