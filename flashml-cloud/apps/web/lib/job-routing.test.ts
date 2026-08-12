import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RoutingCard } from "@/components/jobs/RoutingCard";

import {
  NOT_OBSERVED,
  VERDICT_COPY,
  basisLabel,
  summariseRouting,
  type RoutingRead,
} from "./job-routing";
import type {
  PlanPreview,
  PreviewPlan,
  PreviewVenue,
} from "./cloud-api";

// Typed builders over the real `cloud-api` interfaces rather than loose
// object literals: a change to the route's response shape then breaks these
// at compile time instead of leaving a green suite asserting a contract the
// API no longer keeps.

function venue(over: Partial<PreviewVenue> & { id: string }): PreviewVenue {
  const suited = over.suited ?? true;
  const acquirable = over.acquirable ?? true;
  return {
    display: over.id,
    currency: "USD",
    reason: "",
    acquisition: acquirable ? "automatic" : "none",
    ...over,
    suited,
    acquirable,
    usable: suited && acquirable,
  };
}

function plan(over: Partial<PreviewPlan> & { name: string }): PreviewPlan {
  return {
    recommended: false,
    tasks_placed: 0,
    tasks_unplaced: 0,
    cost: { zc: 0, usd: 0, total_usd_value: 0 },
    within_budget: { zc: null, usd: null },
    makespan_seconds: null,
    deadline_seconds: null,
    deadline_met: null,
    achievable_deadline_seconds: null,
    basis: null,
    n: null,
    duration_basis: null,
    dominated_by: null,
    allocations: [],
    notes: [],
    ...over,
  };
}

function preview(over: Partial<PlanPreview> = {}): PlanPreview {
  return {
    job_id: "job-1",
    tasks: 1,
    duration: null,
    plans: [],
    candidates: [],
    canary: null,
    recommended: null,
    eligible_machines: 0,
    excluded_machines: 0,
    unplannable_machines: 0,
    notes: [],
    ...over,
  };
}

function previewed(over: Partial<PlanPreview> = {}): RoutingRead {
  return { status: "previewed", preview: preview(over) };
}

/** The four venues as the route ranks them for a GPU fine-tune: the two that
 * can take the work, then the real fit we cannot obtain capacity at, then the
 * CPU-only box the hardware requirement rules out. Reasons are abbreviated
 * from the ones `router/venues.py` actually returns — this module must carry
 * whatever sentence it is handed, so their content is the test's input and
 * never its expectation. */
const GPU_FINETUNE_VENUES: PreviewVenue[] = [
  venue({
    id: "owned",
    display: "Your machines",
    currency: "ZC",
    reason:
      "a home rig in the gpu-24gb class is a real training machine, and it is free to its workspace (M1).",
  }),
  venue({
    id: "runpod",
    display: "RunPod",
    acquisition: "manual",
    reason:
      "a rented pod is a card held for the whole run, which is what a long job needs. A human has to start it.",
  }),
  venue({
    id: "fc-gpu",
    display: "Alibaba FC serverless GPU",
    acquirable: false,
    acquisition: "none",
    reason:
      "T4 16 GB through Hopper 96 GB, up to 8 cards. Not integrated, so it can hold no work today.",
  }),
  venue({
    id: "fc-sandbox",
    display: "Alibaba FC Agent Sandbox",
    suited: false,
    reason:
      "2 vCPU, 2 GB RAM and no GPU (gpuConfig: null). Gradient work does not fit in that at any duration.",
  }),
];

