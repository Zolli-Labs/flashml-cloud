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

from collections.abc import Sequence
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


def dataset_chunks(sizes: Sequence[int], chunks: int) -> list[list[int]]:
    """Manifest indices per chunk: contiguous runs split on cumulative BYTES.

    Byte-weighted rather than count-weighted because real shards are
    uneven — dividing 512 files evenly by count hands whoever gets the big
    ones several times the work, and a round waits for its slowest slot.

    Contiguous rather than strided because a contiguous range is what makes
    a host's cache worth having across rounds; ``round_chunk_offset``
    already moves the window so the fleet still covers a whole pass.

    Total, never raising. The sizes come from a stranger's file listing —
    ``datasets.resolve`` reports whatever the origin says, including a
    manifest of empty files — and a submit that dies part-way through
    cutting slices would refuse a job for a shape that has a perfectly good
    layout. Two invariants hold for every input:

    * the result is exactly ``max(0, chunks)`` lists long, so a caller may
      zip it against its own task list without checking anything;
    * every index in ``sizes`` appears in exactly one of them — unless the
      caller asked for no chunks at all, which is the one case where there
      is nowhere to put them.

    That second exception is why no floor of 1 is applied here: a caller
    that computed zero slots has a zero-length task list, and handing it one
    chunk anyway turns its own ``enumerate`` into an ``IndexError`` at a
    point where the manifest is no longer in scope. ``fleet_shape`` and
    ``cap_chunks_to_manifest`` are where the floor belongs.

    More chunks than files yields empty chunks, which is a real (if
    wasteful) layout rather than an error. ``cap_chunks_to_manifest`` is
    what prevents it, and it is the caller's decision because only the
    caller knows whether the split is ``shard`` or ``replica``.
    """
    out: list[list[int]] = [[] for _ in range(max(0, chunks))]
    if not out or not sizes:
        return out
    total = sum(sizes)
    if total <= 0:
        # Every file is empty (or a listing reported nonsense): weighting by
        # bytes would divide by zero, so fall back to position. Nothing is
        # dropped, and the layout is still contiguous.
        for index in range(len(sizes)):
            out[min(index * chunks // len(sizes), chunks - 1)].append(index)
        return out
    cursor = 0
    running = 0
    for index, size in enumerate(sizes):
        # The file's MIDPOINT decides its chunk, not its start: weighing a
        # file by where it begins pushes every boundary file one chunk
        # early and leaves the last chunk short.
        midpoint = running + size / 2
        target = min(int(midpoint * chunks / total), chunks - 1)
        # Contiguity, and the reason a float divide is safe here: the cursor
        # never steps backwards, so neither a rounding wobble nor a negative
        # size a listing invented can interleave two chunks or index ``out``
        # from the wrong end.
        cursor = max(cursor, target)
        out[cursor].append(index)
        running += size
    return out


def cap_chunks_to_manifest(
    total_chunks: int, shard_count: int
) -> tuple[int, str | None]:
    """Bound a round's chunk count by the data's own granularity.

    ``flashml.yaml`` used to ask for ``shards`` and the answer was always
    wrong — "eleven machines online with shards: 3 left eight of them doing
    nothing" is why the knob was removed. A dataset reintroduces the same
    fixed number through the back door: it has however many shards it has.

    So the number is capped rather than obeyed, and the caller is TOLD.
    Silently running a 3-machine round on a 20-machine pool is the exact
    complaint that killed the knob; the difference now is that it is
    visible. A warning and not a refusal: a small dataset during development
    is a legitimate thing to run.

    The floor is 1 on both arguments, for two different reasons. A manifest
    of no files caps to one slot rather than none, because a zero-slot round
    is not a round — ``fleet_shape`` makes the same call about an empty
    Crew. A ``total_chunks`` of zero or less is not the dataset's fault, so
    it is floored silently: the warning names the manifest, and pointing at
    the manifest for the caller's arithmetic would be a lie.
    """
    capped = max(1, min(total_chunks, shard_count))
    if total_chunks < 1 or shard_count >= total_chunks:
        return capped, None
    return capped, (
        f"this dataset has {shard_count} shard(s), so at most {capped} "
        f"of the {total_chunks} machines available can work on it. Split the "
        f"dataset into more files to use the whole fleet."
    )


def effective_width(sizes: Sequence[int], chunks: int) -> int:
    """How many chunks actually receive at least one file.

    ``cap_chunks_to_manifest`` bounds a round by the manifest's FILE COUNT,
    which is a proxy — and a leaky one. Byte-weighting assigns by byte
    position, so a single dominant file can monopolise the middle of the
    range and leave chunks empty even when there are as many files as
    chunks: five files of ``[300, 100, 4200, 50, 900]`` over five chunks
    fills three and strands two.

    An empty chunk is a machine that fetches nothing, trains nothing and
    reports nothing — and FedAvg then averages over a member whose gradient
    does not exist. That is not a smaller round, it is a different
    experiment, and it is the complaint that killed the ``shards:`` knob.

    So the width a round can actually use is measured after the cut, not
    predicted from a count before it.
    """
    return sum(1 for group in dataset_chunks(sizes, chunks) if group)
