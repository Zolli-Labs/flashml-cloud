import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
import { expect, it } from "vitest";

import { ProviderDetailView } from "@/components/network/ProviderDetailView";
import { TrustBadge } from "@/components/network/TrustBadge";
import { PageHeader } from "@/components/shell/PageHeader";
import { PageShell } from "@/components/shell/PageShell";
import type {
  LeaseDay,
  NetworkTotals,
  ProviderDetail,
  UptimeHour,
} from "@/lib/network/api";

/**
 * `/market/providers/[id]`, rendered so it can be LOOKED AT without a
 * session.
 *
 *   npx vitest run --config preview/vitest.preview.config.ts
 *
 * TWO PROVIDERS, because the page has two shapes and only one of them is the
 * happy one. The first is an owned anchor with a full history — the mini-map,
 * the donut row, thirty days of leases, a day of hours, and the owner-only
 * block with its node id, its credits and the location form. The second is
 * somebody else's machine that has never stated where it is and has three
 * resolved attempts to its name: no map, no owner block, "no data yet" where
 * a percentage would go, and a success column that refuses.
 *
 * It renders `ProviderDetailView` — the same component the page renders —
 * rather than re-assembling the layout here. A harness that builds its own
 * version of the screen is a second screen that drifts from the first, and
 * since nobody in this repo can sign in to compare them, the drift would be
 * invisible.
 *
 * The fixtures are invented. That is what a preview is for, and it is the one
 * place a shape has to be made up to be drawn at all.
 */
const webRoot = process.cwd();
const outDir = process.env.PREVIEW_OUT ?? path.join(webRoot, ".preview");

const GIB = 1024 ** 3;

/** Thirty days ending today. Deliberately not smooth: a weekly rhythm, one
 * quiet stretch, and two days where `accepted` falls short of `resolved` —
 * which is the gap between attempted and accepted work that the repo's fourth
 * hard rule is about, and the only thing the second line on the chart is
 * there to show. */
function leaseDays(): LeaseDay[] {
  const resolved = [
    4, 6, 5, 9, 11, 7, 3, 5, 8, 12, 14, 10, 6, 4, 7, 13, 16, 15, 11, 8, 5, 9,
    12, 18, 21, 17, 13, 9, 14, 19,
  ];
  const shortfall = new Map([
    [10, 3],
    [17, 4],
    [24, 2],
  ]);
  const start = Date.UTC(2026, 6, 15);
  return resolved.map((count, i) => ({
    date: new Date(start + i * 86_400_000).toISOString().slice(0, 10),
    resolved: count,
    accepted: count - (shortfall.get(i) ?? 0),
  }));
}

/** Twenty-four hours, with a two-hour outage in the middle of the night.
 * `hour_ts` is a real timestamp because the strip's tooltips read it. */
function uptimeHours(down: number[]): UptimeHour[] {
  const start = Date.UTC(2026, 7, 12, 12);
  return Array.from({ length: 24 }, (_, i) => ({
    hour_ts: new Date(start + i * 3600_000).toISOString(),
    up: !down.includes(i),
  }));
}

const OWNED: ProviderDetail = {
  id: "prov_anchor_eu",
  label: "zolli_anchor_gpu_a5000_eu",
  own: true,
  online: true,
  last_seen_at: new Date(Date.now() - 14_000).toISOString(),
  location: {
    country: "DE",
    region: "Hessen",
    city: "Frankfurt",
    lat: 50.11,
    lon: 8.68,
    source: "host",
  },
  uptime_7d_pct: 99.4,
  uptime: { hours_up_7d: 167, hours_observed_7d: 168 },
  leases: { active: 4, total: 141, resolved: 138, success_rate: 0.94 },
  specs: {
    cpu_cores: 24,
    memory_bytes: 64 * GIB,
    gpus: [
      { name: "rtx a5000", memory_total_mb: 24576 },
      { name: "rtx a5000", memory_total_mb: 24576 },
    ],
    os: "Ubuntu 24.04",
    architecture: "x86_64",
  },
  badge: "trusted",
  series: {
    leases_daily_30d: leaseDays(),
    uptime_24h: uptimeHours([9, 10]),
  },
  history: {
    attempts: { accepted: 130, failed: 6, expired: 2, unresolved: 3 },
    accepted_duration_s_total: 486_240,
  },
  owner: {
    name: "workstation-frankfurt",
    node_id: "fn-98ca4710c2b1",
    credits_earned: 41_820,
  },
};

/** Somebody else's, brand new, nowhere in particular. Every refusal on the
 * page at once. */
