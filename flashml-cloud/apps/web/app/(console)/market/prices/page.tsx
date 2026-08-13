"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowClockwise } from "@phosphor-icons/react";

import { PricesPanel } from "@/components/market/PricesPanel";
import { PageHeader } from "@/components/shell/PageHeader";
import { PageShell } from "@/components/shell/PageShell";
import { Button } from "@/components/ui/button";
import {
  NotAuthenticated,
  getMarketHint,
  getPrices,
  listMachines,
  type PricesView,
} from "@/lib/cloud-api";
import type { HostMachine } from "@/lib/market/board";

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
 * signed in goes to /sign-in either way.
 *
 * TWO INDEPENDENT READS, same reasoning the class page documents. The
 * board is one route; the host's own machines are a list route plus one
 * hint call each. Either can fail without emptying the other, so they keep
 * separate state words. */
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
  const [machines, setMachines] = useState<HostMachine[]>([]);
  const [machinesState, setMachinesState] = useState<
    "loading" | "present" | "unreadable"
  >("loading");

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

  /** The host's own machines, each resolved to the capability class the
   * market would file it under.
   *
   * ONE HINT CALL PER MACHINE, in parallel: the API has no bulk hint
   * route, and an account has a handful of machines rather than hundreds.
   * A machine whose hint fails — or that the ladder cannot classify at all
   * (`capability_class` null, which is the ladder's own refusal and not an
   * error) — is skipped SILENTLY. It has no class, so there is no card it
   * belongs on, and an error line per unclassifiable laptop would bury the
   * section it sits above.
   *
   * Revoked machines are excluded: they cannot be listed, so their class
   * is not a market a host is in. */
  const loadMachines = useCallback(() => {
    return listMachines()
      .then((found) =>
        Promise.all(
          found
            .filter((machine) => machine.status !== "revoked")
            .map((machine) =>
              getMarketHint(machine.id)
                .then((hint): HostMachine | null =>
                  hint.capability_class === null
                    ? null
                    : {
                        id: machine.id,
                        label: machine.name || machine.node_id,
                        klass: hint.capability_class,
                      }
                )
                .catch(() => null)
            )
        )
      )
      .then((resolved) => {
        setMachines(resolved.filter((m): m is HostMachine => m !== null));
        setMachinesState("present");
      })
      .catch(() => setMachinesState("unreadable"));
  }, []);

  useEffect(() => {
    load();
    loadMachines();
  }, [load, loadMachines]);

  const retry = useCallback(() => {
    if (!hadData.current) {
      setState("loading");
      setError(null);
    }
    setRefreshing(true);
    // Both reads, because the refresh button is on the page and not on one
    // of its panels. The spinner stops when the slower of the two lands,
    // which is the honest answer to "is it still refreshing".
    Promise.all([load(), loadMachines()]).finally(() => setRefreshing(false));
  }, [load, loadMachines]);

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
          machines={machines}
          machinesState={machinesState}
          onRetry={retry}
          error={error}
          stale={stale}
        />
      </div>
    </PageShell>
  );
}
