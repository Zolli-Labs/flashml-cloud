import { describe, expect, it } from "vitest";

import {
  CAPABILITY_CLASS_ALIAS,
  CPU_LARGE_MIN_CORES,
  CPU_TYPICAL_CORES,
  GPU_CLASS_FLOORS_MB,
  REFERENCE_CLASSES,
  REFERENCE_META,
  classDerivation,
  referenceClass,
  referenceKlassFor,
  referenceSeries,
  referenceSpecLine,
  referenceZcPerHour,
} from "./reference";

/** The coordinator's ladder, in the order `marketplace.py` builds it. Written
 * out rather than derived from the table under test, so a floor that moves in
 * the API has to move here too. */
const LADDER = [
  "cpu-small",
  "cpu-large",
  "gpu-8gb",
  "gpu-16gb",
  "gpu-24gb",
  "gpu-48gb",
  "gpu-80gb",
  "gpu-80gb-hopper",
] as const;

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

describe("the ladder this module derives against", () => {
  // The floors are a transcription of `GPU_CLASS_FLOORS_MB` in
  // `apps/api/flashml_cloud_api/marketplace.py`. A copy that drifts is worse
  // than no copy, so the numbers are asserted here rather than only used.
  it("carries the API's own MiB floors, richest first", () => {
    expect(GPU_CLASS_FLOORS_MB.map(([, name]) => name)).toEqual([
      "gpu-80gb",
      "gpu-48gb",
      "gpu-24gb",
      "gpu-16gb",
      "gpu-8gb",
    ]);
    expect(GPU_CLASS_FLOORS_MB.map(([mb]) => mb)).toEqual([
      79_000, 46_000, 23_000, 15_000, 7_800,
    ]);
  });

  it("splits the CPU classes where the API splits them", () => {
    expect(CPU_LARGE_MIN_CORES).toBe(8);
    expect(CPU_TYPICAL_CORES["cpu-large"]).toBe(CPU_LARGE_MIN_CORES);
    expect(CPU_TYPICAL_CORES["cpu-small"]).toBe(CPU_LARGE_MIN_CORES / 2);
  });
});

