import { describe, expect, it } from "vitest";

import type { NetworkTotals, Provider } from "./api";
import {
  COORDINATOR_HUB,
  MIN_RESOLVED_FOR_RATE,
  approxUtcOffset,
  badgeLabel,
  bytesText,
  capacityDonuts,
  filterProviders,
  gpuChips,
  gpuVendor,
  isPlaced,
  locationShort,
  locationText,
  mapMarkers,
  matchesSearch,
  maxCpuCores,
  officialChip,
  project,
  providerShareDonuts,
  sortProviders,
  successStat,
  tableSummary,
  uptimeStat,
} from "./providers";

const GIB = 1024 ** 3;

function provider(over: Partial<Provider> = {}): Provider {
  return {
    id: "p1",
    label: "box-one",
    own: false,
    online: true,
    last_seen_at: "2026-08-13T10:00:00Z",
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
    leases: { active: 2, total: 40, resolved: 38, success_rate: 0.94 },
    specs: {
      cpu_cores: 16,
      memory_bytes: 64 * GIB,
      gpus: [{ name: "rtx3090", memory_total_mb: 24576 }],
      os: "linux",
      architecture: "x86_64",
    },
    badge: "sandboxed",
    ...over,
  };
}

describe("uptime — an unobserved machine is not a machine that was down", () => {
  it("says so in words when nothing was observed", () => {
    const stat = uptimeStat({
      uptime_7d_pct: 0,
      uptime: { hours_up_7d: 0, hours_observed_7d: 0 },
    });
    expect(stat.text).toBe("no data yet");
    expect(stat.tone).toBe("unknown");
  });

  it("reads the window BEFORE the percentage", () => {
    // A server that sends `0` alongside an empty window must not render as a
    // machine that was down all week. This is the whole reason the `uptime`
    // object travels with the percentage.
    const unobserved = uptimeStat({
      uptime_7d_pct: 0,
      uptime: { hours_up_7d: 0, hours_observed_7d: 0 },
    });
    const genuinelyDown = uptimeStat({
      uptime_7d_pct: 0,
      uptime: { hours_up_7d: 0, hours_observed_7d: 168 },
    });
    expect(unobserved.text).toBe("no data yet");
    expect(genuinelyDown.text).toBe("0%");
    expect(genuinelyDown.tone).toBe("bad");
  });

  it("refuses a null percentage too", () => {
    expect(uptimeStat({ uptime_7d_pct: null }).text).toBe("no data yet");
  });

  it("takes the percentage as 0–100 and never scales it again", () => {
    expect(uptimeStat({ uptime_7d_pct: 91.24 }).text).toBe("91.2%");
    expect(uptimeStat({ uptime_7d_pct: 100 }).text).toBe("100%");
  });

  it("grades the bands rather than painting everything green", () => {
    expect(uptimeStat({ uptime_7d_pct: 99.4 }).tone).toBe("good");
    expect(uptimeStat({ uptime_7d_pct: 93 }).tone).toBe("warn");
    expect(uptimeStat({ uptime_7d_pct: 62.5 }).tone).toBe("bad");
  });
});

describe("success rate — five resolved attempts or nothing", () => {
  it("refuses below the floor and says why", () => {
    const stat = successStat({ resolved: 3, success_rate: 0.667 });
    expect(stat.text).toBe("n/a — low evidence");
    expect(stat.tone).toBe("unknown");
    expect(stat.detail).toContain("3 resolved attempts");
  });

  it("states the floor rather than hiding it", () => {
    expect(MIN_RESOLVED_FOR_RATE).toBe(5);
    expect(successStat({ resolved: 4, success_rate: 1 }).text).toBe(
      "n/a — low evidence"
    );
    expect(successStat({ resolved: 5, success_rate: 1 }).text).toBe("100%");
  });

  it("refuses a null rate at any evidence level", () => {
    expect(successStat({ resolved: 400, success_rate: null }).text).toBe(
      "n/a — low evidence"
    );
  });

  it("reads a fraction as a fraction and a percentage as a percentage", () => {
    expect(successStat({ resolved: 40, success_rate: 0.94 }).text).toBe("94%");
    // Above 1 it cannot be a fraction.
    expect(successStat({ resolved: 40, success_rate: 94 }).text).toBe("94%");
  });
});

