"use client";

import { CapacityDonuts } from "./CapacityDonuts";
import { LeasesChart } from "./LeasesChart";
import { OwnerPanel } from "./OwnerPanel";
import { GeneralInfoCard, SpecsCard } from "./ProviderFacts";
import { ProviderSummary } from "./ProviderSummary";
import { UptimeStrip } from "./UptimeStrip";
import type { NetworkTotals, ProviderDetail } from "@/lib/network/api";
import { providerShareDonuts } from "@/lib/network/providers";

/**
 * Everything on a provider's page below the heading.
 *
 * WHY THIS IS A COMPONENT AND NOT THE PAGE'S OWN JSX. The page cannot be
 * opened by anyone working in this repo — no agent can sign in — so the only
 * way this screen gets looked at is the preview harness, and a harness that
 * re-assembles the page's layout from the same parts is a second layout that
 * drifts from the first. Whatever the harness renders has to BE the page.
 * So the page owns the reads, the states and the heading; this owns the
 * arrangement; and `preview/provider-detail.render.tsx` renders exactly what
 * a signed-in user would see.
 *
 * It takes data, not promises: no fetching, no router, no effects.
 */
export function ProviderDetailView({
  provider,
  totals,
  totalsState,
  onLocationSaved,
}: {
  provider: ProviderDetail;
  /** The network's own capacity, read separately — the donuts' denominator.
   * Null while that read is in flight or after it failed; `totalsState` is
   * what tells those two apart. */
  totals: NetworkTotals | null;
  totalsState: "loading" | "present" | "unreadable";
  onLocationSaved: (patched: {
    country: string;
    region?: string;
    city?: string;
  }) => void;
}) {
  const donuts = providerShareDonuts(provider.specs, totals);

  return (
    <div className="space-y-8">
      <ProviderSummary provider={provider} />

      <section>
        <h2 className="label-caps">Capacity</h2>
        {donuts === null ? (
          <p className="mt-2 max-w-prose text-sm text-muted-foreground">
            {totalsState === "loading"
              ? "Reading the network's totals…"
              : "Couldn't read the network's totals, so there is nothing to draw this provider against. Its own specs are in the card below."}
          </p>
        ) : (
          <div className="mt-3">
            <CapacityDonuts
              donuts={donuts}
              size={104}
              pctLabel="of the network"
              caption="This provider against the whole network. Per-provider commitment is not reported, so the filled arc is what this machine contributes — not what it is running right now."
            />
          </div>
        )}
      </section>

      <div className="grid items-start gap-8 lg:grid-cols-2">
        <section>
          <h2 className="label-caps">Active leases · 30 days</h2>
          <LeasesChart days={provider.series.leases_daily_30d} />
        </section>

        <section>
          <h2 className="label-caps">Up time (24h)</h2>
          <UptimeStrip hours={provider.series.uptime_24h} />

          <dl className="mt-7 grid grid-cols-4 gap-x-4">
            <Attempt label="Accepted" value={provider.history.attempts.accepted} />
            <Attempt label="Failed" value={provider.history.attempts.failed} />
            <Attempt label="Expired" value={provider.history.attempts.expired} />
            <Attempt
              label="Unresolved"
              value={provider.history.attempts.unresolved}
            />
          </dl>
          <p className="mt-2.5 max-w-prose text-[11px] leading-relaxed text-muted-foreground">
            Attempts all time. Accepted work totals{" "}
            {hoursText(provider.history.accepted_duration_s_total)} of machine
            time.
          </p>
        </section>
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-2">
        <GeneralInfoCard provider={provider} />
        <SpecsCard provider={provider} />
      </div>

      {provider.own && (
        <OwnerPanel provider={provider} onLocationSaved={onLocationSaved} />
      )}
    </div>
  );
}

function Attempt({ label, value }: { label: string; value: number }) {
  return (
    <div className="min-w-0">
      <dt className="label-caps">{label}</dt>
      <dd className="metric-value mt-0.5 text-lg">{value}</dd>
    </div>
  );
}

/** Seconds as the coarsest unit that still says something. A machine-time
 * total of four seconds is `4s`, not `0.0h`. */
export function hoursText(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0s";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  const minutes = seconds / 60;
  if (minutes < 90) return `${Math.round(minutes)}m`;
  const hours = minutes / 60;
  return hours < 100
    ? `${Math.round(hours * 10) / 10}h`
    : `${Math.round(hours)}h`;
}
