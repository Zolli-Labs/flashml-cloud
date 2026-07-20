"""E2E fixtures: a real coordinator subprocess and a shared dataset.

The coordinator is uvicorn in a child process with tmp-path state and the
KubeRay backend disabled — exactly the self-hosted profile a lab would run.
Agents in the tests talk to it over real localhost HTTP.
"""

from __future__ import annotations

import json
import os
import random
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Coordinator:
    def __init__(self, base_url: str, process: subprocess.Popen):
        self.base_url = base_url
        self.process = process

    def get(self, path: str):
        with urllib.request.urlopen(f"{self.base_url}{path}", timeout=10) as r:
            return json.loads(r.read())

    def post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def put_bytes(self, path: str, data: bytes) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method="PUT",
            headers={"Content-Type": "application/octet-stream"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())


@pytest.fixture()
def coordinator(tmp_path: Path):
    port = _free_port()
    env = {
        **os.environ,
        "FLASHML_ENABLE_KUBERAY": "0",
        "FLASHML_SERVICE_AUTOINIT": "1",
        "FLASHML_LEDGER_PATH": str(tmp_path / "ledger.db"),
        "FLASHML_LOCAL_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "flashruntime.service.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=env,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=1):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise RuntimeError("coordinator did not become healthy in 20s")
                time.sleep(0.2)
        yield Coordinator(base, proc)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def make_dataset_csv(n: int = 240, features: int = 6, seed: int = 7) -> bytes:
    """Deterministic, linearly separable two-class dataset — pure stdlib, so
    the harness itself needs no ML dependencies. Headerless CSV, label last."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        label = i % 2
        shift = 1.6 * label
        rows.append(
            ",".join(f"{rng.gauss(shift, 1.0):.4f}" for _ in range(features)) + f",{label}"
        )
    return ("\n".join(rows) + "\n").encode()