describe("classDerivation", () => {
  it("prices every rung on the ladder", () => {
    // The complaint this exists for: six of the eight rendered as a dash.
    for (const klass of LADDER) {
      const derived = classDerivation(klass);
      expect(derived, klass).not.toBeNull();
      expect(derived!.priceUsdHr, klass).toBeGreaterThan(0);
    }
  });

  it("maps a GPU floor to the cheapest card that clears it", () => {
    const table = LADDER.filter((k) => k.startsWith("gpu")).map((klass) => [
      klass,
      classDerivation(klass)!.donor.klass,
      classDerivation(klass)!.priceUsdHr,
    ]);
    expect(table).toEqual([
      // Nothing in the seed reaches down to 8 GB, so the cheapest card that
      // clears the floor is the cheapest card there is.
      ["gpu-8gb", "V100-16G", 0.19],
      ["gpu-16gb", "V100-16G", 0.19],
      ["gpu-24gb", "RTX-A5000-24G", 0.27],
      ["gpu-48gb", "A40-48G", 0.44],
      ["gpu-80gb", "A100-80G", 1.59],
      ["gpu-80gb-hopper", "H100-80G", 3.29],
    ]);
  });

  it("agrees with the two definitional aliases", () => {
    // The alias table is the class's own definition. A derivation that
    // disagreed with it would put a $1.59 A100 price on the Hopper rung,
    // which is the exact 2.5x error the ladder splits those two to avoid.
    for (const [klass, donor] of Object.entries(CAPABILITY_CLASS_ALIAS)) {
      expect(classDerivation(klass)!.donor.klass, klass).toBe(donor);
    }
  });

  it("reaches the same donor for gpu-80gb by definition and by floor", () => {
    // The one class where both paths answer: alias says A100-80G, and the
    // cheapest card clearing 79_000 MiB is also A100-80G. If they ever
    // disagree, one of the two is wrong and this says so.
    const cheapestAt80 = REFERENCE_CLASSES.filter(
      (entry) => entry.kind === "gpu" && (entry.vramGb ?? 0) * 1024 >= 79_000
    ).sort((a, b) => a.refPriceUsdHr - b.refPriceUsdHr)[0];
    expect(cheapestAt80.klass).toBe("A100-80G");
    expect(classDerivation("gpu-80gb")!.donor.klass).toBe(cheapestAt80.klass);
  });

  it("classes a card by its reported memory, not by the round number", () => {
    // A 20 GB RTX 4000 Ada clears 15_000 MiB and not 23_000, so it can be a
    // gpu-16gb donor and never a gpu-24gb one — the same side of the same
    // floor the coordinator would put the machine on.
    const donors = LADDER.map((klass) => classDerivation(klass)!.donor.klass);
    expect(donors).not.toContain("RTX-4000ADA-20G");
    expect((referenceClass("RTX-4000ADA-20G")!.vramGb ?? 0) * 1024).toBeLessThan(
      23_000
    );
  });

  it("scales the CPU classes off the cheapest core-hour in the seed", () => {
    // CPU-128 at $5.17/128 is $0.0404 a core — cheaper per core than the
    // 32- and 64-core lines, which is what a class priced in cores compares.
    const small = classDerivation("cpu-small")!;
    const large = classDerivation("cpu-large")!;
    expect(small.donor.klass).toBe("CPU-128");
    expect(large.donor.klass).toBe("CPU-128");
    expect(small.priceUsdHr).toBe(0.16);
    expect(large.priceUsdHr).toBe(0.32);
    // Twice the cores, twice the price: the only thing separating the two
    // rows is `CPU_TYPICAL_CORES`, which is stated rather than observed.
    expect(large.priceUsdHr / small.priceUsdHr).toBeCloseTo(2, 5);
  });

  it("never lets a Mac rental or a TPU chip-hour donate", () => {
    for (const klass of LADDER) {
      const donor = classDerivation(klass)!.donor;
      expect(donor.klass, klass).not.toBe("APPLE-SILICON-CI");
      expect(donor.kind, klass).not.toBe("tpu");
    }
  });

  it("names the donor in a sentence a chip can carry", () => {
    expect(classDerivation("gpu-24gb")!.note).toBe(
      "cheapest card meeting the 24 GB floor: NVIDIA RTX A5000"
    );
    expect(classDerivation("gpu-80gb-hopper")!.note).toBe(
      "this class names one card: NVIDIA H100 (SXM/PCIe/NVL)"
    );
    expect(classDerivation("cpu-large")!.note).toBe(
      "8 stated typical cores at the cheapest seed core-hour: High-core-count CPU instance (128 vCPU)"
    );
  });

  it("carries the donor's own series for a GPU class, digit for digit", () => {
    const derived = classDerivation("gpu-24gb")!;
    expect(derived.scale).toBe(1);
    expect(derived.history).toEqual(referenceSeries(derived.donor));
    expect(derived.history).toHaveLength(30);
    expect(derived.history[derived.history.length - 1].valueMzc).toBe(
      derived.priceMzc
    );
  });

  it("keeps a scaled CPU series ending on the price the row prints", () => {
    // The chart and the number above it are drawn from one derivation, so a
    // 128-vCPU instance's $5.17 can never appear under a $0.32 row.
    const derived = classDerivation("cpu-large")!;
    expect(derived.history).toHaveLength(derived.donor.history.length);
    expect(derived.history.map((p) => p.at)).toEqual(
      derived.donor.history.map((p) => p.date)
    );
    expect(derived.history[derived.history.length - 1].valueMzc).toBe(320);
    expect(derived.priceMzc).toBe(320);
  });

  it("has nothing to say about a rung the ladder never built", () => {
    for (const klass of ["gpu-40gb", "gpu-large", "npu-shiny", "", "H100-80G"]) {
      expect(classDerivation(klass), klass).toBeNull();
    }
  });
});
