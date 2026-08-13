"""Which venues suit which kind of work, and the fact behind every answer.

A venue is a *place capacity comes from*: machines people already run, a
rented pod, an Alibaba FC sandbox, an FC serverless GPU function. It is a
different axis from ``plan.VENUE_WORKSPACE``/``MARKET``/``RENTED``, which say
*whose machine it is and who pays* — a workspace machine and a market listing
are both FlashNode hosts on someone's desk, one free to a team and one priced.
Both axes are kept because both are true, and ``venue_for_listing`` maps the
commercial label onto the physical one.

**Fit is not price.** This module answers "can this venue do this kind of
work", and nothing else. A venue that costs more is still suited; the planner
sorts on price *inside* the fit. Collapsing the two would produce the mistake
the whole layer exists to prevent — a cheap venue that cannot run the workload
is not a cheap option, it is not an option — and it would also produce its
mirror image, a capable venue quietly hidden because it looked expensive. So
``suited=False`` appears here **only** when the venue physically cannot do the
work, never because it would be a poor deal.

**Every fit carries a fact.** The reasons below cite measurements taken on
account ``5055584162230015`` on 2026-08-11 (``.evidence/alibaba-*``, and the
integration spec's §1 table), Alibaba's published FC GPU instance and billing
model, and two absences verified by reading this repo: there is no
``ResourceProvider`` for RunPod anywhere in it, and nothing in it creates an
FC GPU function. ``ecs-gpu`` joined on 2026-08-12, and its number is a live
``DescribePrice`` against that same account rather than a rate card anybody
remembered. An unsourced preference would be a slogan, and a slogan is
what this layer is meant to replace.

**``acquisition`` is load-bearing.** ``automatic`` means this API can bring
capacity into existence; ``manual`` means a human starts it; ``none`` means
the integration does not exist. A venue we cannot acquire from must be
visibly distinct from one we can, or a page implies a capability the product
does not have. ``VenueFit.usable`` is the only thing a plan may rest on, and
it is false for every ``none`` venue no matter how well it fits — which is
the honest, and slightly disappointing, answer for ``fc-gpu``.

Pure data and pure functions. No I/O, deterministic, and every table is a
tuple so the same question always returns the same answer in the same order.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from flashml_cloud_api.router.workload import WorkloadKind

__all__ = [
    "ACQUISITION_AUTOMATIC",
    "ACQUISITION_MANUAL",
    "ACQUISITION_NONE",
    "CURRENCY_USD",
    "CURRENCY_ZC",
    "VENUES",
    "VENUE_ECS_GPU",
    "VENUE_FC_GPU",
    "VENUE_FC_SANDBOX",
    "VENUE_OWNED",
    "VENUE_RUNPOD",
    "Venue",
    "VenueFit",
    "usable_venue_ids",
    "venue",
    "venue_for_listing",
    "venues_for",
]

VENUE_OWNED = "owned"
VENUE_RUNPOD = "runpod"
VENUE_FC_SANDBOX = "fc-sandbox"
VENUE_FC_GPU = "fc-gpu"
VENUE_ECS_GPU = "ecs-gpu"

CURRENCY_ZC = "ZC"
CURRENCY_USD = "USD"

#: This API can create capacity here without a human in the loop.
ACQUISITION_AUTOMATIC = "automatic"
#: Capacity exists only after a person starts it and points it at us.
ACQUISITION_MANUAL = "manual"
#: There is no integration. Nothing in this repo can obtain capacity here.
ACQUISITION_NONE = "none"


@dataclass(frozen=True)
class Venue:
    """A place capacity comes from, described by facts rather than by hope.

    ``max_vram_gb`` is ``None`` when VRAM is **not a venue-level property**:
    an owned fleet spans ``cpu-small`` to ``gpu-80gb-hopper`` and a rented pod
    is whatever pod a person started, so the ceiling is a per-machine fact the
    placement gate already checks. ``0.0`` is different and is a claim: this
    venue has no GPU at all.

    ``hibernates`` means the venue can idle at near-zero cost without losing
    its prepared state. It is what makes wait-heavy work cheap, and it is the
    single property no ordinary machine has.
    """

    id: str
    display: str
    currency: Literal["ZC", "USD"]
    has_gpu: bool
    max_vram_gb: float | None
    hibernates: bool
    acquisition: Literal["automatic", "manual", "none"]
    notes: str


@dataclass(frozen=True)
class VenueFit:
    """One venue judged against one kind of work.

    Three separate verdicts, deliberately not merged:

    - ``suited`` — can this venue do this work? A hardware fact or a shape
      fact, never a price.
    - ``acquirable`` — can we get capacity here at all today?
    - ``usable`` — both. The only one a plan may act on.

    Keeping them apart is what lets a surface say the true and useful thing
    about ``fc-gpu``: *this is where inference belongs, and we cannot put
    anything there yet*. Merged into one boolean, that sentence becomes either
    a lie or silence.
    """

    venue: Venue
    suited: bool
    acquirable: bool
    reason: str

    @property
    def usable(self) -> bool:
        return self.suited and self.acquirable


# ---------------------------------------------------------------------------
# the venues
# ---------------------------------------------------------------------------

_OWNED = Venue(
    id=VENUE_OWNED,
    display="Your machines",
    currency=CURRENCY_ZC,
    has_gpu=True,
    # Not a venue-level fact: the fleet spans the whole class ladder, and
    # `estimator.hardware_class` reads each machine's own card.
    max_vram_gb=None,
    hibernates=False,
    acquisition=ACQUISITION_AUTOMATIC,
    notes=(
        "FlashNode hosts that are already enrolled and heartbeating, so there "
        "is nothing to acquire — 'automatic' here means they are already "
        "there. Free to fellow workspace members (M1), priced in ZC on the "
        "open market (M2), and a zero ask is legal and labelled donated (M13)."
    ),
)

_RUNPOD = Venue(
    id=VENUE_RUNPOD,
    display="RunPod",
    currency=CURRENCY_USD,
    has_gpu=True,
    # No rate card is readable from this repo, so no ceiling is claimed.
    max_vram_gb=None,
    hibernates=False,
    acquisition=ACQUISITION_MANUAL,
    notes=(
        "Real GPUs priced per pod-hour in USD. This repo contains 0 "
        "ResourceProvider implementations for RunPod — searched 2026-08-11, "
        "not assumed — so capacity appears only when a person starts a pod "
        "running 'flashnode work' and it enrols itself. A held pod bills "
        "whether or not it is claimed."
    ),
)

_FC_SANDBOX = Venue(
    id=VENUE_FC_SANDBOX,
    display="Alibaba FC Agent Sandbox",
    currency=CURRENCY_USD,
    has_gpu=False,
    max_vram_gb=0.0,
    hibernates=True,
    acquisition=ACQUISITION_AUTOMATIC,
    notes=(
        "2 vCPU, 2 GB RAM, 10 GB disk, gpuConfig: null — CPU only, confirmed "
        "by listing the account's FC functions (2026-08-11). Hibernates: "
        "pause p50 2.6 s, wake p50 1.1 s, and a 45-minute hibernation "
        "survived with the TTL clock stopped and a 1.2 s wake, with a running "
        "'flashnode work' still claiming leases across the pause. This API "
        "creates and enrols them itself (sandbox_orchestrator.start_session), "
        "which today it does only for evaluation sessions — the mechanism is "
        "general, the caller is not."
    ),
)

_FC_GPU = Venue(
    id=VENUE_FC_GPU,
    display="Alibaba FC serverless GPU",
    currency=CURRENCY_USD,
    has_gpu=True,
    # The top of the published instance range: Hopper 96 GB per card.
    max_vram_gb=96.0,
    hibernates=True,
    acquisition=ACQUISITION_NONE,
    notes=(
        "Tesla T4 16 GB, Ampere A10 24 GB, Ada 24/48 GB and Hopper 96 GB, up "
        # 1.7/0.2, NOT the 1.5/0.25 an earlier brief carried — see migration
        # 0019, which records the published factors and the correction. This
        # note and the `price_quotes` rows must not disagree: they are the
        # same number shown in two places.
        "to 8 cards, billed per GB-VRAM-second (ada.1: 1.7 CU active against "
        "0.2 CU idle). Elastic instances scale to zero; Provisioned ones "
        "remove the cold start. NOT INTEGRATED: nothing in this repo creates "
        "an FC GPU function, so no candidate can carry this venue and no plan "
        "may rest on it."
    ),
)

_ECS_GPU = Venue(
    id=VENUE_ECS_GPU,
    display="Alibaba ECS GPU",
    currency=CURRENCY_USD,
    has_gpu=True,
    # Not a venue-level fact, for RunPod's reason turned inside out: the
    # instance type is deployment configuration (`settings.ecs_instance_type`,
    # default `ecs.gn6i-c4g1.xlarge` = 1x T4 16 GB), and the family ladder runs
    # far past it. The ceiling is whatever this deployment is pointed at, and
    # the per-machine placement gate already checks the card it gets.
    max_vram_gb=None,
    hibernates=False,
    acquisition=ACQUISITION_AUTOMATIC,
    notes=(
        "A real VM with a real GPU, created and destroyed by this API: "
        "`capacity/ecs.py` runs one pay-as-you-go instance per job through "
        "RunInstances and destroys it with DeleteInstance (Force=true) — "
        "destroyed, never stopped, because a stopped instance keeps its disk "
        "and keeps billing. The default ecs.gn6i-c4g1.xlarge (1x Tesla T4, "
        "16 GB) measured $1.279/hr pay-as-you-go in ap-southeast-1 via "
        "DescribePrice on 2026-08-12 — 4-8x RunPod for comparable hardware, "
        "and chosen anyway because demonstrating Alibaba compute is the "
        "point. The instance metadata endpoint is disabled at creation and "
        "no RAM role is attached, so unsandboxed task code has no path to "
        "cloud credentials. NOT YET PROVEN ON HARDWARE: nothing has booted "
        "flashnode on an Alibaba GPU image, and an unconfigured deployment "
        "(no ECS credentials) can create nothing here."
    ),
)

#: Declaration order, which is also the product's default preference: what you
#: already have, then the cheap place to wait, then rented hardware — cheaper
#: first — and last the venue that does not exist yet.
VENUES: tuple[Venue, ...] = (_OWNED, _FC_SANDBOX, _RUNPOD, _ECS_GPU, _FC_GPU)

_BY_ID = {item.id: item for item in VENUES}

#: How a marketplace listing's venue maps onto a physical one.
#:
#: Both a workspace machine and an open-market listing are a FlashNode host on
#: somebody's desk; the difference between them is who pays, which is
#: ``plan.DEFAULT_VENUE_ORDER``'s business and not this module's.
#: ``plan.VENUE_RENTED`` is deliberately absent: it names "rented cloud"
#: generically, and RunPod and FC are not interchangeable, so guessing which
#: one a rented listing meant would attach a hardware claim to a machine
#: nothing verified. An unmapped listing is simply not venue-attributed, and
#: the planner leaves it alone rather than excluding it on a guess.
_LISTING_VENUE = {
    "workspace": VENUE_OWNED,
    "market": VENUE_OWNED,
}


def venue(venue_id: str) -> Venue:
    """The venue with this id. ``KeyError`` for an unknown one, on purpose —
    a mistyped venue that silently produced a permissive default would be a
    fit decision taken by a typo."""
    return _BY_ID[venue_id]


def venue_for_listing(commercial_venue: str | None) -> str | None:
    """``workspace``/``market`` → ``owned``; anything else → ``None``.

    ``None`` means *not attributable to a venue*, which is a state and not a
    failure. See ``_LISTING_VENUE``.
    """
    if commercial_venue is None:
        return None
    return _LISTING_VENUE.get(str(commercial_venue))


# ---------------------------------------------------------------------------
# fit, per kind
# ---------------------------------------------------------------------------

_UNSUITED_NO_GPU = (
    "{display} has no GPU at all (gpuConfig: null, 2 vCPU / 2 GB RAM / 10 GB "
    "disk, confirmed by listing the account's FC functions). This job asks "
    "for {gpus} GPU(s) per task, so no price and no fit argument can put it "
    "here."
)

#: ``kind → ((venue id, suited, reason), ...)``, in preference order.
#:
#: One row per venue per kind, and every row states a fact rather than a
#: preference. Where two venues are both suited the order follows the same
#: product judgement the planner already encodes — use what you own before you
#: rent — so the ordering never has to be argued twice.
_FITS: dict[WorkloadKind, tuple[tuple[str, bool, str], ...]] = {
    # Many independent short trials, latency-tolerant. The one shape a pull
    # fleet of ordinary machines is strictly good at: every machine that
    # claims takes another trial, and no trial waits on any other.
    WorkloadKind.HPO: (
        (
            VENUE_OWNED,
            True,
            "trials are independent and short, which is exactly what a pull "
            "fleet absorbs — each machine that claims takes another trial, "
            "with no coordination between them — and workspace machines are "
            "free to their own members (M1).",
        ),
        (
            VENUE_FC_SANDBOX,
            True,
            "a trial is short and self-contained, so the gap between trials "
            "costs almost nothing here: pause p50 2.6 s, wake p50 1.1 s, and "
            "hibernation held for 45 minutes with the TTL clock stopped. "
            "CPU-only, so only CPU trials belong.",
        ),
        (
            VENUE_RUNPOD,
            True,
            "real GPUs by the hour, which a GPU sweep can use — but trials "
            "are latency-tolerant by definition, so buying pod-hours to "
            "finish sooner is the expensive corner of the frontier rather "
            "than the natural home. A human starts the pod.",
        ),
        (
            VENUE_ECS_GPU,
            True,
            "independent trials fan out across rented instances the same way "
            "they fan out across owned ones, and this is the one GPU venue "
            "this API can create capacity at without a human. It is also the "
            "expensive way to buy speed for latency-tolerant work: $1.279/hr "
            "for a 16 GB T4 (DescribePrice, ap-southeast-1, 2026-08-12), "
            "billed for the whole rental including the boot and the install.",
        ),
        (
            VENUE_FC_GPU,
            True,
            "bursty independent GPU trials are what per-GB-VRAM-second "
            "billing and scale-to-zero are for (ada.1: 1.5 CU active, 0.25 "
            "idle). Nothing in this repo can create such a function, so this "
            "is where the work belongs and not where it can go.",
        ),
    ),
    # One long checkpointed job that wants a real card for its duration.
    WorkloadKind.TRAINING: (
        (
            VENUE_OWNED,
            True,
            "a home rig in the gpu-24gb class is a real training machine, and "
            "the 2026-08-02 supply-side note names that tier as the one that "
            "matters. It is free to its workspace (M1). The caveat is "
            "interruption: a long single job loses the most when a volunteer "
            "machine disappears, and only checkpointed work survives it.",
        ),
        (
            VENUE_RUNPOD,
            True,
            "a rented pod is a card held for the whole run, which is what a "
            "long job needs and what a volunteer fleet cannot promise. Priced "
            "per hour in USD, and with no ResourceProvider in this repo a "
            "human has to start it.",
        ),
        (
            VENUE_ECS_GPU,
            True,
            "the same card-for-the-whole-run argument as RunPod, and the only "
            "one of the two this API can actually obtain: one instance is "
            "created per job and destroyed when the rental ends. $1.279/hr "
            "for a 16 GB T4 measured on 2026-08-12, so a long run is where "
            "that rate hurts most — and interruption risk is a rented "
            "machine's own, not a volunteer's, which is what a checkpointed "
            "job is buying here.",
        ),
        (
            VENUE_FC_GPU,
            True,
            "T4 16 GB through Hopper 96 GB, up to 8 cards, and a Provisioned "
            "instance removes the cold start a long job would otherwise pay "
            "once. Not integrated, so it can hold no work today.",
        ),
        (
            VENUE_FC_SANDBOX,
            False,
            "2 vCPU, 2 GB RAM and no GPU (gpuConfig: null, confirmed by "
            "listing the account's FC functions). Gradient work does not fit "
            "in that at any duration — this is the venue's hard limit, not a "
            "judgement about its price.",
        ),
    ),
    # Deliberately the same table as TRAINING; see below.
    WorkloadKind.FINETUNE: (),
    # Many short forward passes. Batch throughput, never per-request latency.
    WorkloadKind.INFERENCE: (
        (
            VENUE_FC_GPU,
            True,
            "the venue this workload is the argument for: idle GPU bills at "
            "roughly a sixth of active (ada.1: 0.25 CU against 1.5), and "
            "Provisioned instances mean no cold start, so bursty forward "
            "passes pay for the passes and not for the waiting. Not "
            "integrated — acquisition is 'none' — so it appears here as fit "
            "and never as capacity.",
        ),
        (
            VENUE_OWNED,
            True,
            "a GPU rig already online runs forward passes at no marginal "
            "cost. It cannot idle cheaply, so it is the right answer when the "
            "batch is continuous and the wrong one when it is bursty.",
        ),
        (
            VENUE_RUNPOD,
            True,
            "a held pod has no cold start either, but it bills whether a "
            "batch is running or not — the opposite of what bursty inference "
            "wants, and the reason FC's idle rate is the interesting number.",
        ),
        (
            VENUE_ECS_GPU,
            True,
            "a real card with no cold start once it is up, and it does not "
            "hibernate: an ECS instance bills its full $1.279/hr (16 GB T4, "
            "measured 2026-08-12) between batches exactly as it does during "
            "them. Right for a continuous batch, and the most expensive "
            "possible way to wait for a bursty one.",
        ),
        (
            VENUE_FC_SANDBOX,
            False,
            "CPU-only (gpuConfig: null, 2 vCPU / 2 GB RAM). A forward pass "
            "small enough to run there is not the workload this venue exists "
            "for.",
        ),
    ),
    # Scores outputs. Often wait-heavy, often CPU-bound.
    WorkloadKind.EVALUATION: (
        (
            VENUE_FC_SANDBOX,
            True,
            "this is the workload the venue was built for in this product: "
            "sandbox_orchestrator.start_session creates a sandbox, enrols a "
            "FlashNode worker in it and hibernates it while training runs "
            "elsewhere. Measured: pause 2.6 s, wake 1.1 s (1.2 s even after "
            "45 minutes), TTL clock stopped while paused, and the worker kept "
            "claiming leases straight across a pause. Waiting costs almost "
            "nothing here and costs a whole machine anywhere else.",
        ),
        (
            VENUE_OWNED,
            True,
            "an idle workspace machine scores an artifact for free (M1). What "
            "it cannot do is hibernate: a wait-heavy evaluation holds the "
            "whole machine for the length of the wait.",
        ),
        (
            VENUE_RUNPOD,
            True,
            "capable, and usually the expensive way to do it — a pod billed "
            "by the hour to wait on CPU-bound scoring is paying for a card it "
            "is not using. Capable is still capable, so this is a price "
            "argument for the planner and not a fit refusal.",
        ),
        (
            VENUE_ECS_GPU,
            True,
            "capable, and the worst deal on this table for wait-heavy "
            "scoring: $1.279/hr (16 GB T4, measured 2026-08-12) for a card "
            "that a CPU-bound evaluation never touches, with no hibernation "
            "to make the waiting cheap. A price argument, not a fit refusal.",
        ),
        (
            VENUE_FC_GPU,
            True,
            "a GPU function can score too, and would be paying GB-VRAM-"
            "seconds for work that is usually CPU-bound. Not integrated in "
            "any case.",
        ),
    ),
    # Rounds of local training with an aggregation step between them.
    WorkloadKind.FEDERATED: (
        (
            VENUE_OWNED,
            True,
            "the entire point: data that cannot be pooled stays on the "
            "machine that holds it (local_inputs are bind-mounted read-only "
            "by the host and never uploaded), and aggregation runs on trusted "
            "infrastructure here rather than on any host.",
        ),
        (
            VENUE_RUNPOD,
            True,
            "a rented pod is a real GPU for a round's local training, but it "
            "holds none of the submitter's private data — capacity for the "
            "training, not a participant in the privacy argument.",
        ),
        (
            VENUE_ECS_GPU,
            True,
            "same reading as RunPod and the same limit: an instance we rent "
            "into the submitter's own pool is capacity for a round's local "
            "training and never a holder of anyone's private data, since "
            "local_inputs are bind-mounted by the host that owns them and "
            "are never uploaded. $1.279/hr, 16 GB T4, measured 2026-08-12.",
        ),
        (
            VENUE_FC_GPU,
            True,
            "same reading as RunPod, with per-GB-VRAM-second billing that "
            "suits a round's start-stop rhythm. Not integrated.",
        ),
        (
            VENUE_FC_SANDBOX,
            False,
            "a federated round is local training, and this venue has 2 vCPU, "
            "2 GB RAM and no GPU (gpuConfig: null). A model small enough to "
            "train there exists, but nothing in the spec says this is one, "
            "and guessing that it is would put real gradient work on a "
            "two-core box.",
        ),
    ),
    # The honest fallback. Fit rules nothing out, because nothing was learned.
    WorkloadKind.COMMAND: (
        (
            VENUE_OWNED,
            True,
            "nothing in the spec identifies what this computes, so fit rules "
            "nothing out and the placement gates and the price decide alone. "
            "Machines already enrolled, free to their workspace (M1).",
        ),
        (
            VENUE_FC_SANDBOX,
            True,
            "unclassified work is not refused a venue on a guess. CPU-only, "
            "so a declared GPU requirement still excludes it — that is a "
            "hardware fact and not a fit reading.",
        ),
        (
            VENUE_RUNPOD,
            True,
            "capable of anything a pod can run; a human starts it, and it "
            "bills by the hour.",
        ),
        (
            VENUE_ECS_GPU,
            True,
            "capable of anything a VM can run, and this API can start it "
            "without a human. Nothing in the spec says what this computes, "
            "so the $1.279/hr rate (16 GB T4, 2026-08-12) and the placement "
            "gates decide alone.",
        ),
        (
            VENUE_FC_GPU,
            True,
            "no fit objection, and no way to obtain capacity: not integrated.",
        ),
    ),
}

# FINETUNE routes exactly as TRAINING does. The two are reported separately
# because "starts from someone else's weights" is worth telling a reader, and
# they are routed identically because nothing about the venue changes — the
# same card, held for the same kind of run. Aliasing the table rather than
# copying it is what guarantees they cannot drift into disagreeing, which
# would turn a cosmetic distinction into a placement difference.
_FITS[WorkloadKind.FINETUNE] = _FITS[WorkloadKind.TRAINING]


def venues_for(
    kind: WorkloadKind, *, gpus_per_task: int | None = None
) -> tuple[VenueFit, ...]:
    """Every venue judged against ``kind``, best fit first, each with a reason.

    **All four are returned, always**, including the ones that do not fit.
    A surface that shows only the survivors cannot say why the others are
    missing, and "RunPod is not listed because nobody started a pod" is the
    sentence that keeps the product honest on a stage. The order is: usable
    first, then suited-but-unobtainable, then unsuited, and inside each group
    the declaration order in ``_FITS``, so the same question always returns
    the same list.

    ``gpus_per_task`` is the job's declared GPU requirement when there is one.
    It applies a **hardware** refusal on top of the fit table: a venue with no
    GPU cannot run a task that asked for one, whatever kind of work it is, and
    that refusal overrides even ``COMMAND``'s "rule nothing out". It is
    keyword-only and optional so ``venues_for(kind)`` remains the question
    "where does this kind of work belong", which is a real question with a
    real answer independent of any one job.

    This narrows and never widens. Nothing here can make a venue suited that
    the table calls unsuited, and nothing here runs before the placement
    gates — ``plan.eligible_fleet`` has already been applied by the time any
    of this is consulted.
    """
    ranked: list[tuple[int, int, VenueFit]] = []
    for position, (venue_id, suited, reason) in enumerate(_FITS[kind]):
        item = _BY_ID[venue_id]
        if suited and gpus_per_task is not None and gpus_per_task >= 1:
            if not item.has_gpu:
                suited = False
                reason = _UNSUITED_NO_GPU.format(
                    display=item.display, gpus=gpus_per_task
                )
        fit = VenueFit(
            venue=item,
            suited=suited,
            acquirable=item.acquisition != ACQUISITION_NONE,
            reason=reason,
        )
        bucket = 0 if fit.usable else 1 if fit.suited else 2
        ranked.append((bucket, position, fit))
    return tuple(fit for _, _, fit in sorted(ranked, key=lambda row: row[:2]))


def usable_venue_ids(
    kind: WorkloadKind, *, gpus_per_task: int | None = None
) -> frozenset[str]:
    """The venue ids a plan may actually rest on for ``kind``.

    Suited **and** acquirable. A venue whose ``acquisition`` is ``none`` never
    appears here however well it fits, because a plan naming it would be
    describing capacity that does not exist — which is the one thing a
    comparison view must never do.
    """
    return frozenset(
        fit.venue.id
        for fit in venues_for(kind, gpus_per_task=gpus_per_task)
        if fit.usable
    )
