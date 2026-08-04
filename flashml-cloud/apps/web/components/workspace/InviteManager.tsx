"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Copy, Warning } from "@phosphor-icons/react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import { formatInviteState } from "@/lib/pool-invite-state";
import {
  ApiError,
  createPoolInvite,
  getPoolInviteState,
  revokePoolInvites,
  type PoolInviteState,
} from "@/lib/cloud-api";

// ---------------------------------------------------------------------------
// Invite section — owner only, a single standing capped link
// ---------------------------------------------------------------------------

export function InviteManager({ poolId }: { poolId: string }) {
  const [inviteState, setInviteState] = useState<PoolInviteState | null>(
    null
  );
  const [state, setState] = useState<"loading" | "ready" | "error">(
    "loading"
  );
  const [link, setLink] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(() => {
    getPoolInviteState(poolId)
      .then((s) => {
        setInviteState(s);
        setState("ready");
      })
      .catch(() => {
        setState("error");
      });
  }, [poolId]);

  useEffect(() => {
    load();
  }, [load]);

  // Regenerate covers both "there's no link yet" and "replace the current
  // one" with the same call — revoking a pool with nothing outstanding is
  // a no-op on the API side (`revokePoolInvites`'s own contract), so there
  // is no separate "create" action to keep in sync with this one.
  async function regenerate() {
    setWorking(true);
    setError(null);
    setCopied(false);
    try {
      await revokePoolInvites(poolId);
      // The revoke above just succeeded, so whatever invite state and
      // link were on screen before this call now describe something
      // dead. Clear them here — immediately, before the mint even
      // starts — rather than in the catch below: if the revoke itself
      // had thrown, execution would never reach this line, and the
      // still-valid previous link stays displayed instead of vanishing
      // for a failure that didn't actually touch it. But once we're
      // past the revoke, a mint failure below must not leave a "N uses
      // left" description or a Copy button in front of a link that no
      // longer works.
      setInviteState(null);
      setLink(null);
      const { token } = await createPoolInvite(poolId, { uses: 10 });
      setLink(`${window.location.origin}/pools/join?token=${token}`);
      // Best effort: the new link is already in hand even if this refresh
      // fails, so a failure here doesn't roll anything back — it just
      // leaves the state line reading "no active invite" until the next
      // load, rather than a stale description of the revoked one.
      try {
        setInviteState(await getPoolInviteState(poolId));
      } catch {
        // leave inviteState null
      }
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : "Couldn't regenerate the invite link. Try again."
      );
    } finally {
      setWorking(false);
    }
  }

  async function revoke() {
    setWorking(true);
    try {
      await revokePoolInvites(poolId);
      setInviteState(null);
      setLink(null);
      toast.success("Invite link revoked", {
        description: "It can no longer be used to join this pool.",
      });
    } catch {
      toast.error("Couldn't revoke the invite link", {
        description: "Try again.",
      });
    } finally {
      setWorking(false);
    }
  }

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      toast.success("Invite link copied");
    } catch {
      toast.error("Your browser blocked clipboard access");
    }
  }

  return (
    <section className="panel p-5">
      <h2 className="text-sm font-semibold">Invite a teammate</h2>
      <p className="mt-1 text-xs text-muted-foreground">
        A standing link — anyone who opens it and signs in joins this pool.
        Treat it like a password: it&apos;s good for the uses and time shown
        below, and Regenerate invalidates whatever copy is currently out.
      </p>

      {state === "loading" ? (
        <div className="skeleton mt-3 h-4 w-40" />
      ) : state === "error" ? (
        <p className="mt-3 text-xs text-destructive">
          Couldn&apos;t load the current invite state.
        </p>
      ) : inviteState ? (
        <p className="mt-3 text-xs text-muted-foreground">
          {formatInviteState(inviteState)}
        </p>
      ) : (
        <p className="mt-3 text-xs text-muted-foreground">
          No active invite link.
        </p>
      )}

      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}

      {link && (
        <>
          <div className="mt-3 flex items-center gap-2">
            <Input
              readOnly
              value={link}
              onFocus={(e) => e.currentTarget.select()}
              aria-label="Invite link"
              className="font-mono text-xs"
            />
            <button
              type="button"
              onClick={copy}
              aria-label={copied ? "Copied" : "Copy invite link"}
              className="shrink-0 rounded-md border border-border p-2 text-muted-foreground hover:bg-white/[0.06] hover:text-foreground"
            >
              {copied ? (
                <Check
                  size={14}
                  weight="bold"
                  className="text-[var(--node-green)]"
                />
              ) : (
                <Copy size={14} />
              )}
            </button>
          </div>
          <p className="mt-2.5 flex items-start gap-1.5 text-xs text-[var(--warning)]">
            <Warning className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
            <span>
              This link is shown once. Copy it now — FlashML can&apos;t show
              it to you again.
            </span>
          </p>
        </>
      )}

      <div className="mt-3 flex items-center gap-3">
        <button
          type="button"
          onClick={regenerate}
          disabled={working}
          className="interactive rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {working ? "Working…" : "Regenerate"}
        </button>

        {inviteState && (
          <AlertDialog>
            <AlertDialogTrigger
              render={
                <button
                  type="button"
                  disabled={working}
                  className="rounded-md px-2.5 py-1.5 text-sm text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:cursor-not-allowed disabled:opacity-40"
                >
                  Revoke
                </button>
              }
            />
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Revoke this invite link?</AlertDialogTitle>
                <AlertDialogDescription>
                  Anyone still holding it can no longer use it to join this
                  pool. Members already in the pool are unaffected.
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Keep it</AlertDialogCancel>
                <AlertDialogAction
                  disabled={working}
                  onClick={revoke}
                  className="bg-destructive/15 text-destructive hover:bg-destructive/25"
                >
                  {working ? "Revoking…" : "Revoke"}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        )}
      </div>
    </section>
  );
}
