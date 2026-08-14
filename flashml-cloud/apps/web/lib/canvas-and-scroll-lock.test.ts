import { readFileSync } from "node:fs";
import path from "node:path";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
import { describe, expect, it } from "vitest";

/**
 * Two fixes for one bug, reported live from the console in light mode: the
 * left rail displaced upward — its top cut off above the viewport, a gray
 * band under its bottom edge — plus a gray strip down the scrollbar gutter.
 * Also reproducible with plain macOS rubber-band overscroll.
 *
 * Root causes, confirmed by reading `node_modules/@base-ui/utils/
 * useScrollLock.js` and a controlled repro (not re-litigated here — see the
 * coordinator's diagnosis):
 *
 *   1. Base UI's scroll lock picks its target with
 *      `isOverflowElement(html) ? html : body`. This app's `html` carried no
 *      overflow style, so `body` was always chosen — and `body` is where the
 *      console rail's `position: sticky` lives. Base UI's own comment
 *      ("instead, as sticky elements shift otherwise") is why every strategy
 *      avoids locking body when it can lock html instead; ours could never
 *      take that branch. Opening an AlertDialog (Revoke, on /machines) or any
 *      other Base UI popup mutated `body`'s overflow/position and bumped the
 *      sticky rail out of place.
 *   2. `<html>` painted with no background of its own, so wherever the
 *      canvas showed through — the scroll-lock gutter, the shrunken body box,
 *      macOS overscroll bounce — the user saw the browser's default white,
 *      not the app.
 *
 * This runs the REAL Tailwind v4 compiler (the same `@tailwindcss/postcss`
 * plugin the Next.js build uses) over the real `globals.css` and asserts
 * against the EMITTED CSS, the same method `lib/theme-tokens.test.ts` and
 * `lib/dark-console-theme.test.ts` established: build, then grep the
 * compiled stylesheet, rather than trusting the source.
 *
 * The `overflow-y: auto` assertion is the one most likely to look like dead
 * weight to a future cleanup: visually it changes nothing (the viewport
 * already scrolled through `body`'s default overflow), so a reviewer who
 * does not know about `useScrollLock`'s `isOverflowElement` check can read
 * it as a no-op and delete it. It is not a no-op — it is what makes
 * `isOverflowElement(html)` true, which is what keeps every Base UI lock off
 * `body` and off the sticky rail. This test exists so that deletion fails
 * loudly instead of silently reintroducing the bump the next time someone
 * opens a dialog.
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

describe("globals.css compiled output — scroll-lock target and console canvas", () => {
  it(
    "emits an html rule with overflow-y: auto (load-bearing for @base-ui's isOverflowElement check)",
    async () => {
      const compiled = await compileGlobalsCss();

      expect(compiled).toMatch(
        /\bhtml\s*\{[^}]*overflow-y:\s*auto[^}]*\}/
      );
    },
    20000
  );

  it(
    "emits html:has(.console-theme) painting the canvas with --background",
    async () => {
      const compiled = await compileGlobalsCss();

      expect(compiled).toMatch(
        /html:has\(\.console-theme\)\s*\{[^}]*background:\s*var\(--background\)[^}]*\}/
      );
    },
    20000
  );

  it(
    "emits the lg (1024px) variant splitting the canvas into rail colour + page colour",
    async () => {
      const compiled = await compileGlobalsCss();

      expect(compiled).toMatch(
        /@media \(min-width:\s*1024px\)\s*\{\s*html:has\(\.console-theme\)\s*\{[^}]*var\(--bg-rail\)[^}]*var\(--background\)[^}]*\}\s*\}/
      );
    },
    20000
  );
});
