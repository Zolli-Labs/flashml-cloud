import { CloudArrowUp, Desktop, Laptop } from "@phosphor-icons/react/dist/ssr";
import { Reveal, RevealGroup, RevealItem } from "@/components/motion/Reveal";
import { SpotlightCard } from "@/components/motion/SpotlightCard";

// Honesty constraint, from the supply-side note: GPU support does not exist
// yet (no probe, no --gpus, no CUDA image, placement reads no capabilities).
// Two of these three tiers therefore cannot run GPU work today, and the
// cards say so rather than implying a capability the runtime lacks.

type Status = "today" | "gpu-gated";

const STATUS_COPY: Record<Status, string> = {
  today: "Available today",
  "gpu-gated": "Waiting on GPU support",
};

const STATUS_STYLE: Record<Status, string> = {
  today: "text-[var(--node-green)] border-[var(--node-green)]/30 bg-[var(--node-green)]/10",
  "gpu-gated": "text-warning-foreground border-[var(--warning)]/30 bg-[var(--warning)]/10",
};

function StatusChip({ status }: { status: Status }) {
  return (
    <span
      className={`inline-flex items-center rounded-sm border px-2 py-[3px] font-mono text-[10px] ${STATUS_STYLE[status]}`}
    >
      {STATUS_COPY[status]}
    </span>
  );
}

function IconTile({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-10 w-10 items-center justify-center rounded-[10px] bg-primary/15 text-brand-foreground transition-colors duration-300 group-hover:bg-primary/25">
      {children}
    </div>
  );
}

export function SupplyTiers() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-20 sm:px-6 md:py-28">
      <Reveal className="max-w-2xl">
        <h2 className="text-3xl font-semibold tracking-[-0.025em] md:text-4xl">
          One interface. Three kinds of supply.
        </h2>
        <p className="mt-4 text-base leading-relaxed text-muted-foreground">
          The same runtime treats a rented pod, a spare 4090 and a spot
          instance as one thing: a machine that might vanish.
        </p>
      </Reveal>

      <RevealGroup className="mt-12 grid gap-4 lg:grid-cols-12">
        <RevealItem className="lg:col-span-7">
          <SpotlightCard className="h-full p-7">
            <IconTile>
              <CloudArrowUp size={20} weight="duotone" />
            </IconTile>
            <div className="mt-5 flex flex-wrap items-center gap-3">
              <h3 className="text-lg font-semibold">Rented pods</h3>
              <StatusChip status="today" />
            </div>
            <p className="mt-3 max-w-[52ch] text-sm leading-relaxed text-muted-foreground">
              Rent the box, install the agent, and it starts claiming work.
              Nobody to support, nobody to pay out to, and the preemption you
              already accepted for the discount stops mattering.
            </p>
          </SpotlightCard>
        </RevealItem>

        <RevealItem className="lg:col-span-5">
          <SpotlightCard className="h-full p-7">
            <IconTile>
              <Desktop size={20} weight="duotone" />
            </IconTile>
            <div className="mt-5 flex flex-wrap items-center gap-3">
              <h3 className="text-lg font-semibold">Home rigs</h3>
              <StatusChip status="gpu-gated" />
            </div>
            <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
              A 4090 in a spare room already has drivers and Docker. It needs
              its GPU detected and matched to GPU work, not an installer.
            </p>
          </SpotlightCard>
        </RevealItem>

        <RevealItem className="lg:col-span-12">
          <SpotlightCard className="p-7">
            <div className="flex flex-col gap-5 md:flex-row md:items-center md:gap-8">
              <IconTile>
                <Laptop size={20} weight="duotone" />
              </IconTile>
              <div className="flex flex-wrap items-center gap-3 md:w-56 md:shrink-0">
                <h3 className="text-lg font-semibold">Spare machines</h3>
                <StatusChip status="today" />
              </div>
              <p className="max-w-[70ch] text-sm leading-relaxed text-muted-foreground">
                Laptops and idle desktops. The cheapest tier to add and the
                honest one to be careful about: a laptop CPU contributes very
                little to real training, and it is the tier that generates the
                most support. Supported, not recommended.
              </p>
            </div>
          </SpotlightCard>
        </RevealItem>
      </RevealGroup>
    </section>
  );
}
