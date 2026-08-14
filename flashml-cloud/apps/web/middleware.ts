import { NextResponse, type NextRequest } from "next/server";
import { createServerClient } from "@supabase/ssr";
import { safeNext } from "@/lib/safe-next";

// Routes that render without a signed-in Supabase session. Everything else
// is private: a page rendering without a session is a bug (the API will
// 401 on first call), not a legitimate "logged out" state, so unauthenticated
// visitors are redirected before the page ever renders rather than left to
// discover a broken screen.
const PUBLIC_PATHS = [
  "/",
  "/sign-in",
  "/auth/callback",
  "/contact",
  "/privacy",
  "/terms",
  "/security",
  "/manifest.webmanifest",
  // The live-network demo — a no-login page showing the real fleet and
  // letting a visitor drive one run on each coordinator. Public for the
  // same reason `/share/<token>` is: it is judged over a weekend, by people
  // with no account and nobody around to approve one.
  //
  // A LITERAL HERE, NOT A PREFIX RULE. Unlike the share page, nothing about
  // this route is parameterised — there is exactly one URL — so it belongs
  // in this list, where `includes` gives an exact match for free and there
  // is no pattern to get wrong. `/demo/x`, `/demos` and `/demo/../jobs` are
  // all private, which is what a `startsWith("/demo")` would have quietly
  // given away. See `SHARE_PATH` below for why that direction of mistake is
  // the one worth guarding against.
  "/demo",
];

// The public evidence page, `/share/<share_token>` — the ONE authenticated-
// product route that answers without a session, and the reason the exact
// shape of this pattern matters more than the others above.
//
// It is a prefix rule and not an entry in PUBLIC_PATHS because the token is
// part of the path. That makes it the only rule here that could, written
// carelessly, open something that is not this page — so it is pinned tighter
// than `startsWith` in three ways:
//
//   - Anchored at both ends. `/share/abc/../machines` and `/share/abc/edit`
//     do not match: the token is ONE path segment, with no slash in it.
//   - `/share` and `/share/` alone do not match, and neither does
//     `/shareholders` — the `/share/` prefix plus a non-empty segment is
//     required, so no sibling route can be reached by resembling this one.
//   - The character class is exactly what `secrets.token_urlsafe` produces
//     (`shr_` + base64url). No `%`, no `.`, no `:` — a percent-encoded
//     traversal attempt fails the match and falls through to the signed-out
//     redirect, which is the safe direction to fail in.
//
// Widening WITHIN this pattern opens nothing but the share page itself, which
// is public by design and holds no full identifiers (see the route's own
// docstring and `SESSION_SHARE_COLUMNS` upstream). Widening OUTSIDE it — a
// bare `startsWith("/share")`, say — is how a matcher meant to satisfy one
// requirement quietly unauthenticates the console.
const SHARE_PATH = /^\/share\/[A-Za-z0-9_-]{1,128}$/;

export function isPublicPath(pathname: string): boolean {
  if (PUBLIC_PATHS.includes(pathname)) return true;
  if (SHARE_PATH.test(pathname)) return true;
  // Next.js static assets / metadata files served from the app's public dir.
  if (pathname.startsWith("/_next")) return true;
  if (pathname.startsWith("/models/")) return true;
  if (pathname === "/favicon.ico") return true;
  return false;
}

export function buildSignedOutRedirect(
  requestUrl: Pick<URL, "origin" | "pathname" | "search">
): URL {
  const next = safeNext(requestUrl.pathname + requestUrl.search);
  const redirectUrl = new URL("/sign-in", requestUrl.origin);
  redirectUrl.searchParams.set("next", next);
  return redirectUrl;
}

// Supabase does not always send the browser where we asked. `emailRedirectTo`
// is honoured only when that exact URL is in the project's Redirect URLs
// allowlist; otherwise Auth falls back to the project's Site URL, which is a
// bare origin with no path. The magic link then lands on `/` as
// `/?code=<uuid>` — a page with no exchange logic — and sign-in appears to do
// nothing whatsoever. Nothing is logged, in the browser or on the server: the
// code simply sits in the address bar unredeemed.
//
// Rather than let working sign-in depend on a dashboard field matching a
// deploy URL, catch the code wherever it lands and forward it to the route
// that knows what to do with it. The allowlist should still be configured
// (apps/web/README.md), but this makes getting it wrong a tidiness problem
// rather than an outage — including on the day the deployed URL changes and
// nobody remembers this setting exists.
function forwardAuthCode(request: NextRequest): NextResponse | null {
  const { pathname, searchParams } = request.nextUrl;
  // Already where it belongs; redirecting again would loop.
  if (pathname === "/auth/callback") return null;
  if (!searchParams.has("code")) return null;

  const target = request.nextUrl.clone();
  target.pathname = "/auth/callback";
  // Preserve intent: a code that landed on /machines should still finish at
  // /machines rather than the callback's default.
  if (!target.searchParams.has("next") && pathname !== "/") {
    target.searchParams.set("next", pathname);
  }
  return NextResponse.redirect(target);
}

