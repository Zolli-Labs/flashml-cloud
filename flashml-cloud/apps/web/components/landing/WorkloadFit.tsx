import { WorkloadRows } from "@/components/landing/WorkloadRows";
import { WorkloadVelocityRail } from "@/components/landing/WorkloadVelocityRail";
import { WORKLOADS } from "@/lib/landing/workloads";

export function WorkloadFit() {
  return (
    <section
      id="workloads"
      data-surface="light"
      className="landing-surface-light scroll-mt-20 border-b border-border py-20 md:py-28"
    >
      <div className="mx-auto max-w-[1240px] px-5 sm:px-6">
        <div className="grid gap-10 lg:grid-cols-[.72fr_1.28fr] lg:gap-12">
          <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
            Workload fit
          </p>
          <div>
            <h2 className="max-w-[820px] text-[clamp(2.65rem,5.4vw,4.75rem)] font-semibold leading-[0.99] tracking-[-0.052em]">
              Can your work run on <span className="text-muted-foreground">Zolli?</span>
            </h2>
            <p className="mt-5 max-w-[58ch] text-base leading-relaxed text-muted-foreground">
              Zolli fits work that can be divided or resumed.
            </p>
          </div>
        </div>

        <WorkloadVelocityRail labels={WORKLOADS.map(({ title }) => title)} />
        <WorkloadRows />
      </div>
    </section>
  );
}
