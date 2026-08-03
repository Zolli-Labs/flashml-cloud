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
from flashml_workloads.fedavg_driver import HttpCoordinator, resume_state, run_fedavg
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
    """One agent stops after round 0 (closed laptop); with
    `min_participants=1` the remaining rounds still complete, solo.

    Driven as TWO `run_fedavg` calls rather than one with an `on_round`
    hook, because the single-call version was flaky on CI (PROGRESS
    2026-08-03): it ran every round at `min_participants=1` and then
    asserted round 0 had two participants. Quorum of 1 is reached by
    whichever agent commits first, so on a contended runner round 0
    aggregated before the second agent had claimed anything, and the
    assertion — which is correct and must not be weakened — lost the race.

    Splitting the run puts the requirement where it can be enforced instead
    of hoped for: round 0 runs at `min_participants=2`, so the driver
    *cannot* aggregate until two shards have committed. It waits for the
    second agent, or it raises `QuorumNotMet`. The closed laptop then
    happens between the two calls, where it is an ordinary sequential step.

    What round 0 pins is "two contributions were aggregated", not "two
    distinct machines contributed" — a single agent that claimed both
    shards in sequence would also satisfy it. That is deliberate: asserting
    distinct node ids would re-introduce exactly the race this removes,
    since a badly enough stalled agent B is indistinguishable from one that
    never started.
    """
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

    coord = HttpCoordinator(coordinator.base_url)
    try:
        first = run_fedavg(
            coord, rounds=1, num_shards=2,
            min_participants=2, worker_params=WORKER_PARAMS,
            initial_weights=_initial_weights(),
            round_timeout_s=120.0, poll_seconds=0.25,
        )

        # The laptop closes. JOIN, don't just signal: a still-running node-b
        # can commit a shard into round 1 and make it a two-participant
        # round, which is the same race one step later.
        loop_b.stop_event.set()
        thread_b.join(timeout=15)
        assert not thread_b.is_alive(), (
            "node-b did not stop; the rounds that follow are supposed to be solo"
        )

        # Pick up where round 0 left off, through the driver's own documented
        # resume path rather than by hand-building the weights URI — so this
        # test also exercises the contract a restarted driver depends on.
        start_round, weights, weights_uri = resume_state(coord, first["job_ids"])
        assert start_round == 1, f"round 0 should be the last completed round, got {start_round}"

        rest = run_fedavg(
            coord, rounds=3, num_shards=2,
            min_participants=1, worker_params=WORKER_PARAMS,
            initial_weights=weights, weights_uri=weights_uri,
            start_round=start_round, prior_job_ids=first["job_ids"],
            round_timeout_s=120.0, poll_seconds=0.25,
        )
    finally:
        loop_a.stop_event.set()
        loop_b.stop_event.set()
        thread_a.join(timeout=10)
        thread_b.join(timeout=15)

    history = first["history"] + rest["history"]
    losses = [h["mean_loss"] for h in history]
    assert len(losses) == 3
    assert history[0]["participants"] == 2, "round 0 ran with both agents up"
    assert all(h["participants"] == 1 for h in history[1:]), (
        f"rounds after the closed laptop should complete solo: {history}"
    )
