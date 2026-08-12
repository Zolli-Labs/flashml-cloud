"""The one door onto FlashRuntime's placement gates and task expansion.

This is the ONLY module in this package permitted to import
``flashruntime.scheduler`` or ``flashruntime.service.modea``, and it exists so
that permission is a single reviewable file rather than a habit.

**Why import them at all.** The compute router (``router/plan.py``) has to
answer "which machines could legally run this job?" before it can price
anything. That question already has exactly one correct answer:
``IsolationAwarePlacement.eligible`` — seven fail-closed gates covering
capability, pool membership, GPU count, local data, dependencies, dataset
capacity and node exclusion, each of which fails closed for a stated safety
reason.

The alternative was never "a narrower boundary". It was a hand-copied
predicate in this repo that agrees with the coordinator today and diverges the
first time either side changes — and the divergence would not look like a bug,
it would look like a plan that quietly never fills, or worse, a plan that
recommends a machine the coordinator will refuse. This is the same argument
``fedavg.py`` makes about ``chunks.slot_start``, and it lands the same way:
importing the shared function is strictly the safer of the two.

**Why a separate module rather than importing at the call site.** Two reasons.
``test_import_boundary`` enumerates permitted importers by filename, so a door
that is one file stays auditable — you can read everything this repo borrows
from the runtime's internals in one sitting. And ``router/`` deliberately
takes ``eligible`` as an *injected* callable, so the router package itself
contains no eligibility rule at all. There is nothing there to drift, because
there is nothing there.

**What this module must never become.** A place to re-export whatever is
convenient. Every name added here widens what this repo depends on in the
public runtime, and the runtime's internals change far more often than its
protocol does. If a new import is needed, it needs the same argument the two
below carry: that a local copy would be *less* safe, not merely less
convenient.

Owner decision, 2026-08-11: one-door module plus a `SANCTIONED_EXCEPTIONS`
entry, over widening the boundary package-wide or adding a coordinator-side
preview endpoint. The third option is architecturally cleanest and remains the
right long-term answer — but ``flashml`` is the PUBLIC repo consumed as an
exact PyPI version, so it means merge → release → bump four pin sites, which is
days rather than hours.
"""
from __future__ import annotations

from typing import Any, Callable

__all__ = ["placement_predicate", "task_expander", "available"]


def placement_predicate() -> Callable[[Any, Any], bool] | None:
    """Return the real ``eligible(task, node) -> bool``, or None.

    None means the runtime is not importable in this environment — which
    happens in a unit-test process that stubs the dependency, never in a
    deployment. Callers must treat None as "cannot plan" and say so, and must
    NOT substitute a permissive predicate: a fallback that answers True is
    seven absent safety gates wearing the name of seven present ones.
    """
    try:
        from flashruntime.scheduler import IsolationAwarePlacement
    except Exception:  # noqa: BLE001 - absence is a state, not an error
        return None
    return IsolationAwarePlacement().eligible


def task_expander() -> Callable[..., Any] | None:
    """Return the real task expansion, or None.

    The router prices *tasks*, and how a job spec becomes a task list is the
    coordinator's rule — an HPO sweep, a sharded command and a federated round
    all expand differently. Guessing the count here would misprice every job
    whose expansion is not one-task-per-spec.
    """
    try:
        from flashruntime.service.modea import expand_tasks
    except Exception:  # noqa: BLE001
        return None
    return expand_tasks


def available() -> bool:
    """Whether planning can run at all in this process.

    Exposed so a route can answer 200 with an explanatory note instead of
    failing, and so the reason appears in the response rather than only in a
    log nobody reads.
    """
    return placement_predicate() is not None and task_expander() is not None
