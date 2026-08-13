#!/usr/bin/env python3
"""D-9: the cost worksheet — every figure names its source, unit, and kind.

Parent: `docs/superpowers/specs/2026-08-11-competition-requirements.md` §7.3
(the OSS disk-shrink table) and §8 (a cost worksheet artifact). This is a
worksheet, not a new measurement: it reads two things that already exist and
renders them as one attributed table.

    1. §7.3's disk-shrink arithmetic — "environment stays, data streams" is a
       cost decision, not a slogan. Deep hibernation bills
       ``memory x 2 + disk`` with no free allowance, so moving the model and
       dataset off the sandbox's own disk (30 GiB -> 15 GiB, at 4 GiB memory)
       measurably shrinks the hibernation bill. `disk_shrink_lines` computes
       both figures and their percentage delta.

    2. Whatever `hibernation_modes_probe.py` already measured, in the newest
       `.evidence/alibaba-hibernation-modes-*.json` it wrote. `hibernation_
       cost_lines` reads it and labels every figure it finds ``measured``,
       naming the evidence file in `source` so nobody has to take it on
       trust.

**Rates are reused, not re-derived.** `hibernation_modes_probe.py` already
carries a tested `hourly_rates(vcpu=, memory_gib=, disk_gib=, mainland=)`
reproducing the published-rate table (doc 3045213) to the last decimal,
including the deep-hibernation `memory x 2 + disk` rule and the fact that the
15 GiB free disk allowance does NOT apply to deep hibernation. This module
loads that exact function via `importlib` — the same pattern
`test_hibernation_modes_probe.py` already uses to load the module for its own
tests, confirming it is safe: `hibernation_modes_probe.py` imports nothing but
the standard library at module scope (the E2B SDK is imported lazily, inside
`main()`), so loading it here needs no sandbox, no key, and no network.
Because this is an import of the real function rather than a copy, there is
nothing that could drift.

**Honesty rule (house style):** a `Line.kind` of ``"estimate"`` must never
carry the word "measured" in its `source`. This module currently emits only
``"published"``, ``"derived"``, and ``"measured"`` lines — no assumption is
guessed at — but the rule is enforced by a test in case that ever changes.

Usage
-----
    python cost_worksheet.py

Reads the newest hibernation-modes evidence (if any) from `../../.evidence/`
and writes `../../.evidence/cost-worksheet-<stamp>.md` beside it.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
#: Same depth as `hibernation_modes_probe.py`'s own default evidence dir.
EVIDENCE_DIR = Path(__file__).resolve().parents[2] / ".evidence"
EVIDENCE_GLOB = "alibaba-hibernation-modes-*.json"


def _load_module(name: str, path: Path):
    """`importlib.util.spec_from_file_location`, registered before exec.

    Copied from `test_hibernation_modes_probe.py`'s `_load()`: these are
    standalone scripts, not a package, so there is no `import
    hibernation_modes_probe` to reach for. Registering in `sys.modules`
    before `exec_module` matters because `@dataclass` looks its class's
    module up there and raises an unrelated `AttributeError` if it is absent.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


#: Loaded once, at import time. Module-scope code in `hibernation_modes_probe`
#: is stdlib-only (constants, dataclasses, pure functions) — nothing here
#: needs the E2B SDK, a key, or a sandbox.
_hibernation_probe = _load_module("hibernation_modes_probe", HERE / "hibernation_modes_probe.py")
hourly_rates = _hibernation_probe.hourly_rates


# ---------------------------------------------------------------------------
# The line item
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    label: str
    value: float
    unit: str          # "USD/hr", "USD", "s", "ms", "GiB", "pct", "ratio", ...
    source: str         # "measured: <evidence-file>#report.cost..." | "published: doc 3045213" | "derived: ..."
    kind: str           # "measured" | "published" | "estimate" | "derived"


# ---------------------------------------------------------------------------
# §7.3 — the disk-shrink table
# ---------------------------------------------------------------------------

