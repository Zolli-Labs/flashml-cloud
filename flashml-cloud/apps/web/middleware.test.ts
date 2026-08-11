import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

// middleware.ts talks to Supabase only through @supabase/ssr's
// createServerClient — mock that boundary rather than let it construct a
// real client, the same pattern cloud-api.test.ts uses for the browser
// client. This matters beyond test isolation: @supabase/ssr's client pulls
// in supabase-js's realtime client, which probes for a native WebSocket at
// CONSTRUCTION time, not at connection time. Node 22 (this machine) has a
// global WebSocket and never notices; CI's Node 20 does not, so
// createServerClient() throws "Node.js detected but native WebSocket not
// found" before getUser() is ever reached — a CI-only failure. Mocking the
// boundary means no real client, and therefore no realtime client, is ever
// constructed.
const { createServerClient, getUser } = vi.hoisted(() => ({
  createServerClient: vi.fn(),
  getUser: vi.fn(),
}));
vi.mock("@supabase/ssr", () => ({
  createServerClient,
}));

import { middleware } from "./middleware";

/**
 * The magic link lands wherever Supabase decides, not where we asked.
 *
 * `emailRedirectTo` is honoured only when the exact URL is in the project's
 * Redirect URLs allowlist. When it is not, Auth falls back to the Site URL —
 * a bare origin — so the link arrives as `/?code=<uuid>` on a page with no
 * exchange logic, and sign-in silently does nothing. These pin the recovery.
 */
describe("auth code forwarding", () => {
  function get(url: string) {
    return middleware(new NextRequest(new Request(url)));
  }

  it("forwards a code that landed on the site root", async () => {
    const res = await get("http://localhost:3000/?code=abc-123");
    expect(res.status).toBe(307);
    const location = new URL(res.headers.get("location")!);
    expect(location.pathname).toBe("/auth/callback");
    expect(location.searchParams.get("code")).toBe("abc-123");
  });

  it("does not invent a next for a code that landed on the root", async () => {
    // `/` is the landing page, not a destination worth returning to; the
    // callback's own default (/machines) is the better answer.
    const res = await get("http://localhost:3000/?code=abc-123");
    const location = new URL(res.headers.get("location")!);
    expect(location.searchParams.has("next")).toBe(false);
  });

  it("preserves where the user was heading", async () => {
    const res = await get("http://localhost:3000/machines?code=abc-123");
    const location = new URL(res.headers.get("location")!);
    expect(location.pathname).toBe("/auth/callback");
    expect(location.searchParams.get("next")).toBe("/machines");
  });

  it("keeps an explicit next rather than overwriting it", async () => {
    const res = await get(
      "http://localhost:3000/jobs?code=abc-123&next=%2Fsubmit"
    );
    const location = new URL(res.headers.get("location")!);
    expect(location.searchParams.get("next")).toBe("/submit");
  });

  it("does not redirect the callback route to itself", async () => {
    // The loop this guards against would burn the code on the first hop and
    // then bounce forever.
    const res = await get("http://localhost:3000/auth/callback?code=abc-123");
    // No redirect at all: the request falls through to the route handler.
    expect(res.headers.get("location")).toBeNull();
  });

  it("leaves ordinary requests alone", async () => {
    const res = await get("http://localhost:3000/sign-in");
    const location = res.headers.get("location");
    expect(location === null || !location.includes("/auth/callback")).toBe(true);
  });
});

/**
 * A signed-out visitor on a protected path gets redirected to `/sign-in`
 * before any of it renders. `next` has to carry the FULL path — pathname
 * plus query string — or `/pools/join?token=...` (an invite link, which
 * exists specifically for a signed-out visitor) loses its token on the
 * round trip: it would survive only as a sibling `?token=...` on
 * `/sign-in` itself, which `SignInCard` never reads before calling
 * `window.location.assign(next)`.
 *
 * This branch only runs once the middleware gets past its own
 * "Supabase not configured" bailout, so — unlike the `code`-forwarding
 * tests above, which return before that check runs at all — these stub
 * NEXT_PUBLIC_SUPABASE_URL/ANON_KEY. The fake project is never actually
 * reached: `@supabase/ssr` is mocked (see the top of this file), so
 * `createServerClient` never builds a real client and `getUser()` resolves
 * whatever this block tells it to, offline and instantly.
 */
