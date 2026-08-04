"use client";

import { useState } from "react";
import { Warning } from "@phosphor-icons/react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError, NotFound, acceptInvite } from "@/lib/cloud-api";
import { tokenFromInput } from "@/lib/invite-token";

/** Stands in for the whole console when `getMe()` reports `admitted: false`
 * (see `ConsoleShell`). A signed-in account with no invite gets exactly
 * one thing to do here: redeem one.
 *
 * On success this reloads the page rather than flipping local state — the
 * admission decision is made server-side, by `POST /v1alpha1/invites/accept`,
 * and a full reload is the one way to make the console re-derive its own
 * state (nav, this gate, every page under it) from what the API now says,
 * instead of this component guessing at what changed. */
export function InviteGate() {
  const [value, setValue] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    const token = tokenFromInput(value);
    if (!token) {
      setError("Paste your invite link or code.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await acceptInvite(token);
      window.location.reload();
    } catch (err) {
      // NotAuthenticated is not handled specially here: the console already
      // requires a session to reach the console shell at all, so a 401 mid
      // -submit means the session just expired. The generic message below
      // is honest about that without a special-cased redirect this one
      // corner doesn't need.
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

  return (
    <div className="flex min-h-[calc(100dvh-3.5rem)] items-center justify-center px-4 py-10">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>FlashML is invite-only right now</CardTitle>
          <CardDescription>
            Paste the invite link or code someone on FlashML sent you.
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
