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
            HttpCoordinator(coordinator.base_url), epochs=3, sync_every=1.0,
            total_chunks=2, slots=2, expected_machines=2,
            worker_params=WORKER_PARAMS,
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
    # Coverage, not participant count. `min_participants` is gone: flashruntime
    # 0.5.0 closes a round when the chunks its contributors report cover
    # `sync_every` of a pass, and the design states outright that nothing sets
    # a floor on how many machines supply them. Asserting `participants == 2`
    # would re-impose the requirement the coverage model exists to remove, and
    # it was never the property under test — what matters is that a full pass
    # over the data was trained and combined, whoever did it.
    assert all(h["covered"] == pytest.approx(1.0) for h in result["history"]), (
        f"every round should cover a full pass at sync_every=1.0: {result['history']}"
    )
    assert losses[-1] < losses[0], f"loss did not decrease across rounds: {losses}"


def test_fedavg_survives_a_closed_laptop(coordinator):
    """One agent stops after round 0 (closed laptop); the remaining rounds
    still complete on the machine that is left.

    Still driven as TWO `run_fedavg` calls, but for a simpler reason than
    before: the laptop closes *between* them, where it is an ordinary
    sequential step rather than something raced against a running round.

    Rewritten for flashruntime 0.5.0, which removed `min_participants`.
    The old staging ran round 0 at `min_participants=2` so the driver could
    not aggregate until two shards had committed, and the rest at
    `min_participants=1` so they could finish solo. Neither knob exists: a
    round now closes when the chunks its contributors report cover
    `sync_every` of a pass, and the design says plainly that nothing sets a
    floor on contributor count.

    That makes the old assertions unavailable AND unwanted. `participants`
    is an outcome now, not a requirement — one machine fast enough to cover
    the target closes a round by itself, which is precisely the elasticity
    being tested, so pinning `participants == 2` would fail the design
    rather than the code. What survives the rewrite is the property the
    test was really for: **the fleet losing a machine mid-run does not stop
    the run, and the remaining rounds still train a full pass.** Coverage
    states that directly, and unlike a quorum it cannot be satisfied by a
    fast committer racing ahead — the data has to actually be trained.

    `expected_machines` drops from 2 to 1 for the solo half: it is what
    sizes each slot's chunk budget, so telling the driver to expect one
    machine is how the survivor is handed enough work to cover the pass
    alone. Getting that wrong does not fail loudly — the round would simply
    poll to its backstop with partial coverage.
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
            coord, epochs=1, sync_every=1.0,
            total_chunks=2, slots=2, expected_machines=2,
            worker_params=WORKER_PARAMS,
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
            coord, epochs=3, sync_every=1.0,
            total_chunks=2, slots=2, expected_machines=1,
            worker_params=WORKER_PARAMS,
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
    # The point of the test, stated in the terms 0.5.0 actually closes on:
    # every round trained a full pass, including the ones that ran after a
    # machine vanished. `participants` is reported for observability and is
    # deliberately NOT asserted — see the docstring.
    assert all(h["covered"] == pytest.approx(1.0) for h in history), (
        f"a lost machine must not cost the run its coverage: {history}"
    )
    assert losses[-1] < losses[0], (
        f"training did not progress across the closed laptop: {losses}"
    )
