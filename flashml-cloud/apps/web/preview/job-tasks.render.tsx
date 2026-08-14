import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
import { expect, it } from "vitest";

import { TasksTable } from "@/components/jobs/TasksTable";
import { deriveAttemptCoverage, deriveAttempts } from "@/lib/job-activity";
import { taskKey } from "@/lib/task-detail";
import type { CheckpointPanel } from "@/lib/task-checkpoints";
import type { JobEvent, JobTask } from "@/lib/cloud-api";

/**
 * The Placement view's Tasks table, WITH A ROW OPEN — rendered so it can be
 * looked at without a session.
 *
 *   PREVIEW_OUT=.preview npx vitest run --config preview/vitest.preview.config.ts
 *
 * No agent working in this repo can sign in to the console, so a panel nobody
 * can open is a panel nobody has seen. This writes the table three ways to
 * `.preview/job-tasks.html`: the churned task expanded (the case the feature
 * exists for), the same table closed, and a job whose ledger carries no
 * machine ids at all — the fallback path, where the attempt total comes from
 * the coordinator's task view and the per-task history is honestly empty.
 *
 * Named `*.render.tsx` so `vitest.config.ts`'s `**\/*.test.*` glob cannot
 * collect it and inflate the suite baseline. The judgements it draws are
 * tested in `lib/task-detail.test.ts`; the assertions here are the ones only
 * rendered HTML can make.
 *
 * THE FIXTURE IS A REAL JOB'S SHAPE. `TASK` is a `/tasks` payload the leases
 * backend produced; the events mirror the ledger behind it — a coordinator
 * outage that cost two machines their leases before a third finished the
 * work. The `fn-*` ids are lease holders and are printed verbatim: they are
 * not machines in any account's list, nothing looks them up, and none of them
 * is a link.
 */
const FONT_VARS = `:root{--font-instrument-sans:ui-sans-serif,system-ui,-apple-system,sans-serif;--font-geist-mono:ui-monospace,SFMono-Regular,Menlo,monospace}`;

const webRoot = process.cwd();
const outDir = process.env.PREVIEW_OUT ?? path.join(webRoot, ".preview");

async function compiledCss(): Promise<string> {
  const globalsPath = path.join(webRoot, "app/globals.css");
  const css = readFileSync(globalsPath, "utf8");
  const result = await postcss([tailwindcss({ base: webRoot })]).process(css, {
    from: globalsPath,
  });
  return result.css;
}

const NODE_A = "fn-fa5b036f46f84538";
const NODE_B = "fn-3949134a0acd4d91";
const NODE_C = "fn-1af2567e03d744e8";

const T = (m: number, s: number) =>
  `2026-08-13T10:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}Z`;

function ev(
  type: string,
  at: string,
  data: Record<string, unknown>,
  message = ""
): JobEvent {
  return {
    job_id: "job-leases-1",
    type,
    timestamp: at,
    source: "flashruntime.leases",
    message,
    data,
  };
}

const TASKS: JobTask[] = [
  {
    task_id: "task-000",
    state: "COMPLETED",
    attempts: 3,
    max_attempts: 4,
    node_id: NODE_C,
    deadline: null,
  },
  {
    task_id: "task-001",
    state: "LEASED",
    attempts: 1,
    max_attempts: 4,
    node_id: NODE_C,
    deadline: T(9, 30),
  },
];

