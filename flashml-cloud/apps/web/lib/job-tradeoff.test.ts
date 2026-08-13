import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TradeoffCard } from "@/components/jobs/TradeoffCard";

import {
  ADVICE_COPY,
  NOT_OBSERVED,
  RENTING_COPY,
  adviceCopy,
  basisLabel,
  summariseTradeoff,
  type AdviceCode,
  type TradeoffRead,
} from "./job-tradeoff";
import type {
  JobTradeoff,
  TradeoffOwned,
  TradeoffPoint,
  TradeoffPrice,
  TradeoffRenting,
} from "./cloud-api";

// Typed builders over the real `cloud-api` interfaces rather than loose object
// literals: a change to the route's response shape then breaks these at
// compile time instead of leaving a green suite asserting a contract the API
// no longer keeps. Everything is built at call time — no fixture-shaped
// constants sitting in the repo.

function point(
  over: Partial<TradeoffPoint> & { total_slots: number; advice_code: string }
): TradeoffPoint {
  const owned = over.owned_slots ?? 1;
  return {
    owned_slots: owned,
    rented_slots: over.total_slots - owned,
    finish_seconds: null,
    zc_cost: 0,
    usd_cost: null,
    total_usd_value: null,
    ...over,
  };
}

/** A curve at one owned slot, priced and timed: the six fleet sizes a
 * five-task job sweeps. Costs are computed from the finish times the way the
 * route computes them, so the flat step really does cost more than the step
 * before it. */
function fiveTaskCurve(): TradeoffPoint[] {
  const seconds = 60;
  const rate = 0.16;
  const codes: [number, string][] = [
    [1, "baseline"],
    [2, "helps"],
    [3, "helps"],
    [4, "no_marginal_gain"],
    [5, "helps"],
    [6, "beyond_task_count"],
  ];
  return codes.map(([slots, code]) => {
    const finish = Math.ceil(5 / slots) * seconds;
    const usd = Number(((slots - 1) * rate * (finish / 3600)).toFixed(4));
    return point({
      total_slots: slots,
      owned_slots: 1,
      rented_slots: slots - 1,
      advice_code: code,
      finish_seconds: finish,
      usd_cost: usd,
      total_usd_value: usd,
    });
  });
}

/** A sweep the ROUTE CUT SHORT, built with the route's own arithmetic.
 *
 * `_TRADEOFF_MAX_RENTED_STEPS` is 16, so a 40-task job on one owned slot gets
 * fleet sizes 1..17 and stops — nine machines short of the 41 where
 * `ceil(task_count / slots)` reaches 1. The tail is flat (17 slots and 15
 * slots both finish in three task-lengths) purely because the sweep ended
 * there, and 20 slots would still finish sooner.
 *
 * `owned` and `tasks` are parameters because the same truncation produces two
 * different false claims: a ceiling that is not one, and — with enough owned
 * slots that no swept step gains at all — "nothing helps" about a curve that
 * was never swept far enough to find out.
 */
function truncatedSweep(
  { tasks, owned }: { tasks: number; owned: number } = { tasks: 40, owned: 1 }
): TradeoffPoint[] {
  const seconds = 90;
  const rate = 0.16;
  const steps = 16; // _TRADEOFF_MAX_RENTED_STEPS, as app.py sets it
  const points: TradeoffPoint[] = [];
  let previous: number | null = null;
  for (let rented = 0; rented <= steps; rented += 1) {
    const slots = owned + rented;
    const finish = Math.ceil(tasks / slots) * seconds;
    const code =
      rented === 0
        ? "baseline"
        : tasks <= 1
          ? "no_parallelism"
          : slots > tasks
            ? "beyond_task_count"
            : previous != null && finish >= previous
              ? "no_marginal_gain"
              : "helps";
    previous = finish;
    const usd = Number((rented * rate * (finish / 3600)).toFixed(4));
    points.push(
      point({
        total_slots: slots,
        owned_slots: owned,
        rented_slots: rented,
        advice_code: code,
        finish_seconds: finish,
        usd_cost: usd,
        total_usd_value: usd,
      })
    );
  }
  return points;
}

/** The route's own sentence when `_TRADEOFF_MAX_RENTED_STEPS` is what stopped
 * the sweep. Verbatim from `_tradeoff_rented_slots`. */
const SIZE_LIMIT_REASON =
  "the sweep stops at 16 machines, which is this answer's size limit and not " +
  "a fleet size where renting stops helping.";

