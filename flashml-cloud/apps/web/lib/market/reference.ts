/** The generated reference band table, and the one rule for using it.
 *
 * WHY THIS MODULE EXISTS. The coordinator's board only has rows for the
 * eight classes on its own capability ladder, and only prices the ones a
 * host has actually listed in. A console that renders that alone shows an
 * empty market to the first hundred visitors. `reference-price-seed.json`
 * is a generated snapshot of published vendor prices — real observations,
 * but not OUR book — so it can fill the page provided it never passes for
 * live. That "provided" is the whole module: every value that leaves here
 * is reference data, and the caller is expected to badge it as such.
 *
 * THE PARITY. 1 ZC = 1 USD on marketplace surfaces, so a seed price in
 * USD/hour is the same digits in ZC/hour. `referenceZcPerHour` converts to
 * the millicredit integer the rest of the console counts in, which is what
 * lets `formatZc` render a seed row and a live row with one formatter.
 *
 * THE NORMALIZER IS DELIBERATELY ALMOST EMPTY. See ALIAS below: two of the
 * eight capability classes name exactly one card, and the other six name a
 * VRAM floor that four different cards land on. Guessing which one a buyer
 * is looking at would put a $0.19 V100 price on a row that might be an
 * $0.84 RTX 6000 Ada. No match is a row that says "no match"; a wrong match
 * is a row that lies with a number on it.
 */
import seed from "./reference-price-seed.json";

export interface ReferenceMeta {
  /** Always true here — the file is generated, and the flag exists so a UI
   * cannot render it without having had the fact in hand. */
  generated: boolean;
  /** ISO date the snapshot was taken, for the badge caption. */
  generatedAt: string;
  basis: string;
}

export type ReferenceKind = "gpu" | "cpu" | "tpu";

export interface ReferencePoint {
  /** `YYYY-MM-DD`. */
  date: string;
  priceUsdHr: number;
}

export interface ReferenceClass {
  /** The seed's own ticker — a hardware SKU (`H100-80G`), NOT a coordinator
   * capability class. The two vocabularies are different on purpose; see
   * ALIAS. */
  klass: string;
  displayName: string;
  kind: ReferenceKind;
  vramGb?: number;
  cores?: number;
  refPriceUsdHr: number;
  bandLowUsdHr: number;
  bandHighUsdHr: number;
  sources: string[];
  /** Oldest → newest, 30 daily points. */
  history: ReferencePoint[];
}

interface ReferenceSeed {
  meta: ReferenceMeta;
  classes: ReferenceClass[];
}

// The JSON's inferred type widens `kind` to `string`; the cast is the only
// place the file's shape is asserted, and `reference.test.ts` checks the
// assertion against the real file rather than trusting it.
const SEED = seed as ReferenceSeed;

export const REFERENCE_META: ReferenceMeta = SEED.meta;

export const REFERENCE_CLASSES: readonly ReferenceClass[] = SEED.classes;

const BY_KLASS: ReadonlyMap<string, ReferenceClass> = new Map(
  SEED.classes.map((c) => [c.klass, c])
);

/** Coordinator capability class → seed klass, where the correspondence is
 * the class's own definition rather than a guess.
 *
 * The ladder in `marketplace.py` is eight VRAM floors:
 * `cpu-small`, `cpu-large`, `gpu-8gb`, `gpu-16gb`, `gpu-24gb`, `gpu-48gb`,
 * `gpu-80gb`, `gpu-80gb-hopper`. Only the top two name one card each:
 *
 *   `gpu-80gb-hopper` exists BECAUSE compute capability 9.0 separates an
 *   H100 from an A100 at the same 80 GB — the class is "the Hopper 80",
 *   which is the H100.
 *   `gpu-80gb` is then the same floor without Hopper, which is the A100 80.
 *
 * The other six are left unmapped, and that is the decision this table is
 * here to record:
 *
 *   `gpu-48gb`  L40S, L40, A6000 and RTX 6000 Ada — $0.53 to $0.99.
 *   `gpu-24gb`  4090, 3090, A5000, L4, and an under-claimed 40 GB A100.
 *   `gpu-16gb`  T4, A4000, V100 — $0.19 to $0.295.
 *   `gpu-8gb`   nothing in the seed reaches down this far.
 *   `cpu-small` / `cpu-large` the seed's smallest CPU line is 32 vCPU, and
 *               `cpu-large` starts at 8 cores; a 32-vCPU price on an
 *               8-core row would be off by more than the ladder's own
 *               gpu-to-cpu ratio.
 *
 * Adding a row here means the correspondence is definitional. It is not a
 * place to record which card is most common — nor a place to put a price for
 * an unpriced rung. That is `classDerivation` below, which answers a
 * different question ("what is an hour of this class worth") and answers it
 * under a different stamp. */
