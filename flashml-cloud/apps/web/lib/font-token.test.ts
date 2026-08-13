import { readFileSync } from "node:fs";
import path from "node:path";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
import { describe, expect, it } from "vitest";

/**
 * `font-mono` must survive its own font variable failing to load.
 *
 * `@theme` declared `--font-mono: var(--font-geist-mono)` with no fallback,
 * while seven hand-written rules in the same stylesheet (`.meta`,
 * `.terminal-text`, `.metric-value` and others) all carry `, monospace`. So
 * the utility and the classes disagreed: anywhere the next/font variable did
 * not resolve, anything styled with the UTILITY silently rendered in the sans
 * fallback while anything styled with a CLASS stayed monospace.
 *
 * Silently is the operative word, and it is why this is a compiled-output test
 * rather than a source one. The class name is present, the utility exists in
 * the bundle, nothing errors — the only symptom is that a job id renders in
 * the wrong typeface, which no assertion about source can see.
 *
 * Every identifier in this console — job ids, machine names, protocol events —
 * is monospace on purpose: it is how a reader tells a value the system
 * assigned from prose someone wrote.
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

describe("typeface utilities survive a missing font variable", () => {
  it("emits .font-mono with a generic fallback after the variable", async () => {
    const compiled = await compileGlobalsCss();

    const rule = compiled.match(/\.font-mono\s*\{([^}]*)\}/);
    expect(rule, ".font-mono did not reach the compiled bundle").not.toBeNull();

    const body = rule![1];
    expect(body).toMatch(/--font-geist-mono/);
    // The fallback, and the reason this test exists.
    expect(body).toMatch(/monospace/);
  });

  it("emits .font-sans with a generic fallback too", async () => {
    const compiled = await compileGlobalsCss();

    const rule = compiled.match(/\.font-sans\s*\{([^}]*)\}/);
    expect(rule, ".font-sans did not reach the compiled bundle").not.toBeNull();
    expect(rule![1]).toMatch(/sans-serif/);
  });

  it("gives an identifier title its own class rather than a utility", async () => {
    const css = readFileSync(globalsPath, "utf8");

    // `.title` is unlayered, so it beats any Tailwind utility regardless of
    // specificity: `class="title font-mono"` renders SANS. `.title-mono`
    // exists so a monospace page title does not depend on winning that
    // fight. If someone deletes it and reaches for the utility again, the
    // bug returns invisibly.
    expect(css).toMatch(/\.title-mono\s*\{[^}]*--font-geist-mono/);
  });
});
