import { describe, expect, it } from "vitest";

import {
  CAPABILITY_CLASS_ALIAS,
  REFERENCE_CLASSES,
  REFERENCE_META,
  referenceClass,
  referenceKlassFor,
  referenceSeries,
  referenceSpecLine,
  referenceZcPerHour,
} from "./reference";

describe("the seed file matches the shape this module asserts", () => {
  // `reference.ts` casts the JSON. The cast is only as good as this test:
  // a regenerated seed that drops `kind` or renames `refPriceUsdHr` would
  // otherwise typecheck and fail on the page.
  it("carries a generated stamp with a date", () => {
    expect(REFERENCE_META.generated).toBe(true);
    expect(REFERENCE_META.generatedAt).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(REFERENCE_META.basis.length).toBeGreaterThan(10);
  });

  it("gives every class a name, a kind, a price and a history", () => {
    expect(REFERENCE_CLASSES.length).toBeGreaterThan(0);
    for (const entry of REFERENCE_CLASSES) {
      expect(entry.klass.length, entry.klass).toBeGreaterThan(0);
      expect(entry.displayName.length, entry.klass).toBeGreaterThan(0);
      expect(["gpu", "cpu", "tpu"], entry.klass).toContain(entry.kind);
      expect(entry.refPriceUsdHr, entry.klass).toBeGreaterThan(0);
      expect(entry.history.length, entry.klass).toBeGreaterThan(1);
      expect(entry.sources.length, entry.klass).toBeGreaterThan(0);
    }
  });

  it("names each class once", () => {
    const names = REFERENCE_CLASSES.map((entry) => entry.klass);
    expect(new Set(names).size).toBe(names.length);
  });

  it("keeps every reference price inside its own observed band", () => {
    for (const entry of REFERENCE_CLASSES) {
      expect(entry.refPriceUsdHr, entry.klass).toBeGreaterThanOrEqual(
        entry.bandLowUsdHr
      );
      expect(entry.refPriceUsdHr, entry.klass).toBeLessThanOrEqual(
        entry.bandHighUsdHr
      );
    }
  });
});

describe("referenceClass", () => {
  it("matches a seed klass exactly", () => {
    expect(referenceClass("H100-80G")?.displayName).toContain("H100");
  });

  it("resolves the two definitional capability-class aliases", () => {
    expect(referenceClass("gpu-80gb-hopper")?.klass).toBe("H100-80G");
    expect(referenceClass("gpu-80gb")?.klass).toBe("A100-80G");
  });

  it("returns null rather than guessing at an ambiguous VRAM floor", () => {
    // Four different cards land on each of these, at prices that differ by
    // more than 3x. A plausible match here is a wrong number on a page that
    // looks authoritative.
    for (const klass of [
      "gpu-8gb",
      "gpu-16gb",
      "gpu-24gb",
      "gpu-48gb",
      "cpu-small",
      "cpu-large",
    ]) {
      expect(referenceClass(klass), klass).toBeNull();
    }
  });

  it("never matches on a prefix, a substring or a case fold", () => {
    expect(referenceClass("H100")).toBeNull();
    expect(referenceClass("h100-80g")).toBeNull();
    expect(referenceClass("H100-80G-SXM")).toBeNull();
    expect(referenceClass("")).toBeNull();
  });

  it("only aliases names the seed actually has", () => {
    const known = new Set(REFERENCE_CLASSES.map((entry) => entry.klass));
    for (const [from, to] of Object.entries(CAPABILITY_CLASS_ALIAS)) {
      expect(known.has(to), `${from} → ${to}`).toBe(true);
    }
  });
});

describe("referenceKlassFor", () => {
  it("answers the seed name a live rung already stands for", () => {
    expect(referenceKlassFor("gpu-80gb-hopper")).toBe("H100-80G");
    expect(referenceKlassFor("gpu-24gb")).toBeNull();
  });
});

describe("referenceZcPerHour", () => {
  it("converts USD to whole millicredits at 1 ZC = 1 USD", () => {
    const h100 = referenceClass("H100-80G");
    expect(h100).not.toBeNull();
    expect(referenceZcPerHour(h100!)).toBe(3290);
  });

  it("rounds rather than carrying a float's tail", () => {
    const t4 = referenceClass("T4-16G");
    expect(t4).not.toBeNull();
    // 0.295 * 1000 is not 295 in binary floating point.
    expect(referenceZcPerHour(t4!)).toBe(295);
    expect(Number.isInteger(referenceZcPerHour(t4!))).toBe(true);
  });
});

describe("referenceSpecLine", () => {
  it("leads with memory for anything that reports memory", () => {
    expect(referenceSpecLine(referenceClass("RTX-4090-24G")!)).toBe(
      "24 GB · gpu"
    );
  });

  it("leads with cores for the CPU lines", () => {
    expect(referenceSpecLine(referenceClass("CPU-64")!)).toBe("64 cores · cpu");
  });
});

describe("referenceSeries", () => {
  it("keeps the seed's own oldest-to-newest order", () => {
    const entry = referenceClass("H100-80G")!;
    const series = referenceSeries(entry);
    expect(series).toHaveLength(entry.history.length);
    expect(series[0].at).toBe(entry.history[0].date);
    expect(series[series.length - 1].valueMzc).toBe(referenceZcPerHour(entry));
  });
});
