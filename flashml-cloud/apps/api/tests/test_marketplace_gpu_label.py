"""``machine_gpu_label`` — the machine-picker spec line, and nothing it did
not read.

Pure function, no database: every case here is a claim about what a reader
sees for a given ``capabilities`` snapshot, and the central rule is the one
the module docstring states for its sibling ``capability_class`` — refuse
rather than guess. A label invented from an unreadable shape is worse than
no label, because a host has no way to notice the difference except from
what it implies about their hardware.
"""
from __future__ import annotations

from flashml_cloud_api import marketplace as mk

#: A 24GB card as a driver actually reports it — 24564 MiB, not 24576 and not
#: 24000 — the same real reading `test_marketplace.py` uses, so a rounding
#: bug would have to be wrong on both files to hide.
RTX_4090 = {"index": 0, "name": "NVIDIA GeForce RTX 4090", "memory_total_mb": 24564,
            "compute_capability": "8.9"}
RTX_3070 = {"index": 0, "name": "NVIDIA GeForce RTX 3070", "memory_total_mb": 8192,
            "compute_capability": "8.6"}


def test_a_single_named_gpu_reports_its_name_and_rounded_vram_with_no_count():
    """One card needs no "1 GPU" suffix to be unambiguous, and the number is
    display-only: 24564 MiB rounds to 24 GB, but the class ladder underneath
    still reads the exact MiB — this label never feeds it."""
    label = mk.machine_gpu_label({"gpus": [RTX_4090]})
    assert label == "NVIDIA GeForce RTX 4090 · 24 GB"


def test_a_multi_gpu_rig_is_named_by_its_smallest_card_and_carries_a_count():
    """Two cards CAN promise only their smaller member to a placed task
    (`GpuInfo`'s docstring: placement matches on count alone), so the label
    that would mislead a reader is the one naming the bigger card."""
    label = mk.machine_gpu_label({"gpus": [RTX_4090, RTX_3070]})
    assert label == "NVIDIA GeForce RTX 4090 · 8 GB · 2 GPU"


def test_an_unnamed_gpu_falls_back_to_a_generic_device_word_not_a_blank():
    """A driver that reports memory but no name still describes something
    real; the placeholder says so instead of leaving the line half empty."""
    label = mk.machine_gpu_label(
        {"gpus": [{"index": 0, "memory_total_mb": 24564}]}
    )
    assert label == "GPU · 24 GB"


def test_unreadable_gpu_memory_refuses_the_label_same_direction_as_the_ladder():
    """`capability_class` returns None for exactly this shape rather than
    filing the machine as CPU-only at a tenth of its worth; the spec line
    must refuse for the identical reason — a plausible-looking label here is
    a promise about VRAM made from no reading at all."""
    assert mk.machine_gpu_label(
        {"gpus": [{"index": 0, "name": "x", "memory_total_mb": None}]}
    ) is None
    # One readable card beside one unreadable one is still a refusal: the
    # label can only promise what EVERY card on the machine can back up.
    assert mk.machine_gpu_label({"gpus": [RTX_4090, {"index": 1, "name": "y"}]}) is None


def test_a_cpu_only_machine_reports_cores_alone_when_ram_was_never_reported():
    """No `memory_bytes` key is the ordinary case for a registration that
    predates the field; the line must still say the one true thing it has —
    the core count — rather than showing nothing at all."""
    assert mk.machine_gpu_label({"cpu_cores": 8}) == "8 cores"


def test_a_cpu_only_machine_appends_rounded_system_ram_when_it_was_reported():
    """The spec's own example is `'8 cores · 16 GB'`: RAM is a real
    `NodeCapabilities.memory_bytes` field and belongs on the line exactly
    like VRAM does for a GPU machine, rounded the same way — bytes to whole
    GiB, display-only."""
    label = mk.machine_gpu_label({"cpu_cores": 8, "memory_bytes": 16 * 1024**3})
    assert label == "8 cores · 16 GB"


def test_ram_that_did_not_round_to_a_whole_gib_still_rounds_for_display_only():
    """15.5 GiB is a real reading from a real machine (`test_nodes.py` uses
    exactly this shape); rounding it for the spec line is a display fact,
    never a promise fed back into a class or a price."""
    label = mk.machine_gpu_label(
        {"cpu_cores": 4, "memory_bytes": int(15.5 * 1024**3)}
    )
    assert label == "4 cores · 16 GB"


def test_a_non_numeric_or_boolean_ram_reading_is_dropped_not_fabricated():
    """`bool` is an `int` in Python, so `True` would otherwise print as
    `1 GB` of RAM — the exact trap this module refuses elsewhere for a float
    price. The core count still renders; only the fabricated half is cut."""
    assert mk.machine_gpu_label({"cpu_cores": 8, "memory_bytes": True}) == "8 cores"
    assert mk.machine_gpu_label({"cpu_cores": 8, "memory_bytes": "16gb"}) == "8 cores"


def test_capabilities_with_no_gpus_and_no_cpu_cores_field_has_no_label():
    """Not the same as `capability_class`, which under-claims an unknown
    core count to `cpu-small` — a SPEC LINE with nothing to report must say
    nothing rather than assert a number nobody supplied."""
    assert mk.machine_gpu_label({}) is None
    assert mk.machine_gpu_label({"gpus": []}) is None


def test_a_non_mapping_capabilities_value_is_unrecognised_not_a_crash():
    """`capabilities` is untrusted jsonb read back from Postgres; a stray
    list, string or None must answer None, the same refusal as an
    unreadable shape, rather than raising out of a route."""
    assert mk.machine_gpu_label(None) is None
    assert mk.machine_gpu_label([]) is None
    assert mk.machine_gpu_label("gpu") is None
