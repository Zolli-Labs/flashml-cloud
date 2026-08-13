"use client";

import { zcAmountText, type PriceHistoryData } from "@/lib/market/board";
import { REFERENCE_META } from "@/lib/market/reference";
import { SourceChip } from "./SourceChip";
import { Sparkline } from "./Sparkline";

/** One class's price over time, drawn from whichever series exists.
 *
 * INLINE SVG, NO CHART LIBRARY. The whole drawing is a polyline, an area
 * under it and three gridlines; a charting dependency would arrive with an
 * axis engine, a tooltip layer and a theme of its own, none of which this
 * page wants and all of which would have to be argued back out.
 *
 * OURS AND NOT-OURS DO NOT LOOK ALIKE, and that is the point. Our own
 * observations are a solid brand-coloured line with the last point marked.
 * Anything borrowed is a muted dashed line under a caption that names what
 * was borrowed — the seed's own band for a vendor SKU, and for a derived
 * class the donor card, by name, because that line is literally another
 * product's history drawn under this class's heading. Nothing renders a
 * series without saying which kind it is, and with no series at all the
 * component says "no history yet" over `Sparkline`'s dashed baseline rather
 * than drawing a flat line at some invented level.
 *
 * The plot is `preserveAspectRatio="none"` in a 0–100 box, so it stretches
 * to whatever width the column gives it; every stroke carries
 * `vector-effect="non-scaling-stroke"` so the stretch never thickens a line,
 * and both axes are HTML rather than `<text>` for the same reason. The
 * labels are positioned against the same inset box the plot fills, so no
 * arithmetic here assumes a font size.
 *
 * `compact` is a HEIGHT, not a lesser chart. The board's own cards stack
 * one per class and a 160px plot in each pushes the second class below the
 * fold; nothing else is dropped, because a chart with its axes taken off
 * is a shape, and the whole point of putting a real one on the front of
 * the page was that a host can read a number off it. */
export function PriceHistory({
  data,
  compact = false,
}: {
  data: PriceHistoryData;
  compact?: boolean;
}) {
  // `classHistory` never hands over a single point, and this is what keeps
  // that a decision rather than an assumption: one point has no span to
  // normalise against, and the drawing would be a line to nowhere.
  if (data === null || data.points.length < 2) {
    return (
      <div className="mt-3 flex items-center gap-3">
        <Sparkline points={null} />
        <p className="text-xs text-muted-foreground">no history yet</p>
      </div>
    );
  }

  const values = data.points.map((point) => point.valueMzc);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min;
  const step = 100 / (data.points.length - 1);
  // A flat series centres rather than dividing by zero — the same choice
  // `sparkPoints` makes, so the two drawings of one series agree.
  const coords = values.map((value, i) => ({
    x: i * step,
    y: span === 0 ? 50 : 100 - ((value - min) / span) * 100,
  }));
  const line = coords.map((c) => `${c.x},${c.y}`).join(" ");
  const area = `M 0,100 ${coords.map((c) => `L ${c.x},${c.y}`).join(" ")} L 100,100 Z`;
  const last = coords[coords.length - 1];
  const live = data.source === "live";
  const labels = xLabels(data.points.map((point) => point.at));

  return (
    <figure className="mt-3">
      <div className="grid grid-cols-[3.25rem_minmax(0,1fr)] gap-x-2 pr-2">
        {/* The value axis: three levels, top to bottom. A flat series
            repeats one number rather than inventing a range around it. */}
        <div className="relative">
          <div className="absolute inset-x-0 inset-y-2">
            {[max, span === 0 ? max : min + span / 2, min].map((value, i) => (
              <span
                key={i}
                className="meta absolute right-0 -translate-y-1/2 tabular-nums"
                style={{ top: `${i * 50}%` }}
              >
                {zcAmountText(Math.round(value))}
              </span>
            ))}
          </div>
        </div>

        <div
          className={`relative min-w-0 ${compact ? "h-[120px]" : "h-40"}`}
        >
          <div className="absolute inset-x-0 inset-y-2">
            <svg
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
              className="h-full w-full"
              role="img"
              aria-label={
                live
                  ? "observed best ask over time"
                  : data.source === "derived"
                    ? "reference card's price over time, standing in for this class"
                    : "reference price over time, generated from vendor bands"
              }
            >
              {[0, 50, 100].map((y) => (
                <line
                  key={y}
                  x1="0"
                  y1={y}
                  x2="100"
                  y2={y}
                  stroke="var(--border)"
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
              ))}
              <path
                d={area}
                fill={live ? "var(--z-orange)" : "currentColor"}
                className={
                  live ? "opacity-10" : "text-muted-foreground opacity-10"
                }
              />
              <polyline
                points={line}
                fill="none"
                stroke={live ? "var(--z-orange)" : "currentColor"}
                className={live ? undefined : "text-muted-foreground"}
                strokeWidth={2}
                strokeDasharray={live ? undefined : "5 4"}
                strokeLinecap="round"
                strokeLinejoin="round"
                vectorEffect="non-scaling-stroke"
              />
            </svg>
            {live && (
              <span
                aria-hidden
                className="absolute h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[var(--z-orange)]"
                style={{ left: "100%", top: `${last.y}%` }}
              />
            )}
          </div>
        </div>

        <div />
        <div className="mt-1 flex justify-between gap-2">
          {labels.map((label, i) => (
            <span key={i} className="meta tabular-nums">
              {label}
            </span>
          ))}
        </div>
      </div>

      {!live && (
        <figcaption className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
          <SourceChip
            source={
              data.source === "derived"
                ? { kind: "derived", note: data.note }
                : { kind: "reference" }
            }
          />
          {/* The donor sentence is the caption for a derived chart, not a
              tooltip on it: this line IS somebody else's card's history, and
              a reader who cannot see whose is reading an unlabelled chart. */}
          <span>
            {data.source === "derived"
              ? data.note
              : `generated ${REFERENCE_META.generatedAt} inside observed vendor bands`}
          </span>
        </figcaption>
      )}
    </figure>
  );
}

/** Three or four dates across the axis, taken from real points rather than
 * from a generated tick sequence — a label under a position where nothing
 * was observed is a label for a measurement that does not exist. */
function xLabels(stamps: string[]): string[] {
  const wanted = Math.min(4, stamps.length);
  const picked = new Set<number>();
  for (let i = 0; i < wanted; i += 1) {
    picked.add(Math.round((i * (stamps.length - 1)) / (wanted - 1 || 1)));
  }
  const sameDay = new Set(stamps.map((s) => s.slice(0, 10))).size === 1;
  return [...picked].sort((a, b) => a - b).map((i) => stamp(stamps[i], sameDay));
}

/** A point's date, or its clock time when the whole series is one day. An
 * unparseable stamp is quoted back, never rendered as "Invalid Date". */
function stamp(iso: string, sameDay: boolean): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return iso;
  const at = new Date(ms);
  return sameDay
    ? at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : at.toLocaleDateString([], { month: "short", day: "numeric" });
}
