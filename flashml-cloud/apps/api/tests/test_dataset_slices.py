"""Cutting a manifest into per-task slices.

Byte-weighted, not count-weighted: real shards are uneven, and dividing by
file count hands one host 10x the bytes and stalls every round on that
straggler.

Contiguous, not strided: a contiguous range is what makes a host's cache
useful across rounds, and `round_chunk_offset` already sweeps the window
forward so the fleet covers an epoch rather than retraining a prefix.

`dataset_chunks` is TOTAL — it has no error case at all. A manifest arrives
from a third party's file listing, so every degenerate shape it can take (no
files, empty files, more chunks than files) has to be a layout rather than an
exception raised half-way through a submit. The tests below pin each of those
shapes to a specific answer, because "does not crash" is not a contract.
"""

from __future__ import annotations

import random

import pytest

from flashml_cloud_api.elastic import (
    cap_chunks_to_manifest,
    dataset_chunks,
    effective_width,
)


def _cover(slices, n):
    return sorted(i for s in slices for i in s) == list(range(n))


def _contiguous(slices):
    """Every non-empty slice is an unbroken run, and the runs ascend."""
    seen: list[int] = [i for s in slices for i in s]
    for s in slices:
        if s and s != list(range(s[0], s[-1] + 1)):
            return False
    return seen == sorted(seen)


@pytest.mark.parametrize("chunks", [1, 2, 3, 4, 7, 16])
def test_every_file_is_assigned_exactly_once(chunks):
    sizes = [10, 90, 40, 60, 5]
    assert _cover(dataset_chunks(sizes, chunks), len(sizes))


def test_slices_are_contiguous():
    sizes = [10, 90, 40, 60, 5, 100]
    for s in dataset_chunks(sizes, 3):
        assert s == list(range(s[0], s[-1] + 1)) if s else True


def test_the_split_balances_bytes_not_file_count():
    """One huge file and three tiny ones: a count-weighted split would put
    two files with the giant one."""
    sizes = [1000, 1, 1, 1]
    slices = dataset_chunks(sizes, 2)
    loads = [sum(sizes[i] for i in s) for s in slices]
    assert loads[0] == 1000 and loads[1] == 3


def test_more_chunks_than_files_leaves_empty_chunks():
    """Documented, not prevented, here — the CAP is what prevents it, and
    it is applied by the caller so this function stays total."""
    slices = dataset_chunks([1, 2, 3], 7)
    assert _cover(slices, 3)
    assert sum(1 for s in slices if not s) == 4


def test_an_empty_manifest_is_not_a_crash():
    assert dataset_chunks([], 3) == [[], [], []]


def test_zero_byte_files_still_get_assigned():
    """A manifest of empty files has total 0 — the divisor must not be it."""
    assert _cover(dataset_chunks([0, 0, 0], 2), 3)


# ---------------------------------------------------------------------------
# the degenerate chunk counts, which a caller reaches by arithmetic rather
# than by typing them
# ---------------------------------------------------------------------------


def test_no_chunks_asked_for_is_no_chunks_returned():
    """The length invariant is the contract: exactly ``max(0, chunks)``
    lists, always. A round with no slots is the caller's bug to notice, and
    it notices it by zipping this against its own empty task list — which
    only works if a floor is NOT applied here."""
    assert dataset_chunks([1, 2, 3], 0) == []


def test_a_negative_chunk_count_is_clamped_rather_than_an_IndexError():
    assert dataset_chunks([1, 2, 3], -4) == []


def test_zero_byte_files_into_more_chunks_than_files():
    """Both degenerate paths at once: the ``total == 0`` fallback must obey
    the same bounds as the weighted one."""
    slices = dataset_chunks([0, 0], 5)
    assert len(slices) == 5
    assert _cover(slices, 2)
    assert _contiguous(slices)