describe("summariseRouting — a GPU fine-tune", () => {
  const panel = summariseRouting(
    previewed({
      kind: "finetune",
      kind_evidence:
        "args carry --base-model, and the spec expands to 1 task over no sweep axes",
      tasks: 1,
      venues: GPU_FINETUNE_VENUES,
      eligible_machines: 3,
      excluded_machines: 1,
      venue_excluded_machines: 2,
      unplannable_machines: 0,
    })
  );

  it("reports the kind with the evidence behind it, never the label alone", () => {
    expect(panel.state).toBe("routed");
    expect(panel.kind).toBe("finetune");
    expect(panel.kindEvidence).toContain("--base-model");
  });

  it("keeps all four venues, including the two it cannot use", () => {
    expect(panel.venues.map((v) => v.id)).toEqual([
      "owned",
      "runpod",
      "fc-gpu",
      "fc-sandbox",
    ]);
  });

  it("says fc-sandbox cannot run this work — a fact about the hardware", () => {
    const sandbox = panel.venues.find((v) => v.id === "fc-sandbox");
    expect(sandbox?.verdict).toBe("unsuited");
    expect(sandbox?.headline).toBe("Can't run this work");
  });

  it("says fc-gpu is a fit we cannot reach — a gap on our side", () => {
    const gpu = panel.venues.find((v) => v.id === "fc-gpu");
    expect(gpu?.verdict).toBe("unreachable");
    expect(gpu?.headline).toBe("No capacity we can reach");
  });

  // The whole point of the panel. If these two ever collapse into one
  // string, the UI starts implying we CHOSE not to use a venue we simply
  // cannot obtain capacity at.
  it("never gives the two refusals the same words", () => {
    const sandbox = panel.venues.find((v) => v.id === "fc-sandbox");
    const gpu = panel.venues.find((v) => v.id === "fc-gpu");
    expect(sandbox?.headline).not.toBe(gpu?.headline);
    expect(sandbox?.meaning).not.toBe(gpu?.meaning);
    expect(VERDICT_COPY.unsuited.headline).not.toBe(
      VERDICT_COPY.unreachable.headline
    );
  });

  it("quotes each venue's reason verbatim rather than paraphrasing it", () => {
    for (const row of panel.venues) {
      const source = GPU_FINETUNE_VENUES.find((v) => v.id === row.id);
      expect(row.reason).toBe(source?.reason);
    }
  });

  it("carries the venue-fit exclusions separately from the gate refusals", () => {
    expect(panel.fleet).toEqual({
      eligible: 3,
      excluded: 1,
      venueExcluded: 2,
      unplannable: 0,
    });
  });

  it("marks an unsuited-and-unobtainable venue as both, hardware first", () => {
    const both = summariseRouting(
      previewed({
        kind: "finetune",
        venues: [
          venue({
            id: "fc-sandbox",
            suited: false,
            acquirable: false,
            reason: "no GPU, and nothing here can create one either",
          }),
        ],
      })
    );
    expect(both.venues[0].verdict).toBe("unsuited");
    expect(both.venues[0].alsoUnreachable).toBe(true);
  });

  it("does not set alsoUnreachable on a venue we can obtain", () => {
    const sandbox = panel.venues.find((v) => v.id === "fc-sandbox");
    expect(sandbox?.alsoUnreachable).toBe(false);
  });
});

describe("summariseRouting — an HPO sweep", () => {
  const panel = summariseRouting(
    previewed({
      kind: "hpo",
      kind_evidence:
        "sweep over lr, batch_size expands to 40 independent trials sharing one entrypoint",
      tasks: 40,
      venues: [
        venue({
          id: "owned",
          display: "Your machines",
          currency: "ZC",
          reason:
            "trials are independent and short, which is exactly what a pull fleet absorbs.",
        }),
        venue({
          id: "fc-sandbox",
          display: "Alibaba FC Agent Sandbox",
          reason:
            "a trial is short and self-contained, so the gap between trials costs almost nothing here.",
        }),
      ],
      eligible_machines: 6,
      unplannable_machines: 1,
      recommended: "cheapest",
      plans: [
        plan({
          name: "cheapest",
          recommended: true,
          tasks_placed: 40,
          cost: { zc: 12, usd: 3.5, total_usd_value: 15.5 },
          makespan_seconds: 1840,
          basis: "measured",
          n: 6,
          allocations: [],
        }),
        plan({
          name: "fastest",
          tasks_placed: 40,
          cost: { zc: 30, usd: 9.25, total_usd_value: 39.25 },
          makespan_seconds: 420,
          basis: "projected",
          n: 2,
          dominated_by: null,
        }),
      ],
    })
  );

  it("routes the sweep and keeps the trial count the route expanded", () => {
    expect(panel.state).toBe("routed");
    expect(panel.kind).toBe("hpo");
    expect(panel.tasks).toBe(40);
    expect(panel.kindEvidence).toContain("40 independent trials");
  });

  it("keeps the plans in the route's own frontier order", () => {
    expect(panel.plans.map((p) => p.name)).toEqual(["cheapest", "fastest"]);
    expect(panel.recommended).toBe("cheapest");
    expect(panel.plans[0].recommended).toBe(true);
    expect(panel.quotedNothing).toBe(false);
  });

  it("carries each plan's own basis and n — the weakest behind it", () => {
    expect(basisLabel(panel.plans[0].basis, panel.plans[0].n)).toBe(
      "measured, n=6"
    );
    expect(basisLabel(panel.plans[1].basis, panel.plans[1].n)).toBe(
      "projected, n=2"
    );
  });
});

