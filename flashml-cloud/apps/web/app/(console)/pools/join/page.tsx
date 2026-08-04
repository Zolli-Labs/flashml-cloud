"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Warning } from "@phosphor-icons/react";
import { toast } from "sonner";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError, NotAuthenticated, NotFound, acceptInvite } from "@/lib/cloud-api";
import { bankedJoinTail } from "@/lib/invite-outcome";
import { tokenFromInput } from "@/lib/invite-token";

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

/** Rendered once `acceptInvite` has succeeded but `joined` came back
 * `false` — the join is banked, not applied. Shared by both ways to land
 * here: a clicked link (the auto-redeem effect below) and a pasted code
 * (`JoinByCode`), so the confirmation reads identically either way. */
function InviteSaved({ name }: { name: string }) {
  return (
    <div className="flex min-h-[calc(100dvh-3.5rem)] items-center justify-center px-4 py-10">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Invite saved</CardTitle>
          <CardDescription>{bankedJoinTail(name)}</CardDescription>
        </CardHeader>
      </Card>
    </div>
  );
}

/** No-token / failed-token fallback — the paste-a-code affordance
 * `InviteGate` used to own before it was deleted (Task 11). This route is
 * the right home for it: `screenFor` (`lib/access-screen.ts`) returns
 * `"console"` for `INVITE_ROUTE` in every access state, so it is the one
 * page guaranteed reachable by the people who need to redeem a code they
 * typed rather than clicked — including a `"pending"` account, for whom
 * `/pools` itself renders the waiting screen instead. */
function JoinByCode({ invalidLink }: { invalidLink: boolean }) {
  const router = useRouter();
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [banked, setBanked] = useState<string | null>(null);

  async function submit() {
    const token = tokenFromInput(value);
    if (!token) {
      setError("Paste your invite link or code.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const result = await acceptInvite(token);
      if (!result.joined) {
        // Say so honestly instead of claiming membership: nothing joined
        // yet, the membership is banked on the access request.
        toast.success(`Saved. ${bankedJoinTail(result.name)}`);
        setBanked(result.name);
        return;
      }
      toast.success(`Joined ${result.name}`);
      router.push("/pools");
    } catch (err) {
      // NotAuthenticated is not handled specially here: reaching this page
      // already required a session, so a 401 mid-submit means the session
      // just expired. The generic message below is honest about that
      // without a special-cased redirect this one corner doesn't need.
      if (err instanceof NotFound) {
        setError("That invite link isn't valid, or it's already been used.");
      } else {
        setError(
          err instanceof ApiError
            ? err.detail
            : "Couldn't redeem that invite. Try again."
        );
      }
      setSubmitting(false);
    }
  }

  if (banked) return <InviteSaved name={banked} />;

  return (
    <div className="flex min-h-[calc(100dvh-3.5rem)] items-center justify-center px-4 py-10">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>
            {invalidLink ? "That invite link didn't work" : "Join with a code"}
          </CardTitle>
          <CardDescription>
            {invalidLink
              ? "It may be mistyped, expired, or already used — the API folds all three into one answer, so we cannot say which. Paste a fresh invite link or code below."
              : "Paste the invite link or code someone on FlashML sent you."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
            className="flex flex-col gap-3"
          >
            <Input
              autoFocus
              value={value}
              onChange={(e) => {
                setValue(e.target.value);
                setError(null);
              }}
              placeholder="Invite link or code"
              aria-label="Invite link or code"
              aria-invalid={!!error || undefined}
              className="font-mono"
            />
            {error && (
              <p
                role="alert"
                className="flex items-start gap-1.5 text-xs text-destructive"
              >
                <Warning className="mt-0.5 h-3 w-3 shrink-0" weight="fill" />
                <span>{error}</span>
              </p>
            )}
            <button
              type="submit"
              disabled={submitting || value.trim().length === 0}
              className="interactive rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {submitting ? "Joining…" : "Join"}
            </button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

function JoinPoolInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const [failed, setFailed] = useState(false);
  const [banked, setBanked] = useState<string | null>(null);

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
        if (!result.joined) {
          setBanked(result.name);
          return;
        }
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

  if (banked) return <InviteSaved name={banked} />;

  // A missing, stale, mistyped, or already-used link must still leave the
  // visitor somewhere they can act instead of a dead end: `JoinByCode`,
  // the paste-a-code affordance `InviteGate` used to own before it was
  // deleted (Task 11).
  if (!token || failed) return <JoinByCode invalidLink={!!token && failed} />;

  return <Loading />;
}

export default function JoinPoolPage() {
  return (
    <Suspense fallback={<Loading />}>
      <JoinPoolInner />
    </Suspense>
  );
}
