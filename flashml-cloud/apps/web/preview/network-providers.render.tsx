import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
import { expect, it } from "vitest";

import { NetworkCapacity } from "@/components/network/NetworkCapacity";
import { ProvidersTable } from "@/components/network/ProvidersTable";
import { PageHeader } from "@/components/shell/PageHeader";
import { PageShell } from "@/components/shell/PageShell";
import type { NetworkProviders, Provider } from "@/lib/network/api";
import { mapMarkers, matchesSearch } from "@/lib/network/providers";

/**
 * `/market/providers`, rendered so it can be LOOKED AT without a session.
 *
 *   npx vitest run --config preview/vitest.preview.config.ts
 *
 * No agent working in this repo can sign in to the console, so a page nobody
 * can open is a page nobody has seen. This writes the whole screen — dark
 * capacity panel, world map, donut row, provider table — to
 * `.preview/network-providers.html` with the real compiled stylesheet behind
 * it, and asserts the handful of things about it that a type-check cannot:
 * that the refusals actually render as refusals.
 *
 * Named `*.render.tsx` so `vitest.config.ts`'s `**\/*.test.*` glob cannot
 * collect it and inflate the suite baseline.
 *
 * THE FIXTURE IS INVENTED AND SAYS SO. Every provider below is made up —
 * that is what a preview is for, and `app/globals.css`'s honesty rules
 * forbid fixture-shaped literals in the app itself, not in the one place a
 * shape has to be invented to be drawn at all. It is built to exercise the
 * cases that are easy to get wrong rather than to look tidy: a provider with
 * no location, a provider with three resolved attempts, an offline provider,
 * a machine nobody has observed yet, an AMD box, a CPU-only box, and three
 * owned anchors on three continents — one of which (`prov_anchor_eu`, the
 * same id `provider-detail.render.tsx`'s owned fixture uses) is also marked
 * `official: true`, so the "Official" chip has a row to render next to in
 * this table and the other two anchors show what an owned-but-not-official
 * row looks like beside it.
 */
const webRoot = process.cwd();
const outDir = process.env.PREVIEW_OUT ?? path.join(webRoot, ".preview");

const GIB = 1024 ** 3;

function provider(p: {
  id: string;
  label: string;
  official?: boolean;
  own?: boolean;
  online?: boolean;
  city?: string | null;
  region?: string | null;
  country?: string | null;
  lat?: number | null;
  lon?: number | null;
  source?: string | null;
  uptimePct?: number | null;
  observedHours?: number;
  active: number;
  total: number;
  resolved: number;
  success: number | null;
  cores: number | null;
  memGib: number | null;
  gpus?: [string, number | null][];
  os?: string | null;
  arch?: string | null;
  badge?: Provider["badge"];
}): Provider {
  const observed = p.observedHours ?? 168;
  const pct = p.uptimePct ?? null;
  return {
    id: p.id,
    label: p.label,
    official: p.official ?? false,
    own: p.own ?? false,
    online: p.online ?? true,
    last_seen_at: (p.online ?? true)
      ? new Date(Date.now() - 20_000).toISOString()
      : new Date(Date.now() - 9 * 3600_000).toISOString(),
    location: {
      country: p.country ?? null,
      region: p.region ?? null,
      city: p.city ?? null,
      lat: p.lat ?? null,
      lon: p.lon ?? null,
      source: p.source ?? "host",
    },
    uptime_7d_pct: pct,
    uptime: {
      hours_up_7d: pct === null ? 0 : Math.round((pct / 100) * observed * 10) / 10,
      hours_observed_7d: observed,
    },
    leases: {
      active: p.active,
      total: p.total,
      resolved: p.resolved,
      success_rate: p.success,
    },
    specs: {
      cpu_cores: p.cores,
      memory_bytes: p.memGib === null ? null : p.memGib * GIB,
      // One entry per physical card, the way the API reports them — the
      // chips' `×2` comes from aggregating those, not from a count field.
      gpus: (p.gpus ?? []).map(([name, mb]) => ({
        name,
        memory_total_mb: mb,
      })),
      os: p.os ?? "linux",
      architecture: p.arch ?? "x86_64",
    },
    badge: p.badge ?? "sandboxed",
  };
}

