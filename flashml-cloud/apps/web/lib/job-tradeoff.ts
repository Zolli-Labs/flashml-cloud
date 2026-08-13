/** What each additional rented machine buys this job — and when it buys
 * nothing.
 *
 * WHY THIS MODULE EXISTS. `router/tradeoff.py` computes the buyer's curve and
 * has since it was reviewed; it had no API route and nothing in the console
 * consumed it, so the product's central promise — *here is what one more GPU
 * gets you, and here is where it gets you nothing* — was arithmetic nobody
 * could see. This turns one read of `GET /v1alpha1/jobs/{id}/tradeoff` into
 * everything the panel may say, in a file a test can reach (`vitest.config.ts`
 * collects only `lib/**\/*.test.ts`, so a `.tsx` component gets no coverage —
 * the same split `lib/job-routing.ts` documents).
 *
 * THE RULE THIS MODULE EXISTS TO PROTECT. **`no_marginal_gain` must never
 * render as an upsell.** It marks a fleet that costs more than the one before
 * it and finishes no sooner, because `ceil(task_count / slots)` did not step
 * down. A curve that always slopes downward is a sales tool rather than a
 * planner, and it would spend somebody's money on a machine that cannot help
 * them. So the five verdicts carry three TONES and only one of them is a
 * gain; there is deliberately no code path that turns a refusal into a
 * recommendation.
 *
 * The other two refusals are just as load-bearing and just as separate:
 *
 *   `baseline`           the buyer's own hardware, at zero cost. It has no
 *                        predecessor to improve on, so it is neutral — never
 *                        a "this helps" badge for a machine nobody bought.
 *   `no_parallelism`     one task. No fleet is faster at any price. This is
 *                        advice, stated plainly, and not a disabled button.
 *   `beyond_task_count`  more machines than there is work for them.
 *
 * NOTHING HERE IS INVENTED. Every number, name and sentence is a value the
 * route returned, and the route's contract is that anything not derivable is
 * `null` — *not observed*, never 0 — so a null arrives here as a null and
 * leaves as `NOT_OBSERVED`. The one derived figure is `savedSeconds`, which
 * is the difference between two finish times the route reported and is
 * omitted the moment either of them is null.
 *
 * BOTH UNITS TRAVEL. Capacity this account already reaches settles in ZC and
 * a rented machine settles in USD. 1 ZC = $1 USD is settled policy, so the
 * route supplies the sum as well — and this module carries all three rather
 * than reducing them, so the panel can show what each side actually pays.
 */

import type {
  JobTradeoff,
  PreviewEstimate,
  TradeoffPoint,
  TradeoffPrice,
  TradeoffRenting,
} from "./cloud-api";

/** One attempt at reading the curve, classified rather than thrown — the same
 * shape `RoutingRead` and `ArtifactsRead` use next door, and for the same
 * reason: the panel renders every outcome, so every outcome has to be a
 * value.
 *
 * `unreadable` carries the API's own words and is deliberately NOT split by
 * status code: a 409 (this job has no stored spec to plan against), a 502 and
 * a dead network all leave the console equally unable to show the curve, and
 * pretending to tell them apart would mean guessing. */
export type TradeoffRead =
  | { status: "loading" }
  | { status: "read"; tradeoff: JobTradeoff }
  | { status: "unreadable"; detail: string };

/** Said wherever a figure the route reported as `null` would otherwise be
 * rendered. Never "0": an unobserved finish time and an instantaneous one are
 * different claims, and only one of them is ever true here. */
export const NOT_OBSERVED = "not observed";

/** The five verdicts, exactly as `router/tradeoff.py` names them. */
export type AdviceCode =
  | "baseline"
  | "helps"
  | "no_marginal_gain"
  | "beyond_task_count"
  | "no_parallelism";

/** How a row reads. Three tones for five codes, and only ONE of them is a
 * gain — see the module docblock. `neutral` is for a row that is not making
 * a claim about a purchase at all. */
export type AdviceTone = "neutral" | "gain" | "no-gain";

export interface AdviceCopy {
  headline: string;
  meaning: string;
  tone: AdviceTone;
}

/** The fixed copy for each verdict.
 *
 * A headline is a LABEL FOR THE KIND OF ANSWER. The two that refuse are
 * written to be unmistakable for one another and for the one that does not,
 * because that is exactly how they appear: side by side in one table. */
