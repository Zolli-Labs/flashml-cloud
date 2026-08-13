import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
import { expect, it } from "vitest";

import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/shell/PageHeader";
import { PageShell } from "@/components/shell/PageShell";
import { StatePanel } from "@/components/shell/StatePanel";
import type { PanelState } from "@/lib/console/panel-state";
import type { MetricsSummary } from "@/lib/platform-metrics";

/**
 * The states the console-page sweep ADDED, rendered so they can be looked at
 * without a session — plus two assertions about the primitives that a
 * type-check cannot make.
 *
 *   npx vitest run --config preview/vitest.preview.config.ts
 *
 * Named `*.render.tsx` so `vitest.config.ts`'s `**\/*.test.*` glob cannot
 * collect it and inflate the suite baseline.
 */
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

/**
 * Built here, never imported: the honesty rules forbid fixture-shaped
 * literals in the app, and a preview is the one place a shape has to be
 * invented to be drawn at all. Every derived field is `measured: false`
 * because that is what the API returns for every account today.
 */
function summary(): MetricsSummary {
  const unmeasured = (label: string) => ({
    label,
    measured: false,
    display: "Not measured yet",
  });
  return {
    windowDays: 30,
    jobCounts: [
      { label: "Total", value: 4 },
      { label: "Succeeded", value: 3 },
      { label: "Partial", value: 0 },
      { label: "Failed", value: 1 },
      { label: "In flight", value: 0 },
    ],
    taskCounts: [
      { label: "Attempted", value: 12 },
      { label: "Resolved", value: 12 },
      { label: "Accepted", value: 11 },
    ],
    machinesContributing: 2,
    goodput: unmeasured("Goodput"),
    lostTaskTime: unmeasured("Lost task time"),
    mttr: unmeasured("Mean time to recovery"),
    mttd: unmeasured("Mean time to detect"),
  };
}

const METRICS_STATES: Array<{ label: string; state: PanelState<MetricsSummary> }> =
  [
    { label: "loading — the state this sweep added", state: { kind: "loading" } },
    { label: "present", state: { kind: "present", data: summary() } },
    {
      label: "unreadable",
      state: {
        kind: "unreadable",
        detail: "The coordinator did not answer in time.",
      },
    },
  ];

function MetricsStates() {
  return (
    <>
      {METRICS_STATES.map(({ label, state }) => (
        <div key={label} style={{ marginBottom: "2rem" }}>
          <p className="label-caps" style={{ marginBottom: "0.5rem" }}>
            {label}
          </p>
          <StatePanel
            state={state}
            label="this account's reliability numbers"
            loadingRows={3}
            empty={{ title: "Nothing measured in this window." }}
            unreadable={{ retry: () => {} }}
          >
            {(s) => (
              <div className="grid gap-3 sm:grid-cols-4">
                {s.jobCounts.map((c) => (
                  <div
                    key={c.label}
                    className="rounded-lg border border-border bg-surface px-4 py-3.5"
                  >
                    <div className="metric-value text-2xl">{c.value}</div>
                    <div className="label-caps mt-1">{c.label}</div>
                  </div>
                ))}
              </div>
            )}
          </StatePanel>
        </div>
      ))}
    </>
  );
}

function Gallery() {
  return (
    <div className="bg-cream text-ink" style={{ minHeight: "100vh" }}>
      <PageShell width="standard">
        <PageHeader
          title="Reliability"
          description="The metrics page's panel states. No live data."
        />
        <div style={{ marginTop: "2rem" }}>
          <MetricsStates />
        </div>
      </PageShell>
    </div>
  );
}

it("writes the console pages preview", async () => {
  const css = await compiledCss();
  const body = renderToStaticMarkup(<Gallery />);
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Console pages preview</title><style>${css}</style></head>
<body>${body}</body></html>`;

  mkdirSync(outDir, { recursive: true });
  const out = path.join(outDir, "console-pages.html");
  writeFileSync(out, html, "utf8");
   
  console.log(`preview written: ${out}`);
}, 60_000);

/**
 * `components/ui/button.tsx` wraps Base UI's `Button`, whose `useButton`
 * hook merges `{ type: 'button' }` into the props it returns. Three pages in
 * this sweep put a `<Button type="submit">` inside a `<form>`; if that merge
 * won, pressing the button would stop submitting the form and the failure
 * would be invisible to `tsc` (the prop type-checks either way) and to
 * `lint`.
 *
 * The merge order says external props come last, so `submit` should survive.
 * That is a reading of minified vendor code, which is exactly the kind of
 * thing to check rather than believe.
 */
it("keeps type=submit on a Button inside a form", () => {
  const html = renderToStaticMarkup(<Button type="submit">Create</Button>);
  expect(html).toContain('type="submit"');
});

it("still defaults to type=button when nothing is asked for", () => {
  const html = renderToStaticMarkup(<Button>Approve</Button>);
  expect(html).toContain('type="button"');
});
