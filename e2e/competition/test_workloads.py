"""The competition demo's workloads, held to the four claims the demo makes.

    e2e/.venv/bin/pytest e2e/competition/test_workloads.py -v

Everything here drives the real scripts as **subprocesses**, never by importing
`main()` and calling it. Two reasons, and both are the point of the suite:

* The demo's central claim is about bytes on disk surviving a process that
  stops existing. `SIGKILL` is not expressible against a function call, and a
  simulated interruption that unwinds a Python stack tests the opposite of the
  failure being claimed.
* The scripts are what a volunteer's machine runs — argv in, files out. A test
  that reaches inside them tests something nobody deploys.

The four claims, in the order they are asserted below:

1. **Determinism.** Same seed, same bytes. Two runs, one sha256.
2. **Resume equivalence.** A run killed with `-9` and restarted from its last
   committed checkpoint produces a **byte-identical** `model.json` to a run
   nobody touched. This is the one the entire demo rests on: it is what makes
   the recovery provably free rather than plausibly cheap. If it ever fails,
   it must fail loudly here rather than be softened to "close enough" — an
   accuracy-tolerance assertion would pass while the recovery quietly changed
   the model, which is the exact claim being made.
3. **Checkpoint atomicity.** A damaged checkpoint is refused, not resumed
   from. Resuming from garbage would train to completion and commit a model
   whose provenance is fiction.
4. **The evaluator.** Right numbers for a known model, the model's own hash
   reported back, and a corrupt or substituted model refused rather than
   scored.

Plus one cross-repo contract test (`test_checkpoint_names_match_the_relay_
contract`), because flashnode parses these filenames in the other repository
and nothing else in either suite pins the two together.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from workload_common import (
    CHECKPOINT_SCHEMA,
    WorkloadError,
    canonical_bytes,
    evaluate_params,
    load_checkpoint,
    seal,
    sha256_hex,
    synth_dataset,
)

HERE = Path(__file__).parent
TRAIN = HERE / "train_checkpointed.py"
EVALUATE = HERE / "evaluate_model.py"

#: Small enough that the suite stays quick, large enough that the interrupted
#: run below is genuinely killed mid-flight rather than racing its own exit.
#: `test_the_interrupted_run_was_really_interrupted` is what keeps that honest
#: — if this ever shrinks to where the process finishes before the kill lands,
#: the suite says so instead of passing a test that proved nothing.
SMALL = ("--epochs", "12", "--samples", "900", "--features", "16",
         "--hidden", "20", "--batch-size", "30")

#: The interruption test's own sizing: several seconds of work so that
#: "kill it after 4 checkpoints" is a comfortable target rather than a race.
KILLABLE = ("--epochs", "24", "--samples", "1800", "--features", "24",
            "--hidden", "32", "--batch-size", "24")

#: How many checkpoints must land before the kill. Low enough to reach
#: quickly, high enough that a meaningful amount of work is at stake.
KILL_AFTER_CHECKPOINTS = 4

RUN_TIMEOUT_S = 300.0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _train_argv(out: Path, resume: Path, sizing: tuple[str, ...]) -> list[str]:
    return [sys.executable, str(TRAIN), "--out", str(out),
            "--resume", str(resume), *sizing]


def run_training(
    out: Path, resume: Path | None = None, sizing: tuple[str, ...] = SMALL
) -> dict:
    """Train to completion; return the parsed `metrics.json`.

    `resume` defaults to a path that does not exist, which is the ordinary
    first-attempt case and explicitly not an error.
    """
    resume = resume or (out.parent / "inputs" / "resume.json")
    proc = subprocess.run(
        _train_argv(out, resume, sizing),
        capture_output=True, text=True, timeout=RUN_TIMEOUT_S,
    )
    assert proc.returncode == 0, f"training failed:\n{proc.stdout}\n{proc.stderr}"
    return json.loads((out / "metrics.json").read_text())


def checkpoints(out: Path) -> list[Path]:
    """Every committed checkpoint, in step order.

    Sorted by the integer step rather than by filename, deliberately: the
    filenames are not zero-padded, so `sorted()` on the name puts step-100
    before step-24. That is harmless upstream — the coordinator's
    `CheckpointCatalog.latest_valid` selects with `max(m.step, ...)` — and it
    would be quietly wrong here, where "the last checkpoint" has to mean the
    furthest one.
    """
    return sorted(
        (out / "ckpt").glob("step-*.json"),
        key=lambda p: int(p.stem.split("-")[1]),
    )


def kill_group(proc: subprocess.Popen) -> None:
    """`SIGKILL` the process GROUP, the way a machine vanishing does it.

    Not `terminate()`, not `SIGINT`, and the distinction is the whole demo.
    A signal the process can observe lets it finish the epoch, write its
    checkpoint and exit tidily — the easy case, where somebody announced the
    death. The hard case is the one worth proving: nothing is announced, the
    last write is whatever the filesystem had already committed, and recovery
    has to come from the checkpoint alone.

    The group, not the pid, because a trainer that had spawned anything would
    otherwise leave it behind holding the output directory.
    """
    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    proc.wait(timeout=30)


def start_killable_training(
    out: Path, resume: Path, sizing: tuple[str, ...] = KILLABLE
) -> subprocess.Popen:
    """Launch training in its own process group so `killpg` has a target."""
    return subprocess.Popen(
        _train_argv(out, resume, sizing),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )


def wait_for_checkpoints(out: Path, count: int, proc: subprocess.Popen,
                         timeout_s: float = 120.0) -> list[Path]:
    """Block until `count` checkpoints exist. Fails if the run ends first.

    A run that completed before the kill landed would make every assertion
    downstream pass while proving nothing, so exiting early is a test failure
    here rather than a shrug.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        found = checkpoints(out)
        if len(found) >= count:
            return found
        if proc.poll() is not None:
            pytest.fail(
                f"training exited (rc={proc.returncode}) with only "
                f"{len(found)} checkpoint(s) — it finished before the kill "
                f"could land, so nothing about recovery was exercised. Raise "
                f"KILLABLE's sizing."
            )
        if time.monotonic() > deadline:
            pytest.fail(f"only {len(found)} checkpoint(s) after {timeout_s}s")
        time.sleep(0.02)


