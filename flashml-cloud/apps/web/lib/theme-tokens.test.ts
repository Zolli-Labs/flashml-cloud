import { readFileSync } from "node:fs";
import path from "node:path";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
import { describe, expect, it } from "vitest";

/**
 * `border-muted` styles the `no_marginal_gain` and `baseline` badges in
 * `TradeoffCard` — the rows that tell a buyer NOT to spend, which is the
 * whole point of that panel — and the same class is reused by `StateBadge`,
 * `ArtifactsCard`, `RoutingCard` and `SandboxLifecycle`. `@theme` in
 * `globals.css` defines `--color-muted-foreground` but used to define no
 * `--color-muted`, so Tailwind had nothing to generate the `border-*` /
 * `bg-*` / `text-*` family FROM: the class name is correct everywhere it is
 * used in source, and it still never reached the compiled bundle.
 *
 * A test that only inspects `globals.css` as a string cannot catch this kind
 * of bug — the source can name a token correctly and the compiler can still
 * fail to emit a utility for it. This runs the REAL Tailwind v4 compiler
 * (the same `@tailwindcss/postcss` plugin the Next.js build uses) over the
 * real `globals.css` and asserts against the emitted CSS — the same method
 * that found the bug: build, then grep the compiled stylesheet for the
 * class, rather than trusting the source.
 */
const root = process.cwd();
const globalsPath = path.join(root, "app/globals.css");

async function compileGlobalsCss(): Promise<string> {
  const css = readFileSync(globalsPath, "utf8");
  const result = await postcss([tailwindcss({ base: root })]).process(css, {
    from: globalsPath,
  });
  return result.css;
}

describe("globals.css compiled output — border tokens", () => {
  it(
    "emits .border-muted with a chosen colour, the same way .border-border does",
    async () => {
      const compiled = await compileGlobalsCss();

      // The neighbouring, already-working token. Asserted first so a failure
      // below is about `--color-muted` specifically, not about the harness
      // (wrong `base`, Tailwind not resolving, scan finding nothing).
      expect(compiled).toMatch(
        /\.border-border\s*\{[^}]*border-color:\s*var\(--border\)/
      );

      // The bug this test exists to catch: `.border-muted` must reach the
      // OUTPUT and resolve to the design system's muted-surface token — not
      // be missing (falling back to `currentColor` on the badges that use
      // it), and not some invented colour.
      expect(compiled).toMatch(
        /\.border-muted\s*\{[^}]*border-color:\s*var\(--muted\)/
      );
    },
    20000
  );
});
