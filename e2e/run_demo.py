"""The watchable version of the local loop — real processes, real SIGKILL.

    make e2e-demo     (or: e2e/.venv/bin/python e2e/run_demo.py)

Starts a coordinator (uvicorn) and two genuine `flashnode work` agent
processes, uploads a shared dataset to the coordinator's local artifact
host, submits a 12-trial sweep, SIGKILLs one agent mid-run, and narrates
until the job completes. Open the dashboard URL it prints to watch nodes go
red and the orphaned trial requeue in real time.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from conftest import make_dataset_csv

HERE = Path(__file__).parent
PYTHON = str(HERE / ".venv" / "bin" / "python")

TRIALS = (
    [{"model": "logreg", "C": c} for c in (0.01, 0.03, 0.1, 0.3, 1.0, 3.0)]
    + [{"model": "rf", "n_estimators": n} for n in (10, 25, 50, 75, 100, 150)]
)


def http(method: str, url: str, data: bytes | None = None, ctype="application/json"):
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": ctype} if data else {})
    with urllib.request.urlopen(req, timeout=10) as r:
        body = r.read()
        return json.loads(body) if body else None


def main() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    state = Path(tempfile.mkdtemp(prefix="flashml-demo-"))
    print(f"▶ state dir: {state}")

    env = {**os.environ, "FLASHML_ENABLE_KUBERAY": "0",
           "FLASHML_LEDGER_PATH": str(state / "ledger.db"),
           "FLASHML_LOCAL_ARTIFACTS_DIR": str(state / "artifacts")}
    procs: list[subprocess.Popen] = []
    try:
        # cwd = state dir: running from the workspace root would let the
        # repo *directories* shadow the installed packages on sys.path.
        procs.append(subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "flashruntime.service.app:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            env=env, cwd=state))
        for _ in range(100):
            try:
                http("GET", f"{base}/healthz")
                break
            except OSError:
                time.sleep(0.2)
        print(f"▶ coordinator up — dashboard: {base}/")

        http("PUT", f"{base}/v1alpha1/artifacts/datasets/demo/train.csv",
             make_dataset_csv(), ctype="application/octet-stream")
        print("▶ shared dataset uploaded to the coordinator (local artifact host)")

        agents = []
        flashnode_bin = str(HERE / ".venv" / "bin" / "flashnode")
        for name in ("machine-a", "machine-b"):
            agent_state = state / name
            agent_state.mkdir(parents=True, exist_ok=True)
            agent_env = {**os.environ, "FLASHNODE_STATE_DIR": str(agent_state),
                         "FLASHNODE_COORDINATOR_URL": base}
            p = subprocess.Popen([flashnode_bin, "work"], env=agent_env, cwd=agent_state)
            agents.append(p)
            procs.append(p)
        print("▶ two flashnode agents started (separate processes)")

        job = http("POST", f"{base}/v1alpha1/jobs", json.dumps({
            "apiVersion": "flashml.dev/v1alpha1", "kind": "Job",
            "metadata": {"name": "demo-sweep"},
            "spec": {
                "execution": {"backend": "leases"},
                "image": {"repository": "local/tier1", "tag": "dev"},
                "workload": {"type": "hyperparameter_search", "parameters": {
                    "trials": TRIALS,
                    "inputs": {"dataset": "artifact://datasets/demo/train.csv"},
                    "lease_seconds": 8}},
            }}).encode())
        job_id = job["job_id"]
        print(f"▶ job {job_id}: {len(TRIALS)} trials submitted — watch {base}/")

        killed = False
        while True:
            time.sleep(2)
            tasks = http("GET", f"{base}/v1alpha1/jobs/{job_id}/tasks")
            done = sum(1 for t in tasks if t["state"] == "COMPLETED")
            job_state = http("GET", f"{base}/v1alpha1/jobs/{job_id}")["state"]
            print(f"  … {done}/{len(tasks)} completed ({job_state})")
            if not killed and done >= 4:
                agents[1].send_signal(signal.SIGKILL)
                killed = True
                print("💥 SIGKILL machine-b — watch its node go red and its task requeue")
            if job_state in ("SUCCEEDED", "FAILED"):
                break

        print(f"\n▶ job {job_state}")
        for t in http("GET", f"{base}/v1alpha1/jobs/{job_id}/tasks"):
            marker = "  ← recovered" if t["attempts"] > 1 else ""
            print(f"  {t['task_id']}  {t['state']:<9} attempts={t['attempts']} "
                  f"node={t['node_id']}{marker}")
        for n in http("GET", f"{base}/v1alpha1/nodes"):
            print(f"  node {n['node_id'][:16]}… online={n['online']} "
                  f"accepted={n['accepted_tasks']}")
        best = None
        for a in http("GET", f"{base}/v1alpha1/jobs/{job_id}/artifacts"):
            if a["key"].endswith("metrics.json"):
                m = http("GET", f"{base}/v1alpha1/artifacts/{a['key']}")
                if best is None or m["accuracy_mean"] > best["accuracy_mean"]:
                    best = m
        if best:
            print(f"\n▶ best trial: {best['params']} → accuracy {best['accuracy_mean']}")
        print(f"▶ dashboard stays up for inspection: {base}/  (Ctrl-C to stop)")
        try:
            procs[0].wait()
        except KeyboardInterrupt:
            pass
        return 0
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
