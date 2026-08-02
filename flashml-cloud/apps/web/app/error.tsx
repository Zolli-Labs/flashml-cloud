"use client";

import { useEffect } from "react";
import Link from "next/link";
import { ArrowClockwise, Warning } from "@phosphor-icons/react";

/** Route-level error boundary. Without one, an uncaught render error shows
 * Next's default "Application error: a client-side exception has occurred",
 * which tells the user nothing and tells us nothing either.
 *
 * The digest is shown deliberately. In production the real message is
 * stripped from the client bundle and only the digest reaches the browser,
 * so it is the single token that lets a user's screenshot be matched to a
 * server log line. Hiding it to look tidy makes every report unactionable. */
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("[flashml] unhandled render error", error);
  }, [error]);

  return (
    <div className="mx-auto flex min-h-[60dvh] max-w-lg flex-col items-center justify-center px-4 text-center">
      <Warning className="h-7 w-7 text-destructive" weight="fill" />
      <h1 className="mt-4 text-lg font-semibold">Something broke on this page</h1>
      <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
        The rest of the app is fine. Retrying re-renders this page without
        reloading the whole console.
      </p>

      {error.digest && (
        <p className="mt-3 font-mono text-xs text-muted-foreground">
          error id <span className="text-foreground">{error.digest}</span>
        </p>
      )}

      <div className="mt-6 flex flex-wrap justify-center gap-2">
        <button
          type="button"
          onClick={reset}
          className="interactive inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:brightness-110"
        >
          <ArrowClockwise size={14} weight="bold" />
          Try again
        </button>
        <Link
          href="/overview"
          className="inline-flex items-center rounded-md border border-border px-4 py-2 text-sm hover:bg-white/[0.06]"
        >
          Back to overview
        </Link>
      </div>
    </div>
  );
}
