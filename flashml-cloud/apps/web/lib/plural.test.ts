import { describe, expect, it } from "vitest";
import { countOf } from "./plural";

// Regression: ISSUE-004 — the workspace header read "1 people · 0 machines
// online" on every tab of a workspace with one member.
// Found by hands-on QA on 2026-08-04.
// Report: .gstack/qa-reports/qa-report-flashml-console-2026-08-04.md
describe("countOf", () => {
  it("uses the singular for exactly one", () => {
    expect(countOf(1, "person", "people")).toBe("1 person");
    expect(countOf(1, "machine")).toBe("1 machine");
  });

  it("uses the plural for zero", () => {
    expect(countOf(0, "person", "people")).toBe("0 people");
    expect(countOf(0, "machine")).toBe("0 machines");
  });

  it("uses the plural for many", () => {
    expect(countOf(2, "person", "people")).toBe("2 people");
    expect(countOf(17, "machine")).toBe("17 machines");
  });

  // The irregular case is the whole reason this takes an explicit plural:
  // "1 persons" and "2 persons" are both wrong for what the UI says.
  it("does not invent an -s plural when an irregular one is given", () => {
    expect(countOf(3, "person", "people")).toBe("3 people");
  });
});
