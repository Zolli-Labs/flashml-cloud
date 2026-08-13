import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
import { expect, it } from "vitest";

import { MemberTable } from "@/components/workspace/MemberTable";
import { PoolFleetTable } from "@/components/workspace/PoolFleetTable";
import type { PoolMachine, PoolMember } from "@/lib/cloud-api";

/**
 * The two workspace tables after their conversion from hand-written
 * `<table>` to `components/ui/table.tsx`.
 *
 * WHY THIS EXISTS. The conversion was supposed to move no pixels: the
 * primitive's cell padding was retuned to the console's own modal treatment
 * (`px-3 py-2.5` cells, `px-3 py-2` heads) rather than the tables being
 * restyled to the primitive's stock `p-2`. That is a claim about rendered
 * output, and no session here can sign in to look at the real page — so the
 * assertions below check the emitted HTML instead, and the written file is
 * there to be opened by whoever can.
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

/** Built here in the harness, never imported from the app: the honesty rules
 * forbid fixture-shaped literals in the repo, and a preview is the one place
 * a shape has to be invented to be drawn at all. */
function members(): PoolMember[] {
  const hourAgo = new Date(Date.now() - 3_600_000).toISOString();
  return [
    {
      user_id: "u-owner",
      display_name: "Priya",
      joined_at: hourAgo,
      machine_count: 2,
      machines_online: 1,
    },
    {
      // `display_name: null` is the case `memberName` exists for — a member
      // who joined before setting one. Drawn so the fallback is visible.
      user_id: "u-second",
      display_name: null,
      joined_at: hourAgo,
      machine_count: 0,
      machines_online: 0,
    },
  ];
}

function fleet(): PoolMachine[] {
  const justNow = new Date(Date.now() - 5_000).toISOString();
  const longAgo = new Date(Date.now() - 86_400_000).toISOString();
  return [
    {
      id: "m-live",
      node_id: "node-live",
      name: "studio-3090",
      owner_id: "u-owner",
      owner_display_name: "Priya",
      status: "active",
      last_seen_at: justNow,
      sandbox_capable: true,
      argv_capable: true,
      unsandboxed_argv_capable: false,
      module_capable: true,
    },
    {
      // Revoked, so the badge that distinguishes "token destroyed" from
      // "merely asleep" renders — the whole reason that column exists.
      id: "m-dead",
      node_id: "node-dead",
      name: null,
      owner_id: "u-second",
      owner_display_name: null,
      status: "revoked",
      last_seen_at: longAgo,
      sandbox_capable: false,
      argv_capable: false,
      unsandboxed_argv_capable: false,
      module_capable: false,
    },
  ];
}

function Gallery() {
  return (
    <div className="bg-cream text-ink" style={{ minHeight: "100vh" }}>
      <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        <p className="label-caps">People — MemberTable</p>
        <div className="mt-3">
          <MemberTable members={members()} ownerId="u-owner" />
        </div>

        <p className="label-caps" style={{ marginTop: "2.5rem" }}>
          Machines — PoolFleetTable
        </p>
        <div className="mt-3">
          <PoolFleetTable machines={fleet()} />
        </div>
      </div>
    </div>
  );
}

it("writes the workspace tables preview", async () => {
  const css = await compiledCss();
  const body = renderToStaticMarkup(<Gallery />);

  // The conversion's whole claim: cells and heads carry the console's modal
  // padding, which is what makes adopting the primitive a no-op on screen.
  expect(body).toContain('class="px-3 py-2.5 align-middle');
  expect(body).toContain("px-3 py-2 text-left align-middle");

  // And no row asserts interactivity. A hover fill is this console's signal
  // that a row navigates, and neither of these tables' rows do — the stock
  // primitive put one on every row.
  expect(body).not.toContain("hover:bg-muted-50");

  // Every value drawn came from the fixture above; nothing renders a figure
  // the "API" did not return. `machines_online: 0` is a counted zero and
  // must appear as "0" rather than being blanked into a dash.
  expect(body).toContain(">0<");

  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Workspace tables preview</title><style>${FONT_VARS}</style><style>${css}</style></head>
<body>${body}</body></html>`;

  mkdirSync(outDir, { recursive: true });
  const out = path.join(outDir, "workspace-tables.html");
  writeFileSync(out, html, "utf8");
  console.log(`preview written: ${out}`);
}, 60_000);
