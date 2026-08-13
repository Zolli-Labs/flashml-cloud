import { SectionReveal } from "@/components/landing/motion/SectionReveal";
import { HOST_SUPPORT, RUNTIME_SUPPORT } from "@/lib/landing/platform";

// OS badges only — `HOST_SUPPORT` also carries "RunPod NVIDIA GPUs", which is
// a provider, not an operating system, and belongs in the one sentence below
// rather than beside Linux, Windows, and macOS.
const OS_SUPPORT = HOST_SUPPORT.filter(({ platform }) => platform !== "RunPod NVIDIA GPUs");

export function PlatformStrip() {
  return (
    <section
      id="platform"
      data-surface="sand"
      className="landing-surface-sand scroll-mt-20 border-b border-border py-20 md:py-28"
    >
      <div className="mx-auto max-w-[1240px] px-5 sm:px-6">
        <div className="grid gap-8 lg:grid-cols-[.72fr_1.28fr] lg:gap-12">
          <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-brand-foreground">
            Platform support
          </p>
          <h2 className="max-w-[820px] text-[clamp(2.65rem,5.4vw,4.75rem)] font-semibold leading-[0.99] tracking-[-0.052em]">
            Bring the machines <span className="text-muted-foreground">you already use.</span>
          </h2>
        </div>

        <SectionReveal className="mt-14 md:mt-20">
          <div className="border-t border-[var(--z-border-strong)] pt-8 md:pt-10">
            <div className="flex flex-wrap items-center gap-x-10 gap-y-6">
              <div role="list" aria-label="Supported operating systems" className="flex flex-wrap gap-2">
                {OS_SUPPORT.map(({ platform, state }) => (
                  <span
                    key={platform}
                    role="listitem"
                    data-os-badge={platform}
                    className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-3.5 py-2 text-sm font-medium"
                  >
                    {platform}
                    {state === "Preview" ? (
                      <span
                        data-os-badge-status
                        className="rounded-full border border-current px-1.5 py-0.5 font-mono text-[9px] font-medium uppercase tracking-[0.08em] text-[var(--z-warning)]"
                      >
                        Preview
                      </span>
                    ) : null}
                  </span>
                ))}
              </div>

              <div role="list" aria-label="Supported runtimes" className="flex flex-wrap gap-2">
                {RUNTIME_SUPPORT.map(({ icon, label }) => (
                  <span
                    key={label}
                    role="listitem"
                    data-runtime-chip={icon}
                    className="inline-flex items-center rounded-full border border-border px-3.5 py-2 text-sm font-medium text-muted-foreground"
                  >
                    {label}
                  </span>
                ))}
              </div>
            </div>

            <p className="mt-8 max-w-[68ch] text-sm leading-relaxed text-muted-foreground">
              macOS, Linux, and RunPod NVIDIA GPUs are verified; Windows 11 is in preview through Docker
              Desktop and WSL2.
            </p>
          </div>
        </SectionReveal>
      </div>
    </section>
  );
}
