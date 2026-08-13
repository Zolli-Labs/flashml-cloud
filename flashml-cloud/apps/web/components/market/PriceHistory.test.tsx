import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { classHistory } from "@/lib/market/board";
import type { ZcRung } from "@/lib/cloud-api";
import { PriceHistory } from "./PriceHistory";

/**
 * THE CAPTION IS THE CONTRACT. A derived chart is one product's price
 * history drawn under another product's heading, and the only thing making
 * that honest rather than misleading is a line under it saying whose. These
 * assertions go through `classHistory` — the seam the class page and the
 * host cards both draw from — so a caption cannot be right in a test and
 * absent on the page.
 */
const untouched = (klass: string): ZcRung => ({
  capability_class: klass,
  reference_zc_per_hour: 1_000,
  reference_usd_per_hour: "1.00",
  best_ask_zc: null,
  best_ask_usd: null,
  change_zc: null,
  depth: 0,
  history: [],
});

const draw = (klass: string) =>
  renderToStaticMarkup(
    <PriceHistory data={classHistory(untouched(klass), null, klass)} />
  );

describe("PriceHistory", () => {
  it("names the donor under a derived chart", () => {
    const html = draw("gpu-24gb");
    expect(html).toContain("REFERENCE · derived");
    expect(html).toContain(
      "cheapest card meeting the 24 GB floor: NVIDIA RTX A5000"
    );
  });

  it("draws that chart in the borrowed styling, not ours", () => {
    const html = draw("cpu-large");
    expect(html).toContain("stroke-dasharray");
    expect(html).not.toContain("var(--z-orange)");
    expect(html).toContain("standing in for this class");
  });

  it("plots the donor's whole series rather than a summary of it", () => {
    // Thirty daily points, the same ones the derivation carries — the
    // sparkline's seven-point window is a board-cell decision and does not
    // reach the page-sized chart.
    const points = classHistory(untouched("gpu-48gb"), null, "gpu-48gb");
    expect(points?.points).toHaveLength(30);
    expect(draw("gpu-48gb")).toContain("NVIDIA A40");
  });

  it("says no history yet when nothing describes the class", () => {
    const html = renderToStaticMarkup(
      <PriceHistory data={classHistory(untouched("npu-shiny"), null)} />
    );
    expect(html).toContain("no history yet");
    expect(html).toContain("no price history");
  });
});
