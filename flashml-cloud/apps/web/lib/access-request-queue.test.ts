import { describe, expect, it } from "vitest";
import {
  formatRequestedAt,
  fullNameFor,
  inviteLine,
  labelFor,
  restoreRequest,
} from "./access-request-queue";
import { ROLE_OPTIONS } from "./onboarding-options";
import type { AccessRequestRow } from "./cloud-api";

// Minimal fixture — only the fields each function under test reads.
function row(overrides: Partial<AccessRequestRow> = {}): AccessRequestRow {
  return {
    user_id: "u1",
    email: "ha@vinai.io",
    first_name: "Ha",
    last_name: "Nguyen",
    company_name: "VinAI",
    role: "researcher",
    team_size: "2_5",
    email_domain: "vinai.io",
    is_personal_email: false,
    use_case: "Fine-tune across the lab.",
    compute_sources: ["own_machines"],
    heard_from: "github",
    requested_at: "2026-08-01T00:00:00Z",
    pending_pool_name: null,
    invited_by_name: null,
    ...overrides,
  };
}

describe("labelFor", () => {
  it("renders the human label for a known value", () => {
    expect(labelFor(ROLE_OPTIONS, "ml_engineer")).toBe("ML engineer");
  });

  it("falls back to the raw value for an unrecognised one, rather than hiding it", () => {
    expect(labelFor(ROLE_OPTIONS, "quantum_wizard")).toBe("quantum_wizard");
  });

  it("renders an em dash for no value at all", () => {
    expect(labelFor(ROLE_OPTIONS, null)).toBe("—");
  });
});

describe("formatRequestedAt", () => {
  it("renders a day-precision date, not a time-of-day", () => {
    const out = formatRequestedAt("2026-08-01T12:00:00Z");
    expect(out).toMatch(/^[A-Za-z]+ \d{1,2}, \d{4}$/);
    expect(out).not.toMatch(/:/);
  });
});

describe("fullNameFor", () => {
  it("joins first and last name", () => {
    expect(fullNameFor(row())).toBe("Ha Nguyen");
  });

  it("falls back to email when both names are missing", () => {
    expect(
      fullNameFor(row({ first_name: null, last_name: null }))
    ).toBe("ha@vinai.io");
  });

  it("falls back to a plain label when there is no email either", () => {
    expect(
      fullNameFor(row({ first_name: null, last_name: null, email: null }))
    ).toBe("No name on file");
  });

  it("uses whichever single name is present", () => {
    expect(fullNameFor(row({ last_name: null }))).toBe("Ha");
  });
});

describe("inviteLine", () => {
  it("is null when the request has no pending pool", () => {
    expect(inviteLine(row())).toBeNull();
  });

  it("names the pool and the inviter", () => {
    expect(
      inviteLine(
        row({ pending_pool_name: "Lab GPUs", invited_by_name: "Minh" })
      )
    ).toBe("Invited to Lab GPUs by Minh");
  });

  it("falls back when the inviter has no display name on file", () => {
    expect(
      inviteLine(row({ pending_pool_name: "Lab GPUs", invited_by_name: null }))
    ).toBe("Invited to Lab GPUs by someone else");
  });
});

describe("restoreRequest", () => {
  it("re-inserts a row in requested_at order", () => {
    const early = row({ user_id: "a", requested_at: "2026-08-01T00:00:00Z" });
    const late = row({ user_id: "b", requested_at: "2026-08-03T00:00:00Z" });
    const middle = row({ user_id: "c", requested_at: "2026-08-02T00:00:00Z" });

    const restored = restoreRequest([early, late], middle);
    expect(restored.map((r) => r.user_id)).toEqual(["a", "c", "b"]);
  });

  it("restores the only row to an empty queue", () => {
    const only = row();
    expect(restoreRequest([], only)).toEqual([only]);
  });
});
