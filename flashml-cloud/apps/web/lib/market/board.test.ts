import { describe, expect, it } from "vitest";

import type { PricePoint, PricesView, ZcRung } from "../cloud-api";
import {
  boardRows,
  changeCell,
  classHistory,
  classRow,
  classSpecLine,
  hottestRows,
  tickerRows,
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

describe("tickerRows", () => {
  it("ranks by depth, then observations", () => {
    const rows = boardRows(
      view({
        zc: [
          rung({ capability_class: "gpu-16gb", depth: 1 }),
          rung({ capability_class: "gpu-48gb", depth: 9 }),
          rung({ capability_class: "gpu-80gb", depth: 1, history: [] }),
        ],
      })
    );
    const top = tickerRows(rows, 3).map((row) => row.klass);
    expect(top[0]).toBe("gpu-48gb");
    expect(top[1]).toBe("gpu-16gb");
    expect(top[2]).toBe("gpu-80gb");
  });

  it("puts a priced reference row above a rung with nothing on it", () => {
    // The quiet-market case: eight rungs exist from the first request and
    // none of them has a number yet.
    const rows = boardRows(
      view({
        zc: [
          rung({
            capability_class: "gpu-24gb",
            depth: 0,
            best_ask_zc: null,
            best_ask_usd: null,
            change_zc: null,
            history: [],
          }),
        ],
      })
    );
    expect(tickerRows(rows, 1)[0].source.kind).toBe("reference");
  });

  it("returns at most the limit", () => {
    expect(tickerRows(boardRows(view()), 4)).toHaveLength(4);
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
