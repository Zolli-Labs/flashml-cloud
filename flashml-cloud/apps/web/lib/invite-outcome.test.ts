import { describe, expect, it } from "vitest";
import { bankedJoinTail } from "./invite-outcome";

describe("bankedJoinTail", () => {
  it("names the pool and states approval, not membership", () => {
    expect(bankedJoinTail("Acme Lab")).toBe(
      "You'll join Acme Lab as soon as your access is approved."
    );
  });

  it("composes into the join-page card's copy", () => {
    expect(`Invite saved\n${bankedJoinTail("Acme Lab")}`).toBe(
      "Invite saved\nYou'll join Acme Lab as soon as your access is approved."
    );
  });

  it("composes into the paste-a-code box's honest copy", () => {
    expect(`Saved. ${bankedJoinTail("Acme Lab")}`).toBe(
      "Saved. You'll join Acme Lab as soon as your access is approved."
    );
  });
});