export async function middleware(request: NextRequest) {
  // Before the session check: this runs for signed-out visitors, which is
  // precisely who is completing a sign-in.
  const forwarded = forwardAuthCode(request);
  if (forwarded) return forwarded;

  const { pathname } = request.nextUrl;

  // Public content must not depend on Supabase availability or pay the cost
  // of constructing an auth client. /sign-in is the sole exception: when
  // auth is configured it verifies the session so a returning user can be
  // sent straight to the console.
  if (isPublicPath(pathname) && pathname !== "/sign-in") {
    return NextResponse.next({ request });
  }

  let response = NextResponse.next({ request });

  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!supabaseUrl || !supabaseAnonKey) {
    // A missing auth client cannot establish a private session. Keep the
    // sign-in screen available so the deployment can recover, but fail all
    // private routes closed through the same signed-out redirect contract.
    console.error(
      "middleware: NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY not set"
    );
    return pathname === "/sign-in"
      ? response
      : NextResponse.redirect(buildSignedOutRedirect(request.nextUrl));
  }

  const supabase = createServerClient(supabaseUrl, supabaseAnonKey, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet) {
        cookiesToSet.forEach(({ name, value }) =>
          request.cookies.set(name, value)
        );
        response = NextResponse.next({ request });
        cookiesToSet.forEach(({ name, value, options }) =>
          response.cookies.set(name, value, options)
        );
      },
    },
  });

  // getUser() (not getSession()) so the session is verified against
  // Supabase rather than trusted from a cookie that could be stale or
  // forged; this call is also what refreshes an expiring session and
  // writes the refreshed cookies via setAll above.
  const {
    data: { user },
  } = await supabase.auth.getUser();

  if (!user && !isPublicPath(pathname)) {
    // `pathname` alone drops the query string — for most routes that is
    // nothing worth keeping, but `/pools/join?token=...` is exactly a
    // route whose entire reason for existing is that one query param. Build
    // `next` from the full path so it survives the round trip through
    // sign-in.
    //
    // A FRESH URL, not `request.nextUrl.clone()`: cloning carries every
    // param on the original request along as a SIBLING of `next` on
    // `/sign-in` (e.g. `/sign-in?token=fmi_abc&next=%2Fpools%2Fjoin`) —
    // `token` would ride there unused (`SignInCard` only ever reads
    // `next`) while `next` itself still lost the query string, so the
    // token effectively vanishes for a signed-out invitee: it survives on
    // the wrong param, unreachable, while the one param that's read gets
    // handed a bare path.
    //
    // `safeNext`, not `pathname` raw: `pathname` is normally safe, but a
    // request URL like `https://this-site.com//evil.com/foo` parses with
    // `pathname === "//evil.com/foo"` — a protocol-relative value that
    // `SignInCard` would later hand straight to `window.location.assign`,
    // leaving the site. See `lib/safe-next.ts`.
    return NextResponse.redirect(buildSignedOutRedirect(request.nextUrl));
  }

  // Signed in and asking for /sign-in: send them to the console home, not
  // to /machines. Landing a returning user on a single resource list was a
  // stand-in for not having an overview page; there is one now.
  //
  // `/overview` is not itself a page any more — it resolves to a specific
  // workspace (`WorkspaceResolver`, reading the last-visited-workspace
  // cookie and the caller's pool membership) or to `/workspaces` if there
  // is none. Middleware cannot make that call itself: it runs on the edge,
  // before any component tree exists, with no access to the pool list
  // `resolveWorkspace` needs — so it hands off to the one fixed URL that is
  // always safe to redirect to, and lets client-side resolution pick the
  // actual workspace.
  if (user && pathname === "/sign-in") {
    const redirectUrl = request.nextUrl.clone();
    redirectUrl.pathname = "/overview";
    redirectUrl.searchParams.delete("next");
    return NextResponse.redirect(redirectUrl);
  }

  return response;
}

export const config = {
  matcher: [
    // Run on every request except static assets and image optimization
    // files, which never need a session and would only add latency.
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
