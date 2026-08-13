import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
import { it } from "vitest";

import { Button } from "@/components/ui/button";
import { PageShell } from "@/components/shell/PageShell";
import { PageHeader } from "@/components/shell/PageHeader";
import { StatePanel } from "@/components/shell/StatePanel";
import type { PanelState } from "@/lib/console/panel-state";

/**
 * The four panel states, side by side, because the whole point of the contract
 * is that they are DIFFERENT and that is a claim only the eye can settle.
 *
 * `empty` and `unreadable` being visually distinguishable is a house rule
 * ("there is nothing here" and "could not read this" are different sentences),
 * and it is the rule most likely to be lost in a visual pass. A test can assert
 * the strings differ; only a render shows whether a reader would notice.
 *
 * See `preview/console-primitives.render.tsx` for why this harness exists at
 * all — no session in this workspace can sign in, so no console route can be
 * opened.
 *
 *   npx vitest run --config preview/vitest.preview.config.ts
 */
/**
 * `next/font` defines `--font-instrument-sans` and `--font-geist-mono` on the
 * <html> element in `app/layout.tsx`, which a static render never runs — so
 * without this the harness draws EVERY typeface as the browser default and a
 * mono/sans distinction cannot be judged at all. That is not a cosmetic gap:
 * it silently hid a real bug in `PageHeader`'s identifier tone, because the
 * broken and the correct render looked identical.
 *
 * Generic families rather than the real webfonts — the metrics differ, but
 * the sans/mono distinction the console relies on is faithful.
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

interface Machine {
  id: string;
  venue: string;
}

/** Built here in the harness, never imported from the app: the honesty rules
 * forbid fixture-shaped literals in the repo, and a preview is the one place
 * a shape has to be invented to be drawn at all. */
function machines(): Machine[] {
  return [
    { id: "node-a", venue: "runpod" },
    { id: "node-b", venue: "local" },
  ];
}

const STATES: Array<{ label: string; state: PanelState<Machine[]> }> = [
  { label: "loading", state: { kind: "loading" } },
  { label: "present", state: { kind: "present", data: machines() } },
  { label: "empty", state: { kind: "empty" } },
  {
    label: "unreadable",
    state: { kind: "unreadable", detail: "The API returned 502." },
  },
];

function Panels() {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
        gap: "1.25rem",
      }}
    >
      {STATES.map(({ label, state }) => (
        <div key={label}>
          <p className="label-caps" style={{ marginBottom: "0.5rem" }}>
            {label}
          </p>
          <div className="panel">
            <StatePanel
              state={state}
              label="your machines"
              empty={{
                title: "No machines yet",
                description: "Add a machine to run jobs in this workspace.",
              }}
              // A retry handler, so the `Button` primitive actually renders:
              // the outline variant is the one surface of this panel that
              // comes from `components/ui/button.tsx` rather than from here.
              unreadable={{ retry: () => {}, retryLabel: "Try again" }}
            >
              {(rows) => (
                <ul style={{ display: "grid", gap: "0.35rem" }}>
                  {rows.map((m) => (
                    <li key={m.id} className="font-mono text-sm">
                      {m.id} · {m.venue}
                    </li>
                  ))}
                </ul>
              )}
            </StatePanel>
          </div>
        </div>
      ))}
    </div>
  );
}

function Gallery() {
  return (
    <div className="bg-cream text-ink" style={{ minHeight: "100vh" }}>
      {/* `width` is required by design — PageShell has no default, so that
          every page states its column. "Machines" is a record table, which
          `lib/console/page-width.ts` files as `wide`. */}
      <PageShell width="wide">
        <PageHeader
          title="Machines"
          description="Every machine registered to this workspace."
        />
        <p className="meta" style={{ margin: "0 0 1.5rem" }}>
          The four panel states, rendered from source. No live data.
        </p>
        <Panels />

        {/* The PageHeader surfaces the header above does not exercise: a back
            link, an actions slot, a meta line, and the one enumerated
            heading exception — `titleTone="identifier"` for a name a machine
            emitted, which switches to the mono face and truncates. */}
        <hr className="rule-fade" style={{ margin: "2.5rem 0" }} />
        <div style={{ maxWidth: "34rem" }}>
        <PageHeader
          back={{ href: "#", label: "Jobs" }}
          title="resnet50-cifar-federated-run-0007"
          titleTone="identifier"
          meta="job_01H9QK · federated · 4 tasks"
          actions={
            <>
              <Button variant="outline" size="sm">
                Cancel
              </Button>
              <Button size="sm">Re-run</Button>
            </>
          }
        />
        </div>
      </PageShell>
    </div>
  );
}

it("writes the console shell preview", async () => {
  const css = await compiledCss();
  const body = renderToStaticMarkup(<Gallery />);
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Console shell preview</title><style>${FONT_VARS}</style><style>${css}</style></head>
<body>${body}</body></html>`;

  mkdirSync(outDir, { recursive: true });
  const out = path.join(outDir, "console-shell.html");
  writeFileSync(out, html, "utf8");
  // eslint-disable-next-line no-console
  console.log(`preview written: ${out}`);
}, 60_000);
