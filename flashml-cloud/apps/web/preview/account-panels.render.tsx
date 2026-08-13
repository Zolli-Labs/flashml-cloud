import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
import { it } from "vitest";

import { EnrolInstructions } from "@/components/machines/EnrolInstructions";
import { PageHeader } from "@/components/shell/PageHeader";
import { PageShell } from "@/components/shell/PageShell";
import { StatePanel } from "@/components/shell/StatePanel";
import { Button } from "@/components/ui/button";
import type { PanelState } from "@/lib/console/panel-state";

/**
 * The states the account sweep CHANGED, rendered so they can be looked at.
 *
 * Two things here are not checkable by any test in the suite and were the
 * real reason this file exists:
 *
 *  1. `/account/machines`'s empty state used to be a hand-written block that
 *     stacked a heading, a sentence and `EnrolInstructions` in a centred
 *     column. It is now `StatePanel`'s `empty.action` slot, which renders
 *     inside `flex flex-col items-center … text-center`. `EnrolInstructions`
 *     carries its own `w-full max-w-xl text-left`, so it SHOULD survive that
 *     — but "should" is exactly the kind of claim that needs a picture, since
 *     a centred code block with three commands in it would be a visible
 *     regression that `tsc` and `vitest` both pass happily.
 *
 *  2. The account page's own panels put a `StatePanel` INSIDE a `.panel`, and
 *     cancel its inset with `px-0 sm:px-0` because the panel already has
 *     `p-5`. Whether that lands flush is a measurement, not an opinion.
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

/** Built here, never imported: the honesty rules forbid fixture-shaped
 * literals in the app, and a preview is the one place a shape has to be
 * invented to be drawn at all. */
function storage(): { usedLabel: string } {
  return { usedLabel: "1.4 GB" };
}

const ACCOUNT_STATES: Array<{
  label: string;
  state: PanelState<{ usedLabel: string }>;
}> = [
  { label: "loading", state: { kind: "loading" } },
  { label: "present", state: { kind: "present", data: storage() } },
  {
    label: "unreadable",
    state: {
      kind: "unreadable",
      detail: "Couldn't load your storage usage.",
    },
  },
];

/** The account page's panel shape: a `.panel` with its own heading, and a
 * `StatePanel` inside it whose inset is cancelled. */
function StoragePanels() {
  return (
    <div className="grid gap-5 sm:grid-cols-3">
      {ACCOUNT_STATES.map(({ label, state }) => (
        <div key={label}>
          <p className="label-caps mb-2">{label}</p>
          <section className="panel p-5">
            <h2 className="text-sm font-semibold">Storage</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Artifacts and checkpoints kept for jobs this account submitted.
            </p>
            <StatePanel
              state={state}
              className="mt-4 px-0 sm:px-0"
              loadingRows={1}
              label="your storage usage"
              empty={{ title: "No storage figures for this account." }}
              unreadable={{ retry: () => {} }}
            >
              {(s) => (
                <div className="mt-4">
                  <span className="metric-value text-lg">{s.usedLabel}</span>{" "}
                  <span className="text-sm text-muted-foreground">
                    of 5 GB
                  </span>
                </div>
              )}
            </StatePanel>
          </section>
        </div>
      ))}
    </div>
  );
}

function Gallery() {
  return (
    <div className="bg-cream text-ink" style={{ minHeight: "100vh" }}>
      <PageShell width="wide">
        <PageHeader
          title="My Machines"
          description="Machines you own. Tick one into a Workspace to let your workspace members place jobs on it."
          actions={
            <>
              <Button variant="ghost" size="icon-sm" aria-label="Refresh">
                <span>↻</span>
              </Button>
              <Button>Add a Machine</Button>
            </>
          }
        />

        <p className="meta mt-6 mb-2">
          machines · empty (EnrolInstructions in StatePanel&apos;s action slot)
        </p>
        <StatePanel
          state={{ kind: "empty" }}
          label="your Machines"
          empty={{
            title: "No machines yet",
            description:
              "Run these commands on the machine you want to connect. It can be this one.",
            action: <EnrolInstructions base="https://api.example.test" />,
          }}
          unreadable={{ retry: () => {} }}
        >
          {() => null}
        </StatePanel>

        <hr className="rule-fade" style={{ margin: "2.5rem 0" }} />

        <p className="meta mb-3">
          account · StatePanel nested inside a `.panel` with its inset cancelled
        </p>
        <StoragePanels />
      </PageShell>
    </div>
  );
}

it("writes the account panels preview", async () => {
  const css = await compiledCss();
  const body = renderToStaticMarkup(<Gallery />);
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Account panels preview</title><style>${FONT_VARS}</style><style>${css}</style></head>
<body>${body}</body></html>`;

  mkdirSync(outDir, { recursive: true });
  const out = path.join(outDir, "account-panels.html");
  writeFileSync(out, html, "utf8");
  console.log(`preview written: ${out}`);
}, 60_000);
