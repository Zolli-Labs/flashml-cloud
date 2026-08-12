import { describe, expect, it } from "vitest";

import type { PriceQuote, PriceUnpriced, ZcRung } from "./cloud-api";
import { ageLabel, quoteRow, unpricedRow, zcRungRow } from "./market-prices";

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
  it("names an empty book as no live ask, not zero", () => {
    const rung: ZcRung = {
      capability_class: "gpu-80gb-hopper",
      reference_zc_per_hour: 5000,
      best_ask_zc: null,
    };
    const row = zcRungRow(rung);
    expect(row.referenceText).toBe("5 ZC/hour reference");
    expect(row.bestAskText).toBe("no live ask");
  });

  it("names a zero best ask as donated", () => {
    const row = zcRungRow({
      capability_class: "cpu-small",
      reference_zc_per_hour: 100,
      best_ask_zc: 0,
    });
    expect(row.bestAskText).toBe("donated");
  });
});
