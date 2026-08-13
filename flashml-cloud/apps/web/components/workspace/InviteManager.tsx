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
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
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
  /** Why the READ failed, in the API's words. Kept separate from `error`
   * above, which is why a WRITE (regenerate) failed — they are different
   * failures with different recoveries, and one string for both would put a
   * write error where the read's status line goes. */
  const [loadError, setLoadError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(() => {
    // No `setState("loading")` here: `load` is what the mount effect calls,
    // and a synchronous setState in an effect body double-renders on every
    // mount (`react-hooks/set-state-in-effect`). `state` is already
    // initialised to `"loading"`, which is the honest initial value for a
    // component that always fetches on mount. Going BACK to loading is the
    // retry's job, and that runs from a click — see `retry` below.
    getPoolInviteState(poolId)
      .then((s) => {
        setInviteState(s);
        setState("ready");
      })
      .catch((err) => {
        // DEFECT FIX. This was `.catch(() => setState("error"))` — the error
        // was not even bound, so the API's account of what went wrong was
        // discarded at the only point it existed, and the panel rendered one
        // fixed sentence for a 403, a 502 and a dead network alike. Keeping
        // the words is the same rule `lib/console/panel-state.ts` encodes as
        // `state.detail`: the reason belongs to the API, and a paraphrase is
        // more confident than the evidence behind it.
        setLoadError(
          err instanceof ApiError
            ? err.detail
            : err instanceof Error
              ? err.message
              : null
        );
        setState("error");
      });
  }, [poolId]);

  useEffect(() => {
    load();
  }, [load]);

  /** Re-run the read after a failure. From a click, so it may reset to
   * `loading` — the skeleton comes back and the previous failure's sentence
   * clears, instead of the old error sitting there unchanged while the new
   * request is in flight. */
  const retry = useCallback(() => {
    setState("loading");
    setLoadError(null);
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
        description: "It can no longer be used to join this Workspace.",
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
      {/* Joining and admission are two separate things (see
          `pools/join/page.tsx`): `acceptInvite` only adds a member outright
          when the caller is ALREADY admitted to FlashML. For anyone else the
          join is banked on their access request and materializes when an
          admin approves them — so this must not promise immediate
          membership. */}
      <p className="mt-1 text-xs text-muted-foreground">
        A standing link — anyone who opens it and signs in joins this
        Workspace if they&apos;re already admitted to Zolli Cloud; otherwise the
        join is saved and applied once an admin approves them. Either way it
        spends one use. Treat it like a password: it&apos;s good for the uses
        and time shown below, and Regenerate invalidates whatever copy is
        currently out.
      </p>

      {state === "loading" ? (
        <Skeleton className="mt-3 h-4 w-40" />
      ) : state === "error" ? (
        // DEFECT FIX (second half). The read could fail and leave no way to
        // re-run it: there was no retry here, so a transient 502 hid the
        // owner's invite controls until they reloaded the whole page. The
        // fixed sentence stays as the heading — it is true and it is about
        // us, per `UNREADABLE_TITLE`'s reasoning — and the API's own words
        // now sit under it when there were any.
        <div role="alert" className="mt-3">
          <p className="text-xs text-destructive">
            Couldn&apos;t load the current invite state.
          </p>
          {loadError && (
            <p className="mt-1 text-xs text-muted-foreground">{loadError}</p>
          )}
          <Button variant="outline" size="sm" className="mt-2" onClick={retry}>
            Try again
          </Button>
        </div>
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
              className="shrink-0 rounded-md border border-border bg-surface p-2 text-muted-foreground hover:bg-surface-2 hover:text-foreground"
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
          <p className="mt-2.5 flex items-start gap-1.5 text-xs text-warning-foreground">
            <Warning className="mt-0.5 h-3.5 w-3.5 shrink-0" weight="fill" />
            <span>
              This link is shown once. Copy it now — Zolli can&apos;t show
              it to you again.
            </span>
          </p>
        </>
      )}

      <div className="mt-3 flex items-center gap-3">
        <Button type="button" onClick={regenerate} disabled={working}>
          {working ? "Working…" : "Regenerate"}
        </Button>

        {inviteState && (
          <AlertDialog>
            <AlertDialogTrigger
              render={
                <Button
                  type="button"
                  variant="ghost"
                  disabled={working}
                  className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                >
                  Revoke
                </Button>
              }
            />
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Revoke this invite link?</AlertDialogTitle>
                <AlertDialogDescription>
                  Anyone still holding it can no longer use it to join this
                  Workspace. Members already in the Workspace are unaffected.
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
