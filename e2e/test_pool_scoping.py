"""Team pools, end to end: the boundary, the trusted worker, the revocation.

Team pools give a submitter a way to confine work to a set of machines their
team trusts, instead of the whole opted-in fleet. The whole feature is one
placement gate (`IsolationAwarePlacement`'s seventh gate,
`flashruntime.scheduler`) reading `task.payload["pool"]` against a claiming
node's `capabilities.pools` — but that gate has three separate ways to be
silently absent rather than enforced: the recipe could forget to stamp the
payload, the node view the claim endpoint builds could drop the
advertisement, or a membership change could fail to reach placement without
an agent restart. Each of those breaks exactly the way `test_gpu_placement.py`
warns about: unit tests on both sides stay green while the boundary itself
does nothing. This file walks the real chain over real HTTP instead.

Three things are asserted, each end to end:
1. Two pools, one member agent each — a task scoped to pool-a is completed
   only by the pool-a agent; the pool-b agent never accepts it.
2. A trusted worker (no container, `unsandboxed_argv_capable`) claims and
   completes a pool-scoped argv task, and stays locked out of a public
   sandboxed argv task with no pool — the trusted-runner alternative in the
   argv gate requires a pool, and this is the one test proving that.
3. Membership is live, not just at registration: a heartbeat that replaces
   `capabilities.pools` with `[]` reaches placement immediately — the very
   next claim for a pool-scoped task is refused.

THIS FILE SKIPS AGAINST THE PINNED ARTIFACTS, DELIBERATELY (see
`_require_pool_support` below) — the released pin the suite may fall back to
predates team pools. The skip is keyed on the *feature*
(`NodeCapabilities.pools`) rather than a version string, so it switches
itself on the moment the pin moves, exactly like `test_gpu_placement.py`'s
`_require_gpu_support`.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest
from conftest import make_dataset_csv

from flashnode.executor import CoordinatorClient, ExecutorLoop
from flashnode.executor.trusted_runner import TrustedArgvRunner


def _require_pool_support() -> None:
    """Feature probe, not a version string (the test_gpu_placement rule):
    NodeCapabilities.pools is what the whole chain hangs off."""
    proto = pytest.importorskip("flashruntime.protocol.v1alpha1")
    if "pools" not in proto.NodeCapabilities.model_fields:
        pytest.skip(
            "the resolved flashruntime has no NodeCapabilities.pools — this "
            "is the released pin, which predates team pools. Re-run after "
            "the release, or now with `make e2e-setup LOCAL=1`."
        )


def _register(coordinator, node_id, *, pools=(), **reg_kwargs):
    from flashruntime.protocol.v1alpha1 import NodeCapabilities, NodeRegistration

    reg = NodeRegistration(
        node_id=node_id,
        kubernetes_node="",
        hostname=f"{node_id}.e2e",
        capabilities=NodeCapabilities(cpu_cores=2, pools=list(pools)),
        environment="local",
        **reg_kwargs,
    )
    coordinator.post("/v1alpha1/nodes/register", reg.model_dump(mode="json"))


def _accepted_tasks(coordinator, node_id: str) -> int:
    for n in coordinator.get("/v1alpha1/nodes"):
        if n["node_id"] == node_id:
            return n["accepted_tasks"]
    raise AssertionError(f"node {node_id} not in /v1alpha1/nodes")


def _task_states(coordinator, job_id: str) -> list[str]:
    return [t["state"] for t in coordinator.get(f"/v1alpha1/jobs/{job_id}/tasks")]


def _wait_until(predicate, *, timeout: float = 30.0, interval: float = 0.2) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


# ---------------------------------------------------------------------------
# 1. Two pools, one agent each — the boundary itself.
# ---------------------------------------------------------------------------


def test_a_pool_scoped_task_is_claimed_only_by_its_pool(coordinator):
    """A `hyperparameter_search` job placed on pool-a. The pool-b agent
    polls the whole time the pool-a agent is working and must never accept
    it — 0 accepted tasks, not merely "didn't happen to win the race"."""
    _require_pool_support()

    up = coordinator.put_bytes(
        "/v1alpha1/artifacts/datasets/pools/train.csv", make_dataset_csv()
    )
    assert up["sha256"]

    _register(coordinator, "pool-a-agent", pools=["pool-a"])
    _register(coordinator, "pool-b-agent", pools=["pool-b"])

    job = coordinator.post(
        "/v1alpha1/jobs",
        {
            "apiVersion": "flashml.dev/v1alpha1",
            "kind": "Job",
            "metadata": {"name": "pool-boundary"},
            "spec": {
                "execution": {"backend": "leases"},
                "image": {"repository": "local/tier1", "tag": "dev"},
                "workload": {
                    "type": "hyperparameter_search",
                    "parameters": {
                        "trials": [{"model": "logreg", "C": 0.5}],
                        "inputs": {"dataset": "artifact://datasets/pools/train.csv"},
                    },
                },
                "placement": {"pool": "pool-a"},
            },
        },
    )
    job_id = job["job_id"]
    tasks = coordinator.get(f"/v1alpha1/jobs/{job_id}/tasks")
    assert len(tasks) == 1

    agent_a = ExecutorLoop(
        CoordinatorClient(coordinator.base_url), "pool-a-agent",
        poll_seconds=0.2, node_heartbeat_seconds=2.0,
    )
    agent_b = ExecutorLoop(
        CoordinatorClient(coordinator.base_url), "pool-b-agent",
        poll_seconds=0.2, node_heartbeat_seconds=2.0,
    )
    threading.Thread(target=agent_a.run, daemon=True).start()
    threading.Thread(target=agent_b.run, daemon=True).start()
    try:
        _wait_until(lambda: _task_states(coordinator, job_id) == ["COMPLETED"])
        # pool-b's loop has been polling this whole time; give it a few more
        # cycles after the win to prove it never claims a same-pool task
        # that does not exist for it, rather than merely losing a race.
        time.sleep(0.6)
    finally:
        agent_a.stop_event.set()
        agent_b.stop_event.set()

    task = coordinator.get(f"/v1alpha1/jobs/{job_id}/tasks")[0]
    assert task["node_id"] == "pool-a-agent"
    assert _accepted_tasks(coordinator, "pool-a-agent") == 1
    assert _accepted_tasks(coordinator, "pool-b-agent") == 0


# ---------------------------------------------------------------------------
# 2. The trusted worker — pool argv yes, public argv never.
# ---------------------------------------------------------------------------


_METRICS_SCRIPT = (
    "import json; json.dump({'ok': True}, open('out/metrics.json', 'w'))"
)


def _argv_job(*, pool: str | None, allow_fallback: bool, name: str) -> dict:
    spec: dict = {
        "apiVersion": "flashml.dev/v1alpha1",
        "kind": "Job",
        "metadata": {"name": name},
        "spec": {
            "execution": {"backend": "leases"},
            "image": {"repository": "local/tier1", "tag": "dev"},
            "workload": {
                "type": "command",
                "parameters": {"command": [sys.executable, "-c", _METRICS_SCRIPT]},
            },
            "isolation": {"tier": "sandboxed", "allowFallback": allow_fallback},
        },
    }
    if pool is not None:
        spec["spec"]["placement"] = {"pool": pool}
    return spec


def test_trusted_worker_claims_pool_argv_but_never_public_argv(coordinator):
    """`unsandboxed_argv_capable` opts a host into running POOL argv work
    with no container. The gate's trusted-worker alternative requires a
    pool on the task; a public (no-pool) argv task must stay unclaimed by
    this node even though nothing else in the fleet could take it either —
    the point is that IT never does, not that the queue drains some other
    way."""
    _require_pool_support()

    _register(
        coordinator, "trusted-a", pools=["pool-a"],
        unsandboxed_argv_capable=True,
    )

    pool_job = coordinator.post(
        "/v1alpha1/jobs",
        _argv_job(pool="pool-a", allow_fallback=True, name="pool-argv"),
    )
    pool_job_id = pool_job["job_id"]

    agent = ExecutorLoop(
        CoordinatorClient(coordinator.base_url), "trusted-a",
        runner=TrustedArgvRunner(), poll_seconds=0.2, node_heartbeat_seconds=2.0,
    )
    thread = threading.Thread(target=agent.run, daemon=True)
    thread.start()
    try:
        _wait_until(lambda: _task_states(coordinator, pool_job_id) == ["COMPLETED"])
        assert _accepted_tasks(coordinator, "trusted-a") == 1

        # A public task: no pool, and the submitter did NOT waive the sandbox
        # tier (allowFallback False) — CommandRecipe refuses allowFallback
        # without a pool outright, so this also pins that a *legitimate*
        # public argv job (the common case) still cannot reach this
        # trusted-only node.
        public_job = coordinator.post(
            "/v1alpha1/jobs",
            _argv_job(pool=None, allow_fallback=False, name="public-argv"),
        )
        public_job_id = public_job["job_id"]

        # The agent thread is STILL RUNNING and polling here — stop_event is
        # not set until this whole `try` exits, unlike a check made after
        # the loop was already stopped, which would prove nothing. Several
        # real poll cycles' worth of time to show it does not quietly pick
        # this up.
        time.sleep(0.6)
        assert _task_states(coordinator, public_job_id) == ["PENDING"]
        assert _accepted_tasks(coordinator, "trusted-a") == 1  # unchanged

        # And the direct claim, unambiguous: nothing else is registered, this
        # public task is the only thing left in the queue, and the node is
        # refused it outright — independent of (and consistent with) the
        # polling loop above, which was already proving the same thing on
        # every cycle during the sleep.
        lease = CoordinatorClient(coordinator.base_url).claim("trusted-a")
        assert lease is None
    finally:
        agent.stop_event.set()


# ---------------------------------------------------------------------------
# 3. Membership revocation — a heartbeat reaches placement immediately.
# ---------------------------------------------------------------------------


def test_pool_membership_revoked_by_heartbeat_is_refused_on_next_claim(coordinator):
    """`NodeHeartbeat.pools` replaces `capabilities.pools` wholesale, live —
    no agent restart. A member that leaves must lose eligibility on its very
    next claim, not eventually."""
    _require_pool_support()
    from flashruntime.protocol.v1alpha1 import NodeHeartbeat

    _register(coordinator, "revoked-a", pools=["pool-a"])

    job = coordinator.post(
        "/v1alpha1/jobs",
        {
            "apiVersion": "flashml.dev/v1alpha1",
            "kind": "Job",
            "metadata": {"name": "pool-revocation"},
            "spec": {
                "execution": {"backend": "leases"},
                "image": {"repository": "local/tier1", "tag": "dev"},
                "workload": {
                    "type": "hyperparameter_search",
                    "parameters": {"trials": [{"model": "logreg", "C": 0.5}]},
                },
                "placement": {"pool": "pool-a"},
            },
        },
    )
    job_id = job["job_id"]
    assert _task_states(coordinator, job_id) == ["PENDING"]

    # Revoke membership before anything ever claims the task.
    coordinator.post(
        "/v1alpha1/nodes/revoked-a/heartbeat",
        NodeHeartbeat(node_id="revoked-a", pools=[]).model_dump(mode="json"),
    )

    lease = CoordinatorClient(coordinator.base_url).claim("revoked-a")
    assert lease is None
    assert _task_states(coordinator, job_id) == ["PENDING"]
    assert _accepted_tasks(coordinator, "revoked-a") == 0
