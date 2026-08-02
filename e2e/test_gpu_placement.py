"""The GPU requirement, walked end to end across both public packages.

WHY THIS FILE EXISTS, and why unit tests were not enough.

The `local_datasets` feature — structurally identical to this one — was built
correctly at both ends and broken in the middle **three separate times**:

  1. the coordinator's node view omitted the capability, so the gate read it
     as absent on every node and its acceptance test passed vacuously;
  2. `CommandRecipe.expand` dropped the payload key, so the gate saw nothing
     to enforce and **failed open**;
  3. neither flashnode runner forwarded the key, so the executor half was
     implemented and dead.

Every one of those had green unit tests on both sides of the break. What
none of them had was a single test that walks the whole chain, which is
this file:

    JobSpec.resources.gpuPerTask
      → CommandRecipe.expand      → task.payload["gpus"]
      → IsolationAwarePlacement   → placed on a GPU host, refused on a CPU one
      → flashnode harden_args     → docker argv contains --gpus

The cloud hop (`flashml.yaml resources.gpus` → `gpuPerTask`) is covered in
flashml-cloud's own `tests/test_compile.py`; e2e cannot import the private
package. What e2e uniquely covers is the flashruntime→flashnode span, which
is where hops 2 and 3 broke.

THIS FILE SKIPS AGAINST THE PINNED ARTIFACTS, DELIBERATELY. `make e2e-setup`
installs the released flashruntime/flashnode, and GPU support is unreleased
at the time of writing. The skip is keyed on the *feature* being present
rather than on a version string, so it switches itself on the moment the pin
moves — no one has to remember to re-enable it. Until then, run it against a
working tree with `make e2e-setup LOCAL=1`.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _require_gpu_support() -> None:
    """Skip unless BOTH packages resolve to a build that has GPU support.

    Checked by feature, not by version: `ResourcesSpec.gpuPerTask` is the
    field the whole chain hangs off, and `harden_args(gpus=...)` is the far
    end of it. A pin that has one but not the other is exactly the drift this
    suite exists to catch, so each is asserted separately with a message
    naming which half is behind.
    """
    resources = pytest.importorskip("flashruntime.protocol.v1alpha1")
    if "gpuPerTask" not in resources.ResourcesSpec.model_fields:
        pytest.skip(
            "the resolved flashruntime has no ResourcesSpec.gpuPerTask — this "
            "is the released pin, which predates GPU support. Re-run after the "
            "release, or now with `make e2e-setup LOCAL=1`."
        )
    hardening = pytest.importorskip("flashnode.executor.hardening")
    import inspect

    if "gpus" not in inspect.signature(hardening.harden_args).parameters:
        pytest.skip(
            "the resolved flashnode's harden_args takes no `gpus` — flashnode "
            "is behind flashruntime. Bump the node pin."
        )


def _job_spec(gpus: int):
    """A minimal sandboxed command JobSpec asking for `gpus` devices."""
    from flashruntime.protocol.v1alpha1 import JobSpec

    return JobSpec.model_validate(
        {
            "apiVersion": "flashml.dev/v1alpha1",
            "kind": "Job",
            "metadata": {"name": "gpu-chain"},
            "spec": {
                "image": {
                    "repository": "ghcr.io/zolli-labs/flashml-pytorch-cuda",
                    # pinned, never "latest" — ImageSpec rejects it outright
                    "tag": "2026.08.2",
                },
                "workload": {
                    "type": "command",
                    "parameters": {"command": ["python", "train.py"]},
                },
                "isolation": {"tier": "sandboxed", "allowFallback": False},
                "resources": {"gpuPerTask": gpus},
            },
        }
    )


def _node(*, gpus: int):
    """A node view shaped exactly as `service/modea.py` builds it for the
    placement policy — `capabilities` as a plain dict, because that hop is
    `capabilities.model_dump()`."""
    return {
        "node_id": f"node-{gpus}gpu",
        "sandbox_capable": True,
        "argv_capable": True,
        "module_capable": True,
        "local_datasets": [],
        "capabilities": {
            "cpu_cores": 8,
            "memory_bytes": 16 * 1024**3,
            "gpus": [{"index": i, "name": "Test GPU"} for i in range(gpus)],
            "os": "linux",
            "architecture": "x86_64",
        },
    }


def _expand(spec):
    from flashruntime.recipes.command import CommandRecipe

    return CommandRecipe().expand("job-gpu-chain", spec)


def _eligible(task, node) -> bool:
    from flashruntime.scheduler import IsolationAwarePlacement

    return IsolationAwarePlacement().eligible(task, node)


# ---------------------------------------------------------------------------


def test_a_gpu_requirement_survives_every_hop():
    """The positive path, asserted at each hop rather than only at the end.

    Asserting only the final argv would still pass if the requirement were
    lost at hop 2 and re-derived elsewhere; naming each hop is what makes a
    failure point at the guilty one.
    """
    _require_gpu_support()
    from flashnode.executor.hardening import harden_args

    # hop 1: the spec carries it
    spec = _job_spec(gpus=1)
    assert spec.spec.resources.gpuPerTask == 1

    # hop 2: the recipe forwards it into the payload  (failed OPEN last time)
    task = _expand(spec)[0]
    assert task.payload["gpus"] == 1, (
        "CommandRecipe.expand dropped the GPU requirement. The placement gate "
        "then sees a task requiring nothing and places it anywhere — the gate "
        "fails OPEN, silently."
    )

    # hop 3: placement honours it
    assert _eligible(task, _node(gpus=1)) is True
    assert _eligible(task, _node(gpus=0)) is False

    # hop 4: the agent turns it into a real docker flag  (was dead code last time)
    flags = harden_args(
        Path("/tmp/work"), cpus=1.0, memory_gb=2.0, gpus=task.payload["gpus"]
    )
    assert "--gpus" in flags
    assert flags[flags.index("--gpus") + 1] == "1"


def test_a_job_asking_for_no_gpu_is_unchanged_end_to_end():
    """The path every job that exists today takes. It must be byte-identical
    to life before this feature — absent key, placed anywhere, no flag."""
    _require_gpu_support()
    from flashnode.executor.hardening import harden_args

    task = _expand(_job_spec(gpus=0))[0]
    assert "gpus" not in task.payload, (
        "a zero requirement must leave the key ABSENT, not set it to 0 — the "
        "no-GPU path has to keep exercising the key-missing branch"
    )
    assert _eligible(task, _node(gpus=0)) is True
    assert _eligible(task, _node(gpus=2)) is True   # GPU hosts still take CPU work
    assert "--gpus" not in harden_args(Path("/tmp/work"), cpus=1.0, memory_gb=2.0)


def test_more_gpus_than_the_host_has_is_refused():
    _require_gpu_support()
    task = _expand(_job_spec(gpus=2))[0]
    assert task.payload["gpus"] == 2
    assert _eligible(task, _node(gpus=1)) is False
    assert _eligible(task, _node(gpus=2)) is True


def test_the_agent_refuses_a_hostile_gpus_payload():
    """`--gpus all` is valid docker syntax for EVERY device on the host, and
    the payload is attacker-influenced. The placement gate already refuses a
    non-int requirement, so reaching here means something upstream was
    bypassed — which is exactly when defence in depth has to hold."""
    _require_gpu_support()
    from flashnode.executor.hardening import harden_args

    for hostile in ("all", True, -1, "1", 1.5):
        flags = harden_args(
            Path("/tmp/work"), cpus=1.0, memory_gb=2.0, gpus=hostile
        )
        assert "--gpus" not in flags, f"harden_args accepted gpus={hostile!r}"
