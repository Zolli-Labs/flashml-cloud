import { describe, expect, it } from "vitest";
import {
  COMPUTE_OPTIONS,
  HEARD_FROM_OPTIONS,
  ROLE_OPTIONS,
  TEAM_SIZE_OPTIONS,
  isComplete,
} from "./onboarding-options";

// These values are a contract with the API's enumerations. A label typo is
// cosmetic; a VALUE typo is a 400 the user cannot fix, so the values are
// pinned here rather than trusted to review.
describe("option values match the API enumerations", () => {
  it("roles", () => {
    expect(ROLE_OPTIONS.map((o) => o.value)).toEqual([
      "researcher",
      "ml_engineer",
      "student",
      "founder",
      "other",
    ]);
  });

  it("team sizes", () => {
    expect(TEAM_SIZE_OPTIONS.map((o) => o.value)).toEqual([
      "solo",
      "2_5",
      "6_20",
      "20_plus",
    ]);
  });

  it("compute sources", () => {
    expect(COMPUTE_OPTIONS.map((o) => o.value)).toEqual([
      "own_machines",
      "colab",
      "runpod",
      "cloud",
      "none",
    ]);
  });

  it("heard-from values are a subset of what the API accepts", () => {
    const allowed = new Set([
      "github", "search", "twitter", "friend", "paper", "event", "other",
    ]);
    for (const o of HEARD_FROM_OPTIONS) expect(allowed.has(o.value)).toBe(true);
  });

  it("every option has a human label", () => {
    for (const o of [
      ...ROLE_OPTIONS, ...TEAM_SIZE_OPTIONS, ...COMPUTE_OPTIONS, ...HEARD_FROM_OPTIONS,
    ]) {
      expect(o.label.trim().length).toBeGreaterThan(0);
    }
  });
});

describe("isComplete", () => {
  const full = {
    first_name: "Ha",
    last_name: "Nguyen",
    company_name: "VinAI",
    role: "researcher",
    team_size: "2_5",
    use_case: "Fine-tune across the lab.",
    compute_sources: ["own_machines"],
    heard_from: "github",
  };

  it("accepts a complete draft", () => {
    expect(isComplete(full)).toBe(true);
  });

  it("compute_sources may be empty — the API allows it", () => {
    expect(isComplete({ ...full, compute_sources: [] })).toBe(true);
  });

  it("heard_from is optional", () => {
    expect(isComplete({ ...full, heard_from: "" })).toBe(true);
  });

  it.each(["first_name", "last_name", "company_name", "use_case"])(
    "requires %s",
    (field) => {
      expect(isComplete({ ...full, [field]: "   " })).toBe(false);
    }
  );

  it.each(["role", "team_size"])("requires %s to be chosen", (field) => {
    expect(isComplete({ ...full, [field]: "" })).toBe(false);
  });
});
