import { NextResponse, type NextRequest } from "next/server";
import { createServerClient } from "@supabase/ssr";
import { safeNext } from "@/lib/safe-next";

// Routes that render without a signed-in Supabase session. Everything else
// is private: a page rendering without a session is a bug (the API will
// 401 on first call), not a legitimate "logged out" state, so unauthenticated
// visitors are redirected before the page ever renders rather than left to
// discover a broken screen.
const PUBLIC_PATHS = ["/", "/sign-in", "/auth/callback"];

function isPublicPath(pathname: string): boolean {
  if (PUBLIC_PATHS.includes(pathname)) return true;
  // Next.js static assets / metadata files served from the app's public dir.
  if (pathname.startsWith("/_next")) return true;
  if (pathname === "/favicon.ico") return true;
  return false;
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

  let response = NextResponse.next({ request });

  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!supabaseUrl || !supabaseAnonKey) {
    // Misconfigured deploy. Do not silently let every page render as if
    // signed out (or, worse, as if signed in) — fail loud in the server
    // log and let the request through as unauthenticated; the API will
    // reject it with 401 rather than leak data.
    console.error(
      "middleware: NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY not set"
    );
    return response;
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

  const { pathname } = request.nextUrl;

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
    const next = safeNext(pathname + request.nextUrl.search);
    const redirectUrl = new URL("/sign-in", request.nextUrl.origin);
    redirectUrl.searchParams.set("next", next);
    return NextResponse.redirect(redirectUrl);
  }

  // Signed in and asking for /sign-in: send them to the console home, not
  // to /machines. Landing a returning user on a single resource list was a
  // stand-in for not having an overview page; there is one now.
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
