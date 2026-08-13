"use client";

import { Avatar } from "@/components/shell/Avatar";
import { initialsFor, type SessionUser } from "@/lib/session-user";
import type { Profile } from "@/lib/cloud-api";

/**
 * Who you are signed in as, from the two sources that answer it.
 *
 * NO `StatePanel` HERE, deliberately. This card's content comes from the
 * SESSION, which is read by `useSessionUser` and is available whether or not
 * `GET /me` succeeded — so a failed profile read must not blank it out. It is
 * the one thing on this page that still tells you which account you are
 * looking at when everything else on it is unreadable, and that is exactly
 * when it is most worth having.
 *
 * `profile` is therefore optional and additive: when it loaded and carries a
 * display name, that name wins; otherwise the provider's own name or email
 * stands in. Every branch renders a value one of the two sources actually
 * returned — never a placeholder standing in for one.
 */
export function AccountIdentityCard({
  session,
  profile,
}: {
  /** `undefined` while the session is still being read, `null` when there is
   * none. The two are not merged: the ellipsis below belongs to `undefined`
   * only. */
  session: SessionUser | null | undefined;
  /** `null` while the profile read is in flight or after it failed. This card
   * works without it. */
  profile: Profile | null;
}) {
  const initials = initialsFor(
    profile?.display_name,
    session?.providerName,
    session?.email
  );

  return (
    <section className="panel mt-6 flex items-center gap-4 p-5">
      <Avatar src={session?.avatarUrl ?? null} initials={initials} size={64} />
      <div className="min-w-0">
        <div className="truncate text-lg font-semibold">
          {profile?.display_name ||
            session?.providerName ||
            session?.email ||
            "…"}
        </div>
        <div className="truncate font-mono text-sm text-muted-foreground">
          {session === undefined ? "…" : (session?.email ?? "no email on file")}
        </div>
      </div>
    </section>
  );
}
