import { describe, expect, it } from "vitest";

import {
  applyCreditQueueLoad,
  creditRequestSummary,
  nextCreditQueueGeneration,
  parseZcInput,
  restoreCreditRequest,
  totalRequestedZc,
  usdForMillicredits,
} from "./credit-requests";

describe("parseZcInput", () => {
  it("parses a positive ZC input with up to three decimals into millicredits", () => {
    expect(parseZcInput("10.125")).toBe(10_125);
    expect(parseZcInput("10")).toBe(10_000);
    expect(parseZcInput("0.001")).toBe(1);
  });

  it("refuses zero, negative, and over-precise inputs", () => {
    expect(parseZcInput("0")).toBeNull();
    expect(parseZcInput("-1")).toBeNull();
    expect(parseZcInput("1.0001")).toBeNull();
  });
});

describe("usdForMillicredits", () => {
  it("formats the USD preview from an integer millicredit amount", () => {
    expect(usdForMillicredits(10_125)).toBe("$10.13");
  });
});

describe("creditRequestSummary", () => {
  it("distinguishes the approved amount from the original request", () => {
    expect(
      creditRequestSummary({ requested_zc: 50_000, approved_zc: 25_000 })
    ).toContain("25 ZC approved from 50 ZC requested");
  });
});

describe("totalRequestedZc", () => {
  it("sums requested_zc across the visible queue", () => {
    expect(
      totalRequestedZc([{ requested_zc: 12_000 }, { requested_zc: 3_500 }])
    ).toBe(15_500);
  });

  it("is a real 0 for an empty queue, not a claim about an unread one", () => {
    expect(totalRequestedZc([])).toBe(0);
  });
});

describe("restoreCreditRequest", () => {
  it("restores a removed queue row once in queue request order", () => {
    const newest = { id: "newest", requested_at: "2026-08-03T00:00:00Z" };
    const oldest = { id: "oldest", requested_at: "2026-08-01T00:00:00Z" };
    const restored = restoreCreditRequest([oldest], newest);

    expect(restored).toEqual([oldest, newest]);
    expect(restoreCreditRequest(restored, newest)).toEqual([oldest, newest]);
  });
});

describe("applyCreditQueueLoad", () => {
  const request = { id: "r1", requested_at: "2026-08-03T00:00:00Z" };

  it("discards a list response that began before a credit decision settled", () => {
    const initialGeneration = 0;
    const staleLoadGeneration = nextCreditQueueGeneration(initialGeneration);
    const decisionStartedGeneration = nextCreditQueueGeneration(
      staleLoadGeneration
    );
    const decisionSettledGeneration = nextCreditQueueGeneration(
      decisionStartedGeneration
    );

    expect(
      applyCreditQueueLoad(
        [request],
        staleLoadGeneration,
        decisionSettledGeneration,
        new Set([request.id])
      )
    ).toBeNull();
  });

  it("filters a decided request from a current resync response after a 404 race", () => {
    const generation = nextCreditQueueGeneration(0);

    expect(
      applyCreditQueueLoad([request], generation, generation, new Set([request.id]))
    ).toEqual([]);
  });
});
