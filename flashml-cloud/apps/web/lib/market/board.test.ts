import { describe, expect, it } from "vitest";

import type { PricePoint, PricesView, ZcRung } from "../cloud-api";
import {
  boardRows,
  changeCell,
  classHistory,
  classRow,
  classSpecLine,
  dayVerdict,
  derivedChangeCell,
  filterRows,
  historyStamp,
  hostClassGroups,
  hottestRows,
  kindChips,
  paginate,
  rowKind,
  rowVramGb,
} from "./board";
import { REFERENCE_CLASSES, referenceClass, referenceSeries } from "./reference";

function point(over: Partial<PricePoint> = {}): PricePoint {
  return {
    at: "2026-08-13T09:00:00Z",
    best_ask_zc: 1_000,
    best_ask_usd: "1.00",
    open_asks: 2,
    ...over,
  };
}

function rung(over: Partial<ZcRung> = {}): ZcRung {
  return {
    capability_class: "gpu-24gb",
    reference_zc_per_hour: 1_000,
    reference_usd_per_hour: "1.00",
    best_ask_zc: 900,
    best_ask_usd: "0.90",
    change_zc: -100,
    depth: 3,
    history: [point({ best_ask_zc: 900 }), point({ best_ask_zc: 1_000 })],
    ...over,
  };
}

/** The coordinator's own eight rungs, in ladder order — the rows this
 * console renders whether or not anything has traded in them. */
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

function view(over: Partial<PricesView> = {}): PricesView {
  return {
    quotes: [],
    unpriced: [],
    zc: [rung()],
    board: { open_asks_total: 3, live_classes: 1, observations_24h: 2 },
    ...over,
  };
}

describe("changeCell", () => {
  it("says why there is no delta instead of printing a zero", () => {
    expect(changeCell(null)).toEqual({
      direction: "none",
      text: "no history",
      reference: false,
    });
  });

  it("keeps a measured zero as a measured zero", () => {
    const cell = changeCell(0);
    expect(cell.direction).toBe("none");
    expect(cell.text).toBe("0 ZC");
  });

  it("marks direction the way a market does", () => {
    expect(changeCell(250)).toEqual({
      direction: "up",
      text: "▲ 0.25 ZC",
      reference: false,
    });
    expect(changeCell(-250)).toEqual({
      direction: "down",
      text: "▼ 0.25 ZC",
      reference: false,
    });
  });

  it("never marks a measured move as somebody else's", () => {
    // The flag is what keeps green and red for our own book. A cell built
    // from `change_zc` is always ours, whatever else is on the board.
    for (const change of [null, 0, 250, -250]) {
      expect(changeCell(change).reference).toBe(false);
    }
  });
});

describe("derivedChangeCell", () => {
  const day = (n: number, valueMzc: number) => ({
    at: `2026-08-${String(n).padStart(2, "0")}`,
    valueMzc,
  });

  it("reports a percentage, not a ZC amount nobody paid", () => {
    // A live cell says `▲ 0.25 ZC` — an amount somebody could have been
    // charged. A derived cell may only describe a shape.
    const cell = derivedChangeCell([day(12, 1_000), day(13, 1_034)]);
    expect(cell).toEqual({ direction: "up", text: "▲ 3.4%", reference: true });
    expect(derivedChangeCell([day(12, 1_000), day(13, 900)]).text).toBe(
      "▼ 10.0%"
    );
  });

  it("stays marked as somebody else's, in every direction", () => {
    expect(derivedChangeCell([day(12, 5), day(13, 5)])).toEqual({
      direction: "none",
      text: "unchanged",
      reference: true,
    });
    expect(derivedChangeCell([]).reference).toBe(true);
    expect(derivedChangeCell([day(13, 5)]).text).toBe("—");
  });

  it("states a direction without a percentage off a zero baseline", () => {
    const cell = derivedChangeCell([day(12, 0), day(13, 500)]);
    expect(cell).toEqual({ direction: "up", text: "▲", reference: true });
  });

  it("agrees with the verdict drawn under the same points", () => {
    // One reading behind both sentences: the card cannot say 3.4% while the
    // board row says 3.5%.
    const points = [day(12, 1_000), day(13, 1_034)];
    const cell = derivedChangeCell(points);
    const verdict = dayVerdict({ source: "derived", points, note: "n" });
    expect(verdict.text).toContain("3.4%");
    expect(cell.text).toContain("3.4%");
    expect(verdict.direction).toBe(cell.direction);
    expect(verdict.reference).toBe(cell.reference);
  });
});