describe("location", () => {
  it("joins what exists and returns null for nowhere", () => {
    expect(
      locationText({
        country: "DE",
        region: "Hessen",
        city: "Frankfurt",
        lat: null,
        lon: null,
        source: null,
      })
    ).toBe("Frankfurt, Hessen, DE");
    expect(
      locationText({
        country: null,
        region: null,
        city: null,
        lat: 1,
        lon: 2,
        source: "host",
      })
    ).toBeNull();
  });

  it("does not say a city-state's name twice", () => {
    expect(
      locationText({
        country: "SG",
        region: "Singapore",
        city: "Singapore",
        lat: null,
        lon: null,
        source: null,
      })
    ).toBe("Singapore, SG");
  });

  it("never falls back to coordinates as a place name", () => {
    const loc = {
      country: null,
      region: null,
      city: null,
      lat: 47.4,
      lon: 8.5,
      source: "geoip",
    };
    expect(locationText(loc)).toBeNull();
    expect(locationShort(loc)).toBeNull();
  });

  it("keeps an unlocated provider off the map and nowhere else", () => {
    const nowhere = provider({
      location: {
        country: null,
        region: null,
        city: null,
        lat: null,
        lon: null,
        source: null,
      },
    });
    expect(isPlaced(nowhere)).toBe(false);
    expect(mapMarkers([nowhere, provider()])).toHaveLength(1);
    // …and it survives every filter that is not about location.
    expect(
      filterProviders([nowhere], {
        search: "",
        activeOnly: true,
        gpuOnly: true,
        mineOnly: false,
      })
    ).toHaveLength(1);
  });
});

describe("the map's projection", () => {
  it("puts 0,0 in the middle and the poles at the edges", () => {
    expect(project(0, 0)).toEqual({ x: 500, y: 250 });
    expect(project(90, -180)).toEqual({ x: 0, y: 0 });
    expect(project(-90, 180)).toEqual({ x: 1000, y: 500 });
  });

  it("puts the coordinator where render.yaml says it runs", () => {
    // Oregon: west of Greenwich, north of the equator.
    const hub = project(COORDINATOR_HUB.lat, COORDINATOR_HUB.lon);
    expect(hub.x).toBeLessThan(500);
    expect(hub.y).toBeLessThan(250);
  });

  it("paints northern markers first so a tooltip is never buried", () => {
    const north = provider({
      id: "n",
      location: { ...provider().location, lat: 60, lon: 0 },
    });
    const south = provider({
      id: "s",
      location: { ...provider().location, lat: -30, lon: 0 },
    });
    expect(mapMarkers([south, north]).map((m) => m.id)).toEqual(["n", "s"]);
  });
});

describe("GPU chips", () => {
  it("aggregates identical models into one chip with a count", () => {
    const chips = gpuChips({
      ...provider().specs,
      gpus: [
        { name: "rtx3090", memory_total_mb: 24576 },
        { name: "rtx3090", memory_total_mb: 24576 },
        { name: "rtx4070", memory_total_mb: 12288 },
      ],
    });
    expect(chips).toEqual([
      { model: "rtx3090", count: 2, vramText: "24Gi" },
      { model: "rtx4070", count: 1, vramText: "12Gi" },
    ]);
  });

  it("renders a card with no reported memory as a bare model", () => {
    const [chip] = gpuChips({
      ...provider().specs,
      gpus: [{ name: "apple m2", memory_total_mb: null }],
    });
    expect(chip.vramText).toBeNull();
  });

  it("treats a reported zero as a failed probe, not as a card with no memory", () => {
    const [chip] = gpuChips({
      ...provider().specs,
      gpus: [{ name: "rtx3090", memory_total_mb: 0 }],
    });
    expect(chip.vramText).toBeNull();
  });

  it("keeps the good probe when two cards of one model disagree", () => {
    const [chip] = gpuChips({
      ...provider().specs,
      gpus: [
        { name: "rtx3090", memory_total_mb: null },
        { name: "rtx3090", memory_total_mb: 24576 },
      ],
    });
    expect(chip).toEqual({ model: "rtx3090", count: 2, vramText: "24Gi" });
  });

  it("names a vendor only from a token that can mean nothing else", () => {
    const specs = (names: string[]) => ({
      ...provider().specs,
      gpus: names.map((name) => ({ name, memory_total_mb: 8192 })),
    });
    expect(gpuVendor(specs(["rtx3090"]))).toBe("NVIDIA");
    expect(gpuVendor(specs(["mi250"]))).toBe("AMD");
    expect(gpuVendor(specs(["apple m2"]))).toBe("Apple");
    // Unrecognised, and a mixed box, both refuse.
    expect(gpuVendor(specs(["quantum-9000"]))).toBeNull();
    expect(gpuVendor(specs(["rtx3090", "mi250"]))).toBeNull();
  });
});

