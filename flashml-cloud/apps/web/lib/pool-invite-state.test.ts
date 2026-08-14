import { describe, expect, it } from "vitest";
import { formatInviteExpiry, formatInviteState } from "./pool-invite-state";
import type { PoolInviteState } from "./cloud-api";

const NOW = new Date("2026-08-03T12:00:00Z").getTime();

function state(overrides: Partial<PoolInviteState> = {}): PoolInviteState {
  return {
    uses_remaining: 10,
    expires_at: "2026-09-02T12:00:00Z", // 30d out from NOW
    created_at: "2026-08-03T12:00:00Z",
    ...overrides,
  };
}

describe("formatInviteState", () => {
  it("pluralises uses remaining", () => {
    expect(formatInviteState(state({ uses_remaining: 5 }), NOW)).toBe(
      "5 uses left · expires in 30d"
    );
  });

  it("keeps 'use' singular for exactly one remaining", () => {
    expect(formatInviteState(state({ uses_remaining: 1 }), NOW)).toBe(
      "1 use left · expires in 30d"
    );
  });

  it("reports days for an expiry more than a day out", () => {
    expect(
      formatInviteState(state({ expires_at: "2026-08-05T12:00:00Z" }), NOW)
    ).toBe("10 uses left · expires in 2d");
  });

  it("reports hours for an expiry under a day out", () => {
    expect(
      formatInviteState(state({ expires_at: "2026-08-03T18:00:00Z" }), NOW)
    ).toBe("10 uses left · expires in 6h");
  });

  it("reports minutes for an expiry under an hour out", () => {
    expect(
      formatInviteState(state({ expires_at: "2026-08-03T12:30:00Z" }), NOW)
    ).toBe("10 uses left · expires in 30m");
  });

  it("reports seconds for an expiry under a minute out", () => {
    expect(
      formatInviteState(state({ expires_at: "2026-08-03T12:00:45Z" }), NOW)
    ).toBe("10 uses left · expires in 45s");
  });

  it("treats an already-passed expiry as expired, not a negative duration", () => {
    expect(
      formatInviteState(state({ expires_at: "2026-08-01T00:00:00Z" }), NOW)
    ).toBe("10 uses left · expired");
  });

  it("treats an expiry equal to now as expired", () => {
    expect(
      formatInviteState(state({ expires_at: "2026-08-03T12:00:00Z" }), NOW)
    ).toBe("10 uses left · expired");
  });

  it("falls back to 'expiry unknown' for an unparseable timestamp", () => {
    expect(
      formatInviteState(state({ expires_at: "not-a-date" }), NOW)
    ).toBe("10 uses left · expiry unknown");
  });
});

describe("formatInviteExpiry", () => {
  // The same cases formatInviteState covers, minus the uses-remaining
  // prefix — this is the piece `InviteManager.tsx` renders beside the
  // uses-remaining StatTile rather than welded to it in one sentence.
  it("carries none of the uses-remaining prefix", () => {
    expect(formatInviteExpiry(state({ uses_remaining: 5 }), NOW)).toBe(
      "expires in 30d"
    );
    expect(formatInviteExpiry(state({ uses_remaining: 1 }), NOW)).toBe(
      "expires in 30d"
    );
  });

  it("reports hours for an expiry under a day out", () => {
    expect(
      formatInviteExpiry(state({ expires_at: "2026-08-03T18:00:00Z" }), NOW)
    ).toBe("expires in 6h");
  });

  it("treats an already-passed expiry as expired, not a negative duration", () => {
    expect(
      formatInviteExpiry(state({ expires_at: "2026-08-01T00:00:00Z" }), NOW)
    ).toBe("expired");
  });

  it("treats an expiry equal to now as expired", () => {
    expect(
      formatInviteExpiry(state({ expires_at: "2026-08-03T12:00:00Z" }), NOW)
    ).toBe("expired");
  });

  it("falls back to 'expiry unknown' for an unparseable timestamp", () => {
    expect(formatInviteExpiry(state({ expires_at: "not-a-date" }), NOW)).toBe(
      "expiry unknown"
    );
  });
});
