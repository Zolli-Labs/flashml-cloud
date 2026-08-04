import { describe, expect, it } from "vitest";
import { approveNotFoundMessage } from "./activate-errors";

describe("approveNotFoundMessage", () => {
  it("tells a not-yet-member to accept the pool invite first", () => {
    expect(approveNotFoundMessage("unknown pool")).toBe(
      "You're not a member of that pool yet — accept the pool invite first, then approve here."
    );
  });

  it("falls back to the generic bad-code message for 'unknown code'", () => {
    expect(approveNotFoundMessage("unknown code")).toBe(
      "We couldn't find that code. Check it against the laptop's screen — codes are only valid for a few minutes."
    );
  });

  it("falls back to the generic message for any other/unrecognized detail", () => {
    expect(approveNotFoundMessage("not found")).toBe(
      "We couldn't find that code. Check it against the laptop's screen — codes are only valid for a few minutes."
    );
  });
});
