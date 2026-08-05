import { ZolliCharacter } from "@/components/brand/ZolliCharacter";
import { Reveal, RevealGroup, RevealItem } from "@/components/motion/Reveal";
import { ZOLLI_ROLES, type ZolliRole } from "@/lib/zolli-brand";

const ROLES = Object.entries(ZOLLI_ROLES) as Array<
  [ZolliRole, (typeof ZOLLI_ROLES)[ZolliRole]]
>;

export function CrewRoles() {
  return (
    <section id="crew" className="scroll-mt-24 py-20 md:py-28">
      <div className="mx-auto max-w-7xl px-4 sm:px-6">
        <Reveal className="max-w-3xl">
          <p className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-brand-foreground">
            Meet the crew
          </p>
          <h2 className="mt-4 font-display text-4xl font-semibold leading-[1.02] tracking-[-0.035em] md:text-6xl">
            Six roles. One resilient Crew.
          </h2>
          <p className="mt-5 max-w-[58ch] leading-relaxed text-muted-foreground">
            Each character explains a real part of the platform—from coordinating leases to producing training and inference outputs.
          </p>
        </Reveal>

        <RevealGroup className="mt-12 grid gap-4 sm:grid-cols-2 lg:grid-cols-3" stagger={0.06}>
          {ROLES.map(([role, definition]) => (
            <RevealItem key={role}>
              <article className="group h-full rounded-3xl border border-border bg-surface p-6 shadow-sm transition-transform duration-300 motion-safe:hover:-translate-y-1">
                <div className="flex items-start justify-between gap-4">
                  <ZolliCharacter
                    role={role}
                    size={108}
                    mood={role === "scout" ? "waving" : role === "worker" ? "focused" : "happy"}
                  />
                  <span className="rounded-full border border-border bg-surface-2 px-3 py-1 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                    {definition.subtitle}
                  </span>
                </div>
                <h3 className="mt-4 text-2xl font-semibold tracking-[-0.025em]">{definition.label}</h3>
                <p className="mt-2 max-w-[42ch] text-sm leading-relaxed text-muted-foreground">{definition.description}</p>
              </article>
            </RevealItem>
          ))}
        </RevealGroup>
      </div>
    </section>
  );
}
