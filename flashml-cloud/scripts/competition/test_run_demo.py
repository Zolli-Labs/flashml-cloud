"""`run_demo.py`, driven against a fake control plane.

    flashml-cloud/apps/api/.venv/bin/python -m pytest \
        flashml-cloud/scripts/competition/test_run_demo.py -v

**No Alibaba, ever.** Every route the demo touches is served here by a stdlib
HTTP server returning the shapes the real API returns, so the loop —
submit, hibernate, wake, evaluate, clean up — is exercised end to end without
provisioning anything, without a credential, and without a sandbox that bills
by the second. That is a constraint on this test and also the only way to run
the demo's failure paths at all: there is no way to ask a real sandbox to time
out on demand.

The fake is not a stub that agrees with everything. Three things make it a
real check rather than a mirror:

* **The `evaluation_spec` is compiled by the REAL
  `app.build_evaluation_jobspec`.** If `run_demo` sends a spec the API would
  refuse — an unpinned image, a command that is not a list of strings — this
  test fails exactly where the API would, with the API's own message.
* **The inlined workload is actually executed.** `test_the_inlined_workload_
  runs` takes the argv `run_demo` submitted, runs it against a real model over
  loopback HTTP, and checks the metrics that come out. The inline payload is
  this script's workaround for a spec that cannot stage code (see
  `run_demo.__doc__`), and a workaround nobody runs is a guess.
* **Cleanup is asserted on the failure paths, not only the happy one.** A
  leaked sandbox is the expensive bug, and it is precisely the one a
  happy-path test never sees.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
RUN_DEMO = HERE / "run_demo.py"
REPO_ROOT = HERE.parents[2]
API_DIR = REPO_ROOT / "flashml-cloud" / "apps" / "api"
WORKLOADS = REPO_ROOT / "e2e" / "competition"

TOKEN = "fmu_faketoken"
JOB_ID = "543b2c82b152"
SESSION_ID = "11111111-2222-3333-4444-555555555555"
SHARE_TOKEN = "shr_abcdef"

#: The training result the fake serves, copied in shape from a real
#: `metrics.json` this workload produced (`run_local_recovery.sh`'s own run).
TRAIN_METRICS = {
    "schema": "flashml.competition.train-metrics/v1",
    "workload": "train_checkpointed",
    "accuracy": 0.9516666666666667,
    "epochs": 60,
    "epochs_executed": 35,
    "resumed": True,
    "resumed_from_step": 8325,
    "recomputed_steps": 0,
    "model_file": "model.json",
    "model_sha256":
        "aa90ea25f13f21281e7616e95daded6408e1582ff303813033d518cb91128d07",
}

#: The ledger a healthy session writes, in order. Types are
#: `sandbox_orchestrator`'s own.
HAPPY_EVENTS = [
    ("session.opened", None),
    ("sandbox.created", 912.0),
    ("sandbox.hibernated", 2611.0),
    ("artifact.observed", 41.0),
    ("session.resuming", None),
    ("sandbox.woken", 1840.0),
    ("artifacts.presigned", 120.0),
    ("evaluation.started", None),
    ("evaluation.submitted", 63.0),
    ("evaluation.accepted", 4120.0),
]

FAILING_EVENTS = HAPPY_EVENTS[:-1] + [("session.failed", None)]


class FakeApi:
    """The six routes `run_demo` drives, plus the reads around them."""

    def __init__(self, *, events, existing_session=None, existing_jobs=()):
        self.events = list(events)
        self.existing_session = existing_session
        self.jobs = list(existing_jobs)
        # What the test inspects afterwards.
        self.calls: list[tuple[str, str]] = []
        self.evaluation_spec: dict | None = None
        self.sessions_created = 0
        self.model_ready_calls = 0
        self.cleanup_calls = 0
        self.unauthenticated: list[str] = []
        self.state = "HIBERNATED"

    # -- routing -----------------------------------------------------------

    def handle(self, method: str, path: str, query: dict, body) -> tuple[int, object]:
        self.calls.append((method, path))
        parts = [p for p in path.split("/") if p]

        if method == "GET" and path == "/v1alpha1/jobs":
            return 200, self.jobs
        if method == "POST" and path == "/v1alpha1/jobs/from-repo":
            self.jobs.append({"job_id": JOB_ID, "name": "flashml-competition-train"})
            return 201, {"job_id": JOB_ID, "state": "PENDING", "findings": []}
        if method == "GET" and parts[:2] == ["v1alpha1", "jobs"] and len(parts) == 3:
            return 200, {"job_id": parts[2], "state": "SUCCEEDED"}
        if method == "GET" and "artifacts" in parts:
            return 200, TRAIN_METRICS
        if method == "GET" and parts[-1] == "sandbox-sessions" and "jobs" in parts:
            return 200, [self.existing_session] if self.existing_session else []

        if method == "POST" and path == "/v1alpha1/sandbox-sessions":
            self.sessions_created += 1
            self.evaluation_spec = body.get("evaluation_spec")
            # The real route compiles the spec BEFORE provisioning anything,
            # and answers 400 for one it cannot compile. Same order here, and
            # with the same function — so a spec this fake accepts is a spec
            # the API would have accepted.
            sys.path.insert(0, str(API_DIR))
            from flashml_cloud_api.app import (  # noqa: PLC0415
                EvaluationSpecError,
                build_evaluation_jobspec,
            )
            try:
                build_evaluation_jobspec(
                    session_id="00000000-0000-0000-0000-000000000000",
                    pool_id="sandbox",
                    training_job_id=body.get("training_job_id"),
                    spec=self.evaluation_spec,
                )
            except EvaluationSpecError as exc:
                return 400, {"detail": str(exc)}
            return 201, {"session_id": SESSION_ID, "state": "HIBERNATED",
                         "share_token": SHARE_TOKEN}

        if method == "POST" and parts[-1] == "model-ready":
            self.model_ready_calls += 1
            return 202, {"session_id": SESSION_ID, "state": self.state}
        if method == "POST" and parts[-1] == "cleanup":
            self.cleanup_calls += 1
            self.state = "TERMINATED"
            return 200, {"session_id": SESSION_ID, "state": "TERMINATED"}
        if method == "GET" and parts[-1] == "events":
            after = int(query.get("after_sequence", ["0"])[0])
            return 200, [
                {
                    "sequence": i + 1,
                    "type": kind,
                    "source": "controller",
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "latency_ms": latency,
                    "data": {},
                }
                for i, (kind, latency) in enumerate(self.events)
                if i + 1 > after
            ]
        if method == "GET" and parts[:2] == ["v1alpha1", "sandbox-sessions"]:
            terminal = self.events[-1][0]
            return 200, {
                "id": SESSION_ID,
                "state": "SUCCEEDED" if terminal == "evaluation.accepted" else "FAILED",
                "evaluation_job_id": "fc-eval-1234",
                "error_code": None if terminal == "evaluation.accepted" else "EvaluationRejected",
                "share_token": SHARE_TOKEN,
            }
        return 404, {"detail": f"fake has no route for {method} {path}"}


class _Handler(BaseHTTPRequestHandler):
    api: FakeApi = None  # type: ignore[assignment]

    def _dispatch(self, method: str):
        parsed = urllib.parse.urlsplit(self.path)
        # Every route `run_demo` uses is authenticated. Recording the misses
        # rather than 401-ing keeps a missing header visible as a named test
        # failure instead of an opaque "API answered 401".
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.api.unauthenticated.append(parsed.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        status, payload = self.api.handle(
            method, parsed.path, urllib.parse.parse_qs(parsed.query), body
        )
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")

    def log_message(self, *args):
        return


@pytest.fixture()
def serve():
    servers = []

    def start(api: FakeApi) -> str:
        handler = type("_H", (_Handler,), {"api": api})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def demo(base: str, *extra: str) -> subprocess.CompletedProcess:
    """`run_demo.py` as a subprocess, exactly as an operator runs it.

    The API venv's interpreter, because `run_demo` reads
    `flashml.evaluate.yaml` with PyYAML and resolves the curated image alias
    through `flashml_cloud_api.images` — the two things it needs an
    environment for, and both of which it refuses clearly without.
    """
    return subprocess.run(
        [str(API_DIR / ".venv" / "bin" / "python"), str(RUN_DEMO),
         "--api-base", base, "--token", TOKEN,
         "--console-base", "https://console.example",
         "--poll", "0.05", *extra],
        capture_output=True, text=True, timeout=180,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------


def test_the_whole_loop_runs_and_cleans_up(serve):
    api = FakeApi(events=HAPPY_EVENTS)
    result = demo(serve(api))

    assert result.returncode == 0, result.stdout + result.stderr
    assert api.sessions_created == 1
    assert api.model_ready_calls == 1
    assert api.cleanup_calls == 1, "the sandbox was not cleaned up"
    assert not api.unauthenticated, f"unauthenticated: {api.unauthenticated}"

    # Every beat the requirement names, narrated in order.
    for beat in ("sandbox.hibernated", "sandbox.woken", "evaluation.accepted"):
        assert beat in result.stdout, beat
    assert "timeline" in result.stdout
    assert f"https://console.example/share/{SHARE_TOKEN}" in result.stdout
    assert TRAIN_METRICS["model_sha256"] in result.stdout


def test_the_evaluation_spec_asserts_the_trained_models_hash(serve):
    """The evaluation must be pinned to the model the training job reported.

    Without `--expect-sha256` the sandbox would score whatever it was handed
    and report a number that looks exactly as good.
    """
    api = FakeApi(events=HAPPY_EVENTS)
    assert demo(serve(api)).returncode == 0

    command = api.evaluation_spec["command"]
    assert command[0] == "python" and command[1] == "-c"
    assert "--expect-sha256" in command
    assert command[command.index("--expect-sha256") + 1] == TRAIN_METRICS["model_sha256"]
    # And the argv the config declares survived verbatim into it.
    assert "--artifacts" in command and "/home/user/.flashml/artifacts.json" in command


def test_the_evaluation_image_is_a_pinned_reference(serve):
    """`build_evaluation_jobspec` has no alias registry — an alias would be
    split on its last colon and refused. The alias must be resolved before it
    is sent."""
    api = FakeApi(events=HAPPY_EVENTS)
    assert demo(serve(api)).returncode == 0

    image = api.evaluation_spec["image"]
    assert image != "python-slim"
    repository, _, tag = image.rpartition(":")
    assert repository and tag, image


def test_the_inlined_workload_runs(serve, tmp_path):
    """Take the argv the demo submitted and actually run it.

    The spec cannot stage code (see `run_demo.__doc__`), so the evaluator is
    carried inline as a base64 tarball and unpacked by a bootstrap that runs
    inside a sandbox nobody can attach a debugger to. This is the test that
    the workaround works: real argv, a real model served over loopback, real
    metrics out the far end.
    """
    api = FakeApi(events=HAPPY_EVENTS)
    assert demo(serve(api)).returncode == 0
    command = list(api.evaluation_spec["command"])

    # A real model, produced by the real trainer.
    out = tmp_path / "train"
    subprocess.run(
        [sys.executable, str(WORKLOADS / "train_checkpointed.py"),
         "--out", str(out), "--resume", str(tmp_path / "none.json"),
         "--epochs", "4", "--samples", "300", "--features", "8",
         "--hidden", "8", "--batch-size", "30"],
        check=True, capture_output=True, timeout=120,
    )
    model = (out / "model.json").read_bytes()
    trained = json.loads((out / "metrics.json").read_text())

    from http.server import BaseHTTPRequestHandler as _B

    class _Model(_B):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(model)))
            self.end_headers()
            self.wfile.write(model)

        def log_message(self, *a):
            return

    origin = ThreadingHTTPServer(("127.0.0.1", 0), _Model)
    threading.Thread(target=origin.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{origin.server_address[1]}/model.json"

    artifacts = tmp_path / "artifacts.json"
    artifacts.write_text(json.dumps({
        "schema": "flashml.sandbox-artifacts/v1",
        "session_id": SESSION_ID, "job_id": JOB_ID,
        "issued_at": "2026-08-11T00:00:00Z", "ttl_s": 900,
        "objects": {f"jobs/{JOB_ID}/task-000/model.json": url},
    }))

    # Point the argv at this model instead of the fake's, exactly the way the
    # sandbox would have been pointed at a presigned one.
    command[command.index("--artifacts") + 1] = str(artifacts)
    command[command.index("--expect-sha256") + 1] = trained["model_sha256"]
    command[command.index("--out") + 1] = str(tmp_path / "evalout")
    command[0] = sys.executable  # `python` is `python3` on this host

    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    finally:
        origin.shutdown()
        origin.server_close()

    assert result.returncode == 0, result.stdout + result.stderr
    metrics = json.loads((tmp_path / "evalout" / "metrics.json").read_text())
    assert metrics["model_sha256"] == trained["model_sha256"]
    assert metrics["accuracy"] == trained["accuracy"]
    assert metrics["training_job_id"] == JOB_ID


# ---------------------------------------------------------------------------
# restartability, and the paths that leak money
# ---------------------------------------------------------------------------


def test_a_rerun_adopts_the_open_session_instead_of_opening_a_second(serve):
    """The expensive mistake this guards: two sandboxes for one demo.

    A session that is not settled is the previous run's, and building another
    beside it doubles the bill while making the console show two answers for
    one question.
    """
    api = FakeApi(
        events=HAPPY_EVENTS,
        existing_session={"id": SESSION_ID, "state": "HIBERNATED",
                          "share_token": SHARE_TOKEN},
        existing_jobs=[{"job_id": JOB_ID, "name": "flashml-competition-train"}],
    )
    result = demo(serve(api))

    assert result.returncode == 0, result.stdout + result.stderr
    assert api.sessions_created == 0, "opened a second sandbox"
    assert "adopting session" in result.stdout
    assert api.cleanup_calls == 1


def test_a_rerun_adopts_the_existing_training_job(serve):
    api = FakeApi(
        events=HAPPY_EVENTS,
        existing_jobs=[{"job_id": JOB_ID, "name": "flashml-competition-train"}],
    )
    result = demo(serve(api))

    assert result.returncode == 0, result.stdout + result.stderr
    assert ("POST", "/v1alpha1/jobs/from-repo") not in api.calls
    assert "found an existing" in result.stdout


def test_a_failed_evaluation_still_cleans_up(serve):
    """The path a happy-path test never reaches, and the one that costs money.

    The run must fail — a demo that reports success on a rejected evaluation
    is worse than one that fails — and the sandbox must still be killed.
    """
    api = FakeApi(events=FAILING_EVENTS)
    result = demo(serve(api))

    assert result.returncode == 2
    assert api.cleanup_calls == 1, "a failed run leaked its sandbox"
    assert "session.failed" in result.stdout


def test_an_unreachable_api_fails_before_anything_is_built():
    """A wrong `--api-base` must say so, not traceback."""
    result = demo("http://127.0.0.1:1")
    assert result.returncode == 2
    assert "could not reach" in result.stderr
    assert "--api-base" in result.stderr


def test_keep_sandbox_skips_cleanup_and_says_what_it_costs(serve):
    api = FakeApi(events=HAPPY_EVENTS)
    result = demo(serve(api), "--keep-sandbox")

    assert result.returncode == 0, result.stdout + result.stderr
    assert api.cleanup_calls == 0
    assert "still running" in result.stdout
    assert "bills by the second" in result.stdout


def test_a_missing_token_is_refused_before_any_request():
    result = subprocess.run(
        [str(API_DIR / ".venv" / "bin" / "python"), str(RUN_DEMO),
         "--api-base", "http://127.0.0.1:1"],
        capture_output=True, text=True, timeout=60,
        env={k: v for k, v in os.environ.items() if k != "FLASHML_TOKEN"},
    )
    assert result.returncode != 0
    assert "token" in result.stderr.lower()


def test_the_inline_payload_is_byte_stable(serve):
    """Two runs build the same argv.

    `EvaluationDriver.submit` recognises a resubmission by the job NAME, so
    an argv that changed between runs would produce one name over two
    different evaluations — and nothing would notice.
    """
    first, second = FakeApi(events=HAPPY_EVENTS), FakeApi(events=HAPPY_EVENTS)
    assert demo(serve(first)).returncode == 0
    assert demo(serve(second)).returncode == 0
    assert first.evaluation_spec["command"] == second.evaluation_spec["command"]
