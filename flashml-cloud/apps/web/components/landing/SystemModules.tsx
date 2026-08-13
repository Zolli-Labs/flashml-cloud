import { SectionReveal } from "@/components/landing/motion/SectionReveal";

const LANES = [
  {
    index: "01",
    title: "Host",
    body: "Every machine answers to flashnode. Shared machines run only allowlisted Docker images, sandboxed from the host.",
    contract: "--network none · read-only rootfs · cpu/mem caps",
    modules: [
      ["Enroll", "DEVICE_CODE · ACTIVATION"],
      ["Coordinate", "LEASE_CLAIMED"],
    ],
  },
  {
    index: "02",
    title: "Runtime",
    body: "Independent tasks lease across machines. Inside one machine, multi-GPU DDP and FSDP run as PyTorch intends.",
    contract: "independent tasks across machines · DDP / FSDP inside one",
    modules: [
      ["Execute", "LEASE_RENEWED"],
      ["Checkpoint", "CHECKPOINT_MANIFEST_COMMITTED"],
    ],
  },
  {
    index: "03",
    title: "Recovery",
    body: "A lost heartbeat expires the lease, the task requeues, and another machine resumes from the last verified checkpoint.",
    contract: "checkpoint manifests · heartbeat expiry · one accepted commit",
    modules: [
      ["Recover", "TASK_REQUEUED"],
      ["Verify", "TASK_COMMIT_ACCEPTED"],
    ],
  },
] as const;

export function SystemModules() {
  return (
    <section
      id="architecture"
      data-surface="dark"
      data-layout="runtime-lanes"
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
            A sandboxed host lane, a parallel runtime, and a checkpoint recovery
            path — three separate contracts, one accepted outcome.
          </p>
        </div>

        <SectionReveal
          className="mt-12 md:mt-16"
          lineClassName="h-px w-full bg-white/18"
          bottomLineClassName="h-px w-full bg-white/18"
        >
          <div className="grid gap-px bg-white/14 lg:grid-cols-3">
            {LANES.map(({ index, title, body, contract, modules }) => (
              <article key={title} className="min-w-0 bg-[#111416] p-6 sm:p-7">
                <div className="flex items-start justify-between gap-5">
                  <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-[var(--landing-orange)]">
                    {index} {title}
                  </p>
                  <span aria-hidden className="mt-1 h-px flex-1 bg-white/14" />
                </div>
                <p className="mt-8 max-w-[43ch] text-[15px] leading-relaxed text-white/62">{body}</p>
                <p className="mt-4 break-words font-mono text-[9px] leading-relaxed text-white/52">{contract}</p>
                <dl className="mt-8 border-t border-white/14">
                  {modules.map(([module, event]) => (
                    <div key={module} className="grid min-w-0 gap-2 border-b border-white/10 py-4 sm:items-baseline sm:gap-5">
                      <dt className="text-[15px] font-semibold text-white/92">{module}</dt>
                      <dd className="min-w-0 break-words font-mono text-[9px] leading-relaxed text-white/52">{event}</dd>
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
