// Every decision the providers pages make, in one place with no JSX around
// it — what a number means, when it is too thin to state, how a machine's
// GPUs collapse into chips, where a coordinate lands on the map.
//
// It lives here rather than in the components for the reason
// `lib/console/panel-state.ts` gives for its own split: `vitest.config.ts`
// collects `**/*.test.ts`, so a rule written inside a `.tsx` component gets
// no coverage. The rules below are exactly the ones worth a test — the two
// refusals especially — so they are written where a test can reach them and
// the components stay markup.
//
// THE TWO REFUSALS THIS MODULE EXISTS FOR:
//
//   1. A success rate over fewer than five resolved attempts is noise. It is
//      not rounded, not hedged with a tooltip, and not shown: it says so.
//   2. Uptime with no observation window is "no data yet", never 0%. A
//      machine nobody watched and a machine that was down all week are
//      different facts and they must not render as the same red zero.

import {
  MACHINE_BADGE_LABELS,
  MACHINE_BADGE_STYLES,
} from "@/lib/machine-badge";
import type {
  CapacityTotal,
  NetworkTotals,
  Provider,
  ProviderBadge,
  ProviderLocation,
  ProviderSpecs,
  ProviderUptime,
} from "./api";

// ---------------------------------------------------------------------------
// uptime
// ---------------------------------------------------------------------------

/** How much evidence stands behind a percentage, and therefore how it reads.
 * `unobserved` is not a failure and not a zero — it is the honest answer for
 * a machine that joined an hour ago. */
export type Tone = "good" | "warn" | "bad" | "unknown";

export interface Stat {
  text: string;
  tone: Tone;
  /** A sentence for the cell's `title`, when there is more to say than the
   * cell has room for. Never the only place a refusal is stated. */
  detail?: string;
}

/** Seven-day availability.
 *
 * Reads the OBSERVATION WINDOW first and the percentage second. A provider
 * with `hours_observed_7d === 0` has no denominator, so there is no
 * percentage to round — that is the case the API's `uptime` object was added
 * to make unambiguous, and it is checked before `uptime_7d_pct` precisely so
 * that a server sending `0` alongside an empty window cannot render as a
 * machine that was down all week.
 *
 * The bands are graded rather than uniformly green: a fleet page where every
 * number is the same colour tells a reader nothing, and 91% and 100% are not
 * the same news. */
export function uptimeStat(p: {
  uptime_7d_pct: number | null;
  /** Optional so the two callers that only have a percentage — a preview
   * fixture, a future summary shape — can still ask. Present on every
   * `Provider` the API sends. */
  uptime?: ProviderUptime;
}): Stat {
  if (p.uptime && p.uptime.hours_observed_7d <= 0) {
    return {
      text: "no data yet",
      tone: "unknown",
      detail: "Nothing has been observed for this provider in the last 7 days.",
    };
  }
  if (p.uptime_7d_pct === null) {
    return {
      text: "no data yet",
      tone: "unknown",
      detail: "The API reported no uptime for this provider.",
    };
  }
  const pct = p.uptime_7d_pct;
  return {
    text: pctText(pct),
    tone: pct >= 99 ? "good" : pct >= 90 ? "warn" : "bad",
    detail:
      p.uptime && p.uptime.hours_observed_7d > 0
        ? `${round1(p.uptime.hours_up_7d ?? 0)} of ${round1(
            p.uptime.hours_observed_7d
          )} observed hours.`
        : undefined,
  };
}

/** The evidence floor under a success rate.
 *
 * FIVE, and the number is the point. Four resolved attempts can only ever
 * render as 0/25/50/75/100 %, so every value it can produce looks like a
 * verdict and none of them is one. The console says so in words rather than
 * showing a number it would have to caveat. */
export const MIN_RESOLVED_FOR_RATE = 5;