export const CAPABILITY_CLASS_ALIAS: Readonly<Record<string, string>> = {
  "gpu-80gb-hopper": "H100-80G",
  "gpu-80gb": "A100-80G",
};

/** The seed row for a class name, or null when nothing here describes it.
 *
 * Exact seed klass first — a caller may hold either vocabulary — then the
 * alias table. Never a prefix, a substring or a nearest match. */
export function referenceClass(klass: string): ReferenceClass | null {
  const exact = BY_KLASS.get(klass);
  if (exact) return exact;
  const aliased = CAPABILITY_CLASS_ALIAS[klass];
  return aliased ? (BY_KLASS.get(aliased) ?? null) : null;
}

/** The seed klass a capability class resolves to, for de-duplicating the
 * board: a seed row already represented by a live rung must not appear a
 * second time under its hardware name. */
export function referenceKlassFor(capabilityClass: string): string | null {
  return referenceClass(capabilityClass)?.klass ?? null;
}

/** The seed's reference price as millicredits per hour, under 1 ZC = 1 USD.
 * Rounded, because the console counts in whole millicredits and a binary
 * float of `0.295 * 1000` is not one. */
export function referenceZcPerHour(entry: ReferenceClass): number {
  return Math.round(entry.refPriceUsdHr * 1000);
}

/** The one-line spec: what the class physically is. Memory for anything
 * with memory reported, cores for the CPU lines, and the kind either way so
 * a bare "14 cores" is not mistaken for a GPU row. */
export function referenceSpecLine(entry: ReferenceClass): string {
  if (entry.vramGb !== undefined) return `${entry.vramGb} GB · ${entry.kind}`;
  if (entry.cores !== undefined) return `${entry.cores} cores · ${entry.kind}`;
  return entry.kind;
}

/** A drawable point: a stamp and a millicredit level, the shape every chart
 * and sparkline in the market surface consumes. */
export interface SeriesPoint {
  at: string;
  valueMzc: number;
}

/** The seed's daily history as millicredit points, oldest → newest — the
 * order `PriceHistory` draws in. Same rounding as `referenceZcPerHour`. */
export function referenceSeries(entry: ReferenceClass): SeriesPoint[] {
  return entry.history.map((point) => ({
    at: point.date,
    valueMzc: Math.round(point.priceUsdHr * 1000),
  }));
}

// ---------------------------------------------------------------------------
// deriving a price for a capability class
// ---------------------------------------------------------------------------
//
// WHY THIS EXISTS, GIVEN THAT ALIAS REFUSES TO GUESS. ALIAS answers "which
// card IS this class", and for six of the eight the answer is genuinely
// "none of them". That refusal is still right. What it left on screen was
// not: the coordinator's own eight rungs are the rows a visitor reads first,
// and six of them rendered as a dash, the words "no history", and a chip
// advertising a live book with nothing in it.
//
// THE FLOOR IS A PROMISE, AND THE CHEAPEST CARD THAT KEEPS IT SETS THE PRICE.
// `gpu-24gb` promises a task 24 GB and says nothing else; a host who fills it
// with the cheapest card clearing that floor has kept the promise in full. So
// the cheapest qualifying card is what an hour of the class is worth — not the
// mean of the four cards that might land there, which is a number no host
// would accept and no buyer would pay.
//
// This is an ESTIMATE and the module's one rule is unchanged: it leaves here
// stamped `derived`, carrying the sentence that names its donor, and the
// caller renders neither without the other.

/** VRAM floors in MiB, richest first, TRANSCRIBED from `GPU_CLASS_FLOORS_MB`
 * in `apps/api/flashml_cloud_api/marketplace.py`. The ladder's thresholds,
 * not this file's reading of the class names.
 *
 * The names are round and the floors are not — `gpu-24gb` is 23_000 — because
 * a driver reports usable memory rather than the number on the box (a 24 GB
 * 4090 reports 24564 MiB, an 80 GB H100 reports 81559). Comparing a seed row
 * against 24 instead of 23_000 would file cards on the wrong side of a rung
 * the coordinator has already classified machines with.
 *
 * A class name absent from this table gets NO derivation. The ladder can grow
 * a rung this console has never heard of, and a nearest-floor guess is the
 * same wrong number with a price on it that ALIAS exists to refuse. */
