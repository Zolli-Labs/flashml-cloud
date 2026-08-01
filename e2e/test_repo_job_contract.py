"""The repo-job contract, asserted across all three repos at once.

A user pastes a GitHub URL. Three components then have to agree about one
path, and none of them can see the other two:

1. **flashml-cloud** compiles ``python /work/inputs/code/<entrypoint>`` and
   declares ``unpack_inputs: ["code"]``.
2. **flashruntime**'s ``CommandRecipe`` forwards both into the task payload
   a node will claim.
3. **flashnode** reads ``unpack_inputs``, extracts that input into
   ``inputs/code/`` (stripping GitHub's ``owner-repo-<sha>/`` wrapper), and
   binds the workdir at ``/work``.

Every one of those steps had tests. The system still shipped broken: the
recipe had a fixed payload key set with no ``unpack_inputs`` in it, so the
API's declaration was dropped in transit, the tarball landed on the node as
a single gzip *file*, and every repo job died on ``python: can't open file
'/work/inputs/code/train.py'``. Three green suites, one broken product —
because the contract lives strictly *between* them, and nothing in any one
repo could see it.

So this test does not assert any of the three steps. It runs a realistic
GitHub tarball through all of them and asserts the only thing that actually
matters: **the file the compiled argv names exists on the node that will run
it.** It fails if any single side changes its half of the path.

This is the workspace ``e2e/`` suite, one of the two places allowed to
import all three repos (see ``test_archive_parity.py`` for the other, and
why the duplication it guards exists at all).
"""

from __future__ import annotations

import io
import json
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parent.parent
CLOUD_API_SRC = WORKSPACE / "flashml-cloud" / "apps" / "api"

# Same reasoning as test_archive_parity.py: the private API is not installed
# into e2e/.venv, and the modules used here need only pyyaml + flashruntime,
# both of which the harness already has.
if str(CLOUD_API_SRC) not in sys.path:
    sys.path.insert(0, str(CLOUD_API_SRC))

from flashml_cloud_api.compile import compile_to_jobspec  # noqa: E402
from flashml_cloud_api.flashml_yaml import parse_flashml_yaml  # noqa: E402
from flashml_cloud_api.images import resolve_image  # noqa: E402
from flashml_cloud_api.preflight import preflight  # noqa: E402
from flashml_cloud_api.repo import extract_safely  # noqa: E402
from flashnode.executor import CoordinatorClient, ExecutorLoop  # noqa: E402
from flashruntime.protocol.v1alpha1 import JobSpec  # noqa: E402
from flashruntime.recipes.command import CommandRecipe  # noqa: E402

#: What codeload.github.com actually serves: one wrapper directory named
#: ``<owner>-<repo>-<short sha>``, everything else beneath it. The wrapper is
#: the reason the contract is subtle — it must NOT appear in the argv path.
WRAPPER = "acme-widgets-abc123"

TRAIN_PY = b"""\
import argparse, json, pathlib

p = argparse.ArgumentParser()
p.add_argument("--lr", type=float, default=0.1)
args = p.parse_args()

out = pathlib.Path("/work/out")
out.mkdir(parents=True, exist_ok=True)
(out / "metrics.json").write_text(json.dumps({"loss": args.lr}))
"""

FLASHML_YAML = b"""\
version: 1
name: widgets-trainer
image: python-slim
entrypoint: train.py
args: ["--lr", "0.05"]
"""

CODE_KEY = "uploads/deadbeef/code.tar.gz"
CODE_URI = f"artifact://{CODE_KEY}"


def _github_tarball() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(name=f"{WRAPPER}/")
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
        for name, body in (("train.py", TRAIN_PY), ("flashml.yaml", FLASHML_YAML)):
            info = tarfile.TarInfo(name=f"{WRAPPER}/{name}")
            info.size = len(body)
            info.type = tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(body))
    return buf.getvalue()


# -- the node side, stubbed at the transport seam ----------------------------
#
# Only the network is faked. The staging, the extractor, the wrapper strip
# and the workdir layout are all flashnode's real code.


class _StubCoordinator:
    def __init__(self, artifacts: dict[str, bytes]):
        self.artifacts = dict(artifacts)
        self.leases: list[dict] = []
        self.completed: list[str] = []

    def lease(self, payload: dict) -> None:
        self.leases.append({
            "lease_id": "ls-1",
            "task_id": payload["task_id"],
            "job_id": "job-e2e",
            "node_id": "n1",
            "attempt_number": 1,
            "deadline": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
            "payload": payload,
        })

    def request(self, method, path, body=None):
        if path == "/v1alpha1/leases/claim":
            return (200, json.dumps(self.leases.pop(0)).encode()) if self.leases else (204, b"")
        if path.startswith("/v1alpha1/artifacts/"):
            key = path.removeprefix("/v1alpha1/artifacts/")
            if method == "PUT":
                self.artifacts[key] = body
                return 200, b"{}"
            if key in self.artifacts:
                return 200, self.artifacts[key]
            return 404, b'{"detail": "missing"}'
        if path.endswith("/complete"):
            self.completed.append(path)
            return 200, b'{"accepted": true}'
        if path.endswith("/heartbeat"):
            return 200, b"{}"
        raise AssertionError(f"unexpected request {method} {path}")


