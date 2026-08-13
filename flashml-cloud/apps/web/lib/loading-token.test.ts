import { readFileSync } from "node:fs";
import path from "node:path";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
import { describe, expect, it } from "vitest";

/**
 * The loading placeholder has to be VISIBLE, and that is not something the
 * type system, the linter or a render test can check.
 *
 * `components/ui/skeleton.tsx` used to paint `bg-muted`. `--muted` resolves to
 * `--surface-2` = #f0eee8; the console page is `--cream` = #f1efe9. That is a
 * difference of one value per channel — the placeholder was invisible, so a
 * panel that was still loading looked exactly like a panel that had come back
 * empty. Under the honesty rules those two states must never be confusable, so
 * an invisible skeleton is a correctness bug and not a style preference.
 *
 * Two assertions, because the bug has two independent halves and fixing either
 * one alone leaves it broken:
 *
 *   1. `bg-skeleton` must reach the COMPILED bundle. In Tailwind v4 a utility
 *      only exists if `@theme` declares `--color-skeleton`; naming the class
 *      correctly in source is not enough, and the failure is silent. This is
 *      the same trap `lib/theme-tokens.test.ts` documents for `border-muted`,
 *      and it is checked the same way: run the real compiler, then grep the
 *      output.
 *   2. The surface must stay far enough from the page colour to be seen. A
 *      token can be wired up perfectly and still be the wrong colour — which
 *      is precisely how this shipped.
 */
const root = process.cwd();
const globalsPath = path.join(root, "app/globals.css");

function readGlobals(): string {
  return readFileSync(globalsPath, "utf8");
}

async function compileGlobalsCss(): Promise<string> {
  const css = readGlobals();
  const result = await postcss([tailwindcss({ base: root })]).process(css, {
    from: globalsPath,
  });
  return result.css;
}

/**
 * Resolves a token to its literal hex, following `var(--other)` aliases.
 *
 * The palette is deliberately two-layered — `--cream: var(--z-app-bg)` and
 * `--z-app-bg: #f1efe9` — so reading one declaration is not enough. Following
 * the chain is the point: it is what makes the comparison below test the
 * colours that actually render rather than the names they are reached by.
 */
function hexToken(css: string, name: string, seen: string[] = []): string {
  if (seen.includes(name)) {
    throw new Error(`--${name} is a circular alias: ${[...seen, name].join(" -> ")}`);
  }
  const match = css.match(new RegExp(`--${name}:\\s*([^;]+);`));
  if (!match) {
    throw new Error(`--${name} is not declared in app/globals.css`);
  }
  const value = match[1].trim();

  const hex = value.match(/^#([0-9a-fA-F]{6})$/);
  if (hex) return value.toLowerCase();

  const alias = value.match(/^var\(\s*--([A-Za-z0-9-]+)\s*\)$/);
  if (alias) return hexToken(css, alias[1], [...seen, name]);

  throw new Error(
    `--${name} resolves to ${value}, which is neither a 6-digit hex nor a var() alias`
  );
}

function channels(hex: string): [number, number, number] {
  return [
    parseInt(hex.slice(1, 3), 16),
    parseInt(hex.slice(3, 5), 16),
    parseInt(hex.slice(5, 7), 16),
  ];
}

/** Largest single-channel distance. Crude, but it is what "can I see it" needs. */
function maxChannelDelta(a: string, b: string): number {
  const [ar, ag, ab] = channels(a);
  const [br, bg, bb] = channels(b);
  return Math.max(Math.abs(ar - br), Math.abs(ag - bg), Math.abs(ab - bb));
}

describe("the loading placeholder is visible", () => {
  it("emits .bg-skeleton from the compiled stylesheet", async () => {
    const compiled = await compileGlobalsCss();

    // The neighbouring, already-working utility. Asserted first so a failure
    // below is about `--color-skeleton` specifically and not about the harness
    // (wrong `base`, Tailwind not resolving, scan finding nothing).
    expect(compiled).toMatch(
      /\.bg-cream\s*\{[^}]*background-color:\s*var\(--cream\)/
    );

    expect(compiled).toMatch(
      /\.bg-skeleton\s*\{[^}]*background-color:\s*var\(--skeleton\)/
    );
  });

  it("keeps the skeleton surface distinguishable from the page it sits on", () => {
    const css = readGlobals();
    const skeleton = hexToken(css, "skeleton");
    const page = hexToken(css, "cream");

    // The bug was a delta of 1. Anything under ~6 is not perceivable as a
    // shape on the page, which is the entire job of a skeleton. This is the
    // assertion that stops the token being re-pointed at `--surface-2` (or any
    // other near-page-colour surface) by a future tidy-up.
    expect(maxChannelDelta(skeleton, page)).toBeGreaterThanOrEqual(6);
  });

  it("declares the skeleton surface as a token, not an inline hex", () => {
    const css = readGlobals();

    // `.skeleton` carried `background: #eee5dc` directly. House rule: never
    // inline a colour — if it is missing, add the token.
    expect(css).toMatch(/\.skeleton\s*\{[^}]*background:\s*var\(--skeleton\)/);
  });
});