export const GPU_CLASS_FLOORS_MB: readonly (readonly [number, string])[] = [
  [79_000, "gpu-80gb"],
  [46_000, "gpu-48gb"],
  [23_000, "gpu-24gb"],
  [15_000, "gpu-16gb"],
  [7_800, "gpu-8gb"],
] as const;

/** Cores at which the ladder calls a CPU machine `cpu-large` rather than
 * `cpu-small` — `CPU_LARGE_MIN_CORES` in `marketplace.py`. It is the only
 * core count the ladder states, which is why both CPU derivations below are
 * written in terms of it rather than in terms of two new numbers. */
export const CPU_LARGE_MIN_CORES = 8;

/** The core count each CPU class is PRICED AT. **Stated, not observed.**
 *
 * Nothing in the seed, the ladder or our own book reports the cores of a
 * typical machine in either class, and the seed's CPU rows are whole
 * instances of 32, 64 and 128 vCPU — an order of magnitude away from the
 * boundary the ladder actually splits on. So the multiplier is a decision,
 * taken here, once, from the one number the ladder does state:
 *
 *   `cpu-large` is priced AT its own floor. The class is 8 cores and up with
 *   no ceiling, and pricing the floor under-claims — the same direction
 *   `capability_class` itself takes when it refuses to promise more than it
 *   read.
 *   `cpu-small` is priced at half the boundary, the middle of the 1–7 cores
 *   the class can hold.
 *
 * Change these and the two CPU rows change; that is the point of them being
 * a named constant rather than a literal inside the arithmetic. */
export const CPU_TYPICAL_CORES: Readonly<Record<string, number>> = {
  "cpu-small": CPU_LARGE_MIN_CORES / 2,
  "cpu-large": CPU_LARGE_MIN_CORES,
};

/** Seed rows that may never donate a price, whatever the arithmetic says.
 *
 * The Apple row is a Mac mini rented whole, by the box, with a 14-core figure
 * that describes the box rather than a market in cores — its core-hour is an
 * arithmetic artefact, not an observation, and letting it into the CPU search
 * would price a Linux workstation off a Mac rental. TPU rows are excluded by
 * kind rather than by name: a chip-hour is its own market with its own
 * classes, and neither the GPU floors nor the CPU boundary describe one. */
const DERIVATION_EXCLUDED: ReadonlySet<string> = new Set(["APPLE-SILICON-CI"]);

/** MiB in the GB a seed row states. The ladder measures in MiB against a
 * driver reading and the seed states nominal whole GB, so the two meet at
 * 1024 — and every seed row lands on the same side of every floor either
 * way, because the floors were chosen with the gap between nominal and
 * reported already in them (16 GB nominal is 16384 here and 15360 from a T4;
 * both clear 15_000). */
const MIB_PER_GB = 1024;

/** What a derived row knows about itself. Nothing here is our book: the
 * price is a vendor price, the series is a vendor series, and `note` is the
 * sentence that has to travel with both. */
export interface ClassDerivation {
  /** The capability class being priced — `gpu-24gb`, not the donor's SKU. */
  klass: string;
  /** The seed row the number came from, in full, so a caller can name it. */
  donor: ReferenceClass;
  priceUsdHr: number;
  /** The same price as millicredits, under 1 ZC = 1 USD. */
  priceMzc: number;
  /** Donor price → class price. Exactly 1 for every GPU class, which is what
   * makes their series the donor's own numbers rather than a rescaling of
   * them; only the CPU classes, which are priced per core, move it. */
  scale: number;
  /** Why this donor, naming it. The derived chip's title and the derived
   * chart's caption are both this sentence. */
  note: string;
  /** The donor's own 30 daily points at this class's scale, oldest → newest.
   * Never generated, never interpolated, never extended past what the donor
   * actually carries. */
  history: SeriesPoint[];
}

/** The cheapest seed GPU that clears a floor, or null when none does.
 *
 * Ties go to the seed's own order, so the answer does not depend on a sort
 * that two engines could disagree about. */