function price(over: Partial<TradeoffPrice> = {}): TradeoffPrice {
  return {
    provider: "runpod",
    sku: "NVIDIA RTX A5000",
    region: "global",
    tier: "community",
    currency: "USD",
    amount: "0.16",
    unit: "gpu-hour",
    captured_at: "2026-08-12T03:55:00Z",
    age_seconds: 3600,
    stale: false,
    max_age_seconds: 86400,
    source: "runpod REST v2 GPU catalogue (list-gpu-types)",
    observed_by: "runpod-api",
    ...over,
  };
}

function renting(over: Partial<TradeoffRenting> = {}): TradeoffRenting {
  const suited = over.suited ?? true;
  const acquirable = over.acquirable ?? true;
  return {
    reason: "",
    price_reason: "",
    venue_id: acquirable ? "runpod" : null,
    usd_per_hour: acquirable ? 0.16 : null,
    price: acquirable ? price() : null,
    slots: 5,
    slots_reason: "",
    ...over,
    suited,
    acquirable,
    usable: suited && acquirable,
  };
}

function owned(over: Partial<TradeoffOwned> = {}): TradeoffOwned {
  return {
    machines: 1,
    slots: 1,
    reachable_machines: 1,
    ask_zc_per_hour: 0,
    ...over,
  };
}

function tradeoff(over: Partial<JobTradeoff> = {}): JobTradeoff {
  return {
    job_id: "job-1",
    tasks: 5,
    duration: null,
    task_seconds: null,
    owned: owned(),
    renting: renting(),
    points: [],
    notes: [],
    ...over,
  };
}

function read(over: Partial<JobTradeoff> = {}): TradeoffRead {
  return { status: "read", tradeoff: tradeoff(over) };
}