describe("boardRows", () => {
  it("puts every rung first, then every seed class no rung stands for", () => {
    const rows = boardRows(view());
    expect(rows[0].klass).toBe("gpu-24gb");
    expect(rows[0].source).toEqual({ kind: "live", observations: 2 });
    expect(rows.length).toBe(1 + REFERENCE_CLASSES.length);
    expect(rows.slice(1).every((row) => row.source.kind === "reference")).toBe(
      true
    );
  });

  it("drops the seed row a rung already stands for", () => {
    const rows = boardRows(
      view({ zc: [rung({ capability_class: "gpu-80gb-hopper" })] })
    );
    expect(rows.filter((row) => row.klass === "H100-80G")).toHaveLength(0);
    expect(rows[0].displayName).toContain("H100");
  });

  it("renders an empty book as an em dash, never as a price of zero", () => {
    const rows = boardRows(
      view({ zc: [rung({ best_ask_zc: null, best_ask_usd: null })] })
    );
    expect(rows[0].lastAskText).toBe("—");
    expect(rows[0].equivalentText).toBeNull();
  });

  it("calls a zero ask what it is", () => {
    const rows = boardRows(view({ zc: [rung({ best_ask_zc: 0 })] }));
    expect(rows[0].lastAskText).toBe("donated");
  });

  it("gives a reference row no depth and no sparkline", () => {
    const reference = boardRows(view()).find(
      (row) => row.source.kind === "reference"
    );
    expect(reference?.depth).toBeNull();
    expect(reference?.spark).toBeNull();
    expect(reference?.change.text).toBe("—");
  });

  it("lines a price column up on the decimal point", () => {
    // `formatZc` trims the trailing zero, which puts 0.5 under 4.59 in a
    // right-aligned column and reads as a different order of magnitude.
    const row = boardRows(view()).find((r) => r.klass === "RTX-3090-24G");
    expect(row?.lastAskText).toBe("0.50 ZC/hr");
    expect(row?.equivalentText).toBe("$0.50/hr");
  });

  it("links a class name through its encoded ticker", () => {
    const rows = boardRows(view({ zc: [rung({ capability_class: "a/b" })] }));
    expect(rows[0].href).toBe("/market/prices/a%2Fb");
  });

  it("never leaves a row unsourced", () => {
    for (const row of boardRows(view())) {
      expect(["live", "reference", "derived", "empty"]).toContain(
        row.source.kind
      );
    }
  });
});

