import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Sparkline } from "./Sparkline";

/**
 * THE DASHED BASELINE AND THE DASHED CURVE ARE DIFFERENT CLAIMS, and a
 * reader has only the stroke to tell them apart: one says "nothing was
 * recorded", the other says "this is a reference card's week". A flat
 * derived series would land on the same pixels as the baseline if the two
 * shared a dash pattern, which is what these assertions exist to prevent.
 */
const flat = [
  { x: 0, y: 50 },
  { x: 50, y: 50 },
  { x: 100, y: 50 },
];

describe("Sparkline", () => {
  it("draws a solid brand line for our own observations", () => {
    const html = renderToStaticMarkup(<Sparkline points={flat} />);
    expect(html).toContain("var(--z-orange)");
    expect(html).not.toContain("stroke-dasharray");
    expect(html).toContain("recent price observations");
  });

  it("draws a derived series as a muted dashed curve", () => {
    const html = renderToStaticMarkup(<Sparkline points={flat} dashed />);
    expect(html).toContain("<polyline");
    expect(html).toContain('stroke-dasharray="3 3"');
    expect(html).toContain("currentColor");
    expect(html).not.toContain("var(--z-orange)");
    expect(html).toContain("text-muted-foreground");
  });

  it("keeps the no-data baseline distinguishable from that curve", () => {
    const baseline = renderToStaticMarkup(<Sparkline points={null} />);
    const curve = renderToStaticMarkup(<Sparkline points={flat} dashed />);
    // A rule, not a series: one element, one pattern, and its own label.
    expect(baseline).toContain("<line");
    expect(baseline).not.toContain("<polyline");
    expect(baseline).toContain('stroke-dasharray="5 4"');
    expect(baseline).toContain("no price history");
    expect(curve).not.toContain('stroke-dasharray="5 4"');
    expect(curve).toContain("derived price history");
  });

  it("keeps the geometry the two modes share", () => {
    // Same box, same weight, same non-scaling stroke: dashing is the only
    // difference, so a derived curve reads as the same measurement drawn
    // with less authority rather than as a different chart.
    const solid = renderToStaticMarkup(<Sparkline points={flat} />);
    const dashed = renderToStaticMarkup(<Sparkline points={flat} dashed />);
    for (const html of [solid, dashed]) {
      expect(html).toContain('viewBox="-2 -6 104 112"');
      expect(html).toContain('points="0,50 50,50 100,50"');
      expect(html).toContain('stroke-width="2"');
      expect(html).toContain('vector-effect="non-scaling-stroke"');
    }
  });
});