export const ADVICE_COPY: Record<AdviceCode, AdviceCopy> = {
  baseline: {
    headline: "What you already have",
    meaning:
      "The machines this account already reaches, at no cost. Nothing was rented here, so there is nothing yet to have improved on.",
    tone: "neutral",
  },
  helps: {
    headline: "Finishes sooner",
    meaning: "This fleet finishes sooner than the one above it.",
    tone: "gain",
  },
  // "Compared with the row directly above" is not decoration. Cost is
  // rate x machines x the wall-clock the fleet is HELD, so a dominated row can
  // cost more in USD than a better row further down: 0.24, 0.32, 0.20 is the
  // correct column for a curve whose finish time steps down at the third of
  // those. Every verdict here is pairwise against its predecessor and nothing
  // else on the page says so, which leaves a reader scanning a falling price
  // column to conclude the table is broken — and to discount the two refusals,
  // which are the whole point of the panel.
  no_marginal_gain: {
    headline: "Costs more, finishes no sooner",
    meaning:
      "Compared with the row directly above: one more machine, and the same finish time. The work does not divide any further at this fleet size, so this machine is paid for and idle.",
    tone: "no-gain",
  },
  beyond_task_count: {
    headline: "More machines than tasks",
    meaning:
      "This fleet is larger than the number of tasks this job has. Every machine past the task count is bought and unused.",
    tone: "no-gain",
  },
  no_parallelism: {
    headline: "No fleet is faster",
    meaning:
      "This job is one task. A task cannot be split across machines, so no fleet finishes it sooner — at any price.",
    tone: "no-gain",
  },
};

/** What a verdict this console has never heard of is allowed to say.
 *
 * Not "helps", and not a guess: an API that grows a sixth code must reach a
 * reader as an answer nobody characterised, because the alternative is a
 * badge that claims something the route did not. */
export const UNKNOWN_ADVICE: AdviceCopy = {
  headline: "Not classified",
  meaning:
    "This API returned a verdict this console does not know how to read, so nothing is claimed about it.",
  tone: "neutral",
};

const KNOWN: ReadonlySet<string> = new Set(Object.keys(ADVICE_COPY));

/** The copy for a verdict, or the conservative fallback. */
export function adviceCopy(code: string): AdviceCopy {
  return KNOWN.has(code) ? ADVICE_COPY[code as AdviceCode] : UNKNOWN_ADVICE;
}

/** One fleet size, as the panel renders it. */
export interface TradeoffRow {
  /** Total concurrency slots at this fleet size. */
  totalSlots: number;
  ownedSlots: number;
  rentedSlots: number;
  /** `null` when no per-task duration has been observed. Not 0. */
  finishSeconds: number | null;
  /** Seconds this row finishes ahead of the row above it, when both were
   * observed. `null` otherwise, and never negative-as-zero: a row that
   * finishes no sooner reports 0 saved, which is the honest number. */
  savedSeconds: number | null;
  /** What the machines this account already reaches settle, in ZC. */
  zcCost: number;
  /** What the rented machines settle, in USD. `null` when unpriced. */
  usdCost: number | null;
  /** Their sum at the settled 1 ZC = $1 rate, or `null`. */
  totalUsdValue: number | null;
  /** The route's own verdict string, verbatim. */
  adviceCode: string;
  headline: string;
  meaning: string;
  tone: AdviceTone;
}

/** Which of three statements the route is making about renting. */
export type RentingVerdict = "can-help" | "not-for-this-job" | "unpriced";

/** The fixed copy for each verdict.
 *
 * `not-for-this-job` and `unpriced` are DIFFERENT REFUSALS and are written to
 * be unmistakable: the first is about who is allowed to run this job's code,
 * the second is about whether we hold a price. Collapsing them would send
 * somebody off to create a workspace when the real gap was a missing quote. */
export const RENTING_COPY: Record<RentingVerdict, string> = {
  "can-help": "Renting can add machines to this job",
  "not-for-this-job": "Renting cannot help this job",
  unpriced: "No rented machine is priced for this work",
};

export interface RentingPanel {
  verdict: RentingVerdict;
  headline: string;
  suited: boolean;
  acquirable: boolean;
  /** The API's own sentence for the `suited` verdict. Verbatim. */
  reason: string;
  /** The API's own sentence for the price. Verbatim. */
  priceReason: string;
  /** How many rented machines the curve swept, and why it stopped there. */
  slots: number;
  slotsReason: string;
  venueId: string | null;
  usdPerHour: number | null;
  price: TradeoffPrice | null;
}