class _RecordingRunner:
    """Stands in for ``ArgvDockerRunner``.

    It cannot be the real one — that needs a Docker daemon — but it is
    handed exactly what the real one is handed, at exactly the same moment,
    and the mapping the real one applies is one documented fact:
    ``harden_args`` binds ``-v <workdir>:/work``, so a container path
    ``/work/X`` is ``workdir/X`` on the host.

    It records *facts*, not paths. The executor runs each task in a
    ``TemporaryDirectory`` that is gone by the time the loop returns, so a
    test that kept the path and looked afterwards would be asserting about
    a deleted tree — and would pass or fail for the wrong reason.
    """

    def __init__(self):
        self.payload: dict | None = None
        self.inputs: dict[str, dict] = {}
        self.argv_target: dict = {}

    def run(self, payload: dict, workdir: Path, inputs: dict[str, Path]) -> Path:
        self.payload = payload
        workdir = Path(workdir)

        self.inputs = {
            name: {
                "is_dir": path.is_dir(),
                "is_file": path.is_file(),
                # where it sits relative to the bind mount, i.e. the path
                # the container will see under /work
                "container_path": "/work/" + str(path.relative_to(workdir)),
                "listing": sorted(p.name for p in path.iterdir()) if path.is_dir() else [],
            }
            for name, path in inputs.items()
        }

        # The one question that matters: is the file the command is about to
        # open actually there?
        argv = payload.get("argv") or []
        container_path = argv[1] if len(argv) > 1 else ""
        host = (
            workdir / container_path.removeprefix("/work/")
            if container_path.startswith("/work/")
            else None
        )
        self.argv_target = {
            "container_path": container_path,
            "exists": bool(host is not None and host.is_file()),
            "bytes": host.read_bytes() if host is not None and host.is_file() else None,
        }

        outdir = workdir / "out"
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "metrics.json").write_text("{}")
        return outdir


@pytest.fixture()
def node(monkeypatch):
    """A flashnode executor whose only fake is its HTTP transport."""
    coordinator = _StubCoordinator({CODE_KEY: _github_tarball()})
    client = CoordinatorClient("http://stub")
    monkeypatch.setattr(
        client, "_request",
        lambda m, p, body=None, content_type="", headers=None: coordinator.request(m, p, body),
    )
    runner = _RecordingRunner()
    return coordinator, ExecutorLoop(client, "n1", runner=runner), runner


# -- the three steps, run for real -------------------------------------------


def _compile_like_the_api(tar_bytes: bytes, tmp_path: Path) -> dict:
    """The API's ``POST /jobs/from-repo`` path, minus HTTP and the network:
    extract, read flashml.yaml, parse, resolve the image, preflight, compile.
    Same functions, same order (``app.py``)."""
    repo_root = extract_safely(tar_bytes, tmp_path / "src", max_bytes=10_000_000)
    config = parse_flashml_yaml((repo_root / "flashml.yaml").read_text())
    image = resolve_image(config.image)

    findings = preflight(config, repo_root, image)
    assert [f for f in findings if f.level == "error"] == [], (
        "the fixture repo must pass preflight, or this test proves nothing "
        "about the path a real job takes"
    )
    return compile_to_jobspec(config, image, CODE_URI, config.name)


def test_the_argv_path_exists_on_the_node_that_will_run_it(node, tmp_path):
    """The whole point. One tarball, all three sides, one assertion.

    Drop ``unpack_inputs`` anywhere along the chain — from the compiler, from
    the recipe's payload, from the executor's dispatch — and the argv names a
    path that is not there, which is exactly how this shipped broken.
    """
    coordinator, loop, runner = node
    spec = _compile_like_the_api(_github_tarball(), tmp_path)

    # coordinator: JobSpec -> the payload a node claims
    task = CommandRecipe().expand("job-e2e", JobSpec.model_validate(spec))[0]
    coordinator.lease(task.payload)

    # node: claim -> stage inputs -> run
    assert loop.run(max_tasks=1, idle_exit=True) == 1

    target = runner.argv_target
    assert target["exists"], (
        f"argv names {target['container_path']}, which does not exist under "
        f"the node's workdir — the compiled command and the staged layout "
        f"disagree"
    )
    assert target["bytes"] == TRAIN_PY, "the staged file is not the repo's"


