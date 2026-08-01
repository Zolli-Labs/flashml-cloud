"""Prove the write-scope exploit is closed against a REAL coordinator process
over real HTTP — not the in-process `TestClient` every unit test used.

Everything asserted here was already proven with `TestClient` in
`flashruntime/tests/test_service_write_scope.py`. This file re-proves the
same claims against a real uvicorn subprocess (the `secured_coordinator`
fixture in `e2e/conftest.py`), because that subprocess-over-HTTP path is the
configuration that actually gets deployed. The project's standing rule: the
coordinator does not go on a public address until this passes.

The core exploit under test: one volunteer machine overwriting another's
already-committed result, or stealing a task by force-failing the lease that
covers it.
"""

from __future__ import annotations

import time

import requests

from flashruntime.protocol.v1alpha1 import NodeCapabilities, NodeRegistration

NODE_TOKENS = {"node-a": "tok-a", "node-b": "tok-b"}
OPERATOR_TOKEN = "op-tok"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(coordinator, node_id: str) -> None:
    reg = NodeRegistration(
        node_id=node_id,
        kubernetes_node="",
        hostname=f"{node_id}.e2e",
        capabilities=NodeCapabilities(cpu_cores=2, memory_bytes=4_000_000_000),
        environment="local",
    )
    r = requests.post(
        f"{coordinator.base_url}/v1alpha1/nodes/register",
        json=reg.model_dump(mode="json"),
        headers=_auth(NODE_TOKENS[node_id]),
        timeout=10,
    )
    assert r.status_code == 200, r.text


def _submit_job(coordinator, lease_seconds: float = 60.0, trials: int = 2) -> str:
    """One hyperparameter_search job, `trials` independent leasable tasks."""
    r = requests.post(
        f"{coordinator.base_url}/v1alpha1/jobs",
        json={
            "apiVersion": "flashml.dev/v1alpha1",
            "kind": "Job",
            "metadata": {"name": "write-scope"},
            "spec": {
                "execution": {"backend": "leases"},
                "image": {"repository": "local/tier1", "tag": "dev"},
                "workload": {
                    "type": "hyperparameter_search",
                    "parameters": {
                        "trials": [{"C": float(i + 1)} for i in range(trials)],
                        "lease_seconds": lease_seconds,
                    },
                },
            },
        },
        timeout=10,
    )
    assert r.status_code == 201, r.text
    return r.json()["job_id"]


def _claim(coordinator, node_id: str, body_node_id: str | None = None) -> dict:
    r = requests.post(
        f"{coordinator.base_url}/v1alpha1/leases/claim",
        json={"node_id": body_node_id or node_id},
        headers=_auth(NODE_TOKENS[node_id]),
        timeout=10,
    )
    assert r.status_code == 200, r.text
    lease = r.json()
    assert lease, "expected a claimable task, got no lease"
    return lease


def _artifact_url(coordinator, key: str) -> str:
    return f"{coordinator.base_url}/v1alpha1/artifacts/{key}"


# -- 1: no token -------------------------------------------------------------


def test_write_with_no_token_is_401(secured_coordinator):
    r = requests.put(
        _artifact_url(secured_coordinator, "jobs/j/trial-000/metrics.json"),
        data=b"{}",
        timeout=10,
    )
    assert r.status_code == 401, r.text


# -- 2: forged token -----------------------------------------------------


def test_write_with_forged_token_is_401(secured_coordinator):
    r = requests.put(
        _artifact_url(secured_coordinator, "jobs/j/trial-000/metrics.json"),
        data=b"{}",
        headers=_auth("totally-made-up-token"),
        timeout=10,
    )
    assert r.status_code == 401, r.text


# -- 3: the core exploit — one volunteer overwriting another's task ---------


def test_node_b_cannot_write_under_a_task_node_a_holds(secured_coordinator):
    _register(secured_coordinator, "node-a")
    _register(secured_coordinator, "node-b")
    job_id = _submit_job(secured_coordinator)
    lease = _claim(secured_coordinator, "node-a")
    task_id = lease["payload"]["task_id"]
    assert lease["node_id"] == "node-a"

    r = requests.put(
        _artifact_url(secured_coordinator, f"jobs/{job_id}/{task_id}/metrics.json"),
        data=b"evil-overwrite",
        headers=_auth("tok-b"),
        timeout=10,
    )
    assert r.status_code == 403, r.text


