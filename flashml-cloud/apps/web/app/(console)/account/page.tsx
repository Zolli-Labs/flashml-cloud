"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AccountDetailsForm } from "@/components/account/AccountDetailsForm";
import { AccountFacts } from "@/components/account/AccountFacts";
import { AccountIdentityCard } from "@/components/account/AccountIdentityCard";
import { ContributionsPanel } from "@/components/account/ContributionsPanel";
import { DisplayNameForm } from "@/components/account/DisplayNameForm";
import { SignOutPanel } from "@/components/account/SignOutPanel";
import { StoragePanel } from "@/components/account/StoragePanel";
import { PageHeader } from "@/components/shell/PageHeader";
import { PageShell } from "@/components/shell/PageShell";
import { StatePanel } from "@/components/shell/StatePanel";
import { resolvePanel } from "@/lib/console/panel-state";
import { useSessionUser } from "@/lib/session-user";
import { NotAuthenticated, getMe, type Profile } from "@/lib/cloud-api";

// Account page. Two sources, kept visibly separate because the user can
// change one and not the other:
//
//   identity provider  email, avatar        read-only, not ours
//   FlashML profile    display name, roles  display name is editable
//
// Showing a greyed-out "email" input the user cannot change would imply we
// own it. It is rendered as a value with the provider named instead.
//
// COMPOSITION ONLY. This file used to be 945 lines carrying four independent
// fetches, seven sections and every one of their local components. The
// sections now live in `components/account/`, each owning the read it needs,
// and this page's whole job is to sequence them and to own the ONE read that
// more than one of them depends on — `GET /me`.
//
// The three profile-driven sections are wrapped in a single `StatePanel`
// rather than one each, because they are one read: `getMe` either answered or
// it did not, and three copies of "Could not read this." stacked down the page
// would be three sentences about the same failure.
export default function AccountPage() {
  const router = useRouter();
  const session = useSessionUser();

  const [profile, setProfile] = useState<Profile | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // No `setLoading(true)` here. `loading` starts true, so the first read is
  // covered by the initial state, and calling setState synchronously in a
  // function the effect below invokes is a cascading render
  // (`react-hooks/set-state-in-effect`). On a RETRY it means the unreadable
  // panel stays put until the new read answers, rather than flipping to a
  // skeleton and back — which is the more honest of the two: the last thing
  // we know is still that the read failed.
  const load = useCallback(() => {
    return getMe()
      .then((p) => {
        setProfile(p);
        setLoadError(null);
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push("/sign-in?next=/account");
          return;
        }
        setLoadError(
          err instanceof Error ? err.message : "Couldn't load your profile."
        );
      })
      .finally(() => setLoading(false));
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  // `() => false`: a profile that loaded is never "empty". Every account has
  // an id and a created_at, and the fields that can be null are absences
  // within a present record, not an absent record.
  const panel = resolvePanel({ loading, error: loadError, data: profile }, () => false);

  return (
    <PageShell width="reading">
      <PageHeader title="Account" />

      {/* Outside the panel deliberately: this card is built from the SESSION,
          which is readable whether or not `GET /me` answered, and it is what
          still tells you which account you are looking at when the rest of the
          page cannot be read. */}
      <AccountIdentityCard session={session} profile={profile} />

      {/* `className` reaches the loading, empty and unreadable branches only —
          `present` renders its children raw — so this gives the three
          non-present states the panel box that the sections below draw for
          themselves. */}
      <StatePanel
        state={panel}
        className="panel mt-4"
        loadingRows={3}
        label="your profile"
        empty={{ title: "No profile on this account." }}
        unreadable={{ retry: load }}
      >
        {(p) => (
          <>
            <DisplayNameForm
              profile={p}
              providerName={session?.providerName}
              onSaved={setProfile}
            />
            <AccountDetailsForm profile={p} onSaved={setProfile} />
            <AccountFacts profile={p} />
          </>
        )}
      </StatePanel>

      <StoragePanel />
      <ContributionsPanel />
      <SignOutPanel />
    </PageShell>
  );
}
