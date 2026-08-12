import { describe, expect, it } from "vitest";

import type { MarketAsk, MarketListing } from "./cloud-api";
import {
  askPriceLabel,
  groupBookByClass,
  listingStateLabel,
  recordLabel,
} from "./market-listings";

function ask(over: Partial<MarketAsk> = {}): MarketAsk {
  return {
    id: "listing-1",
    machine_id: "machine-1",
    host_id: "host-1",
    capability_class: "gpu-24gb",
    ask_zc_per_hour: 1000,
    donated: false,
    price_label: "1 ZC/hour",
    max_concurrent_tasks: 1,
    acceptance_rate: null,
    resolved_n: null,
    ...over,
  };
}

describe("groupBookByClass", () => {
  it("groups by class and preserves the API's rank order inside it", () => {
    const books = groupBookByClass([
      ask({ id: "a", ask_zc_per_hour: 900 }),
      ask({ id: "b", capability_class: "cpu-small" }),
      ask({ id: "c", ask_zc_per_hour: 1100 }),
    ]);
    expect(books.map((b) => b.capabilityClass)).toEqual([
      "gpu-24gb",
      "cpu-small",
    ]);
    expect(books[0].asks.map((a) => a.id)).toEqual(["a", "c"]);
  });

  it("an empty book is an empty list, not an error", () => {
    expect(groupBookByClass([])).toEqual([]);
  });
});

describe("recordLabel — null is unproven, never zero", () => {
  it("says unproven in words when there is no record", () => {
    expect(recordLabel(ask())).toContain("unproven");
    expect(recordLabel(ask())).not.toMatch(/0%/);
  });

  it("carries the count behind a real rate", () => {
    expect(
      recordLabel(ask({ acceptance_rate: 0.81, resolved_n: 34 }))
    ).toBe("81% accepted of 34 resolved in this class");
  });
});

describe("askPriceLabel", () => {
  it("calls a zero ask donated, the market's word for it", () => {
    expect(askPriceLabel(ask({ ask_zc_per_hour: 0, donated: true }))).toBe(
      "donated"
    );
  });

  it("formats a priced ask through the integer formatter", () => {
    expect(askPriceLabel(ask({ ask_zc_per_hour: 220 }))).toBe(
      "0.22 ZC/hour"
    );
  });
});

describe("listingStateLabel", () => {
  it("renders the API's state vocabulary with its consequence", () => {
    const listing = (state: string): MarketListing => ({
      id: "l",
      machine_id: "m",
      capability_class: "gpu-24gb",
      ask_zc_per_hour: 1000,
      max_concurrent_tasks: 1,
      state,
      donated: false,
      price_label: "1 ZC/hour",
      created_at: "2026-08-12T00:00:00Z",
    });
    expect(listingStateLabel(listing("open"))).toContain("open");
    expect(listingStateLabel(listing("paused"))).toContain("paused");
    expect(listingStateLabel(listing("weird"))).toContain('"weird"');
  });
});