describe("signed-out redirect to /sign-in", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "https://fake-project.supabase.co");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "fake-anon-key");
    createServerClient.mockReset();
    createServerClient.mockReturnValue({ auth: { getUser } });
    getUser.mockReset();
    getUser.mockResolvedValue({ data: { user: null } });
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  function get(url: string) {
    return middleware(new NextRequest(new Request(url)));
  }

  it("carries the query string into next for a protected path", async () => {
    const res = await get("http://localhost:3000/pools/join?token=fmi_abc");
    expect(res.status).toBe(307);
    const location = new URL(res.headers.get("location")!);
    expect(location.pathname).toBe("/sign-in");
    expect(location.searchParams.get("next")).toBe("/pools/join?token=fmi_abc");
  });

  it("does not leave the token riding as a sibling param on /sign-in", async () => {
    const res = await get("http://localhost:3000/pools/join?token=fmi_abc");
    const location = new URL(res.headers.get("location")!);
    expect(location.searchParams.has("token")).toBe(false);
    // Exactly one param: `next`. Nothing else rode along from the original
    // request.
    expect([...location.searchParams.keys()]).toEqual(["next"]);
  });

  it("still works for a protected path with no query string at all", async () => {
    const res = await get("http://localhost:3000/machines");
    const location = new URL(res.headers.get("location")!);
    expect(location.searchParams.get("next")).toBe("/machines");
  });

  it("preserves multiple query params, not just the first", async () => {
    const res = await get(
      "http://localhost:3000/pools/join?token=fmi_abc&ref=email"
    );
    const location = new URL(res.headers.get("location")!);
    expect(location.searchParams.get("next")).toBe(
      "/pools/join?token=fmi_abc&ref=email"
    );
  });

  it("does not redirect a public path", async () => {
    const res = await get("http://localhost:3000/");
    expect(res.status).not.toBe(307);
  });

  it.each([
    "/",
    "/contact",
    "/privacy",
    "/terms",
    "/security",
    "/models/hero/fabric/everyday-machines.glb",
    "/auth/callback",
    "/manifest.webmanifest",
  ])("serves public path %s without constructing an auth client", async (pathname) => {
    const res = await get(`http://localhost:3000${pathname}`);

    expect(res.status).not.toBe(307);
    expect(createServerClient).not.toHaveBeenCalled();
    expect(getUser).not.toHaveBeenCalled();
  });

  it("still checks auth on /sign-in so signed-in visitors can be redirected", async () => {
    await get("http://localhost:3000/sign-in");

    expect(createServerClient).toHaveBeenCalledTimes(1);
    expect(getUser).toHaveBeenCalledTimes(1);
  });

  // `https://this-site.com//evil.com/foo` parses with pathname
  // `//evil.com/foo` — no scheme, so a naive check would wave it through,
  // but the leading `//` makes it protocol-relative. Unsanitized, this
  // pathname would ride into `next` verbatim and `SignInCard` would later
  // hand it straight to `window.location.assign`, leaving the site. See
  // `lib/safe-next.ts`.
  it("does not let a //evil.com pathname become an open redirect through next", async () => {
    const res = await get("http://localhost:3000//evil.com/foo");
    const location = new URL(res.headers.get("location")!);
    expect(location.pathname).toBe("/sign-in");
    expect(location.searchParams.get("next")).toBe("/machines");
  });
});

describe("missing Supabase configuration", () => {
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_URL", "");
    vi.stubEnv("NEXT_PUBLIC_SUPABASE_ANON_KEY", "");
    createServerClient.mockReset();
    getUser.mockReset();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  function get(url: string) {
    return middleware(new NextRequest(new Request(url)));
  }

  it("fails a private route closed through the exact signed-out redirect contract", async () => {
    const res = await get("http://localhost:3000/pools/join?token=fmi_abc");

    expect(res.status).toBe(307);
    expect(res.headers.get("location")).toBe(
      "http://localhost:3000/sign-in?next=%2Fpools%2Fjoin%3Ftoken%3Dfmi_abc"
    );
    expect(createServerClient).not.toHaveBeenCalled();
  });

  it.each(["/", "/contact", "/manifest.webmanifest", "/sign-in"])(
    "still serves %s when auth configuration is absent",
    async (pathname) => {
      const res = await get(`http://localhost:3000${pathname}`);

      expect(res.status).not.toBe(307);
      expect(createServerClient).not.toHaveBeenCalled();
    }
  );
});
