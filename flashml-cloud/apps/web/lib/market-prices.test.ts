import { describe, expect, it } from "vitest";

import type { PricePoint, PriceQuote, PriceUnpriced, ZcRung } from "./cloud-api";
import {
  ageLabel,
  boardStrip,
  deltaCell,
  quoteRow,
  sparkPoints,
  unpricedRow,
  zcRungRow,
} from "./market-prices";

function quote(over: Partial<PriceQuote> = {}): PriceQuote {
  return {
    provider: "runpod",
    sku: "NVIDIA RTX 4090",
    region: "global",
    tier: "community",
    currency: "USD",
    amount: "0.34",
    unit: "gpu-hour",
    attrs: {},
    captured_at: "2026-08-12T03:55:00Z",
    age_seconds: 7200,
    stale: false,
    max_age_seconds: 86_400,
    source: "runpod REST v2 GPU catalogue",
    observed_by: null,
    ...over,
  };
}

describe("ageLabel", () => {
  it("floors at each unit and never claims false precision", () => {
    expect(ageLabel(30)).toBe("30s ago");
    expect(ageLabel(119)).toBe("1m ago");
    expect(ageLabel(7200)).toBe("2h ago");
    expect(ageLabel(3 * 86_400)).toBe("3d ago");
  });

  it("quotes a negative age as-is rather than clamping it", () => {
    expect(ageLabel(-5)).toContain("future");
  });
});

describe("quoteRow", () => {
  it("keeps the vendor's digits as a string, denomination visible", () => {
    const row = quoteRow(quote());
    expect(row.amountText).toBe("USD 0.34 / gpu-hour");
    expect(row.venue).toBe("runpod — NVIDIA RTX 4090");
    expect(row.detail).toBe("global · community");
  });

  it("puts the staleness verdict up front when stale", () => {
    const row = quoteRow(quote({ stale: true }));
    expect(row.capturedText.startsWith("stale")).toBe(true);
    expect(row.capturedText).toContain("2h ago");
  });

  it("a fresh quote says observed-ago and never 'live'", () => {
    const row = quoteRow(quote());
    expect(row.capturedText).toBe("observed 2h ago");
    expect(row.capturedText).not.toContain("live");
  });
});

describe("unpricedRow — a venue with no quote is still a row", () => {
  it("renders not observed, never 0", () => {
    const venue: PriceUnpriced = {
      provider: "fc-gpu",
      state: "not observed",
      amount: null,
      currency: null,
      unit: null,
      captured_at: null,
      age_seconds: null,
      stale: null,
      source: null,
      observed_by: null,
    };
    const row = unpricedRow(venue);
    expect(row.amountText).toBe("not observed");
    expect(row.amountText).not.toContain("0");
  });
});

describe("zcRungRow — the ZC side, never converted", () => {
  const rung = (over: Partial<ZcRung>): ZcRung => ({
    capability_class: "gpu-24gb",
    reference_zc_per_hour: 1000,
    best_ask_zc: null,
    change_zc: null,
    depth: 0,
    history: [],
    ...over,
  });

  it("names an empty book as no live ask, not zero", () => {
    const row = zcRungRow(
      rung({ capability_class: "gpu-80gb-hopper", reference_zc_per_hour: 5000 })
    );
    expect(row.referenceText).toBe("5 ZC/hour reference");
    expect(row.bestAskText).toBe("no live ask");
  });

  it("names a zero best ask as donated", () => {
    const row = zcRungRow(
      rung({ capability_class: "cpu-small", reference_zc_per_hour: 100, best_ask_zc: 0 })
    );
    expect(row.bestAskText).toBe("donated");
  });
});

describe("the compute board", () => {
  const point = (best: number | null, day: number): PricePoint => ({
    at: `2026-08-0${day}T00:00:00Z`,
    best_ask_zc: best,
    open_asks: 1,
  });

  it("delta is directional, and null change is 'no history' not 0", () => {
    expect(deltaCell(null)).toEqual({ direction: "none", text: "—" });
    expect(deltaCell(100).direction).toBe("up");
    expect(deltaCell(-100).direction).toBe("down");
    expect(deltaCell(100).text).toContain("▲");
  });

  it("a sparkline needs two real points; flat series centres, nulls drop", () => {
    expect(sparkPoints([point(null, 1)])).toBeNull();
    expect(
      sparkPoints([point(5, 1), point(5, 2)])!.every((p) => p.y === 50)
    ).toBe(true);
    // Input is newest-first, like the API; the oldest low sits at the bottom.
    const rising = sparkPoints([point(10, 3), point(null, 2), point(0, 1)]);
    expect(rising).toHaveLength(2);
    expect(rising![0].y).toBe(100);
    expect(rising![1].y).toBe(0);
  });

  it("the header strip is three real counts", () => {
    expect(
      boardStrip({ open_asks_total: 3, live_classes: 2, observations_24h: 5 })
    ).toEqual([
      { label: "open asks", value: "3" },
      { label: "classes with a live book", value: "2" },
      { label: "observations, 24h", value: "5" },
    ]);
  });
});