export interface OwnedPanel {
  machines: number;
  slots: number;
  reachableMachines: number;
  askZcPerHour: number;
}

export type TradeoffPanelState =
  | "loading"
  | "curve"
  | "empty"
  | "unreadable";

export interface TradeoffPanel {
  /** `curve` — the route swept at least one fleet size.
   *  `empty` — the route answered and there was nothing to sweep. Its notes
   *    say which of the honest reasons it is (routing not configured here, a
   *    spec that expands to no tasks, an account that reaches no capacity and
   *    may not rent). Distinct from `unreadable` because the answer IS the
   *    answer.
   *  `unreadable` — the read itself failed. */
  state: TradeoffPanelState;
  /** The API's own words, on `unreadable` only. */
  detail: string | null;
  tasks: number | null;
  /** The per-task duration the whole curve rests on, or `null`. */
  taskSeconds: number | null;
  duration: PreviewEstimate | null;
  owned: OwnedPanel | null;
  renting: RentingPanel | null;
  rows: TradeoffRow[];
  /** The largest SWEPT fleet that finishes sooner than the one before it.
   * `null` when no swept row gains at all.
   *
   * This is a fact about the rows that arrived and nothing more. Whether it
   * may be turned into "nothing past here helps" depends on `sweepComplete` —
   * see it. */
  lastGain: TradeoffRow | null;
  /** True when the sweep ran to the end of the range where renting could
   * still matter, rather than being cut short.
   *
   * WHY THE PANEL CANNOT SKIP THIS. The route bounds its sweep three ways
   * (`_tradeoff_rented_slots`), and two of them are limits on the ANSWER
   * rather than on the world: `_TRADEOFF_MAX_RENTED_STEPS` caps it at 16
   * rented machines, and this deployment's rolling USD ceiling caps it at what
   * it could pay for. Both say so in `renting.slotsReason` — "this answer's
   * size limit and not a fleet size where renting stops helping" — and a
   * truncated curve therefore ends on a run of `no_marginal_gain` rows that
   * mean *the answer ran out*, not *renting ran out*. Reading a ceiling off
   * that tail states the OPPOSITE of the truth, and it does so worst on an
   * HPO sweep of 17+ trials, which is the workload this product is pitched on.
   *
   * The test is the LAST ROW'S OWN VERDICT, and it needs no arithmetic of its
   * own: the route sweeps `task_count + 1 - owned_slots` machines, so a sweep
   * nothing truncated always ends past the task count (`beyond_task_count`) or
   * on the single task no fleet divides (`no_parallelism`). Anything else at
   * the end is a curve that stopped early. */
  sweepComplete: boolean;
  /** True when the route swept fleet sizes it could price, ran the sweep to
   * the end, and none of them helps. The panel says so in words rather than
   * showing a flat table and letting a reader conclude the page is broken.
   *
   * All three conditions are load-bearing, and two of them are guards against
   * asserting a trade-off nobody computed:
   *
   *   at least one RENTED row  a sweep cut to zero rented machines for a
   *                            non-arithmetic reason — a rate above
   *                            `rented_usd_per_acquisition_max`, say — leaves
   *                            a baseline-only curve. It never priced a
   *                            machine; it refused to.
   *   `sweepComplete`          a truncated sweep can be flat all the way
   *                            across (20 owned slots against 40 tasks: every
   *                            one of the 16 swept machines lands on the same
   *                            `ceil(40 / slots) = 2` step) while the 21st
   *                            machine, one past the cap, halves the job. */
  nothingHelps: boolean;
  /** The route's own notes, verbatim and in order. */
  notes: string[];
}

const EMPTY: TradeoffPanel = {
  state: "loading",
  detail: null,
  tasks: null,
  taskSeconds: null,
  duration: null,
  owned: null,
  renting: null,
  rows: [],
  lastGain: null,
  sweepComplete: false,
  nothingHelps: false,
  notes: [],
};

/** The two verdicts a sweep NOTHING TRUNCATED ends on. See
 * `TradeoffPanel.sweepComplete`. */
const SWEPT_OUT: ReadonlySet<string> = new Set([
  "beyond_task_count",
  "no_parallelism",
]);

