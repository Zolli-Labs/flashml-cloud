import type { RowSource } from "@/lib/market/board";

/** Where a row's price came from, on the row itself.
 *
 * The board mixes our own book with a generated vendor snapshot, and the
 * only thing keeping the two apart on screen is this chip — so it is not
 * optional decoration and it takes the row's `source` rather than a
 * boolean somebody has to remember to pass correctly.
 *
 * A live chip carries its observation count, including zero: "LIVE · 0 obs"
 * is a real book nobody has quoted into yet, which is a different thing
 * from a reference price and reads as one. */
export function SourceChip({ source }: { source: RowSource }) {
  if (source.kind === "reference") {
    return (
      <span className="inline-flex shrink-0 items-center rounded-full border border-dashed border-border px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-muted-foreground">
        REFERENCE
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
