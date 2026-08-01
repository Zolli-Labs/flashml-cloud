import { describe, expect, it } from "vitest";
import { NextRequest } from "next/server";

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