/** Which statement the route is making about renting.
 *
 * `suited` is checked FIRST and wins. A job a rented machine may not run is
 * not made rentable by a price existing, and "no rented machine is priced"
 * about a job that could never use one would blame the wrong thing — the same
 * ordering, and the same argument, as `verdictFor` in `lib/job-routing.ts`. */
function rentingVerdict(renting: TradeoffRenting): RentingVerdict {
  if (!renting.suited) return "not-for-this-job";
  if (!renting.acquirable) return "unpriced";
  return "can-help";
}

function rentingPanel(renting: TradeoffRenting): RentingPanel {
  const verdict = rentingVerdict(renting);
  return {
    verdict,
    headline: RENTING_COPY[verdict],
    suited: renting.suited,
    acquirable: renting.acquirable,
    reason: renting.reason,
    priceReason: renting.price_reason,
    slots: renting.slots,
    slotsReason: renting.slots_reason,
    venueId: renting.venue_id,
    usdPerHour: renting.usd_per_hour,
    price: renting.price ?? null,
  };
}

function row(point: TradeoffPoint, previous: TradeoffPoint | undefined): TradeoffRow {
  const copy = adviceCopy(point.advice_code);
  const saved =
    previous == null ||
    previous.finish_seconds == null ||
    point.finish_seconds == null
      ? null
      : previous.finish_seconds - point.finish_seconds;
  return {
    totalSlots: point.total_slots,
    ownedSlots: point.owned_slots,
    rentedSlots: point.rented_slots,
    finishSeconds: point.finish_seconds,
    savedSeconds: saved,
    zcCost: point.zc_cost,
    usdCost: point.usd_cost,
    totalUsdValue: point.total_usd_value,
    adviceCode: point.advice_code,
    headline: copy.headline,
    meaning: copy.meaning,
    tone: copy.tone,
  };
}

/** How strong the evidence behind the seconds is, said in one phrase.
 *
 * A `null` basis is `NOT_OBSERVED` and never "measured, n=0": a basis nobody
 * could establish and a measurement of zero samples are different claims, and
 * only the first is ever true here. */
export function basisLabel(basis: string | null, n: number | null): string {
  if (!basis) return NOT_OBSERVED;
  if (n == null || n <= 0) return basis;
  return `${basis}, n=${n}`;
}

/** One read of the tradeoff route turned into everything the panel may say.
 *
 * Pure: no clock, no network, no randomness. The same read always produces
 * the same panel. */
export function summariseTradeoff(read: TradeoffRead): TradeoffPanel {
  if (read.status === "loading") return EMPTY;

  if (read.status === "unreadable") {
    return { ...EMPTY, state: "unreadable", detail: read.detail };
  }

  const t = read.tradeoff;
  const notes = t.notes ?? [];
  const points = t.points ?? [];
  const rows = points.map((point, index) => row(point, points[index - 1]));
  // The LAST gaining row, not the best-value one: everything after it costs
  // more and finishes no sooner, which is the stop sign. Searched from the
  // end so a curve that steps down again after a flat step still ends in the
  // right place.
  let lastGain: TradeoffRow | null = null;
  for (let i = rows.length - 1; i >= 0; i -= 1) {
    if (rows[i].tone === "gain") {
      lastGain = rows[i];
      break;
    }
  }

  // Whether the sweep ran out of useful fleet sizes or merely ran out of
  // room. The last row's own verdict answers it — see `sweepComplete`.
  const last = rows.length === 0 ? null : rows[rows.length - 1];
  const sweepComplete = last != null && SWEPT_OUT.has(last.adviceCode);

  return {
    state: rows.length === 0 ? "empty" : "curve",
    detail: null,
    tasks: t.tasks ?? null,
    taskSeconds: t.task_seconds ?? null,
    duration: t.duration ?? null,
    owned:
      t.owned == null
        ? null
        : {
            machines: t.owned.machines,
            slots: t.owned.slots,
            reachableMachines: t.owned.reachable_machines,
            askZcPerHour: t.owned.ask_zc_per_hour,
          },
    renting: t.renting == null ? null : rentingPanel(t.renting),
    rows,
    lastGain,
    sweepComplete,
    // Only meaningful once RENTED fleet sizes were swept and the sweep
    // finished: a curve with no rented row priced nothing, and a truncated one
    // can be flat across every row it managed to reach. Both are silence, not
    // a verdict. See `nothingHelps`.
    nothingHelps:
      sweepComplete && rows.some((r) => r.rentedSlots > 0) && lastGain === null,
    notes,
  };
}
