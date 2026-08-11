import { MARKETING } from "@/lib/marketing";

const FAQS = [
  {
    question: "What does Zolli coordinate?",
    answer:
      "Zolli coordinates jobs, tasks, leases, checkpoints, recovery, and accepted results across the machines in a workspace.",
  },
  {
    question: "Which machines are supported?",
    answer:
      "macOS arm64 and Linux x86_64 are production-proven hosts. Windows 11 with Docker Desktop and the WSL2 backend is currently preview support.",
  },
  {
    question: "What happens when a machine disappears?",
    answer:
      "A missing heartbeat expires that machine’s ownership of the task. The task is requeued and another worker resumes from the last verified checkpoint.",
  },
  {
    question: "Does every machine need Docker?",
    answer:
      "No. Subprocess execution is available for trusted pools, while allowlisted Docker containers are the isolation path for shared machines.",
  },
  {
    question: "How are code, artifacts, and credentials handled?",
    answer:
      "Task environments are scrubbed, machine writes are authenticated and lease-scoped, and artifacts and checkpoints are hash-verified. Deployment configuration still matters to the security boundary.",
  },
  {
    question: "How is Zolli priced?",
    answer:
      "Pricing is not published during early access. Schedule a conversation or email Zolli to agree the scope directly.",
  },
  {
    question: "What support is available during early access?",
    answer:
      "Early-access support can cover onboarding, workload integration, deployment, and recovery design by agreement. No service-level agreement is offered or implied.",
  },
] as const;

export function Faq() {
  return (
    <section
      id="faq"
      data-surface="light"
      className="landing-surface-light scroll-mt-20 border-b border-border py-20 md:py-28"
    >
      <div className="mx-auto grid max-w-[1240px] gap-12 px-5 sm:px-6 lg:grid-cols-[.72fr_1.28fr] lg:gap-16">
        <div>
          <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
            FAQ
          </p>
          <h2 className="mt-5 text-[clamp(2.65rem,5.4vw,4.75rem)] font-semibold leading-[0.99] tracking-[-0.052em]">
            Evaluate the fit. <span className="text-muted-foreground">Know the boundaries.</span>
          </h2>
          <p className="mt-6 max-w-[45ch] text-base leading-relaxed text-muted-foreground">
            Need to discuss a specific deployment? Email{" "}
            <a
              href={`mailto:${MARKETING.contactEmail}`}
              className="interactive inline-flex min-h-10 items-center align-middle underline decoration-border underline-offset-4 hover:text-foreground"
            >
              {MARKETING.contactEmail}
            </a>
            .
          </p>
        </div>

        <div className="border-t border-[var(--z-border-strong)]">
          {FAQS.map((faq, index) => (
            <details key={faq.question} className="group relative border-b border-border">
              <summary className="interactive flex min-h-16 cursor-pointer list-none items-center justify-between gap-5 py-5 text-left [&::-webkit-details-marker]:hidden">
                <span className="flex items-baseline gap-4">
                  <span className="font-mono text-[10px] text-muted-foreground">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <span className="text-base font-semibold tracking-[-0.015em] sm:text-lg">
                    {faq.question}
                  </span>
                </span>
                <span
                  aria-hidden="true"
                  className="font-mono text-lg text-brand-foreground transition-transform duration-200 group-open:rotate-45"
                >
                  +
                </span>
              </summary>
              <div className="opacity-0 transition-opacity duration-200 group-open:opacity-100">
                <p className="max-w-[68ch] pb-6 pl-10 pr-8 text-[15px] leading-relaxed text-muted-foreground">
                  {faq.answer}
                </p>
              </div>
              <span
                aria-hidden
                className="absolute inset-x-0 bottom-[-1px] h-px origin-left scale-x-0 bg-foreground transition-transform duration-200 group-open:scale-x-100"
              />
            </details>
          ))}
        </div>
      </div>
    </section>
  );
}
