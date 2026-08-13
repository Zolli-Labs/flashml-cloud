import { ArchitectureSignal } from "@/components/landing/ArchitectureSignal";
import { SectionReveal } from "@/components/landing/motion/SectionReveal";

const LAYERS = [
  {
    index: "01",
    title: "Control",
    body: "Turn submitted work and available machines into bounded ownership.",
    modules: [["Coordinate", "LEASE_CLAIMED"], ["Enroll", "DEVICE_CODE · ACTIVATION"]],
  },
  {
    index: "02",
    title: "Execution",
    body: "Run isolated attempts and preserve durable progress while they work.",
    modules: [["Execute", "LEASE_RENEWED"], ["Checkpoint", "CHECKPOINT_MANIFEST_COMMITTED"]],
  },
  {
    index: "03",
    title: "Integrity",
    body: "Recover interrupted work and accept one verified outcome.",
    modules: [["Recover", "TASK_REQUEUED"], ["Verify", "TASK_COMMIT_ACCEPTED"]],
  },
] as const;

const LAYOUT = [
  "lg:col-span-7 lg:row-span-2",
  "lg:col-span-5",
  "lg:col-span-5",
] as const;

export function SystemModules() {
  return (
    <section
      id="architecture"
      data-surface="dark"
      data-layout="dense-architecture"
      className="landing-surface-dark relative scroll-mt-20 border-b border-white/10 py-20 md:py-28"
    >
      <span id="compute" className="pointer-events-none absolute -top-20" aria-hidden="true" />
      <span id="crew" className="pointer-events-none absolute -top-20" aria-hidden="true" />
      <div className="mx-auto max-w-[1240px] px-5 sm:px-6">
        <div className="max-w-3xl">
          <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
            Technical depth
          </p>
          <h2 className="mt-5 text-[clamp(2.65rem,5.4vw,4.75rem)] font-semibold leading-[0.99] tracking-[-0.052em]">
            The machinery behind <span className="text-muted-foreground">a reliable market.</span>
          </h2>
          <p className="mt-6 max-w-[58ch] text-lg leading-[1.55] text-muted-foreground">
            Allocation, execution, and integrity stay separate, so changing
            capacity still yields one accepted outcome.
          </p>
        </div>

        <ArchitectureSignal />

        <SectionReveal
          className="mt-12 md:mt-16"
          lineClassName="h-px w-full bg-white/18"
          bottomLineClassName="h-px w-full bg-white/18"
        >
          <div className="grid grid-flow-dense gap-px bg-white/14 lg:grid-cols-12 lg:grid-rows-2">
            {LAYERS.map(({ index, title, body, modules }, layerIndex) => (
              <article
                key={title}
                className={`min-w-0 bg-[#111416] p-6 sm:p-7 ${LAYOUT[layerIndex]}`}
              >
                <div className="flex items-start justify-between gap-5">
                  <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-[var(--landing-orange)]">
                    {index} {title}
                  </p>
                  <span aria-hidden className="mt-1 h-px flex-1 bg-white/14" />
                </div>
                <p className="mt-8 max-w-[43ch] text-[15px] leading-relaxed text-white/62">{body}</p>
                <dl className="mt-9 border-t border-white/14">
                  {modules.map(([module, event]) => (
                    <div key={module} className="grid min-w-0 gap-2 border-b border-white/10 py-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)] sm:items-baseline sm:gap-5">
                      <dt className="text-[15px] font-semibold text-white/92">{module}</dt>
                      <dd className="min-w-0 break-words font-mono text-[9px] leading-relaxed text-white/52 sm:text-right">{event}</dd>
                    </div>
                  ))}
                </dl>
              </article>
            ))}
          </div>
        </SectionReveal>
      </div>
    </section>
  );
}
