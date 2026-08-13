import { ArrowUpRight } from "@phosphor-icons/react/dist/ssr";
import { SectionReveal } from "@/components/landing/motion/SectionReveal";
import { MARKETING } from "@/lib/marketing";

const SERVICES = [
  {
    index: "01",
    title: "Architecture and workload assessment",
    description:
      "Map the machines, workload boundaries, and recovery requirements before rollout.",
  },
  {
    index: "02",
    title: "Machine and GPU fleet onboarding",
    description:
      "Bring local workstations, home rigs, and cloud GPU hosts into one operational workspace.",
  },
  {
    index: "03",
    title: "Runtime and job-spec integration",
    description:
      "Shape existing Python entrypoints and flashml.yaml job definitions around bounded tasks.",
  },
  {
    index: "04",
    title: "Private deployment and recovery design",
    description:
      "Define deployment boundaries, checkpoint flow, and deterministic recovery for your environment.",
  },
] as const;

const SERVICE_ROWS = [SERVICES.slice(0, 2), SERVICES.slice(2, 4)] as const;

export function ProfessionalServices() {
  return (
    <section
      id="services"
      data-surface="dark"
      data-layout="service-rows"
      className="landing-surface-dark scroll-mt-20 border-b border-border py-20 md:py-28"
    >
      <div className="mx-auto max-w-[1240px] px-5 sm:px-6">
        <div className="grid gap-10 lg:grid-cols-[.95fr_1.05fr] lg:gap-16">
          <div>
            <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
              Professional services
            </p>
            <h2 className="landing-heading-balance mt-5 max-w-[720px] text-[clamp(2.25rem,9.4vw,3.75rem)] font-semibold leading-[0.99] tracking-[-0.052em]">
              Start with the machines and workloads you already have.
            </h2>
          </div>

          <SectionReveal className="lg:pt-8">
            <div className="pt-7">
              <p className="max-w-[55ch] text-base leading-relaxed text-muted-foreground">
                Zolli can help determine fit, connect a fleet, and adapt a divisible or checkpointable
                workload. Engagement scope is agreed directly with Zolli.
              </p>
              <div className="mt-8 flex flex-wrap items-center gap-x-5 gap-y-3">
                <a
                  href={MARKETING.calendlyUrl}
                  target="_blank"
                  rel="noreferrer"
                  aria-label="Schedule with Zolli (opens in a new tab)"
                  className="interactive inline-flex min-h-10 items-center gap-2 rounded-[7px] border border-foreground bg-foreground px-4 text-[13px] font-semibold text-background hover:opacity-90"
                >
                  Schedule with Zolli
                  <ArrowUpRight weight="bold" className="h-4 w-4" />
                </a>
                <a
                  href={`mailto:${MARKETING.contactEmail}`}
                  className="interactive inline-flex min-h-10 items-center text-[13px] font-semibold text-muted-foreground underline decoration-border underline-offset-4 hover:text-foreground"
                >
                  {MARKETING.contactEmail}
                </a>
              </div>
            </div>
          </SectionReveal>
        </div>

        <div className="mt-14 border-t border-[var(--z-border-strong)] md:mt-20">
          {SERVICE_ROWS.map((row, rowIndex) => (
            <div
              key={rowIndex}
              data-service-row={rowIndex + 1}
              className="grid border-b border-[var(--z-border-strong)] md:grid-cols-2"
            >
              {row.map((service) => (
                <article
                  key={service.title}
                  className="grid gap-5 border-b border-border py-8 last:border-b-0 md:grid-cols-[3rem_1fr] md:border-b-0 md:px-8 md:first:border-r md:first:pl-0 md:last:pr-0"
                >
                  <p className="font-mono text-[10px] text-muted-foreground">
                    {service.index}
                  </p>
                  <div>
                    <h3 className="max-w-[22ch] text-2xl font-semibold tracking-[-0.035em]">
                      {service.title}
                    </h3>
                    <p className="mt-4 max-w-[48ch] text-sm leading-relaxed text-muted-foreground">
                      {service.description}
                    </p>
                  </div>
                </article>
              ))}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
