"""Cross-repo allowlist parity guard.

Ensures that every task module the coordinator (flashruntime) will dispatch
is one the agent (flashnode) will accept. This catches drift where someone
adds a module to the coordinator's allowlist but forgets to add it to the
agent's, causing tasks to be dispatched but then refused by every node.

The direction of the subset relation: ALLOWED_TASK_MODULES ⊆ DEFAULT_ALLOWED_MODULES.
Reason: an agent accepting a module the coordinator never dispatches is harmless
(unused capacity); a coordinator dispatching a module the agent refuses is the
outage that was just fixed. We guard the failure direction.
"""

from __future__ import annotations

from typing import Any

import pytest


def _allowlist_or_skip(module_name: str, attr: str) -> Any:
    """The named allowlist, or skip the test if its module cannot import.

    The subtlety this exists for: `pytest.importorskip` must be given the
    module that actually FAILED. Falling back to the top-level package
    (`importorskip("flashruntime")` when it was `flashruntime.service.modea`
    that raised) does nothing — the package imports fine, importorskip
    returns, execution falls through, and the test dies with a `NameError`
    on the name the failed import never bound. A guard whose fallback path
    raises NameError instead of skipping is worse than no guard: it reports
    a missing optional dependency as a hard failure of the parity check
    itself.
    """
    module = pytest.importorskip(
        module_name,
        reason=f"{module_name} is not importable (likely a missing optional "
               "dependency in this venv), so allowlist parity cannot be checked",
    )
    return getattr(module, attr)


def test_missing_dependency_skips_instead_of_raising_name_error():
    """Regression guard for the guard: an unimportable SUBMODULE of an
    importable package must produce a Skipped, never a NameError."""
    with pytest.raises(pytest.skip.Exception):
        _allowlist_or_skip("flashruntime.service.no_such_module", "ANYTHING")


def test_task_module_allowlist_parity():
    """Both allowlists present and coordinator's is a subset of agent's."""
    ALLOWED_TASK_MODULES = _allowlist_or_skip(
        "flashruntime.service.modea", "ALLOWED_TASK_MODULES")
    DEFAULT_ALLOWED_MODULES = _allowlist_or_skip(
        "flashnode.executor.runner", "DEFAULT_ALLOWED_MODULES")

    # Assert parity: every module the coordinator will hand out must be
    # acceptable to the agent.
    missing = ALLOWED_TASK_MODULES - DEFAULT_ALLOWED_MODULES
    assert not missing, (
        f"Allowlist drift detected: the following modules are in the "
        f"coordinator's ALLOWED_TASK_MODULES (flashruntime/service/modea.py) "
        f"but NOT in the agent's DEFAULT_ALLOWED_MODULES (flashnode/executor/runner.py):\n"
        f"  Missing: {sorted(missing)}\n"
        f"Fix: add these modules to DEFAULT_ALLOWED_MODULES in flashnode/executor/runner.py"
    )
