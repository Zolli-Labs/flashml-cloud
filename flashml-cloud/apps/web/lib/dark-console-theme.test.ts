import { readFileSync } from "node:fs";
import path from "node:path";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
import { describe, expect, it } from "vitest";

/**
 * The console's dark register is scoped with `html.dark:has(.console-theme)`
 * — an `<html>`-level selector, not a `body:has(...)` one — and that
 * particular element is not a stylistic detail this test can take on faith.
 * CSS custom properties are substituted at COMPUTED-VALUE time, on the
 * element where they are DECLARED (css-variables-1): `:root`'s `--surface:
 * var(--z-app-surface)` resolves to the light hex on `<html>`, and every
 * descendant inherits that already-resolved value. An override of
 * `--z-app-surface` on any element BELOW `<html>` — `<body>`, say — cannot
 * make `:root`'s mapping re-resolve; it compiles, the selector matches
 * exactly what it's supposed to, and it changes nothing anyone can see. That
 * shipped once: a `.dark body:has(.console-theme)` block that flipped the
 * page background while every semantic-token surface — the top bar, panels,
 * table rows, headings — stayed on light values, because `:root` had already
 * resolved them on `<html>` before `body`'s override ever ran. A test that
 * only checks the primitives reach SOME rule in the compiled output would
 * have passed on that broken version; the selector is the bug, not the
 * values, so the selector is what this file pins down.
 *
 * This runs the REAL Tailwind v4 compiler (the same `@tailwindcss/postcss`
 * plugin the Next.js build uses) over the real `globals.css` and asserts
 * against the EMITTED CSS, the same method `lib/theme-tokens.test.ts` and
 * `lib/loading-token.test.ts` established: build, then grep the compiled
 * stylesheet, rather than trusting the source.
 *
 * Four assertions:
 *   1. the dark console rule reaches the output on the `html.dark:has(...)`
 *      selector with the right primitives and `color-scheme: dark` — the
 *      thing this file exists to prove;
 *   2. NO `.dark body:has(` selector survives anywhere in the compiled
 *      output — a regression guard against the inert form silently coming
 *      back, since it would compile cleanly and look identical to a reader
 *      who did not already know the difference matters;
 *   3. the light `:root` block is unchanged — so a typo in the dark block
 *      that leaks into `:root` fails loudly instead of only showing up as a
 *      console that stays dark at noon;
 *   4. `.marketing-dark` is unchanged — the marketing register stays on its
 *      own cool graphite, untouched by the console's warm dark variant, per
 *      the approved scope decision (console-only, marketing unaffected).
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

describe("globals.css compiled output — dark console register", () => {
  it(
    "emits html.dark:has(.console-theme) with the warm dark primitives and color-scheme: dark",
    async () => {
      const compiled = await compileGlobalsCss();

      expect(compiled).toMatch(
        /html\.dark:has\(\.console-theme\)\s*\{[^}]*color-scheme:\s*dark[^}]*\}/
      );
      expect(compiled).toMatch(
        /html\.dark:has\(\.console-theme\)\s*\{[^}]*--z-app-bg:\s*#131110[^}]*\}/
      );
    },
    20000
  );

  it(
    "never emits the inert .dark body:has(...) form (regression guard)",
    async () => {
      const compiled = await compileGlobalsCss();

      expect(compiled).not.toMatch(/\.dark body:has\(/);
    },
    20000
  );

  it(
    "leaves the light :root register emitting --z-app-bg: #f1efe9 (regression guard)",
    async () => {
      const compiled = await compileGlobalsCss();

      expect(compiled).toMatch(
        /:root\s*\{[^}]*--z-app-bg:\s*#f1efe9[^}]*\}/
      );
    },
    20000
  );

  it(
    "leaves .marketing-dark emitting --z-bg: #131110 (marketing register untouched by the CONSOLE dark-token block)",
    async () => {
      // #131110, not the original #0b0d0e: the landing was later retinted
      // from cool graphite to the console's warm-black family, and
      // `.marketing-dark`'s own values moved as part of that separate,
      // deliberate edit. What this test guards has not changed — the
      // `html.dark:has(.console-theme)` block above must never leak into or
      // overwrite `.marketing-dark`'s own register — only the concrete
      // value `.marketing-dark` itself now carries.
      const compiled = await compileGlobalsCss();

      expect(compiled).toMatch(
        /\.marketing-dark\s*\{[^}]*--z-bg:\s*#131110[^}]*\}/
      );
    },
    20000
  );
});
