import type { RowSource } from "@/lib/market/board";

/** Where a row's price came from, on the row itself.
 *
 * The board mixes our own book with a generated vendor snapshot, and the
 * only thing keeping the two apart on screen is this chip — so it is not
 * optional decoration and it takes the row's `source` rather than a
 * boolean somebody has to remember to pass correctly.
 *
 * FOUR ANSWERS, AND `LIVE · 0 obs` IS NO LONGER ONE OF THEM ON ITS OWN. That
 * chip used to sit on rungs the coordinator publishes because its ladder has
 * them, beside a dash and the words "no history" — a market badge on a market
 * that had not opened. `lib/market/board.ts` now stamps such a rung `derived`
 * where the seed can price it and `empty` where it cannot, and this renders
 * the live chip only for a rung with an observation or an open ask behind it.
 *
 * The derived chip stays inside the REFERENCE family, muted and dashed, and
 * carries its donor sentence as a title: the word "derived" is the warning,
 * and the sentence is what makes the warning checkable. */
export function SourceChip({ source }: { source: RowSource }) {
  if (source.kind === "empty") {
    // Deliberately not a chip. There is no source to stamp, and a pill
    // saying so would still read as a badge earned by something.
    return (
      <span className="font-mono text-[10px] tracking-wide text-muted-foreground">
        no data yet
      </span>
    );
  }

  if (source.kind === "reference" || source.kind === "derived") {
    return (
      <span
        title={source.kind === "derived" ? source.note : undefined}
        className="inline-flex shrink-0 items-center rounded-full border border-dashed border-border px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-muted-foreground"
      >
        {source.kind === "derived" ? "REFERENCE · derived" : "REFERENCE"}
      </span>
    );
  }

  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full border px-1.5 py-0.5 font-mono text-[10px] tracking-wide ${
        source.observations > 0
          ? "border-evergreen/40 text-evergreen"
          : "border-border text-muted-foreground"
      }`}
    >
      LIVE · {source.observations} obs
    </span>
  );
}
