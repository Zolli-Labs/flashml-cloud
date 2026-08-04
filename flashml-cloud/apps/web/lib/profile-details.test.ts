import { describe, expect, it } from "vitest";
import type { Profile } from "./cloud-api";
import { changedDetails, draftFromProfile, isDetailsEmpty } from "./profile-details";

const BASE: Profile = {
  id: "u1",
  display_name: "Ha",
  github_login: null,
  is_host: false,
  is_developer: false,
  created_at: "2026-01-01T00:00:00Z",
  admitted: true,
  access: "admitted",
  is_admin: false,
  first_name: null,
  last_name: null,
  company_name: null,
  role: null,
  team_size: null,
};

describe("draftFromProfile", () => {
  it("maps null fields to empty strings, not null", () => {
    expect(draftFromProfile(BASE)).toEqual({
      first_name: "",
      last_name: "",
      company_name: "",
      role: "",
      team_size: "",
    });
  });

  it("carries through set fields", () => {
    expect(
      draftFromProfile({
        ...BASE,
        first_name: "Ha",
        last_name: "Nguyen",
        company_name: "VinAI",
        role: "researcher",
        team_size: "2_5",
      })
    ).toEqual({
      first_name: "Ha",
      last_name: "Nguyen",
      company_name: "VinAI",
      role: "researcher",
      team_size: "2_5",
    });
  });

  it("a null profile (still loading) seeds an all-empty draft", () => {
    expect(draftFromProfile(null)).toEqual({
      first_name: "",
      last_name: "",
      company_name: "",
      role: "",
      team_size: "",
    });
  });
});

describe("isDetailsEmpty", () => {
  it("is true when every field is null", () => {
    expect(isDetailsEmpty(BASE)).toBe(true);
  });

  it("is true for a null (not yet loaded) profile", () => {
    expect(isDetailsEmpty(null)).toBe(true);
  });

  it("is false the moment any single field is set", () => {
    expect(isDetailsEmpty({ ...BASE, company_name: "VinAI" })).toBe(false);
    expect(isDetailsEmpty({ ...BASE, role: "researcher" })).toBe(false);
  });

  it("is false when every field is set", () => {
    expect(
      isDetailsEmpty({
        ...BASE,
        first_name: "Ha",
        last_name: "Nguyen",
        company_name: "VinAI",
        role: "researcher",
        team_size: "2_5",
      })
    ).toBe(false);
  });
});

describe("changedDetails", () => {
  const current = draftFromProfile(BASE);

  it("returns nothing when the draft matches the current profile", () => {
    expect(changedDetails(current, current)).toEqual({});
  });

  it("returns only the fields that changed", () => {
    expect(
      changedDetails({ ...current, first_name: "Ha", role: "researcher" }, current)
    ).toEqual({ first_name: "Ha", role: "researcher" });
  });

  it("trims whitespace before comparing and before returning", () => {
    expect(changedDetails({ ...current, first_name: "  Ha  " }, current)).toEqual({
      first_name: "Ha",
    });
  });

  it("a value that trims back to the current value is not a change", () => {
    const withName = { ...current, first_name: "Ha" };
    expect(changedDetails({ ...withName, first_name: "  Ha  " }, withName)).toEqual({});
  });

  it("allows clearing a previously-set field back to empty", () => {
    const withCompany = { ...current, company_name: "VinAI" };
    expect(changedDetails(current, withCompany)).toEqual({ company_name: "" });
  });
});
