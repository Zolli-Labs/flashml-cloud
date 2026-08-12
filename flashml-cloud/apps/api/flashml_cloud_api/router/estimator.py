"""How long will one task take — and the four rungs it climbs to find out.

Pure functions over rows a query supplied. No database, no clock, no config,
no I/O of any kind, for the reason ``verify.py`` states next door: the rules
below are policy, they have to be readable and arguable, and a policy that
lives inside a SQL string is neither.

**The evidence ladder, in order.** Each rung is weaker than the one above it,
and the estimator stops at the first rung that has anything usable to say:

1. ``same_job_peers`` — other machines' recorded durations on THIS job
   (``db.peer_task_durations``). The strongest evidence there is: the same
   code, the same inputs, the same round.
2. ``user_shape`` — this submitter's history for this task shape, on other
   jobs.
3. ``class_shape`` — the same shape anywhere, within the same capability
   class.
4. ``Prior`` — a reference observation from a DIFFERENT class, widened. This
   is the last rung and it is deliberately close to useless; see
   ``project_across_class``.

Below all four is ``None``, and ``None`` is the answer this module is most
proud of. Roughly 29 credited tasks exist in the whole ledger, so a novel
workload genuinely has no history and the honest response is *not observed* —
which is what ``plan.Canary`` exists to convert into a measurement.

**Five rules the ladder never bends.**

*Below ``MIN_EVIDENCE`` the answer is a RANGE labelled ``projected``.* Never a
point estimate, never a confidence percentage. A "91% confidence" derived
from two observations is exactly the number ``metrics.py`` was written to
refuse, and the fact that it is arithmetically computable is not an argument
that it means anything.

*Never aggregate across capability class.* A host at 0.95 on ``cpu-small``
and 0.40 on ``gpu-24gb`` reads ~0.9 in aggregate and then fails the GPU task.
Observations from another class are DROPPED, not averaged in, and
``select_acceptance`` refuses to fall back to a neighbouring class even when
that leaves it with nothing to return.

*Federated work is not timing evidence.* ``fedavg.on_round`` credits a
round's contributors from the coordinator's task view, which reports no
duration — production read 26 credits against 16 attempts on one machine on
2026-08-03, and the ten-row gap was federated. Those rows are real
contributions and useless here.

*Unproven is not zero.* ``scheduler/__init__.py:747`` — *"0.0 is 'it failed
everything', None is 'it has not been asked yet', and treating the second as
the first is exactly how a pool stops accepting new volunteers."*
``reliability_tier`` returns ``unproven`` and no number, and nothing
downstream may substitute one.

*Survivorship bias is disclosed, every time.* ``contributions`` holds only
ACCEPTED work — the runs that timed out, crashed or were abandoned are
invisible in it by construction — so every median taken from it is biased
fast. Every note this module writes says so.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from flashml_cloud_api.metrics import MIN_EVIDENCE

__all__ = [
    "BASIS_ESTIMATED",
    "BASIS_MEASURED",
    "BASIS_PROJECTED",
    "CLASS_LADDER",
    "CPU_LARGE_CORES",
    "CANARY_SPREAD",
    "LADDER_SPREAD_PER_STEP",
    "MIN_EVIDENCE",
    "RUNG_CLASS_SHAPE",
    "RUNG_LADDER",
    "RUNG_SAME_JOB",
    "RUNG_USER_SHAPE",
    "SURVIVORSHIP_NOTE",
    "THIN_EVIDENCE_SPREAD",
    "TIER_MIXED",
    "TIER_SHAKY",
    "TIER_STEADY",
    "TIER_UNPROVEN",
    "Estimate",
    "Evidence",
    "Observation",
    "Prior",
    "estimate_task_seconds",
    "from_canary",
    "hardware_class",
    "planning_seconds",
    "project_across_class",
    "reliability_tier",
    "select_acceptance",
]

# ---------------------------------------------------------------------------
# the vocabulary
# ---------------------------------------------------------------------------

#: This exact job, observed. Rung 1 with enough samples to be a distribution.
BASIS_MEASURED = "measured"

#: The same task shape, observed somewhere else. Rungs 2 and 3 with enough
#: samples. Real observations, indirectly applicable.
BASIS_ESTIMATED = "estimated"

#: Thin or transplanted. ANY rung below ``MIN_EVIDENCE`` samples, and every
#: cross-class projection however many samples it rests on. A ``projected``
#: value is always a range and never a point.
BASIS_PROJECTED = "projected"

Basis = Literal["measured", "estimated", "projected"]

RUNG_SAME_JOB = "same_job_peers"
RUNG_USER_SHAPE = "user_shape"
RUNG_CLASS_SHAPE = "class_shape"

#: Strongest first. ``estimate_task_seconds`` sorts the evidence it is given
#: into this order rather than trusting the caller's, so a caller cannot
#: invert the ladder by accident and have a weaker rung answer first.
RUNG_LADDER = (RUNG_SAME_JOB, RUNG_USER_SHAPE, RUNG_CLASS_SHAPE)

#: Which basis a rung earns once it has ``MIN_EVIDENCE`` samples. Rung 1 is
#: the job itself running on other machines — that is a measurement of this
#: workload. Rungs 2 and 3 are the same *shape* elsewhere, which is an
#: inference from real data and is named as one.
_RUNG_BASIS: dict[str, Basis] = {
    RUNG_SAME_JOB: BASIS_MEASURED,
    RUNG_USER_SHAPE: BASIS_ESTIMATED,
    RUNG_CLASS_SHAPE: BASIS_ESTIMATED,
}

TIER_UNPROVEN = "unproven"
TIER_STEADY = "steady"
TIER_MIXED = "mixed"
TIER_SHAKY = "shaky"

#: At or above this acceptance rate a host is ``steady``.
#:
#: The same 0.9 as ``flashruntime.scheduler.RELIABILITY_STEADY_AT``, and
#: restated here rather than imported for one reason only: the workspace rule
#: is that this repo imports ``flashruntime.protocol`` and nothing else, and
#: widening that boundary is a decision to record in a commit, not to make in
#: passing inside a router. ``tests/test_router_estimator.py`` imports the
#: runtime and asserts these three constants still match it, so the copy
#: cannot drift silently — which is the only property that mattered.
RELIABILITY_STEADY_AT = 0.9

#: Below this acceptance rate a host is ``shaky``. The gap between the two
#: thresholds is deliberate dead space: a host inside it is ``mixed``, its
#: evidence is genuinely ambiguous, and nothing acts on it.
RELIABILITY_SHAKY_BELOW = 0.75

#: How many decimal places a duration keeps. Milliseconds, matching
#: ``metrics.py``: the underlying instants are database timestamps around
#: network hops, and anything finer is precision the measurement never had.
#: Rounding here also makes every value below bit-reproducible, which is what
#: lets the determinism tests compare whole structures.
_SECONDS_PLACES = 3

#: Attached to every estimate drawn from ``contributions``.
SURVIVORSHIP_NOTE = (
    "accepted runs only — contributions holds no failed or abandoned work, "
    "so this is biased fast"
)

# ---------------------------------------------------------------------------
# how wide a band is, when it cannot be narrow
# ---------------------------------------------------------------------------

#: Multiplier applied either side of the observed range when there are fewer
#: than ``MIN_EVIDENCE`` samples.
#:
#: With one to four observations the *observed* spread is not the
#: distribution's spread — it is an artefact of how few times we looked, and
#: reporting min..max would present the narrowest possible claim on the
#: weakest possible evidence. Two is a stated constant, not a measured one,
#: and it is chosen to be wide enough that nobody mistakes the band for a
#: prediction. It is the price of refusing to return a point.
THIN_EVIDENCE_SPREAD = 2.0

#: Multiplier either side of a single measured canary run.
#:
#: Narrower than ``THIN_EVIDENCE_SPREAD`` on purpose, and the difference is
#: the whole argument for the canary: one historical sample might be a
#: different shape on a different host under different concurrency, while a
#: canary is THIS shape, on THIS class, minutes ago. Run-to-run variance
#: between identical trials on one host is the only thing left, and it is
#: small compared with everything the historical rungs carry.
#:
#: Still a band and still ``projected``, because one run cannot bound
#: variance — it can only locate it. Still a stated constant: the moment five
#: canaries exist for a shape, they are rung-3 evidence and this number stops
#: being consulted.
CANARY_SPREAD = 1.5

#: Widening applied per step of capability-ladder distance when a reference
#: observation has to be transplanted across classes.
#:
#: Uncapped, deliberately. Seven steps produce a band of 128x either side,
#: which is useless — and looking useless is the correct behaviour, because
#: no measured speed ratio between two capability classes exists anywhere in
#: this system. Capping it would make a transplanted guess look better
#: founded the further it travelled.
LADDER_SPREAD_PER_STEP = 2.0

# ---------------------------------------------------------------------------
# the capability ladder
# ---------------------------------------------------------------------------

#: The classes, weakest first. Taken from the marketplace design §2.5.2,
#: whose ratios come from RunPod community prices captured 2026-08-11.
#:
#: §4 of the same document lists a slightly different set (``cpu-small ·
#: cpu-large · gpu-8gb · gpu-16gb · gpu-24gb · gpu-40gb · gpu-80gb``). §2.5.2
#: wins here because it is the priced ladder the credit unit is defined
#: against, and because ``gpu-40gb`` corresponds to no tier anybody sells.
CLASS_LADDER = (
    "cpu-small",
    "cpu-large",
    "gpu-8gb",
    "gpu-16gb",
    "gpu-24gb",
    "gpu-48gb",
    "gpu-80gb",
    "gpu-80gb-hopper",
)

#: Floor VRAM for each GPU class, in MB as the driver reports it.
_GPU_CLASS_FLOORS_MB: tuple[tuple[int, str], ...] = (
    (81920, "gpu-80gb"),
    (49152, "gpu-48gb"),
    (24576, "gpu-24gb"),
    (16384, "gpu-16gb"),
    (8192, "gpu-8gb"),
)

#: How far under nominal a driver's VRAM reading may sit and still count.
#:
#: Load-bearing, not a nicety. An RTX 3090 reports 24564 MB, not 24576, and a
#: strict ``>=`` would file the single densest card in the reference class as
#: ``gpu-16gb`` — mislabelling the exact hardware §2.5.2 picked as the unit.
#: 5% clears every real card by a wide margin (the next class down is 2x
#: away, so no tolerance this small can reach it).
VRAM_TOLERANCE = 0.05

#: Compute capability at or above which an 80GB card is Hopper-or-later.
#: H100 and H200 are sm_90; A100 is sm_80.
_HOPPER_COMPUTE_CAPABILITY = 9.0

#: Cores at or above which a CPU-only host is ``cpu-large``.
CPU_LARGE_CORES = 8.0


# ---------------------------------------------------------------------------
# the shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Estimate:
    """A duration band, what it rests on, and how much of it there was.

    ``low``/``high`` are seconds and are always a band — ``low == high`` only
    when every observation in a ``MIN_EVIDENCE``-strong sample was identical,
    which is a real measurement rather than a collapsed guess. Nothing here
    ever returns a point estimate from thin evidence, and nothing anywhere
    returns a confidence percentage.

    ``n`` is the number of usable observations behind the band. It is on the
    face of the result rather than buried in ``note`` because it is the field
    that tells a reader whether to believe the other four.
    """

    low: float
    high: float
    basis: Basis
    n: int
    note: str


@dataclass(frozen=True)
class Observation:
    """One recorded duration, with the two facts that decide whether it counts.

    ``capability_class`` is the class of the machine that PRODUCED this
    duration — hardware, not the work's label. An observation from another
    class is dropped rather than pooled.

    ``federated`` marks a contribution credited by ``fedavg.on_round``. Those
    rows carry no duration in practice, so the finite-and-positive filter
    already removes them; the flag exists so a caller that can identify one
    positively says so, and so a federated row that somehow acquired a
    duration still cannot become timing evidence.
    """

    seconds: float | None
    capability_class: str | None = None
    federated: bool = False


@dataclass(frozen=True)
class Evidence:
    """Everything one rung of the ladder has to offer."""

    rung: str
    observations: tuple[Observation, ...] = ()


@dataclass(frozen=True)
class Prior:
    """A reference duration observed in a DIFFERENT capability class.

    The last rung, and the only one that transplants a number rather than
    reading one. ``project_across_class`` is where it is widened; nothing
    else in this module reads it.
    """

    seconds: float
    capability_class: str
    n: int = 1


# ---------------------------------------------------------------------------
# capability class
# ---------------------------------------------------------------------------


def hardware_class(capabilities: Mapping[str, Any] | None) -> str | None:
    """Which ladder class this host's HARDWARE sits in, or None.

    Reads ``NodeCapabilities`` as the registry stores it (``machines
    .capabilities``, jsonb) and nothing else. That model carries
    ``cpu_cores``, ``memory_bytes``, ``gpus: list[GpuInfo]``,
    ``dataset_cache_bytes``, ``pools``, ``os`` and ``architecture`` — so
    those are what a class may be derived from. The ``flashnode/benchmark/``
    admission probes (``cpu_hash_mbps``, ``net_down_mbps``, …) are NOT
    available: their concrete implementations do not exist yet ("interface +
    orchestration complete; concrete probes land with the GPU/community
    tier"), nothing calls ``run_admission`` outside flashnode's own tests,
    and ``NodeCapabilities`` has no field to carry a result if one were
    computed. A class derived from them would be derived from nothing.

    **This labels HARDWARE.** ``metrics.acceptance_rates`` keys on a class
    that labels WORK — the same machine appears there once per class of task
    it has attempted, which is what makes "0.95 on cpu-small, 0.40 on
    gpu-24gb" expressible. The two are different keys and this function
    supplies only the first: it decides which durations are comparable, never
    which acceptance rate applies. ``select_acceptance`` does the second.

    ``None`` is returned, and is a real answer, whenever the reading does not
    support a class:

    - No ``gpus`` and no ``cpu_cores``. A host that reported nothing.
    - GPUs present but every ``memory_total_mb`` is null — a driver that
      answered, unparseably. Real hardware, unknown class; it stays fully
      eligible and simply has no historical evidence pooled to it.
    - A GPU below the ladder's floor (a 6GB card). Clamping it up to
      ``gpu-8gb`` would let it inherit evidence from cards half again its
      size, which is the mislabelling the ladder exists to prevent.

    Multi-GPU hosts take the class of their LARGEST device. Device *count* is
    the fifth placement gate's business and is read there, from the length of
    the same list; it has nothing to say about which durations compare.
    """
    if not isinstance(capabilities, Mapping):
        return None

    gpus = capabilities.get("gpus")
    if isinstance(gpus, list) and gpus:
        return _gpu_class(gpus)

    cores = _finite(capabilities.get("cpu_cores"))
    if cores is None or cores <= 0:
        return None
    return "cpu-large" if cores >= CPU_LARGE_CORES else "cpu-small"


def _gpu_class(gpus: Sequence[Any]) -> str | None:
    """The class of the largest reported device, Hopper-aware."""
    best_mb: float | None = None
    best_capability: float | None = None
    for gpu in gpus:
        if not isinstance(gpu, Mapping):
            continue
        megabytes = _finite(gpu.get("memory_total_mb"))
        if megabytes is None or megabytes <= 0:
            continue
        if best_mb is None or megabytes > best_mb:
            best_mb = megabytes
            best_capability = _finite(gpu.get("compute_capability"))

    if best_mb is None:
        return None  # devices present, none of them measurable

    for floor_mb, name in _GPU_CLASS_FLOORS_MB:
        if best_mb >= floor_mb * (1 - VRAM_TOLERANCE):
            if (
                name == "gpu-80gb"
                and best_capability is not None
                and best_capability >= _HOPPER_COMPUTE_CAPABILITY
            ):
                return "gpu-80gb-hopper"
            return name
    return None  # below the ladder's floor; clamping up would pool it wrongly


def _class_distance(left: str | None, right: str | None) -> int | None:
    """Steps between two ladder classes, or None if either is off the ladder."""
    if left is None or right is None:
        return None
    try:
        return abs(CLASS_LADDER.index(left) - CLASS_LADDER.index(right))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# reliability, as a tier and never as a divisor
# ---------------------------------------------------------------------------


def select_acceptance(
    rows: Iterable[Mapping[str, Any]],
    *,
    machine_id: str,
    capability_class: str | None,
) -> Mapping[str, Any] | None:
    """The one ``metrics.acceptance_rates`` row for this machine in this class.

    ``None`` when there is no row for that exact pair — and **no fallback to
    another class, ever**, which is the entire reason this function exists
    rather than a dict lookup at the call site. ``acceptance_rates`` already
    refuses to produce an all-class rollup and says a caller who wants one
    "has to write the flattening down where it can be reviewed". This is that
    review point, and the answer is no: a host with a 0.95 record on
    ``cpu-small`` has told us nothing about the ``gpu-24gb`` task, and
    borrowing the cpu number is exactly the failure the split was built to
    make impossible.

    A ``capability_class`` of ``None`` — a host whose hardware class could
    not be derived — matches nothing and returns ``None``. It is unproven,
    which is a state the fleet handles.
    """
    if capability_class is None:
        return None
    for row in rows:
        if (
            str(row.get("machine_id")) == str(machine_id)
            and str(row.get("capability_class")) == capability_class
        ):
            return row
    return None


def reliability_tier(row: Mapping[str, Any] | None) -> str:
    """``unproven`` / ``steady`` / ``mixed`` / ``shaky`` from one acceptance row.

    Mirrors ``flashruntime.scheduler.reliability_tier`` exactly, including its
    thresholds, but over a different body of evidence: the runtime reads the
    coordinator's in-memory counters, this reads resolved ``attempts`` in
    Postgres. Same rule, two ledgers — and one of them survives a deploy.

    **``None`` in, ``unproven`` out — never ``shaky``.** So is a row with
    fewer than ``MIN_EVIDENCE`` resolved attempts, and so is a row whose
    ``acceptance_rate`` came back ``None`` (which is what
    ``acceptance_rates`` returns below the same threshold). ``0.0`` means
    "it failed everything"; absence means "it has not been asked yet"; a
    router that spends the second as the first stops new volunteers ever
    getting a first task.

    Returns a tier and never a number, because the number is what invites the
    arithmetic this design forbids — ``ask ÷ goodput`` was in the first draft
    of the marketplace design and was removed for inventing a quantity the
    ledger does not hold.
    """
    if row is None:
        return TIER_UNPROVEN
    resolved = _as_int(row.get("resolved"))
    if resolved is None or resolved < MIN_EVIDENCE:
        return TIER_UNPROVEN
    rate = _finite(row.get("acceptance_rate"))
    if rate is None:
        return TIER_UNPROVEN
    if rate >= RELIABILITY_STEADY_AT:
        return TIER_STEADY
    if rate < RELIABILITY_SHAKY_BELOW:
        return TIER_SHAKY
    return TIER_MIXED


# ---------------------------------------------------------------------------
# the ladder
# ---------------------------------------------------------------------------


def estimate_task_seconds(
    evidence: Sequence[Evidence],
    *,
    capability_class: str | None,
    prior: Prior | None = None,
) -> Estimate | None:
    """Seconds per task for ``capability_class``, or ``None`` if nothing knows.

    Walks ``RUNG_LADDER`` strongest-first — in ladder order regardless of the
    order ``evidence`` arrives in, so a caller cannot let a weaker rung answer
    first by shuffling a list — and returns from the first rung with at least
    one usable observation. "Usable" drops, in this order and each for its own
    reason:

    - a non-finite or non-positive duration (nothing downloads an input,
      computes and uploads a result in zero seconds — ``verify.py`` treats
      the same value as impossible rather than fast);
    - a federated contribution, which carries no duration by construction;
    - an observation from a different capability class, which is not averaged
      in, softened, or weighted down. It is dropped.

    A rung with ``MIN_EVIDENCE`` or more survivors reports p50 with an IQR
    band and earns its rung's basis. A rung with one to four reports the
    observed range widened by ``THIN_EVIDENCE_SPREAD`` and is ``projected``
    whatever rung it sat on — the sample is too small for its own spread to
    mean anything, and a narrow band from two runs is a stronger claim than a
    wide one from twenty.

    With nothing on any rung, ``prior`` is transplanted across the ladder
    (see ``project_across_class``). With no prior either, ``None``: not
    observed. That is the common case at 29 credited tasks, it is not a
    failure, and ``plan.Canary`` is what answers it.

    Raises ``ValueError`` on an unknown rung name. A typo would otherwise
    make a rung silently invisible and let a weaker one answer in its place,
    which reads exactly like a correct answer.
    """
    for item in evidence:
        if item.rung not in _RUNG_BASIS:
            raise ValueError(
                f"unknown evidence rung {item.rung!r}; the ladder is "
                f"{RUNG_LADDER}. A rung this module does not recognise would "
                f"be skipped, and a weaker rung would answer in its place."
            )

    for rung in RUNG_LADDER:
        samples = [
            seconds
            for item in evidence
            if item.rung == rung
            for seconds in _usable(item.observations, capability_class)
        ]
        if samples:
            return _from_samples(
                samples,
                rung_basis=_RUNG_BASIS[rung],
                rung=rung,
                capability_class=capability_class,
            )

    if prior is not None:
        return project_across_class(prior, capability_class)
    return None


def _usable(
    observations: Sequence[Observation], capability_class: str | None
) -> list[float]:
    """The durations in ``observations`` that may be pooled for this class."""
    kept: list[float] = []
    for observation in observations:
        if observation.federated:
            continue
        if observation.capability_class != capability_class:
            continue  # NEVER pooled across class, however tempting
        seconds = _finite(observation.seconds)
        if seconds is None or seconds <= 0:
            continue
        kept.append(seconds)
    return kept


def _from_samples(
    samples: list[float], *, rung_basis: Basis, rung: str, capability_class: str | None
) -> Estimate:
    """A band over ``samples``, thin-evidence rules applied."""
    n = len(samples)
    label = capability_class or "unclassified"

    if n >= MIN_EVIDENCE:
        quartiles = statistics.quantiles(samples, n=4, method="inclusive")
        low, high = quartiles[0], quartiles[2]
        median = statistics.median(samples)
        return Estimate(
            low=_round(low),
            high=_round(high),
            basis=rung_basis,
            n=n,
            note=(
                f"p50/IQR of {n} accepted runs (p50 {_round(median)}s), "
                f"{label}, {rung}, non-federated; {SURVIVORSHIP_NOTE}"
            ),
        )

    low = min(samples) / THIN_EVIDENCE_SPREAD
    high = max(samples) * THIN_EVIDENCE_SPREAD
    return Estimate(
        low=_round(low),
        high=_round(high),
        basis=BASIS_PROJECTED,
        n=n,
        note=(
            f"{n} accepted run{'s' if n != 1 else ''} on {label} ({rung}) — "
            f"fewer than the {MIN_EVIDENCE} this system requires before a "
            f"rate or a median means anything, so the observed range is "
            f"widened {THIN_EVIDENCE_SPREAD:g}x either side and this is a "
            f"range, not a prediction; {SURVIVORSHIP_NOTE}"
        ),
    )


def project_across_class(prior: Prior, capability_class: str | None) -> Estimate | None:
    """Transplant a reference duration onto another class. The weakest rung.

    **It widens; it does not scale.** There is no measured speed ratio
    between any two capability classes anywhere in this system, and the one
    ratio that does exist — the ZC ladder in the marketplace design §2.5.2 —
    is a *price* ratio scraped from RunPod. Using a price ratio as a speed
    ratio would be inventing the number, and inventing it in the direction
    that flatters the expensive machine.

    So the band is symmetric: ``seconds`` divided and multiplied by
    ``LADDER_SPREAD_PER_STEP`` raised to the ladder distance. Symmetric
    rather than "bigger class is never slower" because we do not know the
    workload is compute-bound: a dataloader-bound trial runs at the same
    speed on an H100 and a 3070, and a fast accelerator behind a slow host
    CPU can be genuinely worse. Assuming monotonicity would be assuming the
    thing that has not been measured.

    Two steps produce a 4x band either side, seven produce 128x. That is
    useless, and it is supposed to look useless — the honest reading of a
    useless band is "run the canary", which is what the caller does next.

    ``None`` when either class is off the ladder, and when the prior is
    already in the target class — the latter is not a projection at all and
    a caller offering one has mislabelled a rung rather than found a
    shortcut.
    """
    steps = _class_distance(prior.capability_class, capability_class)
    if steps is None or steps == 0:
        return None
    seconds = _finite(prior.seconds)
    if seconds is None or seconds <= 0:
        return None

    spread = LADDER_SPREAD_PER_STEP**steps
    return Estimate(
        low=_round(seconds / spread),
        high=_round(seconds * spread),
        basis=BASIS_PROJECTED,
        n=max(int(prior.n), 0),
        note=(
            f"no measured speed ratio exists between "
            f"{prior.capability_class} and {capability_class}, so this is "
            f"{_round(seconds)}s widened {spread:g}x either side for "
            f"{steps} ladder step{'s' if steps != 1 else ''} — a transplant, "
            f"not a scaled prediction; {SURVIVORSHIP_NOTE}"
        ),
    )


def from_canary(
    observed_seconds: float, *, capability_class: str | None, tasks_remaining: int
) -> Estimate | None:
    """The estimate one measured probe run buys for the rest of the job.

    This is the seam the whole cold-start argument turns on. A 40-trial sweep
    with no history is unpredictable; the same sweep after ONE trial has run
    is arithmetic. Six seconds converts "we cannot predict this" into "we
    measured it", and the result is defensible in a way no estimate over 29
    ledger rows is.

    It is still ``projected`` and still a band, at ``n=1``, and the honesty is
    load-bearing: one run locates a distribution, it cannot bound one. What
    the caller may say is "we measured one trial at 6.0s and project the
    other 39 from it" — which is what ``note`` says, in those words.

    ``CANARY_SPREAD`` is narrower than ``THIN_EVIDENCE_SPREAD`` because the
    probe is the same shape on the same class minutes ago, where a lone
    historical row could be anything. Both are stated constants; neither is
    measured. Once five canaries exist for a shape they are ordinary rung-3
    evidence and neither is consulted.

    ``None`` for a non-finite or non-positive observation. A probe that
    reported nothing has calibrated nothing, and the caller is back where it
    started rather than one step worse off.
    """
    seconds = _finite(observed_seconds)
    if seconds is None or seconds <= 0:
        return None
    label = capability_class or "unclassified"
    remaining = max(int(tasks_remaining), 0)
    return Estimate(
        low=_round(seconds / CANARY_SPREAD),
        high=_round(seconds * CANARY_SPREAD),
        basis=BASIS_PROJECTED,
        n=1,
        note=(
            f"1 measured canary run at {_round(seconds)}s on {label}; the "
            f"remaining {remaining} task{'s' if remaining != 1 else ''} are "
            f"projected from it with a {CANARY_SPREAD:g}x band — one run "
            f"locates the variance, it cannot bound it"
        ),
    )


def planning_seconds(estimate: Estimate) -> float:
    """The single number a plan is built on: the TOP of the band.

    A plan needs one duration, and choosing which end of the band to use is
    policy, so it lives here in the open rather than inside the solver.

    The top, for two reasons that point the same way. Every band drawn from
    ``contributions`` is already biased fast — only accepted work is in
    there, so the runs that died are invisible — which means the bottom of
    the band is optimistic twice over. And the two errors cost differently: a
    job that finishes ahead of its quoted time costs nothing, while one that
    overruns costs exactly the trust the panel exists to build. Budgets
    computed from the same number are over-stated in the same direction,
    which is the safe one for somebody spending a one-time grant.
    """
    return estimate.high


# ---------------------------------------------------------------------------
# small shared helpers
# ---------------------------------------------------------------------------


def _finite(value: object) -> float | None:
    """``float(value)`` when that is a finite number, else ``None``.

    ``bool`` is refused outright, exactly as ``verify._as_finite_float``
    refuses it: ``True`` would otherwise arrive as a one-second duration — a
    plausible-looking number derived from something that was never a
    measurement.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _round(value: float) -> float:
    return round(float(value), _SECONDS_PLACES)