const EVENTS: JobEvent[] = [
  ev("TASK_CREATED", T(0, 0), { task_id: "task-000", node_id: null }, "task-000 created"),
  ev("LEASE_CLAIMED", T(0, 5), { task_id: "task-000", node_id: NODE_A }),
  ev("LEASE_RENEWED", T(0, 35), { task_id: "task-000", node_id: NODE_A }),
  ev("LEASE_RENEWED", T(1, 5), { task_id: "task-000", node_id: NODE_A }),
  ev("LEASE_EXPIRED", T(2, 5), { task_id: "task-000", node_id: NODE_A }),
  ev(
    "TASK_REQUEUED",
    T(2, 6),
    { task_id: "task-000" },
    "lease expired with no renewal; task-000 returned to the queue"
  ),
  ev("LEASE_CLAIMED", T(2, 10), { task_id: "task-000", node_id: NODE_B }),
  ev("LEASE_RENEWED", T(2, 40), { task_id: "task-000", node_id: NODE_B }),
  ev("LEASE_EXPIRED", T(3, 40), { task_id: "task-000", node_id: NODE_B }),
  ev(
    "TASK_REQUEUED",
    T(3, 41),
    { task_id: "task-000" },
    "lease expired with no renewal; task-000 returned to the queue"
  ),
  ev("LEASE_CLAIMED", T(3, 45), { task_id: "task-000", node_id: NODE_C }),
  ev("LEASE_RENEWED", T(4, 15), { task_id: "task-000", node_id: NODE_C }),
  ev("LEASE_RENEWED", T(4, 45), { task_id: "task-000", node_id: NODE_C }),
  ev("LEASE_RENEWED", T(5, 15), { task_id: "task-000", node_id: NODE_C }),
  ev("TASK_ATTEMPT_COMPLETED", T(5, 30), {
    task_id: "task-000",
    node_id: NODE_C,
  }),
  ev("LEASE_CLAIMED", T(6, 0), { task_id: "task-001", node_id: NODE_C }),
  ev("LEASE_RENEWED", T(6, 30), { task_id: "task-001", node_id: NODE_C }),
];

/** One committed checkpoint on the task still in flight, so the expanded row
 * shows what a machine dying right now would and would not cost. */
const CHECKPOINTS: CheckpointPanel = {
  state: "rows",
  rows: [
    {
      taskId: "task-001",
      taskState: "LEASED",
      kind: "committed",
      step: 40,
      committedAt: T(6, 20),
      validation: "hash_verified",
      errorDetail: null,
      attempts: 1,
      latestAttemptStartedAt: Date.parse(T(6, 0)),
      resumedFromStep: null,
    },
  ],
  ambiguityNote: null,
  resumeNote: null,
  truncationNote: null,
};

/** The fallback case: a ledger whose events name no machine at all, so
 * `deriveAttempts` returns nothing and every per-machine view is empty. The
 * coordinator's own tally still knows three attempts were spent. */
const BLIND_TASKS: JobTask[] = [
  {
    task_id: "task-000",
    state: "FAILED",
    attempts: 3,
    max_attempts: 3,
    node_id: null,
    deadline: null,
  },
];
const BLIND_EVENTS: JobEvent[] = [
  ev("TASK_CREATED", T(0, 0), { task_id: "task-000" }, "task-000 created"),
  ev("TASK_REQUEUED", T(2, 6), { task_id: "task-000" }, "task-000 requeued"),
];

/** A task no event in the ledger names. The panel has to say so rather than
 * draw an empty rail — a blank here reads as a broken console. */
const UNNAMED_TASKS: JobTask[] = [
  {
    task_id: "task-042",
    state: "PENDING",
    attempts: 0,
    max_attempts: 4,
    node_id: null,
    deadline: null,
  },
];

