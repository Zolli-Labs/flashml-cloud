import { NextResponse, type NextRequest } from "next/server";
import { cookies } from "next/headers";
import { createServerSupabaseClient } from "@/lib/supabase";

// Supabase redirects here after either auth path hands back a PKCE code:
// Google's OAuth consent screen, or a clicked email magic link
// (`signInWithOtp` with `emailRedirectTo` pointed at this route). Exchanging
// the code for a session is what actually sets the auth cookies; everything
// before this point — the /sign-in form, Google's consent screen, the
// emailed link — is just getting the browser to this URL with a `code` on
// it.
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
