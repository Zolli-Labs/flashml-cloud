"use client";

import type { NetworkTotals, Provider } from "@/lib/network/api";
import {
  capacityDonuts,
  isPlaced,
  mapMarkers,
} from "@/lib/network/providers";
import { CapacityDonuts } from "./CapacityDonuts";
import { WorldMap } from "./WorldMap";

/**
 * The page's instrument: how many providers are up, where they are, and how
 * much of the network's capacity is committed.
 *
 * WHY THIS ONE PANEL IS DARK ON A LIGHT CONSOLE. `app/globals.css` puts the
 * product register in warm mineral light and keeps a graphite register beside
 * it for surfaces that are instruments rather than documents — the console
 * rail (`.console-rail`), the landing's workflow canvas (`.workflow-scene`),
 * the marketing route (`.marketing-dark`). A world map is the second kind: a
 * light map has to draw the ocean as a fill and then fight it for every dot,
 * and the two-tone graphite ramp gives an unlit ocean for free. The class
 * used is `.marketing-dark`, which remaps the semantic tokens for this
 * subtree, so everything inside — border, surface, muted-foreground, the
 * donuts' track — resolves to its dark peer with no per-element overrides.
 *
 * **Flagged for the owner:** the right home for that scope is a
 * `.console-instrument` alias beside `.console-rail` in `app/globals.css`,
 * which this session does not own. Same declarations, honest name.
 *
 * THE COUNT AND THE MAP CAN DISAGREE, and the caption says so rather than
 * hiding it. `providers_online` counts every provider; the map can only draw
 * the ones that reported coordinates. A reader who counts eleven dots under a
 * heading that says twelve has found a real fact about the fleet — one host
 * has not set a location — and should be told, not quietly reconciled.
 */
export function NetworkCapacity({
  totals,
  providers,
}: {
  totals: NetworkTotals;
  providers: Provider[];
}) {
  const markers = mapMarkers(providers);
  const donuts = capacityDonuts(totals);
  const unplaced = providers.length - providers.filter(isPlaced).length;

  return (
    <section className="marketing-dark overflow-hidden rounded-lg border border-border bg-background text-foreground">
      <header className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4 px-5 pt-5 pb-4 sm:px-6">
        <div>
          <h2 className="label-caps">Network capacity</h2>
          <p className="mt-1.5 flex items-baseline gap-2">
            <span className="metric-lg text-foreground">
              {totals.providers_online}
            </span>
            <span className="text-sm text-muted-foreground">
              active {totals.providers_online === 1 ? "provider" : "providers"}
              {totals.providers_total > totals.providers_online && (
                <span className="meta"> of {totals.providers_total} known</span>
              )}
            </span>
          </p>
        </div>

        <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
          <Key tone="var(--z-orange)">online</Key>
          <Key tone="#6d706f">offline</Key>
          <Key tone="var(--z-orange)" ring>
            yours
          </Key>
          <li className="flex items-center gap-1.5">
            <svg viewBox="0 0 10 10" className="h-2.5 w-2.5" aria-hidden>
              <path d="M5 0.4 9.6 5 5 9.6 0.4 5Z" fill="#e8e4dc" />
            </svg>
            <span className="meta">coordinator</span>
          </li>
        </ul>
      </header>

      {/* The map keeps its own aspect ratio rather than a pixel height, so it
          never letterboxes: the crop is 1000×388 and the box matches it.

          The fade at the foot of it is the same device `.workflow-scene-canvas`
          uses on the landing: the map's bottom edge is empty ocean, and a hard
          rule between that and the donut row reads as two panels stacked
          rather than one instrument. */}
      <div className="relative">
        <WorldMap markers={markers} className="aspect-[1000/388] w-full" />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 bottom-0 h-24 bg-gradient-to-b from-transparent to-[var(--z-bg)]"
        />
      </div>

      <div className="border-t border-border px-5 pt-7 pb-6 sm:px-6">
        <CapacityDonuts
          donuts={donuts}
          caption={
            unplaced > 0
              ? `Committed against the whole network. ${markers.length} of ${providers.length} providers are on the map — ${unplaced} ${unplaced === 1 ? "has" : "have"} not stated a location.`
              : "Committed against the whole network."
          }
        />
      </div>
    </section>
  );
}

function Key({
  tone,
  ring = false,
  children,
}: {
  tone: string;
  ring?: boolean;
  children: React.ReactNode;
}) {
  return (
    <li className="flex items-center gap-1.5">
      <span
        aria-hidden
        className="h-2 w-2 rounded-full"
        style={{
          background: tone,
          boxShadow: ring ? `0 0 0 2.5px color-mix(in srgb, ${tone} 45%, transparent)` : undefined,
        }}
      />
      <span className="meta">{children}</span>
    </li>
  );
}