function cheapestGpuAtLeast(floorMb: number): ReferenceClass | null {
  let best: ReferenceClass | null = null;
  for (const entry of SEED.classes) {
    if (entry.kind !== "gpu") continue;
    if (DERIVATION_EXCLUDED.has(entry.klass)) continue;
    if (entry.vramGb === undefined) continue;
    if (entry.vramGb * MIB_PER_GB < floorMb) continue;
    if (best === null || entry.refPriceUsdHr < best.refPriceUsdHr) best = entry;
  }
  return best;
}

/** The cheapest core-hour the seed's whole-instance CPU rows imply.
 *
 * Cheapest per CORE rather than cheapest instance, because the instances are
 * not the same size: CPU-128 at $5.17 is dearer than CPU-64 at $3.10 and is
 * also the better buy per core, and a class priced by cores has to compare
 * them that way. Same rule as the GPU search — the cheapest supply that
 * satisfies the class is what the class is worth. */
function cheapestCoreHourUsd(): { donor: ReferenceClass; perCore: number } | null {
  let best: { donor: ReferenceClass; perCore: number } | null = null;
  for (const entry of SEED.classes) {
    if (entry.kind !== "cpu") continue;
    if (DERIVATION_EXCLUDED.has(entry.klass)) continue;
    if (entry.cores === undefined || entry.cores <= 0) continue;
    const perCore = entry.refPriceUsdHr / entry.cores;
    if (best === null || perCore < best.perCore) best = { donor: entry, perCore };
  }
  return best;
}

/** The donor's daily points at a class's scale, in millicredits.
 *
 * At `scale === 1` this is `referenceSeries` to the digit, which is what
 * lets a GPU derivation claim the donor's own history rather than a copy of
 * it that rounds differently. */
function scaledSeries(donor: ReferenceClass, scale: number): SeriesPoint[] {
  return donor.history.map((point) => ({
    at: point.date,
    valueMzc: Math.round(point.priceUsdHr * scale * 1000),
  }));
}

/** A derived price for a coordinator capability class, or null when nothing
 * in the seed describes one.
 *
 * ALIAS FIRST, ALWAYS. The two definitional classes name a card outright, and
 * that answer outranks any search: `gpu-80gb-hopper` searched by VRAM alone
 * would find the A100, because Hopper is a compute capability and the seed
 * states memory. (`gpu-80gb` is the case where the two paths agree, and
 * `reference.test.ts` holds them to it.)
 *
 * Then the GPU floors, then the CPU boundary, then null. Null is an answer:
 * a rung this console cannot price says "no data yet" rather than borrowing
 * the nearest number it can reach. */
export function classDerivation(klass: string): ClassDerivation | null {
  const aliased = CAPABILITY_CLASS_ALIAS[klass];
  if (aliased) {
    const donor = BY_KLASS.get(aliased);
    if (donor) {
      return derivation(
        klass,
        donor,
        donor.refPriceUsdHr,
        `this class names one card: ${donor.displayName}`
      );
    }
  }

  const floor = GPU_CLASS_FLOORS_MB.find(([, name]) => name === klass);
  if (floor) {
    const donor = cheapestGpuAtLeast(floor[0]);
    if (donor) {
      // The floor in the class's own words — the name states whole GB and
      // that is what a reader is looking at on the row.
      const statedGb = /^gpu-(\d+)gb/.exec(klass)?.[1] ?? String(floor[0]);
      return derivation(
        klass,
        donor,
        donor.refPriceUsdHr,
        `cheapest card meeting the ${statedGb} GB floor: ${donor.displayName}`
      );
    }
  }

  const cores = CPU_TYPICAL_CORES[klass];
  if (cores !== undefined) {
    const cheapest = cheapestCoreHourUsd();
    if (cheapest) {
      // To the cent. A whole-instance price divided by its cores and
      // multiplied by a STATED core count has no third decimal in it, and
      // printing one would dress a decision up as a measurement.
      const priceUsdHr = Math.round(cheapest.perCore * cores * 100) / 100;
      return derivation(
        klass,
        cheapest.donor,
        priceUsdHr,
        `${cores} stated typical cores at the cheapest seed core-hour: ${cheapest.donor.displayName}`
      );
    }
  }

  return null;
}

function derivation(
  klass: string,
  donor: ReferenceClass,
  priceUsdHr: number,
  note: string
): ClassDerivation {
  const scale = donor.refPriceUsdHr > 0 ? priceUsdHr / donor.refPriceUsdHr : 1;
  return {
    klass,
    donor,
    priceUsdHr,
    priceMzc: Math.round(priceUsdHr * 1000),
    scale,
    note,
    history: scaledSeries(donor, scale),
  };
}
