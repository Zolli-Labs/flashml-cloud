"""FlashML Cloud API.

Backend-neutral by design: it knows FlashRuntime's public API and protocol,
never Ray/KubeRay specifics. Environment:

  FLASHML_RUNTIME_API      FlashRuntime base URL (default http://localhost:8100)
  FLASHML_CLOUD_DB         SQLite path (default /data/flashml-cloud.db)
  FLASHML_NODE_OFFLINE_SECONDS  heartbeat-loss threshold (default 30)
  FLASHML_PROFILE          local | alibaba-ack (display only)
  Alibaba panel display:   FLASHML_ACK_CONNECTED, FLASHML_ACR_IMAGE,
                           FLASHML_OSS_BUCKET, FLASHML_SLS_ENABLED,
                           FLASHML_PROMETHEUS_ENABLED, FLASHML_SANDBOX_POOL
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from flashruntime.protocol.v1alpha1 import JobSpec, NodeHeartbeat, NodeRegistration

from flashml_cloud_api.store import NodeStore

log = logging.getLogger("flashml-cloud-api")
logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","service":"flashml-cloud-api","msg":%(message)s}',
)


def create_app() -> FastAPI:
    runtime_api = os.environ.get("FLASHML_RUNTIME_API", "http://localhost:8100").rstrip("/")
    db_path = os.environ.get("FLASHML_CLOUD_DB", "flashml-cloud.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    store = NodeStore(
        db_path,
        offline_after_seconds=float(os.environ.get("FLASHML_NODE_OFFLINE_SECONDS", "30")),
    )

    app = FastAPI(title="FlashML Cloud API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=os.environ.get("FLASHML_CORS_ORIGINS", "*").split(","),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.store = store

    async def _proxy(method: str, path: str, body: dict | None = None) -> Response:
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                r = await client.request(method, f"{runtime_api}{path}", json=body)
            except httpx.ConnectError as exc:
                raise HTTPException(status_code=502,
                                    detail=f"FlashRuntime unreachable: {exc}")
        return Response(content=r.content, status_code=r.status_code,
                        media_type="application/json")

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    # -- nodes -------------------------------------------------------------

    @app.post("/v1alpha1/nodes/register", status_code=201)
    async def register_node(registration: NodeRegistration):
        store.register(registration)
        log.info(json.dumps({"text": "node registered",
                             "node_id": registration.node_id,
                             "k8s_node": registration.kubernetes_node}))
        return {"node_id": registration.node_id, "status": "registered"}

    @app.post("/v1alpha1/nodes/{node_id}/heartbeat")
    async def node_heartbeat(node_id: str, hb: NodeHeartbeat):
        if hb.node_id != node_id:
            raise HTTPException(status_code=422, detail="node_id mismatch")
        if not store.heartbeat(hb):
            raise HTTPException(status_code=404,
                                detail=f"unregistered node: {node_id}")
        return {"status": "ok"}

    @app.get("/v1alpha1/nodes")
    async def list_nodes():
        return store.list_nodes()

    # -- jobs (delegated to FlashRuntime) ----------------------------------

    @app.post("/v1alpha1/jobs", status_code=201)
    async def submit_job(spec: JobSpec):
        return await _proxy("POST", "/v1alpha1/jobs",
                            body=json.loads(spec.model_dump_json()))

    @app.get("/v1alpha1/jobs")
    async def list_jobs():
        return await _proxy("GET", "/v1alpha1/jobs")

    @app.get("/v1alpha1/jobs/{job_id}")
    async def get_job(job_id: str):
        return await _proxy("GET", f"/v1alpha1/jobs/{job_id}")

    @app.get("/v1alpha1/jobs/{job_id}/events")
    async def get_events(job_id: str):
        return await _proxy("GET", f"/v1alpha1/jobs/{job_id}/events")

    @app.get("/v1alpha1/jobs/{job_id}/logs")
    async def get_logs(job_id: str):
        return await _proxy("GET", f"/v1alpha1/jobs/{job_id}/logs")

    @app.post("/v1alpha1/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        return await _proxy("POST", f"/v1alpha1/jobs/{job_id}/cancel")

    # -- deployment / Alibaba integration panel ----------------------------

    @app.get("/v1alpha1/integration")
    async def integration_status():
        env = os.environ
        profile = env.get("FLASHML_PROFILE", "local")
        return {
            "profile": profile,
            "backend": "ray/kuberay",
            "environment": "Alibaba ACK" if profile == "alibaba-ack" else "Local Kind",
            "artifact_store": env.get("FLASHML_ARTIFACT_BACKEND", "minio"),
            "image_registry": env.get("FLASHML_ACR_IMAGE", "local"),
            "ack_connected": profile == "alibaba-ack",
            "oss_bucket": env.get("FLASHML_OSS_BUCKET", ""),
            "sls_enabled": env.get("FLASHML_SLS_ENABLED", "false") == "true",
            "prometheus_enabled": env.get("FLASHML_PROMETHEUS_ENABLED", "false") == "true",
            "sandbox_pool_available": env.get("FLASHML_SANDBOX_POOL", "") != "",
            "paidlc_adapter": "not implemented",
        }

    return app


app = create_app()
