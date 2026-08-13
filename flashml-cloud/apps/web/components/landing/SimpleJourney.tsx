const STEPS = [
  {
    title: "Tell Zolli what you need.",
    body: "Describe the work, required hardware, and whether price or finish time matters more.",
  },
  {
    title: "The network finds suitable machines.",
    body: "Zolli can consider owned, community, and rented capacity that fits the work.",
  },
  {
    title: "Your work continues as capacity changes.",
    body: "Progress can be recorded so supported work can continue on another compatible machine.",
  },
] as const;

export function SimpleJourney() {
  return (
    <section
      id="how-it-works"
      data-surface="dark"
      className="scroll-mt-20 border-b border-white/10 py-20 text-[var(--landing-ivory)] md:py-28"
    >
      <div className="mx-auto max-w-[1240px] px-5 sm:px-6">
        <div className="grid gap-8 border-b border-white/12 pb-10 md:grid-cols-[.72fr_1.28fr] md:gap-12 md:pb-14">
          <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-[var(--landing-orange)]">How Zolli works</p>
          <h2 className="max-w-[820px] text-[clamp(2.65rem,5.4vw,4.75rem)] font-semibold leading-[0.99] tracking-[-0.052em]">From a compute need to finished work.</h2>
        </div>
        <ol className="grid md:grid-cols-3">
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
      </div>
    </section>
  );
}
