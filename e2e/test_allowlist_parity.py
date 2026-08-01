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


def test_task_module_allowlist_parity():
    """Both allowlists present and coordinator's is a subset of agent's."""
    # Import both allowlists live from their source modules.
    # If either is unavailable (e.g., missing dependency), skip explicitly.
    try:
        from flashruntime.service.modea import ALLOWED_TASK_MODULES
    except ImportError as e:
        import pytest
        pytest.importorskip(
            "flashruntime",
            reason=f"flashruntime.service.modea not importable (likely fastapi missing): {e}",
        )

    try:
        from flashnode.executor.runner import DEFAULT_ALLOWED_MODULES
    except ImportError as e:
        import pytest
        pytest.importorskip(
            "flashnode",
            reason=f"flashnode.executor.runner not importable: {e}",
        )

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