#: The dossier's own worked shape (§7.3): a 2 vCPU / 4 GiB evaluator. vCPU
#: does not affect the deep-hibernation rate at all — `hourly_rates` bills
#: deep hibernation as `memory x 2 + disk` only, no vCPU term — but the
#: function requires one, so this pins the dossier's own assumed value rather
#: than an arbitrary one.
DISK_SHRINK_VCPU = 2.0
DISK_SHRINK_MEM_GIB = 4.0
#: Model + dataset held on the sandbox's own disk.
DISK_SHRINK_LOCAL_DISK_GIB = 30.0
#: The same evaluator with model + dataset moved to OSS.
DISK_SHRINK_OSS_DISK_GIB = 15.0


def disk_shrink_lines(mem_gib: float) -> list[Line]:
    """§7.3: local-disk vs. OSS-backed deep-hibernation rate, and the delta.

    Both rates come straight out of the reused, tested `hourly_rates` — this
    function does no rate arithmetic of its own, only the percentage delta
    between two of its outputs.
    """
    local = hourly_rates(
        vcpu=DISK_SHRINK_VCPU, memory_gib=mem_gib,
        disk_gib=DISK_SHRINK_LOCAL_DISK_GIB, mainland=True,
    )
    oss = hourly_rates(
        vcpu=DISK_SHRINK_VCPU, memory_gib=mem_gib,
        disk_gib=DISK_SHRINK_OSS_DISK_GIB, mainland=True,
    )
    local_deep = local["deep_hibernation"]
    oss_deep = oss["deep_hibernation"]
    pct = 100.0 * (1.0 - oss_deep / local_deep) if local_deep else 0.0

    return [
        Line(
            label="local_30gib_hibernated",
            value=round(local_deep, 6),
            unit="USD/hr",
            source=(
                f"published: doc 3045213 hourly_rates(deep_hibernation, "
                f"mem={mem_gib:g}GiB, disk={DISK_SHRINK_LOCAL_DISK_GIB:g}GiB, "
                f"mainland) — model+dataset held on the sandbox's own disk"
            ),
            kind="published",
        ),
        Line(
            label="oss_15gib_hibernated",
            value=round(oss_deep, 6),
            unit="USD/hr",
            source=(
                f"published: doc 3045213 hourly_rates(deep_hibernation, "
                f"mem={mem_gib:g}GiB, disk={DISK_SHRINK_OSS_DISK_GIB:g}GiB, "
                f"mainland) — model+dataset moved to OSS"
            ),
            kind="published",
        ),
        Line(
            label="disk_shrink_pct",
            value=round(pct, 2),
            unit="pct",
            source=(
                "derived: 100 * (1 - oss_15gib_hibernated / local_30gib_hibernated)"
            ),
            kind="derived",
        ),
    ]


# ---------------------------------------------------------------------------
# Evidence — the latest hibernation_modes_probe.py run
# ---------------------------------------------------------------------------


def load_latest_evidence(evidence_dir: Path) -> tuple[str, dict]:
    """The newest `alibaba-hibernation-modes-*.json`, by filename.

    The `%Y%m%dT%H%M%SZ` stamp in the filename sorts chronologically, so a
    plain lexicographic sort of the filenames is the newest-first order —
    `hibernation_modes_probe.py`'s own stamp convention.
    """
    candidates = sorted(evidence_dir.glob(EVIDENCE_GLOB))
    if not candidates:
        raise FileNotFoundError(
            f"no {EVIDENCE_GLOB} evidence file found in {evidence_dir}"
        )
    latest = candidates[-1]
    return latest.name, json.loads(latest.read_text())


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _unit_for(label: str) -> str:
    lowered = label.lower()
    if lowered.endswith("_hourly") or lowered.endswith("rate_usd_per_hour") or "rate_usd_per_hour" in lowered:
        return "USD/hr"
    if lowered.endswith("_usd") or "cost_usd" in lowered:
        return "USD"
    if lowered.endswith("_pct"):
        return "pct"
    if lowered.endswith("_ms"):
        return "ms"
    if lowered.endswith("_seconds") or lowered.endswith("_s"):
        return "s"
    if lowered.endswith("_gib") or "gib" in lowered:
        return "GiB"
    if lowered == "vcpu":
        return "vCPU"
    return "value"


