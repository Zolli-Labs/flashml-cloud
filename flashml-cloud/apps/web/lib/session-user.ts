"use client";

import { useEffect, useState } from "react";
import { createBrowserSupabaseClient } from "@/lib/supabase";

/** The identity-provider half of "who is signed in".
 *
 * Email and avatar come from Supabase's session, not from our own profiles
 * table: they belong to whoever the user authenticated with and we neither
 * store nor edit them. The FlashML half (display name, github login, roles)
 * comes from `GET /v1alpha1/me`.
 *
 * `undefined` means the session has not resolved yet, which is different
 * from `null` meaning signed out. Callers must not render a signed-out state
 * during the former or the nav flickers "Sign in" on every load. */
export interface SessionUser {
  email: string | null;
  /** Google and GitHub use different keys for the same thing, and a provider
   * that supplies neither is not an error. */
  avatarUrl: string | null;
  /** The provider's own idea of the user's name, used only as a placeholder
   * when they have not set a display name of their own. */
  providerName: string | null;
}

function read(meta: Record<string, unknown> | undefined): {
  avatarUrl: string | null;
  providerName: string | null;
} {
  const pick = (...keys: string[]): string | null => {
    for (const k of keys) {
      const v = meta?.[k];
      if (typeof v === "string" && v.length > 0) return v;
    }
    return null;
  };
  return {
    avatarUrl: pick("avatar_url", "picture"),
    providerName: pick("full_name", "name", "user_name", "preferred_username"),
  };
}

export function useSessionUser(): SessionUser | null | undefined {
  const [user, setUser] = useState<SessionUser | null | undefined>(undefined);

  useEffect(() => {
    const supabase = createBrowserSupabaseClient();
    supabase.auth.getUser().then(({ data }) => {
      if (!data.user) {
        setUser(null);
        return;
      }
      setUser({
        email: data.user.email ?? null,
        ...read(data.user.user_metadata as Record<string, unknown> | undefined),
      });
    });
    // Subscribing matters for the OAuth redirect: without it the nav keeps
    // showing "Sign in" until a full reload.
    const { data: sub } = supabase.auth.onAuthStateChange((_e, session) => {
      const u = session?.user;
      setUser(
        u
          ? {
              email: u.email ?? null,
              ...read(u.user_metadata as Record<string, unknown> | undefined),
            }
          : null
      );
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  return user;
}

/** Two initials for the avatar fallback. Falls back through display name,
 * provider name, then the local part of the email, because a user who signed
 * in with a provider that returns no name still needs something in the
 * circle that is not a generic silhouette. */
export function initialsFor(...candidates: (string | null | undefined)[]): string {
  for (const c of candidates) {
    if (!c) continue;
    const source = c.includes("@") ? c.split("@")[0] : c;
    const words = source.split(/[\s._-]+/).filter(Boolean);
    if (words.length === 0) continue;
    const letters =
      words.length === 1
        ? words[0].slice(0, 2)
        : words[0][0] + words[words.length - 1][0];
    return letters.toUpperCase();
  }
  return "?";
}