describe("summariseTradeoff — a five-task job on one machine", () => {
  const panel = summariseTradeoff(
    read({
      task_seconds: 60,
      duration: {
        low_seconds: 60,
        high_seconds: 60,
        basis: "measured",
        n: 5,
        note: "p50/IQR of 5 accepted runs",
      },
      points: fiveTaskCurve(),
    })
  );

  it("is a curve, and carries one row per fleet size", () => {
    expect(panel.state).toBe("curve");
    expect(panel.rows.map((r) => r.totalSlots)).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it("starts at the buyer's own hardware, neutrally and never as a gain", () => {
    const first = panel.rows[0];
    expect(first.adviceCode).toBe("baseline");
    expect(first.rentedSlots).toBe(0);
    expect(first.tone).toBe("neutral");
    expect(first.headline).not.toMatch(/help|sooner|faster/i);
  });

  it("never renders no_marginal_gain as a gain", () => {
    // THE rule this module exists to protect. A fleet that costs more and
    // finishes no sooner must not be able to reach an affirmative tone.
    const flat = panel.rows.find((r) => r.adviceCode === "no_marginal_gain");
    expect(flat).toBeDefined();
    expect(flat!.tone).toBe("no-gain");
    expect(flat!.headline).toBe("Costs more, finishes no sooner");
    expect(flat!.savedSeconds).toBe(0);
  });

  it("proves the flat row's arithmetic rather than trusting its label", () => {
    const three = panel.rows.find((r) => r.totalSlots === 3)!;
    const four = panel.rows.find((r) => r.totalSlots === 4)!;
    expect(four.finishSeconds).toBe(three.finishSeconds);
    expect(four.usdCost!).toBeGreaterThan(three.usdCost!);
  });

  it("marks a fleet past the task count as bought and unused", () => {
    const beyond = panel.rows.find(
      (r) => r.adviceCode === "beyond_task_count"
    )!;
    expect(beyond.totalSlots).toBeGreaterThan(panel.tasks!);
    expect(beyond.tone).toBe("no-gain");
  });

  it("says where buying stops helping", () => {
    expect(panel.lastGain!.totalSlots).toBe(5);
    expect(panel.nothingHelps).toBe(false);
  });

  it("carries both settlement units and their settled-rate total", () => {
    for (const row of panel.rows) {
      expect(row.zcCost).toBe(0);
      expect(row.usdCost).not.toBeNull();
      expect(row.totalUsdValue).not.toBeNull();
    }
  });
});

describe("summariseTradeoff — the five advice codes", () => {
  it("gives every code a headline, a meaning and a tone", () => {
    const codes: AdviceCode[] = [
      "baseline",
      "helps",
      "no_marginal_gain",
      "beyond_task_count",
      "no_parallelism",
    ];
    for (const code of codes) {
      const copy = ADVICE_COPY[code];
      expect(copy.headline.length).toBeGreaterThan(0);
      expect(copy.meaning.length).toBeGreaterThan(0);
      expect(["neutral", "gain", "no-gain"]).toContain(copy.tone);
    }
  });

  it("marks exactly one of the five as a gain", () => {
    const gains = Object.entries(ADVICE_COPY).filter(
      ([, copy]) => copy.tone === "gain"
    );
    expect(gains.map(([code]) => code)).toEqual(["helps"]);
  });

  it("declines to characterise a verdict it has never heard of", () => {
    // An API that grows a sixth code must not reach a reader as a badge that
    // claims something the route did not say.
    const copy = adviceCopy("some_future_code");
    expect(copy.tone).toBe("neutral");
    expect(copy.headline).not.toMatch(/sooner|faster|help/i);
  });
});

describe("summariseTradeoff — a one-task job", () => {
  const panel = summariseTradeoff(
    read({
      tasks: 1,
      task_seconds: 60,
      points: [
        point({
          total_slots: 1,
          advice_code: "baseline",
          finish_seconds: 60,
          usd_cost: 0,
          total_usd_value: 0,
        }),
        point({
          total_slots: 2,
          advice_code: "no_parallelism",
          finish_seconds: 60,
          usd_cost: 0.0027,
          total_usd_value: 0.0027,
        }),
      ],
    })
  );

  it("says plainly that no fleet is faster, as advice and not as an error", () => {
    const rented = panel.rows[1];
    expect(rented.adviceCode).toBe("no_parallelism");
    expect(rented.tone).toBe("no-gain");
    expect(rented.meaning).toContain("cannot be split");
    expect(rented.meaning).toContain("at any price");
  });

  it("reports that nothing on the curve helps", () => {
    expect(panel.lastGain).toBeNull();
    expect(panel.nothingHelps).toBe(true);
  });
});

describe("summariseTradeoff — a sweep the route cut short", () => {
  // THE DEMO-BLOCKING ONE. `_TRADEOFF_MAX_RENTED_STEPS` stops the sweep at 16
  // rented machines and the route SAYS SO in `slots_reason`. An HPO sweep of
  // 17+ trials — the workload this product is pitched on — therefore ends on a
  // run of `no_marginal_gain` rows that mean "the answer ran out", not
  // "renting ran out". Turning that tail into "nothing past 14 slots finishes
  // this job any sooner" states the opposite of the truth: 20 slots finishes
  // this curve in half the time and 40 in a quarter.
  const panel = summariseTradeoff(
    read({
      tasks: 40,
      task_seconds: 90,
      renting: renting({ slots: 16, slots_reason: SIZE_LIMIT_REASON }),
      points: truncatedSweep(),
    })
  );

  it("ends on a flat tail that is the size limit, not a ceiling", () => {
    const codes = panel.rows.map((r) => r.adviceCode);
    expect(panel.rows).toHaveLength(17);
    expect(codes[codes.length - 1]).toBe("no_marginal_gain");
    // And the flatness really is only the tail: a bigger fleet off the end of
    // the sweep still halves the finish time.
    const last = panel.rows[panel.rows.length - 1];
    expect(last.finishSeconds).toBe(Math.ceil(40 / 17) * 90);
    expect(Math.ceil(40 / 20) * 90).toBeLessThan(last.finishSeconds!);
  });

  it("refuses to call a truncated sweep a complete one", () => {
    expect(panel.sweepComplete).toBe(false);
    // The data is unchanged — 14 IS the last gaining row that was swept. What
    // changes is whether the panel is allowed to make a claim out of it.
    expect(panel.lastGain!.totalSlots).toBe(14);
  });

  it("calls an untruncated sweep complete, on its last row's own verdict", () => {
    // An untruncated sweep always ends past the useful range, because the
    // route sweeps `task_count + 1 - owned_slots` machines.
    const whole = summariseTradeoff(
      read({ task_seconds: 60, points: fiveTaskCurve() })
    );
    expect(whole.rows[whole.rows.length - 1].adviceCode).toBe(
      "beyond_task_count"
    );
    expect(whole.sweepComplete).toBe(true);

    // A single-task job is the other honest ending: no fleet is faster, and
    // the route sweeps one machine to show it.
    const single = summariseTradeoff(
      read({
        tasks: 1,
        task_seconds: 60,
        points: [
          point({ total_slots: 1, advice_code: "baseline", finish_seconds: 60 }),
          point({
            total_slots: 2,
            advice_code: "no_parallelism",
            finish_seconds: 60,
          }),
        ],
      })
    );
    expect(single.sweepComplete).toBe(true);
  });

  it("never says nothing helps about a curve it never swept far enough", () => {
    // 20 owned slots against 40 tasks: every one of the 16 swept machines
    // lands on the same `ceil(40 / slots) = 2` step, so NOT ONE ROW GAINS.
    // The 21st machine — one past the size limit — is the one that halves it.
    const flat = summariseTradeoff(
      read({
        tasks: 40,
        task_seconds: 90,
        owned: owned({ machines: 20, slots: 20, reachable_machines: 20 }),
        renting: renting({ slots: 16, slots_reason: SIZE_LIMIT_REASON }),
        points: truncatedSweep({ tasks: 40, owned: 20 }),
      })
    );
    expect(flat.rows.every((r) => r.tone !== "gain")).toBe(true);
    expect(flat.lastGain).toBeNull();
    expect(flat.sweepComplete).toBe(false);
    expect(flat.nothingHelps).toBe(false);
  });
});

describe("summariseTradeoff — a curve that priced no machine at all", () => {
  // `nothingHelps` used to be `rows.length > 0 && lastGain === null`, which
  // asserts a trade-off the route never computed. A sweep cut to zero rented
  // machines for a NON-ARITHMETIC reason — the cheapest published rate above
  // `rented_usd_per_acquisition_max`, say — leaves a baseline-only curve, and
  // "buying capacity would spend money and save nothing" is a claim about
  // machines nobody priced. It refused to price them.
  const REFUSED_ON_RATE =
    "no machine is swept: the cheapest published rate is $2.69/hr and this " +
    "deployment refuses any single rental above $1.5/hr.";
  const panel = summariseTradeoff(
    read({
      tasks: 5,
      task_seconds: 300,
      renting: renting({ slots: 0, slots_reason: REFUSED_ON_RATE }),
      points: [
        point({
          total_slots: 1,
          advice_code: "baseline",
          finish_seconds: 1500,
          usd_cost: 0,
          total_usd_value: 0,
        }),
      ],
    })
  );

  it("makes no claim about renting when no rented row exists", () => {
    expect(panel.rows.some((r) => r.rentedSlots > 0)).toBe(false);
    expect(panel.lastGain).toBeNull();
    expect(panel.nothingHelps).toBe(false);
  });

  it("still says nothing helps when rented rows were swept and none did", () => {
    // The guard must not swallow the true case: a one-task job's rented row
    // exists, was priced, and buys nothing.
    const single = summariseTradeoff(
      read({
        tasks: 1,
        task_seconds: 60,
        points: [
          point({ total_slots: 1, advice_code: "baseline", finish_seconds: 60 }),
          point({
            total_slots: 2,
            advice_code: "no_parallelism",
            finish_seconds: 60,
          }),
        ],
      })
    );
    expect(single.nothingHelps).toBe(true);
  });
});

describe("summariseTradeoff — a public job that cannot rent", () => {
  const REFUSAL =
    "renting cannot make this job faster at any price. It is a public job — placement.pool is 'any' — so it carries no allowFallback waiver, and its tasks are sandboxed, which the coordinator places only on a host advertising sandbox_capable: true.";
  const panel = summariseTradeoff(
    read({
      renting: renting({
        suited: false,
        reason: REFUSAL,
        price_reason: "priced at $0.16/hr from RunPod.",
        slots: 0,
        slots_reason: "no rented machine is swept.",
      }),
      points: [
        point({
          total_slots: 1,
          advice_code: "baseline",
          finish_seconds: 300,
          usd_cost: 0,
          total_usd_value: 0,
        }),
      ],
    })
  );

  it("says renting cannot help, and carries the API's reason verbatim", () => {
    expect(panel.renting!.verdict).toBe("not-for-this-job");
    expect(panel.renting!.headline).toBe(RENTING_COPY["not-for-this-job"]);
    expect(panel.renting!.reason).toBe(REFUSAL);
  });

  it("keeps 'may not rent' and 'no price' as different refusals", () => {
    // The bug this guards: a job that may not use a rented machine reading
    // as one we merely failed to price, which sends somebody off to fix the
    // wrong thing.
    const unpriced = summariseTradeoff(
      read({ renting: renting({ acquirable: false }) })
    );
    expect(unpriced.renting!.verdict).toBe("unpriced");
    expect(panel.renting!.headline).not.toBe(unpriced.renting!.headline);
    expect(unpriced.renting!.suited).toBe(true);
  });

  it("offers no rented row at all", () => {
    expect(panel.rows).toHaveLength(1);
    expect(panel.rows[0].rentedSlots).toBe(0);
  });
});

describe("summariseTradeoff — the price claim's qualifications (price_notes)", () => {
  // `5f7fb50` split the API's one ~90-word `price_reason` string into the
  // claim alone plus a `price_notes: list[str]`, each entry a standalone
  // qualification. This module's job is just to carry the list through
  // without joining, dropping or reordering it.
  const NOTES = [
    "this compares only runpod SKUs that publish a rate here.",
    "runpod capacity is acquired manually: a person starts that machine.",
    "the automatically-acquired venues are absent from this comparison, not more expensive than it.",
  ];

  it("carries every note through, verbatim and in order", () => {
    const panel = summariseTradeoff(
      read({ renting: renting({ price_notes: NOTES }) })
    );
    expect(panel.renting!.priceNotes).toEqual(NOTES);
  });

  it("reports an empty list when the route sent none, never undefined", () => {
    const panel = summariseTradeoff(read({ renting: renting({}) }));
    expect(panel.renting!.priceNotes).toEqual([]);
  });

  it("reports an empty list when the route sent null — an older response", () => {
    const panel = summariseTradeoff(
      read({
        renting: renting({ price_notes: null as unknown as string[] }),
      })
    );
    expect(panel.renting!.priceNotes).toEqual([]);
  });
});

describe("summariseTradeoff — nothing observed", () => {
  const panel = summariseTradeoff(read({ points: fiveTaskCurve().map((p) => ({
    ...p,
    finish_seconds: null,
    usd_cost: null,
    total_usd_value: null,
  })) }));

  it("keeps every advice code even with no seconds behind it", () => {
    expect(panel.rows.map((r) => r.adviceCode)).toEqual([
      "baseline",
      "helps",
      "helps",
      "no_marginal_gain",
      "helps",
      "beyond_task_count",
    ]);
  });

  it("reports null as not observed and never as 0", () => {
    for (const row of panel.rows) {
      expect(row.finishSeconds).toBeNull();
      expect(row.usdCost).toBeNull();
      expect(row.totalUsdValue).toBeNull();
      expect(row.savedSeconds).toBeNull();
    }
    expect(panel.taskSeconds).toBeNull();
  });

  it("says not observed for a basis nobody could establish", () => {
    expect(basisLabel(null, null)).toBe(NOT_OBSERVED);
    expect(basisLabel("measured", 0)).toBe("measured");
    expect(basisLabel("measured", 5)).toBe("measured, n=5");
  });
});

describe("summariseTradeoff — the four states", () => {
  it("shows a loading read as loading, with nothing asserted yet", () => {
    const panel = summariseTradeoff({ status: "loading" });
    expect(panel.state).toBe("loading");
    expect(panel.rows).toEqual([]);
    expect(panel.owned).toBeNull();
    expect(panel.renting).toBeNull();
  });

  it("shows an answered-but-empty curve as empty, with its notes", () => {
    const panel = summariseTradeoff(
      read({
        owned: null,
        renting: null,
        points: [],
        notes: ["routing is not configured on this deployment"],
      })
    );
    expect(panel.state).toBe("empty");
    expect(panel.notes[0]).toContain("routing is not configured");
    expect(panel.nothingHelps).toBe(false);
  });

  it("never turns a failed read into a job no fleet can help", () => {
    // "Could not read this" and "there is nothing" are different sentences.
    const failed = summariseTradeoff({ status: "unreadable", detail: "502" });
    const answered = summariseTradeoff(read({ points: [] }));
    expect(failed.state).toBe("unreadable");
    expect(failed.detail).toBe("502");
    expect(failed.state).not.toBe(answered.state);
    expect(failed.nothingHelps).toBe(false);
    expect(failed.renting).toBeNull();
  });
});

describe("job-tradeoff stays pure", () => {
  it("gives the same answer twice for the same read", () => {
    const same = read({ task_seconds: 60, points: fiveTaskCurve() });
    expect(summariseTradeoff(same)).toEqual(summariseTradeoff(same));
  });
});

// Two of this panel's properties are properties of the RENDERED page rather
// than of the view model — that a row which buys nothing does not look like
// an offer, and that a failed read does not render as an empty curve. So they
// are asserted against the markup, the way `lib/job-routing.test.ts` does.
function render(panel: ReturnType<typeof summariseTradeoff>) {
  return renderToStaticMarkup(
    createElement(TradeoffCard, { panel, onRetry: () => undefined })
  );
}

describe("TradeoffCard — the rendered page", () => {
  const markup = render(
    summariseTradeoff(
      read({
        task_seconds: 60,
        duration: {
          low_seconds: 60,
          high_seconds: 60,
          basis: "measured",
          n: 5,
          note: "",
        },
        renting: renting({
          reason: "this job is scoped to a workspace and carries the waiver.",
          price_reason: "priced at $0.16/hr from the cheapest published hour.",
          slots_reason: "the sweep stops one machine past the task count.",
        }),
        points: fiveTaskCurve(),
      })
    )
  );

  it("shows every fleet size and its verdict", () => {
    expect(markup).toContain("Costs more, finishes no sooner");
    expect(markup).toContain("More machines than tasks");
    expect(markup).toContain("Finishes sooner");
    expect(markup).toContain("What you already have");
  });

  it("gives the gaining and the non-gaining rows different treatment", () => {
    // A row that buys nothing must not be able to wear the affirmative
    // colour. The evergreen token is the one this card uses for a gain.
    const rows = Array.from(markup.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/g));
    const flat = rows.find((r) =>
      r[1].includes("Costs more, finishes no sooner")
    )!;
    const gain = rows.find((r) => r[1].includes("Finishes sooner"))!;
    expect(gain[1]).toContain("evergreen");
    expect(flat[1]).not.toContain("evergreen");
  });

  it("prints the API's own sentences about renting verbatim", () => {
    expect(markup).toContain("this job is scoped to a workspace");
    expect(markup).toContain("priced at $0.16/hr");
    // `slots_reason` is NOT asserted here: this fixture's sweep is complete
    // and gains, so the panel prints the stronger, definitive "Nothing past
    // N slots finishes this job any sooner" claim below the table instead
    // (see "says where buying stops helping") — and, on purpose, does not
    // ALSO print `slots_reason` beside the renting summary above the table
    // the way it once did. Printing both was the exact bug fixed here: the
    // same sentence twice on a truncated curve (see "says the
    // truncated-sweep sentence once" below, and "claims no ceiling when the
    // sweep was cut short" for `slots_reason` actually rendering when the
    // sweep is NOT complete).
  });

  it("shows the price with its provenance", () => {
    expect(markup).toContain("NVIDIA RTX A5000");
    expect(markup).toContain("gpu-hour");
    expect(markup).toContain("2026-08-12T03:55:00Z");
  });

  it("says where buying stops helping", () => {
    expect(markup).toContain("finishes this job any sooner");
  });

  it("renders unobserved figures as unobserved, never as zero", () => {
    const blank = render(
      summariseTradeoff(
        read({
          points: fiveTaskCurve().map((p) => ({
            ...p,
            finish_seconds: null,
            usd_cost: null,
            total_usd_value: null,
          })),
        })
      )
    );
    expect(blank).toContain(NOT_OBSERVED);
    expect(blank).not.toContain(">0s<");
  });

  it("does not stamp a measurement failure on the row with no predecessor", () => {
    // The baseline row's `savedSeconds` is null because there is no row above
    // it to have saved anything against — NOT because a measurement failed.
    // "not observed" beside a populated finish time reads, on a projector, as
    // a broken read in row one.
    const rows = Array.from(markup.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/g));
    const baseline = rows.find((r) => r[1].includes("What you already have"))!;
    expect(baseline[1]).toContain("5m"); // its finish time WAS observed
    expect(baseline[1]).not.toContain(NOT_OBSERVED);
    expect(baseline[1]).toContain("—");

    // And a row that DOES have a predecessor whose finish time was genuinely
    // null still says so.
    const unobserved = render(
      summariseTradeoff(
        read({
          points: fiveTaskCurve().map((p) => ({ ...p, finish_seconds: null })),
        })
      )
    );
    expect(unobserved).toContain(NOT_OBSERVED);
  });

  it("says which row each refusal is measured against", () => {
    // 0.24 -> 0.32 -> 0.20 down the USD column is correct — cost is rate x
    // machines x wall-clock held — and reads as a broken table unless the
    // legend says every verdict is pairwise against the row directly above.
    expect(ADVICE_COPY.no_marginal_gain.meaning).toMatch(
      /^Compared with the row directly above:/
    );
    expect(markup).toContain("Compared with the row directly above");
  });

  it("claims no ceiling when the sweep was cut short", () => {
    // B1, on the page. The route's own `slots_reason` takes the slot the
    // false sentence used to occupy.
    const cut = render(
      summariseTradeoff(
        read({
          tasks: 40,
          task_seconds: 90,
          renting: renting({ slots: 16, slots_reason: SIZE_LIMIT_REASON }),
          points: truncatedSweep(),
        })
      )
    );
    expect(cut).not.toContain("finishes this job any sooner");
    expect(cut).not.toContain("spend money and save nothing");
    expect(cut).toContain("not a fleet size where renting stops helping");
  });

  it("makes no claim at all about a curve that priced no machine", () => {
    const unpriced = render(
      summariseTradeoff(
        read({
          renting: renting({
            slots: 0,
            slots_reason: "no machine is swept: the cheapest published rate.",
          }),
          points: [
            point({
              total_slots: 1,
              advice_code: "baseline",
              finish_seconds: 300,
            }),
          ],
        })
      )
    );
    expect(unpriced).not.toContain("spend money and save nothing");
    expect(unpriced).not.toContain("finishes this job any sooner");
  });

  it("says a failed read failed, in the API's own words", () => {
    const failed = render(
      summariseTradeoff({
        status: "unreadable",
        detail: "this job has no stored spec to plan against",
      })
    );
    expect(failed).toContain("Couldn&#x27;t read what another machine would buy");
    expect(failed).toContain("this job has no stored spec to plan against");
    // And above all, no claim that a fleet was compared and found wanting.
    expect(failed).not.toContain("Costs more, finishes no sooner");
    expect(failed).not.toContain("finishes this job any sooner");
  });

  it("tells an empty answer apart from a failed one on the page", () => {
    const empty = render(
      summariseTradeoff(
        read({
          owned: null,
          renting: null,
          points: [],
          notes: ["this spec expands to no tasks"],
        })
      )
    );
    expect(empty).toContain("There is no fleet to compare for this job.");
    expect(empty).toContain("this spec expands to no tasks");
    expect(empty).not.toContain("Couldn&#x27;t read");
  });

  it("gives no sentence about pricing to a job that can never rent", () => {
    // `suited: false` means no rented machine is ever eligible for this
    // job's work — a public job with sandboxed tasks, say. Printing a price
    // sentence under that refusal reads as a non-sequitur: how carefully we
    // priced a machine this job may not use is not information, it is noise
    // beside the answer that actually matters.
    const cannotRent = render(
      summariseTradeoff(
        read({
          renting: renting({
            suited: false,
            reason: "renting cannot make this job faster at any price.",
            price_reason: "priced at $0.16/hr from RunPod.",
          }),
          points: [
            point({
              total_slots: 1,
              advice_code: "baseline",
              finish_seconds: 300,
            }),
          ],
        })
      )
    );
    expect(cannotRent).toContain(
      "renting cannot make this job faster at any price."
    );
    expect(cannotRent).not.toContain("priced at $0.16/hr from RunPod.");

    // The same sentence prints when renting IS available for the job —
    // `suited: true`, whether or not a price was actually found — so the
    // guard is on `suited` and nothing else.
    const canRent = render(
      summariseTradeoff(
        read({
          renting: renting({
            suited: true,
            reason: "this job carries the waiver.",
            price_reason: "priced at $0.16/hr from RunPod.",
          }),
        })
      )
    );
    expect(canRent).toContain("priced at $0.16/hr from RunPod.");
  });

  it("says the truncated-sweep sentence once, not once above the table and once below it", () => {
    // Both placements were correct in isolation — the route's own
    // `slots_reason` printed beside the renting summary, and again as the
    // panel's fallback explanation under the curve. Together they say the
    // exact same sentence twice on one page, which reads as a bug even
    // though neither copy is wrong on its own. Keep the one closest to what
    // it explains: where the curve stops, not the header above it.
    const cut = render(
      summariseTradeoff(
        read({
          tasks: 40,
          task_seconds: 90,
          renting: renting({ slots: 16, slots_reason: SIZE_LIMIT_REASON }),
          points: truncatedSweep(),
        })
      )
    );
    // Matched without the apostrophe: `renderToStaticMarkup` HTML-encodes it
    // (`answer&#x27;s`), so splitting on the raw sentence would under-count.
    const needle = "a fleet size where renting stops helping.";
    const occurrences = cut.split(needle).length - 1;
    expect(occurrences).toBe(1);
  });
});

