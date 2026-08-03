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
