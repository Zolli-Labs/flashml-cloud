"use client";

import Link from "next/link";
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
 * It deliberately does NOT say an email is on its way. Nothing sends one:
 * this deployment has no email provider, the same constraint that removed
 * magic links from sign-in (`app/(auth)/sign-in/SignInCard.tsx` documents
 * it at length — "This flow sends no email at all"). Approval is silent and
 * the owner tells people by hand, so the only honest instruction we can
 * give someone waiting is to reload.
 *
 * The "Have an invite code?" link is the one other thing a pending account
 * can still do: `/pools` renders this screen instead of the paste-a-code
 * box for them, so without a link here a bare invite code (not a link) has
 * no path in at all. Points at `/pools/join`, the one route `screenFor`
 * (`lib/access-screen.ts`) keeps reachable in every access state. */
export function PendingScreen({ email }: { email: string | null }) {
  return (
    <div className="flex min-h-[calc(100dvh-3.5rem)] items-center justify-center px-4 py-10">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Request received</CardTitle>
          <CardDescription>
            A human reads every request — FlashML is a small alpha, not an
            automated signup.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col items-start gap-3">
          <p className="text-sm text-muted-foreground">
            We&apos;ll get back to you at{" "}
            <span className="text-foreground">
              {email ?? "the address you signed up with"}
            </span>
            .
          </p>
          <p className="text-xs text-muted-foreground">
            Already approved? Reload this page.
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
            className="text-xs text-muted-foreground hover:text-foreground hover:underline"
          >
            Have an invite code?
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}
