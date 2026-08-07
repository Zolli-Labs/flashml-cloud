import { describe, expect, it } from "vitest";
import type { Profile } from "./cloud-api";
import {
  TEXT_FIELD_CAPS,
  changedDetails,
  detailsTextError,
  draftFromProfile,
  isDetailsEmpty,
  type DetailsTextField,
} from "./profile-details";

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

// Mirrors `_PATCHABLE_TEXT` in `app.py` exactly, including its `>`-not-`>=`
// comparison — the client must refuse to offer an action the server will
// reject, never merely react to the 400 after the fact.
describe("detailsTextError", () => {
  const FIELDS: DetailsTextField[] = ["first_name", "last_name", "company_name"];

  it("accepts an ordinary, well-within-cap value for every field", () => {
    for (const field of FIELDS) {
      expect(detailsTextError("Ha", field)).toBeNull();
    }
  });

  it.each(FIELDS)("rejects %s when blank", (field) => {
    expect(detailsTextError("", field)).toBe("Can't be blank.");
  });

  it.each(FIELDS)("rejects %s when it is whitespace only", (field) => {
    expect(detailsTextError("   ", field)).toBe("Can't be blank.");
  });

  it.each(FIELDS)("rejects %s one character over its cap", (field) => {
    const cap = TEXT_FIELD_CAPS[field];
    const value = "a".repeat(cap + 1);
    expect(detailsTextError(value, field)).toBe(
      `${cap + 1}/${cap} characters. Too long.`
    );
  });

  it.each(FIELDS)(
    "accepts %s exactly at its cap — the API compares with '>', not '>='",
    (field) => {
      const cap = TEXT_FIELD_CAPS[field];
      const value = "a".repeat(cap);
      expect(detailsTextError(value, field)).toBeNull();
    }
  );

  it("trims before measuring, so surrounding whitespace cannot itself push a value over its cap", () => {
    const cap = TEXT_FIELD_CAPS.first_name;
    const value = `  ${"a".repeat(cap)}  `;
    expect(detailsTextError(value, "first_name")).toBeNull();
  });

  it("first and last name share an 80-character cap; company gets 160", () => {
    expect(TEXT_FIELD_CAPS).toEqual({
      first_name: 80,
      last_name: 80,
      company_name: 160,
    });
  });
});