describe("summariseRouting — settlement totals plus normalized USD value", () => {
  const panel = summariseRouting(
    previewed({
      kind: "hpo",
      plans: [
        plan({
          name: "cheapest",
          tasks_placed: 40,
          cost: { zc: 12, usd: 3.5, total_usd_value: 15.5 },
        }),
      ],
    })
  );
  const row = panel.plans[0];

  it("reports exactly two figures, each labelled with its own currency", () => {
    expect(row.costs).toEqual([
      { currency: "ZC", amount: 12 },
      { currency: "USD", amount: 3.5 },
    ]);
  });

  it("carries the API's normalized total without rewriting either source", () => {
    expect(row.totalUsdValue).toBe(15.5);
    expect(row.costs).toEqual([
      { currency: "ZC", amount: 12 },
      { currency: "USD", amount: 3.5 },
    ]);
  });

  it("keeps a zero in one currency, because zero spent is a measurement", () => {
    const free = summariseRouting(
      previewed({
        kind: "hpo",
        plans: [
          plan({
            name: "cheapest",
            cost: { zc: 0, usd: 0, total_usd_value: 0 },
          }),
        ],
      })
    );
    expect(free.plans[0].costs).toEqual([
      { currency: "ZC", amount: 0 },
      { currency: "USD", amount: 0 },
    ]);
  });
});

describe("summariseRouting — unobserved is not zero", () => {
  it("leaves a null makespan null rather than reporting a zero-second plan", () => {
    const panel = summariseRouting(
      previewed({
        kind: "training",
        plans: [
          plan({ name: "cheapest", tasks_placed: 4, makespan_seconds: null }),
        ],
      })
    );
    expect(panel.plans[0].makespanSeconds).toBeNull();
    expect(panel.plans[0].makespanSeconds).not.toBe(0);
  });

  it("calls a missing basis not observed, never a measurement of nothing", () => {
    expect(basisLabel(null, null)).toBe(NOT_OBSERVED);
    expect(basisLabel(null, 0)).toBe(NOT_OBSERVED);
    expect(basisLabel(null, 7)).toBe(NOT_OBSERVED);
    expect(basisLabel("measured", 0)).toBe("measured");
    expect(basisLabel("measured", null)).toBe("measured");
  });

  it("keeps a canary instead of inventing the number it exists to measure", () => {
    // The cold-start case, which is the steady state: no peer durations, so
    // no plan can be quoted and the honest answer is a probe.
    const panel = summariseRouting(
      previewed({
        kind: "training",
        tasks: 4,
        venues: [venue({ id: "owned", currency: "ZC", reason: "already enrolled" })],
        eligible_machines: 1,
        unplannable_machines: 1,
        duration: null,
        canary: {
          machine_id: "m-1",
          tasks_to_calibrate: 3,
          reason: "no duration evidence exists for this job yet",
          current_basis: null,
        },
        notes: ["no duration evidence exists for any eligible machine"],
      })
    );
    expect(panel.state).toBe("routed");
    expect(panel.quotedNothing).toBe(true);
    expect(panel.plans).toEqual([]);
    expect(panel.duration).toBeNull();
    expect(panel.canary?.tasks_to_calibrate).toBe(3);
    // The venues still answer, because fit does not depend on evidence.
    expect(panel.venues).toHaveLength(1);
  });
});

describe("summariseRouting — the router did not run", () => {
  // The shipped deployment's `_degraded` answer: a 200 that omits `kind`,
  // `kind_evidence`, `venues` and `venue_excluded_machines` and zeroes the
  // counters, because nothing was computed.
  const panel = summariseRouting(
    previewed({
      kind: undefined,
      tasks: null,
      notes: [
        "routing is not configured on this deployment: the placement gates and the task expansion both live in the runtime",
      ],
    })
  );

  it("is its own state, not an error and not an empty routing answer", () => {
    expect(panel.state).toBe("unrouted");
    expect(panel.detail).toBeNull();
  });

  it("says why, in the API's own words", () => {
    expect(panel.notes[0]).toContain("routing is not configured");
  });

  it("reports no fleet counts for a fleet nothing looked at", () => {
    // The route sends 0 here because it never counted, not because the
    // answer was zero. Rendering "0 eligible machines" would be a fabricated
    // measurement.
    expect(panel.fleet).toBeNull();
    expect(panel.venues).toEqual([]);
    expect(panel.kind).toBeNull();
  });
});

