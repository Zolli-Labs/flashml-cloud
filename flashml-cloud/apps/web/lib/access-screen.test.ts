import { describe, expect, it } from "vitest";
import { ADMIN_ROUTE, INVITE_ROUTE, screenFor } from "./access-screen";

describe("screenFor", () => {
  it("pins the invite route's value", () => {
    // The value is a contract twice over: with the route directory on disk
    // (`app/(console)/pools/join/`) and with a second team building against
    // it. Every other test here uses the imported constant, so without this
    // line the value could drift to anything and stay green while the one
    // route that must survive every access state stopped being reachable.
    expect(INVITE_ROUTE).toBe("/pools/join");
  });

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

// Regression: ISSUE-002 — the optimistic default is right for ordinary
// console pages and wrong for the admin queue. A signed-in account that was
// neither admitted nor admin watched "Access requests" paint in full —
// heading, skeleton rows, workspace picker — for 1–2s before the gate
// replaced it. No data leaked (the API 401s independently and no request
// even fired), but a page nobody but an admin should see was on screen.
//
// The trade-off that justifies optimism elsewhere inverts here: the admin
// queue is visited rarely and only by admins, so waiting one round trip
// costs almost nothing, while the flash is exactly the wrong thing to show.
// Found by hands-on QA on 2026-08-04.
// Report: .gstack/qa-reports/qa-report-flashml-console-2026-08-04.md
describe("screenFor on admin routes", () => {
  it("waits for the answer instead of guessing", () => {
    expect(screenFor(undefined, ADMIN_ROUTE)).toBe("loading");
    expect(screenFor(undefined, `${ADMIN_ROUTE}/anything`)).toBe("loading");
  });

  it("still routes a known state normally once it arrives", () => {
    expect(screenFor("admitted", ADMIN_ROUTE)).toBe("console");
    expect(screenFor("pending", ADMIN_ROUTE)).toBe("pending");
    expect(screenFor("needs_onboarding", ADMIN_ROUTE)).toBe("onboarding");
    expect(screenFor("declined", ADMIN_ROUTE)).toBe("declined");
  });

  it("leaves every other route optimistic", () => {
    for (const path of ["/overview", "/jobs", "/account/machines", "/w/abc/people"]) {
      expect(screenFor(undefined, path)).toBe("console");
    }
  });

  // A route merely starting with the same letters is a different route.
  it("does not match a lookalike path", () => {
    expect(screenFor(undefined, "/administration")).toBe("console");
  });
});