def _cost_top_level_lines(cost: dict, filename: str) -> list[Line]:
    """Any scalar directly on `report.cost` (e.g. a future `active_hourly`).

    Generic on purpose: `report.cost` is produced by
    `hibernation_modes_probe.py`'s `cost_section`, which is free to grow new
    top-level scalar fields. This walks whatever is there rather than
    hard-coding a field list that would silently stop attributing a new one.
    """
    lines: list[Line] = []
    for key, value in cost.items():
        if _is_number(value):
            lines.append(Line(
                label=key,
                value=float(value),
                unit=_unit_for(key),
                source=f"measured: {filename}#report.cost.{key}",
                kind="measured",
            ))
    return lines


def _cost_per_state_lines(cost: dict, filename: str) -> list[Line]:
    per_state = cost.get("per_state")
    if not isinstance(per_state, dict):
        return []
    lines: list[Line] = []
    for state, info in per_state.items():
        if not isinstance(info, dict):
            continue
        rate = info.get("rate_usd_per_hour")
        if _is_number(rate):
            lines.append(Line(
                label=f"{state}_rate_usd_per_hour",
                value=float(rate),
                unit="USD/hr",
                source=f"measured: {filename}#report.cost.per_state.{state}.rate_usd_per_hour",
                kind="measured",
            ))
        hib_cost = info.get("cost_of_measured_hibernated_time_usd")
        if _is_number(hib_cost):
            lines.append(Line(
                label=f"{state}_hibernated_time_cost_usd",
                value=float(hib_cost),
                unit="USD",
                source=(
                    f"measured: {filename}#report.cost.per_state.{state}"
                    ".cost_of_measured_hibernated_time_usd"
                ),
                kind="measured",
            ))
    return lines


#: Baseline-A fields worth naming individually rather than dropped generically.
_BASELINE_FIELDS = (
    "measured_hibernated_seconds", "active_cost_usd", "deep_hibernation_cost_usd",
    "light_hibernation_cost_usd", "saving_vs_active_deep_pct", "saving_vs_active_light_pct",
)


def _cost_baseline_lines(cost: dict, filename: str) -> list[Line]:
    baseline = cost.get("baseline")
    if not isinstance(baseline, dict):
        return []
    lines: list[Line] = []
    for key in _BASELINE_FIELDS:
        value = baseline.get(key)
        if _is_number(value):
            lines.append(Line(
                label=f"baseline_{key}",
                value=float(value),
                unit=_unit_for(key),
                source=f"measured: {filename}#report.cost.baseline.{key}",
                kind="measured",
            ))
    return lines


def _sandbox_shape_lines(cost: dict, filename: str) -> list[Line]:
    shape = cost.get("measured_sandbox_shape")
    if not isinstance(shape, dict):
        return []
    lines: list[Line] = []
    for key in ("vcpu", "memory_gib", "disk_gib"):
        value = shape.get(key)
        if _is_number(value):
            lines.append(Line(
                label=f"sandbox_{key}",
                value=float(value),
                unit=_unit_for(key),
                source=f"measured: {filename}#report.cost.measured_sandbox_shape.{key}",
                kind="measured",
            ))
    return lines


def _summary_wake_lines(summary: dict, filename: str) -> list[Line]:
    by_mode = summary.get("wake_ms_by_mode") if isinstance(summary, dict) else None
    if not isinstance(by_mode, dict):
        return []
    lines: list[Line] = []
    for mode_label, mode_stats in by_mode.items():
        if not isinstance(mode_stats, dict):
            continue
        p50 = mode_stats.get("p50_ms")
        if _is_number(p50):
            safe = mode_label.replace("=", "_").replace(" ", "_")
            lines.append(Line(
                label=f"wake_p50_ms_{safe}",
                value=float(p50),
                unit="ms",
                source=(
                    f"measured: {filename}#report.summary.wake_ms_by_mode."
                    f"{mode_label}.p50_ms"
                ),
                kind="measured",
            ))
    return lines


