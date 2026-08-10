"use client";

import Link from "next/link";
import { ZolliCharacter } from "@/components/brand/ZolliCharacter";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/** Stands in for the whole console while `access` is `pending` — the
 * request is in and an admin has not decided yet.
 *
 * Approval and decline both send mail now (see
 * `docs/superpowers/specs/2026-08-10-transactional-email-design.md`), so
 * this screen may finally promise one. It still offers Reload, because a
 * person holding this tab open when the decision lands should not have to
 * wait for an inbox.
 *
 * The "Have an invite code?" link is the one other thing a pending account
 * can still do: `/pools` renders this screen instead of the paste-a-code
 * box for them, so without a link here a bare invite code (not a link) has
 * no path in at all. Points at `/pools/join`, the one route `screenFor`
 * (`lib/access-screen.ts`) keeps reachable in every access state. */
export function PendingScreen({ email }: { email: string | null }) {
  return (
    <div className="flex min-h-[calc(100dvh-3.5rem)] items-center justify-center px-4 py-10">
      <Card className="w-full max-w-sm border-border bg-surface shadow-md">
        <CardHeader className="items-center text-center">
          <ZolliCharacter
            role="keeper"
            size={88}
            mood="focused"
            label="Keeper is holding your place while access is reviewed"
          />
          <CardTitle className="font-display text-2xl">Request received</CardTitle>
          <CardDescription>
            A human reads every request — ZolliAI Cloud is a small alpha, not an
            automated signup.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col items-center gap-3 text-center">
          <p className="text-sm text-muted-foreground">
            We&rsquo;ll email you at{" "}
            <span className="font-medium text-foreground">
              {email ?? "the address you signed up with"}
            </span>{" "}
            as soon as a human has looked at it. Already approved? Reload
            this page.
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => window.location.reload()}
          >
            Reload
          </Button>
          <Link
            href="/pools/join"
            className="text-xs text-muted-foreground hover:text-brand-foreground hover:underline"
          >
            Have a workspace invite code?
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}
