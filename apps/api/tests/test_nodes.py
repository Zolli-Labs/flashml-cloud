import time

from fastapi.testclient import TestClient

from flashml_cloud_api.app import create_app


def make_client(tmp_path, monkeypatch, offline_seconds="0.4"):
    monkeypatch.setenv("FLASHML_CLOUD_DB", str(tmp_path / "cloud.db"))
    monkeypatch.setenv("FLASHML_NODE_OFFLINE_SECONDS", offline_seconds)
    return TestClient(create_app())


REG = {
    "schema_version": "v1alpha1",
    "node_id": "fn-abc",
    "kubernetes_node": "worker-1",
    "hostname": "worker-1",
    "capabilities": {"cpu_cores": 4, "memory_bytes": 8 * 1024**3,
                     "gpus": [], "os": "linux", "architecture": "arm64"},
    "environment": "local",
    "sandbox_capable": False,
    "pool": "local",
    "runtime_profile": "kubernetes",
    "labels": {},
    "agent_version": "0.1.0",
}


def test_register_then_listed_online(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, offline_seconds="30")
    assert client.post("/v1alpha1/nodes/register", json=REG).status_code == 201
    nodes = client.get("/v1alpha1/nodes").json()
    assert len(nodes) == 1
    assert nodes[0]["online"] is True
    assert nodes[0]["registration"]["node_id"] == "fn-abc"


def test_heartbeat_requires_registration(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    hb = {"schema_version": "v1alpha1", "node_id": "fn-ghost", "status": "online"}
    assert client.post("/v1alpha1/nodes/fn-ghost/heartbeat", json=hb).status_code == 404


def test_heartbeat_node_id_mismatch_rejected(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    client.post("/v1alpha1/nodes/register", json=REG)
    hb = {"schema_version": "v1alpha1", "node_id": "fn-other", "status": "online"}
    assert client.post("/v1alpha1/nodes/fn-abc/heartbeat", json=hb).status_code == 422


def test_node_goes_offline_after_timeout(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, offline_seconds="0.3")
    client.post("/v1alpha1/nodes/register", json=REG)
    assert client.get("/v1alpha1/nodes").json()[0]["online"] is True
    time.sleep(0.4)
    assert client.get("/v1alpha1/nodes").json()[0]["online"] is False


def test_terminating_heartbeat_marks_offline(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, offline_seconds="30")
    client.post("/v1alpha1/nodes/register", json=REG)
    hb = {"schema_version": "v1alpha1", "node_id": "fn-abc", "status": "terminating"}
    client.post("/v1alpha1/nodes/fn-abc/heartbeat", json=hb)
    assert client.get("/v1alpha1/nodes").json()[0]["online"] is False


def test_runtime_unreachable_maps_to_502(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASHML_RUNTIME_API", "http://127.0.0.1:9")  # dead port
    client = make_client(tmp_path, monkeypatch)
    r = client.get("/v1alpha1/jobs")
    assert r.status_code == 502
    assert "unreachable" in r.json()["detail"]


def test_integration_panel_defaults_local(tmp_path, monkeypatch):
    for var in ("FLASHML_PROFILE", "FLASHML_SLS_ENABLED", "FLASHML_SANDBOX_POOL"):
        monkeypatch.delenv(var, raising=False)
    client = make_client(tmp_path, monkeypatch)
    status = client.get("/v1alpha1/integration").json()
    assert status["environment"] == "Local Kind"
    assert status["ack_connected"] is False
    assert status["sls_enabled"] is False
    assert status["sandbox_pool_available"] is False
    assert status["paidlc_adapter"] == "not implemented"
