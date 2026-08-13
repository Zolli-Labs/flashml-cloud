import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// ArtifactsCard's download buttons ask Next for the router; there is no app
// router in a bare SSR render.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: () => {} }),
  usePathname: () => "/jobs/job-1",
}));

import { RoutingCard } from "@/components/jobs/RoutingCard";
import { TradeoffCard } from "@/components/jobs/TradeoffCard";
import { ArtifactsCard } from "@/components/jobs/ArtifactsCard";
import { LedgerView } from "@/components/jobs/LedgerView";
import { summariseRouting } from "@/lib/job-routing";
import { summariseTradeoff } from "@/lib/job-tradeoff";
import { summariseJobArtifacts } from "@/lib/job-artifacts";
import type { JobEvent } from "@/lib/cloud-api";

const html = (node: React.ReactElement) =>
  renderToStaticMarkup(node).replace(/&#x27;/g, "'").replace(/&amp;/g, "&");
const countOf = (s: string, needle: string) => s.split(needle).length - 1;

const VENUES = [
  {
    id: "fc-sandbox",
    display: "Function Compute sandbox",
    currency: "ZC",
    suited: false,
    acquirable: true,
    reason:
      "2 vCPU, 2 GB, no GPU. This job asks for a CUDA device, so nothing here can run it. No price changes that fact.",
  },
  {
    id: "fc-gpu",
    display: "Function Compute GPU",
    currency: "USD",
    suited: true,
    acquirable: false,
    reason:
      "A real fit for this work. There is no integration behind it yet, so we cannot obtain capacity here.",
  },
  {
    id: "pool",
    display: "Workspace machines",
    currency: "ZC",
    suited: true,
    acquirable: true,
    reason: "Three machines in this workspace match the job's resource ask.",
  },
  {
    id: "runpod",
    display: "RunPod",
    currency: "USD",
    suited: true,
    acquirable: true,
    reason: "Priced at 0.38 USD per hour. Captured 2026-08-12.",
  },
  {
    id: "ecs",
    display: "ECS spot",
    currency: "USD",
    suited: false,
    acquirable: false,
    reason: "No GPU sku is enabled on this account.",
  },
];

const UNPRICED_PLAN = (name: string, recommended: boolean) => ({
  name,
  recommended,
  tasks_placed: 4,
  tasks_unplaced: 0,
  cost: { zc: 0, usd: 0, total_usd_value: 0 },
  makespan_seconds: null,
  deadline_met: null,
  achievable_deadline_seconds: null,
  basis: null,
  n: null,
  dominated_by: null,
  allocations: [{}, {}],
  notes: [`${name} placed every task on workspace machines.`],
});

describe("Placement — every venue sentence survives the fold", () => {
  const panel = summariseRouting({
    status: "previewed",
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    preview: {
      kind: "gpu-finetune",
      kind_evidence: "The spec asks for one CUDA device per task.",
      tasks: 4,
      venues: VENUES,
      plans: [UNPRICED_PLAN("cheapest", true), UNPRICED_PLAN("fastest", false)],
      recommended: "cheapest",
      duration: null,
      canary: null,
      eligible_machines: 3,
      excluded_machines: 1,
      venue_excluded_machines: 2,
      unplannable_machines: 3,
      notes: ["Routing ran against 9 reachable machines."],
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any,
  });

  const out = html(<RoutingCard panel={panel} onRetry={() => {}} />);

  it("keeps every venue's full reason, verbatim", () => {
    for (const v of VENUES) expect(out).toContain(v.reason);
  });

  it("leads with the first sentence of each reason", () => {
    expect(out).toContain("2 vCPU, 2 GB, no GPU.");
    // …and did not split the captured price on its decimal point
    expect(out).toContain("Priced at 0.38 USD per hour.");
  });

  it("keeps every verdict definition and the second refusal", () => {
    expect(out).toContain("This venue physically cannot run this job.");
    expect(out).toContain("Suited to this work, and we can obtain capacity here.");
    expect(out).toContain("that is a gap on our side, not a verdict on the venue");
    expect(out).toContain("We also have no way to obtain capacity here.");
    expect(out).toContain("What the verdicts mean");
  });

  it("folds the unpriced plan table but still renders every cell of it", () => {
    expect(out).toContain("No per-task duration observed yet");
    expect(out).toContain("the plan table anyway");
    expect(out).toContain("<table");
    expect(out).toContain("cheapest");
    expect(out).toContain("fastest");
    expect(out).toContain("recommended");
    expect(out).toContain("cheapest placed every task on workspace machines.");
    expect(out).toContain("there is no balanced plan to show");
    expect(out).toContain("not observed");
  });

  it("puts one disclosure per venue plus the legend plus the table", () => {
    expect(countOf(out, "<details")).toBe(VENUES.length + 2);
  });
});

describe("Placement — the unobserved trade-off curve", () => {
  const point = (slots: number, code: string) => ({
    total_slots: slots,
    owned_slots: 1,
    rented_slots: slots - 1,
    finish_seconds: null,
    zc_cost: 0,
    usd_cost: null,
    total_usd_value: null,
    advice_code: code,
  });

  const panel = summariseTradeoff({
    status: "read",
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    tradeoff: {
      tasks: 4,
      task_seconds: null,
      duration: null,
      owned: {
        machines: 1,
        slots: 1,
        reachable_machines: 3,
        ask_zc_per_hour: 0,
      },
      renting: null,
      points: [
        point(1, "baseline"),
        point(2, "no_marginal_gain"),
        point(3, "no_marginal_gain"),
        point(4, "no_marginal_gain"),
        point(5, "beyond_task_count"),
      ],
      notes: ["No per-task duration has been observed for this spec."],
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any,
  });

  const out = html(<TradeoffCard panel={panel} onRetry={() => {}} />);

  it("says it once and keeps the table one click away", () => {
    expect(out).toContain("No per-task duration observed yet");
    expect(out).toContain("the fleet sizes and verdicts are real");
    expect(out).toContain("<table");
    expect(out).toContain("Total (ZC+USD)");
  });

  it("keeps every verdict definition that is on the curve", () => {
    expect(out).toContain("What the verdicts mean");
    expect(out).toContain("The machines this account already reaches, at no cost.");
    expect(out).toContain("bought and unused");
    expect(out).toContain("this machine is paid for and idle");
  });
});

describe("Progress — 54 artifact rows behind 9 lines", () => {
  const artifacts = Array.from({ length: 9 }, (_, t) => {
    const task = `task-${String(t).padStart(3, "0")}`;
    return [
      { key: `${task}/result.json`, size_bytes: 1024 },
      { key: `${task}/stdout.txt`, size_bytes: 2048 },
      { key: `${task}/stderr.txt`, size_bytes: 512 },
      ...Array.from({ length: 3 }, (_, s) => ({
        key: `${task}/ckpt/step-${s + 1}.json`,
        size_bytes: 64,
      })),
    ];
  }).flat();

  const tasks = Array.from({ length: 9 }, (_, t) => ({
    task_id: `task-${String(t).padStart(3, "0")}`,
    state: t === 3 ? "FAILED" : "COMPLETED",
    attempts: 1,
    max_attempts: 3,
    node_id: null,
    deadline: null,
  }));

  const panel = summariseJobArtifacts({
    read: {
      status: "listed",
      listing: { artifacts, storage: "oss", mirrored_at: "2026-08-13T10:00:00Z" },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
    } as any,
    jobState: "SUCCEEDED",
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    tasks: tasks as any,
  });

  const out = html(
    <ArtifactsCard
      jobId="job-1"
      panel={panel}
      onCleared={() => {}}
      onRetry={() => {}}
    />
  );

  it("still renders all 54 file rows", () => {
    expect(artifacts).toHaveLength(54);
    for (const a of artifacts) expect(out).toContain(a.key);
  });

  it("folds them into one line per task, stating what each holds", () => {
    expect(countOf(out, "<details")).toBe(9);
    expect(out).toContain("3 files · 3 checkpoints · 3.7 KiB");
  });

  it("opens the failed task by default and keeps its callout", () => {
    expect(countOf(out, "<details")).toBe(9);
    expect(countOf(out, 'open=""')).toBe(1);
    expect(out).toContain("This task failed. Its stdout and stderr are below.");
  });

  it("keeps the checkpoint explanation and the job-level total", () => {
    expect(out).toContain("Written while the task ran");
    expect(out).toContain("54 files");
    expect(out).toContain("Mirrored to Alibaba OSS.");
  });
});

describe("Ledger — a run of 23 renewals behind one row", () => {
  const at = (m: number, s: number) =>
    `2026-08-13T10:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}Z`;

  const events: JobEvent[] = [
    {
      job_id: "job-1",
      type: "LEASE_CLAIMED",
      timestamp: at(20, 0),
      source: "coordinator",
      message: "fn-98ca4710 claimed task-000",
      data: { node_id: "fn-98ca4710", task_id: "task-000" },
    },
    ...Array.from({ length: 23 }, (_, i) => ({
      job_id: "job-1",
      type: "LEASE_RENEWED",
      timestamp: at(20 + Math.floor(i / 4), (i % 4) * 15),
      source: "coordinator",
      message: `renewal ${i} of task-000`,
      data: { node_id: "fn-98ca4710", task_id: "task-000" },
    })),
    {
      job_id: "job-1",
      type: "TASK_COMMIT_ACCEPTED",
      timestamp: at(26, 0),
      source: "coordinator",
      message: "accepted task-000",
      data: { node_id: "fn-98ca4710", task_id: "task-000" },
    },
  ];

  const out = html(<LedgerView events={events} />);

  it("collapses the renewals into one row that says how many and over what", () => {
    expect(out).toContain("×23");
    expect(countOf(out, "LEASE_RENEWED")).toBe(24); // the run row + 23 rows inside
    expect(countOf(out, "<details")).toBe(1);
  });

  it("keeps every renewal message verbatim inside the fold", () => {
    for (let i = 0; i < 23; i += 1) {
      expect(out).toContain(`renewal ${i} of task-000`);
    }
  });

  it("never folds a claim or an acceptance", () => {
    expect(countOf(out, "LEASE_CLAIMED")).toBe(1);
    expect(countOf(out, "TASK_COMMIT_ACCEPTED")).toBe(1);
    expect(out).toContain("fn-98ca4710 claimed task-000");
  });

  it("offers the four filters with their counts", () => {
    expect(out).toContain("All");
    expect(out).toContain("Commits");
    expect(out).toContain("Leases");
    expect(out).toContain("Failures");
    expect(out).toContain("25 events");
  });
});
