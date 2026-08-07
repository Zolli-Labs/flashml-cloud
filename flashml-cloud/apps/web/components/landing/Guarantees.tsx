import {
  Fingerprint,
  SealCheck,
  Table,
  Ticket,
} from "@phosphor-icons/react/dist/ssr";
import { Reveal, RevealGroup, RevealItem } from "@/components/motion/Reveal";

// Copy is deliberately mechanism-first. The reason string in the fourth
// block is lifted from `flashruntime/recovery/policy.py`, punctuation
// adjusted only. Policy reasons written for engineers turn out to be better
// marketing copy than marketing copy.

const ITEMS = [
  {
    icon: Ticket,
    title: "Leases, not assignments",
    body: "A dead worker needs no handling. Its lease passes a deadline, the sweep expires it, and the task goes back in the queue.",
  },
  {
    icon: Fingerprint,
    title: "Commit keys",
    body: "Exactly one accepted result may ever exist per task. A duplicate arriving late is rejected, never counted twice.",
  },
  {
    icon: SealCheck,
    title: "Manifests, not paths",
    body: "No manifest means no checkpoint. Parts upload first, and the manifest is written only after every expected part hashes clean.",
  },
  {
    icon: Table,
    title: "A policy table, not an agent",
    body: "Same failure and same policy version always give the same action. One entry reads: deterministic application error, retrying burns money on a bug.",
  },
];

export function Guarantees() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-20 sm:px-6 md:py-28">
      <Reveal>
        <h2 className="max-w-2xl text-3xl font-semibold tracking-[-0.025em] md:text-4xl">
          Mechanisms, not adjectives.
        </h2>
      </Reveal>

      <RevealGroup
        className="mt-14 grid border-t border-border sm:grid-cols-2"
        stagger={0.08}
      >
        {ITEMS.map(({ icon: Icon, title, body }) => (
          <RevealItem
            key={title}
            className="group border-b border-border px-0 py-10 sm:px-8 sm:odd:pl-0 sm:even:border-l"
          >
            <Icon
              size={22}
              weight="duotone"
              className="text-brand-foreground transition-transform duration-500 ease-[cubic-bezier(0.16,1,0.3,1)] group-hover:-translate-y-0.5"
            />
            <h3 className="mt-5 text-lg font-semibold">{title}</h3>
            <p className="mt-2.5 max-w-[46ch] text-sm leading-relaxed text-muted-foreground">
              {body}
            </p>
          </RevealItem>
        ))}
      </RevealGroup>
    </section>
  );
}