def _cells_create_ms_line(cells: list, filename: str) -> Line | None:
    values = [
        c.get("create_ms") for c in cells
        if isinstance(c, dict) and _is_number(c.get("create_ms"))
    ]
    if not values:
        return None
    mean_ms = sum(values) / len(values)
    return Line(
        label="mean_sandbox_create_ms",
        value=round(mean_ms, 1),
        unit="ms",
        source=(
            f"measured: {filename}#report.cells[*].create_ms "
            f"(mean over {len(values)} cells)"
        ),
        kind="measured",
    )


def hibernation_cost_lines(evidence: dict, filename: str) -> list[Line]:
    """Everything `hibernation_modes_probe.py` measured, attributed.

    Reads `report.cost` (rates, baseline savings, sandbox shape),
    `report.summary` (wake latency), and `report.cells[*].create_ms` — every
    figure pulled out is labelled `kind="measured"` with `filename` named in
    `source`, because these numbers came out of a real sandbox run, not a
    published table.
    """
    report = evidence.get("report") if isinstance(evidence, dict) else None
    if not isinstance(report, dict):
        return []

    lines: list[Line] = []

    cost = report.get("cost")
    if isinstance(cost, dict):
        lines.extend(_cost_top_level_lines(cost, filename))
        lines.extend(_cost_per_state_lines(cost, filename))
        lines.extend(_cost_baseline_lines(cost, filename))
        lines.extend(_sandbox_shape_lines(cost, filename))

    summary = report.get("summary")
    if isinstance(summary, dict):
        lines.extend(_summary_wake_lines(summary, filename))

    cells = report.get("cells")
    if isinstance(cells, list):
        cells_line = _cells_create_ms_line(cells, filename)
        if cells_line is not None:
            lines.append(cells_line)

    return lines


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


#: House convention (`hibernation_modes_probe.py`'s own `render()`) prints a
#: dollar rate as `${rate:.8f}`. Applied here too so a reader sees the full
#: precision the source carries instead of Python's default float repr, which
#: silently drops trailing zeros (`0.012120` and `0.01212` are the same float,
#: but only the former visibly matches the published table to 6 decimals).
_MONEY_UNITS = ("USD/hr", "USD")


def _fmt_value(value: float, unit: str) -> str:
    if unit in _MONEY_UNITS:
        return f"{value:.8f}"
    return str(value)


def render_markdown(lines: list[Line]) -> str:
    """A table where every row shows value, unit, source, AND kind.

    The honesty rule this whole worksheet exists to serve — never render a
    number the source did not produce, and never let an estimate pass as
    measured — only holds if a reader can see, per row, which one it is.
    """
    header = "| Label | Value | Unit | Kind | Source |\n"
    sep = "|---|---|---|---|---|\n"
    if not lines:
        return header + sep + "| _(none)_ |  |  |  |  |\n"
    rows = "\n".join(
        f"| {line.label} | {_fmt_value(line.value, line.unit)} | {line.unit} | "
        f"{line.kind} | {line.source} |"
        for line in lines
    )
    return header + sep + rows + "\n"


# ---------------------------------------------------------------------------


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    lines: list[Line] = list(disk_shrink_lines(mem_gib=DISK_SHRINK_MEM_GIB))

    try:
        filename, evidence = load_latest_evidence(EVIDENCE_DIR)
        lines.extend(hibernation_cost_lines(evidence, filename))
        evidence_note = f"Measured lines are pulled from `{filename}`."
    except FileNotFoundError as exc:
        print(f"cost_worksheet: {exc}")
        evidence_note = (
            "No hibernation-modes evidence found — this worksheet carries only "
            "the §7.3 published-rate table below; no measured line is present."
        )

    body = render_markdown(lines)
    doc = (
        f"# Cost worksheet (D-9)\n\n"
        f"Generated {stamp}. {evidence_note} Every row below names its own "
        f"unit and kind (`measured` / `published` / `derived`); an `estimate` "
        f"line, if one is ever added, may never say \"measured\" in its "
        f"source. Rates are Alibaba's published preview pay-as-you-go, Pro "
        f"tier, doc 3045213 — modelled, not billed; reconcile against the "
        f"invoice before quoting.\n\n"
        f"{body}"
    )

    out_path = EVIDENCE_DIR / f"cost-worksheet-{stamp}.md"
    out_path.write_text(doc)
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
