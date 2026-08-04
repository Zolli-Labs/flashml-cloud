import { describe, expect, it } from "vitest";
import { INVITE_ROUTE, screenFor } from "./access-screen";

describe("screenFor", () => {
  it("renders the console while the state is still unknown", () => {
    // The shell mounts once per console session. Showing a loading state on
    // every first paint would punish the overwhelming majority — already
    // -admitted returning users — for one round trip. Nothing this guards
    // is enforced only here; every state-creating route re-checks
    // server-side.
    expect(screenFor(undefined, "/overview")).toBe("console");
  });

  it("routes each state to its screen", () => {
    expect(screenFor("admitted", "/overview")).toBe("console");
    expect(screenFor("needs_onboarding", "/overview")).toBe("onboarding");
    expect(screenFor("pending", "/overview")).toBe("pending");
    expect(screenFor("declined", "/overview")).toBe("declined");
  });

  it("lets every state reach /pools/join", () => {
    // This is how an invite survives the wait: a pending account must be
    // able to redeem a link so the join is banked and applied on approval.
    for (const state of ["needs_onboarding", "pending", "declined"] as const) {
      expect(screenFor(state, INVITE_ROUTE)).toBe("console");
    }
  });

  it("does not treat a route merely starting with the invite path as the invite route", () => {
    expect(screenFor("pending", "/pools/joinery")).toBe("pending");
  });
});
