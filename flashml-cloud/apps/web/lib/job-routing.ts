/** What the resource-allocation router decided about one job, and what the
 * console is allowed to say about it.
 *
 * WHY THIS MODULE EXISTS. `POST /v1alpha1/jobs/preview-plans` has been built
 * and tested since 2026-08-11 and the console called it zero times, so the
 * product's central claim — *work routes to a venue by fit, and only then is
 * the cheapest machine inside that fit chosen* — was provable only with
 * `curl`. This turns one read of that route into the whole vocabulary of the
 * Routing panel, in a file a test can reach (`vitest.config.ts` collects only
 * `**\/*.test.ts`, so a `.tsx` component gets no coverage — the same split
 * `lib/job-artifacts.ts` and `lib/task-checkpoints.ts` document).
 *
 * THE DISTINCTION THIS MODULE EXISTS TO PROTECT. A venue can be missing from
 * a plan for two completely different reasons, and merging them is the one
 * mistake that would make this panel dishonest:
 *
 *   `suited: false`      the venue PHYSICALLY CANNOT run this work
 *   `acquirable: false`  we CANNOT GET CAPACITY there yet
 *
 * On a GPU fine-tune those are both true of different venues at the same
 * time: `fc-sandbox` comes back unsuited (2 vCPU, 2 GB, no GPU) while
 * `fc-gpu` comes back not-acquirable (a real fit with no integration behind
 * it). The first is a fact about hardware; the second is a roadmap item.
 * Collapsed into one "unavailable" they would read as *we chose not to use
 * these*, which claims a capability the product does not have. They arrive
 * here as two booleans and leave as two different verdicts with two
 * different headlines, and there is deliberately no code path that produces
 * one from the other.
 *
 * NOTHING HERE IS INVENTED. Every number, venue and sentence on the panel is
 * a value the route returned. The route's own contract is that anything not
 * derivable is `null` — *not observed*, never 0 — so a null arrives here as
 * a null and leaves as `NOT_OBSERVED`, never as a zero and never as a guess.
 * Reasons are carried through verbatim: a paraphrase would be more confident
 * than the evidence behind it.
 *
 * ZC AND USD RETAIN SEPARATE SETTLEMENT FIELDS. The API may expose a normalized
 * comparison value for the scheduler, but this module preserves the source
 * figures and the routing card does not render a combined cash total.
 */

import type {
  PlanPreview,
  PreviewCanary,
  PreviewEstimate,
  PreviewPlan,
  PreviewVenue,
} from "./cloud-api";

/** One attempt at reading the preview, classified rather than thrown — the
 * same shape `ArtifactsRead` and `TaskCheckpointRead` use next door, and for
 * the same reason: the panel renders every outcome, so every outcome has to
 * be a value.
 *
 * `unavailable` carries the API's own words. It is deliberately NOT split by
 * status code: a 409 (this job has no stored spec to plan against), a 502
 * and a dead network all leave the console equally unable to show how this
 * job would route, and pretending to tell them apart would mean guessing. */
export type RoutingRead =
  | { status: "loading" }
  | { status: "previewed"; preview: PlanPreview }
  | { status: "unavailable"; detail: string };

/** Which of three statements a venue is making about this job. Mirrors the
 * route's own ordering (usable, then suited-but-unobtainable, then
 * unsuited), so the panel and the API can never disagree about rank. */
export type VenueVerdict = "usable" | "unreachable" | "unsuited";

/** The fixed copy for each verdict.
 *
 * A headline is a LABEL FOR THE KIND OF ANSWER, never a summary of the
 * venue's `reason` — the reason is rendered verbatim underneath it. The two
 * refusal headlines are written to be unmistakable for one another when read
 * side by side in the same list, because that is exactly how they appear:
 * "Can't run this work" is about the hardware, "No capacity we can reach" is
 * about us. */
export const VERDICT_COPY: Record<
  VenueVerdict,
  { headline: string; meaning: string }
> = {
  usable: {
    headline: "Can run this",
    meaning: "Suited to this work, and we can obtain capacity here.",
  },
  unsuited: {
    headline: "Can't run this work",
    meaning:
      "This venue physically cannot run this job. No price changes that.",
  },
  unreachable: {
    headline: "No capacity we can reach",
    meaning:
      "A real fit for this work. We have no way to obtain capacity here yet — that is a gap on our side, not a verdict on the venue.",
  },
};

/** Said wherever a figure the route reported as `null` would otherwise be
 * rendered. Never "0", never "—" alone: an unobserved duration and a
 * zero-second one are different claims, and only one of them is true. */
export const NOT_OBSERVED = "not observed";

