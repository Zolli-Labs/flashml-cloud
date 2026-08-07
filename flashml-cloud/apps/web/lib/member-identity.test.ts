import { describe, expect, it } from "vitest";
import { memberName, memberSubtitle } from "./member-identity";

const UUID = "25b9f87b-2695-41f6-a6e6-8c3e301922d8";

// Regression: ISSUE-005 — the People tab printed the member's raw user_id
// under their name, so the one table whose job is telling teammates apart
// identified them by a 36-character UUID.
// Found by hands-on QA on 2026-08-04.
// Report: .gstack/qa-reports/qa-report-flashml-console-2026-08-04.md
describe("memberSubtitle", () => {
  it("is empty when the member has a name — the name IS the identity", () => {
    expect(memberSubtitle({ user_id: UUID, display_name: "QA Tester" })).toBe("");
  });

  it("never returns the full user_id", () => {
    const subtitle = memberSubtitle({ user_id: UUID, display_name: null });
    expect(subtitle).not.toContain(UUID);
  });

  // Without a name, two members would be indistinguishable rows of
  // "unnamed". A short prefix disambiguates without pasting an identifier
  // nobody reads.
  it("falls back to a short id when there is no name", () => {
    expect(memberSubtitle({ user_id: UUID, display_name: null })).toBe("25b9f87b");
    expect(memberSubtitle({ user_id: UUID, display_name: "   " })).toBe("25b9f87b");
  });
});

describe("memberName", () => {
  it("prefers the display name", () => {
    expect(memberName({ user_id: UUID, display_name: "QA Tester" })).toBe("QA Tester");
  });

  it("says unnamed rather than showing an id where a name goes", () => {
    expect(memberName({ user_id: UUID, display_name: null })).toBe("unnamed");
    expect(memberName({ user_id: UUID, display_name: "  " })).toBe("unnamed");
  });
});
