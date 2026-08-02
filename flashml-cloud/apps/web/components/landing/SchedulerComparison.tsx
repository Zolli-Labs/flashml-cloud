import { Reveal } from "@/components/motion/Reveal";

// Adapted from a pattern seen on Webhound's landing page (via LandingFolio):
// two side-by-side transcripts, one showing the ordinary outcome and one
// showing theirs, with a mono caption underneath drawing the contrast.
//
// It is here because it closed a real hole rather than because it looked
// good. Every other section on this page shows what FlashML DOES; not one
// of them shows what happens without it, so the reader has nothing to
// measure the claim against and "leases, not assignments" lands as a
// detail instead of as a difference.
//
// Adapted, not copied: the reference is a serif-and-salmon page and this
// one is mono-and-indigo. What is inherited is the STRUCTURE — paired
// panels, one dimmed, mono micro-tags, a caption that names the contrast.

const WITHOUT = [
  { text: "task-03 claimed by node-7fb2", tone: "dim" },
  { text: "node-7fb2 goes quiet", tone: "dim" },
  { text: "no lease to expire", tone: "warn" },
  { text: "the run has no way to notice", tone: "warn" },
  { text: "job fails, or restarts from the top", tone: "warn" },
] as const;

const WITH = [
  { text: "LEASE_CLAIMED  node-7fb2  task-03", tone: "dim" },
  { text: "node-7fb2 goes quiet", tone: "dim" },
  { text: "LEASE_EXPIRED  deadline passed", tone: "warn" },
  { text: "TASK_REQUEUED  attempt 2/3", tone: "dim" },
  { text: "TASK_COMMIT_ACCEPTED  node-be40", tone: "good" },
] as const;

const TONE: Record<string, string> = {
  dim: "text-muted-foreground",
  warn: "text-[var(--warning)]",
  good: "text-[var(--node-green)]",
};

function Panel({
  title,
  tag,
  lines,
  muted = false,
}: {
  title: string;
  tag: string;
  lines: readonly { text: string; tone: string }[];
  muted?: boolean;
}) {
  return (
    <div className={muted ? "opacity-60" : undefined}>
      <div className="flex flex-wrap items-center gap-2.5">
        <span
          aria-hidden
          className="h-1.5 w-1.5 rounded-full"
          style={{
            background: muted
              ? "var(--muted-foreground)"
              : "var(--node-green)",
          }}
        />
        <h3 className="text-sm font-semibold">{title}</h3>
        <span className="meta rounded-sm border border-border px-1.5 py-0.5 uppercase tracking-[0.1em]">
          {tag}
        </span>
      </div>
      <ul className="panel mt-3 space-y-2 p-4">
        {lines.map((l) => (
          <li
            key={l.text}
            className={`font-mono text-[11px] leading-relaxed ${TONE[l.tone]}`}
          >
            {l.text}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function SchedulerComparison() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-20 sm:px-6 md:py-28">
      <Reveal className="max-w-2xl">
        <h2 className="title">The same machine dies in both.</h2>
        <p className="mt-4 text-base leading-relaxed text-muted-foreground">
          Nothing here is a fault-tolerance feature bolted onto a scheduler.
          The lease is what makes the second column possible at all.
        </p>
      </Reveal>

      <Reveal delay={0.08} className="mt-10 grid gap-8 md:grid-cols-2 md:gap-12">
        <Panel
          title="Without leases"
          tag="whole job restarts"
          lines={WITHOUT}
          muted
        />
        <Panel title="FlashML" tag="one task requeues" lines={WITH} />
      </Reveal>

      <p className="meta mt-8 text-center">
        {/* No invented durations. The difference is structural, and a
            fabricated "14 minutes saved" would be the one number on this
            page nobody could check. */}
        One loses the whole run. The other loses the work since the last
        verified checkpoint.
      </p>
    </section>
  );
}
