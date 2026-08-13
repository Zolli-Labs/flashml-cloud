"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowClockwise } from "@phosphor-icons/react";

import { PricesPanel } from "@/components/market/PricesPanel";
import { PageHeader } from "@/components/shell/PageHeader";
import { PageShell } from "@/components/shell/PageShell";
import { Button } from "@/components/ui/button";
import { NotAuthenticated, getPrices, type PricesView } from "@/lib/cloud-api";

/** The compute board. Live numbers come from `GET /v1alpha1/prices` and
 * nowhere else; the reference rows beside them come from the generated seed
 * and carry a badge that says so on every row they appear in.
 *
 * A FAILED REFRESH IS NOT AN UNREADABLE PAGE. `hadData` is a ref rather
 * than a look at `view`, because the decision is made inside a promise
 * callback that closed over whatever `view` was when the request left — and
 * "did we ever have an answer" is exactly the question a stale closure gets
 * wrong. First read fails: the unreadable panel, there is nothing behind
 * it. Any later read fails: the board stays and says it is stale. Not
 * signed in goes to /sign-in either way. */
export default function PricesPage() {
  const router = useRouter();
  const [state, setState] = useState<"loading" | "present" | "unreadable">(
    "loading"
  );
  const [view, setView] = useState<PricesView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const hadData = useRef(false);

  // No synchronous setState in here — the effect below calls it on mount,
  // and a setState in an effect body is a cascading render (and a lint
  // error). The spinner flag belongs to the button that starts a refresh,
  // not to the read itself.
  const load = useCallback(() => {
    return getPrices()
      .then((v) => {
        hadData.current = true;
        setView(v);
        setError(null);
        setStale(false);
        setState("present");
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push("/sign-in?next=/market/prices");
          return;
        }
        setError(err instanceof Error ? err.message : "Couldn't load.");
        if (hadData.current) {
          setStale(true);
        } else {
          setState("unreadable");
        }
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
        title="Prices"
        description="1 ZC = $1 on this surface."
        actions={
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={retry}
            aria-label="Refresh the board"
          >
            <ArrowClockwise className={refreshing ? "animate-spin" : ""} />
          </Button>
        }
      />

      <div className="mt-6">
        <PricesPanel
          state={state}
          view={view}
          onRetry={retry}
          error={error}
          stale={stale}
        />
      </div>
    </PageShell>
  );
}
