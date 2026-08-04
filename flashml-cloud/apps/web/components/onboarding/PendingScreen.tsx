"use client";

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
 * give someone waiting is to reload. */
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
        </CardContent>
      </Card>
    </div>
  );
}