const cards = (name: string, mb: number | null, n: number) =>
  Array.from({ length: n }, () => [name, mb] as [string, number | null]);

const PROVIDERS: Provider[] = [
  provider({
    id: "prov_anchor_us",
    label: "zolli_anchor_gpu_a5000_us",
    own: true,
    // Austin rather than Portland, deliberately: the coordinator itself is in
    // Render's `oregon` region, and a provider on the same pixel hides the
    // hub diamond under a dot. Worth keeping the fixture off it so the
    // preview shows both.
    city: "Austin",
    region: "Texas",
    country: "US",
    lat: 30.27,
    lon: -97.74,
    uptimePct: 99.8,
    active: 6,
    total: 214,
    resolved: 208,
    success: 0.97,
    cores: 32,
    memGib: 128,
    gpus: cards("rtx a5000", 24576, 2),
    badge: "trusted",
  }),
  provider({
    id: "prov_anchor_eu",
    label: "zolli_anchor_gpu_a5000_eu",
    official: true,
    own: true,
    city: "Frankfurt",
    region: "Hessen",
    country: "DE",
    lat: 50.11,
    lon: 8.68,
    uptimePct: 100,
    active: 4,
    total: 141,
    resolved: 138,
    success: 0.94,
    cores: 24,
    memGib: 64,
    gpus: cards("rtx a5000", 24576, 1),
  }),
  provider({
    id: "prov_anchor_se",
    label: "zolli_anchor_gpu_a5000_se",
    own: true,
    city: "Singapore",
    country: "SG",
    lat: 1.35,
    lon: 103.82,
    uptimePct: 97.4,
    active: 3,
    total: 88,
    resolved: 81,
    success: 0.91,
    cores: 16,
    memGib: 64,
    gpus: cards("rtx a5000", 24576, 2),
  }),
  provider({
    id: "prov_ams",
    label: "northwind-a100-ams",
    city: "Amsterdam",
    country: "NL",
    lat: 52.37,
    lon: 4.9,
    uptimePct: 99.9,
    active: 8,
    total: 302,
    resolved: 297,
    success: 0.99,
    cores: 48,
    memGib: 256,
    gpus: cards("a100", 81920, 2),
    badge: "trusted",
  }),
  provider({
    id: "prov_sfo",
    label: "helio-3090-sfo",
    city: "San Francisco",
    region: "California",
    country: "US",
    lat: 37.77,
    lon: -122.42,
    uptimePct: 95.6,
    active: 5,
    total: 96,
    resolved: 91,
    success: 0.88,
    cores: 24,
    memGib: 128,
    gpus: cards("rtx3090", 24576, 4),
  }),
  provider({
    id: "prov_yyz",
    label: "cascade-4090-yyz",
    city: "Toronto",
    region: "Ontario",
    country: "CA",
    lat: 43.65,
    lon: -79.38,
    uptimePct: 99.1,
    active: 2,
    total: 47,
    resolved: 44,
    success: 0.95,
    cores: 16,
    memGib: 64,
    gpus: cards("rtx4090", 24576, 1),
  }),
  provider({
    id: "prov_tyo",
    label: "sakura-3090-tyo",
    city: "Tokyo",
    country: "JP",
    lat: 35.68,
    lon: 139.69,
    uptimePct: 98.9,
    active: 3,
    total: 61,
    resolved: 57,
    success: 0.93,
    cores: 20,
    memGib: 96,
    gpus: cards("rtx3090", 24576, 2),
  }),
  provider({
    id: "prov_par",
    label: "mistral-l40-par",
    city: "Paris",
    country: "FR",
    lat: 48.86,
    lon: 2.35,
    uptimePct: 91.2,
    active: 2,
    total: 39,
    resolved: 35,
    success: 0.77,
    cores: 32,
    memGib: 128,
    gpus: cards("l40s", 49152, 2),
  }),
  provider({
    id: "prov_lon",
    label: "kestrel-4070-lon",
    city: "London",
    country: "GB",
    lat: 51.51,
    lon: -0.13,
    uptimePct: 93.7,
    active: 1,
    total: 26,
    resolved: 22,
    success: 0.82,
    cores: 8,
    memGib: 32,
    gpus: cards("rtx4070", 12288, 1),
  }),
  provider({
    id: "prov_sto",
    label: "tundra-cpu-sto",
    city: "Stockholm",
    country: "SE",
    lat: 59.33,
    lon: 18.07,
    uptimePct: 100,
    active: 0,
    total: 14,
    resolved: 12,
    success: 1,
    cores: 64,
    memGib: 192,
    gpus: [],
    badge: "modules-only",
  }),
  provider({
    id: "prov_syd",
    label: "reef-4080-syd",
    city: "Sydney",
    region: "New South Wales",
    country: "AU",
    lat: -33.87,
    lon: 151.21,
    uptimePct: 99.4,
    active: 2,
    total: 33,
    resolved: 29,
    success: 0.9,
    cores: 12,
    memGib: 32,
    gpus: cards("rtx4080", 16384, 1),
  }),
  provider({
    id: "prov_sin",
    label: "banyan-t4-sin",
    city: "Singapore",
    country: "SG",
    lat: 1.29,
    lon: 103.85,
    uptimePct: 96.3,
    active: 1,
    total: 21,
    resolved: 18,
    success: 0.89,
    cores: 8,
    memGib: 32,
    gpus: cards("tesla t4", 16384, 1),
  }),
  provider({
    id: "prov_bng",
    label: "anvil-mi250-bng",
    city: "Bengaluru",
    region: "Karnataka",
    country: "IN",
    lat: 12.97,
    lon: 77.59,
    uptimePct: 92.8,
    active: 1,
    total: 12,
    resolved: 9,
    success: 0.71,
    cores: 32,
    memGib: 128,
    gpus: cards("mi250", 65536, 2),
    badge: "trusted",
  }),
  // Offline: a real machine on the network that is not answering right now.
  provider({
    id: "prov_sao",
    label: "drift-3060-sao",
    online: false,
    city: "São Paulo",
    country: "BR",
    lat: -23.55,
    lon: -46.63,
    uptimePct: 62.5,
    active: 0,
    total: 11,
    resolved: 9,
    success: 0.55,
    cores: 8,
    memGib: 16,
    gpus: cards("rtx3060", 12288, 1),
  }),
  // Fresh: three resolved attempts, so the success column must REFUSE rather
  // than print 66.7%.
  provider({
    id: "prov_den",
    label: "quartz-4060-den",
    city: "Denver",
    region: "Colorado",
    country: "US",
    lat: 39.74,
    lon: -104.99,
    uptimePct: 100,
    observedHours: 6,
    active: 1,
    total: 4,
    resolved: 3,
    success: 0.667,
    cores: 8,
    memGib: 32,
    gpus: cards("rtx4060", 8192, 1),
  }),
  // No location at all, and nothing observed yet: absent from the map,
  // present in the table, "no data yet" rather than 0%.
  provider({
    id: "prov_nomad",
    label: "nomad-m2-unstated",
    country: null,
    lat: null,
    lon: null,
    source: null,
    uptimePct: null,
    observedHours: 0,
    active: 0,
    total: 2,
    resolved: 1,
    success: null,
    cores: 10,
    memGib: 24,
    gpus: [["apple m2", null]],
    os: "darwin",
    arch: "arm64",
    badge: "modules-only",
  }),
];