/** Why this page never shows a `balanced` plan.
 *
 * The route emits `balanced` only when it is given a deadline, because with
 * nothing to balance against it would be "cheapest" shown twice. This panel
 * asks about a job that already exists and supplies no deadline, so two
 * plans is the complete and correct answer rather than a missing third. */
export const NO_DEADLINE_NOTE =
  "No deadline was given, so there is no balanced plan to show — with nothing to balance against it would be the cheapest plan twice.";

/** One venue judged against this job's kind of work. */
export interface VenueRow {
  id: string;
  display: string;
  currency: string;
  verdict: VenueVerdict;
  /** `VERDICT_COPY[verdict].headline`, carried here so a component keeps no
   * second copy of the mapping. */
  headline: string;
  meaning: string;
  /** The API's own sentence. Verbatim, always. */
  reason: string;
  /** True only where a venue is BOTH unsuited and unobtainable. The headline
   * stays the hardware one — it is the more fundamental fact and the route
   * ranks it that way — but the second statement is still true and the panel
   * says so rather than dropping it. */
  alsoUnreachable: boolean;
}

/** One amount in one currency. Never combined with another — see the module
 * docblock. */
export interface CurrencyFigure {
  currency: string;
  amount: number;
}

export interface PlanRow {
  /** `cheapest` / `balanced` / `fastest`, as the route named it. */
  name: string;
  recommended: boolean;
  tasksPlaced: number;
  tasksUnplaced: number;
  /** Side by side, in their own currencies, in a container with nowhere to
   * put a total. */
  costs: CurrencyFigure[];
  /** Fixed-rate normalized value for scheduler comparison. The job routing
   * card deliberately does not render it: settlement stays source-specific. */
  totalUsdValue: number;
  /** `null` when the planner reported no makespan. Not observed, not 0. */
  makespanSeconds: number | null;
  deadlineMet: boolean | null;
  achievableDeadlineSeconds: number | null;
  /** The WEAKEST basis behind any machine this plan allocates to, and the
   * smallest n behind them — the route computes both; this module only
   * carries them. */
  basis: string | null;
  n: number | null;
  /** Set when another plan beats this one on both axes. */
  dominatedBy: string | null;
  /** How many machines this plan allocates to. */
  machines: number;
  notes: string[];
}

/** How much of the reachable fleet survived which stage.
 *
 * Only present when the router actually ran: on the `_degraded` answer these
 * counters are all 0 because nothing was computed, and rendering "0 eligible
 * machines" for a fleet nobody looked at is a fabricated measurement. */
export interface FleetCounts {
  eligible: number;
  /** Refused by a placement gate. */
  excluded: number;
  /** Passed the gates and then left out on venue fit. Counted separately
   * from `excluded` by the route so the two reasons never merge. */
  venueExcluded: number;
  /** Eligible and allowed to claim, simply carrying no duration evidence
   * yet, so no arithmetic can include them. */
  unplannable: number;
}

export type RoutingPanelState =
  | "loading"
  | "routed"
  | "unrouted"
  | "unavailable";

export interface RoutingPanel {
  /** `routed` — the router ran and classified the work.
   *  `unrouted` — the API answered, and the router did not run. Its `notes`
   *    say why (routing not configured on this deployment, a spec that
   *    expands to no tasks, an account with no reachable capacity at all).
   *    Distinct from `unavailable` because the answer IS the answer.
   *  `unavailable` — the read itself failed. */
  state: RoutingPanelState;
  /** The API's own words, on `unavailable` only. */
  detail: string | null;
  /** What kind of work this was taken to be, and why — never the enum
   * alone. `null` outside `routed`. */
  kind: string | null;
  kindEvidence: string | null;
  /** How many tasks the spec expands to, as the route counted them. */
  tasks: number | null;
  /** Every venue, including the refused ones. Empty outside `routed`. */
  venues: VenueRow[];
  plans: PlanRow[];
  /** The name of the recommended plan, or `null` when nothing is quoted. */
  recommended: string | null;
  duration: PreviewEstimate | null;
  canary: PreviewCanary | null;
  fleet: FleetCounts | null;
  /** The route's own notes, verbatim and in order. */
  notes: string[];
  /** The router ran and still quoted no plan. Not an error — the notes say
   * which of the several honest reasons it is. */
  quotedNothing: boolean;
}

const EMPTY: RoutingPanel = {
  state: "loading",
  detail: null,
  kind: null,
  kindEvidence: null,
  tasks: null,
  venues: [],
  plans: [],
  recommended: null,
  duration: null,
  canary: null,
  fleet: null,
  notes: [],
  quotedNothing: false,
};