const STRANGER: ProviderDetail = {
  id: "prov_nomad",
  label: "nomad-m2-unstated",
  own: false,
  online: true,
  last_seen_at: new Date(Date.now() - 41_000).toISOString(),
  location: {
    country: null,
    region: null,
    city: null,
    lat: null,
    lon: null,
    source: null,
  },
  uptime_7d_pct: null,
  uptime: { hours_up_7d: 0, hours_observed_7d: 0 },
  leases: { active: 0, total: 2, resolved: 1, success_rate: null },
  specs: {
    cpu_cores: 10,
    memory_bytes: 24 * GIB,
    // A card whose memory the driver never reported: the chip must render
    // `apple m2` with no VRAM suffix rather than `0Gi`.
    gpus: [{ name: "apple m2", memory_total_mb: null }],
    os: "macOS 15.5",
    architecture: "arm64",
  },
  badge: "modules-only",
  series: {
    leases_daily_30d: [],
    uptime_24h: uptimeHours([]).slice(0, 5),
  },
  history: {
    attempts: { accepted: 1, failed: 0, expired: 0, unresolved: 1 },
    accepted_duration_s_total: 742,
  },
};

const TOTALS: NetworkTotals = {
  cpu_cores: { total: 362, used: 118 },
  gpus: { total: 24, used: 13 },
  memory_bytes: { total: 1416 * GIB, used: 402 * GIB },
  providers_online: 15,
  providers_total: 16,
};

function Screen({
  provider,
  totals,
  totalsState = "present",
}: {
  provider: ProviderDetail;
  totals: NetworkTotals | null;
  totalsState?: "loading" | "present" | "unreadable";
}) {
  return (
    <PageShell width="wide">
      <PageHeader
        title={provider.label}
        titleTone="identifier"
        back={{ href: "/market/providers", label: "Providers" }}
        meta={`${provider.leases.total} leases all time`}
        actions={<TrustBadge badge={provider.badge} />}
      />
      <div className="mt-6">
        <ProviderDetailView
          provider={provider}
          totals={totals}
          totalsState={totalsState}
          onLocationSaved={() => {}}
        />
      </div>
    </PageShell>
  );
}

function Page() {
  return (
    <div
      className="bg-[var(--z-app-canvas)] text-ink"
      style={{ minHeight: "100vh" }}
    >
      <Screen provider={OWNED} totals={TOTALS} />
      <hr className="mx-auto my-4 max-w-6xl border-t border-dashed border-border" />
      <Screen provider={STRANGER} totals={TOTALS} />
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
 * every `.title`, `.meta` and `.metric-value` falls back to the generic
 * family and the screenshot misreports the typography — the one thing a
 * look-at-it harness exists to check. */
const FONT_VARS = `:root{--font-instrument-sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;--font-geist-mono:ui-monospace,SFMono-Regular,Menlo,monospace}`;

it("writes the provider detail preview", async () => {
  const css = await compiledCss();
  const body = renderToStaticMarkup(<Page />);
  const html = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Provider detail preview</title><style>${FONT_VARS}${css}</style></head>
<body>${body}</body></html>`;

  mkdirSync(outDir, { recursive: true });
  const out = path.join(outDir, "provider-detail.html");
  writeFileSync(out, html, "utf8");
  console.log(`preview written: ${out}`);
}, 60_000);

// ---------------------------------------------------------------------------
// The assertions a type-check cannot make.
// ---------------------------------------------------------------------------

const owned = () =>
  renderToStaticMarkup(<Screen provider={OWNED} totals={TOTALS} />);
const stranger = () =>
  renderToStaticMarkup(<Screen provider={STRANGER} totals={TOTALS} />);

it("shows the owner their node id, their credits and the form", () => {
  const out = owned();
  expect(out).toContain("fn-98ca4710c2b1");
  expect(out).toContain("workstation-frankfurt");
  // 41,820 millicredits is 41.82 ZC — formatted by lib/market-credits.ts,
  // never divided by 1000 here.
  expect(out).toContain("41.82 ZC");
  expect(out).toContain("Set location");
});

it("shows a visitor none of it", () => {
  const out = stranger();
  expect(out).not.toContain("Node id");
  expect(out).not.toContain("Set location");
  expect(out).not.toContain("Credits earned");
});

it("draws 24 uptime bars for 24 observed hours, and 5 for 5", () => {
  // The strip is exactly as wide as the evidence — it never pads to a full
  // day with hours nobody watched.
  expect(OWNED.series.uptime_24h).toHaveLength(24);
  expect(owned()).toContain("22 of 24 observed");
  expect(stranger()).toContain("5 of 5 observed");
});

it("refuses the percentage and the chart when there is nothing behind them", () => {
  const out = stranger();
  expect(out).toContain("no data yet");
  expect(out).toContain("n/a — low evidence");
  expect(out).toContain("No lease activity recorded for this provider yet.");
  expect(out).toContain("no coordinates reported");
});

it("renders a GPU with no reported memory as a bare model chip", () => {
  const out = stranger();
  expect(out).toContain("apple m2");
  expect(out).not.toContain("0Gi");
});

it("keeps the network's totals as the donut denominator", () => {
  const out = owned();
  expect(out).toContain("24 GPU");
  expect(out).toContain("362 vCPU");
});

it("says the totals could not be read rather than drawing an empty ring", () => {
  const out = renderToStaticMarkup(
    <Screen provider={OWNED} totals={null} totalsState="unreadable" />
  );
  expect(out).toContain("Couldn&#x27;t read the network&#x27;s totals");
  expect(out).not.toContain("committed");
});
