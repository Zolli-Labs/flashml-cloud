"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { SignOut } from "@phosphor-icons/react";
import { Button } from "@/components/ui/button";
import { createBrowserSupabaseClient } from "@/lib/supabase";

/** Sign-in status in the nav: a "Sign in" link when signed out, an email +
 * sign-out button when signed in. Subscribes to Supabase's auth state so it
 * updates immediately after the OAuth redirect completes, without a full
 * page reload. */
export function UserMenu() {
  const router = useRouter();
  const [email, setEmail] = useState<string | null | undefined>(undefined);

  useEffect(() => {
    const supabase = createBrowserSupabaseClient();
    supabase.auth.getUser().then(({ data }) => {
      setEmail(data.user?.email ?? null);
    });
    const { data: subscription } = supabase.auth.onAuthStateChange(
      (_event, session) => {
        setEmail(session?.user?.email ?? null);
      }
    );
    return () => subscription.subscription.unsubscribe();
  }, []);

  async function signOut() {
    const supabase = createBrowserSupabaseClient();
    await supabase.auth.signOut();
    router.push("/sign-in");
    router.refresh();
  }

  if (email === undefined) {
    // Session not resolved yet — render nothing rather than flash the
    // wrong state.
    return <div className="w-16" />;
  }

  if (!email) {
    return (
      <Link
        href="/sign-in"
        className="px-3 py-1.5 rounded-md text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-white/5 transition-colors"
      >
        Sign in
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-2">
      <span className="hidden lg:inline text-xs text-muted-foreground max-w-40 truncate">
        {email}
      </span>
      <Button type="button" variant="ghost" size="sm" onClick={signOut}>
        <SignOut className="w-3.5 h-3.5" data-icon="inline-start" />
        Sign out
      </Button>
    </div>
  );
}
