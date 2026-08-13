import { describe, expect, it } from "vitest";
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import {
  CONSOLE_PAGE_WIDTH,
  CONSOLE_ROUTES_WITHOUT_SHELL,
  CONSOLE_WIDTHS,
  CONSOLE_WIDTH_CLASS,
  CONSOLE_WIDTH_RATIONALE,
  DEFAULT_CONSOLE_WIDTH,
  consoleRouteKey,
  widthForConsolePath,
  type ConsoleWidth,
} from "./page-width";

/** Every `page.tsx` under `app/(console)`, as the route it serves.
 *
 * Read off the filesystem rather than listed, for the reason
 * `lib/route-exports.test.ts` reads it too: a list in a test goes stale the
 * moment somebody adds a route, and a stale list of routes is exactly the
 * thing that lets a page ship with no considered width. */
function consoleRoutes(): string[] {
  const root = join("app", "(console)");

  function walk(dir: string): string[] {
    return readdirSync(dir).flatMap((entry) => {
      const full = join(dir, entry);
      if (statSync(full).isDirectory()) return walk(full);
      if (entry !== "page.tsx") return [];

      const segments = dir
        .split("/")
        .slice(1) // drop "app"
        .filter((s) => !(s.startsWith("(") && s.endsWith(")"))); // drop groups

      return [`/${segments.join("/")}`];
    });
  }

  return walk(root).sort();
}

describe("the width vocabulary", () => {
  it("names four widths and no more", () => {
    expect(CONSOLE_WIDTHS).toEqual(["focused", "reading", "standard", "wide"]);
  });

  it("gives every width a class and a reason", () => {
    // Both are typed `Record<ConsoleWidth, string>`, so a fifth width is a
    // compile error. This catches the other direction: a class or a reason
    // left behind for a width that no longer exists.
    expect(new Set(Object.keys(CONSOLE_WIDTH_CLASS))).toEqual(
      new Set(CONSOLE_WIDTHS)
    );
    expect(new Set(Object.keys(CONSOLE_WIDTH_RATIONALE))).toEqual(
      new Set(CONSOLE_WIDTHS)
    );
  });

  it("maps each width to a distinct max-w utility", () => {
    const classes = Object.values(CONSOLE_WIDTH_CLASS);
    expect(new Set(classes).size).toBe(classes.length);
    for (const value of classes) expect(value).toMatch(/^max-w-[a-z0-9]+$/);
  });

  it("orders the widths narrow to wide", () => {
    // The vocabulary reads as a scale in source, and a reader who assumes
    // that should be right.
    const rem: Record<ConsoleWidth, number> = {
      focused: 36,
      reading: 48,
      standard: 56,
      wide: 72,
    };
    const widths = CONSOLE_WIDTHS.map((w) => rem[w]);
    expect([...widths].sort((a, b) => a - b)).toEqual(widths);
  });

  it("gives a reason that argues from content, not from taste", () => {
    for (const width of CONSOLE_WIDTHS) {
      expect(CONSOLE_WIDTH_RATIONALE[width].length).toBeGreaterThan(80);
    }
  });
});

describe("consoleRouteKey", () => {
  it("replaces a pool id with the route's own bracket form", () => {
    expect(consoleRouteKey("/w/8f2c-41ab/jobs")).toBe("/w/[poolId]/jobs");
    expect(consoleRouteKey("/w/8f2c-41ab")).toBe("/w/[poolId]");
  });

  it("replaces a job id", () => {
    expect(consoleRouteKey("/jobs/job_01H9")).toBe("/jobs/[jobId]");
  });

  it("leaves the static /pools/join alone while collapsing pool ids", () => {
    // `/pools/join` is a real page and `/pools/<id>` is a redirect; a regex
    // that collapsed both would file the join screen under the redirect.
    expect(consoleRouteKey("/pools/join")).toBe("/pools/join");
    expect(consoleRouteKey("/pools/8f2c-41ab")).toBe("/pools/[poolId]");
  });

  it("drops the query string and the hash", () => {
    expect(consoleRouteKey("/pools/join?token=abc")).toBe("/pools/join");
    expect(consoleRouteKey("/activate?pool=8f2c#top")).toBe("/activate");
  });

  it("drops a trailing slash without eating the root", () => {
    expect(consoleRouteKey("/metrics/")).toBe("/metrics");
    expect(consoleRouteKey("/")).toBe("/");
  });

  it("leaves a static route untouched", () => {
    expect(consoleRouteKey("/account/machines")).toBe("/account/machines");
  });
});

describe("widthForConsolePath", () => {
  it("gives fleet and job tables the wide column", () => {
    expect(widthForConsolePath("/w/8f2c/machines")).toBe("wide");
    expect(widthForConsolePath("/w/8f2c/jobs")).toBe("wide");
    expect(widthForConsolePath("/jobs/job_01H9")).toBe("wide");
    expect(widthForConsolePath("/account/machines")).toBe("wide");
  });

  it("gives the prose pages a reading measure", () => {
    expect(widthForConsolePath("/docs")).toBe("reading");
    expect(widthForConsolePath("/how-it-works")).toBe("reading");
  });

  it("gives a single-decision screen the focused column", () => {
    expect(widthForConsolePath("/workspaces")).toBe("focused");
  });

  it("falls back to the recoverable default for a route nobody has filed", () => {
    // A too-narrow table scrolls inside its own container; a too-wide
    // paragraph just reads badly forever. Default toward the first.
    expect(widthForConsolePath("/some/page/added/tomorrow")).toBe(
      DEFAULT_CONSOLE_WIDTH
    );
    expect(DEFAULT_CONSOLE_WIDTH).toBe("standard");
  });

  it("answers for a live pathname with its query attached", () => {
    expect(widthForConsolePath("/market/listings?state=open")).toBe("wide");
  });
});

describe("the table covers the console", () => {
  it("files every console page as either a width or a considered exception", () => {
    const unfiled = consoleRoutes().filter(
      (route) =>
        !(route in CONSOLE_PAGE_WIDTH) &&
        !(route in CONSOLE_ROUTES_WITHOUT_SHELL)
    );

    expect(unfiled).toEqual([]);
  });

  it("files no page twice", () => {
    const both = Object.keys(CONSOLE_PAGE_WIDTH).filter(
      (route) => route in CONSOLE_ROUTES_WITHOUT_SHELL
    );

    expect(both).toEqual([]);
  });

  it("names no route that does not exist", () => {
    // The other direction: a page deleted or moved leaves an entry behind
    // that then reads as a decision about a screen nobody can reach.
    const routes = new Set(consoleRoutes());
    const orphans = [
      ...Object.keys(CONSOLE_PAGE_WIDTH),
      ...Object.keys(CONSOLE_ROUTES_WITHOUT_SHELL),
    ].filter((route) => !routes.has(route));

    expect(orphans).toEqual([]);
  });

  it("gives every exception a reason", () => {
    for (const [route, why] of Object.entries(CONSOLE_ROUTES_WITHOUT_SHELL)) {
      expect(why.length, route).toBeGreaterThan(20);
    }
  });

  it("uses only widths from the vocabulary", () => {
    const named = new Set<string>(CONSOLE_WIDTHS);
    for (const [route, width] of Object.entries(CONSOLE_PAGE_WIDTH)) {
      expect(named.has(width), route).toBe(true);
    }
  });
});
