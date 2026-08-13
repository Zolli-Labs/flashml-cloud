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
 * place to record which card is most common. */
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

/** The seed's daily history as millicredit points, oldest → newest — the
 * order `PriceHistory` draws in. Same rounding as `referenceZcPerHour`. */
export function referenceSeries(
  entry: ReferenceClass
): { at: string; valueMzc: number }[] {
  return entry.history.map((point) => ({
    at: point.date,
    valueMzc: Math.round(point.priceUsdHr * 1000),
  }));
}
