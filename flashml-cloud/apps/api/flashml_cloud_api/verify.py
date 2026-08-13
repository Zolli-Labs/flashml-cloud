"""Result verification, slice 1: anomaly detection on time we already measure.

Design: ``docs/superpowers/specs/2026-08-03-result-verification-design.md``.

**Nothing here is enforced, and nothing here may become enforceable.** A
verdict is written to a row and that is the whole of its effect: no lease is
refused, no credit withheld, no commit failed, no placement changed. Two
reasons, and the second is the strong one — a false positive costs a
volunteer their machine while a false negative costs one undeserved credit
(with a fleet of two that asymmetry is decisive), and a mismatch does not
identify the liar anyway. A verifier that can refuse work takes the fleet
down on a false positive.

**What this slice actually knows.** ``attempts.claimed_at`` is written by
the API when it records a claim and ``claim_attempt_credit`` computes
``now() - claimed_at`` at credit time. Both timestamps are ours, so a node
cannot fabricate this number. It can only *inflate* it, by sleeping — which
is why §8.1 is honest that sleeping defeats this slice entirely. What it
catches is the naive attack: a result committed a fraction of a second after
the claim, by a machine that did not download the input, let alone train on
it. That is free to detect, and it is most of what anyone would actually try.

**And what it does not know.** It cannot tell "returned instantly because it
cheated" from "returned instantly because the input was cached and the
machine is fast" — hence flag, never reject. It needs a peer baseline, so a
first-of-its-kind task is unverifiable by it (§8.8).

Deliberately pure: no database, no clock, no config. Everything it needs
arrives as arguments, which is what makes the wrong-``pass`` paths below
cheap to enumerate as tests.
"""
from __future__ import annotations

import math
import re
import statistics

#: How many usable peer samples a baseline needs before this slice will say
#: anything but ``unknown``. Fewer than this is not a distribution.
#:
#: Three is a floor, not a considered statistic — with 29 credited tasks in
#: the ledger it is easily met on a sweep, and the alternative (waiting for a
#: sample large enough to be respectable) would leave the slice inert on the
#: fleet that exists. Note this counts SAMPLES, not distinct machines: on a
#: two-machine fleet a baseline is one peer's history, which is a real
#: weakness — that peer could itself be the liar, and its fast times would
#: then set a low bar for everyone. Distinct-machine counting is the right
#: rule the moment there are enough machines to afford it.
DEFAULT_MIN_PEERS = 3

#: A task is flagged when it finishes in less than this fraction of the peer
#: median. A fraction rather than an absolute constant because task cost
#: varies by orders of magnitude between jobs.
#:
#: 0.2 against the real ledger: 29 credited tasks, mean 9.1s, range
#: 5.5–37.5s. A median near 9s puts the floor at ~1.8s, while the FASTEST
#: honest observation on record is 5.5s — roughly 3x the floor, so no
#: observed honest run comes close to tripping it. The attack it is aimed at
#: (a commit 0.3s after the claim) sits at ~0.03x the median, an order of
#: magnitude the other side. 0.5 would put the floor at 4.5s, just under that
#: 5.5s honest minimum, and one cache-warm run on the desktop would flag an
#: honest volunteer. There is room to go to ~0.3 if the slice proves too
#: quiet; there is no room to go near the median.
DEFAULT_FLOOR_RATIO = 0.2


