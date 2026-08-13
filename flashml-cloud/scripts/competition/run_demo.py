#!/usr/bin/env python3
"""The whole competition loop, unattended, against a running API.

    scripts/competition/run_demo.py --api-base http://127.0.0.1:8000 \
        --repo Zolli-Labs/flashml-competition --ref train

    scripts/competition/run_demo.py --api-base https://api.example \
        --training-job-id 543b2c82b152          # resume a half-finished demo

Submit training → open a sandbox session (which bootstraps a worker and
hibernates it) → wait for the model artifact → wake the sandbox with
`model-ready` → poll the session ledger until the evaluation is accepted →
clean up → print a timeline with measured latencies and the share URL.

Requirement D-8 is what this file answers: "one-click reproduction; explicit
execute → hibernate → wake → continue".

**Restartable, and that is not a nicety.** Every step first asks whether it has
already happened. A demo that half-succeeded and left a sandbox running is the
expensive failure — the sandbox bills by the second — so re-running this script
adopts what exists rather than building a second one beside it. Three separate
mechanisms, none of which is this script's invention:

* the training job is found by name, or supplied with `--training-job-id`;
* the session is found through `GET /v1alpha1/jobs/{id}/sandbox-sessions`,
  which answers `[]` rather than 404 for a job that has none;
* `model-ready` is idempotent at the orchestrator — the first thing it does is
  compare-and-set HIBERNATED → RESUMING, and a loser returns having woken
  nothing. So calling it twice is safe by construction, not by luck.

**Cleanup runs in `finally`, on every path including Ctrl-C.** `cleanup_session`
is documented never to raise for exactly this reason. `--keep-sandbox` exists
for a human debugging a failure and prints what it is costing them.

WHAT THIS SCRIPT HAS TO WORK AROUND, both verified against the API rather than
assumed, and both reported rather than hidden:

1. **An evaluation spec cannot stage its own code.**
   `app.build_evaluation_jobspec` builds `parameters["inputs"]` itself — a
   single `model` entry — and forwards only `dependencies`,
   `extra_dependencies`, `validators`, `reduce` and `retention` from the
   submitter's spec. There is no way to declare an `inputs` or an
   `unpack_inputs`, so the repo-staging path every ordinary job uses
   (`artifact://` tarball → `/work/inputs/code/`) does not exist here.
   So the workload is carried INLINE: the two modules are tarred, gzipped and
   base64'd into the argv, and a fifteen-line bootstrap unpacks and runs them.
   It is not elegant and it is honest — the alternative is a command that
   references a file nobody put in the sandbox.

2. **The one input the spec does declare is a prefix, not an object.**
   `inputs = {"model": f"artifact://{job_prefix(training_job_id)}"}` and
   `job_prefix` returns `jobs/<id>/` — a trailing slash. flashnode downloads a
   non-unpacked input with `download_artifact(key, workdir/"inputs"/
   Path(key).name)`, and `Path("jobs/x/").name` is `"x"`, so it would GET a
   directory key. Immaterial here, because this workload never reads that
   input — it fetches the model over the presigned URL in `artifacts.json`,
   which is the whole point of the wake path — but a workload that did rely on
   it would find nothing.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import io
import json
import os
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:  # PyYAML ships with the API venv; a plain interpreter may not have it.
    import yaml
except ImportError:  # pragma: no cover - exercised by the missing-dep path
    yaml = None

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
#: The workloads live in the e2e tree and are the SAME FILES the training job
#: runs. Read from there rather than copied here: two copies of a workload
#: whose whole claim is byte-reproducibility is the one duplication this demo
#: cannot afford.
WORKLOADS = REPO_ROOT / "e2e" / "competition"
EVALUATE_CONFIG = WORKLOADS / "flashml.evaluate.yaml"
EVALUATION_MODULES = ("evaluate_model.py", "workload_common.py")

#: The training job's single task. `CommandRecipe.expand` names tasks
#: `task-{i:03d}` and a non-sweep job has exactly one.
TRAINING_TASK = "task-000"

#: Terminal session states. `SUCCEEDED` and `FAILED` are settled but NOT
#: terminal — the sandbox is still there and still billing until cleanup moves
#: it to `TERMINATED` (see `sandbox_sessions.TERMINAL_STATES`). Getting that
#: backwards is how a demo leaks a sandbox.
SETTLED_STATES = frozenset({"SUCCEEDED", "FAILED", "TERMINATED"})

#: The ledger events this script waits on, from `sandbox_orchestrator`.
ACCEPTED = "evaluation.accepted"
FAILED_EVENTS = frozenset({"session.failed", "evaluation.timeout"})

#: What a sandbox costs while nobody is watching it. Printed, not enforced.
IDLE_WARNING = (
    "a sandbox left running bills by the second — kill it in the console or "
    "re-run this script without --keep-sandbox"
)

#: Unpacks the inlined workload and runs the evaluator. Kept deliberately tiny
#: and dependency-free: it executes inside a sandbox this script cannot debug.
_BOOTSTRAP = """
import base64, io, os, runpy, sys, tarfile, tempfile
blob, argv = sys.argv[1], sys.argv[2:]
root = tempfile.mkdtemp(prefix="flashml-eval-")
with tarfile.open(fileobj=io.BytesIO(base64.b64decode(blob)), mode="r:gz") as tf:
    try:
        tf.extractall(root, filter="data")
    except TypeError:
        tf.extractall(root)