describe("a rung nobody has quoted into", () => {
  /** The shape the owner was looking at: the coordinator publishes the rung
   * because its ladder has it, and nothing has ever happened in it. */
  const untouched = (klass: string) =>
    rung({
      capability_class: klass,
      best_ask_zc: null,
      best_ask_usd: null,
      change_zc: null,
      depth: 0,
      history: [],
    });

  const rowFor = (klass: string) =>
    boardRows(view({ zc: [untouched(klass)] }))[0];

  it("never claims a live book behind nothing", () => {
    // `LIVE · 0 obs` on an empty book is a market badge on a market that has
    // not opened. Neither the derived rows below nor the unknown one carry
    // the live stamp at all.
    for (const klass of ["gpu-24gb", "cpu-small", "npu-shiny"]) {
      expect(rowFor(klass).source.kind, klass).not.toBe("live");
    }
  });

  it("keeps the live stamp for a rung with an ask but no observation", () => {
    // Zero observations is not zero market: somebody has an open ask, which
    // is our own book saying something.
    const row = boardRows(
      view({ zc: [rung({ history: [], depth: 2, best_ask_zc: 900 })] })
    )[0];
    expect(row.source).toEqual({ kind: "live", observations: 0 });
    expect(row.lastAskText).toBe("0.90 ZC/hr");
  });

  it("prices the eight rungs of the ladder from the seed", () => {
    const table = LADDER.map((klass) => {
      const row = rowFor(klass);
      return [klass, row.lastAskText];
    });
    expect(table).toEqual([
      ["cpu-small", "≈ 0.16 ZC/hr"],
      ["cpu-large", "≈ 0.32 ZC/hr"],
      ["gpu-8gb", "≈ 0.19 ZC/hr"],
      ["gpu-16gb", "≈ 0.19 ZC/hr"],
      ["gpu-24gb", "≈ 0.27 ZC/hr"],
      ["gpu-48gb", "≈ 0.44 ZC/hr"],
      ["gpu-80gb", "≈ 1.59 ZC/hr"],
      ["gpu-80gb-hopper", "≈ 3.29 ZC/hr"],
    ]);
  });

  it("marks every derived figure as an estimate, in both currencies", () => {
    const row = rowFor("gpu-24gb");
    expect(row.equivalentText).toBe("≈ $0.27/hr");
    // No ask, whatever the text says: the field a sort would read must not
    // hold an estimate.
    expect(row.lastAskMzc).toBeNull();
  });

  it("stamps the row with the sentence naming its donor", () => {
    expect(rowFor("gpu-48gb").source).toEqual({
      kind: "derived",
      note: "cheapest card meeting the 48 GB floor: NVIDIA A40",
    });
  });

  it("does not rename the class after the card it borrowed from", () => {
    // `gpu-24gb` is not an A5000 and must not be labelled one. The two
    // definitional classes keep the name the alias already gave them.
    expect(rowFor("gpu-24gb").displayName).toBeNull();
    expect(rowFor("gpu-80gb-hopper").displayName).toContain("H100");
  });

  it("moves in the reference marking, never in the market's colours", () => {
    const change = rowFor("gpu-80gb-hopper").change;
    expect(change.reference).toBe(true);
    // The H100's last daily step, as a shape rather than an amount.
    expect(change.text).toBe("▼ 2.9%");
    expect(change.direction).toBe("down");
  });

  it("draws the donor's last week as a dashed curve", () => {
    const row = rowFor("gpu-24gb");
    expect(row.sparkDashed).toBe(true);
    expect(row.spark).toHaveLength(7);
    // Normalised into the same 0–100 box a live sparkline uses.
    expect(row.spark?.[0].x).toBe(0);
    expect(row.spark?.[6].x).toBe(100);
    for (const point of row.spark ?? []) {
      expect(point.y).toBeGreaterThanOrEqual(0);
      expect(point.y).toBeLessThanOrEqual(100);
    }
  });

  it("keeps an empty book's real zero, which a seed row does not have", () => {
    // 0 open asks is a fact about our book; the seed rows have no book at
    // all and print an em dash.
    expect(rowFor("gpu-24gb").depth).toBe(0);
    expect(
      boardRows(view()).find((row) => row.source.kind === "reference")?.depth
    ).toBeNull();
  });

  it("says so plainly for a rung nothing in the seed describes", () => {
    const row = rowFor("npu-shiny");
    expect(row.source).toEqual({ kind: "empty" });
    expect(row.lastAskText).toBe("—");
    expect(row.equivalentText).toBeNull();
    expect(row.change.text).toBe("no history");
    expect(row.spark).toBeNull();
    expect(row.sparkDashed).toBe(false);
  });

  it("still leaves the donor's own row on the board", () => {
    // A borrowed price is not a claim on the vendor row: the A5000 is a
    // ticker of its own and stays one.
    const rows = boardRows(view({ zc: [untouched("gpu-24gb")] }));
    expect(rows.map((row) => row.klass)).toContain("RTX-A5000-24G");
  });

  it("keeps an estimate out of the hottest rail", () => {
    // The rail reads rungs, not rows, and an untouched rung answers none of
    // its three questions however priceable its class is.
    expect(hottestRows([untouched("gpu-24gb"), untouched("cpu-large")])).toEqual(
      []
    );
  });
});

