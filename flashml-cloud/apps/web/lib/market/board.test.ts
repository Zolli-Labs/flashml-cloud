import { describe, expect, it } from "vitest";

import type { PricePoint, PricesView, ZcRung } from "../cloud-api";
import {
  boardRows,
  changeCell,
  classHistory,
  classRow,
  classSpecLine,
  dayVerdict,
  filterRows,
  hostClassGroups,
  hottestRows,
  kindChips,
  paginate,
  rowKind,
  rowVramGb,
} from "./board";
import { REFERENCE_CLASSES, referenceClass } from "./reference";

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
    expect(changeCell(null)).toEqual({ direction: "none", text: "no history" });
  });

  it("keeps a measured zero as a measured zero", () => {
    const cell = changeCell(0);
    expect(cell.direction).toBe("none");
    expect(cell.text).toBe("0 ZC");
  });

  it("marks direction the way a market does", () => {
    expect(changeCell(250)).toEqual({ direction: "up", text: "▲ 0.25 ZC" });
    expect(changeCell(-250)).toEqual({ direction: "down", text: "▼ 0.25 ZC" });
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
      expect(["live", "reference"]).toContain(row.source.kind);
    }
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

  it("is null for a ticker neither source knows", () => {
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

  it("falls back to the seed series, flagged, for a class with no book", () => {
    const groups = hostClassGroups(view(), machines);
    const hopper = groups[1];
    expect(hopper.history?.source).toBe("reference");
    expect(hopper.verdict.reference).toBe(true);
    expect(hopper.displayName).toContain("H100");
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
    const data = classHistory(
      rung({ history: [point()] }),
      referenceClass("H100-80G")
    );
    expect(data?.source).toBe("reference");
    expect(data?.points.length).toBeGreaterThan(2);
  });

  it("is null when neither source has a series", () => {
    expect(classHistory(rung({ history: [] }), null)).toBeNull();
    expect(classHistory(null, null)).toBeNull();
  });
});

describe("classSpecLine", () => {
  it("says nothing about hardware the seed does not know", () => {
    expect(classSpecLine(null)).toBeNull();
    expect(classSpecLine(referenceClass("A100-80G"))).toBe("80 GB · gpu");
  });
});
