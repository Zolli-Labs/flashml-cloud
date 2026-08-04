"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { NotAuthenticated, acceptInvite } from "@/lib/cloud-api";

// This route is the one path every access state can reach (`INVITE_ROUTE`
// in `lib/access-screen.ts`): a signed-in-but-not-yet-admitted account has
// to be able to reach it, since this is where redeeming an invite happens.
// Joining and admission are one signal, not two: `acceptInvite`'s `joined`
// is `true` only for an already-admitted caller, who is added to the pool
// outright. For the account this bypass exists for, nothing joins yet —
// the membership is banked on the account's access request and
// materializes only once an admin approves them. `useSearchParams()` needs
// a Suspense boundary to avoid bailing the whole route out of static
// rendering, hence the split below rather than reading it straight in the
// default export — same shape as `(auth)/sign-in`'s `page.tsx` +
// `SignInCard.tsx`, folded into one file since console routes keep metadata
// in a sibling `layout.tsx` already.

function Loading() {
  return (
    <div className="flex min-h-[calc(100dvh-3.5rem)] items-center justify-center px-4 py-10">
      <div className="skeleton h-40 w-full max-w-sm rounded-lg" />
    </div>
  );
}

function LinkProblem() {
  return (
    <div className="flex min-h-[calc(100dvh-3.5rem)] items-center justify-center px-4 py-10">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>That invite link didn&apos;t work</CardTitle>
          <CardDescription>
            It may be mistyped, expired, or already used — the API folds all
            three into one answer, so we cannot say which. Ask whoever invited
            you for a fresh link, or find the workspace under{" "}
            <Link href="/pools" className="text-foreground underline">
              Pools
            </Link>
            .
          </CardDescription>
        </CardHeader>
      </Card>
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
        // admission once per mount, so a client-side route change would
        // land here with the shell still holding its stale gate state.
        // `result.joined` is `true` only when the caller was already
        // admitted and is now a member of this pool; when it is `false`,
        // nothing joined — the membership is queued behind admin approval
        // and materializes only once someone decides this account.
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

  // A missing, stale, mistyped, or already-used link must still leave the
  // visitor somewhere they can act instead of a dead end. This used to fall
  // back to `InviteGate`'s paste-a-token card, which is gone with the gate
  // (Task 11); Task 13 moves that affordance onto the pools page, which is
  // where this points in the meantime.
  if (!token || failed) return <LinkProblem />;

  return <Loading />;
}

export default function JoinPoolPage() {
  return (
    <Suspense fallback={<Loading />}>
      <JoinPoolInner />
    </Suspense>
  );
}