describe("TradeoffCard — the price claim's qualifications on the page", () => {
  it("shows each price note as its own line, not joined into one paragraph", () => {
    const notes = [
      "this compares only runpod SKUs that publish a rate here.",
      "runpod capacity is acquired manually: a person starts that machine.",
    ];
    const markup = render(
      summariseTradeoff(
        read({
          renting: renting({
            price_reason: "$0.16/hr — the cheapest published runpod rate.",
            price_notes: notes,
          }),
        })
      )
    );
    expect(markup).toContain('data-testid="price-notes"');
    for (const note of notes) {
      expect(markup).toContain(note);
    }
    // Not concatenated: the claim and the notes are separately present, and
    // the claim sentence itself is untouched by the split.
    expect(markup).toContain("$0.16/hr — the cheapest published runpod rate.");
  });

  it("renders cleanly with no stray notes markup when the route sent none", () => {
    const markup = render(
      summariseTradeoff(
        read({
          renting: renting({
            price_reason: "$0.16/hr — the cheapest published runpod rate.",
            price_notes: [],
          }),
        })
      )
    );
    expect(markup).toContain("$0.16/hr — the cheapest published runpod rate.");
    expect(markup).not.toContain('data-testid="price-notes"');
  });

  it("renders cleanly when price_notes is entirely absent — an older response", () => {
    const markup = render(
      summariseTradeoff(
        read({
          renting: renting({
            price_reason: "$0.16/hr — the cheapest published runpod rate.",
            price_notes: undefined,
          }),
        })
      )
    );
    expect(markup).toContain("$0.16/hr — the cheapest published runpod rate.");
    expect(markup).not.toContain('data-testid="price-notes"');
  });

  it("withholds notes exactly when it withholds the claim — on a job that cannot rent", () => {
    const markup = render(
      summariseTradeoff(
        read({
          renting: renting({
            suited: false,
            reason: "renting cannot make this job faster at any price.",
            price_reason: "priced at $0.16/hr from RunPod.",
            price_notes: ["a note that must not leak onto an unsuited job."],
          }),
        })
      )
    );
    expect(markup).not.toContain("priced at $0.16/hr from RunPod.");
    expect(markup).not.toContain('data-testid="price-notes"');
    expect(markup).not.toContain("a note that must not leak onto an unsuited job.");
  });
});