describe("classRow", () => {
  it("prefers the live rung for a class that has one", () => {
    const row = classRow(view(), "gpu-24gb");
    expect(row?.source.kind).toBe("live");
    expect(row?.lastAskText).toBe("0.90 ZC/hr");
  });

  it("falls back to the seed for a class the coordinator has no rung for", () => {
    const row = classRow(view(), "RTX-4090-24G");
    expect(row?.source).toEqual({ kind: "reference" });
    expect(row?.displayName).toContain("4090");
  });

  it("answers for the seed even before the board has been read", () => {
    expect(classRow(null, "H100-80G")?.source.kind).toBe("reference");
  });

  it("derives a class the board has published no rung for", () => {
    const row = classRow(view(), "cpu-large");
    expect(row?.klass).toBe("cpu-large");
    expect(row?.source.kind).toBe("derived");
    // No rung means no book at all, which is not a book with nothing in it.
    expect(row?.depth).toBeNull();
  });

  it("answers a capability class under its own ticker, not the donor's", () => {
    // The seed would answer `gpu-80gb-hopper` with a row calling itself
    // `H100-80G` — a different ticker, with a different page behind it.
    const row = classRow(null, "gpu-80gb-hopper");
    expect(row?.klass).toBe("gpu-80gb-hopper");
    expect(row?.href).toBe("/market/prices/gpu-80gb-hopper");
    expect(row?.lastAskText).toBe("≈ 3.29 ZC/hr");
  });

  it("is null for a ticker no source knows", () => {
    expect(classRow(view(), "gpu-1tb")).toBeNull();
    expect(classRow(null, "")).toBeNull();
  });
});

describe("rowKind", () => {
  it("takes the seed's word when the seed knows the class", () => {
    expect(rowKind("CPU-64", referenceClass("CPU-64"))).toBe("cpu");
    expect(rowKind("TPU-V5E", referenceClass("TPU-V5E"))).toBe("tpu");
  });

  it("reads the ladder's own prefix when the seed does not", () => {
    expect(rowKind("gpu-24gb", null)).toBe("gpu");
    expect(rowKind("cpu-large", null)).toBe("cpu");
    expect(rowKind("gpu-80gb-hopper", null)).toBe("gpu");
  });

  it("calls an unrecognised class other rather than guessing a kind", () => {
    expect(rowKind("npu-shiny", null)).toBe("other");
    expect(rowKind("", null)).toBe("other");
  });
});

describe("rowVramGb", () => {
  it("parses the VRAM floor out of a gpu-NNgb class name", () => {
    expect(rowVramGb("gpu-24gb", null)).toBe(24);
    expect(rowVramGb("gpu-8gb", null)).toBe(8);
    // The qualifier after the floor is compute capability, not memory.
    expect(rowVramGb("gpu-80gb-hopper", null)).toBe(80);
  });

  it("prefers the seed's stated figure", () => {
    expect(rowVramGb("H100-80G", referenceClass("H100-80G"))).toBe(80);
  });

  it("is null, never zero, for a class that states no memory", () => {
    expect(rowVramGb("cpu-large", null)).toBeNull();
    expect(rowVramGb("CPU-64", referenceClass("CPU-64"))).toBeNull();
    expect(rowVramGb("gpu-large", null)).toBeNull();
  });
});

describe("filterRows", () => {
  const rows = boardRows(
    view({
      zc: [
        rung({ capability_class: "gpu-24gb" }),
        rung({ capability_class: "cpu-large" }),
      ],
    })
  );

  it("leaves the board alone when nothing is selected", () => {
    expect(filterRows(rows, { kind: "all", vram: "any" })).toHaveLength(
      rows.length
    );
  });

  it("keeps live rungs ahead of seed-only rows inside the filtered set", () => {
    const gpus = filterRows(rows, { kind: "gpu", vram: "any" });
    expect(gpus[0].klass).toBe("gpu-24gb");
    expect(gpus[0].source.kind).toBe("live");
    expect(gpus.slice(1).every((row) => row.source.kind === "reference")).toBe(
      true
    );
  });

  it("composes kind with VRAM rather than picking one", () => {
    const both = filterRows(rows, { kind: "gpu", vram: "80plus" });
    expect(both.length).toBeGreaterThan(0);
    expect(both.every((row) => row.kind === "gpu")).toBe(true);
    expect(both.every((row) => (row.vramGb ?? 0) >= 80)).toBe(true);
    expect(filterRows(rows, { kind: "cpu", vram: "80plus" })).toEqual([]);
  });

  it("bands memory at the edges the labels claim", () => {
    const klasses = (vram: "le16" | "24" | "32-48") =>
      filterRows(rows, { kind: "all", vram }).map((row) => row.klass);
    expect(klasses("le16")).toContain("T4-16G");
    expect(klasses("le16")).not.toContain("RTX-4000ADA-20G");
    expect(klasses("24")).toContain("RTX-4000ADA-20G");
    expect(klasses("24")).toContain("RTX-4090-24G");
    expect(klasses("32-48")).toContain("RTX-5090-32G");
    expect(klasses("32-48")).toContain("L40S-48G");
    expect(klasses("32-48")).not.toContain("A100-80G");
  });

  it("only lets a band speak about rows it can measure", () => {
    // A CPU class states no VRAM, so no band claims it — and Unknown is
    // where it goes, rather than being read as 0 GB.
    const unknown = filterRows(rows, { kind: "all", vram: "unknown" });
    expect(unknown.map((row) => row.klass)).toContain("cpu-large");
    expect(unknown.every((row) => row.vramGb === null)).toBe(true);
    expect(
      filterRows(rows, { kind: "all", vram: "le16" }).some(
        (row) => row.klass === "cpu-large"
      )
    ).toBe(false);
  });
});

