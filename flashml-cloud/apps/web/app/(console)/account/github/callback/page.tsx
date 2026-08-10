"use client";

/**
 * Where GitHub sends someone after they install the App.
 *
 * This page does one thing: take `installation_id` and `state` out of the
 * query string and hand both to the API together. The state is what proves
 * this account started the flow — the id alone is not a secret and binding
 * on it would let anyone who learned one attach somebody else's
 * organisation to their account.
 *
 * `setup_action` also arrives. GitHub sends `install` for a new
 * installation and `update` when someone edits which repositories the App
 * can see. Both are worth posting: an `update` refreshes
 * `repository_selection`, and the API's insert is idempotent.
 */

import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { CheckCircle, Warning } from "@phosphor-icons/react";
import { Button } from "@/components/ui/button";
import { connectGitHubInstallation } from "@/lib/cloud-api";

function Callback() {
  const router = useRouter();
  const params = useSearchParams();
  const [state, setState] = useState<"working" | "done" | "error">("working");
  const [message, setMessage] = useState("");
  // React 18 StrictMode mounts effects twice in development. The install
  // state is single-use by design, so a second POST would answer 403 and
  // show a failure on a connection that actually succeeded.
  const started = useRef(false);

  // Derived during render, not set from inside the effect: a malformed
  // callback is knowable from the URL alone, and calling setState
  // synchronously in an effect to say so is both a wasted render and the
  // thing `react-hooks/set-state-in-effect` exists to stop.
  const rawId = params.get("installation_id");
  const installState = params.get("state");
  const installationId = Number(rawId);
  const malformed =
    !rawId || !installState || !Number.isInteger(installationId);

  useEffect(() => {
    if (malformed || started.current) return;
    started.current = true;

    connectGitHubInstallation(installationId, installState as string)
      .then((result) => {
        setState("done");
        setMessage(result.account_login);
        router.replace("/account/github");
      })
      .catch((cause) => {
        setState("error");
        setMessage(
          cause instanceof Error ? cause.message : "could not connect GitHub"
        );
      });
  }, [malformed, installationId, installState, router]);

  if (malformed) {
    return (
      <div className="space-y-3">
        <p className="flex items-center gap-2 text-sm text-destructive">
          <Warning size={16} weight="bold" />
          GitHub did not send a usable installation id and state, so there is
          nothing to connect. Start again from the GitHub page.
        </p>
        <Button
          variant="outline"
          onClick={() => router.replace("/account/github")}
        >
          Back to GitHub settings
        </Button>
      </div>
    );
  }

  if (state === "error") {
    return (
      <div className="space-y-3">
        <p className="flex items-center gap-2 text-sm text-destructive">
          <Warning size={16} weight="bold" />
          {message}
        </p>
        <Button
          variant="outline"
          onClick={() => router.replace("/account/github")}
        >
          Back to GitHub settings
        </Button>
      </div>
    );
  }

  if (state === "done") {
    return (
      <p className="flex items-center gap-2 text-sm">
        <CheckCircle size={16} weight="fill" />
        Connected {message}.
      </p>
    );
  }

  return (
    <p className="text-sm text-muted-foreground">
      Connecting your GitHub account…
    </p>
  );
}

export default function GitHubCallbackPage() {
  // `useSearchParams` requires a Suspense boundary, or the whole route opts
  // out of static rendering at build time.
  return (
    <Suspense
      fallback={
        <p className="text-sm text-muted-foreground">Connecting your GitHub account…</p>
      }
    >
      <Callback />
    </Suspense>
  );
}
