"""`cost_worksheet.py` — the D-9 arithmetic and its attribution, offline.

    flashml-cloud/apps/api/.venv/bin/python -m pytest \
        flashml-cloud/scripts/competition/test_cost_worksheet.py -q

**No Alibaba, ever.** Nothing here creates a sandbox or touches a real
`.evidence/` file beyond reading one fixture-shaped dict in memory. What is
tested:

* **§7.3 reproduces exactly.** `disk_shrink_lines` must hit the published
  table's two figures (0.012120 / 0.007336 $/hr) to the last decimal and land
  the delta at ~39.5%, the same numbers `test_hibernation_modes_probe.py`
  already pins for `hourly_rates` itself — this only checks that the
  worksheet's own wrapper doesn't lose precision on the way to a `Line`.
* **Attribution.** Every measured line names the evidence file it came from;
  no estimate line is ever allowed to say "measured" in its own source.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "cost_worksheet", HERE / "cost_worksheet.py"
    )
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: `@dataclass` looks its class's module up in
    # `sys.modules` and raises an unrelated AttributeError if it is not there.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


worksheet = _load()


# ---------------------------------------------------------------------------
# the four tests from the plan
# ---------------------------------------------------------------------------


def test_disk_shrink_reproduces_the_published_table():
    lines = worksheet.disk_shrink_lines(mem_gib=4.0)
    by = {l.label: l for l in lines}
    # §7.3: local 38 GiB hibernated disk -> 0.012120; OSS 23 GiB -> 0.007336;
    # ~39.5% cheaper.
    assert round(by["local_30gib_hibernated"].value, 6) == 0.012120
    assert round(by["oss_15gib_hibernated"].value, 6) == 0.007336
    assert 39.0 <= by["disk_shrink_pct"].value <= 40.0
    assert by["disk_shrink_pct"].kind == "derived"


def test_a_measured_line_names_its_evidence_file():
    ev = {"report": {"cost": {"active_hourly": 0.08}, "summary": {}, "cells": []}}
    lines = worksheet.hibernation_cost_lines(
        ev, "alibaba-hibernation-modes-20260813T041325Z.json"
    )
    active = next(l for l in lines if l.label == "active_hourly")
    assert active.kind == "measured"
    assert "20260813T041325Z" in active.source


def test_no_estimate_is_labelled_measured():
    lines = worksheet.disk_shrink_lines(4.0) + worksheet.hibernation_cost_lines(
        {"report": {"cost": {}, "summary": {}, "cells": []}}, "f.json"
    )
    for l in lines:
        if l.kind == "estimate":
            assert "measured" not in l.source.lower()


def test_render_markdown_has_a_source_and_kind_column():
    lines = worksheet.disk_shrink_lines(4.0)
    table = worksheet.render_markdown(lines)
    header = table.splitlines()[0]
    assert "Source" in header
    assert "Kind" in header
    for l in lines:
        assert l.label in table
        assert l.source in table
        assert l.kind in table


# ---------------------------------------------------------------------------
# additional coverage: attribution of the real evidence shape, and the loader
# ---------------------------------------------------------------------------


#: A slice of the real shape `hibernation_modes_probe.py`'s `cost_section`
#: produces (see `.evidence/alibaba-hibernation-modes-20260813T041325Z.json`),
#: trimmed to what `hibernation_cost_lines` actually reads.
REALISTIC_EVIDENCE = {
    "report": {
        "cost": {
            "status": "MODELLED from published rates — not billed.",
            "measured_sandbox_shape": {"vcpu": 2.0, "memory_gib": 2.041, "disk_gib": 9.809},
            "per_state": {
                "active": {"rate_usd_per_hour": 0.05654376,
                          "cost_of_measured_hibernated_time_usd": 0.02026219},
                "deep_hibernation": {"rate_usd_per_hour": 0.00351553,
                                     "cost_of_measured_hibernated_time_usd": 0.00125978},
            },
            "baseline": {
                "measured_hibernated_seconds": 1290.0,
                "active_cost_usd": 0.02026219,
                "deep_hibernation_cost_usd": 0.00125978,
                "saving_vs_active_deep_pct": 93.78,
            },
        },
        "summary": {
            "wake_ms_by_mode": {
                "keep_memory=True": {"n": 5, "p50_ms": 1109.0},
                "keep_memory=False": {"n": 5, "p50_ms": 1222.3},
            },
        },
        "cells": [
            {"name": "snapshot/long", "create_ms": 1618.9},
            {"name": "nomemory/long", "create_ms": 1500.1},
        ],
    }
}


def test_realistic_evidence_yields_attributed_measured_lines():
    lines = worksheet.hibernation_cost_lines(REALISTIC_EVIDENCE, "real.json")
    by = {l.label: l for l in lines}

    assert by["deep_hibernation_rate_usd_per_hour"].value == 0.00351553
    assert by["deep_hibernation_rate_usd_per_hour"].unit == "USD/hr"
    assert by["baseline_saving_vs_active_deep_pct"].value == 93.78
    assert by["sandbox_memory_gib"].value == 2.041
    assert by["wake_p50_ms_keep_memory_True"].value == 1109.0
    assert by["mean_sandbox_create_ms"].value == round((1618.9 + 1500.1) / 2, 1)

    for line in lines:
        assert line.kind == "measured"
        assert "real.json" in line.source


def test_hibernation_cost_lines_is_defensive_about_missing_shape():
    assert worksheet.hibernation_cost_lines({}, "f.json") == []
    assert worksheet.hibernation_cost_lines({"report": "not-a-dict"}, "f.json") == []
    assert worksheet.hibernation_cost_lines(
        {"report": {"cost": "not-a-dict"}}, "f.json"
    ) == []


def test_load_latest_evidence_picks_the_lexicographically_newest(tmp_path):
    older = tmp_path / "alibaba-hibernation-modes-20260812T000000Z.json"
    newer = tmp_path / "alibaba-hibernation-modes-20260813T041325Z.json"
    older.write_text('{"report": {"cost": {"marker": 1}}}')
    newer.write_text('{"report": {"cost": {"marker": 2}}}')

    filename, evidence = worksheet.load_latest_evidence(tmp_path)

    assert filename == newer.name
    assert evidence["report"]["cost"]["marker"] == 2


def test_load_latest_evidence_raises_when_nothing_is_there(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        worksheet.load_latest_evidence(tmp_path)


def test_hourly_rates_is_the_reused_function_not_a_copy():
    """`cost_worksheet.hourly_rates` must BE `hibernation_modes_probe.hourly_
    rates` — imported, not re-derived — so there is nothing to drift."""
    probe = worksheet._hibernation_probe
    assert worksheet.hourly_rates is probe.hourly_rates