export function successStat(leases: {
  resolved: number;
  success_rate: number | null;
}): Stat {
  if (leases.resolved < MIN_RESOLVED_FOR_RATE || leases.success_rate === null) {
    return {
      text: "n/a — low evidence",
      tone: "unknown",
      detail: `${leases.resolved} resolved ${
        leases.resolved === 1 ? "attempt" : "attempts"
      }. A rate needs at least ${MIN_RESOLVED_FOR_RATE} to mean anything.`,
    };
  }
  // READ AS A FRACTION, with one guard.
  //
  // `uptime_7d_pct` is confirmed to arrive as 0–100 and says `pct` in its
  // name; this field says `rate` and has had no such confirmation. The
  // difference in naming is the only evidence either way, so it is read as a
  // fraction — and a value above 1, which cannot be one, is taken as a
  // percentage already. That guard makes the two readings agree everywhere
  // except a rate of exactly 1.0, where "100%" is the answer under both.
  //
  // **Flagged for the API author.** If `success_rate` is in fact 0–100, this
  // line is the one to change and nothing else on the page moves.
  const pct =
    leases.success_rate <= 1 ? leases.success_rate * 100 : leases.success_rate;
  return {
    text: pctText(pct),
    tone: pct >= 95 ? "good" : pct >= 80 ? "warn" : "bad",
    detail: `${leases.resolved} resolved attempts.`,
  };
}

/** One decimal, and never a trailing `.0` — `100%`, not `100.0%`. */
export function pctText(pct: number): string {
  return `${round1(pct)}%`;
}

function round1(n: number): number {
  return Math.round(n * 10) / 10;
}

/** Tones on a light console surface — the table, the cards. */
export const TONE_CLASS: Record<Tone, string> = {
  good: "text-evergreen",
  warn: "text-warning-foreground",
  bad: "text-destructive",
  unknown: "text-muted-foreground",
};

/** Tones on a graphite instrument panel.
 *
 * IT IS NOT THE SAME MAP, AND ONE ENTRY IS THE REASON. `--warning-foreground`
 * is `#74551c`, a dark olive chosen to sit on cream — on the `#0b0d0e` panel
 * it is a barely-visible brown, and "94%" in it read as damaged text rather
 * than as a number. `.marketing-dark` remaps most tokens but not that one, so
 * the fix belongs here rather than in a scope this session does not own.
 * `--z-warning` (`#d5a33f`) is the same warning in the palette's own bright
 * register, and it is the value the landing's dark surfaces already use.
 *
 * The other three are spelled out rather than spread from `TONE_CLASS` so
 * that a future change to one map is a visible decision about the other. */
export const TONE_CLASS_DARK: Record<Tone, string> = {
  good: "text-evergreen",
  warn: "text-[var(--z-warning)]",
  bad: "text-destructive",
  unknown: "text-muted-foreground",
};

// ---------------------------------------------------------------------------
// location
// ---------------------------------------------------------------------------

/** A place, from whichever of the three fields the provider actually has.
 *
 * Returns `null` for "nowhere stated", which every caller renders as an
 * em dash. It never falls back to coordinates: `47.4, 8.5` is not a place a
 * reader can use, and printing it would make an unlocated machine look
 * located. */
export function locationText(loc: ProviderLocation): string | null {
  const parts = [loc.city, loc.region, loc.country].filter(
    (part): part is string => typeof part === "string" && part.trim() !== ""
  );
  if (parts.length === 0) return null;
  // City and region are frequently the same word for a city-state or a
  // single-region country; saying it twice reads as a bug.
  const seen: string[] = [];
  for (const part of parts) {
    if (!seen.some((s) => s.toLowerCase() === part.toLowerCase())) seen.push(part);
  }
  return seen.join(", ");
}

/** The shortest form that still names somewhere — for a table cell, where
 * "San Francisco, California, US" is three columns wide. */
export function locationShort(loc: ProviderLocation): string | null {
  const place = loc.city ?? loc.region;
  if (place && loc.country) return `${place}, ${loc.country}`;
  return locationText(loc);
}

/** Whether this provider can be drawn on the map at all. A provider without
 * coordinates is still a provider — it is missing from the map and present
 * in the table, and nothing about the table's counts changes. */
export function isPlaced(p: Provider): boolean {
  return (
    typeof p.location.lat === "number" &&
    typeof p.location.lon === "number" &&
    Number.isFinite(p.location.lat) &&
    Number.isFinite(p.location.lon)
  );
}

// ---------------------------------------------------------------------------
// specs
// ---------------------------------------------------------------------------