def test_one_file_lands_in_one_chunk():
    slices = dataset_chunks([700], 4)
    assert _cover(slices, 1)
    assert sum(1 for s in slices if s) == 1


def test_a_size_that_is_not_a_float_is_still_placed():
    """Real manifests carry byte counts past 2**53, where the weighting's
    float division stops being exact. Exactness of the *split* is a nicety;
    losing a file is not."""
    sizes = [2**62, 1, 2**62]
    assert _cover(dataset_chunks(sizes, 3), 3)


def test_the_partition_holds_over_many_random_shapes():
    """The three properties together, on shapes nobody would think to type:
    exact partition, contiguous runs, and one list per requested chunk."""
    rng = random.Random(20260811)
    for _ in range(400):
        n = rng.randrange(0, 12)
        sizes = [rng.choice([0, 1, 7, 1024, 10**9]) for _ in range(n)]
        chunks = rng.randrange(1, 10)
        slices = dataset_chunks(sizes, chunks)
        assert len(slices) == chunks, (sizes, chunks)
        assert _cover(slices, n), (sizes, chunks, slices)
        assert _contiguous(slices), (sizes, chunks, slices)


# ---------------------------------------------------------------------------
# the cap
# ---------------------------------------------------------------------------


def test_the_cap_reduces_chunks_to_the_shard_count_and_explains():
    capped, warning = cap_chunks_to_manifest(20, 3)
    assert capped == 3
    assert warning is not None
    assert "3" in warning and "20" in warning


def test_no_cap_and_no_warning_when_the_data_is_fine_grained_enough():
    assert cap_chunks_to_manifest(3, 512) == (3, None)


def test_the_cap_never_returns_zero():
    """A one-slot round is a real round; a zero-slot round is not."""
    capped, _ = cap_chunks_to_manifest(4, 0)
    assert capped == 1


def test_an_empty_manifest_still_warns_that_the_fleet_is_idle():
    """Capping four machines down to one is exactly the surprise the
    warning exists for, so the floor must not swallow it."""
    _, warning = cap_chunks_to_manifest(4, 0)
    assert warning is not None


def test_an_equal_shard_count_is_not_a_cap_and_not_a_warning():
    """The boundary: three shards across three machines uses the whole
    fleet, and warning about it would train people to ignore the warning."""
    assert cap_chunks_to_manifest(3, 3) == (3, None)


def test_a_meaningless_round_size_is_floored_without_a_warning():
    """``total_chunks <= 0`` is not the dataset's fault, so it gets the
    floor and no finger pointed at the manifest."""
    assert cap_chunks_to_manifest(0, 8) == (1, None)
    assert cap_chunks_to_manifest(-2, 8) == (1, None)


# ---------------------------------------------------------------------------
# effective_width — the count-based cap is a leaky proxy
# ---------------------------------------------------------------------------


def test_a_skewed_manifest_strands_machines_the_count_cap_would_allow():
    """Five files, five chunks, and still only three machines get work.

    Byte-weighting assigns by byte POSITION, so one dominant file
    monopolises the middle of the range. `cap_chunks_to_manifest(5, 5)`
    sees file_count == chunks and says "fine" — it is comparing the wrong
    two numbers. An empty chunk is a machine whose gradient does not
    exist, which FedAvg then averages in.
    """
    sizes = [300, 100, 4200, 50, 900]
    assert cap_chunks_to_manifest(5, len(sizes)) == (5, None), "the proxy says fine"
    assert effective_width(sizes, 5) == 3, "but only three chunks get files"


def test_an_even_manifest_uses_the_whole_width():
    assert effective_width([100] * 5, 5) == 5


def test_effective_width_never_exceeds_the_file_count():
    assert effective_width([1, 2, 3], 7) == 3


def test_an_empty_manifest_has_no_width():
    assert effective_width([], 4) == 0


def test_effective_width_is_zero_for_a_nonsense_round():
    assert effective_width([1, 2, 3], 0) == 0
