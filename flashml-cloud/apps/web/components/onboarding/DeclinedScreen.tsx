"use client";

import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/** Stands in for the whole console while `access` is `declined`.
 *
 * There is no retry control on purpose: `POST /v1alpha1/access-request`
 * answers 409 once access is decided, so a "request again" button would
 * only produce an error. Re-opening a declined account is an admin action,
 * which is why the copy frames the decision as capacity rather than as a
 * verdict on the person. */
export function DeclinedScreen() {
  return (
    <div className="flex min-h-[calc(100dvh-3.5rem)] items-center justify-center px-4 py-10">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Not right now</CardTitle>
          <CardDescription>
            Your request wasn&apos;t approved for this alpha. That&apos;s a
            capacity decision, not a permanent one.
          </CardDescription>
        </CardHeader>
      </Card>
    </div>
  );
}