/** One GPU model this provider has, with how many of it and how much memory
 * each carries.
 *
 * Aggregated by model because a box with four identical cards is "rtx3090
 * ×4", not four chips — and because the model, not the instance, is what a
 * reader is scanning for. */
export interface GpuChip {
  /** The model as the machine reported it, lowercased for the chip. The
   * report is the authority: nothing here maps "NVIDIA GeForce RTX 3090"
   * onto a tidier name, because that mapping would be this console deciding
   * what hardware somebody owns. */
  model: string;
  count: number;
  /** `24Gi`, from `memory_total_mb`.
   *
   * NULL WHEN THE DRIVER DID NOT SAY, and the chip then renders as the bare
   * model. `memory_total_mb` is nullable in the contract, and a card whose
   * memory was not reported must not render as `0 GB` — that would be a
   * claim about the hardware rather than about what we know of it. A
   * reported zero is treated the same way for the same reason: no GPU has
   * no memory, so a zero is a failed probe. */
  vramText: string | null;
}

export function gpuChips(specs: ProviderSpecs): GpuChip[] {
  const byModel = new Map<string, { count: number; mb: number | null }>();
  for (const gpu of specs.gpus) {
    const model = gpu.name.trim();
    if (!model) continue;
    const mb =
      typeof gpu.memory_total_mb === "number" && gpu.memory_total_mb > 0
        ? gpu.memory_total_mb
        : null;
    const at = byModel.get(model);
    if (at) {
      at.count += 1;
      // The larger of the two, and null only if neither card reported: two
      // cards of one model with one bad probe should still show the VRAM
      // the good probe found.
      at.mb = at.mb === null ? mb : mb === null ? at.mb : Math.max(at.mb, mb);
    } else {
      byModel.set(model, { count: 1, mb });
    }
  }
  return [...byModel.entries()].map(([model, { count, mb }]) => ({
    model,
    count,
    vramText: mb === null ? null : `${Math.round(mb / 1024)}Gi`,
  }));
}

export function gpuCount(specs: ProviderSpecs): number {
  return specs.gpus.length;
}

/** Vendor tokens, and the whole reason this is a lookup rather than a guess.
 *
 * The API reports a MODEL and no vendor. "rtx3090" implies NVIDIA to a reader
 * who knows the product line, and nothing at all to one who does not, so
 * naming the vendor is worth doing — but only from the string the machine
 * actually sent, and only when the string contains a token that can mean
 * nothing else. Anything unrecognised returns `null` and the row is omitted;
 * the console never picks the likeliest vendor for a model it has not seen.
 *
 * The row that renders this says where it came from. */
const VENDOR_TOKENS: ReadonlyArray<readonly [RegExp, string]> = [
  [/nvidia|geforce|\brtx|\bgtx|tesla|quadro|\ba100\b|\bh100\b|\bl40\b|\ba\d{4}\b/i, "NVIDIA"],
  [/\bamd\b|radeon|\bmi\d{3}\b|\brx\s?\d{3,4}\b/i, "AMD"],
  [/\bintel\b|\barc\b|iris/i, "Intel"],
  [/\bapple\b|\bm[1-4]\b/i, "Apple"],
];

/** The one vendor every reported model agrees on, or `null`.
 *
 * A box with an NVIDIA card and an AMD card has no single vendor, and saying
 * "NVIDIA" because it was listed first would be wrong about the hardware. */
export function gpuVendor(specs: ProviderSpecs): string | null {
  const vendors = new Set<string>();
  for (const gpu of specs.gpus) {
    const hit = VENDOR_TOKENS.find(([re]) => re.test(gpu.name));
    if (!hit) return null;
    vendors.add(hit[1]);
  }
  return vendors.size === 1 ? [...vendors][0] : null;
}

/** A rough local time zone from longitude — `UTC+2`, `UTC−7`.
 *
 * NOT A FACT THE API SENDS, and the row that renders it says so. Fifteen
 * degrees is an hour by definition, so this is arithmetic on a coordinate the
 * provider reported rather than a lookup of anything: it is right about the
 * solar hour and can be an hour or two off the civil one wherever a country
 * has drawn its zone wide or kept summer time. Worth showing because "is this
 * machine's owner awake" is a real question about a volunteer fleet; worth
 * labelling because a reader must not schedule anything by it. */