def test_the_three_declarations_name_the_same_input(node, tmp_path):
    """Attributes the agreement instead of assuming it.

    The test above would still pass if all three sides happened to hardcode
    ``code``. This one checks they are the *same* declaration: the compiler's
    ``unpack_inputs`` entry, the input the recipe forwards, and the directory
    segment of the argv path.
    """
    coordinator, loop, runner = node
    spec = _compile_like_the_api(_github_tarball(), tmp_path)
    params = spec["spec"]["workload"]["parameters"]

    assert len(params["unpack_inputs"]) == 1
    name = params["unpack_inputs"][0]
    assert name in params["inputs"], "unpacking an input that was never declared"
    assert params["command"][1].startswith(f"/work/inputs/{name}/")

    task = CommandRecipe().expand("job-e2e", JobSpec.model_validate(spec))[0]
    assert task.payload["unpack_inputs"] == [name]

    coordinator.lease(task.payload)
    assert loop.run(max_tasks=1, idle_exit=True) == 1

    # ...and the node handed the runner that input as a DIRECTORY at the
    # place the argv points into, not as the downloaded tarball.
    staged = runner.inputs[name]
    assert staged["is_dir"]
    assert staged["container_path"] == f"/work/inputs/{name}"


def test_githubs_wrapper_directory_is_not_in_the_path(node, tmp_path):
    """The subtle half.

    GitHub wraps every repo in ``<owner>-<repo>-<sha>/``. The compiler cannot
    know that string — it is a commit SHA it never sees — so the extractor
    has to strip it. If it stopped, ``/work/inputs/code/train.py`` would be
    one directory too high and the file above would sit at
    ``code/acme-widgets-abc123/train.py``.
    """
    coordinator, loop, runner = node
    spec = _compile_like_the_api(_github_tarball(), tmp_path)
    task = CommandRecipe().expand("job-e2e", JobSpec.model_validate(spec))[0]

    assert WRAPPER not in task.payload["argv"][1]

    coordinator.lease(task.payload)
    assert loop.run(max_tasks=1, idle_exit=True) == 1

    assert runner.inputs["code"]["listing"] == ["flashml.yaml", "train.py"]
    assert runner.argv_target["exists"]


def test_a_sweep_keeps_the_same_entrypoint_path_in_every_task(node, tmp_path):
    """Fan-out runs each task's argv through ``str.format``. Every one of
    them must still name the staged file — a sweep is where a stray brace or
    a re-escaped token would quietly corrupt the path."""
    tar_bytes = _github_tarball()
    repo_root = extract_safely(tar_bytes, tmp_path / "src", max_bytes=10_000_000)
    config = parse_flashml_yaml(
        (repo_root / "flashml.yaml").read_text() + "sweep:\n  lr: [0.1, 0.2]\n"
    )
    spec = compile_to_jobspec(config, resolve_image(config.image), CODE_URI, config.name)

    tasks = CommandRecipe().expand("job-e2e", JobSpec.model_validate(spec))
    assert len(tasks) == 2
    for task in tasks:
        assert task.payload["argv"][1] == "/work/inputs/code/train.py"
        assert task.payload["unpack_inputs"] == ["code"]

    coordinator, loop, runner = node
    coordinator.lease(tasks[1].payload)
    assert loop.run(max_tasks=1, idle_exit=True) == 1
    assert runner.argv_target["exists"]
    assert runner.argv_target["bytes"] == TRAIN_PY


def test_without_unpack_inputs_the_argv_path_is_missing(node, tmp_path):
    """The negative control, and the reason this file exists.

    Strip ``unpack_inputs`` from the payload and everything else stays valid:
    the spec compiles, the job expands, the node claims and runs the task.
    The only casualty is the file the command was going to open — which is
    why three green suites hid this for as long as they did.
    """
    coordinator, loop, runner = node
    spec = _compile_like_the_api(_github_tarball(), tmp_path)
    task = CommandRecipe().expand("job-e2e", JobSpec.model_validate(spec))[0]

    payload = dict(task.payload)
    assert "unpack_inputs" in payload, "nothing to strip — the forwarding is already gone"
    payload.pop("unpack_inputs")
    coordinator.lease(payload)
    assert loop.run(max_tasks=1, idle_exit=True) == 1

    assert not runner.argv_target["exists"], (
        "with unpack_inputs gone the argv path must NOT exist — if it does, "
        "something else is unpacking the input and this whole file is "
        "asserting nothing"
    )
    code = runner.inputs["code"]
    assert code["is_file"], "the tarball, not the repo"
    assert code["container_path"] == "/work/inputs/code.tar.gz"
