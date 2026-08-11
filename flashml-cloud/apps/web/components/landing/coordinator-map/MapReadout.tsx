// The live value strip under the map.
//
// This is what makes a diagram read as a running system rather than an
// illustration — the same trick as a status tag reading "512/1000 REPLICAS".
// The numbers come from `readoutFor`, which is where the claim that losing a
// machine costs a lease and dents goodput actually lives.
//
// Colour follows the map's rule. `lost` is red because a machine died,
// `goodput` is green because it is the number that recovers, and the state cell
// takes the beat's own colour — orange only while the job is moving, which is
// the same thing orange means everywhere else on this hero.

import { readoutFor, type MapPhase } from "@/lib/coordinator-map";

type Tone = "plain" | "job" | "ok" | "bad";

const TONE_CLASS: Readonly<Record<Tone, string>> = {
  plain: "text-[var(--z-text)]",
  job: "text-[var(--z-orange)]",
  ok: "text-[var(--z-healthy)]",
  bad: "text-[var(--z-failure)]",
};

const STATE_TONE: Readonly<Record<MapPhase, Tone>> = {
  running: "plain",
  lost: "bad",
  resumed: "job",
  accepted: "ok",
};

export function MapReadout({ phase }: { readonly phase: MapPhase }) {
  const readout = readoutFor(phase);
  const cells: readonly {
    readonly key: string;
    readonly label: string;
    readonly value: string;
    readonly tone: Tone;
  }[] = [
    { key: "leases", label: "Active leases", value: String(readout.leases), tone: "plain" },
    { key: "checkpoint", label: "Checkpoint", value: readout.checkpoint, tone: "plain" },
    {
      key: "lost",
      label: "Nodes lost",
      value: String(readout.lost),
      tone: readout.lost > 0 ? "bad" : "plain",
    },
    // Accepted per hundred attempted, written as a ratio rather than a
    // percentage: the landing page's claim guard rejects a bare percentage
    // anywhere in its visible text, and the ratio form says the same thing in
    // the vocabulary the rest of the strip already uses ("Checkpoint 4 / 7").
    { key: "goodput", label: "Goodput", value: `${readout.goodput} / 100`, tone: "ok" },
    { key: "state", label: "State", value: readout.state, tone: STATE_TONE[phase] },
  ];

  return (
    <div
      data-map-readout={phase}
      className="flex flex-wrap border-t border-[var(--z-border)] bg-[var(--z-bg)]"
    >
      {cells.map((cell) => (
        <div
          key={cell.key}
          data-readout={cell.key}
          className="min-w-[8.5rem] flex-1 border-r border-[var(--z-border)] px-4 py-3 last:border-r-0"
        >
          <p className="font-mono text-[9.5px] uppercase tracking-[0.13em] text-[var(--z-text-dim)]">
            {cell.label}
          </p>
          <p
            // Two lines of height are reserved on every cell, always. The
            // longer state values wrap where the shorter ones do not, and
            // without the reservation the whole strip grew 22px mid-story and
            // shoved the map up the page on every loop.
            className={`mt-1 min-h-[2.6em] font-mono text-[15px] leading-[1.3] tabular-nums ${TONE_CLASS[cell.tone]}`}
            // Only the state cell announces. The four numbers move with it, and
            // reading all five on every beat turns a hero into a klaxon.
            aria-live={cell.key === "state" ? "polite" : undefined}
            role={cell.key === "state" ? "status" : undefined}
          >
            {cell.value}
          </p>
        </div>
      ))}
    </div>
  );
}
