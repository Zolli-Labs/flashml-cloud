"use client";

import type { DonutDatum } from "@/lib/network/providers";
import { Donut } from "./Donut";

/**
 * The capacity row: CPU, GPU, Memory — and Storage, if the API ever sends it.
 *
 * THE COLUMN COUNT IS THE DATA'S, NOT THE LAYOUT'S. Nothing in the fleet
 * reports per-machine disk today, so `NetworkTotals.storage_bytes` is absent
 * and `capacityDonuts()` produces three entries. The row is three columns
 * wide when it holds three donuts and four when it holds four — rather than a
 * fixed four with one slot blank, which leaves a quarter of the row empty and
 * reads as a chart that failed to load. That is the opposite of what a
 * measurement nobody takes should look like. The day storage arrives, the row
 * widens with no edit here.
 *
 * Both class strings are written out in full because Tailwind reads source
 * text: a `sm:grid-cols-${n}` built at runtime compiles to nothing.
 *
 * `caption` is what the ring MEANS, and it is required. The same three
 * drawings appear on the network page (committed vs. the network's whole) and
 * on a provider page (this machine vs. the network's whole), and those are
 * different sentences about identical pictures. A donut row without a caption
 * is a donut row a reader has to guess at.
 */
export function CapacityDonuts({
  donuts,
  caption,
  pctLabel,
  size,
}: {
  donuts: DonutDatum[];
  caption: string;
  /** Passed straight to every `Donut` — see its own note. */
  pctLabel?: string;
  size?: number;
}) {
  return (
    <section>
      <div
        className={`grid grid-cols-2 gap-x-4 gap-y-8 ${
          donuts.length >= 4 ? "sm:grid-cols-4" : "sm:grid-cols-3"
        }`}
      >
        {donuts.map((d) => (
          <Donut
            key={d.key}
            title={d.title}
            used={d.used}
            total={d.total}
            usedText={d.usedText}
            totalText={d.totalText}
            pctLabel={pctLabel}
            size={size}
          />
        ))}
      </div>

      {/* Sans, not `.meta`. This is a sentence a person reads, and the mono
          face is reserved in `app/globals.css` for text a machine emitted —
          ids, counts, states. A paragraph set in it also wraps at about
          two-thirds the width, which is how a one-line caption became
          three. */}
      <p className="mx-auto mt-6 max-w-2xl text-center text-xs leading-relaxed text-muted-foreground">
        {caption}
      </p>
    </section>
  );
}
