"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowClockwise, Warning } from "@phosphor-icons/react";

import { OfficialChip } from "@/components/network/OfficialChip";
import { ProviderDetailView } from "@/components/network/ProviderDetailView";
import { TrustBadge } from "@/components/network/TrustBadge";
import { PageHeader } from "@/components/shell/PageHeader";
import { PageShell } from "@/components/shell/PageShell";
import { Button } from "@/components/ui/button";
import { NotAuthenticated, NotFound } from "@/lib/cloud-api";
import {
  getNetworkProvider,
  getNetworkProviders,
  type NetworkTotals,
  type ProviderDetail,
} from "@/lib/network/api";

/** One provider, end to end.
 *
 * TWO INDEPENDENT READS, and the second one is optional. The provider route
 * carries everything this page is about; the list route is read only for the
 * network's totals, which the capacity donuts need as a denominator. Either
 * can fail without emptying the other — a provider whose network totals could
 * not be read still has a header, two charts and two cards, and the donut row
 * says it could not be drawn rather than drawing a provider with no capacity.
 * That is the same split `/market/prices/[klass]` documents for the board and
 * the book.
 *
 * A 404 IS NOT AN ERROR WALL. An unknown id gets one sentence and a way back:
 * nothing failed, the URL is wrong. The API answers 404 for "not yours" as
 * well as "does not exist", and this page must not helpfully distinguish
 * them — telling a prober which ids are real is exactly what that 404 is for.
 */
export default function ProviderDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const router = useRouter();
  const id = use(params).id;

  const [state, setState] = useState<
    "loading" | "present" | "unreadable" | "missing"
  >("loading");
  const [provider, setProvider] = useState<ProviderDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const hadData = useRef(false);

  // Its own state word rather than `totals === null`: not-read-yet and
  // could-not-read look identical in a null, and the two reads land at
  // different moments — so a failure derived from emptiness would flash a
  // refusal for every millisecond between them.
  const [totals, setTotals] = useState<NetworkTotals | null>(null);
  const [totalsState, setTotalsState] = useState<
    "loading" | "present" | "unreadable"
  >("loading");

  const load = useCallback(() => {
    const one = getNetworkProvider(id)
      .then((p) => {
        hadData.current = true;
        setProvider(p);
        setError(null);
        setState("present");
      })
      .catch((err) => {
        if (err instanceof NotAuthenticated) {
          router.push(
            `/sign-in?next=/market/providers/${encodeURIComponent(id)}`
          );
          return;
        }
        if (err instanceof NotFound) {
          setState("missing");
          return;
        }
        setError(err instanceof Error ? err.message : "Couldn't load.");
        if (!hadData.current) setState("unreadable");
      });

    const all = getNetworkProviders()
      .then((v) => {
        setTotals(v.totals);
        setTotalsState("present");
      })
      .catch(() => setTotalsState("unreadable"));

    return Promise.all([one, all]);
  }, [id, router]);

  useEffect(() => {
    load();
  }, [load]);

  const retry = useCallback(() => {
    setRefreshing(true);
    load().finally(() => setRefreshing(false));
  }, [load]);

  return (
    <PageShell width="wide">
      <PageHeader
        title={provider?.label ?? id}
        titleTone="identifier"
        back={{ href: "/market/providers", label: "Providers" }}
        meta={provider ? `${provider.leases.total} leases all time` : undefined}
        actions={
          provider ? (
            <div className="flex items-center gap-2">
              <OfficialChip provider={provider} />
              <TrustBadge badge={provider.badge} />
              <Button
                variant="ghost"
                size="icon-sm"
                onClick={retry}
                aria-label="Refresh this provider"
              >
                <ArrowClockwise className={refreshing ? "animate-spin" : ""} />
              </Button>
            </div>
          ) : undefined
        }
      />

      {state === "loading" && (
        <div className="mt-6 space-y-4" aria-busy="true">
          <div className="skeleton h-56 w-full" />
          <div className="skeleton h-44 w-full" />
          <div className="skeleton h-40 w-full" />
        </div>
      )}

      {state === "missing" && (
        <p className="mt-6 text-sm text-muted-foreground">
          No provider with that id is visible to this account.{" "}
          <Link
            href="/market/providers"
            className="text-brand-foreground hover:underline"
          >
            Back to the network
          </Link>
          .
        </p>
      )}

      {state === "unreadable" && (
        <div className="mt-6 rounded-lg border border-destructive/30 bg-surface p-4">
          <p className="flex max-w-prose items-start gap-2 text-sm text-destructive">
            <Warning className="mt-0.5 h-4 w-4 shrink-0" weight="fill" />
            <span>
              Couldn&apos;t read this provider. A failed read is unreadable, not
              empty.
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

      {state === "present" && provider !== null && (
        <div className="mt-6">
          <ProviderDetailView
            provider={provider}
            totals={totals}
            totalsState={totalsState}
            onLocationSaved={(patched) =>
              setProvider((at) =>
                at === null
                  ? at
                  : {
                      ...at,
                      location: {
                        ...at.location,
                        country: patched.country,
                        region: patched.region ?? null,
                        city: patched.city ?? null,
                        // The coordinates are the API's to resolve from the
                        // new place name, and this page has not re-read them
                        // yet. Keeping the OLD pair would leave the dot on
                        // the previous city under the new label, which is
                        // worse than an honest gap until the next read.
                        lat: null,
                        lon: null,
                        source: "you",
                      },
                    }
              )
            }
          />
        </div>
      )}
    </PageShell>
  );
}
