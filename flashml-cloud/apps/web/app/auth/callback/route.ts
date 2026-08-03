import { NextResponse, type NextRequest } from "next/server";
import { cookies } from "next/headers";
import { createServerSupabaseClient } from "@/lib/supabase";
import { safeNext } from "@/lib/safe-next";

// Supabase redirects here with a PKCE code after Google's OAuth consent
// screen. Exchanging that code for a session is what sets the auth cookies;
// everything before this point is just getting the browser to this URL with
// a `code` on it.
//
// Email/password sign-in never reaches here — `signInWithPassword` returns a
// session directly in the browser. This route is live only for OAuth
// providers, plus any email link issued before the magic-link flow was
// removed (apps/web/README.md explains why it was).
//
// `middleware.ts` forwards a `code` landing on any other path to this route,
// because Supabase falls back to the project's Site URL whenever the
// requested redirect is not on its allow-list.
export async function GET(request: NextRequest) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get("code");
  // Only ever redirect within this app — an open `next` param would make
  // this endpoint an open redirect off a trusted auth flow. `safeNext` is
  // the one guard every `next` read in this app shares (`lib/safe-next.ts`)
  // — this route used to carry its own inline copy of the same check,
  // which (like `SignInCard`'s former default) missed that a browser
  // resolves a leading backslash the same as a leading slash, so
  // `/\evil.com` passed a plain `startsWith("/") && !startsWith("//")`
  // test and still left the site.
  const next = safeNext(searchParams.get("next"));

  if (code) {
    const cookieStore = await cookies();
    const supabase = createServerSupabaseClient({
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        cookiesToSet.forEach(({ name, value, options }) =>
          cookieStore.set(name, value, options)
        );
      },
    });
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      return NextResponse.redirect(`${origin}${next}`);
    }
  }

  const failUrl = new URL("/sign-in", origin);
  failUrl.searchParams.set(
    "error",
    "Sign-in failed or the link expired. Request a new email link, or check that Google is enabled as a provider in the Supabase dashboard (see apps/web/README.md)."
  );
  return NextResponse.redirect(failUrl);
}
