"""Submit-time routing: resolve what a job needs, plan against the book.

Design: docs/superpowers/specs/2026-08-11-zolli-marketplace-design.md §4/§6
and docs (repo root)/research/2026-08-13-automatic-routing-marketplace-matching.md §5.
This module never imports the runtime; eligibility stays the coordinator's,
matching stays `marketplace`'s. Everything here is orchestration and reasons.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import marketplace


class GpuRoutingUnavailable(ValueError):
    """GPU-class routing is blocked on the runtime pin.

    `gpuPerTask` is silently dropped by the pinned flashruntime ResourcesSpec
    (compile.py:608-624), so a routed GPU job would be priced for hardware the
    coordinator cannot yet reserve. Refuse loudly instead of routing a fiction;
    lands with the 0.6.1 release + 4-site pin bump.
    """


def job_capability_class(resources: Mapping[str, Any] | None) -> str:
    """The class the JOB needs — a property of the work (marketplace.py:1639)."""
    res = dict(resources or {})
    gpus = res.get("gpus") or 0
    if isinstance(gpus, bool) or int(gpus) > 0:
        raise GpuRoutingUnavailable(
            "price: routing for gpus > 0 is not available yet — the pinned "
            "runtime drops gpuPerTask (compile.py:608). Remove price: or set "
            "gpus: 0 until the 0.6.1 pin bump."
        )
    cpus = res.get("cpus") or 0
    if float(cpus) >= marketplace.CPU_LARGE_MIN_CORES:
        return "cpu-large"
    return "cpu-small"