# -- 4: the legitimate holder can write its own task -------------------------


def test_node_a_can_write_under_its_own_held_task(secured_coordinator):
    _register(secured_coordinator, "node-a")
    job_id = _submit_job(secured_coordinator)
    lease = _claim(secured_coordinator, "node-a")
    task_id = lease["payload"]["task_id"]

    r = requests.put(
        _artifact_url(secured_coordinator, f"jobs/{job_id}/{task_id}/metrics.json"),
        data=b'{"accuracy": 0.9}',
        headers=_auth("tok-a"),
        timeout=10,
    )
    assert r.status_code == 200, r.text


# -- 5: an expired lease revokes the write, even for the original holder ----


def test_write_after_lease_expiry_is_403(secured_coordinator):
    _register(secured_coordinator, "node-a")
    job_id = _submit_job(secured_coordinator, lease_seconds=2.0, trials=1)
    lease = _claim(secured_coordinator, "node-a")
    task_id = lease["payload"]["task_id"]

    # Sanity: the write is allowed right now, while the lease is live.
    ok = requests.put(
        _artifact_url(secured_coordinator, f"jobs/{job_id}/{task_id}/partial.json"),
        data=b"{}",
        headers=_auth("tok-a"),
        timeout=10,
    )
    assert ok.status_code == 200, ok.text

    time.sleep(3.0)  # past the 2s lease_seconds; liveness is deadline-checked directly

    r = requests.put(
        _artifact_url(secured_coordinator, f"jobs/{job_id}/{task_id}/metrics.json"),
        data=b"too-late",
        headers=_auth("tok-a"),
        timeout=10,
    )
    assert r.status_code == 403, r.text


# -- 6: the federated-averaging model key is outside any task prefix --------


def test_round_weights_key_is_403_for_node_200_for_operator(secured_coordinator):
    _register(secured_coordinator, "node-a")
    job_id = _submit_job(secured_coordinator)
    _claim(secured_coordinator, "node-a")  # holds a live lease, but not on this key
    key = f"jobs/{job_id}/round-000/weights.json"

    node_write = requests.put(
        _artifact_url(secured_coordinator, key),
        data=b"averaged-weights",
        headers=_auth("tok-a"),
        timeout=10,
    )
    assert node_write.status_code == 403, node_write.text

    operator_write = requests.put(
        _artifact_url(secured_coordinator, key),
        data=b"averaged-weights",
        headers=_auth(OPERATOR_TOKEN),
        timeout=10,
    )
    assert operator_write.status_code == 200, operator_write.text


# -- 7: node-b cannot fail or complete a lease node-a holds ------------------


def test_node_b_cannot_fail_or_complete_node_a_lease(secured_coordinator):
    _register(secured_coordinator, "node-a")
    _register(secured_coordinator, "node-b")
    job_id = _submit_job(secured_coordinator, trials=2)
    lease = _claim(secured_coordinator, "node-a")
    lease_id = lease["lease_id"]

    fail_r = requests.post(
        f"{secured_coordinator.base_url}/v1alpha1/attempts/{lease_id}/fail",
        json={"reason": "steal-the-task"},
        headers=_auth("tok-b"),
        timeout=10,
    )
    assert fail_r.status_code == 403, fail_r.text

    complete_r = requests.post(
        f"{secured_coordinator.base_url}/v1alpha1/attempts/{lease_id}/complete",
        json={"output_sha256": "0" * 64},
        headers=_auth("tok-b"),
        timeout=10,
    )
    assert complete_r.status_code == 403, complete_r.text

    # The victim still holds its own lease, undisturbed.
    tasks = requests.get(
        f"{secured_coordinator.base_url}/v1alpha1/jobs/{job_id}/tasks", timeout=10
    ).json()
    held = next(t for t in tasks if t["task_id"] == lease["payload"]["task_id"])
    assert held["state"] == "LEASED"
    assert held["node_id"] == "node-a"


# -- 8: claim resolves node_id from the token, never the request body -------


def test_claim_body_node_id_is_not_authoritative(secured_coordinator):
    _register(secured_coordinator, "node-a")
    _register(secured_coordinator, "node-b")
    _submit_job(secured_coordinator, trials=1)

    lease = _claim(secured_coordinator, "node-b", body_node_id="node-a")
    assert lease["node_id"] == "node-b", (
        "node-b's token claimed under a body that said node-a; the coordinator "
        "must have served this as node-b, not node-a"
    )
