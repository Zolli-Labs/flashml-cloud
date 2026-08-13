import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { boardRows, type BoardRow } from "@/lib/market/board";
import type { PricesView, ZcRung } from "@/lib/cloud-api";
import { SourceChip } from "./SourceChip";

/**
 * WHICH STAMP A ROW GETS, rendered rather than reasoned about.
 *
 * The chip is the only thing on the board separating our own book from a
 * vendor snapshot, so the interesting assertion is not that the component
 * branches — it is that the four board states each reach the branch they
 * should, through `boardRows`, from a rung shaped the way the API sends one.
 */
const markup = (row: BoardRow) =>
  renderToStaticMarkup(<SourceChip source={row.source} />);

function rung(over: Partial<ZcRung> = {}): ZcRung {
  return {
    capability_class: "gpu-24gb",
    reference_zc_per_hour: 1_000,
    reference_usd_per_hour: "1.00",
    best_ask_zc: null,
    best_ask_usd: null,
    change_zc: null,
    depth: 0,
    history: [],
    ...over,
  };
}

function firstRow(over: Partial<ZcRung> = {}): BoardRow {
  const view: PricesView = {
    quotes: [],
    unpriced: [],
    zc: [rung(over)],
    board: { open_asks_total: 0, live_classes: 0, observations_24h: 0 },
  };
  return boardRows(view)[0];
}

describe("SourceChip", () => {
  it("counts observations when there are observations", () => {
    const html = markup(
      firstRow({
        best_ask_zc: 900,
        best_ask_usd: "0.90",
        depth: 1,
        history: [
          { at: "2026-08-13T09:00:00Z", best_ask_zc: 900, best_ask_usd: "0.90", open_asks: 1 },
        ],
      })
    );
    expect(html).toContain("LIVE · 1 obs");
  });

  it("still says LIVE for an open ask nobody has quoted against yet", () => {
    // Zero observations with a real ask behind them is our own book being
    // quiet, not our own book being absent.
    expect(markup(firstRow({ depth: 3, best_ask_zc: 900 }))).toContain(
      "LIVE · 0 obs"
    );
  });

  it("never says LIVE · 0 obs over nothing at all", () => {
    // The defect: a market badge on a rung the ladder published and nobody
    // has touched.
    const html = markup(firstRow());
    expect(html).not.toContain("LIVE");
    expect(html).toContain("derived");
  });

  it("names the donor in the derived chip's title", () => {
    const html = markup(firstRow());
    expect(html).toContain("REFERENCE · derived");
    expect(html).toContain(
      "title=\"cheapest card meeting the 24 GB floor: NVIDIA RTX A5000\""
    );
  });

  it("keeps the plain REFERENCE stamp on a seed row", () => {
    const seed = boardRows({
      quotes: [],
      unpriced: [],
      zc: [],
      board: { open_asks_total: 0, live_classes: 0, observations_24h: 0 },
    })[0];
    const html = markup(seed);
    expect(html).toContain("REFERENCE");
    expect(html).not.toContain("derived");
  });

  it("puts no badge at all on a class nothing describes", () => {
    const html = markup(firstRow({ capability_class: "npu-shiny" }));
    expect(html).toContain("no data yet");
    expect(html).not.toContain("REFERENCE");
    expect(html).not.toContain("LIVE");
    // Not a pill: there is no source here to have earned one.
    expect(html).not.toContain("rounded-full");
  });
});