export function approxUtcOffset(lon: number | null): string | null {
  if (lon === null || !Number.isFinite(lon)) return null;
  const hours = Math.round(lon / 15);
  if (hours === 0) return "UTC";
  // U+2212 MINUS SIGN: a hyphen next to a digit in a proportional face reads
  // as a hyphenation point.
  return `UTC${hours > 0 ? "+" : "−"}${Math.abs(hours)}`;
}

const GIB = 1024 ** 3;

/** Binary units, and labelled as such. `16 GiB` is what 17179869184 bytes
 * is; calling it "16 GB" would be off by 7% and this codebase already spells
 * KiB in the artifacts panel. */
export function bytesText(bytes: number | null): string | null {
  if (bytes === null || !Number.isFinite(bytes)) return null;
  const gib = bytes / GIB;
  if (gib >= 1024) return `${round1(gib / 1024)} TiB`;
  if (gib >= 10) return `${Math.round(gib)} GiB`;
  return `${round1(gib)} GiB`;
}

/** `12 / 64 GiB` — one unit, stated once, at the end. Two units in one cell
 * is what makes a capacity column unreadable. */
export function bytesPairText(used: number, total: number): string {
  const gib = total / GIB;
  const unit = gib >= 1024 ? "TiB" : "GiB";
  const div = unit === "TiB" ? GIB * 1024 : GIB;
  const fmt = (n: number) => {
    const v = n / div;
    return v >= 100 ? String(Math.round(v)) : String(round1(v));
  };
  return `${fmt(used)} / ${fmt(total)} ${unit}`;
}

// ---------------------------------------------------------------------------
// trust badge
// ---------------------------------------------------------------------------

/** The badge's words and colours come from `lib/machine-badge.ts` and from
 * nowhere else.
 *
 * `ProviderBadge` IS `MachineBadge` (see `./api.ts`), so these are two
 * one-line pass-throughs rather than a mapping — deliberately kept as named
 * functions so every call site on this page reads through the same door, and
 * so a future divergence between the API's spelling and the console's has
 * exactly one place to be handled. The badge a host sees on `/machines` and
 * the badge they see here are the same badge; a second styling table for the
 * same three words is how two pages start disagreeing about what "trusted"
 * looks like. */
export function badgeLabel(badge: ProviderBadge): string {
  return MACHINE_BADGE_LABELS[badge];
}

export function badgeClass(badge: ProviderBadge): string {
  return MACHINE_BADGE_STYLES[badge];
}

// ---------------------------------------------------------------------------
// the "Official" chip
// ---------------------------------------------------------------------------

export interface OfficialChip {
  label: string;
}

/** The "Official" chip's data, or `null` when this is not a machine Zolli
 * Labs itself operates.
 *
 * NOT A TRUST TIER. `badge` (above) states what a machine PROVES about
 * itself — sandboxed, trusted, modules-only — and says nothing about who
 * owns it. `official` states who OWNS it, and says nothing about what it can
 * be trusted to run: an official machine can carry any badge. The two answer
 * different questions, so this renders as its own chip rather than folding
 * into `badgeLabel`/`badgeClass`, which would conflate "who runs this" with
 * "what can this be trusted to do".
 *
 * `p.official` UNDEFINED READS AS `false`, on purpose — the API may deploy
 * this field after the console does (see `Provider.official` in `./api.ts`),
 * and a provider from a response that predates it must render as an
 * ordinary listing, not crash or half-render. */
export function officialChip(p: Pick<Provider, "official">): OfficialChip | null {
  return p.official ? { label: "Official" } : null;
}

/** Classes for the chip, in the border/10-bg/text idiom this page already
 * uses for `TrustBadge`'s amber "trusted" tier and the table's orange
 * "yours" tag — but in the green this design system spends on "verified
 * good" (`--evergreen`, the same token behind `TONE_CLASS.good`), which
 * neither of those two claims. Built from a theme token rather than a
 * hard-coded hex, so — unlike `--warning-foreground` — it resolves through
 * `.marketing-dark`'s remap with no second dark-register map needed. */
export const OFFICIAL_CHIP_CLASS = "border-evergreen/30 bg-evergreen/10 text-evergreen";