function Gallery() {
  const coverage = deriveAttemptCoverage(TASKS, deriveAttempts(EVENTS));
  const blindCoverage = deriveAttemptCoverage(
    BLIND_TASKS,
    deriveAttempts(BLIND_EVENTS)
  );
  return (
    <div className="bg-cream text-ink" style={{ minHeight: "100vh" }}>
      <div className="mx-auto max-w-6xl space-y-10 px-4 py-8 sm:px-6">
        <div>
          <p className="label-caps">
            Tasks — a row open on the task that changed hands twice
          </p>
          <div className="mt-3">
            <TasksTable
              tasks={TASKS}
              events={EVENTS}
              checkpoints={CHECKPOINTS}
              coverage={coverage}
              defaultExpandedKey={taskKey(TASKS[0])}
            />
          </div>
        </div>

        <div>
          <p className="label-caps">
            Tasks — the running task open, with its committed checkpoint
          </p>
          <div className="mt-3">
            <TasksTable
              tasks={TASKS}
              events={EVENTS}
              checkpoints={CHECKPOINTS}
              coverage={coverage}
              defaultExpandedKey={taskKey(TASKS[1])}
            />
          </div>
        </div>

        <div>
          <p className="label-caps">Tasks — every row closed</p>
          <div className="mt-3">
            <TasksTable
              tasks={TASKS}
              events={EVENTS}
              checkpoints={CHECKPOINTS}
              coverage={coverage}
            />
          </div>
        </div>

        <div>
          <p className="label-caps">
            Tasks — a ledger that names no machine: the count falls back to the
            coordinator, and the history says so
          </p>
          <div className="mt-3">
            <TasksTable
              tasks={BLIND_TASKS}
              events={BLIND_EVENTS}
              coverage={blindCoverage}
              defaultExpandedKey={taskKey(BLIND_TASKS[0])}
            />
          </div>
        </div>

        <div>
          <p className="label-caps">
            Tasks — a task the ledger never names: an empty state that says so,
            not a blank panel
          </p>
          <div className="mt-3">
            <TasksTable
              tasks={UNNAMED_TASKS}
              events={EVENTS}
              coverage={deriveAttemptCoverage(UNNAMED_TASKS, [])}
              defaultExpandedKey={taskKey(UNNAMED_TASKS[0])}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

it("writes the job tasks preview", async () => {
  const css = await compiledCss();
  const body = renderToStaticMarkup(<Gallery />);

  // The row is a disclosure with real button semantics, and the open panel is
  // named by the control that opened it. This is the whole accessibility
  // claim, and it is not visible in a screenshot.
  expect(body).toContain('aria-expanded="true"');
  expect(body).toContain('aria-expanded="false"');
  // Every open panel is controlled by, and named by, the row button that
  // opened it — and the ids are instance-scoped, because `aria-labelledby`
  // resolves document-wide and this page renders five of these tables.
  const controls = [...body.matchAll(/aria-controls="([^"]+)"/g)].map(
    (m) => m[1]
  );
  const labelledBy = [...body.matchAll(/aria-labelledby="([^"]+)"/g)].map(
    (m) => m[1]
  );
  expect(controls).toHaveLength(4);
  expect(new Set(controls).size).toBe(4);
  expect(new Set(labelledBy).size).toBe(4);
  for (const id of [...controls, ...labelledBy]) {
    expect(body).toContain(`id="${id}"`);
  }

  // Three machines held this task. Every id verbatim, and not one of them
  // wrapped in an anchor — they are lease holders, not machines in a list.
  for (const node of [NODE_A, NODE_B, NODE_C]) expect(body).toContain(node);
  expect(body).not.toContain(`<a href="/machines/${NODE_C}"`);

  // The story: renewals counted rather than listed, both lost leases named by
  // the coordinator's own event type, and the requeues that explain the gaps
  // between the three claims — the events the fleet-wide walk drops.
  expect(body).toContain("2 renewals");
  expect(body).toContain("3 renewals");
  expect(body).toContain("1 renewal<"); // singular, not "1 renewals"
  expect(body.split("LEASE_EXPIRED").length - 1).toBe(2);
  expect(body.split("TASK_REQUEUED").length - 1).toBe(3); // 2 here + 1 in the blind panel
  expect(body).toContain("returned to the queue");
  expect(body).toContain("TASK_ATTEMPT_COMPLETED");

  // A null deadline is an em dash. `new Date(null)` is the epoch, and the
  // symptom of getting this wrong is a confident 1970 in a lease column.
  expect(body).not.toContain("1970");

  // The checkpoint the panel is allowed to show, and only for the task the
  // read covered.
  expect(body).toContain("step 40");
  expect(body).toContain("hash_verified");

  // The fallback: no attempt could be placed on a machine, so the count comes
  // from the task view and the panel says where it came from.
  expect(body).toContain("3 attempts across 1 task, counted by the coordinator");
  // …and inside the row, both counts side by side. Neither is corrected to
  // match the other, and the ledger's 0 is not dressed up as "no attempts".
  expect(body).toContain(
    "The coordinator counts 3 attempts on this task; the ledger this console reads accounts for 0."
  );
  // The events that carry no machine are still the history — TASK_CREATED and
  // TASK_REQUEUED survive where the fleet-wide walk drops them.
  expect(body).toContain("task-000 requeued");

  // A task nothing in the ledger names gets a sentence, not a blank rail.
  expect(body).toContain("Nothing has claimed this task yet.");

  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job tasks preview</title><style>${FONT_VARS}</style><style>${css}</style></head>
<body>${body}</body></html>`;

  mkdirSync(outDir, { recursive: true });
  const out = path.join(outDir, "job-tasks.html");
  writeFileSync(out, html, "utf8");
  console.log(`preview written: ${out}`);
}, 60_000);