describe("bytes", () => {
  it("labels binary units as binary units", () => {
    expect(bytesText(64 * GIB)).toBe("64 GiB");
    expect(bytesText(2048 * GIB)).toBe("2 TiB");
    expect(bytesText(null)).toBeNull();
  });
});

describe("the capacity donuts", () => {
  const totals = (over: Partial<NetworkTotals> = {}): NetworkTotals => ({
    cpu_cores: { total: 362, used: 118 },
    gpus: { total: 24, used: 13 },
    memory_bytes: { total: 1416 * GIB, used: 402 * GIB },
    providers_online: 15,
    providers_total: 16,
    ...over,
  });

  it("draws three while storage is unreported and four once it is not", () => {
    expect(capacityDonuts(totals()).map((d) => d.key)).toEqual([
      "cpu",
      "gpu",
      "memory",
    ]);
    expect(
      capacityDonuts(
        totals({ storage_bytes: { total: 4096 * GIB, used: 900 * GIB } })
      ).map((d) => d.key)
    ).toEqual(["cpu", "gpu", "memory", "storage"]);
  });

  it("labels both halves in one unit, sized off the filled part", () => {
    const [, gpu, memory] = capacityDonuts(totals());
    expect(gpu.usedText).toBe("13 GPU");
    expect(gpu.totalText).toBe("24 GPU");
    // Sizing off the total gave "0.39 TiB / 1.38 TiB", from which the ratio
    // cannot be read back.
    expect(memory.usedText).toBe("402 GiB");
    expect(memory.totalText).toBe("1416 GiB");
  });

  it("has nothing to draw a provider against with no network totals", () => {
    expect(providerShareDonuts(provider().specs, null)).toBeNull();
  });

  it("draws one provider as its share of the network", () => {
    const donuts = providerShareDonuts(provider().specs, totals());
    expect(donuts?.map((d) => `${d.usedText} / ${d.totalText}`)).toEqual([
      "16 vCPU / 362 vCPU",
      "1 GPU / 24 GPU",
      "64 GiB / 1416 GiB",
    ]);
  });
});