describe("kindChips", () => {
  it("always offers the ladder's vocabulary, counted", () => {
    const chips = kindChips(boardRows(view()));
    expect(chips.map((chip) => chip.value)).toEqual([
      "all",
      "gpu",
      "cpu",
      "tpu",
    ]);
    expect(chips[0].count).toBe(boardRows(view()).length);
    expect(chips[3].count).toBeGreaterThan(0);
  });

  it("offers Other only when something is in it", () => {
    const withOther = kindChips(
      boardRows(view({ zc: [rung({ capability_class: "npu-shiny" })] }))
    );
    expect(withOther.map((chip) => chip.value)).toContain("other");
    expect(withOther[withOther.length - 1].count).toBe(1);
  });
});

describe("paginate", () => {
  const rows = boardRows(view());

  it("windows ten rows and says which ten", () => {
    const page = paginate(rows, 1);
    expect(page.rows).toHaveLength(10);
    expect(page.rangeText).toBe(`1–10 of ${rows.length}`);
  });

  it("clamps a page the row set no longer has", () => {
    // The filter-narrowed case: page 3 is showing and four rows are left.
    const page = paginate(rows.slice(0, 4), 3);
    expect(page.page).toBe(1);
    expect(page.pages).toBe(1);
    expect(page.rows).toHaveLength(4);
  });

  it("clamps below the first page too", () => {
    expect(paginate(rows, 0).page).toBe(1);
    expect(paginate(rows, -7).page).toBe(1);
    expect(paginate(rows, Number.NaN).page).toBe(1);
  });

  it("counts a short last page honestly", () => {
    const page = paginate(rows.slice(0, 12), 2);
    expect(page.rows).toHaveLength(2);
    expect(page.rangeText).toBe("11–12 of 12");
  });

  it("says nothing rather than 1–0 when there is nothing", () => {
    const page = paginate([], 1);
    expect(page.rangeText).toBe("0 of 0");
    expect(page.pages).toBe(1);
    expect(page.rows).toEqual([]);
  });
});

describe("dayVerdict", () => {
  const at = (day: string) => `2026-08-${day}T09:00:00Z`;

  it("says cheaper, with the move, off the points the chart drew", () => {
    const verdict = dayVerdict({
      source: "live",
      points: [
        { at: at("11"), valueMzc: 1_000 },
        { at: at("12"), valueMzc: 959 },
      ],
    });
    expect(verdict).toEqual({
      direction: "down",
      text: "cheaper than yesterday ▼ 4.1%",
      reference: false,
    });
  });

  it("says pricier the same way", () => {
    const verdict = dayVerdict({
      source: "live",
      points: [
        { at: at("11"), valueMzc: 1_000 },
        { at: at("12"), valueMzc: 1_023 },
      ],
    });
    expect(verdict.direction).toBe("up");
    expect(verdict.text).toBe("pricier than yesterday ▲ 2.3%");
  });

  it("does not claim a day passed between two readings on one day", () => {
    const verdict = dayVerdict({
      source: "live",
      points: [
        { at: "2026-08-13T09:00:00Z", valueMzc: 1_000 },
        { at: "2026-08-13T09:05:00Z", valueMzc: 900 },
      ],
    });
    expect(verdict.text).toBe("cheaper than the last reading ▼ 10.0%");
  });

  it("flags a movement read off the seed so it cannot pass for ours", () => {
    const verdict = dayVerdict({
      source: "reference",
      points: [
        { at: "2026-08-11", valueMzc: 1_000 },
        { at: "2026-08-12", valueMzc: 900 },
      ],
    });
    expect(verdict.reference).toBe(true);
  });

  it("keeps a measured standstill as a standstill", () => {
    const verdict = dayVerdict({
      source: "live",
      points: [
        { at: at("11"), valueMzc: 900 },
        { at: at("12"), valueMzc: 900 },
      ],
    });
    expect(verdict).toEqual({
      direction: "none",
      text: "unchanged today",
      reference: false,
    });
  });

  it("states the direction without a percentage off a donated baseline", () => {
    const verdict = dayVerdict({
      source: "live",
      points: [
        { at: at("11"), valueMzc: 0 },
        { at: at("12"), valueMzc: 500 },
      ],
    });
    expect(verdict.direction).toBe("up");
    expect(verdict.text).toBe("pricier than yesterday");
  });

  it("has nothing to say with fewer than two points", () => {
    expect(dayVerdict(null).text).toBe("no history yet");
    expect(
      dayVerdict({ source: "live", points: [{ at: at("12"), valueMzc: 1 }] })
        .text
    ).toBe("no history yet");
  });
});