describe("TradeoffCard — a long curve collapses", () => {
  // `truncatedSweep()` (40 tasks, 1 owned slot, 16 rented steps swept) is a
  // real 17-row curve with a genuine wobble: helps dips flat, recovers,
  // dips again. Worked out by hand against `groupTradeoffRows`'s rules:
  //   slots 1        baseline                    — anchor (first row)
  //   slots 2        helps                       — anchor (differs from baseline)
  //   slots 3..7     helps                       — collapses (5 rows, no boundary)
  //   slots 8        helps                       — anchor (last helps before the dip)
  //   slots 9        no_marginal_gain            — anchor (first flat after helps)
  //   slots 10       helps                       — anchor (recovers, then dips again)
  //   slots 11       no_marginal_gain            — anchor (first flat of 2nd dip)
  //   slots 12..13   no_marginal_gain            — a run of exactly TWO: stays plain
  //   slots 14       helps                       — anchor
  //   slots 15       no_marginal_gain            — anchor
  //   slots 16       no_marginal_gain            — a run of exactly ONE: stays plain
  //   slots 17       no_marginal_gain            — anchor (last row)
  // One collapsed group (slots 3..7), thirteen display items for seventeen
  // rows, and every transition pair on the page.
  const markup = render(
    summariseTradeoff(
      read({
        tasks: 40,
        task_seconds: 90,
        renting: renting({ slots: 16, slots_reason: SIZE_LIMIT_REASON }),
        points: truncatedSweep(),
      })
    )
  );

  it("collapses the long same-verdict run into exactly one control", () => {
    const occurrences = markup.split('data-testid="collapsed-tradeoff-rows"').length - 1;
    expect(occurrences).toBe(1);
  });

  it("hides a row in the middle of the collapsed run from the initial markup", () => {
    // Fleet size 4 sits inside the collapsed 3..7 run and is not a boundary.
    expect(markup).not.toContain("4 · +3 rented");
  });

  it("keeps the helps -> no_marginal_gain transition on the page — the pair the panel exists to show", () => {
    // Fleet size 8 is the last `helps` row before the dip, fleet size 9 is
    // the first `no_marginal_gain` row after it.
    expect(markup).toContain("8 · +7 rented");
    expect(markup).toContain("9 · +8 rented");
  });

  it("keeps the first and last row of the curve on the page", () => {
    expect(markup).toContain("1 yours"); // baseline, fleet size 1
    expect(markup).toContain("17 · +16 rented"); // the curve's last row
  });

  it("does not collapse a run of exactly two or exactly one row", () => {
    // Fleet sizes 12, 13 (a run of two) and 16 (a run of one) are all short
    // of the three-row minimum and must render plainly.
    expect(markup).toContain("12 · +11 rented");
    expect(markup).toContain("13 · +12 rented");
    expect(markup).toContain("16 · +15 rented");
  });

  it("says how many rows the control hides, from the rows themselves", () => {
    expect(markup).toContain("5 more");
  });
});

describe("TradeoffCard — a short curve is untouched", () => {
  it("never renders a collapsed-rows control when no run reaches three", () => {
    const markup = render(
      summariseTradeoff(read({ task_seconds: 60, points: fiveTaskCurve() }))
    );
    expect(markup).not.toContain('data-testid="collapsed-tradeoff-rows"');
    // Every fleet size is on the page.
    for (const slots of [1, 2, 3, 4, 5, 6]) {
      expect(markup).toContain(`${slots}`);
    }
  });
});