def _as_finite_float(value: object) -> float | None:
    """``float(value)`` if that is a finite number, else ``None``.

    ``bool`` is rejected outright: ``True`` would otherwise arrive as a
    one-second duration, which is a plausible-looking number derived from
    something that was never a measurement.
    """
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def timing_verdict(
    peers: list[float],
    observed: float,
    *,
    min_peers: int = DEFAULT_MIN_PEERS,
    floor_ratio: float = DEFAULT_FLOOR_RATIO,
) -> tuple[str, dict]:
    """``('pass'|'flag'|'unknown', detail)`` for one task's elapsed time.

    ``peers`` is other machines' ``duration_s`` on the same job — never this
    machine's own, see ``db.peer_task_durations`` for why. ``observed`` is
    this attempt's API-measured elapsed, in seconds.

    The rules, in the order they are applied:

    1. An ``observed`` that is not a finite number is ``unknown``. NaN loses
       every comparison, so the naive ``observed < floor`` answers False and
       reads as ``pass`` — certifying a machine on a duration we never
       learned.
    2. A non-positive ``observed`` is ``flag``, and this runs BEFORE the
       baseline gate on purpose. Both timestamps are the API's own and
       nothing downloads an input, computes and uploads a result in zero
       seconds, so the impossibility needs no peer to establish. Ordering it
       first can only turn an ``unknown`` into a ``flag``, never into a
       ``pass``, and nothing is enforced from a flag.
    3. Peers that are not finite and positive are dropped, and they do not
       count toward ``min_peers``. Zeros are dropped rather than averaged in
       for a reason that matters: they drag the median DOWN, which lowers the
       floor, which helps the next liar pass.
    4. Fewer than ``min_peers`` usable samples is ``unknown``, however
       suspicious the observation looks. Absence of evidence is not evidence
       of honesty — and it is not evidence of guilt either.
    5. Otherwise ``observed < median(peers) * floor_ratio`` is ``flag`` and
       anything else is ``pass``. Strictly ``<``: the boundary belongs to the
       machine.

    ``detail`` carries what the check saw, including the thresholds in force,
    so a row read later can be re-derived rather than taken on faith — the
    thresholds are tunable and a stored verdict must not assume today's.

    Raises ``ValueError`` on a ``floor_ratio`` outside ``(0, 1]`` or a
    ``min_peers`` below 1. A ``floor_ratio`` of 0 puts the floor at 0, which
    nothing can fall below, so every task would be recorded as ``pass``: a
    verifier misconfigured into certifying everything is worse than one that
    is switched off, and it must fail loudly rather than reassuringly. The
    caller wraps this in a best-effort try/except, so the cost of raising is
    a missing row — never a wrong one.
    """
    if not 0 < floor_ratio <= 1:
        raise ValueError(
            f"floor_ratio must be in (0, 1]; got {floor_ratio!r}. A floor at "
            f"or below zero certifies every task as 'pass'; a floor above the "
            f"peer median flags the median machine."
        )
    if min_peers < 1:
        raise ValueError(f"min_peers must be at least 1; got {min_peers!r}")

    usable = [
        sample
        for sample in (_as_finite_float(p) for p in peers)
        if sample is not None and sample > 0
    ]
    base: dict = {"peers": len(usable), "min_peers": min_peers}

    elapsed = _as_finite_float(observed)
    if elapsed is None:
        # No observation at all. Not "fine", not "suspicious" — unknown.
        return "unknown", {**base, "observed_s": None, "reason": "no_observation"}

    if elapsed <= 0:
        return "flag", {
            **base, "observed_s": elapsed, "reason": "non_positive_observed",
        }

    if len(usable) < min_peers:
        return "unknown", {
            **base, "observed_s": elapsed, "reason": "too_few_peers",
        }

    median = statistics.median(usable)
    floor = median * floor_ratio
    return ("flag" if elapsed < floor else "pass"), {
        "observed_s": elapsed,
        "peer_median_s": median,
        "peers": len(usable),
        "floor_s": floor,
        "floor_ratio": floor_ratio,
        "min_peers": min_peers,
    }


# ---------------------------------------------------------------------------
# Slice 2 (G-D): artifact presence — the task claimed success; do the
# artifacts it registered actually exist in the store at the sizes recorded?
# ---------------------------------------------------------------------------


