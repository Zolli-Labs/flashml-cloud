"""Federated averaging driven by the REAL flashnode agent.

`flashruntime/tests/test_fedavg_convergence.py` pins the round loop against
a hand-rolled urllib "agent" (flashruntime must not depend on flashnode —
CLAUDE.md rule #1). This is the same loop proven with the genuine agent:
`flashnode.executor.ExecutorLoop` + `SubprocessRunner`, driven as real
threads against a real coordinator subprocess, exactly the way
`e2e/test_local_loop.py` proves the lease sweep with real agents.

`WORKER_PARAMS` and the initial-weights construction are copied verbatim
from `flashruntime/tests/test_fedavg_convergence.py` so the two tests stay
comparable — do not invent different hyperparameters here.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

torch = pytest.importorskip(
    "torch",
    reason="flashml_workloads.fedavg_worker (and the ExecutorLoop subprocess "
    "that runs it) needs torch importable; this e2e venv does not have it "
    "installed (`make e2e-setup` does not include a torch extra). Install "
    "torch into e2e/.venv to run this test for real.",
)

import flashruntime as flash
from flashml_workloads import fedavg_worker
from flashml_workloads.fedavg_driver import HttpCoordinator, run_fedavg
from flashnode.executor import CoordinatorClient, ExecutorLoop, SubprocessRunner
from flashruntime.protocol.v1alpha1 import NodeCapabilities, NodeRegistration

# Copied verbatim from flashruntime/tests/test_fedavg_convergence.py so the
# fake-agent and real-agent proofs stay comparable.
WORKER_PARAMS = {
    "local_steps": 20, "lr": 0.1, "batch_size": 16, "seed": 0,
    "in_dim": 8, "hidden": 16, "out_dim": 2, "dataset_size": 256,
}


def _initial_weights() -> dict:
    model = fedavg_worker.build_model(
        WORKER_PARAMS["seed"], WORKER_PARAMS["in_dim"],
        WORKER_PARAMS["hidden"], WORKER_PARAMS["out_dim"],
    )
    return fedavg_worker.state_to_blob(model)


def _register(coordinator, node_id: str) -> NodeRegistration:
    reg = NodeRegistration(
        node_id=node_id,
        kubernetes_node="",
        hostname=f"{node_id}.e2e",
        capabilities=NodeCapabilities(cpu_cores=4, memory_bytes=8_000_000_000),
        environment="local",
    )
    coordinator.post("/v1alpha1/nodes/register", json.loads(reg.model_dump_json()))
    return reg


def test_fedavg_rounds_reduce_loss_with_two_real_agents(coordinator):
    """Two genuine flashnode ExecutorLoop agents, each in its own thread,
    jointly train one model across several rounds: mean loss must decrease."""
    reg_a = _register(coordinator, "fn-e2e-node-a")
    reg_b = _register(coordinator, "fn-e2e-node-b")

    loop_a = ExecutorLoop(
        CoordinatorClient(coordinator.base_url), "fn-e2e-node-a",
        registration=reg_a, poll_seconds=0.2, node_heartbeat_seconds=2.0,
    )
    loop_b = ExecutorLoop(
        CoordinatorClient(coordinator.base_url), "fn-e2e-node-b",
        registration=reg_b, poll_seconds=0.2, node_heartbeat_seconds=2.0,
    )

    threads = [
        threading.Thread(target=loop_a.run, kwargs={"idle_exit": False}, daemon=True),
        threading.Thread(target=loop_b.run, kwargs={"idle_exit": False}, daemon=True),
    ]
    for t in threads:
        t.start()

    try:
        result = run_fedavg(
            HttpCoordinator(coordinator.base_url), rounds=3, num_shards=2,
            min_participants=2, worker_params=WORKER_PARAMS,
            initial_weights=_initial_weights(),
            round_timeout_s=120.0, poll_seconds=0.25,
        )
    finally:
        loop_a.stop_event.set()
        loop_b.stop_event.set()
        for t in threads:
            t.join(timeout=10)

    losses = [h["mean_loss"] for h in result["history"]]
    assert len(losses) == 3
    assert all(h["participants"] == 2 for h in result["history"])
    assert losses[-1] < losses[0], f"loss did not decrease across rounds: {losses}"


def test_fedavg_survives_a_closed_laptop(coordinator):
    """One agent stops after round 1 (closed laptop); with
    `min_participants=1` the remaining rounds still complete, solo."""
    reg_a = _register(coordinator, "fn-e2e-solo-a")
    reg_b = _register(coordinator, "fn-e2e-solo-b")

    loop_a = ExecutorLoop(
        CoordinatorClient(coordinator.base_url), "fn-e2e-solo-a",
        registration=reg_a, poll_seconds=0.2, node_heartbeat_seconds=2.0,
    )
    loop_b = ExecutorLoop(
        CoordinatorClient(coordinator.base_url), "fn-e2e-solo-b",
        registration=reg_b, poll_seconds=0.2, node_heartbeat_seconds=2.0,
    )

    thread_a = threading.Thread(target=loop_a.run, kwargs={"idle_exit": False}, daemon=True)
    thread_b = threading.Thread(target=loop_b.run, kwargs={"idle_exit": False}, daemon=True)
    thread_a.start()
    thread_b.start()

    rounds_seen: list[dict] = []

    def _on_round(result: dict) -> None:
        rounds_seen.append(result)
        if result["round"] == 0:
            # The laptop closes: node-b stops claiming any further work.
            loop_b.stop_event.set()

    try:
        result = run_fedavg(
            HttpCoordinator(coordinator.base_url), rounds=3, num_shards=2,
            min_participants=1, worker_params=WORKER_PARAMS,
            initial_weights=_initial_weights(),
            round_timeout_s=120.0, poll_seconds=0.25,
            on_round=_on_round,
        )
    finally:
        loop_a.stop_event.set()
        loop_b.stop_event.set()
        thread_a.join(timeout=10)
        thread_b.join(timeout=15)

    losses = [h["mean_loss"] for h in result["history"]]
    assert len(losses) == 3
    assert result["history"][0]["participants"] == 2, "round 0 ran with both agents up"
    assert all(h["participants"] == 1 for h in result["history"][1:]), (
        f"rounds after the closed laptop should complete solo: {result['history']}"
    )