const VIEW: NetworkProviders = {
  totals: {
    // Summed from the fixture so the header cannot disagree with the table
    // under it — the same guarantee the real page gets from one read.
    cpu_cores: {
      total: PROVIDERS.reduce((n, p) => n + (p.specs.cpu_cores ?? 0), 0),
      used: 118,
    },
    gpus: {
      total: PROVIDERS.reduce((n, p) => n + p.specs.gpus.length, 0),
      used: 13,
    },
    memory_bytes: {
      total: PROVIDERS.reduce((n, p) => n + (p.specs.memory_bytes ?? 0), 0),
      used: 402 * GIB,
    },
    providers_online: PROVIDERS.filter((p) => p.online).length,
    providers_total: PROVIDERS.length,
  },
  providers: PROVIDERS,
};

function Page() {
  return (
    <div className="bg-[var(--z-app-canvas)] text-ink" style={{ minHeight: "100vh" }}>
      <PageShell width="wide">
        <PageHeader
          title="Providers"
          description="Every machine offering capacity to this network, and what each is carrying right now."
        />
        <div className="mt-6 space-y-8">
          <NetworkCapacity totals={VIEW.totals} providers={VIEW.providers} />
          <ProvidersTable providers={VIEW.providers} />
        </div>
      </PageShell>
    </div>
  );
}