def _artifact_entry(value: object) -> tuple[str, int] | None:
    """``(key, size_bytes)`` from one artifact record, or ``None``.

    Mirrors ``_as_finite_float``: an entry this cannot read cleanly is
    dropped rather than guessed at. Matches the shape this codebase already
    produces for an artifact record — ``artifact_mirror.MirroredObject
    .as_json()`` (``{"key", "size_bytes", "sha256"}``) and the coordinator's
    own ``GET /jobs/{job_id}/artifacts`` listing
    (``flashruntime.service.modea.job_artifacts``, ``{"uri", "key",
    "size_bytes"}``) both carry a string ``key`` and an int ``size_bytes``.

    ``bool`` is rejected as a size for the same reason ``_as_finite_float``
    rejects it as a duration: a plausible-looking number that was never a
    measurement. A negative size is rejected outright — nothing this system
    writes can produce one, so seeing one means the record is not to be
    trusted, not that the artifact is unusually small.
    """
    if not isinstance(value, dict):
        return None
    key = value.get("key")
    if not isinstance(key, str) or not key:
        return None
    size = value.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return None
    return key, size


def artifact_presence_verdict(
    registered: list[dict] | None,
    observed: list[dict] | None,
) -> tuple[str, dict]:
    """``('pass'|'flag'|'unknown', detail)`` for the artifacts one task
    claims against what the store actually holds.

    ``registered`` is what the task claims it produced: artifact records in
    the ``{"key": ..., "size_bytes": ...}`` shape used throughout this
    codebase (``artifact_mirror.MirroredObject.as_json()``; the
    coordinator's own artifact listing). ``observed`` is a listing of that
    same shape read back from wherever the durable store actually is
    (today: the OSS mirror manifest's ``objects``, once this slice is
    wired to a fetch for it — see this module's caller-facing notes).

    The rules, in the order they are applied:

    1. Nothing usable in ``registered`` is ``unknown``. There is nothing to
       check, which is not the same as everything checking out — a task
       that registered no artifacts is not this slice's business either
       way, and saying ``pass`` about zero claims would certify a check
       that never ran.
    2. ``observed is None`` is ``unknown`` REGARDLESS of what ``registered``
       says. ``None`` means the store could not be asked — an absent fetch,
       a failed listing — not "the store was asked and is empty". Folding
       those together would be the exact tolerant-``pass``-shaped mistake
       this layer forbids, just wearing a ``flag`` instead of a ``pass``:
       an observation that was never made must not enter the record as if
       it had been.
    3. ``observed == []`` is a real answer, not a missing one: the store
       WAS asked and holds nothing under scope. Every entry in
       ``registered`` is then reported missing and the verdict is ``flag``
       (rule 1 already guarantees at least one registered entry survives
       to this point).
    4. Otherwise: any registered key absent from ``observed``, or present
       at a different ``size_bytes``, is ``flag``. Every registered key
       present at its recorded size is ``pass``.

    ``detail`` carries counts and the specific keys that disagreed, so a
    row read later shows what was actually compared rather than only the
    verdict.
    """
    reg_entries = [
        e for e in (_artifact_entry(r) for r in (registered or [])) if e is not None
    ]
    if not reg_entries:
        return "unknown", {
            "registered": 0,
            "observed": None,
            "reason": "nothing_registered",
        }

    if observed is None:
        return "unknown", {
            "registered": len(reg_entries),
            "observed": None,
            "reason": "no_observation",
        }

    obs_entries = [
        e for e in (_artifact_entry(o) for o in observed) if e is not None
    ]
    obs_by_key = dict(obs_entries)

    missing: list[str] = []
    wrong_size: list[dict] = []
    for key, size in reg_entries:
        if key not in obs_by_key:
            missing.append(key)
        elif obs_by_key[key] != size:
            wrong_size.append({
                "key": key,
                "registered_bytes": size,
                "observed_bytes": obs_by_key[key],
            })

    detail: dict = {
        "registered": len(reg_entries),
        "observed": len(obs_entries),
        "missing": sorted(missing),
        "wrong_size": wrong_size,
    }
    if missing or wrong_size:
        return "flag", {**detail, "reason": "missing_or_wrong_size"}
    return "pass", detail


# ---------------------------------------------------------------------------
# Slice 3 (G-D): checkpoint monotonicity — did the relayed steps advance, or
# did a resumed attempt silently restart from zero?
# ---------------------------------------------------------------------------