class _Serve(BaseHTTPRequestHandler):
    """Serves a dict of path -> bytes over loopback HTTP."""

    objects: dict[str, bytes] = {}

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
        body = self.objects.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep pytest output readable
        return


@pytest.fixture()
def model_server():
    """A loopback HTTP origin for the evaluator's fetch.

    Deliberately real HTTP rather than a `file://` shortcut: `check_url`
    admits loopback precisely so the fetch path — Content-Length ceiling,
    HTTPError handling, byte-for-byte hashing of what came back — is the code
    that runs in this suite and not code first exercised on the night of the
    demo.
    """
    handler = type("_H", (_Serve,), {"objects": {}})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]

    class Origin:
        base = f"http://{host}:{port}"

        def put(self, path: str, body: bytes) -> str:
            handler.objects[path] = body
            return f"{self.base}{path}"

    try:
        yield Origin()
    finally:
        server.shutdown()
        server.server_close()


def write_artifacts(path: Path, urls: dict[str, str], *,
                    session_id: str = "sess-1", job_id: str = "job-1") -> None:
    """The orchestrator's `artifacts.json`, in its own shape.

    Built here from the same fields `sandbox_orchestrator.artifacts_document`
    writes (schema, session_id, job_id, issued_at, ttl_s, objects) rather than
    imported from it: this suite runs in the e2e venv with no API package
    installed, and the evaluator's contract is with the *document*, not with
    the module that happens to produce it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps({
        "schema": "flashml.sandbox-artifacts/v1",
        "session_id": session_id,
        "job_id": job_id,
        "issued_at": "2026-08-11T00:00:00Z",
        "ttl_s": 900,
        "objects": dict(sorted(urls.items())),
    }, sort_keys=True, separators=(",", ":")).encode())


def run_evaluation(out: Path, artifacts: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(EVALUATE), "--artifacts", str(artifacts),
         "--out", str(out), *extra],
        capture_output=True, text=True, timeout=RUN_TIMEOUT_S,
    )


# ---------------------------------------------------------------------------
# 1. determinism
# ---------------------------------------------------------------------------


def test_two_runs_at_one_seed_produce_one_model(tmp_path: Path):
    """Same seed, same bytes — the floor everything else stands on.

    Compared on the sha256 of `model.json` AND on the raw bytes. The hash is
    the thing the platform carries around; the bytes are what makes a failure
    diagnosable, because a hash mismatch alone cannot tell you whether one
    float moved or the whole file did.
    """
    a, b = tmp_path / "a" / "out", tmp_path / "b" / "out"
    first, second = run_training(a), run_training(b)

    assert first["model_sha256"] == second["model_sha256"]
    assert (a / "model.json").read_bytes() == (b / "model.json").read_bytes()
    # The trainer hashes what it wrote; so does this. A `model_sha256` that
    # did not match the file on disk would make every downstream check a
    # comparison of two claims rather than of two artifacts.
    assert first["model_sha256"] == sha256_hex((a / "model.json").read_bytes())
    assert first["accuracy"] == second["accuracy"]
    assert first["resumed"] is False and first["resumed_from_step"] is None


def test_a_different_seed_produces_a_different_model(tmp_path: Path):
    """The control for the test above.

    Without it, a trainer that ignored its inputs entirely and wrote a
    constant file would pass the determinism assertion perfectly.
    """
    a = run_training(tmp_path / "a" / "out")
    b = run_training(tmp_path / "b" / "out", sizing=(*SMALL, "--seed", "999"))
    assert a["model_sha256"] != b["model_sha256"]


def test_checkpoint_names_match_the_relay_contract(tmp_path: Path):
    """A cross-repo contract, pinned from the side that produces it.

    flashnode's `_CheckpointRelay` (public repo, `executor/loop.py`) selects
    files with `ckpt_dir.glob("step-*.json")` and derives the step with
    `int(path.stem.split("-")[1])`. Neither repo may import the other, so this
    filename shape is an unwritten agreement between them — and it is the one
    that decides whether a dying attempt's work is shipped at all. A rename
    here would leave the relay finding nothing, silently, with training that
    looked perfectly healthy.
    """
    out = tmp_path / "out"
    metrics = run_training(out)
    names = [p.name for p in checkpoints(out)]
    assert names, "training committed no checkpoints"
    assert len(names) == metrics["epochs"], "one checkpoint per epoch"
    assert names == [Path(k).name for k in metrics["checkpoints"]]
    for name in names:
        assert Path(name).match("step-*.json")
        step = int(Path(name).stem.split("-")[1])  # exactly the relay's parse
        assert step > 0
    steps = [int(Path(n).stem.split("-")[1]) for n in names]
    assert steps == sorted(steps), "steps must increase"
    assert steps[-1] == metrics["steps"]


# ---------------------------------------------------------------------------
# 2. resume equivalence — the property the demo rests on
# ---------------------------------------------------------------------------


@pytest.fixture()
def interrupted_run(tmp_path: Path):
    """A real SIGKILL mid-training, and the state it left behind.

    Returns `(out, resume_path, killed_at_step, checkpoints_before_kill)`.
    The staged `resume.json` is a *copy* of the last committed checkpoint,
    placed where a resumed attempt's argv points — which is precisely what
    flashnode does on the recovery path: it downloads the coordinator's latest
    valid manifest part to `<workdir>/inputs/resume.json` and runs the same
    argv again.
    """
    out = tmp_path / "run" / "out"
    resume = tmp_path / "run" / "inputs" / "resume.json"
    proc = start_killable_training(out, resume)
    try:
        before = wait_for_checkpoints(out, KILL_AFTER_CHECKPOINTS, proc)
        assert proc.poll() is None, "process died on its own — nothing was killed"
        kill_group(proc)
    finally:
        if proc.poll() is None:  # a failed assertion must not leak the child
            kill_group(proc)

    assert proc.returncode != 0, "a SIGKILLed process cannot exit cleanly"
    assert not (out / "model.json").exists(), (
        "the killed attempt wrote a final model — it was not interrupted "
        "mid-training and this fixture proves nothing"
    )

    survived = checkpoints(out)
    assert len(survived) >= len(before)
    last = survived[-1]
    resume.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(last, resume)
    return out, resume, int(last.stem.split("-")[1]), survived


def test_the_kill_left_only_intact_checkpoints(interrupted_run):
    """Every file that survived the kill loads, or the atomic write is a lie.

    `SIGKILL` lands wherever it lands, including in the middle of a write.
    `atomic_write_json` claims a reader never sees a partial file — temp file,
    fsync, rename, fsync the directory. This is that claim measured against a
    real kill rather than asserted in a docstring.
    """
    _out, _resume, _step, survived = interrupted_run
    assert survived, "the kill landed before any checkpoint was committed"
    for path in survived:
        document = load_checkpoint(path)  # raises for truncated/damaged/wrong
        assert document["schema"] == CHECKPOINT_SCHEMA


def test_resumed_run_reproduces_the_uninterrupted_model_byte_for_byte(
    interrupted_run, tmp_path: Path
):
    """**The property the entire demo rests on.**

    A run that was killed with `-9` partway, then restarted from its last
    committed checkpoint, produces a `model.json` byte-identical to a run
    nobody touched. Not "similar accuracy", not "within tolerance" — the same
    bytes, and therefore the same sha256 the platform carries around as the
    model's identity.

    That is what makes the recovery *provably free*. A resumed run that landed
    somewhere merely nearby would still be a working demo and a much weaker
    claim: nobody could then say whether the machine's death cost the model
    anything, only that the number still looked fine.

    It holds because batch order for epoch *e* is a pure function of
    `(seed, e)` rather than of a serialised PRNG state, and because weights and
    momentum round-trip losslessly through `repr`-formatted JSON. If this ever
    goes red, one of those two is what broke — do not weaken the assertion.
    """
    out, resume, killed_at, _survived = interrupted_run

    resumed = run_training(out, resume=resume, sizing=KILLABLE)

    clean_out = tmp_path / "clean" / "out"
    clean = run_training(clean_out, sizing=KILLABLE)

    resumed_bytes = (out / "model.json").read_bytes()
    clean_bytes = (clean_out / "model.json").read_bytes()
    assert resumed_bytes == clean_bytes, (
        "resume is NOT byte-equivalent — the recovery changed the model. This "
        "is the demo's central claim; fix the trainer, never this assertion."
    )
    assert resumed["model_sha256"] == clean["model_sha256"]

    # And the recovery actually recovered something, rather than quietly
    # restarting from scratch and arriving at the same place.
    assert resumed["resumed"] is True
    assert resumed["resumed_from_step"] == killed_at
    assert resumed["epochs_executed"] < resumed["epochs"], (
        "the resumed attempt ran every epoch — it restarted rather than resumed"
    )
    # The ceiling the demo claims: never more than one checkpoint interval of
    # repeated work. Zero by construction, because checkpoints land on epoch
    # boundaries and a resume restarts at one.
    assert resumed["recomputed_steps"] == 0
    # Same holdout number, necessarily — same bytes.
    assert resumed["accuracy"] == clean["accuracy"]


def test_resuming_from_the_final_checkpoint_still_agrees(tmp_path: Path):
    """The degenerate end of the same property: resume at the last epoch.

    Worth its own test because it is the boundary the loop arithmetic is most
    likely to get wrong — `range(start_epoch, epochs)` with `start_epoch ==
    epochs` must execute nothing and still write the same model, rather than
    running one extra epoch or none of the final bookkeeping.
    """
    clean_out = tmp_path / "clean" / "out"
    clean = run_training(clean_out)

    resume = tmp_path / "again" / "inputs" / "resume.json"
    resume.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(checkpoints(clean_out)[-1], resume)

    again_out = tmp_path / "again" / "out"
    again = run_training(again_out, resume=resume)

    assert (again_out / "model.json").read_bytes() == (clean_out / "model.json").read_bytes()
    assert again["epochs_executed"] == 0
    assert again["resumed_from_step"] == clean["steps"]


# ---------------------------------------------------------------------------
# 3. checkpoint atomicity — a damaged checkpoint is refused, never resumed from
# ---------------------------------------------------------------------------


@pytest.fixture()
def one_checkpoint(tmp_path: Path) -> Path:
    out = tmp_path / "out"
    run_training(out)
    return checkpoints(out)[-1]


@pytest.mark.parametrize("keep_fraction", [0.0, 0.25, 0.5, 0.75, 0.999])
def test_a_truncated_checkpoint_is_refused(one_checkpoint: Path, tmp_path: Path,
                                           keep_fraction: float):
    """Truncation at any point is refused. Never "parsed as far as it goes".

    This is the shape a file takes when a write loses a race with SIGKILL, so
    it is swept across the whole file rather than tested at one convenient
    offset. The last case keeps 99.9% of the bytes — the one most likely to
    still look like JSON to a lenient reader.
    """
    raw = one_checkpoint.read_bytes()
    damaged = tmp_path / "truncated.json"
    damaged.write_bytes(raw[: int(len(raw) * keep_fraction)])

    with pytest.raises(WorkloadError):
        load_checkpoint(damaged)


def test_a_tampered_checkpoint_is_refused(one_checkpoint: Path, tmp_path: Path):
    """A file that survives truncation *and* stays parseable is still refused.

    The interesting adversary is not the truncated file — that one fails to
    parse and is easy. It is the file that parses perfectly and holds someone
    else's weights. `content_sha256` is what separates the two, and this moves
    exactly one float to prove the seal is computed rather than copied.
    """
    document = json.loads(one_checkpoint.read_text())
    document["model"]["b1"][0] += 1.0  # one number, everything else intact
    damaged = tmp_path / "tampered.json"
    damaged.write_bytes(canonical_bytes(document))

    with pytest.raises(WorkloadError, match="content_sha256 mismatch"):
        load_checkpoint(damaged)


def test_a_checkpoint_with_no_seal_is_refused(one_checkpoint: Path, tmp_path: Path):
    """No `content_sha256` at all: not a checkpoint this trainer wrote."""
    document = json.loads(one_checkpoint.read_text())
    document.pop("content_sha256")
    damaged = tmp_path / "unsealed.json"
    damaged.write_bytes(canonical_bytes(document))

    with pytest.raises(WorkloadError):
        load_checkpoint(damaged)


def test_a_checkpoint_from_another_schema_is_refused(one_checkpoint: Path,
                                                     tmp_path: Path):
    document = json.loads(one_checkpoint.read_text())
    document["schema"] = "flashml.competition.checkpoint/v99"
    damaged = tmp_path / "future.json"
    damaged.write_bytes(canonical_bytes(seal(document)))

    with pytest.raises(WorkloadError):
        load_checkpoint(damaged)


def test_the_trainer_fails_rather_than_silently_restarting(tmp_path: Path):
    """The behavioural half of atomicity, and the one that matters on the night.

    A broken `resume.json` means the recovery path is live and the bytes it
    delivered cannot be trusted. Restarting quietly from scratch would look
    exactly like a successful recovery — same final model, eventually — while
    throwing away every minute the dead attempt spent. So the process must
    EXIT NON-ZERO, which fails the task, which the coordinator already knows
    how to retry.
    """
    seed_out = tmp_path / "seed" / "out"
    run_training(seed_out)
    good = checkpoints(seed_out)[-1]

    out = tmp_path / "run" / "out"
    resume = tmp_path / "run" / "inputs" / "resume.json"
    resume.parent.mkdir(parents=True, exist_ok=True)
    raw = good.read_bytes()
    resume.write_bytes(raw[: len(raw) // 2])

    proc = subprocess.run(
        _train_argv(out, resume, SMALL),
        capture_output=True, text=True, timeout=RUN_TIMEOUT_S,
    )
    assert proc.returncode != 0, (
        "the trainer resumed from a truncated checkpoint — a silent restart "
        "here is indistinguishable from a successful recovery"
    )
    assert not (out / "model.json").exists()
    assert "truncated" in proc.stderr.lower() or "not parseable" in proc.stderr.lower()


def test_a_checkpoint_from_a_different_run_is_refused(tmp_path: Path):
    """One run's weights must not continue under another run's schedule.

    A checkpoint whose `run` block disagrees with this attempt's argv is not a
    recoverable state, and continuing from it would produce a model that no
    reported number describes.
    """
    other_out = tmp_path / "other" / "out"
    run_training(other_out, sizing=(*SMALL, "--seed", "424242"))

    out = tmp_path / "run" / "out"
    resume = tmp_path / "run" / "inputs" / "resume.json"
    resume.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(checkpoints(other_out)[-1], resume)

    proc = subprocess.run(
        _train_argv(out, resume, SMALL),
        capture_output=True, text=True, timeout=RUN_TIMEOUT_S,
    )
    assert proc.returncode != 0
    assert "different" in proc.stderr.lower()


# ---------------------------------------------------------------------------
# 4. the evaluator
# ---------------------------------------------------------------------------


@pytest.fixture()
def trained(tmp_path_factory) -> tuple[Path, dict, bytes]:
    """One trained model, shared by the evaluator tests. `(path, metrics, bytes)`."""
    out = tmp_path_factory.mktemp("trained") / "out"
    metrics = run_training(out)
    model = out / "model.json"
    return model, metrics, model.read_bytes()


def test_the_evaluator_reports_the_trainers_numbers(trained, model_server,
                                                    tmp_path: Path):
    """The evaluation is of the model that was trained, and says so twice over.

    Two independent agreements, and each one would be worth having alone:

    * **The hash.** The trainer hashed the bytes it wrote; the evaluator
      hashes the bytes it downloaded. Equal iff this is that model.
    * **The number.** Both compute holdout accuracy from the recipe embedded
      in the model — the trainer from its in-memory weights, the evaluator
      from a file it fetched over HTTP with no other shared state. Equality is
      evidence the round trip through JSON lost nothing.
    """
    model_path, train_metrics, raw = trained
    url = model_server.put("/jobs/j1/task-000/model.json", raw)
    artifacts = tmp_path / "artifacts.json"
    write_artifacts(artifacts, {"jobs/j1/task-000/model.json": url})

    out = tmp_path / "out"
    proc = run_evaluation(out, artifacts)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    metrics = json.loads((out / "metrics.json").read_text())
    assert metrics["model_sha256"] == sha256_hex(raw) == train_metrics["model_sha256"]
    assert metrics["accuracy"] == train_metrics["accuracy"]
    assert metrics["model_bytes"] == len(raw)
    assert metrics["model_key"] == "jobs/j1/task-000/model.json"
    # Provenance carried through from the orchestrator's document, without the
    # workload knowing anything about a session or a job.
    assert metrics["training_job_id"] == "job-1"
    assert metrics["session_id"] == "sess-1"


def test_the_evaluator_agrees_with_a_hand_computed_score(trained, model_server,
                                                         tmp_path: Path):
    """A third opinion, computed here from the shared primitives.

    The test above compares the evaluator against the trainer, and both call
    `evaluate_params` — so a bug inside that function would agree with itself.
    This recomputes the holdout split and the score in the test process and
    checks the balance of the split too, which is what makes accuracy mean
    what it says.
    """
    _model_path, _train_metrics, raw = trained
    model = json.loads(raw)
    xs, ys = synth_dataset(model["data_recipe"], "holdout")
    loss, accuracy = evaluate_params(model["params"], xs, ys)

    url = model_server.put("/model.json", raw)
    artifacts = tmp_path / "artifacts.json"
    write_artifacts(artifacts, {"model.json": url})
    out = tmp_path / "out"
    assert run_evaluation(out, artifacts).returncode == 0

    metrics = json.loads((out / "metrics.json").read_text())
    assert metrics["accuracy"] == accuracy
    assert metrics["loss"] == loss
    assert metrics["samples"] == len(xs) == model["data_recipe"]["holdout_samples"]
    assert set(metrics["samples_per_class"]) == {"0", "1", "2"}
    assert len(set(metrics["samples_per_class"].values())) == 1, "balanced by construction"
    # The task is not linearly separable and the model is small: a score that
    # pinned at 1.0 would mean the holdout leaked into training.
    assert 0.5 < accuracy < 1.0


def test_a_corrupt_model_is_refused_rather_than_scored(model_server, tmp_path: Path):
    """Truncated bytes on the wire fail the task, and produce no number.

    A wrong number that looks right is worse than a failed task, and a failed
    task is a thing the coordinator already knows how to retry.
    """
    url = model_server.put("/model.json", b'{"schema": "flashml.competi')
    artifacts = tmp_path / "artifacts.json"
    write_artifacts(artifacts, {"model.json": url})

    out = tmp_path / "out"
    proc = run_evaluation(out, artifacts)
    assert proc.returncode != 0
    assert not (out / "metrics.json").exists(), "a refused model produced metrics"


def test_a_model_of_the_wrong_schema_is_refused(trained, model_server, tmp_path: Path):
    _model_path, _metrics, raw = trained
    document = json.loads(raw)
    document["schema"] = "someone-elses-model/v1"
    url = model_server.put("/model.json", canonical_bytes(document))
    artifacts = tmp_path / "artifacts.json"
    write_artifacts(artifacts, {"model.json": url})

    out = tmp_path / "out"
    proc = run_evaluation(out, artifacts)
    assert proc.returncode != 0
    assert not (out / "metrics.json").exists()


def test_a_model_that_contradicts_its_own_architecture_is_refused(
    trained, model_server, tmp_path: Path
):
    """Parses, is the right schema, and lies about its own shape.

    The dangerous middle case: nothing here fails to load, and without the
    cross-check the evaluator would score a model against a held-out set built
    for a different one.
    """
    _model_path, _metrics, raw = trained
    document = json.loads(raw)
    document["architecture"]["shapes"]["W1"] = [999, 999]
    url = model_server.put("/model.json", canonical_bytes(document))
    artifacts = tmp_path / "artifacts.json"
    write_artifacts(artifacts, {"model.json": url})

    out = tmp_path / "out"
    proc = run_evaluation(out, artifacts)
    assert proc.returncode != 0
    assert not (out / "metrics.json").exists()


def test_a_substituted_model_is_refused_on_identity_alone(trained, model_server,
                                                          tmp_path: Path):
    """`--expect-sha256` turns the hash from evidence into an assertion.

    The substituted model here is perfectly valid and would score perfectly
    well. It is simply not the model this evaluation is about, and that is the
    whole objection — which is why the refusal happens before the bytes are
    ever parsed.
    """
    _model_path, _metrics, raw = trained
    other_out = tmp_path / "other" / "out"
    run_training(other_out, sizing=(*SMALL, "--seed", "777"))
    other = (other_out / "model.json").read_bytes()
    assert other != raw

    url = model_server.put("/model.json", other)
    artifacts = tmp_path / "artifacts.json"
    write_artifacts(artifacts, {"model.json": url})

    out = tmp_path / "out"
    proc = run_evaluation(out, artifacts, "--expect-sha256", sha256_hex(raw))
    assert proc.returncode != 0
    assert not (out / "metrics.json").exists()
    assert "does not match" in proc.stderr

    # And the same bytes with the hash they actually have are accepted, so the
    # test above is about identity rather than about the flag rejecting
    # everything.
    ok = run_evaluation(tmp_path / "ok", artifacts,
                        "--expect-sha256", sha256_hex(other))
    assert ok.returncode == 0


def test_an_ambiguous_model_key_is_refused(trained, model_server, tmp_path: Path):
    """Two tasks that both produced a `model.json` is a real possibility.

    Scoring whichever one sorted first would attribute one task's accuracy to
    another task's model — silently, and with a number that looks entirely
    plausible.
    """
    _model_path, _metrics, raw = trained
    artifacts = tmp_path / "artifacts.json"
    write_artifacts(artifacts, {
        "jobs/j1/task-000/model.json": model_server.put("/a/model.json", raw),
        "jobs/j1/task-001/model.json": model_server.put("/b/model.json", raw),
    })

    out = tmp_path / "out"
    proc = run_evaluation(out, artifacts)
    assert proc.returncode != 0
    assert not (out / "metrics.json").exists()


def test_a_non_loopback_plain_http_url_is_refused(tmp_path: Path):
    """The presigned signature is the only integrity the fetch has.

    Loopback is admitted so this suite exercises the real HTTP path; a remote
    plain-HTTP origin is refused outright rather than warned about.
    """
    artifacts = tmp_path / "artifacts.json"
    write_artifacts(artifacts, {"model.json": "http://example.invalid/model.json"})

    out = tmp_path / "out"
    proc = run_evaluation(out, artifacts)
    assert proc.returncode != 0
    assert "refusing to fetch" in proc.stderr


def test_an_artifacts_document_of_the_wrong_schema_is_refused(tmp_path: Path):
    artifacts = tmp_path / "artifacts.json"
    artifacts.write_bytes(json.dumps({
        "schema": "something.else/v1", "objects": {"model.json": "https://x/y"},
    }).encode())

    out = tmp_path / "out"
    proc = run_evaluation(out, artifacts)
    assert proc.returncode != 0
    assert not (out / "metrics.json").exists()


# ---------------------------------------------------------------------------
# the demo's own sizing, kept honest
# ---------------------------------------------------------------------------


def test_the_demo_sizing_is_what_the_configs_declare():
    """`flashml.train.yaml`'s argv is the sizing this demo was measured at.

    Not a style check. The recovery beat is timed against a run of a known
    length — kill around checkpoint 25 of 60 and still finish inside three
    minutes — and a config edited to a different size would leave every timing
    claim in the runbook describing a run nobody performs.
    """
    text = (HERE / "flashml.train.yaml").read_text()
    for token in ("--epochs", "60", "--samples", "8000", "--hidden", "64"):
        assert f'"{token}"' in text or f"- {token}" in text or f'"{token}"' in text, token
    assert "train_checkpointed.py" in text
    assert "python-slim" in text


def test_the_configs_carry_no_key_the_parser_would_refuse():
    """A cheap standin for the real parser, which is not installed here.

    `parse_flashml_yaml` lives in the API package and this suite runs in the
    e2e venv, so the authoritative check is run separately (and was, against
    both files). What this catches is the drift that would otherwise go
    unnoticed between those runs: a key added by hand that no version of the
    schema has ever had.
    """
    allowed = {
        "version", "name", "image", "entrypoint", "args", "sweep", "resources",
        "timeout_seconds", "mode", "epochs", "sync_every", "local_inputs",
        "partition", "validators", "reduce", "allow_partial", "dependencies",
        "datasets",
    }
    for name in ("flashml.train.yaml", "flashml.evaluate.yaml"):
        keys = {
            line.split(":", 1)[0]
            for line in (HERE / name).read_text().splitlines()
            if line and not line.startswith((" ", "-", "#")) and ":" in line
        }
        assert keys, f"{name}: parsed no top-level keys"
        assert keys <= allowed, f"{name}: unknown key(s) {sorted(keys - allowed)}"
        assert {"version", "name", "image", "entrypoint"} <= keys, name


def test_hashlib_agrees_with_the_shared_helper():
    """`sha256_hex` is the identity of every artifact here. One line, pinned."""
    assert sha256_hex(b"flashml") == hashlib.sha256(b"flashml").hexdigest()
