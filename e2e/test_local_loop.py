"""The full local loop, across real process and network boundaries.

plan → submit → discover machines → distribute → kill a machine →
recover → complete exactly once → attribute per-node contribution —
no cloud, no Kubernetes, shared data hosted by the coordinator itself.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request

from conftest import make_dataset_csv

import flashruntime as flash
from flashnode.executor import CoordinatorClient, ExecutorLoop, SubprocessRunner
from flashruntime.protocol.v1alpha1 import NodeCapabilities, NodeRegistration

TRIALS = (
    [{"model": "logreg", "C": c} for c in (0.01, 0.03, 0.1, 0.3, 1.0, 3.0)]
    + [{"model": "rf", "n_estimators": n} for n in (10, 25, 50, 75, 100, 150)]
)
DIES_ON_NTH_TASK = 3  # node-b completes 2 tasks, then dies holding its 3rd


class DyingClient(CoordinatorClient):
    """A machine whose network is severed mid-run: after the kill switch,
    every call fails. No goodbye, no fail() report — the coordinator must
    notice via lease expiry alone."""

    def __init__(self, base_url: str, kill: threading.Event):
        super().__init__(base_url)
        self._kill = kill

    def _request(self, method, path, body=None, content_type="application/json"):
        if self._kill.is_set():
            raise OSError("network severed (simulated machine death)")
        return super()._request(method, path, body, content_type)


class KillSwitchRunner(SubprocessRunner):
    """Severs this machine's network at the start of its Nth task — whatever
    task that turns out to be. The work still runs; nothing can ever be
    reported, so the lease must expire on the coordinator's clock."""

    def __init__(self, kill: threading.Event, **kw):
        super().__init__(**kw)
        self._kill = kill
        self._runs = 0

    def run(self, payload, workdir, inputs):
        self._runs += 1
        if self._runs >= DIES_ON_NTH_TASK:
            self._kill.set()
        return super().run(payload, workdir, inputs)


def _register(coordinator, node_id: str) -> None:
    reg = NodeRegistration(
        node_id=node_id,
        kubernetes_node="",
        hostname=f"{node_id}.e2e",
        capabilities=NodeCapabilities(cpu_cores=4, memory_bytes=8_000_000_000),
        environment="local",
    )
    coordinator.post("/v1alpha1/nodes/register", json.loads(reg.model_dump_json()))


def test_full_local_loop_with_machine_death(coordinator):
    # 1. Plan: the planner routes an independent-task sweep to the lease runtime.
    report = flash.plan(
        flash.PlanRequest(
            workload=flash.IndependentTasks(
                task_kind="hyperparameter_search",
                task_count=len(TRIALS),
                est_minutes_per_task=0.1,
            ),
            resources=flash.Resources(hosts=2, cpu_cores=8),
        )
    )
    assert report.selected.strategy_family == "lease_tasks"
    assert report.selected.launcher == "flashruntime-leases"

    # 2. Shared data: hosted by the coordinator, not any cloud.
    up = coordinator.put_bytes("/v1alpha1/artifacts/datasets/e2e/train.csv", make_dataset_csv())
    assert up["backend"] == "local" and up["sha256"]

    # 3. Submit: expands into 12 leased tasks (short leases → fast recovery).
    job = coordinator.post(
        "/v1alpha1/jobs",
        {
            "apiVersion": "flashml.dev/v1alpha1",
            "kind": "Job",
            "metadata": {"name": "e2e-sweep"},
            "spec": {
                "execution": {"backend": "leases"},
                "image": {"repository": "local/tier1", "tag": "dev"},
                "workload": {
                    "type": "hyperparameter_search",
                    "parameters": {
                        "trials": TRIALS,
                        "inputs": {"dataset": "artifact://datasets/e2e/train.csv"},
                        "lease_seconds": 6,
                    },
                },
            },
        },
    )
    job_id = job["job_id"]
    assert len(coordinator.get(f"/v1alpha1/jobs/{job_id}/tasks")) == len(TRIALS)

    # 4. Two machines discover themselves and pull work over real HTTP.
    kill = threading.Event()
    _register(coordinator, "e2e-node-a")
    _register(coordinator, "e2e-node-b")
    survivor = ExecutorLoop(
        CoordinatorClient(coordinator.base_url), "e2e-node-a",
        poll_seconds=0.3, node_heartbeat_seconds=2.0,
    )
    dying = ExecutorLoop(
        DyingClient(coordinator.base_url, kill), "e2e-node-b",
        runner=KillSwitchRunner(kill), poll_seconds=0.3, node_heartbeat_seconds=2.0,
    )
    def run_until_death():
        # The severed network makes the loop raise — that *is* the machine
        # death; swallow it so pytest doesn't flag the intentional crash.
        try:
            dying.run()
        except OSError:
            pass

    threads = [
        threading.Thread(target=survivor.run, kwargs={"idle_exit": False}, daemon=True),
        threading.Thread(target=run_until_death, daemon=True),
    ]
    for t in threads:
        t.start()

    # 5. Wait for completion (recovery adds one lease-expiry window).
    deadline = time.monotonic() + 180
    state = None
    while time.monotonic() < deadline:
        state = coordinator.get(f"/v1alpha1/jobs/{job_id}")["state"]
        if state in ("SUCCEEDED", "FAILED"):
            break
        time.sleep(1)
    survivor.stop_event.set()
    assert kill.is_set(), "the dying machine never picked up the marked task"
    assert state == "SUCCEEDED", f"job ended {state}"

    # 6. Exactly-once: 12/12 completed; exactly one task needed a second
    #    attempt (the one node-b died holding), finished by the survivor.
    tasks = coordinator.get(f"/v1alpha1/jobs/{job_id}/tasks")
    assert [t["state"] for t in tasks] == ["COMPLETED"] * len(TRIALS)
    retried = [t for t in tasks if t["attempts"] >= 2]
    assert len(retried) == 1, f"expected exactly one recovered task, got {retried}"
    assert retried[0]["node_id"] == "e2e-node-a"  # finished by the survivor

    # 7. Recovery evidence in the ledger, not narrative: the lease expired
    #    and the task was requeued.
    events = coordinator.get(f"/v1alpha1/jobs/{job_id}/events")
    types = [e["type"] for e in events]
    assert "LEASE_EXPIRED" in types and "TASK_REQUEUED" in types

    # 8. Results: one metrics.json per trial, readable, with real accuracies.
    artifacts = coordinator.get(f"/v1alpha1/jobs/{job_id}/artifacts")
    metric_keys = [a["key"] for a in artifacts if a["key"].endswith("metrics.json")]
    assert len(metric_keys) == len(TRIALS)
    with urllib.request.urlopen(
        f"{coordinator.base_url}/v1alpha1/artifacts/{metric_keys[0]}"
    ) as r:
        metrics = json.loads(r.read())
    assert 0.5 <= metrics["accuracy_mean"] <= 1.0

    # 9. Contribution: both machines worked; credit is for accepted work
    #    only — the dying machine keeps its 2 completed tasks, earns nothing
    #    for the one it died holding, and the fleet total is exactly 12.
    nodes = {n["node_id"]: n for n in coordinator.get("/v1alpha1/nodes")}
    assert nodes["e2e-node-b"]["accepted_tasks"] == DIES_ON_NTH_TASK - 1
    assert nodes["e2e-node-a"]["accepted_tasks"] == len(TRIALS) - (DIES_ON_NTH_TASK - 1)
    total = sum(n["accepted_tasks"] for n in nodes.values())
    assert total == len(TRIALS)  # exactly once, across the fleet