describe("hostClassGroups", () => {
  const machines = [
    { id: "m1", label: "studio", klass: "gpu-24gb" },
    { id: "m2", label: "attic-rig", klass: "gpu-80gb-hopper" },
    { id: "m3", label: "spare", klass: "gpu-24gb" },
  ];

  it("groups the host's machines by class, first-seen order", () => {
    const groups = hostClassGroups(view(), machines);
    expect(groups.map((g) => g.klass)).toEqual(["gpu-24gb", "gpu-80gb-hopper"]);
    expect(groups[0].machines.map((m) => m.label)).toEqual([
      "studio",
      "spare",
    ]);
  });

  it("gives a class with a live book the live series and the live row", () => {
    const groups = hostClassGroups(view(), machines);
    expect(groups[0].row?.source.kind).toBe("live");
    expect(groups[0].history?.source).toBe("live");
    expect(groups[0].verdict.reference).toBe(false);
  });

  it("falls back to a borrowed series, flagged, for a class with no book", () => {
    const groups = hostClassGroups(view(), machines);
    const hopper = groups[1];
    expect(hopper.history?.source).toBe("derived");
    expect(hopper.verdict.reference).toBe(true);
    expect(hopper.displayName).toContain("H100");
  });

  it("gives a host in an untouched class a price, a verdict and a donor", () => {
    // The complaint this whole change answers, one card at a time: a
    // cpu-large machine used to show "—" and "no history yet".
    const groups = hostClassGroups(view(), [
      { id: "m1", label: "workstation", klass: "cpu-large" },
    ]);
    expect(groups[0].row?.lastAskText).toBe("≈ 0.32 ZC/hr");
    expect(groups[0].row?.source).toEqual({
      kind: "derived",
      note: expect.stringContaining("128 vCPU"),
    });
    expect(groups[0].history?.source).toBe("derived");
    expect(groups[0].verdict.text).not.toBe("no history yet");
    expect(groups[0].verdict.reference).toBe(true);
  });

  it("still makes a card for a class neither source knows", () => {
    const groups = hostClassGroups(view(), [
      { id: "m9", label: "mystery", klass: "npu-shiny" },
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].row).toBeNull();
    expect(groups[0].verdict.text).toBe("no history yet");
    expect(groups[0].href).toBe("/market/prices/npu-shiny");
  });

  it("is empty when nothing resolved, rather than inventing a class", () => {
    expect(hostClassGroups(view(), [])).toEqual([]);
  });
});

