import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

/**
 * Next.js App Router allows a `page.tsx` to export ONLY a default component
 * plus a fixed set of route-config keys. Anything else makes the route module
 * invalid — and the symptom is not a build failure. The page compiles, deploys,
 * serves, and then the browser shows "This page couldn't load" with nothing to
 * go on.
 *
 * That is not hypothetical: `app/jobs/page.tsx` exported a `StateBadge`
 * component, `app/jobs/[jobId]/page.tsx` imported it from `../page`, and both
 * routes white-screened in production while every other page worked. `tsc`
 * reported it the whole time, buried in a generated file under
 * `.next/dev/types/`, where it read as unrelated noise and was dismissed twice.
 *
 * This test says it in one line, in the suite that actually gets read.
 */
const ALLOWED = new Set([
  "default",
  "metadata",
  "generateMetadata",
  "viewport",
  "generateViewport",
  "generateStaticParams",
  "dynamic",
  "dynamicParams",
  "revalidate",
  "fetchCache",
  "runtime",
  "preferredRegion",
  "maxDuration",
  "experimental_ppr",
]);

// A `route.ts` is a different thing: HTTP method handlers ARE its public API,
// so they are exactly what it is supposed to export. Only page/layout modules
// carry the default-plus-config restriction.
const HTTP_METHODS = new Set([
  "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS",
]);

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return walk(full);
    return /^(page|layout|route)\.tsx?$/.test(entry) ? [full] : [];
  });
}

describe("App Router route modules", () => {
  it("export nothing beyond default and route config", () => {
    const offenders: string[] = [];

    for (const file of walk("app")) {
      const source = readFileSync(file, "utf8");
      // `export function X`, `export const X`, `export class X`.
      // `export default` is matched separately and always allowed.
      const re = /^export\s+(?!default\b)(?:async\s+)?(?:function|const|let|class)\s+([A-Za-z0-9_$]+)/gm;
      const isRouteHandler = /[\\/]route\.tsx?$/.test(file);
      for (const match of source.matchAll(re)) {
        const name = match[1];
        if (ALLOWED.has(name)) continue;
        if (isRouteHandler && HTTP_METHODS.has(name)) continue;
        offenders.push(`${file} exports ${name}`);
      }
    }

    expect(
      offenders,
      "a route module may only export a default component and route config; " +
        "anything else breaks the route at RUNTIME with no build error. " +
        "Move shared components into components/.",
    ).toEqual([]);
  });
});
