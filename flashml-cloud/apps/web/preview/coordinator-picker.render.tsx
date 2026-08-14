import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
import { describe, expect, it } from "vitest";

import { CoordinatorChip } from "@/components/jobs/CoordinatorChip";
import { StateBadge } from "@/components/jobs/StateBadge";
import { CoordinatorPicker } from "@/components/deploy/CoordinatorPicker";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/**
 * The render/FC control-plane picker (submit form) and the chip that shows
 * which one served a job (job rows, job detail header) — drawn together
 * because the point of both is a side-by-side comparison, and no session
 * here can sign in to check that the two actually agree on wording. See
 * `preview/workspace-tables.render.tsx` for why this file exists at all.
 *
 *   npx vitest run --config preview/vitest.preview.config.ts
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

function Gallery() {
  return (
    <div className="bg-cream text-ink" style={{ minHeight: "100vh" }}>
      {/* `max-w-6xl` on the outer column, matching `PageShell width="wide"`
          (`lib/console/page-width.ts`) — the width `/w/[poolId]/jobs`
          actually renders at. The picker/chip sections below narrow
          themselves back to `max-w-3xl` (that page's own `reading` width)
          rather than the whole gallery being capped there, which would
          force a false horizontal scroll on the six-column table further
          down that the real page never has at its real width. */}
      <div className="mx-auto max-w-6xl space-y-10 px-4 py-8 sm:px-6">
        <section className="max-w-3xl">
          <p className="label-caps">Submit form — CoordinatorPicker</p>
          <div className="mt-3 max-w-sm space-y-3">
            <p className="text-xs text-muted-foreground">Render selected (default)</p>
            <CoordinatorPicker value="render" onChange={() => {}} />
            <p className="text-xs text-muted-foreground">Function Compute selected</p>
            <CoordinatorPicker value="fc" onChange={() => {}} />
          </div>
        </section>

        <section className="max-w-3xl">
          <p className="label-caps">Job row / job detail — CoordinatorChip</p>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <CoordinatorChip coordinator="render" />
            <CoordinatorChip coordinator="fc" />
            {/* Absent field — an API that has not shipped the column yet, or
                a job that predates it. Must read exactly like "render",
                never as a third unlabelled state. */}
            <CoordinatorChip coordinator={undefined} />
            <CoordinatorChip coordinator={null} />
          </div>
        </section>

        <section>
          <p className="label-caps">Jobs list — the row in context</p>
          {/* Same six-column shape as
              `app/(console)/w/[poolId]/jobs/page.tsx`: name/id, submitted
              by, mode, started, coordinator, state. Fixture rows only — the
              point is to see the new column sit beside the other five at
              real width, not to re-derive the page's own logic. */}
          <div className="mt-3 overflow-x-auto rounded-lg border border-border">
            <Table className="min-w-[720px]">
              <TableHeader>
                <TableRow>
                  {["Job", "Submitted by", "Mode", "Started", "Coordinator", "State"].map(
                    (h, i) => (
                      <TableHead key={h} className={i === 5 ? "text-right" : undefined}>
                        {h}
                      </TableHead>
                    )
                  )}
                </TableRow>
              </TableHeader>
              <TableBody>
                {[
                  {
                    id: "job-render-1",
                    name: "cifar10-baseline",
                    by: "Priya",
                    mode: "independent",
                    started: "8/14/2026, 9:02:00 AM",
                    coordinator: "render",
                    state: "RUNNING" as const,
                  },
                  {
                    id: "job-fc-1",
                    name: "cifar10-fc-comparison",
                    by: "Priya",
                    mode: "independent",
                    started: "8/14/2026, 9:03:10 AM",
                    coordinator: "fc",
                    state: "SUCCEEDED" as const,
                  },
                  {
                    // Predates the field — must render as Render, same as
                    // an explicit "render" row above it.
                    id: "job-legacy",
                    name: "pre-comparison-job",
                    by: null,
                    mode: "federated",
                    started: "8/10/2026, 4:15:00 PM",
                    coordinator: undefined,
                    state: "FAILED" as const,
                  },
                ].map((j) => (
                  <TableRow key={j.id}>
                    <TableCell>
                      <span className="block truncate font-mono text-sm">{j.name}</span>
                      <span className="meta block truncate">{j.id}</span>
                    </TableCell>
                    <TableCell className="meta">{j.by ?? "—"}</TableCell>
                    <TableCell className="meta">{j.mode}</TableCell>
                    <TableCell className="meta">{j.started}</TableCell>
                    <TableCell>
                      <CoordinatorChip coordinator={j.coordinator} />
                    </TableCell>
                    <TableCell className="text-right">
                      <StateBadge state={j.state} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </section>
      </div>
    </div>
  );
}

describe("Coordinator picker + chip", () => {
  it("labels both picker options for a person, not the wire value", () => {
    const out = renderToStaticMarkup(<Gallery />);
    expect(out).toContain("Render (private service)");
    expect(out).toContain("Function Compute (Singapore)");
    expect(out).not.toContain('>render<');
    expect(out).not.toContain('>fc<');
  });

  it("marks exactly one option checked per picker instance", () => {
    const renderPicker = renderToStaticMarkup(
      <CoordinatorPicker value="render" onChange={() => {}} />
    );
    expect((renderPicker.match(/aria-checked="true"/g) ?? []).length).toBe(1);
    expect(renderPicker).toContain('aria-checked="true"');

    const fcPicker = renderToStaticMarkup(
      <CoordinatorPicker value="fc" onChange={() => {}} />
    );
    expect((fcPicker.match(/aria-checked="true"/g) ?? []).length).toBe(1);
  });

  it("chip labels are short and scannable, not the picker's long form", () => {
    const out = renderToStaticMarkup(
      <>
        <CoordinatorChip coordinator="render" />
        <CoordinatorChip coordinator="fc" />
      </>
    );
    expect(out).toContain(">Render<");
    expect(out).toContain(">Function Compute<");
    expect(out).not.toContain("private service");
    expect(out).not.toContain("Singapore");
  });

  it("an absent or null coordinator reads exactly like Render", () => {
    const withValue = renderToStaticMarkup(<CoordinatorChip coordinator="render" />);
    const absent = renderToStaticMarkup(<CoordinatorChip coordinator={undefined} />);
    const nullish = renderToStaticMarkup(<CoordinatorChip coordinator={null} />);
    expect(absent).toBe(withValue);
    expect(nullish).toBe(withValue);
  });

  it("sits as a sixth column in the jobs table, legacy rows reading as Render", () => {
    const out = renderToStaticMarkup(<Gallery />);
    expect(out).toContain(">Coordinator<");
    expect(out).toContain("cifar10-fc-comparison");
    // Standalone chips section: render, fc, undefined, null → 3 Render + 1
    // Function Compute. Table section: job-render-1, job-fc-1, job-legacy
    // (absent field) → 2 Render + 1 Function Compute. Combined: 5 and 2.
    expect((out.match(/>Render</g) ?? []).length).toBe(5);
    expect((out.match(/>Function Compute</g) ?? []).length).toBe(2);
  });

  it("the two chip states are distinguishable by icon shape, not colour alone", () => {
    const renderChip = renderToStaticMarkup(<CoordinatorChip coordinator="render" />);
    const fcChip = renderToStaticMarkup(<CoordinatorChip coordinator="fc" />);
    // Phosphor's HardDrives and Lightning glyphs draw different <path> data —
    // asserting the SVGs differ is the cheapest proxy this harness has for
    // "not the same shape".
    const svgOf = (html: string) => html.match(/<svg[\s\S]*?<\/svg>/)?.[0];
    expect(svgOf(renderChip)).not.toBe(svgOf(fcChip));
  });
});

it("writes the coordinator picker + chip preview", async () => {
  const css = await compiledCss();
  const body = renderToStaticMarkup(<Gallery />);

  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Coordinator picker + chip preview</title><style>${FONT_VARS}</style><style>${css}</style></head>
<body>${body}</body></html>`;

  mkdirSync(outDir, { recursive: true });
  const out = path.join(outDir, "coordinator-picker.html");
  writeFileSync(out, html, "utf8");
  console.log(`preview written: ${out}`);
}, 60_000);