describe("hottestRows", () => {
  it("reads deepest, biggest mover and cheapest off the live book", () => {
    const rows = hottestRows([
      rung({ capability_class: "gpu-48gb", depth: 7, change_zc: 50 }),
      rung({
        capability_class: "gpu-80gb",
        depth: 2,
        change_zc: -400,
        best_ask_zc: 4_000,
      }),
      rung({ capability_class: "gpu-16gb", depth: 1, best_ask_zc: 120 }),
    ]);
    expect(rows.map((row) => row.klass)).toEqual([
      "gpu-48gb",
      "gpu-80gb",
      "gpu-16gb",
    ]);
    expect(rows[0].valueText).toBe("7 open asks");
    expect(rows[1].valueText).toBe("▼ 0.40 ZC");
    expect(rows[2].valueText).toBe("0.12 ZC/hr");
  });

  it("says nothing at all about a book with nothing in it", () => {
    expect(
      hottestRows([
        rung({
          depth: 0,
          best_ask_zc: null,
          best_ask_usd: null,
          change_zc: null,
        }),
      ])
    ).toEqual([]);
  });

  it("does not call an unchanged class a mover", () => {
    const rows = hottestRows([rung({ depth: 0, change_zc: 0 })]);
    expect(rows.map((row) => row.why)).not.toContain("biggest 24h move");
  });

  it("calls a zero best ask donated", () => {
    const rows = hottestRows([
      rung({ depth: 0, change_zc: null, best_ask_zc: 0 }),
    ]);
    expect(rows[0].valueText).toBe("donated");
  });
});

describe("classHistory", () => {
  it("prefers our own observations once there are two of them", () => {
    const data = classHistory(rung(), referenceClass("H100-80G"));
    expect(data?.source).toBe("live");
    expect(data?.points).toHaveLength(2);
  });

  it("orders live points oldest to newest and drops the askless ones", () => {
    const data = classHistory(
      rung({
        history: [
          point({ at: "2026-08-13T12:00:00Z", best_ask_zc: 300 }),
          point({ at: "2026-08-13T11:00:00Z", best_ask_zc: null }),
          point({ at: "2026-08-13T10:00:00Z", best_ask_zc: 100 }),
        ],
      }),
      null
    );
    expect(data?.points.map((p) => p.valueMzc)).toEqual([100, 300]);
  });

  it("falls back to the seed only when we have fewer than two of our own", () => {
    // A seed ticker's own page: no rung of ours, so the seed's series is
    // the only one there is.
    const data = classHistory(null, referenceClass("RTX-4090-24G"));
    expect(data?.source).toBe("reference");
    expect(data?.points.length).toBeGreaterThan(2);
  });

  it("borrows a donor series for a class with one observation", () => {
    // One point is not a chart. The class still has a derivation, and it is
    // stamped derived rather than passed off as the beginning of our own.
    const data = classHistory(rung({ history: [point()] }), null);
    expect(data?.source).toBe("derived");
    expect(data?.points).toHaveLength(30);
    expect(data?.source === "derived" && data.note).toContain("RTX A5000");
  });

  it("reads the class off the rung when the caller does not pass one", () => {
    // What lets `/market/prices/[klass]` pick a derivation up unchanged.
    expect(classHistory(rung({ history: [] }), null)?.source).toBe("derived");
    expect(classHistory(null, null, "cpu-large")?.source).toBe("derived");
  });

  it("prefers the derivation to the seed row for the two aliased classes", () => {
    // Both would answer with the H100's points; only one of them also says
    // whose points they are.
    const data = classHistory(
      rung({ capability_class: "gpu-80gb-hopper", history: [] }),
      referenceClass("gpu-80gb-hopper")
    );
    expect(data?.source).toBe("derived");
    expect(data?.points).toEqual(referenceSeries(referenceClass("H100-80G")!));
  });

  it("is null when no source has a series", () => {
    expect(
      classHistory(rung({ capability_class: "npu-shiny", history: [] }), null)
    ).toBeNull();
    expect(classHistory(null, null)).toBeNull();
  });
});

describe("historyStamp", () => {
  it("says nothing about our own series", () => {
    expect(historyStamp(null)).toBeNull();
    expect(historyStamp({ source: "live", points: [] })).toBeNull();
  });

  it("hands the derived chip its donor sentence", () => {
    expect(
      historyStamp({ source: "derived", points: [], note: "from a card" })
    ).toEqual({ kind: "derived", note: "from a card" });
    expect(historyStamp({ source: "reference", points: [] })).toEqual({
      kind: "reference",
    });
  });
});

describe("classSpecLine", () => {
  it("says nothing about hardware the seed does not know", () => {
    expect(classSpecLine(null)).toBeNull();
    expect(classSpecLine(referenceClass("A100-80G"))).toBe("80 GB · gpu");
  });
});
