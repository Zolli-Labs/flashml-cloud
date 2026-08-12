"""The compute router: which machines should be eligible, and what it costs.

"OpenRouter for compute." A submitter has a job of independent tasks (an HPO
sweep, an eval shard set) and several venues to run it in — their workspace,
the open market, a rented pod, an Alibaba FC sandbox. This package answers
three questions and refuses a fourth.

**It answers: what kind of work is this (``workload``), which venues suit
that kind (``venues``), how long will one task take (``estimator``), and how
should the fleet be split (``plan``).**

The first two are what make this a resource-allocation layer rather than a
price comparison. Cost and ETA cannot see the difference between forty
six-second trials and one eight-hour fine-tune, and routing by **fit** — HPO
to anything with a CPU, training to real hardware, wait-heavy scoring to a
venue that hibernates at near-zero cost — is the product thesis. Fit decides
the eligible SET; cost and ETA choose WITHIN it. They are never averaged: a
cheap venue that cannot run the workload is not a cheap option, it is not an
option.

**It refuses to answer: which machine runs which task.** ``flashruntime``'s
scheduler states the constraint outright at ``scheduler/__init__.py:628`` —

    **FlashML is a PULL system.** Nodes claim; the coordinator never picks a
    node for a task. By the time a policy runs, the machine has already
    asked, and the only question left is *which of the pending tasks it
    gets, or none*.

so a task→machine binding is unenforceable by construction: nothing in the
runtime can make a host run a particular task, and a router that emitted one
would be describing a system that does not exist. What this package produces
is an **eligible fleet and its arithmetic** — which machines may be in the
candidate set, what each would cost, how long the whole job would take. The
existing pull loop distributes work inside that set, untouched.

Three properties hold everywhere below, and each has a test.

**1. Gates before price, always.** ``plan.eligible_fleet`` filters through the
placement predicate BEFORE any price is read, and the predicate is injected —
this package contains no eligibility rule of its own to drift from the
runtime's. Price never enters eligibility, so the marketplace can be wrong
about money without ever being wrong about safety.

**2. Reliability ranks, it never excludes.** The same scheduler argues this at
length (``scheduler/__init__.py:637-653``): refusing work to flaky hosts
starves small pools, and *"a volunteer network is made of unreliable machines
by construction; surviving them is the product."* A host's record orders
equally-priced candidates and does nothing else. It never removes one, and it
never divides a price.

**3. Cold start is the steady state.** There are roughly 29 credited tasks in
the whole ledger and the coordinator's in-memory reliability record is
cleared by every deploy, so most hosts are ``unproven`` most of the time. A
router that needed history would have no value in the deployment that exists.
So the estimator's answer to a novel workload is ``None`` — *not observed* —
and the answer to ``None`` is ``plan.Canary``: run one task, measure it,
re-plan the other thirty-nine from a number instead of a guess.

Nothing here calls a model, a database, or a clock. It is arithmetic over
rows a query supplied, in the shape ``contributions.py``, ``metrics.py`` and
``verify.py`` already use — the policy has to be readable and testable
without a database, or it ends up decided inside a SQL string.
"""
from __future__ import annotations

from flashml_cloud_api.router.estimator import (
    BASIS_ESTIMATED,
    BASIS_MEASURED,
    BASIS_PROJECTED,
    CLASS_LADDER,
    MIN_EVIDENCE,
    Estimate,
    Evidence,
    Observation,
    Prior,
    RUNG_CLASS_SHAPE,
    RUNG_LADDER,
    RUNG_SAME_JOB,
    RUNG_USER_SHAPE,
    TIER_MIXED,
    TIER_SHAKY,
    TIER_STEADY,
    TIER_UNPROVEN,
    estimate_task_seconds,
    from_canary,
    hardware_class,
    planning_seconds,
    project_across_class,
    reliability_tier,
    select_acceptance,
)
from flashml_cloud_api.router.plan import (
    CURRENCY_USD,
    CURRENCY_ZC,
    DEFAULT_VENUE_ORDER,
    PLAN_BALANCED,
    PLAN_CHEAPEST,
    PLAN_FASTEST,
    VENUE_MARKET,
    VENUE_RENTED,
    VENUE_WORKSPACE,
    Allocation,
    Canary,
    Candidate,
    Cost,
    EligibilityPredicate,
    Plan,
    PlanRequest,
    PlanSet,
    balanced_plan,
    canary_for,
    candidate_venue_id,
    cheapest_plan,
    eligible_fleet,
    fastest_plan,
    frontier,
    plan_job,
    venue_admitted,
)
from flashml_cloud_api.router.venues import (
    ACQUISITION_AUTOMATIC,
    ACQUISITION_MANUAL,
    ACQUISITION_NONE,
    VENUE_FC_GPU,
    VENUE_FC_SANDBOX,
    VENUE_OWNED,
    VENUE_RUNPOD,
    VENUES,
    Venue,
    VenueFit,
    usable_venue_ids,
    venue_for_listing,
    venues_for,
)
from flashml_cloud_api.router.workload import (
    Signals,
    WorkloadKind,
    classify,
    signals_for,
    signals_from_config,
    signals_from_job_spec,
)

__all__ = [
    # estimator
    "BASIS_ESTIMATED",
    "BASIS_MEASURED",
    "BASIS_PROJECTED",
    "CLASS_LADDER",
    "MIN_EVIDENCE",
    "Estimate",
    "Evidence",
    "Observation",
    "Prior",
    "RUNG_CLASS_SHAPE",
    "RUNG_LADDER",
    "RUNG_SAME_JOB",
    "RUNG_USER_SHAPE",
    "TIER_MIXED",
    "TIER_SHAKY",
    "TIER_STEADY",
    "TIER_UNPROVEN",
    "estimate_task_seconds",
    "from_canary",
    "hardware_class",
    "planning_seconds",
    "project_across_class",
    "reliability_tier",
    "select_acceptance",
    # plan
    "CURRENCY_USD",
    "CURRENCY_ZC",
    "DEFAULT_VENUE_ORDER",
    "PLAN_BALANCED",
    "PLAN_CHEAPEST",
    "PLAN_FASTEST",
    "VENUE_MARKET",
    "VENUE_RENTED",
    "VENUE_WORKSPACE",
    "Allocation",
    "Canary",
    "Candidate",
    "Cost",
    "EligibilityPredicate",
    "Plan",
    "PlanRequest",
    "PlanSet",
    "balanced_plan",
    "canary_for",
    "candidate_venue_id",
    "cheapest_plan",
    "eligible_fleet",
    "fastest_plan",
    "frontier",
    "plan_job",
    "venue_admitted",
    # workload
    "Signals",
    "WorkloadKind",
    "classify",
    "signals_for",
    "signals_from_config",
    "signals_from_job_spec",
    # venues
    "ACQUISITION_AUTOMATIC",
    "ACQUISITION_MANUAL",
    "ACQUISITION_NONE",
    "VENUES",
    "VENUE_FC_GPU",
    "VENUE_FC_SANDBOX",
    "VENUE_OWNED",
    "VENUE_RUNPOD",
    "Venue",
    "VenueFit",
    "usable_venue_ids",
    "venue_for_listing",
    "venues_for",
]