/** Which statement this venue is making.
 *
 * `suited` is checked FIRST and wins, which is the same order the route
 * ranks its buckets in (usable, suited-but-unobtainable, unsuited): a venue
 * that cannot do the work is not made reachable by being obtainable, and
 * saying "no capacity we can reach" about a box that could never run the job
 * would blame the wrong thing. */
function verdictFor(venue: PreviewVenue): VenueVerdict {
  if (!venue.suited) return "unsuited";
  if (!venue.acquirable) return "unreachable";
  return "usable";
}

function venueRow(venue: PreviewVenue): VenueRow {
  const verdict = verdictFor(venue);
  return {
    id: venue.id,
    display: venue.display,
    currency: venue.currency,
    verdict,
    headline: VERDICT_COPY[verdict].headline,
    meaning: VERDICT_COPY[verdict].meaning,
    reason: venue.reason,
    alsoUnreachable: verdict === "unsuited" && !venue.acquirable,
  };
}

/** A plan's cost as a list of per-currency figures.
 *
 * BOTH currencies are always present, including a zero: a plan that spends
 * 0 USD spent zero dollars, which is a measurement and not a gap — workspace
 * machines are free to their own members, and a zero ask is legal and
 * labelled donated. Dropping the zero would read as "we do not know", which
 * is the opposite of what it means.
 *
 * A list rather than an object with named fields because a list is the shape
 * with nowhere to put a total. */
function costsOf(plan: PreviewPlan): CurrencyFigure[] {
  return [
    { currency: "ZC", amount: plan.cost.zc },
    { currency: "USD", amount: plan.cost.usd },
  ];
}

function planRow(plan: PreviewPlan): PlanRow {
  return {
    name: plan.name,
    recommended: plan.recommended,
    tasksPlaced: plan.tasks_placed,
    tasksUnplaced: plan.tasks_unplaced,
    costs: costsOf(plan),
    totalUsdValue: plan.cost.total_usd_value,
    makespanSeconds: plan.makespan_seconds,
    deadlineMet: plan.deadline_met,
    achievableDeadlineSeconds: plan.achievable_deadline_seconds,
    basis: plan.basis,
    n: plan.n,
    dominatedBy: plan.dominated_by,
    machines: plan.allocations.length,
    notes: plan.notes,
  };
}

/** How strong the evidence behind a figure is, said in one phrase.
 *
 * `null` basis is `NOT_OBSERVED` and never "measured, n=0": a basis nobody
 * could establish and a measurement of zero samples are different claims,
 * and only the first one is ever true here. An n of 0 is treated the same
 * way for the same reason — the route's contract is that a derivable figure
 * always carries a real n. */
export function basisLabel(basis: string | null, n: number | null): string {
  if (!basis) return NOT_OBSERVED;
  if (n == null || n <= 0) return basis;
  return `${basis}, n=${n}`;
}

/** One read of `preview-plans` turned into everything the panel may say.
 *
 * Pure: no clock, no network, no randomness. The same read always produces
 * the same panel. */
export function summariseRouting(read: RoutingRead): RoutingPanel {
  if (read.status === "loading") return EMPTY;

  if (read.status === "unavailable") {
    return { ...EMPTY, state: "unavailable", detail: read.detail };
  }

  const p = read.preview;
  const notes = p.notes ?? [];

  // `kind` is the signal that the router ran at all. The route's `_degraded`
  // answer omits `kind`, `kind_evidence`, `venues` and
  // `venue_excluded_machines` entirely, and its remaining counters are 0
  // because nothing was computed rather than because nothing qualified. So
  // this branch drops the fleet numbers rather than reporting zeros the
  // route never measured, and leaves the notes to say what happened.
  if (p.kind == null) {
    return {
      ...EMPTY,
      state: "unrouted",
      tasks: p.tasks ?? null,
      notes,
      quotedNothing: true,
    };
  }

  const plans = (p.plans ?? []).map(planRow);

  return {
    state: "routed",
    detail: null,
    kind: p.kind,
    kindEvidence: p.kind_evidence ?? null,
    tasks: p.tasks ?? null,
    venues: (p.venues ?? []).map(venueRow),
    plans,
    recommended: p.recommended ?? null,
    duration: p.duration ?? null,
    canary: p.canary ?? null,
    fleet: {
      eligible: p.eligible_machines,
      excluded: p.excluded_machines,
      venueExcluded: p.venue_excluded_machines ?? 0,
      unplannable: p.unplannable_machines,
    },
    notes,
    quotedNothing: plans.length === 0,
  };
}
