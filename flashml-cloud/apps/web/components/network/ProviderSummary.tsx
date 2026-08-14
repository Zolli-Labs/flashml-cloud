"use client";

import { relativeTime } from "@/lib/machine-status";
import type { ProviderDetail } from "@/lib/network/api";
import {
  MAP_VIEW,
  TONE_CLASS_DARK,
  isPlaced,
  locationText,
  mapMarkers,
  successStat,
  uptimeStat,
  type Stat,
} from "@/lib/network/providers";
import { WorldMap } from "./WorldMap";

/**
 * The top of a provider's page: who it is, how it has behaved, and where in
 * the world it sits.
 *
 * THE MINI-MAP IS THE SAME MAP, CROPPED. Not a second component and not a
 * different projection — `WorldMap` takes a `view`, and this passes a box
 * around the provider. One consequence worth having: a dot on this page and
 * the dot on the network page are in the same place on the same coastline, so
 * moving between them reads as zooming rather than as two drawings that
 * happen to agree.
 *
 * NO COORDINATES, NO MAP — and a sentence instead. An unlocated provider gets
 * neither a world map with no dot on it (which reads as "somewhere, we are
 * not saying") nor a dot at 0°,0° in the Atlantic. If the reader owns the
 * machine, the sentence points at the form that fixes it.
 */
export function ProviderSummary({ provider }: { provider: ProviderDetail }) {
  const up = uptimeStat(provider);
  const success = successStat(provider.leases);
  const place = locationText(provider.location);
  const placed = isPlaced(provider);
  const markers = mapMarkers([provider]);

  return (
    <section className="marketing-dark overflow-hidden rounded-lg border border-border bg-background text-foreground">
      <div className="grid gap-x-8 gap-y-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
        <div className="px-5 pt-5 pb-1 sm:px-6">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className="status-dot"
              data-state={provider.online ? "live" : undefined}
              aria-hidden
              style={{
                background: provider.online
                  ? "var(--node-green)"
                  : "var(--muted-foreground)",
              }}
            />
            <span className="text-sm">
              {provider.online ? "Online" : "Offline"}
            </span>
            <span className="meta">
              last seen {relativeTime(provider.last_seen_at)}
            </span>
            {provider.own && (
              <span className="rounded border border-primary/40 bg-primary/10 px-1.5 py-px text-[11px] text-brand-foreground">
                your machine
              </span>
            )}
          </div>

          <dl className="mt-5 grid grid-cols-2 gap-x-6 gap-y-5 sm:grid-cols-4 lg:grid-cols-2 xl:grid-cols-4">
            <Stat label="Active leases">
              <span className="metric-lg">{provider.leases.active}</span>
            </Stat>
            {/* A REFUSAL IS A SENTENCE, so it is set at sentence size and in
                the sans face. "no data yet" wrapped in `.metric-lg` inherits
                36px and −0.04em tracking, and rendered as a crushed headline
                about an absence — which is the loudest possible way to say
                that nothing is known. */}
            <Stat label="Uptime (7d)">
              <Figure stat={up} />
            </Stat>
            <Stat label="Success">
              <Figure stat={success} />
            </Stat>
            <Stat label="Leases all time">
              <span className="metric-lg">{provider.leases.total}</span>
            </Stat>
          </dl>

          <p className="mt-5 text-sm text-muted-foreground">
            {place ? (
              <>
                {place}
                {provider.location.source && (
                  <span className="meta"> · location from {provider.location.source}</span>
                )}
              </>
            ) : provider.own ? (
              "This machine has not stated a location — set one below and it appears on the network map."
            ) : (
              "This machine has not stated a location, so it is absent from the network map. It is still on the network."
            )}
          </p>
        </div>

        {/* `lg:` and not `md:`: the crop is 2:1 and anything narrower than
            about 20rem turns the mini-map into a letterbox. */}
        <div className="min-w-0 px-5 pb-5 sm:px-6 lg:px-0 lg:pr-6 lg:pb-6">
          {placed ? (
            <WorldMap
              markers={markers}
              view={cropAround(markers[0].x, markers[0].y)}
              arcs={false}
              focusId={provider.id}
              emphasiseFocus
              className="aspect-[2/1] w-full overflow-hidden rounded-md border border-border"
            />
          ) : (
            <div className="flex aspect-[2/1] w-full items-center justify-center rounded-md border border-dashed border-border px-4">
              <p className="meta text-center">no coordinates reported</p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

/** A measured figure, or the sentence that stands in for one.
 *
 * `TONE_CLASS_DARK` and not `TONE_CLASS`: this card is a graphite instrument
 * panel, and the light theme's warning colour is a dark olive that all but
 * disappears on it. See the note on that map. */
function Figure({ stat }: { stat: Stat }) {
  if (stat.tone === "unknown") {
    return (
      <span
        className={`text-base ${TONE_CLASS_DARK.unknown}`}
        title={stat.detail}
      >
        {stat.text}
      </span>
    );
  }
  return (
    <span className={`metric-lg ${TONE_CLASS_DARK[stat.tone]}`} title={stat.detail}>
      {stat.text}
    </span>
  );
}

function Stat({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <dt className="label-caps">{label}</dt>
      <dd className="mt-1">{children}</dd>
    </div>
  );
}

/**
 * A 2:1 window on the world, centred on one provider and pushed back inside
 * the map rather than allowed to run off it.
 *
 * The clamp is what stops a provider in Auckland or Reykjavík from being
 * drawn against half a frame of empty box: the window slides until it fits,
 * so the dot moves off-centre instead of the map falling off the edge.
 */
function cropAround(x: number, y: number) {
  // 220 units is about 79° of longitude — a continent and its neighbours.
  // Wider and the card is mostly ocean; much tighter and a coastline stops
  // being recognisable, which is the only thing that makes a dot mean a
  // place.
  const width = 220;
  const height = width / 2;
  const left = Math.min(
    MAP_VIEW.x + MAP_VIEW.width - width,
    Math.max(MAP_VIEW.x, x - width / 2)
  );
  const top = Math.min(
    MAP_VIEW.y + MAP_VIEW.height - height,
    Math.max(MAP_VIEW.y, y - height / 2)
  );
  return { x: left, y: top, width, height };
}