async function compiledCss(): Promise<string> {
  const globalsPath = path.join(webRoot, "app/globals.css");
  const css = readFileSync(globalsPath, "utf8");
  const result = await postcss([tailwindcss({ base: webRoot })]).process(css, {
    from: globalsPath,
  });
  return result.css;
}

/** The two `next/font` variables the app injects on `<html>`. Without them
 * every `.title`, `.meta` and `.metric-value` in the preview falls back to
 * the generic family and the screenshot misreports the typography — the one
 * thing a look-at-it harness exists to check. */
const FONT_VARS = `:root{--font-instrument-sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;--font-geist-mono:ui-monospace,SFMono-Regular,Menlo,monospace}`;

it("writes the providers preview", async () => {
  const css = await compiledCss();
  const body = renderToStaticMarkup(<Page />);
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Providers preview</title><style>${FONT_VARS}${css}</style></head>
<body>${body}</body></html>`;

  mkdirSync(outDir, { recursive: true });
  const out = path.join(outDir, "network-providers.html");
  writeFileSync(out, html, "utf8");
  console.log(`preview written: ${out}`);
}, 60_000);

// ---------------------------------------------------------------------------
// The assertions a type-check cannot make: that the refusals refuse.
// ---------------------------------------------------------------------------

const markup = () => renderToStaticMarkup(<Page />);

it("refuses a success rate over three resolved attempts", () => {
  const out = markup();
  expect(out).toContain("n/a — low evidence");
  // 2 of 3 is 66.7%, which is exactly the plausible-looking number the floor
  // exists to keep off the page.
  expect(out).not.toContain("66.7%");
});

it("says 'no data yet' rather than 0% for an unobserved machine", () => {
  const out = markup();
  expect(out).toContain("no data yet");
  expect(out).toContain("nomad-m2-unstated");
});

it("keeps an unlocated provider in the table and off the map", () => {
  const out = markup();
  // In the table…
  expect(out).toContain("nomad-m2-unstated");
  // …and the map's own count says one provider is missing from it.
  expect(mapMarkers(PROVIDERS)).toHaveLength(PROVIDERS.length - 1);
  expect(out).toContain("not stated a location");
  expect(out).toContain(`${mapMarkers(PROVIDERS).length} of ${PROVIDERS.length} providers are on the map`);
});

it("draws three donuts, not four, while storage is unreported", () => {
  const out = markup();
  expect(out).toContain("CPU");
  expect(out).toContain("GPU");
  expect(out).toContain("Memory");
  expect(out).not.toContain("Storage");
  expect(out).toContain("sm:grid-cols-3");
});

it("marks the one official anchor, and only that one", () => {
  const out = markup();
  expect(out).toContain("Official");
  const official = PROVIDERS.filter((p) => p.official);
  expect(official.map((p) => p.id)).toEqual(["prov_anchor_eu"]);
  expect(matchesSearch(official[0], "official")).toBe(true);
  expect(
    PROVIDERS.filter((p) => !p.official).some((p) =>
      matchesSearch(p, "official")
    )
  ).toBe(false);
});

it("draws one arc per placed provider, all to the coordinator", () => {
  const out = markup();
  const arcs = out.split("<path d=\"M").length - 1;
  // Every marker's arc, plus the coastline and the coordinator diamond.
  expect(arcs).toBeGreaterThanOrEqual(mapMarkers(PROVIDERS).length);
  expect(out).toContain("coordinator");
});
