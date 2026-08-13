/** A drawing of the MECHANISM, one per template.
 *
 * Not icons. An icon says "this is a training job" in a picture instead of a
 * word, which is the same information twice — the tag chip already says it.
 * These say the thing the word cannot: that the checkpointed run picks up
 * where the dead machine left off, that a sweep is six trials that never
 * touch, that federated rounds converge and redistribute. A reader who has
 * never used the product can tell the five apart at a glance without reading
 * a line of copy, which is the only thing a gallery of five cards has to do.
 *
 * Inline SVG rather than an icon-library import, for `PageHeader`'s reason:
 * no client boundary, and everything is `currentColor` so the glyph adds no
 * colour of its own and inverts with the theme for free. The one accent —
 * `text-brand-foreground` on a nested group — marks the part of each drawing
 * that carries the point: the resumed segment, the winning trial, the
 * averaged model, the needle, the card.
 *
 * 64x40 user units, drawn at 40px tall. Hand-drawn simple on purpose: at
 * this size any more detail is a smudge.
 */

const BOX = "h-10 w-16 shrink-0";

export function TemplateGlyph({ id }: { id: string }) {
  switch (id) {
    case "train":
      return <TrainGlyph />;
    case "hpo":
      return <HpoGlyph />;
    case "federated":
      return <FederatedGlyph />;
    case "evaluate":
      return <EvaluateGlyph />;
    case "gpu-train":
      return <GpuTrainGlyph />;
    default:
      // A template with no drawing gets no drawing. Never a fallback shape:
      // a generic box beside four specific ones reads as a broken image.
      return null;
  }
}

/** Shared: 1.5 stroke, round joins, no fill. Every glyph below assumes it. */
function Frame({ children }: { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 64 40"
      className={BOX}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {children}
    </svg>
  );
}

/** A loss curve that is interrupted and resumes from its last checkpoint.
 *
 * The gap is the machine dying. The square on the curve just before it is
 * the last committed checkpoint, and the accent segment starts from that
 * height rather than from the top of the chart — which is the claim: no more
 * than one epoch is lost, and the run does not restart. */
function TrainGlyph() {
  return (
    <Frame>
      <path d="M3 34 L14 27 L25 21" opacity="0.55" />
      <rect x="12" y="25" width="4" height="4" opacity="0.55" />
      <rect x="23" y="19" width="4" height="4" opacity="0.55" />
      {/* The interruption. Dashed and short — it is a gap in the work, not a
          separate object in the drawing. */}
      <path d="M32 10 L32 32" strokeDasharray="2 3" opacity="0.4" />
      <g className="text-brand-foreground">
        <path d="M39 21 L50 14 L61 8" />
        <rect x="48" y="12" width="4" height="4" />
      </g>
    </Frame>
  );
}

/** One entrypoint, six independent trials, one of them ranked first.
 *
 * The lines never meet again on the right: that is the whole property this
 * shape has — the trials never have to reach each other, which is why a pull
 * fleet of ordinary machines is good at it. The filled dot is the winner the
 * reducer names. */
function HpoGlyph() {
  return (
    <Frame>
      <circle cx="7" cy="20" r="2.5" opacity="0.55" />
      <g opacity="0.4">
        <path d="M10 20 L48 5" />
        <path d="M10 20 L48 11" />
        <path d="M10 20 L48 17" />
        <path d="M10 20 L48 23" />
        <path d="M10 20 L48 29" />
        <path d="M10 20 L48 35" />
      </g>
      <g opacity="0.55">
        <circle cx="51" cy="5" r="2" />
        <circle cx="51" cy="11" r="2" />
        <circle cx="51" cy="23" r="2" />
        <circle cx="51" cy="29" r="2" />
        <circle cx="51" cy="35" r="2" />
      </g>
      <circle
        cx="51"
        cy="17"
        r="3"
        className="fill-current text-brand-foreground"
        stroke="none"
      />
    </Frame>
  );
}

/** Three machines, three different shards, one averaged model going back
 * out.
 *
 * Arrows in and an arrow out of the same ring, because that is the round:
 * deltas converge, the platform averages them, the next round's weights are
 * redistributed. Nothing connects the three machines to each other — task
 * containers run with `--network none` and there are no peers to reach. */
function FederatedGlyph() {
  return (
    <Frame>
      <g opacity="0.55">
        <rect x="3" y="4" width="9" height="6" rx="1" />
        <rect x="3" y="17" width="9" height="6" rx="1" />
        <rect x="3" y="30" width="9" height="6" rx="1" />
      </g>
      <g opacity="0.4">
        <path d="M14 7 L26 18" />
        <path d="M14 20 L26 20" />
        <path d="M14 33 L26 22" />
      </g>
      <g className="text-brand-foreground">
        <circle cx="32" cy="20" r="5.5" />
        <path d="M38 20 L57 20" />
        <path d="M53 16 L57 20 L53 24" />
      </g>
    </Frame>
  );
}

/** A gauge: this workload produces a number, not a model.
 *
 * The one shape here that reads as a measurement rather than as work moving
 * around, which is exactly the distinction the card is making. */
function EvaluateGlyph() {
  return (
    <Frame>
      <path d="M8 32 A20 20 0 0 1 56 32" opacity="0.55" />
      <g opacity="0.4">
        <path d="M8 32 L11 32" />
        <path d="M32 12 L32 15" />
        <path d="M53 32 L56 32" />
      </g>
      <g className="text-brand-foreground">
        <path d="M32 32 L45 21" />
        <circle cx="32" cy="32" r="2.5" className="fill-current" stroke="none" />
      </g>
    </Frame>
  );
}

/** A card with a bolt: the same run as Train, placed on hardware.
 *
 * The pins along the bottom edge are what make it read as a card rather than
 * as a box, and the bolt is inside the card rather than beside it because
 * the point is where the work lands, not that it is fast. */
function GpuTrainGlyph() {
  return (
    <Frame>
      <rect x="10" y="8" width="44" height="22" rx="2" opacity="0.55" />
      <g opacity="0.4">
        <path d="M18 30 L18 35" />
        <path d="M26 30 L26 35" />
        <path d="M34 30 L34 35" />
        <path d="M42 30 L42 35" />
      </g>
      <path
        d="M34 11 L26 21 L32 21 L30 27 L38 17 L32 17 Z"
        className="text-brand-foreground fill-current"
        stroke="none"
      />
    </Frame>
  );
}
