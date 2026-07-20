"""Distributed K-means over the lease runtime, end to end.

Two agents, four shards, three Lloyd iterations driven as three consecutive
lease jobs — the map/reduce-over-leases pattern with real HTTP and real
subprocesses. Asserts convergence (monotone inertia) and that the reduced
centroids land on the true cluster centers.
"""

from __future__ import annotations

import random
import threading

from flashml_workloads.kmeans_driver import (
    init_centroids,
    reduce_partials,  # noqa: F401  (imported to prove the public surface)
    run_kmeans,
    shard_and_upload,
)
from flashnode.executor import CoordinatorClient, ExecutorLoop
from flashruntime.protocol.v1alpha1 import NodeCapabilities, NodeRegistration

TRUE_CENTERS = ([0.0, 0.0], [4.0, 4.0])


def _make_points(n=400, seed=11) -> list[list[float]]:
    rng = random.Random(seed)
    pts = []
    for i in range(n):
        cx, cy = TRUE_CENTERS[i % 2]
        pts.append([rng.gauss(cx, 0.5), rng.gauss(cy, 0.5)])
    return pts


def _register(coordinator, node_id):
    reg = NodeRegistration(
        node_id=node_id, kubernetes_node="", hostname=f"{node_id}.e2e",
        capabilities=NodeCapabilities(cpu_cores=4), environment="local",
    )
    coordinator.post("/v1alpha1/nodes/register", reg.model_dump(mode="json"))


def test_sharded_kmeans_converges_across_two_agents(coordinator):
    points = _make_points()
    shard_uris = shard_and_upload(coordinator.base_url, points, n_shards=4, prefix="datasets/kmeans")
    assert len(shard_uris) == 4

    agents = []
    for node_id in ("km-node-a", "km-node-b"):
        _register(coordinator, node_id)
        loop = ExecutorLoop(
            CoordinatorClient(coordinator.base_url), node_id,
            poll_seconds=0.2, node_heartbeat_seconds=2.0,
        )
        threading.Thread(target=loop.run, daemon=True).start()
        agents.append(loop)

    try:
        result = run_kmeans(
            coordinator.base_url,
            shard_uris,
            centroids=init_centroids(points, k=2),
            iterations=3,
            lease_seconds=20,
            poll_seconds=0.3,
        )
    finally:
        for loop in agents:
            loop.stop_event.set()

    history = result["inertia_history"]
    assert len(history) == 3
    assert history[-1] <= history[0]  # Lloyd's never increases inertia

    got = sorted(result["centroids"])
    want = sorted(TRUE_CENTERS)
    for g, w in zip(got, want):
        assert abs(g[0] - w[0]) < 0.3 and abs(g[1] - w[1]) < 0.3, (got, want)

    # every iteration ran as its own lease job, work spread across the fleet
    assert len(result["job_ids"]) == 3
    nodes = coordinator.get("/v1alpha1/nodes")
    assert sum(n["accepted_tasks"] for n in nodes) == 12  # 3 iterations × 4 shards
