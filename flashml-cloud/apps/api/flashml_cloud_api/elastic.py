"""The numbers a federated round derives from the fleet, not from its author.

``flashml.yaml`` used to ask for ``shards`` and ``min_participants`` — how
many pieces to cut the work into, and how many machines to wait for. Neither
is knowable by the person submitting: they cannot see the Crew, and the Crew
changes while their job runs. This module is where those two numbers come
from instead. Everything here is a function of who is online *now*, or of the
round index, and nothing here is a knob.

Spec: ``docs/superpowers/specs/2026-08-09-elastic-work-distribution-design.md``
§4.

**Two derivations here are copies.** ``run_fedavg`` computes a round's
coverage target and chunk offset inside its own loop and exports neither, so
``round_chunk_offset`` restates the arithmetic at
``flashml_workloads/fedavg_driver.py`` (``target = max(1, ceil(sync_every *
total_chunks))``, ``chunk_offset = (r * target) % total_chunks``), and
``flashml_yaml.derived_round_count`` restates its round count. The same is
true of ``flashml_workloads.chunks.slot_start``, except that one *is*
exported — so it is imported by ``compile.py`` rather than copied, and these
two should follow it the release the runtime exports them.

The copies are pinned two ways: ``tests/test_elastic_layout.py`` compares the
compiled layout against the runtime's own ``slot_start``, and
``tests/test_elastic_driver.py`` drives the real ``run_fedavg`` against a
coordinator that reports the chunks it was actually handed — which credits
nothing if this module and the driver disagree.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import ceil

#: The most task slots one round may open, however large the Crew.
#:
#: Every slot is a share of the round's coverage target, and an unclaimed
#: slot is a share nobody is working on — so the round cannot reach coverage
#: and waits for its duration backstop instead. Over-provisioning slots is
#: therefore not free, which is why this is a cap and not a multiplier.
#:
#: 100 also keeps the round an order of magnitude below the 999 the runtime's
#: federated service bounds a task count at, so this limit is always the one
#: that speaks first and it can say something useful when it does.
MAX_SLOTS = 100


@dataclass(frozen=True)
class FleetShape:
    """How one round is cut, given the machines online when it was submitted.

    ``total_chunks`` equals ``slots`` today: one chunk per slot, which is the
    split every shipped federated job already runs, so this change moves the
    *decision* off the submitter without changing what a machine does.

    Cutting a pass more finely than the fleet — the point of the elastic
    design, where a machine 13x faster completes 13 chunks and one that
    closes its lid loses only the chunk it held — additionally needs the
    round worker to walk a chunk *sequence* and report every id it finished
    (``metrics.json``'s ``chunks_done``). For the built-in worker that landed
    with the runtime; for a user's own entrypoint it is a change to the
    documented federated contract, and it is not in this slice.
    """

    slots: int
    total_chunks: int
    expected_machines: int


def fleet_shape(machines_online: int) -> FleetShape:
    """One slot per online machine, floored at 1 and capped at ``MAX_SLOTS``.

    Not "machines plus headroom": headroom costs a round its coverage (see
    ``MAX_SLOTS``) for as long as the extra slots go unclaimed. Headroom
    becomes free once a slot can carry several chunks, because then the
    machines that *are* present absorb what the absent ones would have done.

    The floor exists because a Crew is often entirely asleep at submit time.
    A one-slot round is a real round: a machine that wakes up later claims
    it, and the job runs slowly rather than not at all.
    """
    if machines_online < 0:
        raise ValueError(
            f"machines_online must be >= 0, got {machines_online}"
        )
    slots = min(max(1, machines_online), MAX_SLOTS)
    return FleetShape(slots=slots, total_chunks=slots,
                      expected_machines=slots)


def round_coverage_target(total_chunks: int, sync_every: float) -> int:
    """Distinct chunks a round must cover before the machines combine.

    Mirrors ``run_fedavg``'s ``target``. At the default ``sync_every`` of 1.0
    this is every chunk in the pass, which is what a round means today.
    """
    return max(1, ceil(sync_every * total_chunks))


def round_chunk_offset(round_index: int, *, total_chunks: int,
                       sync_every: float) -> int:
    """Where round ``round_index``'s slot rotations begin.

    Mirrors ``run_fedavg``'s ``chunk_offset``: round r starts where round
    r-1's coverage target ended, so consecutive rounds sweep forward through
    the data. Without it — and with ``sync_every`` below 1.0, where a round
    stops before a full pass — every round would retrain the same prefix and
    the tail of the data would never be trained at all.

    The advance is modular, so offsets cycle once per pass and the next epoch
    reuses them. That is deliberate, not a wrap-around bug.
    """
    target = round_coverage_target(total_chunks, sync_every)
    return (round_index * target) % total_chunks
