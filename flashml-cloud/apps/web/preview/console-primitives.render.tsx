import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
import { it } from "vitest";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty";

/**
 * A VISUAL harness, not a test.
 *
 * Every route under `app/(console)` is behind a Supabase session, so a session
 * that cannot sign in cannot look at any of them — and on this project "tests
 * pass" has repeatedly not meant "works". This renders console primitives to
 * static HTML against the REAL compiled `globals.css` (the same
 * `@tailwindcss/postcss` pass the Next build makes, the method
 * `lib/theme-tokens.test.ts` established) so they can be screenshotted and
 * actually looked at without auth, and without touching `middleware.ts` — the
 * file that decides whether the console is authenticated at all.
 *
 * Named `*.render.tsx` so the main suite's `**\/*.test.{ts,tsx}` glob cannot
 * collect it. It has no assertions and must never be counted as a test.
 *
 *   npx vitest run --config preview/vitest.preview.config.ts
 */
/**
 * A static render never runs `app/layout.tsx`, so neither `next/font` variable
 * exists and EVERY typeface falls back to the browser default — which means
 * sans and mono look identical and a typeface bug cannot be seen at all. That
 * blind spot hid a real defect through two render cycles: `.title` is declared
 * unlayered, so `class="title font-mono"` renders sans, and the preview could
 * not show it.
 *
 * Stand-ins, not the real faces: the point is that sans and mono are
 * DISTINGUISHABLE, so a typeface applied to the wrong element is visible here.
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

function Section({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section style={{ marginBottom: "2.75rem" }}>
      <h2 className="label-caps" style={{ marginBottom: "0.25rem" }}>
        {title}
      </h2>
      {note ? (
        <p className="meta" style={{ marginBottom: "0.9rem", maxWidth: "60ch" }}>
          {note}
        </p>
      ) : null}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem" }}>
        {children}
      </div>
    </section>
  );
}

const BUTTON_VARIANTS = [
  "default",
  "outline",
  "secondary",
  "ghost",
  "destructive",
  "link",
] as const;

/**
 * The primary-CTA string copy-pasted verbatim into 8 console files, reproduced
 * here EXACTLY so it can be compared side by side with the `Button` primitive
 * those files could have used.
 *
 * It used to prove they differed. It now proves they do not: the primitive was
 * moved to THIS treatment rather than the eight files being moved to the
 * primitive's, so adopting `<Button>` is a refactor instead of a redesign. Keep
 * this literal in sync with the eight call sites — the moment it drifts, this
 * panel stops being evidence and starts being decoration.
 */
const HAND_ROLLED_PRIMARY =
  "interactive rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110";

function Gallery() {
  return (
    <main
      className="bg-cream text-ink"
      style={{ minHeight: "100vh", padding: "2.5rem 3rem", maxWidth: "1200px" }}
    >
      <h1 className="title" style={{ marginBottom: "0.35rem" }}>
        Console primitives
      </h1>
      <p className="meta" style={{ marginBottom: "2.5rem" }}>
        Rendered from source against compiled globals.css. No live data.
      </p>

      <Section
        title="Button — the primitive"
        note="components/ui/button.tsx. Imported by 3 of 37 console files."
      >
        {BUTTON_VARIANTS.map((v) => (
          <Button key={v} variant={v}>
            {v}
          </Button>
        ))}
      </Section>

      <Section
        title="Button — the hand-rolled copy"
        note="The exact class string duplicated across 8 console files. It should now be indistinguishable from the primitive above — the primitive was moved to THIS treatment, so adopting Button is a refactor rather than a redesign. Measured: height 36px, weight 600, 14px, rgb(243,107,50) all match; only horizontal padding differs, 12px vs 14px."
      >
        <button className={HAND_ROLLED_PRIMARY}>hand-rolled primary</button>
        <button className="rounded-md border border-border bg-surface px-3 py-1.5 text-sm hover:bg-surface-2">
          hand-rolled outline
        </button>
      </Section>

      <Section title="Badge">
        <Badge>default</Badge>
        <Badge variant="secondary">secondary</Badge>
        <Badge variant="outline">outline</Badge>
        <Badge variant="destructive">destructive</Badge>
      </Section>

      <Section
        title="Table — the primitive"
        note="components/ui/table.tsx is complete and imported nowhere. 14+ console files hand-write <table> with drifting cell padding."
      >
        <div style={{ width: "100%" }}>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Machine</TableHead>
                <TableHead>State</TableHead>
                <TableHead>Venue</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow>
                <TableCell className="font-mono">node-a</TableCell>
                <TableCell>
                  <Badge variant="secondary">online</Badge>
                </TableCell>
                <TableCell>runpod</TableCell>
              </TableRow>
              <TableRow>
                <TableCell className="font-mono">node-b</TableCell>
                <TableCell>
                  <Badge variant="outline">not observed</Badge>
                </TableCell>
                <TableCell>local</TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
      </Section>

      <Section
        title="Empty — the primitive"
        note="components/ui/empty.tsx is complete and imported nowhere. Three divergent local Empty() functions exist instead."
      >
        <div style={{ width: "100%", maxWidth: "520px" }}>
          <Empty>
            <EmptyHeader>
              <EmptyTitle>No machines yet</EmptyTitle>
              <EmptyDescription>
                Add a machine to start running jobs in this workspace.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        </div>
      </Section>

      <Section
        title="Skeleton"
        note="components/ui/skeleton.tsx is never imported; pages use a separate hand-written .skeleton CSS class instead."
      >
        <div style={{ width: "100%", display: "grid", gap: "0.5rem" }}>
          <Skeleton className="h-4 w-64" />
          <Skeleton className="h-4 w-40" />
          <div className="skeleton" style={{ height: "1rem", width: "16rem" }} />
        </div>
      </Section>
    </main>
  );
}

it("writes the console primitives preview", async () => {
  const css = await compiledCss();
  const body = renderToStaticMarkup(<Gallery />);
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Console primitives preview</title><style>${FONT_VARS}</style><style>${css}</style></head>
<body>${body}</body></html>`;

  mkdirSync(outDir, { recursive: true });
  const out = path.join(outDir, "console-primitives.html");
  writeFileSync(out, html, "utf8");
   
  console.log(`preview written: ${out}`);
}, 60_000);
