"""Cross-machine training resume — Stage 7's money demo, end to end.

Machine B trains, checkpointing every 10 steps; it crashes at step 35 and
dies without a word. The task requeues; machine A picks it up, downloads
B's relayed step-30 checkpoint from the coordinator, resumes, and finishes
— with the ledger able to say exactly how much work was lost (5 steps, not
35). All real HTTP, real subprocesses, no cloud.
"""

from __future__ import annotations

import json
import urllib.error

from conftest import make_dataset_csv

from flashnode.executor import CoordinatorClient, ExecutorLoop
from flashruntime.protocol.v1alpha1 import NodeCapabilities, NodeRegistration


def _register(coordinator, node_id):
    reg = NodeRegistration(
        node_id=node_id, kubernetes_node="", hostname=f"{node_id}.e2e",
        capabilities=NodeCapabilities(cpu_cores=4), environment="local",
    )
    coordinator.post("/v1alpha1/nodes/register", reg.model_dump(mode="json"))


def test_training_crash_on_one_machine_resumes_on_another(coordinator):
    coordinator.put_bytes("/v1alpha1/artifacts/datasets/train/data.csv", make_dataset_csv())

    job = coordinator.post("/v1alpha1/jobs", {
        "apiVersion": "flashml.dev/v1alpha1", "kind": "Job",
        "metadata": {"name": "resume-training"},
        "spec": {
            "execution": {"backend": "leases"},
            "image": {"repository": "local/tier1", "tag": "dev"},
            "workload": {"type": "hyperparameter_search", "parameters": {
                "module": "flashml_workloads.sgd_trainer",
                "trials": [{"steps": 60, "checkpoint_every": 10, "lr": 0.1,
                            "seed": 7, "kill_at_step": 35}],
                "inputs": {"dataset": "artifact://datasets/train/data.csv"},
                "checkpoint": {},
                "lease_seconds": 30,
            }},
        },
    })
    job_id = job["job_id"]

    # -- machine B: claims, checkpoints via the relay, crashes at step 35 ----
    _register(coordinator, "machine-b")
    machine_b = ExecutorLoop(CoordinatorClient(coordinator.base_url), "machine-b")
    lease_b = machine_b.client.claim("machine-b")
    assert machine_b.execute_one(lease_b) is False  # the crash (exit 3)

    # B's death left a valid, resumable checkpoint on the coordinator…
    latest = coordinator.get(
        f"/v1alpha1/jobs/{job_id}/tasks/{lease_b.task_id}/checkpoints/latest"
    )
    assert latest["step"] == 30 and latest["validation"] == "hash_verified"
    # …and the recovery-economics number is honest: 5 steps lost, not 35.
    lost = coordinator.get(
        f"/v1alpha1/jobs/{job_id}/tasks/{lease_b.task_id}/checkpoints/lost-work"
        "?failed_at_step=35"
    )
    assert lost == {"lost_steps": 5, "latest_step": 30}

    # -- machine A: a different machine finishes the job ---------------------
    _register(coordinator, "machine-a")
    machine_a = ExecutorLoop(
        CoordinatorClient(coordinator.base_url), "machine-a", poll_seconds=0.2
    )
    assert machine_a.run(max_tasks=1) == 1

    assert coordinator.get(f"/v1alpha1/jobs/{job_id}")["state"] == "SUCCEEDED"
    [task] = coordinator.get(f"/v1alpha1/jobs/{job_id}/tasks")
    assert task["attempts"] == 2 and task["node_id"] == "machine-a"

    metrics = coordinator.get(f"/v1alpha1/artifacts/jobs/{job_id}/trial-000/metrics.json")
    assert metrics["resumed"] is True
    assert metrics["started_from_step"] == 30
    assert metrics["steps"] == 60

    # baseline sanity: an uninterrupted run produces identical weights
    from flashml_workloads.sgd_trainer import run_trainer
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "d.csv"
        data.write_bytes(make_dataset_csv())
        baseline = run_trainer(
            {"task_id": "b", "params": {"steps": 60, "checkpoint_every": 10,
                                        "lr": 0.1, "seed": 7},
             "inputs": {"dataset": str(data)}},
            Path(tmp) / "out",
        )
    assert metrics["weights"] == baseline["weights"]  # recovery changed nothing