// ---------------------------------------------------------------------------
// the capacity donuts
// ---------------------------------------------------------------------------

export interface DonutDatum {
  key: string;
  /** "GPU", "CPU", "Memory", "Storage". */
  title: string;
  used: number;
  total: number;
  /** `197 GPU`, the committed half of the centre label. */
  usedText: string;
  /** `425 GPU`, the whole. */
  totalText: string;
}

/** The header strip's donuts, in the order Akash's reads: CPU, GPU, Memory,
 * then Storage.
 *
 * THREE OR FOUR, decided by the data. `storage_bytes` is absent from every
 * response the API sends today because nothing in the fleet reports
 * per-machine disk. A fourth donut drawn at 0/0 would claim the network has
 * no storage, which is not what "we do not measure this" means — so the
 * entry is simply not produced, and the row lays out three. */
export function capacityDonuts(totals: NetworkTotals): DonutDatum[] {
  const out: DonutDatum[] = [
    countDonut("cpu", "CPU", totals.cpu_cores, "vCPU"),
    countDonut("gpu", "GPU", totals.gpus, "GPU"),
    byteDonut("memory", "Memory", totals.memory_bytes),
  ];
  if (totals.storage_bytes) {
    out.push(byteDonut("storage", "Storage", totals.storage_bytes));
  }
  return out;
}

function countDonut(
  key: string,
  title: string,
  cap: CapacityTotal,
  unit: string
): DonutDatum {
  return {
    key,
    title,
    used: cap.used,
    total: cap.total,
    usedText: `${cap.used} ${unit}`,
    totalText: `${cap.total} ${unit}`,
  };
}

function byteDonut(key: string, title: string, cap: CapacityTotal): DonutDatum {
  /** THE UNIT COMES FROM THE FILLED PART, NOT THE WHOLE.
   *
   * Sizing off the total put a 64 GiB machine on a 1.4 TiB network at
   * `0.06 TiB / 1.38 TiB`: two numbers whose ratio is exactly what the ring
   * already shows and whose magnitudes are now unreadable. Sizing off the
   * used value gives `64 GiB / 1416 GiB` — a big number for the whole, which
   * is fine, and a legible one for the part, which is the number the reader
   * came for. Both halves stay in ONE unit either way; only which unit
   * changes. */
  const basis = cap.used > 0 ? cap.used : cap.total;
  const unit = basis / GIB >= 1024 ? "TiB" : "GiB";
  const div = unit === "TiB" ? GIB * 1024 : GIB;
  /** Three significant figures, whatever the magnitude.
   *
   * `round1` alone rendered a 402 GiB commitment against a 1.4 TiB network as
   * `0.4 TiB / 1.4 TiB` — two numbers with one digit each, from which the
   * ratio the ring is drawing cannot be read back. The unit comes from the
   * total so both halves stay comparable; the precision comes from the value
   * so neither half is rounded into uselessness. */
  const fmt = (n: number) => {
    const v = n / div;
    const dp = v >= 100 ? 0 : v >= 10 ? 1 : 2;
    const text = v.toFixed(dp);
    // Trailing zeros only, and only after a decimal point — a bare
    // `replace(/0+$/)` turns 1400 into 14. `64.0 GiB` claims a tenth of a
    // gibibyte of precision the number does not have.
    const trimmed = text.includes(".")
      ? text.replace(/\.?0+$/, "")
      : text;
    return `${trimmed} ${unit}`;
  };
  return {
    key,
    title,
    used: cap.used,
    total: cap.total,
    usedText: fmt(cap.used),
    totalText: fmt(cap.total),
  };
}

/** The same three or four donuts for ONE provider, drawn as its share of the
 * network rather than as its own utilisation.
 *
 * WHY A SHARE. The list response carries `used` per resource for the whole
 * network; the provider response carries this machine's `specs` and no `used`
 * at all. There is therefore no honest per-provider "committed vs idle" to
 * draw, and inventing one — `leases.active` as a stand-in for cores in use,
 * say — would be this console making up a measurement. A share of the network
 * IS in the data: both halves are numbers the API sent, and it answers a
 * question a reader of a single provider actually has.
 *
 * The caption under the row says which reading it is. Returns `null` when the
 * network totals could not be read, so the section renders as unread rather
 * than as a provider with no capacity. */