sys.path.insert(0, root)
entry = os.path.join(root, "evaluate_model.py")
sys.argv = [entry] + argv
runpy.run_path(entry, run_name="__main__")
""".strip()


class DemoError(Exception):
    """Something the operator has to fix. Never a traceback at them."""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class Api:
    """The console's own API, over stdlib urllib.

    No `requests`, no `httpx`: this script has to run from a checkout, a CI
    box and a laptop with nothing installed, and its dependencies are its
    reproducibility.
    """

    def __init__(self, base: str, token: str, timeout_s: float = 60.0) -> None:
        self.base = base.rstrip("/")
        self._token = token
        self._timeout = timeout_s

    #: GETs retry on transport weather; nothing else does. The first live run
    #: (2026-08-13, dev) died on a raw socket TimeoutError while POLLING a
    #: training job — the API was busy relaying the job's own checkpoints and
    #: one status read took >60s. A poll is safe to repeat by definition;
    #: crashing the whole demo on one slow read is how an unattended run
    #: fails at minute 11 of 12. POSTs stay single-shot: from-repo would
    #: double-submit, and the idempotent ones (model-ready) don't need it.
    #: The ladder matches app.py's forward_idempotent, the repo's precedent.
    _RETRY_DELAYS_S = (2.0, 5.0, 12.0)

    def request(self, method: str, path: str, body: Any = None,
                *, allow: tuple[int, ...] = ()) -> tuple[int, Any]:
        """`(status, parsed body)`. Raises `DemoError` for anything not 2xx
        and not explicitly allowed — an unexpected status is a demo that has
        gone wrong, and continuing past it produces a confident wrong
        timeline."""
        data = None if body is None else json.dumps(body).encode()
        headers = {"Authorization": f"Bearer {self._token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base}{path}", data=data, method=method, headers=headers
        )
        retries = list(self._RETRY_DELAYS_S) if method == "GET" else []
        while True:
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as response:
                    return response.status, _parse(response.read())
            except urllib.error.HTTPError as exc:
                payload = _parse(exc.read())
                if exc.code in allow:
                    return exc.code, payload
                detail = payload.get("detail") if isinstance(payload, dict) else payload
                raise DemoError(
                    f"{method} {path} answered {exc.code}: {detail}"
                ) from None
            # TimeoutError alongside URLError, deliberately: a read that
            # times out mid-response escapes urllib's own wrapping and
            # arrives as the bare OS-level TimeoutError — the exact
            # traceback the first live run produced.
            except (urllib.error.URLError, TimeoutError) as exc:
                if retries:
                    delay = retries.pop(0)
                    print(f"  … {method} {path} hit transport weather "
                          f"({type(exc).__name__}); retrying in {delay:g}s")
                    time.sleep(delay)
                    continue
                reason = getattr(exc, "reason", exc)
                raise DemoError(
                    f"{method} {path} could not reach {self.base} ({reason}) — "
                    f"is the API running, and is --api-base right?"
                ) from None

    def get(self, path: str, **kw) -> Any:
        return self.request("GET", path, **kw)[1]

    def post(self, path: str, body: Any = None, **kw) -> Any:
        return self.request("POST", path, body, **kw)[1]


def _parse(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", "replace")[:400]


# ---------------------------------------------------------------------------
# the timeline
# ---------------------------------------------------------------------------


class Timeline:
    """Measured wall-clock marks, printed at the end.

    `time.monotonic`, never the wall clock: this runs for minutes across a
    hibernation, and a clock adjustment mid-demo would produce a negative
    latency in the one artifact a judge reads.
    """

    def __init__(self) -> None:
        self._started = time.monotonic()
        self._marks: list[tuple[str, float, str]] = []

    def mark(self, label: str, detail: str = "") -> float:
        at = time.monotonic() - self._started
        self._marks.append((label, at, detail))
        print(f"  [{at:7.1f}s] {label}" + (f" — {detail}" if detail else ""),
              flush=True)
        return at

    def render(self) -> None:
        print("\n  timeline")
        previous = 0.0
        for label, at, detail in self._marks:
            print(f"    {at:7.1f}s  (+{at - previous:5.1f}s)  {label}"
                  + (f"  {detail}" if detail else ""))
            previous = at
        print(f"    {time.monotonic() - self._started:7.1f}s  total")


def step(text: str) -> None:
    print(f"\n\033[1m▶ {text}\033[0m", flush=True)


# ---------------------------------------------------------------------------
# the evaluation spec
# ---------------------------------------------------------------------------


def load_evaluate_config() -> dict[str, Any]:
    if yaml is None:
        raise DemoError(
            "PyYAML is not importable, so flashml.evaluate.yaml cannot be "
            "read. Run this from the API venv "
            "(flashml-cloud/apps/api/.venv/bin/python)."
        )
    if not EVALUATE_CONFIG.is_file():
        raise DemoError(f"missing {EVALUATE_CONFIG}")
    document = yaml.safe_load(EVALUATE_CONFIG.read_text())
    if not isinstance(document, dict):
        raise DemoError(f"{EVALUATE_CONFIG} is not a mapping")
    return document


def resolve_image(alias: str, override: str | None) -> str:
    """A curated alias → the pinned `repository:tag` the orchestrator needs.

    `build_evaluation_jobspec` splits `image` on its last colon and refuses
    anything without both halves; it has no alias registry, deliberately —
    the sandbox is not the place to discover that `python-slim` means
    something. So the alias is resolved HERE, through the API's own registry,
    and `--evaluation-image` is the escape hatch for running this script
    outside the API venv.
    """
    if override:
        return override
    try:
        sys.path.insert(0, str(REPO_ROOT / "flashml-cloud" / "apps" / "api"))
        from flashml_cloud_api.images import resolve_image as _resolve
    except ImportError:
        raise DemoError(
            f"cannot resolve the curated image alias {alias!r} without the API "
            f"package on sys.path. Either run this from "
            f"flashml-cloud/apps/api/.venv/bin/python, or pass "
            f"--evaluation-image repository:tag."
        ) from None
    return _resolve(alias).reference


def workload_payload() -> str:
    """The evaluator and its shared module, as one base64 gzip tarball.

    Deterministic bytes: fixed mtime, fixed uid/gid, fixed order, and an
    explicit `mtime=0` in the gzip header. Two runs of this script against the
    same workload therefore produce the same argv, which is what lets
    `EvaluationDriver.submit`'s find-by-name idempotency actually mean "the
    same evaluation" rather than "an evaluation with the same name".

    The tar and the gzip are built in two steps rather than with `mode="w:gz"`
    for one reason: `tarfile` gives no way to set the gzip header's timestamp,
    and `GzipFile.mtime` is a read-only property — so the single-step form
    stamps the current time and every run produces different bytes.
    """
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w") as tf:
        for name in EVALUATION_MODULES:
            path = WORKLOADS / name
            if not path.is_file():
                raise DemoError(f"missing workload module {path}")
            data = path.read_bytes()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))

    gz_buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buffer, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(tar_buffer.getvalue())
    return base64.b64encode(gz_buffer.getvalue()).decode("ascii")


def build_evaluation_spec(config: dict[str, Any], image: str,
                          expect_sha256: str) -> dict[str, Any]:
    """The `evaluation_spec` the session is opened with.

    `args` comes out of `flashml.evaluate.yaml` verbatim so the argv a judge
    reads in the console is the argv the file declares. `--expect-sha256` is
    appended here and only here: the value is the training job's reported
    model hash, which does not exist when the config is written, and adding it
    turns the hash from evidence into an assertion — a model that is not the
    one this evaluation is about is refused before it is ever parsed.
    """
    args = [str(a) for a in (config.get("args") or [])]
    if expect_sha256:
        args += ["--expect-sha256", expect_sha256]
    spec: dict[str, Any] = {
        "image": image,
        "command": ["python", "-c", _BOOTSTRAP, workload_payload(), *args],
    }
    timeout = config.get("timeout_seconds")
    if timeout is not None:
        spec["timeout_seconds"] = int(timeout)
    # Forwarded because `build_evaluation_jobspec` forwards them; anything
    # else in the config is not the sandbox path's to read and is left alone.
    if config.get("validators"):
        spec["validators"] = config["validators"]
    if config.get("reduce"):
        spec["reduce"] = config["reduce"]
    return spec


# ---------------------------------------------------------------------------
# training
# ---------------------------------------------------------------------------


def find_training_job(api: Api, name: str) -> str | None:
    """The newest job this account owns carrying `name`, or None.

    Name rather than a stored id, for `evaluation_job_name`'s reason: a
    re-run has no memory of the previous one, and a deterministic name is the
    only thing a restart can recognise its own work by.
    """
    jobs = api.get("/v1alpha1/jobs")
    rows = jobs.get("jobs") if isinstance(jobs, dict) else jobs
    for row in rows or []:
        if isinstance(row, dict) and row.get("name") == name:
            return row.get("job_id")
    return None


def submit_training(api: Api, repo: str, ref: str, pool: str | None) -> str:
    body: dict[str, Any] = {"repo": repo, "ref": ref}
    if pool:
        body["pool"] = pool
    answer = api.post("/v1alpha1/jobs/from-repo", body)
    job_id = answer.get("job_id") if isinstance(answer, dict) else None
    if not job_id:
        raise DemoError(f"from-repo returned no job_id: {answer}")
    for finding in (answer.get("findings") or []):
        print(f"    preflight: {finding}")
    return job_id


def wait_for_training(api: Api, job_id: str, timeout_s: float,
                      poll_s: float) -> dict[str, Any]:
    """Block until the training job settles; return its `metrics.json`.

    The model artifact is read back rather than inferred from the job state.
    `SUCCEEDED` says the coordinator accepted a commit; fetching the metrics
    says the bytes are actually there and hands over the `model_sha256` the
    evaluation is about. Only the second is worth building a sandbox on.
    """
    deadline = time.monotonic() + timeout_s
    last = ""
    while True:
        job = api.get(f"/v1alpha1/jobs/{job_id}")
        state = str(job.get("state") or job.get("status") or "?")
        if state != last:
            print(f"    training {state}", flush=True)
            last = state
        if state in ("SUCCEEDED", "PARTIAL"):
            break
        if state in ("FAILED", "CANCELLED"):
            raise DemoError(f"training job {job_id} ended {state}")
        if time.monotonic() > deadline:
            raise DemoError(
                f"training job {job_id} was still {state} after {timeout_s:.0f}s"
            )
        time.sleep(poll_s)

    metrics = api.get(f"/v1alpha1/jobs/{job_id}/artifacts/{TRAINING_TASK}/metrics.json")
    if not isinstance(metrics, dict) or not metrics.get("model_sha256"):
        raise DemoError(
            f"training finished but {TRAINING_TASK}/metrics.json carries no "
            f"model_sha256 — there is nothing to tie an evaluation to"
        )
    return metrics


# ---------------------------------------------------------------------------
# the sandbox session
# ---------------------------------------------------------------------------


def adopt_session(api: Api, job_id: str) -> dict[str, Any] | None:
    """An unsettled session already open for this job, newest first.

    `[]` for a job with no session is a normal answer here, not an error —
    the route says so explicitly — so this is a lookup and never a failure.
    """
    for row in api.get(f"/v1alpha1/jobs/{job_id}/sandbox-sessions") or []:
        if isinstance(row, dict) and row.get("state") not in SETTLED_STATES:
            return row
    return None


def poll_events(api: Api, session_id: str, after: int, seen: set[str],
                timeline: Timeline) -> tuple[int, str | None]:
    """Drain new ledger events, narrating each once. Returns the new cursor
    and the terminal event's type if one arrived.

    `after_sequence` is what makes this cheap across a hibernation that can
    last an hour — the console polls the same way for the same reason.
    """
    events = api.get(
        f"/v1alpha1/sandbox-sessions/{session_id}/events?after_sequence={after}"
    ) or []
    terminal = None
    for event in events:
        if not isinstance(event, dict):
            continue
        sequence = int(event.get("sequence") or 0)
        after = max(after, sequence)
        kind = str(event.get("type") or "?")
        if kind in seen:
            continue
        seen.add(kind)
        latency = event.get("latency_ms")
        detail = f"{float(latency):.0f} ms" if isinstance(latency, (int, float)) else ""
        timeline.mark(kind, detail)
        if kind == ACCEPTED or kind in FAILED_EVENTS:
            terminal = kind
    return after, terminal


def run(args: argparse.Namespace) -> int:
    api = Api(args.api_base, args.token)
    timeline = Timeline()
    session_id: str | None = None
    share_token: str | None = None
    accepted: dict[str, Any] | None = None

    try:
        # -- training ------------------------------------------------------
        step("training")
        job_id = args.training_job_id
        if job_id:
            print(f"    adopting job {job_id} (--training-job-id)")
        else:
            job_id = find_training_job(api, args.job_name)
            if job_id:
                print(f"    found an existing {args.job_name!r}: {job_id}")
            else:
                job_id = submit_training(api, args.repo, args.ref, args.pool)
                print(f"    submitted {job_id}")
        timeline.mark("training submitted", job_id)

        metrics = wait_for_training(api, job_id, args.training_timeout, args.poll)
        model_sha256 = str(metrics["model_sha256"])
        timeline.mark(
            "model artifact present",
            f"accuracy {metrics.get('accuracy')}, sha256 {model_sha256[:16]}…",
        )

        # -- the session ---------------------------------------------------
        step("sandbox session")
        existing = adopt_session(api, job_id)
        if existing:
            session_id = str(existing["id"] if "id" in existing else existing["session_id"])
            share_token = existing.get("share_token")
            print(f"    adopting session {session_id} in state {existing.get('state')}")
        else:
            config = load_evaluate_config()
            image = resolve_image(str(config.get("image") or ""), args.evaluation_image)
            spec = build_evaluation_spec(config, image, model_sha256)
            print(f"    image {image}")
            print(f"    workload carried inline: {len(spec['command'][3])} base64 chars")
            # Blocks for about ten seconds: create, bootstrap, register, pause.
            # There is no session id to hand back before `create_session` runs,
            # so there is nothing for a 202 to point at — see the route.
            answer = api.post("/v1alpha1/sandbox-sessions",
                              {"training_job_id": job_id, "evaluation_spec": spec})
            session_id = str(answer["session_id"])
            share_token = answer.get("share_token")
            print(f"    session {session_id} — {answer.get('state')}")
        timeline.mark("sandbox hibernated", session_id)

        # -- wake and evaluate ---------------------------------------------
        step("wake and evaluate")
        api.post(f"/v1alpha1/sandbox-sessions/{session_id}/model-ready")
        timeline.mark("model-ready accepted", "202, evaluation runs in background")

        cursor, seen, terminal = 0, set(), None
        deadline = time.monotonic() + args.evaluation_timeout
        while terminal is None:
            cursor, terminal = poll_events(api, session_id, cursor, seen, timeline)
            if terminal is not None:
                break
            if time.monotonic() > deadline:
                raise DemoError(
                    f"no settled evaluation for session {session_id} within "
                    f"{args.evaluation_timeout:.0f}s — the session's own ledger "
                    f"is the record of what happened"
                )
            time.sleep(args.poll)

        session = api.get(f"/v1alpha1/sandbox-sessions/{session_id}")
        if terminal != ACCEPTED:
            raise DemoError(
                f"the evaluation did not settle as accepted ({terminal}); "
                f"session state {session.get('state')}, "
                f"error {session.get('error_code')}"
            )
        accepted = session
        step("verdict")
        print(f"    session {session.get('state')}")
        print(f"    evaluation job {session.get('evaluation_job_id')}")
        print(f"    the model this scored: sha256 {model_sha256}")
        return 0

    except DemoError as exc:
        print(f"\nrun_demo: {exc}", file=sys.stderr, flush=True)
        return 2
    except KeyboardInterrupt:
        print("\nrun_demo: interrupted", file=sys.stderr, flush=True)
        return 130
    finally:
        # ALWAYS. A leaked sandbox bills by the second, and every path out of
        # the block above — success, refusal, Ctrl-C, an unexpected status —
        # goes through here. `cleanup_session` is documented never to raise,
        # so the only thing that can go wrong is the HTTP call, which is
        # caught: a failure to clean up must not replace the failure that sent
        # us here.
        if session_id and not args.keep_sandbox:
            try:
                answer = api.post(
                    f"/v1alpha1/sandbox-sessions/{session_id}/cleanup"
                )
                timeline.mark("sandbox cleaned up", str(answer.get("state")))
            except DemoError as exc:
                print(f"  cleanup failed: {exc}", file=sys.stderr)
                print(f"  {IDLE_WARNING}", file=sys.stderr)
        elif session_id:
            print(f"\n  --keep-sandbox: session {session_id} is still running")
            print(f"  {IDLE_WARNING}")

        timeline.render()
        if share_token:
            print(f"\n  share URL: {args.console_base.rstrip('/')}/share/{share_token}")
        elif accepted is not None:
            print("\n  no share token was issued for this session")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_demo.py", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--api-base", default=os.environ.get(
        "FLASHML_API_BASE", "http://127.0.0.1:8000"),
        help="the control-plane API (local or dev), not the coordinator")
    parser.add_argument("--console-base", default=os.environ.get(
        "FLASHML_CONSOLE_BASE", "http://127.0.0.1:3000"),
        help="where the share URL is rendered; the console, not the API")
    parser.add_argument("--token", default=os.environ.get("FLASHML_TOKEN", ""),
                        help="a Supabase JWT or an fmu_ developer token "
                             "(env: FLASHML_TOKEN)")
    parser.add_argument("--repo", default="Zolli-Labs/flashml-competition")
    parser.add_argument("--ref", default="train", help="the training branch")
    parser.add_argument("--pool", default=os.environ.get("FLASHML_POOL") or None)
    parser.add_argument("--job-name", default="flashml-competition-train",
                        help="the compiled job name an existing run is found "
                             "by; must match flashml.train.yaml's 'name'")
    parser.add_argument("--training-job-id", default=None,
                        help="skip submission and drive this job instead")
    parser.add_argument("--evaluation-image", default=None,
                        help="pinned repository:tag, overriding the alias in "
                             "flashml.evaluate.yaml")
    parser.add_argument("--training-timeout", type=float, default=1800.0)
    parser.add_argument("--evaluation-timeout", type=float, default=900.0,
                        help="matches the orchestrator's own "
                             "DEFAULT_EVALUATION_TIMEOUT_S")
    parser.add_argument("--poll", type=float, default=2.0,
                        help="matches EVALUATION_POLL_INTERVAL_S")
    parser.add_argument("--keep-sandbox", action="store_true",
                        help="do not clean up — for debugging a failure. The "
                             "sandbox keeps billing")
    args = parser.parse_args(argv)
    if not args.token:
        parser.error("a --token (or FLASHML_TOKEN) is required")
    return args


if __name__ == "__main__":
    raise SystemExit(run(parse_args()))