#: An all-ASCII-digit string, the shape a step survives as if something
#: upstream only got as far as extracting it from a ``step-<N>.json``
#: filename rather than handing over the manifest's own typed ``step``.
_STEP_RE = re.compile(r"[0-9]+")


def _as_step_int(value: object) -> int | None:
    """One checkpoint step as a non-negative ``int``, or ``None``.

    Mirrors ``_as_finite_float``: coercion is refused rather than attempted,
    because a step number sits inside an ORDER comparison, and a garbage
    value coerced into *some* integer could land on either side of that
    comparison and manufacture a reset that never happened — or hide one
    that did. ``bool`` is rejected for the same reason ``_as_finite_float``
    rejects it: ``True`` was never a measured step.

    Accepts a plain ``int`` (the shape a checkpoint manifest's own ``step``
    field already has — wire-validated ``ge=0`` by
    ``flashruntime.service.checkpoints.CommitRequest``) and an all-digit
    ``str`` (the shape a step survives as if something upstream only
    extracted it from a filename). Anything else — a negative int, a float,
    or the non-numeric ``step-*.json`` filename fragment
    ``docs/superpowers/specs/2026-08-11-open-gaps.md`` §5 records occurring
    at runtime — is unusable.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and _STEP_RE.fullmatch(value.strip()):
        return int(value.strip())
    return None


def checkpoint_monotonicity_verdict(steps: list | None) -> tuple[str, dict]:
    """``('pass'|'flag'|'unknown', detail)`` for one task's relayed
    checkpoint steps, oldest first.

    ``steps`` is whatever the caller collected as the ordered sequence of
    step numbers the relay reported committing for this task, across
    however many attempts it took — see ``flashruntime.checkpoint
    .CheckpointCatalog.commit``'s ``step`` argument, the value this slice
    ultimately judges. Deliberately pure: it does not care where the list
    came from or how it was assembled, only whether it climbs.

    The rules, in the order they are applied:

    1. Fewer than two entries is ``unknown``. One step has no earlier step
       to be compared against, and zero has nothing at all — neither state
       is evidence of a healthy run OR a reset, and reporting one anyway
       would be exactly the tolerant-``pass``-on-no-data bug this layer
       forbids: a single "step 0" logged so far is not a silent restart, it
       may be all that has happened yet.
    2. Any entry that does not parse as a non-negative integer (see
       ``_as_step_int``) makes the WHOLE result ``unknown`` — not just that
       entry dropped. Order is the entire question this function answers,
       and a hole of unknown value in the middle of a sequence cannot be
       silently closed the way an unusable timing peer can be discarded:
       dropping it would silently re-derive an adjacency that was never
       actually observed, in either direction.
    3. Otherwise, walk the parsed sequence pairwise. Non-decreasing
       throughout (ties allowed — recommitting the same step is not a
       reset) is ``pass``. The first place a later step is LOWER than the
       one immediately before it is a silent reset — ``flag`` — most
       legibly back to 0, but any decrease qualifies: a resumed attempt
       does not get to pick an earlier point and have that read as forward
       progress.

    ``detail`` carries the parsed steps when there are any to carry, so a
    stored row can be re-read without re-trusting this function's own
    arithmetic.
    """
    count = len(steps) if steps else 0
    if count < 2:
        return "unknown", {"steps": count, "reason": "too_few_steps"}

    parsed = [_as_step_int(s) for s in steps]
    if any(p is None for p in parsed):
        return "unknown", {
            "steps": count,
            "unparseable": sum(1 for p in parsed if p is None),
            "reason": "unparseable_step",
        }

    usable: list[int] = [p for p in parsed if p is not None]
    for i in range(1, len(usable)):
        if usable[i] < usable[i - 1]:
            return "flag", {
                "steps": usable,
                "reset_index": i,
                "reset_from": usable[i - 1],
                "reset_to": usable[i],
                "reason": "step_decreased",
            }
    return "pass", {"steps": usable}