export function providerShareDonuts(
  specs: ProviderSpecs,
  totals: NetworkTotals | null
): DonutDatum[] | null {
  if (!totals) return null;
  const out: DonutDatum[] = [
    countDonut(
      "cpu",
      "CPU",
      { used: specs.cpu_cores ?? 0, total: totals.cpu_cores.total },
      "vCPU"
    ),
    countDonut(
      "gpu",
      "GPU",
      { used: specs.gpus.length, total: totals.gpus.total },
      "GPU"
    ),
    byteDonut("memory", "Memory", {
      used: specs.memory_bytes ?? 0,
      total: totals.memory_bytes.total,
    }),
  ];
  if (totals.storage_bytes) {
    out.push(
      byteDonut("storage", "Storage", {
        used: 0,
        total: totals.storage_bytes.total,
      })
    );
  }
  return out;
}

// ---------------------------------------------------------------------------
// the map
// ---------------------------------------------------------------------------

/** The equirectangular box `components/network/world-map-path.ts` is drawn
 * in. Every projection on this page shares it, so a dot and the coastline
 * under it cannot drift apart. */
export const MAP_BOX = { width: 1000, height: 500 } as const;

/** The visible crop: 84°N to −56°S.
 *
 * Antarctica is off the bottom on purpose — nobody hosts there, and the 20%
 * of the frame it occupies is 20% less map for the providers who do exist.
 * The top is cut just above Svalbard for the same reason. */
export const MAP_VIEW = { x: 0, y: 17, width: 1000, height: 388 } as const;

export interface MapXY {
  x: number;
  y: number;
}

export function project(lat: number, lon: number): MapXY {
  return {
    x: ((lon + 180) / 360) * MAP_BOX.width,
    y: ((90 - lat) / 180) * MAP_BOX.height,
  };
}

/** Where the coordinator runs: Render's `oregon` region, per `render.yaml`
 * (every one of the six services names it). Not a guess and not a decoration
 * — the arcs on the map are drawn to it because that is genuinely the other
 * end of every provider's connection. */
export const COORDINATOR_HUB = {
  lat: 45.5,
  lon: -122.7,
  label: "Coordinator · Render oregon",
} as const;

export interface MapMarker {
  id: string;
  label: string;
  x: number;
  y: number;
  online: boolean;
  own: boolean;
  activeLeases: number;
  place: string | null;
}

/** Providers that can be drawn, projected once.
 *
 * Sorted by y so northern dots paint first and a southern dot's tooltip
 * hitbox is never buried under one above it. Nothing is dropped for
 * overlapping: two machines in one city are two dots, and pretending
 * otherwise would undercount the map against the table beside it. */
export function mapMarkers(providers: Provider[]): MapMarker[] {
  return providers
    .filter(isPlaced)
    .map((p) => {
      const { x, y } = project(p.location.lat as number, p.location.lon as number);
      return {
        id: p.id,
        label: p.label,
        x,
        y,
        online: p.online,
        own: p.own,
        activeLeases: p.leases.active,
        place: locationShort(p.location),
      };
    })
    .sort((a, b) => a.y - b.y);
}

// ---------------------------------------------------------------------------
// the table: search, filter, sort
// ---------------------------------------------------------------------------

export type SortKey =
  | "label"
  | "location"
  | "uptime"
  | "leases"
  | "cpu"
  | "gpu"
  | "memory";

export interface TableQuery {
  search: string;
  activeOnly: boolean;
  gpuOnly: boolean;
  mineOnly: boolean;
  sort: SortKey;
  desc: boolean;
}

/** Active leases, descending. The page opens on the question it exists to
 * answer — who is actually carrying work — rather than on an alphabetical
 * list, which is a directory. */
export const DEFAULT_TABLE_QUERY: TableQuery = {
  search: "",
  activeOnly: false,
  gpuOnly: false,
  mineOnly: false,
  sort: "leases",
  desc: true,
};

/** Free text over the fields a person would actually type: the label, the
 * place, the GPU models, and — because it is a real word a reader might type
 * to find Zolli Labs' own machines — "official" for a provider the chip is
 * shown on. Not the id — an opaque id is not something anybody searches by,
 * and matching it makes a stray paste look like a hit. */
