import { ZolliCharacter } from "@/components/brand/ZolliCharacter";
import { Reveal, RevealGroup, RevealItem } from "@/components/motion/Reveal";
import type { ZolliRole } from "@/lib/zolli-brand";

const STEPS: Array<{
  role: ZolliRole;
  title: string;
  body: string;
}> = [
  {
    role: "captain",
    title: "Captain assigns a time-limited lease.",
    body: "A Zolli claims a task for a defined window and keeps the lease alive while it works.",
  },
  {
    role: "keeper",
    title: "Keeper saves verified progress.",
    body: "Checkpoint manifests are committed only after their parts have been verified.",
  },
  {
    role: "relay",
    title: "Relay hands interrupted work forward.",
    body: "If a heartbeat stops and the lease expires, the task returns to the queue for another Zolli.",
  },
  {
    role: "builder",
    title: "The Crew accepts only validated results.",
    body: "A task is complete only when the coordinator accepts its commit—not merely when a machine reports success.",
  },
];

export function CrewStory() {
  return (
    <section id="how-it-works" className="scroll-mt-24 border-y border-border bg-surface-2 py-20 md:py-28">
      <div className="mx-auto grid max-w-7xl gap-12 px-4 sm:px-6 lg:grid-cols-[0.8fr_1.2fr] lg:gap-20">
        <Reveal className="self-start lg:sticky lg:top-28">
          <p className="font-mono text-[11px] font-semibold uppercase tracking-[0.18em] text-brand-foreground">
            How it works
          </p>
          <h2 className="mt-4 max-w-xl font-display text-4xl font-semibold leading-[1.02] tracking-[-0.035em] md:text-6xl">
            Work moves forward, even when a Zolli cannot.
          </h2>
          <p className="mt-5 max-w-[52ch] leading-relaxed text-muted-foreground">
            The Crew relies on leases, verified checkpoints, requeueing, and validated commits—the mechanisms already built into the FlashML runtime.
          </p>
        </Reveal>

        <RevealGroup className="grid gap-4" stagger={0.08}>
          {STEPS.map((step, index) => (
            <RevealItem key={step.title}>
              <article className="grid gap-4 rounded-3xl border border-border bg-surface p-5 shadow-sm sm:grid-cols-[6rem_1fr] sm:items-center sm:p-7">
                <div className="flex items-center gap-3 sm:block">
                  <ZolliCharacter role={step.role} size={92} mood={step.role === "relay" ? "focused" : "happy"} />
                  <span className="font-mono text-xs font-semibold text-brand-foreground sm:mt-2 sm:block sm:text-center">
                    0{index + 1}
                  </span>
                </div>
                <div>
                  <h3 className="text-xl font-semibold tracking-[-0.02em] md:text-2xl">{step.title}</h3>
                  <p className="mt-2 max-w-[54ch] text-sm leading-relaxed text-muted-foreground md:text-base">{step.body}</p>
                </div>
              </article>
            </RevealItem>
          ))}
        </RevealGroup>
      </div>
    </section>
  );
}