describe("the table", () => {
  const rows = [
    provider({ id: "a", label: "alpha", leases: { active: 1, total: 9, resolved: 9, success_rate: 1 } }),
    provider({ id: "b", label: "bravo", leases: { active: 7, total: 9, resolved: 9, success_rate: 1 } }),
    provider({
      id: "c",
      label: "charlie",
      online: false,
      leases: { active: 0, total: 9, resolved: 9, success_rate: 1 },
      specs: { ...provider().specs, gpus: [], cpu_cores: 64 },
    }),
    provider({
      id: "d",
      label: "delta",
      own: true,
      uptime_7d_pct: null,
      uptime: { hours_up_7d: 0, hours_observed_7d: 0 },
      leases: { active: 3, total: 9, resolved: 9, success_rate: 1 },
    }),
  ];

  it("sorts active leases descending by default", () => {
    expect(sortProviders(rows, "leases", true).map((p) => p.id)).toEqual([
      "b",
      "d",
      "a",
      "c",
    ]);
  });

  it("keeps a missing value last in BOTH directions", () => {
    // "no data yet" is not a small number. Sorting it to the top of an
    // ascending uptime column would file every new machine among the worst.
    expect(sortProviders(rows, "uptime", false).map((p) => p.id).at(-1)).toBe("d");
    expect(sortProviders(rows, "uptime", true).map((p) => p.id).at(-1)).toBe("d");
  });

  it("breaks ties by name so the order never jitters", () => {
    const tied = sortProviders(
      [rows[0], { ...rows[1], label: "aaa", leases: rows[0].leases }],
      "leases",
      true
    );
    expect(tied.map((p) => p.label)).toEqual(["aaa", "alpha"]);
  });

  it("filters on what a person would actually type", () => {
    expect(matchesSearch(rows[0], "frankfurt")).toBe(true);
    expect(matchesSearch(rows[0], "RTX3090")).toBe(true);
    // Not the id: a stray paste should not look like a hit.
    expect(matchesSearch(rows[0], "p1")).toBe(false);
  });

  it("applies each filter independently", () => {
    const q = { search: "", activeOnly: false, gpuOnly: false, mineOnly: false };
    expect(filterProviders(rows, { ...q, activeOnly: true }).map((p) => p.id)).toEqual(["a", "b", "d"]);
    expect(filterProviders(rows, { ...q, gpuOnly: true }).map((p) => p.id)).toEqual(["a", "b", "d"]);
    expect(filterProviders(rows, { ...q, mineOnly: true }).map((p) => p.id)).toEqual(["d"]);
  });

  it("scales the CPU bar against the largest machine listed", () => {
    expect(maxCpuCores(rows)).toBe(64);
    expect(maxCpuCores([])).toBeNull();
    expect(
      maxCpuCores([provider({ specs: { ...provider().specs, cpu_cores: null } })])
    ).toBeNull();
  });

  it("never says '12 providers' over a filtered list of three", () => {
    expect(tableSummary(16, 16, 15)).toBe("16 providers · 15 online");
    expect(tableSummary(3, 16, 15)).toBe("3 of 16 providers · 15 online");
    expect(tableSummary(1, 1, 1)).toBe("1 provider · 1 online");
  });
});

describe("the trust badge is the console's one badge", () => {
  it("uses lib/machine-badge's words for all three tiers", () => {
    expect(badgeLabel("sandboxed")).toBe("Sandboxed");
    expect(badgeLabel("trusted")).toBe("Trusted");
    expect(badgeLabel("modules-only")).toBe("Modules only");
  });
});

describe("the 'Official' chip is a fact about ownership, not trust", () => {
  it("returns the chip's data when the provider is official", () => {
    expect(officialChip(provider({ official: true }))).toEqual({
      label: "Official",
    });
  });

  it("returns null when the provider is not official", () => {
    expect(officialChip(provider({ official: false }))).toBeNull();
  });

  it("returns null when the API has not started sending the field yet", () => {
    // The base fixture never sets `official` — this is the shape of every
    // response the console reads today.
    expect(officialChip(provider())).toBeNull();
  });

  it("is findable by search", () => {
    const official = provider({ id: "o", label: "alibaba-sgp-1", official: true });
    const ordinary = provider({ id: "x", label: "box-two", official: false });
    expect(matchesSearch(official, "official")).toBe(true);
    expect(matchesSearch(ordinary, "official")).toBe(false);
    // Still findable by its own label, exactly as before.
    expect(matchesSearch(official, "alibaba")).toBe(true);
  });
});

describe("the approximate time zone", () => {
  it("is arithmetic on a longitude, and absent without one", () => {
    expect(approxUtcOffset(8.68)).toBe("UTC+1");
    expect(approxUtcOffset(-122.7)).toBe("UTC−8");
    expect(approxUtcOffset(0)).toBe("UTC");
    expect(approxUtcOffset(null)).toBeNull();
  });
});