export function matchesSearch(p: Provider, search: string): boolean {
  const q = search.trim().toLowerCase();
  if (!q) return true;
  const hay = [
    p.label,
    p.location.city,
    p.location.region,
    p.location.country,
    ...p.specs.gpus.map((g) => g.name),
    p.specs.os,
    p.specs.architecture,
    officialChip(p) ? "official" : null,
  ]
    .filter((s): s is string => typeof s === "string")
    .join(" ")
    .toLowerCase();
  return hay.includes(q);
}

export function filterProviders(
  providers: Provider[],
  q: Pick<TableQuery, "search" | "activeOnly" | "gpuOnly" | "mineOnly">
): Provider[] {
  return providers.filter((p) => {
    if (q.activeOnly && !p.online) return false;
    if (q.gpuOnly && p.specs.gpus.length === 0) return false;
    if (q.mineOnly && !p.own) return false;
    return matchesSearch(p, q.search);
  });
}

/** Sort, with `null` always last regardless of direction.
 *
 * A missing value is not a small value. Sorting "no data yet" to the top of
 * an ascending uptime column would put the machines nobody has measured
 * where the worst machines belong, and a reader scanning for trouble would
 * find a list of new arrivals. */
export function sortProviders(
  providers: Provider[],
  sort: SortKey,
  desc: boolean
): Provider[] {
  const dir = desc ? -1 : 1;
  return [...providers].sort((a, b) => {
    if (sort === "label") return dir * a.label.localeCompare(b.label);
    if (sort === "location") {
      const la = locationShort(a.location);
      const lb = locationShort(b.location);
      if (la === null && lb === null) return 0;
      if (la === null) return 1;
      if (lb === null) return -1;
      return dir * la.localeCompare(lb);
    }
    const va = sortValue(a, sort);
    const vb = sortValue(b, sort);
    if (va === null && vb === null) return a.label.localeCompare(b.label);
    if (va === null) return 1;
    if (vb === null) return -1;
    if (va === vb) return a.label.localeCompare(b.label);
    return dir * (va - vb);
  });
}

function sortValue(p: Provider, sort: SortKey): number | null {
  switch (sort) {
    case "uptime":
      return p.uptime && p.uptime.hours_observed_7d <= 0
        ? null
        : p.uptime_7d_pct;
    case "leases":
      return p.leases.active;
    case "cpu":
      return p.specs.cpu_cores;
    case "gpu":
      return p.specs.gpus.length;
    case "memory":
      return p.specs.memory_bytes;
    default:
      return null;
  }
}

/** The largest core count in a set of providers, or `null` when nobody
 * reported one.
 *
 * WHAT THE TABLE'S BATTERY IS A PICTURE OF, and the reason it is this rather
 * than a utilisation bar. The API reports `used` for the NETWORK
 * (`NetworkTotals.cpu_cores.used`) and nothing per provider, so there is no
 * committed-vs-idle figure for one machine to fill a battery with. Filling it
 * from `leases.active`, or from `online`, would be this console inventing a
 * measurement — and it would be believed, because a battery in a CPU column
 * reads as utilisation whatever the header says.
 *
 * So the glyph is a SIZE, against the biggest machine on the list: a real
 * comparison between two numbers the API sent, useful for exactly the thing a
 * reader scans a fleet table for — where the big boxes are. The table states
 * the reading in a footnote under itself rather than leaving it to be
 * guessed. */
export function maxCpuCores(providers: Provider[]): number | null {
  let max: number | null = null;
  for (const p of providers) {
    const cores = p.specs.cpu_cores;
    if (cores !== null && Number.isFinite(cores) && (max === null || cores > max)) {
      max = cores;
    }
  }
  return max;
}

/** The one-line summary over the table: how many rows are shown, out of how
 * many there are, and how many of those are online. Assembled here so the
 * sentence is testable and so it can never say "12 providers" over a
 * filtered list of three. */
export function tableSummary(shown: number, all: number, online: number): string {
  const noun = all === 1 ? "provider" : "providers";
  if (shown === all) return `${all} ${noun} · ${online} online`;
  return `${shown} of ${all} ${noun} · ${online} online`;
}
