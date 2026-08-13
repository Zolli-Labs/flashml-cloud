"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { SignOut } from "@phosphor-icons/react";
import { Avatar } from "@/components/shell/Avatar";
import { createBrowserSupabaseClient } from "@/lib/supabase";
import { initialsFor, useSessionUser } from "@/lib/session-user";
import { getMe, NotAuthenticated } from "@/lib/cloud-api";

/** Sign-in status: a "Sign in" link when signed out, the user's avatar and a
 * sign-out control when signed in. The avatar is the link to /account, which
 * is where every app of this shape puts it. */
export function UserMenu() {
  const router = useRouter();
  const session = useSessionUser();
  const [displayName, setDisplayName] = useState<string | null>(null);

  useEffect(() => {
    if (!session) return;
    // Best effort. The nav must render from the Supabase session alone: if
    // the API is unreachable the header should still show who is signed in
    // rather than collapsing to "Sign in", which would look like a logout.
    getMe()
      .then((p) => setDisplayName(p.display_name))
      .catch((err) => {
        if (err instanceof NotAuthenticated) setDisplayName(null);
      });
  }, [session]);

  async function signOut() {
    const supabase = createBrowserSupabaseClient();
    await supabase.auth.signOut();
    router.push("/sign-in");
    router.refresh();
  }

  // undefined = session not resolved. Reserve the space rather than flash
  // "Sign in" at a signed-in user on every navigation.
  if (session === undefined) return <div className="h-7 w-7" />;

  if (session === null) {
    return (
      <Link
        href="/sign-in"
        className="rounded-md px-3 py-1.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-primary/10 hover:text-brand-foreground"
      >
        Sign in
      </Link>
    );
  }

  const label = displayName || session.providerName || session.email || "Account";
  const initials = initialsFor(displayName, session.providerName, session.email);

  return (
    <div className="flex items-center gap-2">
      <Link
        href="/settings"
        title={session.email ?? undefined}
        className="flex items-center gap-2 rounded-md px-1.5 py-1 transition-colors hover:bg-primary/10"
      >
        <Avatar src={session.avatarUrl} initials={initials} size={26} />
        <span className="hidden max-w-40 truncate text-xs text-muted-foreground lg:inline">
          {label}
        </span>
      </Link>
      <button
        type="button"
        onClick={signOut}
        aria-label="Sign out"
        className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-primary/10 hover:text-brand-foreground"
      >
        <SignOut className="h-4 w-4" />
      </button>
    </div>
  );
}
