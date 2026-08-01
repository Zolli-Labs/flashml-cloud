import { NextResponse, type NextRequest } from "next/server";
import { cookies } from "next/headers";
import { createServerSupabaseClient } from "@/lib/supabase";

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
  const next = searchParams.get("next") ?? "/machines";
  // Only ever redirect within this app — an open `next` param would make
  // this endpoint an open redirect off a trusted auth flow.
  const safeNext = next.startsWith("/") && !next.startsWith("//") ? next : "/machines";

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
      return NextResponse.redirect(`${origin}${safeNext}`);
    }
  }

  const failUrl = new URL("/sign-in", origin);
  failUrl.searchParams.set(
    "error",
    "Sign-in failed or the link expired. Request a new email link, or check that Google is enabled as a provider in the Supabase dashboard (see apps/web/README.md)."
  );
  return NextResponse.redirect(failUrl);
}
