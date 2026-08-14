"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowClockwise, Warning } from "@phosphor-icons/react";

import { NetworkCapacity } from "@/components/network/NetworkCapacity";
import { ProvidersTable } from "@/components/network/ProvidersTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { PageShell } from "@/components/shell/PageShell";
import { Button } from "@/components/ui/button";
import { NotAuthenticated } from "@/lib/cloud-api";
import {
  getNetworkProviders,
  type NetworkProviders,
} from "@/lib/network/api";

/** Every machine on the network: where it is, what it is, and what it is
 * carrying right now.
 *
 * ONE READ, ON PURPOSE. The header's totals and the table's rows come from
 * the same response, so the count above the map and the count of rows under
 * it cannot disagree — which is exactly what two routes polling at different
 * moments would eventually produce.
 *
 * A FAILED REFRESH IS NOT AN UNREADABLE PAGE — the same distinction
 * `/market/prices` documents, and `hadData` is a ref for the same reason:
 * the decision is made inside a promise callback that closed over whatever
 * state existed when the request left, and "did we ever have an answer" is
 * precisely the question a stale closure gets wrong. First read fails: the
 * unreadable panel. Any later read fails: the page stays and says it is
 * stale. */
export default function ProvidersPage() {
  const router = useRouter();
  const [state, setState] = useState<"loading" | "present" | "unreadable">(
    "loading"
  );
  const [view, setView] = useState<NetworkProviders | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const hadData = useRef(false);

  const load = useCallback(() => {
    return getNetworkProviders()
      .then((v) => {
        hadData.current = true;
        setView(v);
        setError(null);
        setStale(false);
        setState("present");
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push("/sign-in?next=/market/providers");
          return;
        }
        setError(err instanceof Error ? err.message : "Couldn't load.");
        if (hadData.current) setStale(true);
        else setState("unreadable");
      });
  }, [router]);

  useEffect(() => {
    load();
  }, [load]);

  const retry = useCallback(() => {
    if (!hadData.current) {
      setState("loading");
      setError(null);
    }
    setRefreshing(true);
    load().finally(() => setRefreshing(false));
  }, [load]);

  return (
    <PageShell width="wide">
      <PageHeader
        title="Providers"
        description="Every machine offering capacity to this network, and what each is carrying right now."
        actions={
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={retry}
            aria-label="Refresh the network"
          >
            <ArrowClockwise className={refreshing ? "animate-spin" : ""} />
          </Button>
        }
      />

      {state === "loading" && (
        <div className="mt-6 space-y-4" aria-busy="true">
          <div className="skeleton h-[520px] w-full" />
          <div className="skeleton h-64 w-full" />
        </div>
      )}

      {state === "unreadable" && (
        <div className="mt-6 rounded-lg border border-destructive/30 bg-surface p-4">
          <p className="flex max-w-prose items-start gap-2 text-sm text-destructive">
            <Warning className="mt-0.5 h-4 w-4 shrink-0" weight="fill" />
            <span>
              Couldn&apos;t read the network. A failed read is unreadable, not
              empty — the providers are still there.
            </span>
          </p>
          {error && (
            <p className="mt-1.5 break-all font-mono text-[11px] text-muted-foreground">
              {error}
            </p>
          )}
          <button
            type="button"
            onClick={retry}
            className="mt-3 rounded-md border border-border bg-surface px-2.5 py-1 text-xs transition-colors hover:bg-surface-2"
          >
            Try again
          </button>
        </div>
      )}

      {state === "present" && view !== null && (
        <div className="mt-6 space-y-8">
          {stale && (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-1.5 text-xs text-warning-foreground">
              <span>Couldn&apos;t refresh — showing the last answer.</span>
              <button
                type="button"
                onClick={retry}
                className="inline-flex items-center gap-1 rounded border border-warning/40 px-1.5 py-0.5 text-[11px] transition-colors hover:bg-warning/10"
              >
                <ArrowClockwise className="h-3 w-3" />
                Retry
              </button>
            </div>
          )}

          <NetworkCapacity totals={view.totals} providers={view.providers} />
          <ProvidersTable providers={view.providers} />
        </div>
      )}
    </PageShell>
  );
}