describe("summariseRouting — a failed preview call", () => {
  it("renders the API's own words and claims nothing about routing", () => {
    const panel = summariseRouting({
      status: "unavailable",
      detail: "this job has no stored spec to plan against",
    });
    expect(panel.state).toBe("unavailable");
    expect(panel.detail).toBe("this job has no stored spec to plan against");
    expect(panel.venues).toEqual([]);
    expect(panel.plans).toEqual([]);
    expect(panel.fleet).toBeNull();
    expect(panel.kind).toBeNull();
  });

  it("never turns a failed read into a job with no usable venue", () => {
    // The bug this guards: `unavailable` collapsing into the same empty
    // panel as a real answer, so a 502 reads as "nowhere can run this".
    const failed = summariseRouting({ status: "unavailable", detail: "502" });
    const answered = summariseRouting(previewed({ kind: "command", venues: [] }));
    expect(failed.state).not.toBe(answered.state);
  });

  it("shows a loading read as loading, with nothing asserted yet", () => {
    const panel = summariseRouting({ status: "loading" });
    expect(panel.state).toBe("loading");
    expect(panel.quotedNothing).toBe(false);
    expect(panel.notes).toEqual([]);
  });
});

// The panel is markup-only, but two of its properties are properties of the
// RENDERED page rather than of the view model — that the two refusals do not
// read alike, and that no cell anywhere holds a combined currency figure. So
// they are asserted against the markup, the way `lib/auth-rebrand.test.ts`
// asserts copy.
function render(panel: ReturnType<typeof summariseRouting>) {
  return renderToStaticMarkup(
    createElement(RoutingCard, { panel, onRetry: () => undefined })
  );
}

describe("RoutingCard — the rendered page", () => {
  const markup = render(
    summariseRouting(
      previewed({
        kind: "finetune",
        kind_evidence: "args carry --base-model over 1 task",
        tasks: 1,
        venues: GPU_FINETUNE_VENUES,
        eligible_machines: 2,
        venue_excluded_machines: 1,
        plans: [
          plan({
            name: "cheapest",
            recommended: true,
            tasks_placed: 1,
            cost: { zc: 12, usd: 3.5, total_usd_value: 15.5 },
            makespan_seconds: null,
            basis: null,
            n: null,
          }),
        ],
      })
    )
  );

  it("shows the kind with its evidence", () => {
    expect(markup).toContain("finetune");
    expect(markup).toContain("--base-model");
  });

  it("names all four venues, refused ones included", () => {
    expect(markup).toContain("Alibaba FC Agent Sandbox");
    expect(markup).toContain("Alibaba FC serverless GPU");
    expect(markup).toContain("RunPod");
    expect(markup).toContain("Your machines");
  });

  it("gives the two refusals visibly different words on the page", () => {
    expect(markup).toContain("Can&#x27;t run this work");
    expect(markup).toContain("No capacity we can reach");
  });

  it("prints each venue's reason verbatim", () => {
    expect(markup).toContain("2 vCPU, 2 GB RAM and no GPU");
    expect(markup).toContain("Not integrated, so it can hold no work today.");
  });

  it("renders an unobserved makespan as unobserved, never as 0s", () => {
    expect(markup).toContain(NOT_OBSERVED);
    expect(markup).not.toContain(">0s<");
  });

  it("keeps the plan table to six source-settlement cells", () => {
    const table = markup.match(/<table[\s\S]*?<\/table>/)?.[0] ?? "";
    const headerLabels = Array.from(
      table.matchAll(/<th\b[^>]*>(.*?)<\/th>/g),
      ([, label]) => label
    );
    const body = table.match(/<tbody\b[^>]*>([\s\S]*?)<\/tbody>/)?.[1] ?? "";
    const planRows = Array.from(body.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/g));

    expect(table).not.toBe("");
    expect(headerLabels).toEqual([
      "Plan",
      "ZC",
      "USD",
      "Finishes in",
      "Machines",
      "Evidence",
    ]);
    expect(planRows).toHaveLength(1);
    expect(planRows[0][1].match(/<td\b/g)).toHaveLength(6);
    expect(headerLabels.join(" ")).not.toMatch(/\b(cash|equivalent|total)\b/i);
    expect(table).not.toContain("$");
  });

  it("says a failed read failed, in the API's own words", () => {
    const failed = render(
      summariseRouting({
        status: "unavailable",
        detail: "this job has no stored spec to plan against",
      })
    );
    expect(failed).toContain("Couldn&#x27;t read how this job routes.");
    expect(failed).toContain("this job has no stored spec to plan against");
    // Nothing about venues, and above all no claim that none can run it.
    expect(failed).not.toContain("Can&#x27;t run this work");
    expect(failed).not.toContain("No capacity we can reach");
  });
});

describe("job-routing stays pure", () => {
  it("gives the same answer twice for the same read", () => {
    const read = previewed({
      kind: "hpo",
      venues: GPU_FINETUNE_VENUES,
      plans: [
        plan({
          name: "cheapest",
          cost: { zc: 1, usd: 2, total_usd_value: 3 },
        }),
      ],
    });
    expect(summariseRouting(read)).toEqual(summariseRouting(read));
  });
});
