"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { InviteGate } from "@/components/shell/InviteGate";
import { NotAuthenticated, acceptInvite } from "@/lib/cloud-api";

// This route is `ConsoleShell`'s one deliberate bypass of the invite gate
// (see `INVITE_GATE_BYPASS`): a signed-in-but-not-yet-admitted account has
// to be able to reach it, since redeeming the token here IS how they become
// admitted. `useSearchParams()` needs a Suspense boundary to avoid bailing
// the whole route out of static rendering, hence the split below rather
// than reading it straight in the default export — same shape as
// `(auth)/sign-in`'s `page.tsx` + `SignInCard.tsx`, folded into one file
// since console routes keep metadata in a sibling `layout.tsx` already.

function Loading() {
  return (
    <div className="flex min-h-[calc(100dvh-3.5rem)] items-center justify-center px-4 py-10">
      <div className="skeleton h-40 w-full max-w-sm rounded-lg" />
    </div>
  );
}

function JoinPoolInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    // No token to redeem — handled by the render-time check below instead
    // of `setFailed` here, so this effect never sets state synchronously
    // within its own body.
    if (!token) return;
    let cancelled = false;
    acceptInvite(token)
      .then((result) => {
        if (cancelled) return;
        // A full navigation, not `router.replace`: `ConsoleShell` reads
        // admission once per mount, and this account just became admitted
        // — a client-side route change would land on the pool page with
        // the shell still holding its stale "gated" state and showing the
        // invite gate again instead of the pool it was just added to.
        window.location.href = `/pools/${result.pool_id}`;
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof NotAuthenticated) {
          const next = `/pools/join?token=${encodeURIComponent(token)}`;
          router.push(`/sign-in?next=${encodeURIComponent(next)}`);
          return;
        }
        // Missing, unknown, expired, or already-used all land here — the
        // API folds them into one 404 on purpose (`consume_pool_invite`'s
        // own doctrine), so this cannot say which of those it was either.
        setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [token, router]);

  // Fall back to the same paste-a-token card the console shows an
  // un-admitted account, so a missing, stale, mistyped, or already-used
  // link still leaves the visitor somewhere they can act instead of a dead
  // end.
  if (!token || failed) return <InviteGate />;

  return <Loading />;
}

export default function JoinPoolPage() {
  return (
    <Suspense fallback={<Loading />}>
      <JoinPoolInner />
    </Suspense>
  );
}
